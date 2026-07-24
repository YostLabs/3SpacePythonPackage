"""
Gradient Descent Calibration Tool for 3Space sensors.

Replicates the Gradient Descent Calibration in the suite as a CLI tool.
Walks the user through 24 orientations, collects raw accel/mag data from
a connected sensor, runs gradient descent, and optionally applies the result.

Usage:
    yostlabs-calibration COM3
    yostlabs-calibration COM3 --mags 0 --no-apply --output results.json
    python -m yostlabs.tss3.tools.calibration_tool COM3
"""

import argparse
import json
import math
import sys
import threading

import numpy as np

from yostlabs.tss3.api import ThreespaceSensor
from yostlabs.communication.ble import ThreespaceBLEComClass
from yostlabs.tss3.utils.calibration import ThreespaceGradientDescentCalibration
from yostlabs.math import quaternion


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_ODR = 500              # Hz – components below this are raised during calibration
READINGS_PER_SAMPLE = 100  # readings averaged per orientation step


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
    rotation_sign = -1 #1 = CCW, -1 = CW
    for root in _ORIENTATION_ROOTS:
        forward_vec = root[1]
        down_vec = [-v for v in root[0]]
        q = np.array(quaternion.quat_from_two_vectors(forward_vec, down_vec), dtype=np.float64)
        orientations.append(q)
        rotation = quaternion.quat_from_axis_angle([0, 0, 1], math.radians(90 * rotation_sign))
        for _ in range(3):
            q = np.array(quaternion.quat_mul(orientations[-1].tolist(), rotation), dtype=np.float64)
            orientations.append(q)
        rotation_sign *= -1 #Reverse rotation direction to prevent cable from twisting too much

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


# ---------------------------------------------------------------------------
# Sensor I/O helpers
# ---------------------------------------------------------------------------

def _gather_sample(
    sensor: ThreespaceSensor,
    accels: list[int],
    mags: list[int],
) -> tuple[dict[int, np.ndarray], dict[int, np.ndarray]]:
    """Poll READINGS_PER_SAMPLE readings and return per-sensor averages."""
    accel_totals = {i: np.zeros(3, dtype=np.float64) for i in accels}
    mag_totals   = {i: np.zeros(3, dtype=np.float64) for i in mags}

    for _ in range(READINGS_PER_SAMPLE):
        for sid in accels:
            accel_totals[sid] += np.array(sensor.getRawAccelVec(sid).data, dtype=np.float64)
        for sid in mags:
            mag_totals[sid] += np.array(sensor.getRawMagVec(sid).data, dtype=np.float64)

    accel_avg = {i: accel_totals[i] / READINGS_PER_SAMPLE for i in accels}
    mag_avg   = {i: mag_totals[i]   / READINGS_PER_SAMPLE for i in mags}
    return accel_avg, mag_avg


def _run_gradient_thread(
    gradient: ThreespaceGradientDescentCalibration,
    samples: list[np.ndarray],
    origin: np.ndarray,
    result_out: list,
    **kwargs,
) -> None:
    result_out.extend(gradient.calculate(samples, origin, **kwargs).tolist())


# ---------------------------------------------------------------------------
# Wizard
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

    # Determine which components to calibrate
    all_accels: list[int] = sensor.valid_accels
    all_mags:   list[int] = sensor.valid_mags

    if args.accels is not None:
        selected_accels = [a for a in (int(x) for x in args.accels.split(",")) if a in all_accels]
    else:
        selected_accels = list(all_accels)

    if args.mags is not None:
        selected_mags = [m for m in (int(x) for x in args.mags.split(",")) if m in all_mags]
    else:
        selected_mags = list(all_mags)

    if not selected_accels and not selected_mags:
        print("Error: no valid accelerometers or magnetometers to calibrate.", file=sys.stderr)
        return 1

    print(f"  Accelerometers: {selected_accels or 'none'}")
    print(f"  Magnetometers:  {selected_mags or 'none'}")

    # --- Cache current sensor state ---
    cached_axis_order:  str | None = None
    cached_axis_offset: int | None = None
    cached_accel_odrs: dict[int, int] = {}
    cached_mag_odrs:   dict[int, int] = {}

    def restore_sensor() -> None:
        try:
            if cached_axis_order is not None:
                sensor.writeAxisOrder(cached_axis_order)
            if cached_axis_offset is not None:
                sensor.writeAxisOffsetEnabled(cached_axis_offset)
            for sid, odr in cached_accel_odrs.items():
                if odr < MIN_ODR:
                    sensor.writeOdrAccel(sid, odr)
            for sid, odr in cached_mag_odrs.items():
                if odr < MIN_ODR:
                    sensor.writeOdrMag(sid, odr)
        except Exception as exc:
            print(f"Warning: failed to restore sensor settings: {exc}", file=sys.stderr)

    try:
        cached_axis_order  = sensor.readAxisOrder()
        cached_axis_offset = sensor.readAxisOffsetEnabled()
        for sid in selected_accels:
            cached_accel_odrs[sid] = sensor.readOdrAccel(sid)
        for sid in selected_mags:
            cached_mag_odrs[sid] = sensor.readOdrMag(sid)

        # Raise ODR if below minimum
        for sid, odr in cached_accel_odrs.items():
            if odr < MIN_ODR:
                sensor.writeOdrAccel(sid, MIN_ODR)
        for sid, odr in cached_mag_odrs.items():
            if odr < MIN_ODR:
                sensor.writeOdrMag(sid, MIN_ODR)

        # Calibration math requires XYZ axis order with no offset applied
        sensor.writeAxisOrder("xyz")
        sensor.writeAxisOffsetEnabled(0)
    except Exception as e:
        print(f"Error: failed to configure sensor: {e}", file=sys.stderr)
        restore_sensor()
        return 1

    # --- Print instructions ---
    print()
    print("=" * 62)
    print("  GRADIENT DESCENT CALIBRATION  —  24 Orientations")
    print("=" * 62)
    print("For each step, place the sensor in the described orientation,")
    print("hold it still, then press Enter to capture the sample.")
    print()
    print("  Enter      →  capture sample and advance")
    print("  b + Enter  →  redo the previous step")
    print("  q + Enter  →  cancel and quit")
    print()
    print("Axis reference: check the labels printed on your sensor PCB.")
    print("  'up'           = face pointing away from ground (against gravity)")
    print("  'down'         = face pointing towards the ground (with gravity)")
    print("  'forward'      = face pointing opposite your body (away from your eyes)")
    print("  'backwards'    = face pointing in towards your body (towards your eyes)")
    print("  'left'         = face pointing to your left side")
    print("  'right'        = face pointing to your right side")
    print("=" * 62)

    orientations = _build_orientations()
    accel_samples: dict[int, list[np.ndarray]] = {a: [] for a in selected_accels}
    mag_samples:   dict[int, list[np.ndarray]] = {m: [] for m in selected_mags}

    step = 0
    while step < 24:
        face_up, face_toward = _ORIENTATION_LABELS[step]
        print(f"\nStep {step + 1:2d} / 24:  {face_up},  {face_toward}")
        raw = input("  > ").strip().lower()

        if raw == "q":
            print("Calibration cancelled.")
            restore_sensor()
            return 1

        if raw == "b":
            if step == 0:
                print("  Already at step 1.")
                continue
            step -= 1
            for samples in accel_samples.values():
                if samples:
                    samples.pop()
            for samples in mag_samples.values():
                if samples:
                    samples.pop()
            print(f"  Moved back to step {step + 1}.")
            continue

        print(f"  Collecting {READINGS_PER_SAMPLE} readings...", end="", flush=True)
        try:
            accel_avg, mag_avg = _gather_sample(sensor, selected_accels, selected_mags)
        except Exception as e:
            print(f"\n  Error reading sensor: {e}", file=sys.stderr)
            restore_sensor()
            return 1

        for sid in selected_accels:
            accel_samples[sid].append(accel_avg[sid])
        for sid in selected_mags:
            mag_samples[sid].append(mag_avg[sid])

        print(" done.")
        step += 1

    # Restore sensor state before the (potentially long) calculation
    restore_sensor()

    # --- Gradient descent ---
    print("\nCalculating calibration parameters...")
    gradient = ThreespaceGradientDescentCalibration(orientations)
    result: dict[str, dict] = {"accels": {}, "mags": {}}

    for sid in selected_mags:
        print(f"  mag{sid}...", end="", flush=True)
        samples = mag_samples[sid]
        bias_guess = sum(samples) / len(samples)
        centered   = [s - bias_guess for s in samples]

        params: list[float] = []
        t = threading.Thread(
            target=_run_gradient_thread,
            args=(gradient, centered, centered[0], params),
            kwargs={"verbose": args.verbose},
            daemon=True,
        )
        t.start()
        t.join()

        p = np.array(params)
        p[9:] += -bias_guess  #Gradient descent and this use opposite signs, so swap it
        result["mags"][str(sid)] = {"matrix": p[:9].tolist(), "bias": p[9:].tolist()}
        print(" done.")

    for sid in selected_accels:
        print(f"  accel{sid}...", end="", flush=True)
        params: list[float] = []
        t = threading.Thread(
            target=_run_gradient_thread,
            args=(gradient, accel_samples[sid], np.array([0.0, 1.0, 0.0]), params),
            kwargs={"verbose": args.verbose},
            daemon=True,
        )
        t.start()
        t.join()

        p = np.array(params)
        result["accels"][str(sid)] = {"matrix": p[:9].tolist(), "bias": p[9:].tolist()}
        print(" done.")

    # --- Save ---
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        print(f"\nResults saved to {args.output}")

    # --- Apply ---
    if not args.no_apply:
        print("\nApplying calibration to sensor...")
        try:
            current_order = sensor.readAxisOrder()
            sensor.writeAxisOrder("xyz")

            for sid_str, calib in result["mags"].items():
                sid = int(sid_str)
                sensor.writeCalibMatMag(sid, calib["matrix"])
                sensor.writeCalibBiasMag(sid, calib["bias"])
                print(f"  mag{sid} applied.")

            for sid_str, calib in result["accels"].items():
                sid = int(sid_str)
                sensor.writeCalibMatAccel(sid, calib["matrix"])
                sensor.writeCalibBiasAccel(sid, calib["bias"])
                print(f"  accel{sid} applied.")

            sensor.writeAxisOrder(current_order)
        except Exception as e:
            print(f"Error: failed to apply calibration: {e}", file=sys.stderr)
            return 1

        print("Calibration applied. Use 'commit settings' to persist across power cycles.")
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
        help="Serial port of the sensor (e.g. COM3, /dev/ttyUSB0) or BLE Name (e.g. BLE-MyName). If not specified, will auto detect a USB device.",
    )
    parser.add_argument(
        "--accels", default=None,
        metavar="IDS",
        help="Comma-separated accelerometer IDs to calibrate (default: all available)",
    )
    parser.add_argument(
        "--mags", default=None,
        metavar="IDS",
        help="Comma-separated magnetometer IDs to calibrate (default: all available)",
    )
    parser.add_argument(
        "--no-apply", action="store_true",
        help="Compute calibration but do not write it back to the sensor",
    )
    parser.add_argument(
        "--output", "-o", default=None,
        metavar="FILE",
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
