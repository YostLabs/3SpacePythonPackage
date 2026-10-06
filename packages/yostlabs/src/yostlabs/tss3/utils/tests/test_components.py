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

import math
import time
from typing import Any

from yostlabs.tss3.utils.tests.base import SensorTest, ALL_SENSORS, TestResult, TestStatus, Busy, Message, SkipStep, step
from yostlabs.tss3.utils.tests.cli import main
from yostlabs.tss3.utils.streaming import ThreespaceStreamingManager, ThreespaceStreamingStatus
from yostlabs.tss3.api import ThreespaceSensor, StreamableCommands
from yostlabs.tss3.errors import SettingError
from yostlabs.math.vector import vec_len, vec_dot, vec_normalize
from yostlabs.math.quaternion import quat_mul, quat_from_axis_angle, quat_rotate_vec

import logging
logger = logging.getLogger(__name__)


class ComponentTest(SensorTest):
    """
    Tests the data components of the sensor (Accel, Gyro, Mag, Barometer).

    Detect components:
        readValidComponents(). If expected_components supplied, compare and record pass/fail.
        Continue testing all detected components regardless.
    1000 Hz data:
        Set ODR=1000 for all components. Record any errors. Read back true set ODR.
        Stream component data until CHECK_UPDATE_RATE_WAIT_DURATION after setting the ODR.
        - Verify no component has unchanging (static) data.
        - Mag: additionally verify average vector length is not near 0.
        Compare measured update rates to the 1000 ODR true values (within 1% tolerance).
    Flip:
        Set ODR=50 for all components. Read back true set ODR.
        The operator places the sensor on a flat surface, then flips it upside down while it streams.
        Compare measured update rates to the 50 ODR true values (within 1% tolerance).
        Analyze flip data per component:
        - Accel: verify gravity vector direction reversed.
        - Gyro: verify integrated rotation >= 120 degrees (raw gyro assumed in rad/s),
          and that it predicts the change in each accel's direction.
        - Mag: verify field vector direction reversed.
    Barometer raise, Barometer lower (if any barometer's data changed in 1000 Hz data):
        Measure the starting altitude while the sensor is held still. The operator then raises and lowers
        the sensor at least 1 ft, holding it still each time. The operator may pass or fail each stage by hand
        if detection doesn't trigger.

    Checks are created per component, keyed (component type, id, check name) in self.results.
    """

    id = "component"
    name = "Component"
    description = "Tests the primary data components of the sensor (Accel, Gyro, Mag, Barometer)."
    variants = ALL_SENSORS

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
    STREAMING_HZ = 50

    # Per-component checks created for every detected accel/gyro/mag/baro. Baro has no flip, but gets "altitude".
    COMPONENT_CHECKS = ("set_odr_1000", "update_rate_1000", "static_check", "set_odr_50", "update_rate_50", "flip")

    STREAM_COMMANDS = {
        "accel": StreamableCommands.GetRawAccelVec,
        "gyro": StreamableCommands.GetRawGyroRate,
        "mag": StreamableCommands.GetRawMagVec,
        "baro": StreamableCommands.GetBarometerAltitudeById,
    }

    def __init__(self, sensor: ThreespaceSensor,
                 expected_components: list[str] | None = None,
                 streaming_manager: ThreespaceStreamingManager = None):
        super().__init__(sensor, streaming_manager)
        self._expected_components = expected_components
        self._ids: dict[str, list[int]] = {}    # Component type -> ids, Ex: {"accel": [0, 1]}
        self._odr_set_time: float = None

        # Streamed data. "time": [s], <type>: {id: [values]}, "baro_ema": {id: [smoothed altitudes]}
        self._samples: dict = {}
        self._flip_samples: dict = {}
        self._baro_ema_state: dict[int, float | None] = {}
        self._baro_active = False   # The barometer altitude stages are running

    # ------------------------------------------------------------------
    # Steps
    # ------------------------------------------------------------------

    @step("Detect components")
    def valid_components(self):
        detected_str = self.sensor.readValidComponents()
        result = self.check()
        result.add_measurement("detected", detected_str)
        detected_list = [c.strip().lower() for c in detected_str.split(',')] if detected_str else []
        result.add_measurement("detected_modified", [c.split("_")[0] for c in detected_list])

        self._ids = {"accel": list(self.sensor.valid_accels), "gyro": list(self.sensor.valid_gyros),
                     "mag": list(self.sensor.valid_mags), "baro": list(self.sensor.valid_baros)}
        for ctype, cid in self._components():
            self._make_component_checks(ctype, cid)

        if self._expected_components is not None:
            expected_modified = [c.split("_")[0].lower() for c in self._expected_components]  # split into type and ID
            result.add_criteria("expected", list(self._expected_components))
            result.add_criteria("expected_modified", expected_modified)
            matches = set(result.measurements["detected_modified"]) == set(expected_modified)
            result.set_status(TestStatus.PASS if matches else TestStatus.FAIL)
        else:
            result.set_status(TestStatus.INFO)
            result.message = "No expected components supplied; detected components recorded for reference only."

    @step("1000 Hz data", check=None)
    def data_1000(self):
        self._set_odr(1000, "set_odr_1000")
        yield from self._collect_static_data()
        yield from self._check_update_rates("set_odr_1000", "update_rate_1000")

    @step("Flip", check=None)
    def flip(self):
        self._set_odr(50, "set_odr_50")
        yield Message("Place the sensor on a flat, level surface.")
        yield from self._collect_flip_data()
        yield from self._check_update_rates("set_odr_50", "update_rate_50")
        self._analyze_flip("accel")
        self._analyze_flip("mag")
        self._analyze_gyro_flip()

    @step("Barometer raise", check=None)
    def baro_raise(self):
        if not any(self._result("baro", bid, "static_check").success for bid in self._ids["baro"]):
            raise SkipStep()
        yield from self._baro_baseline()
        if not self._baro_active:   # Failed by the operator while measuring the baseline
            return
        baros = self._ids["baro"]
        stable_since = None
        while True:
            self.streaming_manager.update()
            results = {bid: self._result("baro", bid, "altitude") for bid in baros}
            for bid, result in results.items():
                # Lower the start if the sensor sank before being raised
                altitude = self._baro_altitude(bid)
                if altitude < result.measurements["starting_altitude"]:
                    logger.info("Updated starting altitude threshold from %s to %s", result.measurements["starting_altitude"], altitude)
                    result.measurements["starting_altitude"] = altitude
                    result.criteria["high_altitude_threshold"] = altitude + self.BARO_MIN_ALTITUDE_CHANGE

            if all(self._baro_is_stable(bid) and self._baro_altitude(bid) >= result.criteria["high_altitude_threshold"]
                   for bid, result in results.items()):
                stable_since = stable_since or time.perf_counter()
                if time.perf_counter() - stable_since >= self.BARO_STABLE_DURATION:
                    for bid, result in results.items():
                        self._set_high_altitude(result, self._baro_altitude(bid))
                    self._keep_last_baro_window()
                    return
            else:
                stable_since = None

            raising = any(self._baro_altitude(bid) < result.criteria["high_altitude_threshold"] for bid, result in results.items())
            action = yield Busy(f"Raise the sensor at least 1 ft ({self.BARO_MIN_ALTITUDE_CHANGE:.3f} m) above its "
                                f"starting position and hold it still.",
                                status="Raise" if raising else "Hold", actions=("Pass anyway", "Fail"))
            if action == "Fail":
                self._fail_baro()
                return
            if action == "Pass anyway":
                for bid, result in results.items():
                    default = result.measurements["starting_altitude"] + self.BARO_MIN_ALTITUDE_CHANGE
                    self._set_high_altitude(result, self._baro_altitude(bid, default))
                    result.add_measurement("force_passed_high", True)
                return

    @step("Barometer lower", check=None)
    def baro_lower(self):
        if not self._baro_active:
            raise SkipStep()
        baros = self._ids["baro"]
        stable_since = None
        while True:
            self.streaming_manager.update()
            results = {bid: self._result("baro", bid, "altitude") for bid in baros}
            if all(self._baro_is_stable(bid) and self._baro_altitude(bid) <= result.criteria["low_altitude_threshold"]
                   for bid, result in results.items()):
                stable_since = stable_since or time.perf_counter()
                if time.perf_counter() - stable_since >= self.BARO_STABLE_DURATION:
                    for bid, result in results.items():
                        result.add_measurement("low_altitude", self._baro_altitude(bid)).passed()
                    break
            else:
                stable_since = None

            lowering = any(self._baro_altitude(bid) > result.criteria["low_altitude_threshold"] for bid, result in results.items())
            action = yield Busy(f"Lower the sensor at least 1 ft ({self.BARO_MIN_ALTITUDE_CHANGE:.3f} m) below the "
                                f"raised position and hold it still.",
                                status="Lower" if lowering else "Hold", actions=("Pass anyway", "Fail"))
            if action == "Fail":
                self._fail_baro()
                return
            if action == "Pass anyway":
                for bid, result in results.items():
                    default = result.measurements["high_altitude"] - self.BARO_MIN_ALTITUDE_CHANGE
                    result.add_measurement("low_altitude", self._baro_altitude(bid, default))
                    result.add_measurement("force_passed_low", True).passed()
                break
        self._baro_active = False
        self.stop_streaming()

    # ------------------------------------------------------------------
    # Results
    # ------------------------------------------------------------------

    def _components(self, *types: str):
        """(type, id) of every detected component, or of only the given types"""
        return [(ctype, cid) for ctype, ids in self._ids.items() if not types or ctype in types for cid in ids]

    def _result(self, ctype: str, cid: int, check_name: str) -> TestResult:
        return self.results[(ctype, cid, check_name)]

    def _make_component_checks(self, ctype: str, cid: int):
        checks = [c for c in self.COMPONENT_CHECKS if not (ctype == "baro" and c == "flip")]
        if ctype == "baro":
            checks.append("altitude")
        for check_name in checks:
            self.results[(ctype, cid, check_name)] = TestResult(self.id, f"{ctype}.{check_name}", components=[f"{ctype}:{cid}"])

    # ------------------------------------------------------------------
    # Data rates
    # ------------------------------------------------------------------

    def _set_odr(self, odr: int, check_name: str):
        for ctype, cid in self._components():
            result = self._result(ctype, cid, check_name)
            key = f"odr_{ctype}{cid}"
            try:
                self.change_settings(**{key: odr})
            except SettingError as e:
                result.add_measurement("error", str(e)).failed()
                continue
            result.add_measurement("true_odr", self.sensor.read_settings(key)[key]).passed()
        self._odr_set_time = time.perf_counter()

    def _check_update_rates(self, odr_check_name: str, rate_check_name: str):
        # The update rate needs time after setting the ODR to settle
        remaining = self.CHECK_UPDATE_RATE_WAIT_DURATION - (time.perf_counter() - self._odr_set_time)
        if remaining > 0:
            yield from self.wait(remaining, "Waiting for the update rates to settle.")

        for ctype, cid in self._components():
            odr_result = self._result(ctype, cid, odr_check_name)
            rate_result = self._result(ctype, cid, rate_check_name)
            key = f"update_rate_{ctype}{cid}"
            measured_rate = self.sensor.read_settings(key)[key]
            rate_result.add_measurement("actual", measured_rate)
            if not odr_result.success:
                rate_result.skipped("ODR was not set successfully; rate check skipped.")
                continue
            true_odr = odr_result.measurements["true_odr"]
            tolerance = true_odr * self.UPDATE_RATE_TOLERANCE
            rate_result.add_criteria("expected", true_odr)
            rate_result.add_criteria("tolerance", tolerance)
            rate_result.set_status(TestStatus.PASS if abs(measured_rate - true_odr) <= tolerance else TestStatus.FAIL)

    # ------------------------------------------------------------------
    # Data collection
    # ------------------------------------------------------------------

    def _collect_static_data(self):
        """Streams until the update rates have settled after setting the ODR, then checks every component's data changes"""
        self._start_sampling()
        while time.perf_counter() - self._odr_set_time < self.CHECK_UPDATE_RATE_WAIT_DURATION:
            self.streaming_manager.update()
            yield Busy("Collecting component data.")
        self.stop_streaming()
        self._analyze_static_data(self._samples)

    def _collect_flip_data(self):
        """Streams while the operator flips the sensor"""
        self._start_sampling()
        while True:
            self.streaming_manager.update()
            if (yield Busy("Flip the sensor upside down.", actions=("Flipped",))) == "Flipped":
                break
        self.stop_streaming()
        self._flip_samples = self._samples

    # ------------------------------------------------------------------
    # Static data analysis
    # ------------------------------------------------------------------

    def _analyze_static_data(self, samples: dict):
        for ctype, cid in self._components():
            values = samples[ctype][cid]
            result = self._result(ctype, cid, "static_check")
            error = self._static_error(values)
            if error is not None:
                result.failed(error)
                continue
            if ctype == "mag":
                mag_len = sum(vec_len(v) for v in values) / len(values)
                result.add_measurement("avg_length", mag_len)
                result.add_criteria("min_length", self.MAG_MIN_LENGTH)
                if mag_len < self.MAG_MIN_LENGTH:
                    result.failed(f"Average magnitude {mag_len} below minimum {self.MAG_MIN_LENGTH}.")
                    continue
            result.passed()

    @staticmethod
    def _static_error(values: list) -> str | None:
        """Why the values show no variation, or None if they vary"""
        if len(values) < 2:
            return "insufficient samples"
        as_list = lambda v: v if isinstance(v, (list, tuple)) else [v]
        first = as_list(values[0])
        if any(abs(a - b) > 1e-9 for v in values[1:] for a, b in zip(as_list(v), first)):
            return None
        return f"all samples identical: {values[0]}"

    # ------------------------------------------------------------------
    # Flip data analysis
    # ------------------------------------------------------------------

    def _analyze_flip(self, ctype: str):
        """Accel and mag: the vector must point the opposite way after the flip"""
        for cid in self._ids[ctype]:
            values = self._flip_samples[ctype][cid]
            result = self._result(ctype, cid, "flip")
            if len(values) < 2:
                result.failed("insufficient samples")
                continue
            dot = vec_dot(vec_normalize(values[0]), vec_normalize(values[-1]))
            direction_changed = dot < 0.0
            result.add_measurement("dot_product", dot)
            result.add_measurement("direction_changed", direction_changed)
            result.set_status(TestStatus.PASS if direction_changed else TestStatus.FAIL)

    def _analyze_gyro_flip(self):
        principal_axes = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]

        for gyro_id in self._ids["gyro"]:
            times = self._flip_samples["time"]
            gyro_values = self._flip_samples["gyro"][gyro_id]
            timed_values = [(t, g) for t, g in zip(times, gyro_values) if g is not None]
            result = self._result("gyro", gyro_id, "flip")
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
                    q = quat_mul(q, quat_from_axis_angle(vec_normalize(gyro), angle))

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

            result.add_measurement("max_rotation_deg", max_deg)
            result.add_measurement("best_axis", best_axis)
            result.add_criteria("min_rotation_deg", self.GYRO_FLIP_MIN_DEGREES)
            result.set_status(TestStatus.PASS if max_deg >= self.GYRO_FLIP_MIN_DEGREES else TestStatus.FAIL)

            # Cross-check against every accel that passed its own flip test:
            # rotate the initial accel vector by q and verify it aligns with
            # the observed final accel vector (dot >= self.GYRO_ACCEL_DOT_THRESHOLD).
            for accel_id in self._ids["accel"]:
                if not self._result("accel", accel_id, "flip").success:
                    continue
                accel_vals = self._flip_samples["accel"][accel_id]
                predicted = quat_rotate_vec(q, accel_vals[0])
                dot = vec_dot(vec_normalize(predicted), vec_normalize(accel_vals[-1]))

                cross_check = TestResult(self.id, "gyro_accel_check", components=[f"gyro:{gyro_id}", f"accel:{accel_id}"])
                cross_check.add_measurement("dot_product", dot)
                cross_check.add_criteria("min_dot_product", self.GYRO_ACCEL_DOT_THRESHOLD)
                cross_check.set_status(TestStatus.PASS if dot >= self.GYRO_ACCEL_DOT_THRESHOLD else TestStatus.FAIL)
                self.results[("gyro_accel_check", gyro_id, accel_id)] = cross_check

    # ------------------------------------------------------------------
    # Barometer altitude
    # ------------------------------------------------------------------

    def _baro_baseline(self):
        """Starts streaming the barometers and records the starting altitude once enough samples are in"""
        self._baro_ema_state = {}
        self._start_sampling()
        self._baro_active = True
        while not self._baro_window_full():
            self.streaming_manager.update()
            if (yield Busy("Hold the sensor still.", actions=("Fail",))) == "Fail":
                self._fail_baro()
                return
        for bid in self._ids["baro"]:
            starting_altitude = self._baro_altitude(bid)
            result = self._result("baro", bid, "altitude")
            result.add_measurement("starting_altitude", starting_altitude)
            result.add_criteria("min_altitude_change_m", self.BARO_MIN_ALTITUDE_CHANGE)
            result.add_criteria("high_altitude_threshold", starting_altitude + self.BARO_MIN_ALTITUDE_CHANGE)

    def _baro_altitude(self, baro_id: int, default: float = None) -> float:
        """The latest smoothed altitude. default if there is none yet, or raises if no default is given."""
        values = self._samples["baro_ema"][baro_id]
        if not values and default is not None:
            return default
        return values[-1]

    def _baro_window_full(self) -> bool:
        return all(len(self._samples["baro_ema"][bid]) >= self.BARO_WINDOW_SAMPLES for bid in self._ids["baro"])

    def _baro_is_stable(self, baro_id: int) -> bool:
        window = self._samples["baro_ema"][baro_id][-self.BARO_WINDOW_SAMPLES:]
        return max(window) - min(window) < self.BARO_STABLE_THRESHOLD

    def _keep_last_baro_window(self):
        """Drops older samples, so the next stage judges stability on new data only"""
        for bid in self._ids["baro"]:
            for key in ("baro", "baro_ema"):
                self._samples[key][bid] = self._samples[key][bid][-self.BARO_WINDOW_SAMPLES:]

    def _set_high_altitude(self, result: TestResult, high_altitude: float):
        result.add_measurement("high_altitude", high_altitude)
        result.add_criteria("low_altitude_threshold", high_altitude - self.BARO_MIN_ALTITUDE_CHANGE)

    def _fail_baro(self):
        for bid in self._ids["baro"]:
            self._result("baro", bid, "altitude").failed("Failed by the operator.")
        self._baro_active = False
        self.stop_streaming()

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    def _start_sampling(self):
        """Clears the samples and streams every component, collecting one sample per packet"""
        self._samples = {"time": []} | {ctype: {cid: [] for cid in ids} for ctype, ids in self._ids.items()}
        self._samples["baro_ema"] = {cid: [] for cid in self._ids["baro"]}
        commands = [(StreamableCommands.GetTimestamp, None)] + \
                   [(self.STREAM_COMMANDS[ctype], cid) for ctype, cid in self._components()]
        self.start_streaming(commands, self._on_streaming_data, hz=self.STREAMING_HZ)

    def _on_streaming_data(self, status: ThreespaceStreamingStatus, user_data: Any = None):
        # Collect one sample per packet so gyro integration captures every update
        if status != ThreespaceStreamingStatus.Data:
            return
        manager = self.streaming_manager
        self._samples["time"].append(manager.get_value(StreamableCommands.GetTimestamp) / 1_000_000)
        for ctype, cid in self._components():
            self._samples[ctype][cid].append(manager.get_value(self.STREAM_COMMANDS[ctype], cid))
        for cid in self._ids["baro"]:
            value = self._samples["baro"][cid][-1]
            prev = self._baro_ema_state.get(cid)
            ema = value if (prev is None or value is None) else self.BARO_EMA_ALPHA * value + (1 - self.BARO_EMA_ALPHA) * prev
            self._baro_ema_state[cid] = ema
            self._samples["baro_ema"][cid].append(ema)

if __name__ == "__main__":
    import json
    session = main(ComponentTest)
    with open("component_test_results.json", "w") as f:
        json.dump([r.to_dict() for r in session.results], f, indent=4)
