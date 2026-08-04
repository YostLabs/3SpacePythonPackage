"""
Gradient Descent Calibration Tool for 3Space sensors.

Provides a programmatic wizard class (ThreespaceCalibrationWizard) and a CLI
entry point that wraps it.

Usage (CLI)::

    yostlabs-calibration COM3
    yostlabs-calibration COM3 --mags 0 --no-apply --output results.json
    python -m yostlabs.tss3.tools.calibration_tool COM3

Usage (programmatic)::

    wizard = ThreespaceCalibrationWizard(sensor)
    wizard.start()
    wizard.print_header()
    while not wizard.is_done() and not wizard.is_cancelled() and not wizard.is_error():
        wizard.print_instructions()
        user_input = input("> ").strip().lower()
        if user_input == "q":
            wizard.cancel()
        elif user_input == "b":
            wizard.back()
        else:
            wizard.next()
            while wizard.is_busy():
                pass
    if wizard.result:
        wizard.apply_result()
        sensor.commitSettings()
"""

import argparse
import json
import math
import sys
import threading
import time
from enum import Enum, auto
from typing import Any

import numpy as np

from yostlabs.tss3.api import ThreespaceSensor, StreamableCommands
from yostlabs.communication.ble import ThreespaceBLEComClass
from yostlabs.tss3.utils.calibration import ThreespaceGradientDescentCalibration
from yostlabs.math import quaternion


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_ODR = 500              # Hz – components below this are raised during calibration
READINGS_PER_SAMPLE = 100  # readings averaged per orientation step
TOTAL_STEPS = 24


# ---------------------------------------------------------------------------
# Orientation generation
# ---------------------------------------------------------------------------

_ORIENTATION_ROOTS = [
    [( 0,  1,  0), ( 0,  0,  1)],
    [( 0,  0, -1), (-1,  0,  0)],
    [( 0,  1,  0), ( 0,  0, -1)],
    [( 1,  0,  0), ( 0, -1,  0)],
    [( 0,  0,  1), ( 0,  1,  0)],
    [( 0, -1,  0), ( 1,  0,  0)],
]


def _build_orientations() -> list[np.ndarray]:
    """Return the 24 calibration quaternions in wizard order."""
    orientations = []
    rotation_sign = -1  # 1 = CCW, -1 = CW
    for root in _ORIENTATION_ROOTS:
        forward_vec = root[1]
        down_vec = [-v for v in root[0]]
        q = np.array(quaternion.quat_from_two_vectors(forward_vec, down_vec), dtype=np.float64)
        orientations.append(q)
        rotation = quaternion.quat_from_axis_angle([0, 0, 1], math.radians(90 * rotation_sign))
        for _ in range(3):
            q = np.array(quaternion.quat_mul(orientations[-1].tolist(), rotation), dtype=np.float64)
            orientations.append(q)
        rotation_sign *= -1  # Reverse rotation direction to prevent cable from twisting too much
    return orientations


# ---------------------------------------------------------------------------
# Human-readable orientation descriptions
# Each entry: (Z Axis, Y Axis) – the X axis is implied by the right-hand rule.
# Group boundaries match _ORIENTATION_ROOTS above.
# ---------------------------------------------------------------------------

_ORIENTATION_LABELS: list[tuple[str, str]] = [
    # Group 1
    ("+Z forward", "+Y up"),
    ("+Z forward", "+Y right"),
    ("+Z forward", "+Y down"),
    ("+Z forward", "+Y left"),
    # Group 2
    ("+Z left", "+Y backward"),
    ("+Z left", "+Y down"),
    ("+Z left", "+Y forward"),
    ("+Z left", "+Y up"),
    # Group 3
    ("+Z backward", "+Y up"),
    ("+Z backward", "+Y left"),
    ("+Z backward", "+Y down"),
    ("+Z backward", "+Y right"),
    # Group 4
    ("+Z down", "+Y right"),
    ("+Z down", "+Y forward"),
    ("+Z down", "+Y left"),
    ("+Z down", "+Y backward"),
    # Group 5
    ("+Z up", "+Y forward"),
    ("+Z up", "+Y left"),
    ("+Z up", "+Y backward"),
    ("+Z up", "+Y right"),
    # Group 6
    ("+Z right", "+Y down"),
    ("+Z right", "+Y backward"),
    ("+Z right", "+Y up"),
    ("+Z right", "+Y forward"),
]

_AXIS_REFERENCE_TEXT = (
    "Axis reference:\n"
    "  'up'        = face pointing away from ground (against gravity)\n"
    "  'down'      = face pointing towards the ground (with gravity)\n"
    "  'forward'   = face pointing opposite your body (away from your eyes)\n"
    "  'backwards' = face pointing in towards your body (towards your eyes)\n"
    "  'left'      = face pointing to your left side\n"
    "  'right'     = face pointing to your right side"
)


# ---------------------------------------------------------------------------
# Internal state enum
# ---------------------------------------------------------------------------

class _WizardState(Enum):
    IDLE        = auto()
    WAITING     = auto()   # At a step, ready for next() to be called
    COLLECTING  = auto()   # Background thread: gathering sensor readings
    CALCULATING = auto()   # Background thread: running gradient descent
    DONE        = auto()   # Calculation complete; result is available
    CANCELLED   = auto()
    ERROR       = auto()


# ---------------------------------------------------------------------------
# Sensor I/O helper
# ---------------------------------------------------------------------------

def _gather_sample(
    sensor: ThreespaceSensor,
    accels: list[int],
    mags: list[int],
) -> tuple[dict[int, np.ndarray], dict[int, np.ndarray]]:
    """Poll READINGS_PER_SAMPLE readings and return per-sensor averages."""
    accel_totals = {i: np.zeros(3, dtype=np.float64) for i in accels}
    mag_totals   = {i: np.zeros(3, dtype=np.float64) for i in mags}

    last_packet_time = time.perf_counter()
    samples_gathered = 0
    sensor.clearStreamingPackets()
    sensor.startStreaming()

    # Check for a timeout in case the sensor is not responding or streaming is not working
    while samples_gathered < READINGS_PER_SAMPLE and time.perf_counter() - last_packet_time < 1.0:
        sensor.updateStreaming()
        packet = sensor.getOldestStreamingPacket()
        while packet is not None:
            i = 0
            for sid in accels:
                accel_totals[sid] += np.array(packet.data[i], dtype=np.float64)
                i += 1
            for sid in mags:
                mag_totals[sid] += np.array(packet.data[i], dtype=np.float64)
                i += 1

            last_packet_time = time.perf_counter() 
            samples_gathered += 1
            if samples_gathered >= READINGS_PER_SAMPLE:
                break

            packet = sensor.getOldestStreamingPacket()
    sensor.stopStreaming()

    accel_avg = {i: accel_totals[i] / READINGS_PER_SAMPLE for i in accels}
    mag_avg   = {i: mag_totals[i]   / READINGS_PER_SAMPLE for i in mags}
    return accel_avg, mag_avg


# ---------------------------------------------------------------------------
# ThreespaceCalibrationWizard
# ---------------------------------------------------------------------------

class ThreespaceCalibrationWizard:
    """Programmatic gradient descent calibration wizard for 3Space sensors.

    The wizard walks through 24 orientations, collects raw accel/mag data,
    runs gradient descent, and produces a calibration result that can then
    be applied to the sensor.

    Typical usage::

        wizard = ThreespaceCalibrationWizard(sensor)
        wizard.start()
        wizard.print_header()
        while not wizard.is_done() and not wizard.is_cancelled() and not wizard.is_error():
            wizard.print_instructions()
            user_input = input("> ").strip().lower()
            if user_input == "q":
                wizard.cancel()
            elif user_input == "b":
                wizard.back()
            else:
                wizard.next()
                while wizard.is_busy():
                    pass
        if wizard.result:
            wizard.apply_result()
            sensor.commitSettings()
    """

    def __init__(
        self,
        sensor: ThreespaceSensor,
        accels: list[int] | None = None,
        mags: list[int] | None = None,
        verbose: bool = False,
    ) -> None:
        """
        Parameters
        ----------
        sensor:
            Connected ThreespaceSensor instance.
        accels:
            Accelerometer component IDs to calibrate.  ``None`` calibrates
            all valid accelerometers reported by the sensor.
        mags:
            Magnetometer component IDs to calibrate.  ``None`` calibrates
            all valid magnetometers reported by the sensor.
        verbose:
            Pass ``True`` to print gradient-descent progress during
            calculation.
        """
        self._sensor           = sensor
        self._requested_accels = accels
        self._requested_mags   = mags
        self._verbose          = verbose

        # Populated in start()
        self._selected_accels: list[int]      = []
        self._selected_mags:   list[int]      = []
        self._orientations:    list[np.ndarray] = []

        # Cached sensor settings restored after collection / on cancel
        self._cached_axis_order:       str | None       = None
        self._cached_axis_offset:      int | None       = None
        self._cached_accel_odrs:       dict[int, int]   = {}
        self._cached_mag_odrs:         dict[int, int]   = {}
        self._cached_streaming_config: dict[str, Any]   = {}
        self._sensor_configured:       bool             = False

        # Collected samples keyed by component ID
        self._accel_samples: dict[int, list[np.ndarray]] = {}
        self._mag_samples:   dict[int, list[np.ndarray]] = {}

        # Internal state
        self._state:  _WizardState       = _WizardState.IDLE
        self._step:   int                = 0
        self._thread: threading.Thread | None = None
        self._error:  str | None         = None

        # Public result dict; None until the wizard reaches DONE state.
        # Keys: "accels" and "mags", each mapping component-ID strings to
        # {"matrix": [...], "bias": [...]} dicts.
        self.result: dict | None = None

    # ------------------------------------------------------------------
    # Read-only properties
    # ------------------------------------------------------------------

    @property
    def step(self) -> int:
        """Current 0-based step index (0 – TOTAL_STEPS-1)."""
        return self._step

    @property
    def total_steps(self) -> int:
        """Total number of orientation steps (always 24)."""
        return TOTAL_STEPS

    @property
    def selected_accels(self) -> list[int]:
        """Accelerometer IDs selected for calibration (populated after start())."""
        return list(self._selected_accels)

    @property
    def selected_mags(self) -> list[int]:
        """Magnetometer IDs selected for calibration (populated after start())."""
        return list(self._selected_mags)

    @property
    def error(self) -> str | None:
        """Error message when the wizard is in the ERROR state, else None."""
        return self._error

    # ------------------------------------------------------------------
    # Status predicates
    # ------------------------------------------------------------------

    def is_busy(self) -> bool:
        """Return True while sample collection or gradient descent is running."""
        return self._thread is not None and self._thread.is_alive()

    def is_done(self) -> bool:
        """Return True once calibration is complete and *result* is available."""
        return self._state == _WizardState.DONE

    def is_cancelled(self) -> bool:
        """Return True if the wizard was cancelled."""
        return self._state == _WizardState.CANCELLED

    def is_error(self) -> bool:
        """Return True if the wizard encountered an unrecoverable error."""
        return self._state == _WizardState.ERROR

    # ------------------------------------------------------------------
    # Wizard controls
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Configure the sensor and begin the wizard at step 0.

        Raises
        ------
        RuntimeError
            If the wizard was already started, or no valid sensors are
            available for the requested component IDs.
        Exception
            Re-raises sensor communication errors encountered during
            initial configuration.
        """
        if self._state != _WizardState.IDLE:
            raise RuntimeError("Wizard already started. Create a new instance to restart.")

        sensor    = self._sensor
        all_accels: list[int] = sensor.valid_accels
        all_mags:   list[int] = sensor.valid_mags

        if self._requested_accels is not None:
            self._selected_accels = [a for a in self._requested_accels if a in all_accels]
        else:
            self._selected_accels = list(all_accels)

        if self._requested_mags is not None:
            self._selected_mags = [m for m in self._requested_mags if m in all_mags]
        else:
            self._selected_mags = list(all_mags)

        if not self._selected_accels and not self._selected_mags:
            raise RuntimeError("No valid accelerometers or magnetometers to calibrate.")

        # Cache current sensor state so it can be restored later
        self._cached_axis_order  = sensor.readAxisOrder()
        self._cached_axis_offset = sensor.readAxisOffsetEnabled()
        for sid in self._selected_accels:
            self._cached_accel_odrs[sid] = sensor.readOdrAccel(sid)
        for sid in self._selected_mags:
            self._cached_mag_odrs[sid] = sensor.readOdrMag(sid)
        self._cached_streaming_config = sensor.read_settings(
            "stream_slots", "stream_mode", "stream_hz", "stream_count"
        )
        self._sensor_configured = True

        # Raise ODR on any slow components
        for sid, odr in self._cached_accel_odrs.items():
            if odr < MIN_ODR:
                sensor.writeOdrAccel(sid, MIN_ODR)
        for sid, odr in self._cached_mag_odrs.items():
            if odr < MIN_ODR:
                sensor.writeOdrMag(sid, MIN_ODR)

        # Calibration math requires XYZ axis order with no offset applied
        sensor.writeAxisOrder("xyz")
        sensor.writeAxisOffsetEnabled(0)

        # Configure streaming – use count-based mode to avoid BLE saturation issues
        stream_slots = []
        for sid in self._selected_accels:
            stream_slots.append(f"{StreamableCommands.GetRawAccelVec.value}:{sid}")
        for sid in self._selected_mags:
            stream_slots.append(f"{StreamableCommands.GetRawMagVec.value}:{sid}")
        sensor.write_settings(
            stream_slots=','.join(stream_slots),
            stream_hz=MIN_ODR,
            stream_mode=1,           # count-based
            stream_count=READINGS_PER_SAMPLE,
        )

        self._accel_samples = {a: [] for a in self._selected_accels}
        self._mag_samples   = {m: [] for m in self._selected_mags}
        self._orientations  = _build_orientations()
        self._step  = 0
        self._state = _WizardState.WAITING

    def getRequiredOrientation(self) -> np.ndarray | None:
        """Return the target quaternion the sensor should be held in for the
        current step, or ``None`` if not in a WAITING state.

        The returned array is a copy (modifying it does not affect the wizard).
        """
        if self._state == _WizardState.WAITING:
            return self._orientations[self._step].copy()
        return None

    def print_header(self) -> None:
        """Print the one-time introduction text and axis-reference key."""
        print()
        print("=" * 62)
        print(f"  GRADIENT DESCENT CALIBRATION  —  {TOTAL_STEPS} Orientations")
        print("=" * 62)
        print("For each step, place the sensor in the described orientation,")
        print("hold it still, then call next() (or press Enter in the CLI).")
        print()
        print(_AXIS_REFERENCE_TEXT)
        print("=" * 62)

    def print_instructions(self) -> None:
        """Print human-readable guidance for the current wizard state."""
        if self._state == _WizardState.IDLE:
            print("Wizard not started. Call start() first.")
        elif self._state == _WizardState.WAITING:
            face_up, face_toward = _ORIENTATION_LABELS[self._step]
            print(f"\nStep {self._step + 1:2d} / {TOTAL_STEPS}:  {face_up},  {face_toward}")
        elif self._state == _WizardState.COLLECTING:
            print(f"  Collecting {READINGS_PER_SAMPLE} readings for step {self._step + 1}...")
        elif self._state == _WizardState.CALCULATING:
            print("  Calculating calibration parameters...")
        elif self._state == _WizardState.DONE:
            print("Calibration complete.")
        elif self._state == _WizardState.CANCELLED:
            print("Calibration cancelled.")
        elif self._state == _WizardState.ERROR:
            print(f"Calibration error: {self._error}")

    def next(self) -> bool:
        """Capture a sample for the current step and advance.

        Launches a background worker that collects sensor data.  On the
        final step (24) the same worker also runs gradient descent so no
        additional call is needed.  Poll :meth:`is_busy` to wait for
        completion.

        Returns ``True`` if the worker was launched, ``False`` when the
        wizard is not in a WAITING state or is already busy.
        """
        if self._state != _WizardState.WAITING or self.is_busy():
            return False

        self._state  = _WizardState.COLLECTING
        self._thread = threading.Thread(target=self._step_worker, daemon=True)
        self._thread.start()
        return True

    def back(self) -> bool:
        """Discard the last captured sample and return to that step.

        Returns ``True`` if the step was decremented, ``False`` if at step 0,
        busy, or not in a WAITING state.
        """
        if self._state != _WizardState.WAITING or self.is_busy() or self._step == 0:
            return False

        self._step -= 1
        for samples in self._accel_samples.values():
            if samples:
                samples.pop()
        for samples in self._mag_samples.values():
            if samples:
                samples.pop()
        return True

    def cancel(self) -> None:
        """Cancel the wizard and restore the sensor to its previous settings."""
        self._state = _WizardState.CANCELLED
        self._restore_sensor()

    def apply_result(self) -> bool:
        """Write the calibration result to the sensor (does not commit).

        Call ``sensor.commitSettings()`` afterwards to persist across
        power cycles.

        Returns ``True`` on success, ``False`` if no result is available or
        a sensor error occurs (check :attr:`error` for details).
        """
        if self.result is None:
            return False
        try:
            current_order = self._sensor.readAxisOrder()
            self._sensor.writeAxisOrder("xyz")

            for sid_str, calib in self.result["mags"].items():
                sid = int(sid_str)
                self._sensor.writeCalibMatMag(sid, calib["matrix"])
                self._sensor.writeCalibBiasMag(sid, calib["bias"])

            for sid_str, calib in self.result["accels"].items():
                sid = int(sid_str)
                self._sensor.writeCalibMatAccel(sid, calib["matrix"])
                self._sensor.writeCalibBiasAccel(sid, calib["bias"])

            self._sensor.writeAxisOrder(current_order)
            return True
        except Exception as exc:
            self._error = str(exc)
            return False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _step_worker(self) -> None:
        """Background worker: collect one sample; on the last step also
        restores the sensor and runs gradient descent."""
        try:
            accel_avg, mag_avg = _gather_sample(
                self._sensor, self._selected_accels, self._selected_mags
            )
            for sid in self._selected_accels:
                self._accel_samples[sid].append(accel_avg[sid])
            for sid in self._selected_mags:
                self._mag_samples[sid].append(mag_avg[sid])

            self._step += 1

            if self._step >= TOTAL_STEPS:
                self._state = _WizardState.CALCULATING
                self._restore_sensor()
                self._run_gradient_descent()
            else:
                self._state = _WizardState.WAITING

        except Exception as exc:
            self._error = str(exc)
            self._state = _WizardState.ERROR

    def _run_gradient_descent(self) -> None:
        """Run gradient descent for every selected sensor component.
        Called from inside the worker thread after the last sample."""
        try:
            gradient    = ThreespaceGradientDescentCalibration(self._orientations)
            calc_result: dict[str, dict] = {"accels": {}, "mags": {}}

            for sid in self._selected_mags:
                samples    = self._mag_samples[sid]
                bias_guess = sum(samples) / len(samples)
                centered   = [s - bias_guess for s in samples]
                params     = gradient.calculate(centered, centered[0], verbose=self._verbose).tolist()
                p          = np.array(params)
                p[9:] += -bias_guess  # Gradient descent uses opposite sign convention
                calc_result["mags"][str(sid)] = {
                    "matrix": p[:9].tolist(),
                    "bias":   p[9:].tolist(),
                }

            for sid in self._selected_accels:
                params = gradient.calculate(
                    self._accel_samples[sid],
                    np.array([0.0, 1.0, 0.0]),
                    verbose=self._verbose,
                ).tolist()
                p = np.array(params)
                calc_result["accels"][str(sid)] = {
                    "matrix": p[:9].tolist(),
                    "bias":   p[9:].tolist(),
                }

            self.result = calc_result
            self._state = _WizardState.DONE

        except Exception as exc:
            self._error = str(exc)
            self._state = _WizardState.ERROR

    def _restore_sensor(self) -> None:
        """Restore cached sensor settings.  Safe to call more than once."""
        if not self._sensor_configured:
            return
        self._sensor_configured = False
        try:
            if self._cached_axis_order is not None:
                self._sensor.writeAxisOrder(self._cached_axis_order)
            if self._cached_axis_offset is not None:
                self._sensor.writeAxisOffsetEnabled(self._cached_axis_offset)
            for sid, odr in self._cached_accel_odrs.items():
                if odr < MIN_ODR:
                    self._sensor.writeOdrAccel(sid, odr)
            for sid, odr in self._cached_mag_odrs.items():
                if odr < MIN_ODR:
                    self._sensor.writeOdrMag(sid, odr)
            if self._cached_streaming_config:
                self._sensor.write_settings(**self._cached_streaming_config)
        except Exception as exc:
            print(f"Warning: failed to restore sensor settings: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI runner (uses ThreespaceCalibrationWizard internally)
# ---------------------------------------------------------------------------

def _run_wizard(args: argparse.Namespace) -> int:
    print(f"Connecting to sensor on {args.port}...")
    try:
        if args.port is None:
            sensor = ThreespaceSensor()
        elif args.port.lower().startswith("ble-"):
            sensor = ThreespaceSensor(ThreespaceBLEComClass(args.port[4:]))
        else:
            sensor = ThreespaceSensor(args.port)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    accels = [int(x) for x in args.accels.split(",")] if args.accels is not None else None
    mags   = [int(x) for x in args.mags.split(",")]   if args.mags   is not None else None

    wizard = ThreespaceCalibrationWizard(sensor, accels=accels, mags=mags, verbose=args.verbose)

    try:
        wizard.start()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"  Accelerometers: {wizard.selected_accels or 'none'}")
    print(f"  Magnetometers:  {wizard.selected_mags or 'none'}")

    wizard.print_header()
    print("  Enter      →  capture sample and advance")
    print("  b + Enter  →  redo the previous step")
    print("  q + Enter  →  cancel and quit")
    print("=" * 62)

    while not wizard.is_done() and not wizard.is_cancelled() and not wizard.is_error():
        wizard.print_instructions()
        raw = input("  > ").strip().lower()

        if raw == "q":
            wizard.cancel()
            break

        if raw == "b":
            if not wizard.back():
                print("  Already at step 1.")
            else:
                print(f"  Moved back to step {wizard.step + 1}.")
            continue

        is_last_step = wizard.step == TOTAL_STEPS - 1
        if not wizard.next():
            continue

        print(f"  Collecting {READINGS_PER_SAMPLE} readings...", end="", flush=True)
        while wizard.is_busy():
            time.sleep(0.1)

        if wizard.is_error():
            print(f"\nError: {wizard.error}", file=sys.stderr)
            return 1

        if is_last_step and wizard.is_done():
            print("  Calculating calibration parameters... done.")

    if wizard.is_cancelled():
        print("Calibration cancelled.")
        return 1

    if wizard.is_error():
        print(f"\nError: {wizard.error}", file=sys.stderr)
        return 1

    # --- Print / optionally save ---
    results_json = json.dumps(wizard.result, indent=2)
    print("\nCalibration results:")
    print(results_json)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(results_json)
        print(f"\nResults saved to {args.output}")

    # --- Optionally apply ---
    if not args.no_apply:
        print("\nApplying calibration to sensor...")
        if not wizard.apply_result():
            print(f"Error: failed to apply calibration: {wizard.error}", file=sys.stderr)
            return 1
        print("Calibration applied.")
        print("Call sensor.commitSettings() to persist across power cycles.")

        response = ""
        while response.lower() not in ("y", "n"):
            response = input("Would you like to commit the settings now? (y/n) > ")
            if response.lower() not in ("y", "n"):
                print("Please enter 'y' or 'n'.")
                continue
            if response.lower() == "y":
                try:
                    sensor.commitSettings()
                except Exception as e:
                    print(f"Error: failed to commit settings: {e}", file=sys.stderr)
                    return 1
    else:
        print("\nCalibration NOT applied (--no-apply). Use --output to save results.")

    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yostlabs-calibration",
        description="Interactive gradient descent calibration for 3Space sensors.",
    )
    parser.add_argument(
        "--port", default=None,
        help="Serial port (e.g. COM3, /dev/ttyUSB0) or BLE name (e.g. BLE-MyName). "
             "If omitted, auto-detects a USB device.",
    )
    parser.add_argument(
        "--accels", default=None, metavar="IDS",
        help="Comma-separated accelerometer IDs to calibrate (default: all available)",
    )
    parser.add_argument(
        "--mags", default=None, metavar="IDS",
        help="Comma-separated magnetometer IDs to calibrate (default: all available)",
    )
    parser.add_argument(
        "--no-apply", action="store_true",
        help="Compute calibration but do not write it back to the sensor",
    )
    parser.add_argument(
        "--output", "-o", default=None, metavar="FILE",
        help="Save calibration results to this JSON file",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print gradient descent progress during calculation",
    )
    return parser


def main() -> None:
    sys.exit(_run_wizard(_build_parser().parse_args()))

if __name__ == "__main__":
    main()

