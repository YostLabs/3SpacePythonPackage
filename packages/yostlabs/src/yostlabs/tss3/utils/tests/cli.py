"""
Runs a SensorTest in the terminal. Each Request is shown as a prompt, the same requests a GUI shows as pages.
"""
import sys
import time
import queue
import threading

from yostlabs.tss3 import ThreespaceSensor
from yostlabs.tss3.utils.tests.base import SensorTest, TestResult, Step, Request, Message, Confirm, Choice, Busy

import logging


class _LineReader:
    """
    Reads terminal lines on a background thread, so a Busy request's actions can be picked while the test keeps running.
    All input goes through it: a second input() call would race the thread for the same lines.
    """

    def __init__(self):
        self._lines: queue.Queue[str | None] = queue.Queue()   # None: input has ended
        self._thread: threading.Thread = None

    def _read_forever(self):
        try:
            while True:
                self._lines.put(input())
        except EOFError:
            self._lines.put(None)

    def _start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._read_forever, daemon=True)
            self._thread.start()

    def _get(self, timeout: float | None) -> str | None:
        """A line, or None if none arrived within timeout (0: don't wait). Raises EOFError once input has ended."""
        self._start()
        try:
            line = self._lines.get(timeout=timeout) if timeout else self._lines.get_nowait()
        except queue.Empty:
            return None
        if line is None:
            self._lines.put(None)   # Keep reporting the end to later reads
            raise EOFError("Terminal input has ended")
        return line

    def read(self, prompt: str) -> str:
        """Waits for a line. Ctrl+C still interrupts it."""
        print(prompt, end="", flush=True)
        while (line := self._get(timeout=0.1)) is None:
            pass
        return line

    def poll(self) -> str | None:
        """A line if one was entered, without waiting"""
        return self._get(timeout=0)

    def clear(self):
        """Drops lines entered before the current prompt was shown"""
        if self._thread is None:
            return
        while self.poll() is not None:
            pass

_reader = _LineReader()


def run_cli(test: SensorTest, poll_interval: float = 0.01) -> SensorTest:
    """Runs the test to the end, asking the operator in the terminal. Ctrl+C cancels it."""
    shown = None            # The Busy request whose text has been printed
    status_shown = False    # A status line is on screen and needs ending before printing anything else

    def end_status():
        nonlocal status_shown
        if status_shown:
            print()
            status_shown = False

    def announce_step(step: Step):
        nonlocal shown
        end_status()
        shown = None    # Show the new step's first request even if its text matches the last one
        print(f"\n{test.name} - step {test.steps.index(step) + 1}/{len(test.steps)}: {step.title}", flush=True)

    test.on_step_started = announce_step
    try:
        test.start()
        while not test.finished:
            request = test.request
            if not isinstance(request, Busy):
                end_status()
                shown = None
                _reader.clear()
                test.respond(_ask(request))
            else:
                if shown is None or (request.text, request.actions) != (shown.text, shown.actions):
                    end_status()
                    print(request.text + _actions_hint(request.actions))
                    _reader.clear()
                shown = request
                if request.status:
                    print(f"  {request.status}".ljust(60), end="\r", flush=True)
                    status_shown = True
                action = _pick_action(request.actions, _reader.poll()) if request.actions else None
                if action is not None:
                    test.respond(action)
                else:
                    time.sleep(poll_interval)
                    test.update()
    except (KeyboardInterrupt, EOFError) as e:
        test.cancel()
        print(f"\nTest cancelled{': ' + str(e) if str(e) else ''}.")
    return test


def _actions_hint(actions: tuple[str, ...]) -> str:
    if not actions:
        return ""
    if len(actions) == 1:
        return f" (Enter: {actions[0]})"
    return " (" + ", ".join(f"{number}: {action}" for number, action in enumerate(actions, 1)) + ", then Enter)"


def _pick_action(actions: tuple[str, ...], line: str | None) -> str | None:
    if line is None:
        return None
    if len(actions) == 1:
        return actions[0]
    line = line.strip()
    if line.isdigit() and 1 <= int(line) <= len(actions):
        return actions[int(line) - 1]
    return None


def _ask(request: Request):
    match request:
        case Confirm():
            while (answer := _reader.read(f"{request.text} (Y/n) ").strip().lower()) not in ("", "y", "n"):
                pass
            return answer != "n"
        case Choice():
            for number, option in enumerate(request.options, 1):
                print(f"  {number}. {option}")
            while True:
                answer = _reader.read(f"{request.text} ").strip()
                if answer.isdigit() and 1 <= int(answer) <= len(request.options):
                    return request.options[int(answer) - 1]
        case Message():
            _reader.read(f"{request.text} (Enter to continue) ")
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
