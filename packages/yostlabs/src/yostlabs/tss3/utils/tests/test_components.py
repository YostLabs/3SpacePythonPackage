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

from yostlabs.tss3.utils.tests.base import SensorTest, ALL_SENSORS, TestKind, TestResult, TestStatus, Busy, Message, SkipStep, step
from yostlabs.tss3.utils.tests.cli import main
from yostlabs.tss3.utils.streaming import ThreespaceStreamingManager, ThreespaceStreamingStatus
from yostlabs.tss3.api import ThreespaceSensor, StreamableCommands
from yostlabs.tss3.errors import SettingError
import numpy as np

from yostlabs.math.vector import vec_len, vec_dot, vec_normalize
from yostlabs.math.quaternion import (quat_mul, quat_inverse, quat_from_axis_angle, quat_rotate_vec,
                                      quaternion_to_3x3_rotation_matrix)

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
        The operator places the sensor on a flat surface, then turns it, any way, until the face that was up faces
        down, while it streams calibrated data (see FLIP_STREAM_COMMANDS).
        Compare measured update rates to the 50 ODR true values (within 1% tolerance).
        Analyze flip data per component:
        - Accel: verify gravity vector direction reversed.
        - Mag: verify its reading changed by at least MAG_MIN_FIELD_CHANGE. The change between the first and last
          readings cancels any fixed offset, Ex: of a battery in the case, which turns with the sensor.
        - Gyro: verify integrated rotation >= 120 degrees (gyro in rad/s),
          and that it predicts the change in each accel's direction (gyro_accel_check),
          and each mag's readings through the whole flip (gyro_mag_check, see _check_gyro_mag()).
        None of these depend on where the sensor is tested: the field's strength and direction there.
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
    kind = TestKind.FUNCTIONAL

    CHECK_UPDATE_RATE_WAIT_DURATION = 3.0    # seconds to wait before checking update rate (gives time for it to update, including settling time)
    UPDATE_RATE_TOLERANCE = 0.01    # 1% tolerance for update rate vs true ODR
    GYRO_ACCEL_DOT_THRESHOLD = 0.5  # minimum acceptable dot product for gyro-accel cross-check
    MAG_MIN_LENGTH = 0.21           # minimum acceptable average mag vector magnitude
    # Minimum half the change in the mag reading over the flip, in gauss: the part of the Earth's field the flip turned
    # around. Far above noise, and below that part anywhere unless the flip turns the sensor about the field's direction
    MAG_MIN_FIELD_CHANGE = 0.05
    GYRO_MAG_MAX_FIT_ERROR = 0.25   # maximum gyro_mag_check fit error, relative to how far the mag readings moved
    GYRO_FLIP_MIN_DEGREES = 120.0   # integrated rotation threshold to count as a flip
    BARO_MIN_ALTITUDE_CHANGE = 0.3048  # 1 foot in meters; minimum altitude delta for baro test
    BARO_EMA_ALPHA        = 0.1        # IIR smoothing factor α; higher = faster response, more noise
    BARO_STABLE_THRESHOLD = 0.2        # metres; max EMA range within window to be considered stable
    BARO_STABLE_DURATION  = 0.5        # seconds the stability condition must hold continuously
    BARO_WINDOW_SAMPLES   = 25         # ~0.5 s at 50 Hz
    STREAMING_HZ = 50

    # Per-component checks created for every detected accel/gyro/mag/baro, then the one check each type has of its own
    COMPONENT_CHECKS = ("set_odr_1000", "update_rate_1000", "static_check", "set_odr_50", "update_rate_50")
    TYPE_CHECKS = {"accel": "flip", "gyro": "flip", "mag": "field_change", "baro": "altitude"}

    STREAM_COMMANDS = {
        "accel": StreamableCommands.GetRawAccelVec,
        "gyro": StreamableCommands.GetRawGyroRate,
        "mag": StreamableCommands.GetRawMagVec,
        "baro": StreamableCommands.GetBarometerAltitudeById,
    }
    # The flip judges calibrated data, so the accel and gyro read in their real units. The mag needs no calibration:
    # its checks cancel a fixed offset, Ex: a battery in the case, which can be far larger than the Earth's field
    FLIP_STREAM_COMMANDS = STREAM_COMMANDS | {
        "accel": StreamableCommands.GetCorrectedAccelVec,
        "gyro": StreamableCommands.GetCorrectedGyroRate,
        "mag": StreamableCommands.GetCorrectedMagVec,
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
        self._stream_commands = self.STREAM_COMMANDS   # Those _samples are of
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
            detected_modified = result.measurements["detected_modified"]
            # What differs, so a failure reads without comparing the two lists: in the order each list has them
            missing = [c for c in dict.fromkeys(expected_modified) if c not in detected_modified]
            extra = [c for c in dict.fromkeys(detected_modified) if c not in expected_modified]
            result.add_measurement("missing_components", missing)
            result.add_measurement("extra_components", extra)
            result.set_status(TestStatus.FAIL if missing or extra else TestStatus.PASS)
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
        self._analyze_accel_flip()
        self._analyze_mag_field_change()
        self._analyze_gyro_flip()   # After the accel and mag: it cross-checks them

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
        for check_name in self.COMPONENT_CHECKS + (self.TYPE_CHECKS[ctype],):
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
        """Streams calibrated data while the operator flips the sensor"""
        self._start_sampling(self.FLIP_STREAM_COMMANDS)
        while True:
            self.streaming_manager.update()
            if (yield Busy("Flip the sensor upside down.", actions=("Flipped",), default_action="Flipped")) == "Flipped":
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

    def _analyze_accel_flip(self):
        """The gravity vector must point the opposite way after the flip"""
        for cid in self._ids["accel"]:
            values = self._flip_samples["accel"][cid]
            result = self._result("accel", cid, "flip")
            if len(values) < 2:
                result.failed("insufficient samples")
                continue
            dot = vec_dot(vec_normalize(values[0]), vec_normalize(values[-1]))
            direction_changed = dot < 0.0
            result.add_measurement("dot_product", dot)
            result.add_measurement("direction_initial", values[0])
            result.add_measurement("direction_final", values[-1])
            result.add_measurement("direction_changed", direction_changed)
            result.set_status(TestStatus.PASS if direction_changed else TestStatus.FAIL)

    def _analyze_mag_field_change(self):
        """The mag reading must change by at least MAG_MIN_FIELD_CHANGE. The first and last readings are each the
        field plus the same fixed offset, so their difference is the field's change alone"""
        for cid in self._ids["mag"]:
            values = [v for v in self._flip_samples["mag"][cid] if v is not None]
            result = self._result("mag", cid, "field_change")
            if len(values) < 2:
                result.failed("insufficient samples")
                continue
            initial, final = values[0], values[-1]
            half_change = vec_len([a - b for a, b in zip(initial, final)]) / 2
            result.add_measurement("reading_initial", initial)
            result.add_measurement("reading_final", final)
            result.add_measurement("half_change", half_change)
            # About the fixed offset, give or take the field along the axis the sensor was turned about
            result.add_measurement("reading_midpoint", [(a + b) / 2 for a, b in zip(initial, final)])
            result.add_criteria("min_half_change", self.MAG_MIN_FIELD_CHANGE)
            if half_change < self.MAG_MIN_FIELD_CHANGE:
                result.failed("The reading barely changed. If the mag works, flip it about a different axis: turning "
                              "it about the Earth's field's direction doesn't change the field it reads.")
                continue
            result.passed()

    def _analyze_gyro_flip(self):
        principal_axes = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]

        for gyro_id in self._ids["gyro"]:
            result = self._result("gyro", gyro_id, "flip")
            if sum(g is not None for g in self._flip_samples["gyro"][gyro_id]) < 2:
                result.failed("insufficient samples for integration")
                continue
            orientations = self._gyro_orientations(gyro_id)
            q = orientations[-1]

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
            # turn the initial accel vector into the final frame (by q's inverse) and verify it aligns with
            # the observed final accel vector (dot >= self.GYRO_ACCEL_DOT_THRESHOLD).
            for accel_id in self._ids["accel"]:
                if not self._result("accel", accel_id, "flip").success:
                    continue
                accel_vals = self._flip_samples["accel"][accel_id]
                predicted = quat_rotate_vec(quat_inverse(q), accel_vals[0])   # q: final frame -> start frame
                dot = vec_dot(vec_normalize(predicted), vec_normalize(accel_vals[-1]))

                cross_check = TestResult(self.id, "gyro_accel_check", components=[f"gyro:{gyro_id}", f"accel:{accel_id}"])
                cross_check.add_measurement("dot_product", dot)
                cross_check.add_criteria("min_dot_product", self.GYRO_ACCEL_DOT_THRESHOLD)
                cross_check.set_status(TestStatus.PASS if dot >= self.GYRO_ACCEL_DOT_THRESHOLD else TestStatus.FAIL)
                self.results[("gyro_accel_check", gyro_id, accel_id)] = cross_check

            # And every mag whose reading changed enough to judge
            for mag_id in self._ids["mag"]:
                if self._result("mag", mag_id, "field_change").success:
                    self.results[("gyro_mag_check", gyro_id, mag_id)] = self._check_gyro_mag(gyro_id, mag_id, orientations)

    def _gyro_orientations(self, gyro_id: int) -> list[list[float] | None]:
        """The gyro's integrated rotation at each flip sample, since its first value: [x, y, z, w], from the sensor's
        frame at that sample to its frame at the start. None before its first value"""
        orientations = []
        q, last_time = None, None
        for t, gyro in zip(self._flip_samples["time"], self._flip_samples["gyro"][gyro_id]):
            if gyro is not None:
                if q is None:
                    q = [0.0, 0.0, 0.0, 1.0]
                else:
                    angle = vec_len(gyro) * (t - last_time)   # gyro: [wx, wy, wz] in rad/s
                    if angle > 1e-12:
                        q = quat_mul(q, quat_from_axis_angle(vec_normalize(gyro), angle))
                last_time = t
            orientations.append(q)
        return orientations

    def _check_gyro_mag(self, gyro_id: int, mag_id: int, orientations: list) -> TestResult:
        """
        A working mag reads one fixed field, turned by how the sensor turned, plus one fixed offset: at every flip
        sample, reading = R^T field + offset, R the gyro's orientation then. Fits the field and offset by least
        squares, then judges the error left relative to how far the readings moved, so the local field's strength
        doesn't matter. Catches a mag turning the wrong way, Ex: an axis inverted or two swapped, which the start and
        end readings alone can't show
        """
        pairs = [(q, m) for q, m in zip(orientations, self._flip_samples["mag"][mag_id]) if q is not None and m is not None]
        readings = np.array([m for _, m in pairs])
        # Each sample's 3 equations: [R^T | I] @ [field, offset] = reading
        model = np.vstack([np.hstack([np.array(quaternion_to_3x3_rotation_matrix(quat_inverse(q))), np.eye(3)])
                           for q, _ in pairs])
        solution, *_ = np.linalg.lstsq(model, readings.reshape(-1), rcond=None)
        errors = (model @ solution).reshape(-1, 3) - readings
        fit_error = float(np.sqrt(np.mean(np.sum(errors ** 2, axis=1))))   # RMS vector error, gauss
        spread = float(np.sqrt(np.mean(np.sum((readings - readings.mean(axis=0)) ** 2, axis=1))))
        relative_error = fit_error / spread   # field_change passed, so the readings moved

        cross_check = TestResult(self.id, "gyro_mag_check", components=[f"gyro:{gyro_id}", f"mag:{mag_id}"])
        cross_check.add_measurement("fit_error", fit_error)
        cross_check.add_measurement("readings_spread", spread)
        cross_check.add_measurement("relative_fit_error", relative_error)
        cross_check.add_criteria("max_relative_fit_error", self.GYRO_MAG_MAX_FIT_ERROR)
        return cross_check.set_status(TestStatus.PASS if relative_error <= self.GYRO_MAG_MAX_FIT_ERROR else TestStatus.FAIL)

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

    def _start_sampling(self, stream_commands: dict = None):
        """Clears the samples and streams every component, collecting one sample per packet.
        stream_commands: the command streamed per component type, STREAM_COMMANDS (raw) by default"""
        self._stream_commands = stream_commands or self.STREAM_COMMANDS
        self._samples = {"time": []} | {ctype: {cid: [] for cid in ids} for ctype, ids in self._ids.items()}
        self._samples["baro_ema"] = {cid: [] for cid in self._ids["baro"]}
        commands = [(StreamableCommands.GetTimestamp, None)] + \
                   [(self._stream_commands[ctype], cid) for ctype, cid in self._components()]
        self.start_streaming(commands, self._on_streaming_data, hz=self.STREAMING_HZ)

    def _on_streaming_data(self, status: ThreespaceStreamingStatus, user_data: Any = None):
        # Collect one sample per packet so gyro integration captures every update
        if status != ThreespaceStreamingStatus.Data:
            return
        manager = self.streaming_manager
        self._samples["time"].append(manager.get_value(StreamableCommands.GetTimestamp) / 1_000_000)
        for ctype, cid in self._components():
            self._samples[ctype][cid].append(manager.get_value(self._stream_commands[ctype], cid))
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
