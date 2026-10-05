"""
Runs a SensorTest in the terminal. Each Request is shown as a prompt, the same requests a GUI shows as pages.
"""
import sys
import time

from yostlabs.tss3 import ThreespaceSensor
from yostlabs.tss3.utils.tests.base import SensorTest, TestResult, StepState, Request, Message, Confirm, Choice, Busy

import logging


def run_cli(test: SensorTest, poll_interval: float = 0.01) -> SensorTest:
    """Runs the test to the end, asking the operator in the terminal. Ctrl+C cancels it."""
    announced = set()       # Steps whose title has been printed
    shown_text = None       # Text of the Busy request already printed
    status_shown = False    # A status line is on screen and needs ending before printing anything else

    def end_status():
        nonlocal status_shown
        if status_shown:
            print()
            status_shown = False

    def announce_steps():
        for number, step in enumerate(test.steps, 1):
            if step in announced or test.step_states[step] in (StepState.PENDING, StepState.SKIPPED):
                continue
            announced.add(step)
            end_status()
            print(f"\n{test.name} - step {number}/{len(test.steps)}: {step.title}")

    try:
        test.start()
        announce_steps()
        while not test.finished:
            request = test.request
            if not isinstance(request, Busy):
                end_status()
                shown_text = None
                test.respond(_ask(request))
            else:
                if request.text != shown_text:
                    end_status()
                    print(request.text)
                    shown_text = request.text
                if request.status:
                    print(f"  {request.status}".ljust(60), end="\r", flush=True)
                    status_shown = True
                time.sleep(poll_interval)
                test.update()
            announce_steps()
    except KeyboardInterrupt:
        test.cancel()
        print("\nTest cancelled.")
    return test


def _ask(request: Request):
    match request:
        case Confirm():
            while (answer := input(f"{request.text} (Y/n) ").strip().lower()) not in ("", "y", "n"):
                pass
            return answer != "n"
        case Choice():
            for number, option in enumerate(request.options, 1):
                print(f"  {number}. {option}")
            while True:
                answer = input(f"{request.text} ").strip()
                if answer.isdigit() and 1 <= int(answer) <= len(request.options):
                    return request.options[int(answer) - 1]
        case Message():
            input(f"{request.text} (Enter to continue) ")
            return None
        case _:
            raise TypeError(f"No terminal prompt for {type(request).__name__}")


def print_results(results: list[TestResult], show_only_failures: bool = False):
    for result in results:
        if show_only_failures and result.success:
            continue
        components = f" [{', '.join(result.components)}]" if result.components else ""
        message = f" - {result.message}" if result.message else ""
        print(f"{result.test}.{result.check}{components}: {result.status}{message}")
        for name, value in result.measurements.items():
            print(f"    {name}: {value}")
        for name, value in result.criteria.items():
            print(f"    (criteria) {name}: {value}")


def main(test_type: type[SensorTest]) -> SensorTest:
    """Runs one test on the first sensor found. For a test module's __main__."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])

    sensor = ThreespaceSensor()
    try:
        test = run_cli(test_type(sensor))
    finally:
        sensor.cleanup()
    print()
    print_results(test.result_flat)
    print("Overall success:", test.overall_success)
    return test
