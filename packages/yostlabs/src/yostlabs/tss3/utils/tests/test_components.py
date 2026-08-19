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
from typing import Any

from yostlabs.tss3.utils.tests.base import SensorTestBase, TestResult, TestStatus
from yostlabs.tss3.utils.streaming import ThreespaceStreamingManager, ThreespaceStreamingStatus
from yostlabs.tss3.api import ThreespaceSensor, StreamableCommands
from yostlabs.math.vector import vec_len, vec_dot, vec_normalize
from yostlabs.math.quaternion import quat_mul, quat_from_axis_angle, quat_rotate_vec

import logging
logger = logging.getLogger(__name__)


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
    BaroBaseline = 11
    BaroAwaitingRaise = 12
    BaroAwaitingLower = 13
    Finished = 14


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

    CHECK_UPDATE_RATE_WAIT_DURATION = 3.0    # seconds to wait before checking update rate (gives time for it to update, including settling time)
    UPDATE_RATE_TOLERANCE = 0.01    # 1% tolerance for update rate vs true ODR
    GYRO_ACCEL_DOT_THRESHOLD = 0.5  # minimum acceptable dot product for gyro-accel cross-check
    MAG_MIN_LENGTH = 0.21           # minimum acceptable average mag vector magnitude
    GYRO_FLIP_MIN_DEGREES = 120.0   # integrated rotation threshold to count as a flip
    BARO_MIN_ALTITUDE_CHANGE = 0.3048  # 1 foot in meters; minimum altitude delta for baro test
    BARO_EMA_ALPHA        = 0.1        # IIR smoothing factor α; higher = faster response, more noise
    BARO_STABLE_THRESHOLD = 0.2        # metres; max EMA range within window to be considered stable
    BARO_STABLE_DURATION  = 0.5        # seconds the stability condition must hold continuously
    BARO_WINDOW_SAMPLES   = 25         # ~0.5 s at 50 Hz

    # Per-component checks created for every detected accel/gyro/mag/baro. Baro additionally gets "altitude".
    COMPONENT_CHECKS = ("set_odr_1000", "update_rate_1000", "static_check", "set_odr_50", "update_rate_50", "flip")

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

        self._baro_fail_flag: bool = False
        self._baro_ema_state: dict[int, float | None] = {}
        self._baro_stable_since: float | None = None

        # Keys used in self.result:
        # - "valid_components" -> TestResult
        # - (ctype, cid, check_name) -> TestResult, populated in CheckingComponents for every detected component.
        #   check_name is one of COMPONENT_CHECKS, plus "altitude" for baro.
        # - ("gyro_accel_check", gyro_id, accel_id) -> TestResult, populated during flip analysis.
        self.result: dict[Any, TestResult] = {
            "valid_components": TestResult("component", "valid_components"),
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
            case ComponentTestState.BaroBaseline:
                self.__update_baro_baseline()
            case ComponentTestState.BaroAwaitingRaise:
                self.__update_baro_awaiting_raise()
            case ComponentTestState.BaroAwaitingLower:
                self.__update_baro_awaiting_lower()

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

    def notify_baro_fail(self):
        """Manually fail the barometer altitude test (e.g. if stable position is never reached)."""
        if self.state not in (ComponentTestState.BaroBaseline,
                              ComponentTestState.BaroAwaitingRaise,
                              ComponentTestState.BaroAwaitingLower):
            return
        self._baro_fail_flag = True

    # ------------------------------------------------------------------
    # Private state handlers
    # ------------------------------------------------------------------

    def __update_checking_components(self):
        detected_str = self.sensor.readValidComponents()
        result = self.result["valid_components"]
        result.measurements["detected"] = detected_str

        self._accel_ids = list(self.sensor.valid_accels)
        self._gyro_ids = list(self.sensor.valid_gyros)
        self._mag_ids = list(self.sensor.valid_mags)
        self._baro_ids = list(self.sensor.valid_baros)

        # Initialise per-component result entries now that IDs are known
        for ctype, ids in [("accel", self._accel_ids), ("gyro", self._gyro_ids),
                           ("mag", self._mag_ids), ("baro", self._baro_ids)]:
            for cid in ids:
                self.__make_component_entries(ctype, cid)

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
            detected_list = [c.strip().lower() for c in detected_str.split(',') if c.strip()]
            result.add_criteria("expected", list(self._expected_components))
            matches = set(detected_list) == set(c.lower() for c in self._expected_components)
            result.set_status(TestStatus.PASS if matches else TestStatus.FAIL)
        else:
            result.set_status(TestStatus.INFO)
            result.message = "No expected components supplied; detected components recorded for reference only."

        self.__go_next_state()

    def __update_setting_odr(self, target_odr: int, check_name: str):
        odr_methods = {
            "accel": (self._accel_ids, self.sensor.writeOdrAccel, self.sensor.readOdrAccel),
            "gyro":  (self._gyro_ids,  self.sensor.writeOdrGyro,  self.sensor.readOdrGyro),
            "mag":   (self._mag_ids,   self.sensor.writeOdrMag,   self.sensor.readOdrMag),
            "baro":  (self._baro_ids,  self.sensor.writeOdrBaro,  self.sensor.readOdrBaro),
        }

        for ctype, (ids, write_fn, read_fn) in odr_methods.items():
            for cid in ids:
                result = self.__comp_result(ctype, cid, check_name)
                err = write_fn(cid, target_odr)
                if err != 0:
                    result.add_measurement("error", err)
                    result.set_status(TestStatus.FAIL)
                else:
                    result.measurements["true_odr"] = read_fn(cid)
                    result.set_status(TestStatus.PASS)

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

    def __update_reading_update_rate(self, odr_check_name: str, rate_check_name: str):
        def _check(ctype, cid, measured_rate):
            odr_result = self.__comp_result(ctype, cid, odr_check_name)
            rate_result = self.__comp_result(ctype, cid, rate_check_name)
            rate_result.measurements["actual"] = measured_rate
            if not odr_result.success:
                # ODR was not set successfully; skip rate check for this component
                rate_result.skipped("ODR was not set successfully; rate check skipped.")
                return
            true_odr = odr_result.measurements["true_odr"]
            tolerance = true_odr * self.UPDATE_RATE_TOLERANCE
            rate_result.add_criteria("expected", true_odr)
            rate_result.add_criteria("tolerance", tolerance)
            passed = abs(measured_rate - true_odr) <= tolerance
            rate_result.set_status(TestStatus.PASS if passed else TestStatus.FAIL)

        for accel_id in self._accel_ids:
            _check("accel", accel_id, self.sensor.readUpdateRateAccel(accel_id))
        for gyro_id in self._gyro_ids:
            _check("gyro", gyro_id, self.sensor.readUpdateRateGyro(gyro_id))
        for mag_id in self._mag_ids:
            _check("mag", mag_id, self.sensor.readUpdateRateMag(mag_id))
        for baro_id in self._baro_ids:
            _check("baro", baro_id, self.sensor.readUpdateRateBaro(baro_id))

        self.__go_next_state()

    def __update_analyzing_flip_data(self):
        self.__analyze_accel_flip()
        self.__analyze_gyro_flip()
        self.__analyze_mag_flip()
        self.__go_next_state()

    # ------------------------------------------------------------------
    # Result helpers
    # ------------------------------------------------------------------

    def __comp_result(self, ctype: str, cid: int, check_name: str) -> TestResult:
        """Returns the TestResult for a specific component's check."""
        return self.result[(ctype, cid, check_name)]

    def __make_component_entries(self, ctype: str, cid: int):
        """Creates a fresh TestResult for every check of a specific component."""
        component_label = f"{ctype}:{cid}"
        for check_name in self.COMPONENT_CHECKS:
            if ctype == "baro" and check_name == "flip":
                continue  # baro does not have a flip check
            self.result[(ctype, cid, check_name)] = TestResult(
                "component", f"{ctype}.{check_name}", components=[component_label]
            )
        if ctype == "baro":
            self.result[(ctype, cid, "altitude")] = TestResult(
                "component", f"{ctype}.altitude", components=[component_label]
            )

    # ------------------------------------------------------------------
    # Static data analysis
    # ------------------------------------------------------------------

    def __analyze_static_data(self):
        for accel_id in self._accel_ids:
            is_static, error = self.__check_static_vector(self._static_samples, "accel", accel_id)
            result = self.__comp_result("accel", accel_id, "static_check")
            if is_static:
                result.failed(error)
            else:
                result.set_status(TestStatus.PASS)

        for gyro_id in self._gyro_ids:
            is_static, error = self.__check_static_vector(self._static_samples, "gyro", gyro_id)
            result = self.__comp_result("gyro", gyro_id, "static_check")
            if is_static:
                result.failed(error)
            else:
                result.set_status(TestStatus.PASS)

        for mag_id in self._mag_ids:
            is_static, error = self.__check_static_vector(self._static_samples, "mag", mag_id)
            mag_vecs = self._static_samples.get("mag", {}).get(mag_id, [])
            mag_len = sum(vec_len(v) for v in mag_vecs) / len(mag_vecs) if mag_vecs else 0.0
            length_ok = mag_len >= self.MAG_MIN_LENGTH
            result = self.__comp_result("mag", mag_id, "static_check")
            result.measurements["avg_length"] = mag_len
            result.add_criteria("min_length", self.MAG_MIN_LENGTH)
            if is_static:
                result.failed(error)
            elif not length_ok:
                result.failed(f"Average magnitude {mag_len} below minimum {self.MAG_MIN_LENGTH}.")
            else:
                result.set_status(TestStatus.PASS)

        for baro_id in self._baro_ids:
            is_static, error = self.__check_static_scalar(self._static_samples, "baro", baro_id)
            result = self.__comp_result("baro", baro_id, "static_check")
            if is_static:
                result.failed(error)
            else:
                result.set_status(TestStatus.PASS)

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
            result = self.__comp_result("accel", accel_id, "flip")
            if len(values) < 2:
                result.failed("insufficient samples")
                continue
            dot = vec_dot(vec_normalize(values[0]), vec_normalize(values[-1]))
            direction_changed = dot < 0.0
            result.measurements["dot_product"] = dot
            result.measurements["direction_changed"] = direction_changed
            result.set_status(TestStatus.PASS if direction_changed else TestStatus.FAIL)

    def __analyze_mag_flip(self):
        for mag_id in self._mag_ids:
            values = self._flip_samples.get("mag", {}).get(mag_id, [])
            result = self.__comp_result("mag", mag_id, "flip")
            if len(values) < 2:
                result.failed("insufficient samples")
                continue
            dot = vec_dot(vec_normalize(values[0]), vec_normalize(values[-1]))
            direction_changed = dot < 0.0
            result.measurements["dot_product"] = dot
            result.measurements["direction_changed"] = direction_changed
            result.set_status(TestStatus.PASS if direction_changed else TestStatus.FAIL)

    def __analyze_gyro_flip(self):
        principal_axes = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]

        for gyro_id in self._gyro_ids:
            times = self._flip_samples.get("time", [])
            gyro_values = self._flip_samples.get("gyro", {}).get(gyro_id, [])
            timed_values = [(t, g) for t, g in zip(times, gyro_values) if g is not None]
            result = self.__comp_result("gyro", gyro_id, "flip")
            if len(timed_values) < 2:
                result.failed("insufficient samples for integration")
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

            result.measurements["max_rotation_deg"] = max_deg
            result.measurements["best_axis"] = best_axis
            result.add_criteria("min_rotation_deg", self.GYRO_FLIP_MIN_DEGREES)
            result.set_status(TestStatus.PASS if passed else TestStatus.FAIL)

            # Cross-check against every accel that passed its own flip test:
            # rotate the initial accel vector by q and verify it aligns with
            # the observed final accel vector (dot >= self.GYRO_ACCEL_DOT_THRESHOLD).
            for accel_id in self._accel_ids:
                accel_result = self.__comp_result("accel", accel_id, "flip")
                if not accel_result.success:
                    continue
                accel_vals = self._flip_samples.get("accel", {}).get(accel_id, [])
                if len(accel_vals) < 2:
                    continue
                predicted = quat_rotate_vec(q, accel_vals[0])
                dot = vec_dot(vec_normalize(predicted), vec_normalize(accel_vals[-1]))
                check_passed = dot >= self.GYRO_ACCEL_DOT_THRESHOLD

                cross_check = TestResult(
                    "component", "gyro_accel_check",
                    components=[f"gyro:{gyro_id}", f"accel:{accel_id}"]
                )
                cross_check.measurements["dot_product"] = dot
                cross_check.add_criteria("min_dot_product", self.GYRO_ACCEL_DOT_THRESHOLD)
                cross_check.set_status(TestStatus.PASS if check_passed else TestStatus.FAIL)
                self.result[("gyro_accel_check", gyro_id, accel_id)] = cross_check

    # ------------------------------------------------------------------
    # Barometer altitude test
    # ------------------------------------------------------------------

    def __update_baro_baseline(self):
        if self._baro_fail_flag:
            self.__fail_baro_test()
            return
        self._manager.update()
        if all(len(self._current_samples.get("baro", {}).get(bid, [])) >= self.BARO_WINDOW_SAMPLES
               for bid in self._baro_ids):
            for baro_id in self._baro_ids:
                result = self.__comp_result("baro", baro_id, "altitude")
                starting_altitude = self.baro_window_avg(baro_id)
                result.measurements["starting_altitude"] = starting_altitude
                result.add_criteria("min_altitude_change_m", self.BARO_MIN_ALTITUDE_CHANGE)
                result.add_criteria("high_altitude_threshold", starting_altitude + self.BARO_MIN_ALTITUDE_CHANGE)
            self.__go_next_state()

    def __update_baro_awaiting_raise(self):
        if self._baro_fail_flag:
            self.__fail_baro_test()
            return
        self._manager.update()
        if not all(len(self._current_samples.get("baro_ema", {}).get(bid, [])) >= self.BARO_WINDOW_SAMPLES
                   for bid in self._baro_ids):
            return
        for bid in self._baro_ids:
            result = self.__comp_result("baro", bid, "altitude")
            baro_value = self.baro_window_avg(bid)
            if baro_value < result.measurements["starting_altitude"]:
                previous = result.measurements["starting_altitude"]
                result.measurements["starting_altitude"] = baro_value
                result.criteria["high_altitude_threshold"] = baro_value + self.BARO_MIN_ALTITUDE_CHANGE
                logger.info("Updated starting altitude threshold from %s to %s", previous, baro_value)
        condition_met = all(
            self.__baro_is_stable(bid) and
            self.baro_window_avg(bid) >= self.__comp_result("baro", bid, "altitude").criteria["high_altitude_threshold"]
            for bid in self._baro_ids
        )
        if condition_met:
            if self._baro_stable_since is None:
                self._baro_stable_since = time.perf_counter()
            elif time.perf_counter() - self._baro_stable_since >= self.BARO_STABLE_DURATION:
                for baro_id in self._baro_ids:
                    high_alt = self.baro_window_avg(baro_id)
                    result = self.__comp_result("baro", baro_id, "altitude")
                    result.measurements["high_altitude"] = high_alt
                    result.add_criteria("low_altitude_threshold", high_alt - self.BARO_MIN_ALTITUDE_CHANGE)
                    self._current_samples["baro"][baro_id] = self._current_samples["baro"][baro_id][-self.BARO_WINDOW_SAMPLES:]
                    self._current_samples["baro_ema"][baro_id] = self._current_samples["baro_ema"][baro_id][-self.BARO_WINDOW_SAMPLES:]
                self._baro_stable_since = None
                self.__go_next_state()
        else:
            self._baro_stable_since = None

    def __update_baro_awaiting_lower(self):
        if self._baro_fail_flag:
            self.__fail_baro_test()
            return
        self._manager.update()
        if not all(len(self._current_samples.get("baro_ema", {}).get(bid, [])) >= self.BARO_WINDOW_SAMPLES
                   for bid in self._baro_ids):
            return
        condition_met = all(
            self.__baro_is_stable(bid) and
            self.baro_window_avg(bid) <= self.__comp_result("baro", bid, "altitude").measurements["high_altitude"] - self.BARO_MIN_ALTITUDE_CHANGE
            for bid in self._baro_ids
        )
        if condition_met:
            if self._baro_stable_since is None:
                self._baro_stable_since = time.perf_counter()
            elif time.perf_counter() - self._baro_stable_since >= self.BARO_STABLE_DURATION:
                for baro_id in self._baro_ids:
                    result = self.__comp_result("baro", baro_id, "altitude")
                    result.measurements["low_altitude"] = self.baro_window_avg(baro_id)
                    result.set_status(TestStatus.PASS)
                self.__go_next_state()
        else:
            self._baro_stable_since = None

    def __enter_baro_test(self):
        """Check whether any baro passed static check and, if so, initialise the baro altitude test."""
        any_baro_ok = any(
            self.__comp_result("baro", bid, "static_check").success
            for bid in self._baro_ids
        )
        if not any_baro_ok:
            return False
        self._current_samples = self._make_samples_dict()
        self._baro_fail_flag = False
        self._baro_ema_state = {}
        self._baro_stable_since = None
        self._setup_manager(hz=50)
        return True

    def __fail_baro_test(self):
        for baro_id in self._baro_ids:
            self.__comp_result("baro", baro_id, "altitude").failed()
        self.state = ComponentTestState.Finished
        self.__cleanup()

    def baro_window_avg(self, baro_id: int) -> float:
        return self._current_samples["baro_ema"][baro_id][-1]

    def __baro_is_stable(self, baro_id: int) -> bool:
        window = self._current_samples["baro_ema"][baro_id][-self.BARO_WINDOW_SAMPLES:]
        return max(window) - min(window) < self.BARO_STABLE_THRESHOLD

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
            d["baro_ema"] = {cid: [] for cid in self._baro_ids}
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
            val = self._manager.get_value(StreamableCommands.GetBarometerAltitudeById, baro_id)
            self._current_samples["baro"][baro_id].append(val)
            prev = self._baro_ema_state.get(baro_id)
            ema = val if (prev is None or val is None) else self.BARO_EMA_ALPHA * val + (1 - self.BARO_EMA_ALPHA) * prev
            self._baro_ema_state[baro_id] = ema
            self._current_samples["baro_ema"][baro_id].append(ema)

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
                if self.__enter_baro_test():
                    self.state = ComponentTestState.BaroBaseline
                else:
                    self.state = ComponentTestState.Finished
                    self.__cleanup()
            case ComponentTestState.BaroBaseline:
                self.state = ComponentTestState.BaroAwaitingRaise
            case ComponentTestState.BaroAwaitingRaise:
                self.state = ComponentTestState.BaroAwaitingLower
            case ComponentTestState.BaroAwaitingLower:
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

def _print_baro_status(test: ComponentTest) -> None:
    """Print an overwriting single-word status during baro awaiting states."""
    bids = test._baro_ids
    if not bids:
        return
    
    if test.state == ComponentTestState.BaroAwaitingRaise:
        if any(test.baro_window_avg(bid) < test.result[("baro", bid, "altitude")].criteria["high_altitude_threshold"] for bid in bids):
            print("RAISE      \r", end="", flush=True)
        else:
            print("HOLD       \r", end="", flush=True)
    elif test.state == ComponentTestState.BaroAwaitingLower:
        if any(test.baro_window_avg(bid) > test.result[("baro", bid, "altitude")].criteria["low_altitude_threshold"] for bid in bids):
            print("LOWER      \r", end="", flush=True)
        else:
            print("HOLD       \r", end="", flush=True)


def print_results(results: list[TestResult], show_only_failures: bool = False):
    """Print component test results in a human-readable indented format.

    Parameters
    ----------
    results:
        The ``ComponentTest.result_flat`` list.
    show_only_failures:
        When True, only entries that did not pass are shown.
    """
    for result in results:
        if show_only_failures and result.success:
            continue
        components = f" [{', '.join(result.components)}]" if result.components else ""
        message = f" - {result.message}" if result.message else ""
        print(f"{result.check}{components}: {result.status}{message}")
        for name, value in result.measurements.items():
            print(f"    {name}: {value}")


def run_test(sensor: ThreespaceSensor, show_only_failures: bool = False, expected_components: list[str] | None = None) -> tuple[bool | None, list[TestResult]]:
    test = ComponentTest(sensor, expected_components=expected_components)

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
        try:
            while test.state != ComponentTestState.Finished:
                if test.state != last_state:
                    # Exiting a baro-awaiting state: terminate the \r status line
                    if last_state in (ComponentTestState.BaroAwaitingRaise,
                                    ComponentTestState.BaroAwaitingLower):
                        print()
                    if test.state == ComponentTestState.AwaitingFlatSurface:
                        print("Place the sensor on a flat, level surface, then press Enter.")
                        _start_waiting_for_enter()
                        awaiting_enter = True
                    elif test.state == ComponentTestState.StreamingFlip:
                        print("Streaming started. Flip the sensor upside down, then press Enter.")
                        _start_waiting_for_enter()
                        awaiting_enter = True
                    elif test.state == ComponentTestState.BaroAwaitingRaise:
                        print(f"Raise the sensor at least 1 ft ({ComponentTest.BARO_MIN_ALTITUDE_CHANGE:.3f} m) "
                            f"above its starting position and hold still. Call test.notify_baro_fail() to skip.")
                    elif test.state == ComponentTestState.BaroAwaitingLower:
                        print(f"Lower the sensor at least 1 ft ({ComponentTest.BARO_MIN_ALTITUDE_CHANGE:.3f} m) "
                            f"below its starting position and hold still. Call test.notify_baro_fail() to skip.")
                    last_state = test.state

                if awaiting_enter and _enter_event.is_set():
                    awaiting_enter = False
                    if test.state == ComponentTestState.AwaitingFlatSurface:
                        test.notify_flat_ready()
                    elif test.state == ComponentTestState.StreamingFlip:
                        test.notify_flip_done()

                test.update()
                _print_baro_status(test)
                time.sleep(0.005)
        except KeyboardInterrupt:
            if test.state in (ComponentTestState.BaroAwaitingRaise, ComponentTestState.BaroAwaitingLower):
                print("\nBarometer test interrupted by user. Marking barometer test as failed.")
                test.notify_baro_fail()
                # outer while re-enters the inner loop to finish the test
            else:
                test.cancel()
                print("\nTest cancelled by user.")
                return (False if not test.overall_success else None), test.result_flat
    return test.overall_success, test.result_flat


def auto_run_test(show_only_failures: bool = False):
    sensor = ThreespaceSensor()
    overall_success, results = run_test(sensor, show_only_failures)
    sensor.cleanup()
    print_results(results, show_only_failures)
    print(f"\nOverall success: {overall_success}")

    import json
    with open("component_test_results.json", "w") as f:
        json.dump([r.to_dict() for r in results], f, indent=4)

    return overall_success, results


if __name__ == "__main__":
    auto_run_test(show_only_failures=False)
