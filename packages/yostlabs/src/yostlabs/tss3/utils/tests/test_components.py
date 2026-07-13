"""
For testing the data components of the sensor. This includes:
- Accelerometer
- Gyroscope
- Magnetometer
- Barometer

These all have slightly different tests, but it is optimal to do them all at the same time as the process
is generally the same, and the main difference is how the data is validated.

The user can supply an optional list of expected components to compare the detected components against. If
no list is supplied, the detected components will simply be listed with no error indication. All detected
components will still be tested regardless.
"""

import enum
import math
import time
import threading

from yostlabs.tss3.utils.tests.base import SensorTestBase
from yostlabs.tss3.utils.streaming import ThreespaceStreamingManager, ThreespaceStreamingStatus
from yostlabs.tss3.api import ThreespaceSensor, StreamableCommands
from yostlabs.math.vector import vec_len, vec_dot, vec_normalize
from yostlabs.math.quaternion import quat_mul, quat_from_axis_angle, quat_rotate_vec


class ComponentTestState(enum.Enum):
    Inactive = 0
    CheckingComponents = 1
    SettingHighOdr = 2
    StreamingStatic = 3
    ReadingUpdateRateHigh = 4
    SettingLowOdr = 5
    AwaitingFlatSurface = 6
    StreamingFlip = 7
    WaitingForMinDuration = 8
    ReadingUpdateRateLow = 9
    AnalyzingFlipData = 10
    Finished = 11


class ComponentTest(SensorTestBase):
    """
    Tests the data components of the sensor (Accel, Gyro, Mag, Barometer).

    Steps:
    1. readValidComponents(). If expected_components supplied, compare and record pass/fail.
       Continue testing all detected components regardless.
    2. Set ODR=1000 for all components. Record any errors. Read back true set ODR.
    3. Stream 2 seconds of raw component data at 1000 Hz.
       - Verify no component has unchanging (static) data.
       - Mag: additionally verify average vector length is not near 0.
    4. Compare measured update rates to the 1000 ODR true values (within 1% tolerance).
    5. Set ODR=50 for all components. Read back true set ODR.
    6. Prompt user to place sensor on a flat surface (user calls notify_flat_ready()).
    7. Start streaming at 50 Hz, saving all raw component data.
       Prompt user to flip sensor upside down (user calls notify_flip_done()).
    8. Ensure at least 2 seconds of data from streaming start before stopping.
    9. Compare measured update rates to the 50 ODR true values (within 1% tolerance).
    10. Analyze flip data per component:
        - Accel: verify gravity vector direction reversed.
        - Gyro: verify integrated rotation >= 120 degrees (raw gyro assumed in rad/s).
        - Mag: verify field vector direction reversed.
        - Baro: no data verification performed.
    """

    CHECK_UPDATE_RATE_WAIT_DURATION = 2.0    # seconds to wait before checking update rate (gives time for it to update)
    UPDATE_RATE_TOLERANCE = 0.01    # 1% tolerance for update rate vs true ODR
    GYRO_ACCEL_DOT_THRESHOLD = 0.9  # minimum acceptable dot product for gyro-accel cross-check
    MAG_MIN_LENGTH = 0.21           # minimum acceptable average mag vector magnitude
    GYRO_FLIP_MIN_DEGREES = 120.0   # integrated rotation threshold to count as a flip

    def __init__(self, sensor: ThreespaceSensor, expected_components: list[str] | None = None):
        super().__init__(sensor)
        self.state = ComponentTestState.Inactive

        self._expected_components = expected_components
        self._settings_cache: dict = {}

        self._accel_ids: list[int] = []
        self._gyro_ids: list[int] = []
        self._mag_ids: list[int] = []
        self._baro_ids: list[int] = []

        self._manager: ThreespaceStreamingManager | None = None
        self._current_samples: dict = {}
        self._static_samples: dict = {}
        self._flip_samples: dict = {}

        self._flip_done_flag: bool = False
        self._odr_set_time: float | None = None

        self.result = {
            "valid_components": {
                "success": None,
                "detected": None,
                "expected": expected_components,
            },
            # "accel", "gyro", "mag", "baro" keys are populated in CheckingComponents.
            # Structure: result[ctype][cid][test_name] = {success, ...}
            "gyro_accel_check": {},  # (gyro_id, accel_id) -> {success, dot_product}
        }

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start(self):
        if self.state != ComponentTestState.Inactive:
            raise Exception("Component test already started.")
        self.__go_next_state()

    def cancel(self):
        if self.state == ComponentTestState.Inactive:
            return
        self.state = ComponentTestState.Inactive
        self.__cleanup()

    def update(self):
        if self.state in (ComponentTestState.Inactive, ComponentTestState.Finished):
            return
        match self.state:
            case ComponentTestState.CheckingComponents:
                self.__update_checking_components()
            case ComponentTestState.SettingHighOdr:
                self.__update_setting_odr(1000, "set_odr_1000")
            case ComponentTestState.StreamingStatic:
                self.__update_streaming_static()
            case ComponentTestState.ReadingUpdateRateHigh:
                self.__update_reading_update_rate("set_odr_1000", "update_rate_1000")
            case ComponentTestState.SettingLowOdr:
                self.__update_setting_odr(50, "set_odr_50")
            case ComponentTestState.AwaitingFlatSurface:
                pass  # Waiting for notify_flat_ready()
            case ComponentTestState.StreamingFlip:
                self.__update_streaming_flip()
            case ComponentTestState.WaitingForMinDuration:
                self.__update_waiting_for_min_duration()
            case ComponentTestState.ReadingUpdateRateLow:
                self.__update_reading_update_rate("set_odr_50", "update_rate_50")
            case ComponentTestState.AnalyzingFlipData:
                self.__update_analyzing_flip_data()

    def notify_flat_ready(self):
        """Call when the sensor has been placed flat and is stable on a surface."""
        if self.state != ComponentTestState.AwaitingFlatSurface:
            return
        self._current_samples = self._make_samples_dict()
        self._flip_done_flag = False
        self._setup_manager(hz=50)
        self.__go_next_state()

    def notify_flip_done(self):
        """Call once the sensor has been flipped upside down."""
        if self.state != ComponentTestState.StreamingFlip:
            return
        self._flip_done_flag = True

    # ------------------------------------------------------------------
    # Private state handlers
    # ------------------------------------------------------------------

    def __update_checking_components(self):
        detected_str = self.sensor.readValidComponents()
        self.result["valid_components"]["detected"] = detected_str

        self._accel_ids = list(self.sensor.valid_accels)
        self._gyro_ids = list(self.sensor.valid_gyros)
        self._mag_ids = list(self.sensor.valid_mags)
        self._baro_ids = list(self.sensor.valid_baros)

        # Initialise per-component result entries now that IDs are known
        for ctype, ids in [("accel", self._accel_ids), ("gyro", self._gyro_ids),
                           ("mag", self._mag_ids), ("baro", self._baro_ids)]:
            if ids:
                self.result[ctype] = {cid: self.__make_component_entry() for cid in ids}

        # Cache current ODRs and stream settings for restoration on cleanup
        for accel_id in self._accel_ids:
            self._settings_cache[f"odr_accel{accel_id}"] = self.sensor.readOdrAccel(accel_id)
        for gyro_id in self._gyro_ids:
            self._settings_cache[f"odr_gyro{gyro_id}"] = self.sensor.readOdrGyro(gyro_id)
        for mag_id in self._mag_ids:
            self._settings_cache[f"odr_mag{mag_id}"] = self.sensor.readOdrMag(mag_id)
        for baro_id in self._baro_ids:
            self._settings_cache[f"odr_baro{baro_id}"] = self.sensor.readOdrBaro(baro_id)
        self._settings_cache["stream_slots"] = self.sensor.readStreamSlots()
        self._settings_cache["stream_interval"] = self.sensor.readStreamInterval()

        if self._expected_components is not None:
            detected_list = [c.strip() for c in detected_str.split(',') if c.strip()]
            self.result["valid_components"]["success"] = (
                set(detected_list) == set(self._expected_components)
            )
            if not self.result["valid_components"]["success"]:
                self.overall_success = False

        self.__go_next_state()

    def __update_setting_odr(self, target_odr: int, result_key: str):
        all_ok = True

        odr_methods = {
            "accel": (self._accel_ids, self.sensor.writeOdrAccel, self.sensor.readOdrAccel),
            "gyro":  (self._gyro_ids,  self.sensor.writeOdrGyro,  self.sensor.readOdrGyro),
            "mag":   (self._mag_ids,   self.sensor.writeOdrMag,   self.sensor.readOdrMag),
            "baro":  (self._baro_ids,  self.sensor.writeOdrBaro,  self.sensor.readOdrBaro),
        }

        for ctype, (ids, write_fn, read_fn) in odr_methods.items():
            for cid in ids:
                err = write_fn(cid, target_odr)
                if err != 0:
                    self.__comp_result(ctype, cid)[result_key] = {"success": False, "error": err, "true_odr": None}
                    all_ok = False
                else:
                    self.__comp_result(ctype, cid)[result_key] = {"success": True, "error": None, "true_odr": read_fn(cid)}

        if not all_ok:
            self.overall_success = False

        self._odr_set_time = time.perf_counter()
        self.__go_next_state()

    def __update_streaming_static(self):
        # First entry: set up streaming and return; subsequent calls check elapsed time.
        if self._manager is None:
            self._setup_manager(hz=50)
            self._current_samples = self._make_samples_dict()

        self._manager.update()

        if time.perf_counter() - self._odr_set_time >= self.CHECK_UPDATE_RATE_WAIT_DURATION:
            self._stop_manager()
            self._static_samples = self._current_samples
            self._current_samples = self._make_samples_dict()
            self.__analyze_static_data()
            self.__go_next_state()

    def __update_streaming_flip(self):
        self._manager.update()
        if self._flip_done_flag:
            self._stop_manager()
            self._flip_samples = self._current_samples
            self._current_samples = {}
            self.__go_next_state()

    def __update_waiting_for_min_duration(self):
        if time.perf_counter() - self._odr_set_time >= self.CHECK_UPDATE_RATE_WAIT_DURATION:
            self.__go_next_state()

    def __update_reading_update_rate(self, odr_result_key: str, rate_result_key: str):
        all_pass = True

        def _check(ctype, cid, measured_rate):
            nonlocal all_pass
            comp = self.__comp_result(ctype, cid)
            if not comp[odr_result_key]["success"]:
                # ODR was not set successfully; skip rate check for this component
                comp[rate_result_key] = {"success": None, "expected": None, "actual": measured_rate}
                return
            true_odr = comp[odr_result_key]["true_odr"]
            tolerance = true_odr * self.UPDATE_RATE_TOLERANCE
            passed = abs(measured_rate - true_odr) <= tolerance
            comp[rate_result_key] = {"success": passed, "expected": true_odr, "actual": measured_rate}
            if not passed:
                all_pass = False

        for accel_id in self._accel_ids:
            _check("accel", accel_id, self.sensor.readUpdateRateAccel(accel_id))
        for gyro_id in self._gyro_ids:
            _check("gyro", gyro_id, self.sensor.readUpdateRateGyro(gyro_id))
        for mag_id in self._mag_ids:
            _check("mag", mag_id, self.sensor.readUpdateRateMag(mag_id))
        for baro_id in self._baro_ids:
            _check("baro", baro_id, self.sensor.readUpdateRateBaro(baro_id))

        if not all_pass:
            self.overall_success = False

        self.__go_next_state()

    def __update_analyzing_flip_data(self):
        self.__analyze_accel_flip()
        self.__analyze_gyro_flip()
        self.__analyze_mag_flip()
        for baro_id in self._baro_ids:
            self.__comp_result("baro", baro_id)["flip"] = {
                "success": None,
                "note": "No data verification performed for barometer.",
            }
        self.__go_next_state()

    # ------------------------------------------------------------------
    # Result helpers
    # ------------------------------------------------------------------

    def __comp_result(self, ctype: str, cid: int) -> dict:
        """Returns the result sub-dict for a specific component."""
        return self.result[ctype][cid]

    @staticmethod
    def __make_component_entry() -> dict:
        """Returns a fresh per-component result template."""
        return {
            "set_odr_1000": {"success": None, "error": None, "true_odr": None},
            "update_rate_1000": {"success": None, "expected": None, "actual": None},
            "static_check": {"success": None, "static_error": None },
            "set_odr_50": {"success": None, "error": None, "true_odr": None},
            "update_rate_50": {"success": None, "expected": None, "actual": None},
            "flip": {"success": None },
        }

    # ------------------------------------------------------------------
    # Static data analysis
    # ------------------------------------------------------------------

    def __analyze_static_data(self):
        all_pass = True

        for accel_id in self._accel_ids:
            is_static, error = self.__check_static_vector(self._static_samples, "accel", accel_id)
            self.__comp_result("accel", accel_id)["static_check"] = {
                "success": not is_static, "static_error": error
            }
            if is_static:
                all_pass = False

        for gyro_id in self._gyro_ids:
            is_static, error = self.__check_static_vector(self._static_samples, "gyro", gyro_id)
            self.__comp_result("gyro", gyro_id)["static_check"] = {
                "success": not is_static, "static_error": error
            }
            if is_static:
                all_pass = False

        for mag_id in self._mag_ids:
            is_static, error = self.__check_static_vector(self._static_samples, "mag", mag_id)
            mag_vecs = self._static_samples.get("mag", {}).get(mag_id, [])
            mag_len = sum(vec_len(v) for v in mag_vecs) / len(mag_vecs) if mag_vecs else 0.0
            length_ok = mag_len >= self.MAG_MIN_LENGTH
            self.__comp_result("mag", mag_id)["static_check"] = {
                "success": not is_static and length_ok,
                "static_error": error,
                "avg_length": mag_len,
                "length_ok": length_ok,
            }
            if is_static or not length_ok:
                all_pass = False

        for baro_id in self._baro_ids:
            is_static, error = self.__check_static_scalar(self._static_samples, "baro", baro_id)
            self.__comp_result("baro", baro_id)["static_check"] = {
                "success": not is_static, "static_error": error
            }
            if is_static:
                all_pass = False

        if not all_pass:
            self.overall_success = False

    def __check_static_vector(self, samples: dict, ctype: str, cid: int) -> tuple[bool, str]:
        """Returns (is_static, extra). is_static=True means no variation was detected."""
        values = samples.get(ctype, {}).get(cid, [])
        if len(values) < 2:
            return True, "insufficient samples"
        first = values[0]
        for v in values[1:]:
            if any(abs(v[i] - first[i]) > 1e-9 for i in range(len(v))):
                return False, ""
        return True, f"all samples identical: {first}"

    def __check_static_scalar(self, samples: dict, ctype: str, cid: int) -> tuple[bool, str]:
        values = samples.get(ctype, {}).get(cid, [])
        if len(values) < 2:
            return True, "insufficient samples"
        first = values[0]
        if any(abs(v - first) > 1e-9 for v in values[1:]):
            return False, ""
        return True, f"all samples identical: {first}"

    # ------------------------------------------------------------------
    # Flip data analysis
    # ------------------------------------------------------------------

    def __analyze_accel_flip(self):
        for accel_id in self._accel_ids:
            values = self._flip_samples.get("accel", {}).get(accel_id, [])
            if len(values) < 2:
                self.__comp_result("accel", accel_id)["flip"] = {
                    "success": False,
                    "error": "insufficient samples",
                }
                self.overall_success = False
                continue
            dot = vec_dot(vec_normalize(values[0]), vec_normalize(values[-1]))
            direction_changed = dot < 0.0
            self.__comp_result("accel", accel_id)["flip"] = {
                "success": direction_changed,
                "dot_product": dot,
                "direction_changed": direction_changed,
            }
            if not direction_changed:
                self.overall_success = False

    def __analyze_mag_flip(self):
        for mag_id in self._mag_ids:
            values = self._flip_samples.get("mag", {}).get(mag_id, [])
            if len(values) < 2:
                self.__comp_result("mag", mag_id)["flip"] = {
                    "success": False,
                    "reason": "insufficient samples",
                }
                self.overall_success = False
                continue
            dot = vec_dot(vec_normalize(values[0]), vec_normalize(values[-1]))
            direction_changed = dot < 0.0
            self.__comp_result("mag", mag_id)["flip"] = {
                "success": direction_changed,
                "dot_product": dot,
                "direction_changed": direction_changed,
            }
            if not direction_changed:
                self.overall_success = False

    def __analyze_gyro_flip(self):
        principal_axes = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]

        for gyro_id in self._gyro_ids:
            times = self._flip_samples.get("time", [])
            gyro_values = self._flip_samples.get("gyro", {}).get(gyro_id, [])
            timed_values = [(t, g) for t, g in zip(times, gyro_values) if g is not None]
            if len(timed_values) < 2:
                self.__comp_result("gyro", gyro_id)["flip"] = {
                    "success": False,
                    "reason": "insufficient samples for integration",
                    "max_rotation_deg": None,
                }
                self.overall_success = False
                continue

            # Integrate angular velocity (rad/s) into a cumulative rotation quaternion
            q = [0.0, 0.0, 0.0, 1.0]  # identity: [x, y, z, w]
            for i in range(1, len(timed_values)):
                dt = timed_values[i][0] - timed_values[i - 1][0]
                gyro = timed_values[i][1]  # [wx, wy, wz] in rad/s
                angle = vec_len(gyro) * dt
                if angle > 1e-12:
                    axis = vec_normalize(gyro)
                    dq = quat_from_axis_angle(axis, angle)
                    q = quat_mul(q, dq)

            # Pass if any principal axis was rotated >= GYRO_FLIP_MIN_DEGREES.
            # Checking all three avoids false negatives when the flip axis is
            # aligned with the single reference vector used in a one-axis check.
            max_deg = 0.0
            best_axis = None
            for axis in principal_axes:
                rotated = quat_rotate_vec(q, axis)
                cos_a = max(-1.0, min(1.0, vec_dot(axis, vec_normalize(rotated))))
                deg = math.degrees(math.acos(cos_a))
                if deg > max_deg:
                    max_deg = deg
                    best_axis = axis

            passed = max_deg >= self.GYRO_FLIP_MIN_DEGREES

            flip_result = {
                "success": passed,
                "max_rotation_deg": max_deg,
                "best_axis": best_axis,
            }

            # Cross-check against every accel that passed its own flip test:
            # rotate the initial accel vector by q and verify it aligns with
            # the observed final accel vector (dot >= self.GYRO_ACCEL_DOT_THRESHOLD).
            for accel_id in self._accel_ids:
                accel_flip = self.result.get("accel", {}).get(accel_id, {}).get("flip", {})
                if not accel_flip.get("success"):
                    continue
                accel_vals = self._flip_samples.get("accel", {}).get(accel_id, [])
                if len(accel_vals) < 2:
                    continue
                predicted = quat_rotate_vec(q, accel_vals[0])
                dot = vec_dot(vec_normalize(predicted), vec_normalize(accel_vals[-1]))
                check_passed = dot >= self.GYRO_ACCEL_DOT_THRESHOLD
                self.result["gyro_accel_check"][(gyro_id, accel_id)] = {
                    "success": check_passed,
                    "dot_product": dot,
                }
                if not check_passed:
                    self.overall_success = False

            self.__comp_result("gyro", gyro_id)["flip"] = flip_result
            if not passed:
                self.overall_success = False

    # ------------------------------------------------------------------
    # Streaming manager helpers
    # ------------------------------------------------------------------

    def _make_samples_dict(self) -> dict:
        """Create an empty per-component samples dict aligned to the current component IDs."""
        d: dict = {"time": []}
        if self._accel_ids:
            d["accel"] = {cid: [] for cid in self._accel_ids}
        if self._gyro_ids:
            d["gyro"] = {cid: [] for cid in self._gyro_ids}
        if self._mag_ids:
            d["mag"] = {cid: [] for cid in self._mag_ids}
        if self._baro_ids:
            d["baro"] = {cid: [] for cid in self._baro_ids}
        return d

    def _setup_manager(self, hz: int):
        self._manager = ThreespaceStreamingManager(self.sensor)
        self._manager.register_command(self, StreamableCommands.GetTimestamp, immediate_update=False)
        for accel_id in self._accel_ids:
            self._manager.register_command(self, StreamableCommands.GetRawAccelVec, param=accel_id, immediate_update=False)
        for gyro_id in self._gyro_ids:
            self._manager.register_command(self, StreamableCommands.GetRawGyroRate, param=gyro_id, immediate_update=False)
        for mag_id in self._mag_ids:
            self._manager.register_command(self, StreamableCommands.GetRawMagVec, param=mag_id, immediate_update=False)
        for baro_id in self._baro_ids:
            self._manager.register_command(self, StreamableCommands.GetBarometerAltitudeById, param=baro_id, immediate_update=False)
        self._manager.register_callback(self._on_streaming_data, hz=hz)
        self._manager.enable()

    def _stop_manager(self):
        if self._manager is None:
            return
        self._manager.unregister_all_commands_from_owner(self)
        self._manager.unregister_callback(self._on_streaming_data)
        self._manager.disable()
        self._manager = None

    def _on_streaming_data(self, status: ThreespaceStreamingStatus, user_data=None):
        # Collect one sample per packet so gyro integration captures every update
        if status != ThreespaceStreamingStatus.Data:
            return
        self._current_samples["time"].append(self._manager.get_value(StreamableCommands.GetTimestamp) / 1_000_000)
        for accel_id in self._accel_ids:
            self._current_samples["accel"][accel_id].append(
                self._manager.get_value(StreamableCommands.GetRawAccelVec, accel_id))
        for gyro_id in self._gyro_ids:
            self._current_samples["gyro"][gyro_id].append(
                self._manager.get_value(StreamableCommands.GetRawGyroRate, gyro_id))
        for mag_id in self._mag_ids:
            self._current_samples["mag"][mag_id].append(
                self._manager.get_value(StreamableCommands.GetRawMagVec, mag_id))
        for baro_id in self._baro_ids:
            self._current_samples["baro"][baro_id].append(
                self._manager.get_value(StreamableCommands.GetBarometerAltitudeById, baro_id))

    # ------------------------------------------------------------------
    # State machine helpers
    # ------------------------------------------------------------------

    def __go_next_state(self):
        match self.state:
            case ComponentTestState.Inactive:
                self.state = ComponentTestState.CheckingComponents
            case ComponentTestState.CheckingComponents:
                self.state = ComponentTestState.SettingHighOdr
            case ComponentTestState.SettingHighOdr:
                self.state = ComponentTestState.StreamingStatic
            case ComponentTestState.StreamingStatic:
                self.state = ComponentTestState.ReadingUpdateRateHigh
            case ComponentTestState.ReadingUpdateRateHigh:
                self.state = ComponentTestState.SettingLowOdr
            case ComponentTestState.SettingLowOdr:
                self.state = ComponentTestState.AwaitingFlatSurface
            case ComponentTestState.AwaitingFlatSurface:
                self.state = ComponentTestState.StreamingFlip
            case ComponentTestState.StreamingFlip:
                self.state = ComponentTestState.WaitingForMinDuration
            case ComponentTestState.WaitingForMinDuration:
                self.state = ComponentTestState.ReadingUpdateRateLow
            case ComponentTestState.ReadingUpdateRateLow:
                self.state = ComponentTestState.AnalyzingFlipData
            case ComponentTestState.AnalyzingFlipData:
                self.state = ComponentTestState.Finished
                self.__cleanup()
            case _:
                raise Exception(f"Invalid state for __go_next_state: {self.state}")

        self.update()

    def __cleanup(self):
        self._stop_manager()
        if self._settings_cache:
            try:
                self.sensor.write_settings(**self._settings_cache)
            except Exception:
                pass

def print_results(result: dict, show_only_failures: bool = False):
    """Print component test results in a human-readable indented format.

    Parameters
    ----------
    result:
        The ``ComponentTest.result`` dict.
    show_only_failures:
        When True, only entries whose ``success`` field is ``False`` are shown.
    """
    def _fmt(data: dict) -> str:
        return ", ".join(f"{k}={v}" for k, v in data.items() if v is not None)

    # valid_components block
    vc = result.get("valid_components", {})
    if not show_only_failures or vc.get("success") is False:
        print("Valid Components")
        for k, v in vc.items():
            if v is not None:
                print(f"  {k}: {v}")

    # Per-component-type blocks
    for ctype in ("accel", "gyro", "mag", "baro"):
        if ctype not in result:
            continue
        printed_type_header = False
        for cid, tests in result[ctype].items():
            printed_id_header = False
            for test_name, test_data in tests.items():
                if not isinstance(test_data, dict):
                    continue
                success = test_data.get("success")
                if show_only_failures and success is not False:
                    continue
                if not printed_type_header:
                    print(ctype.capitalize())
                    printed_type_header = True
                if not printed_id_header:
                    print(f"  {cid}")
                    printed_id_header = True
                print(f"    {test_name}: {_fmt(test_data)}")

    # Gyro-accel cross-check block
    gyro_accel = result.get("gyro_accel_check", {})
    if gyro_accel:
        printed_header = False
        for (gyro_id, accel_id), check_data in gyro_accel.items():
            success = check_data.get("success")
            if show_only_failures and success is not False:
                continue
            if not printed_header:
                print("Gyro-Accel Cross-Check")
                printed_header = True
            print(f"  gyro={gyro_id}, accel={accel_id}: {_fmt(check_data)}")


def run_test(show_only_failures: bool = False):
    sensor = ThreespaceSensor()
    test = ComponentTest(sensor)

    _enter_event = threading.Event()

    def _await_enter():
        input()
        _enter_event.set()

    def _start_waiting_for_enter():
        _enter_event.clear()
        threading.Thread(target=_await_enter, daemon=True).start()

    test.start()

    last_state = test.state
    awaiting_enter = False
    while test.state != ComponentTestState.Finished:
        if test.state != last_state:
            if test.state == ComponentTestState.AwaitingFlatSurface:
                print("Place the sensor on a flat, level surface, then press Enter.")
                _start_waiting_for_enter()
                awaiting_enter = True
            elif test.state == ComponentTestState.StreamingFlip:
                print("Streaming started. Flip the sensor upside down, then press Enter.")
                _start_waiting_for_enter()
                awaiting_enter = True
            last_state = test.state

        if awaiting_enter and _enter_event.is_set():
            awaiting_enter = False
            if test.state == ComponentTestState.AwaitingFlatSurface:
                test.notify_flat_ready()
            elif test.state == ComponentTestState.StreamingFlip:
                test.notify_flip_done()

        test.update()
        time.sleep(0.005)

    print_results(test.result, show_only_failures)
    print(f"\nOverall success: {test.overall_success}")
    sensor.cleanup()


if __name__ == "__main__":
    run_test(show_only_failures=False)
