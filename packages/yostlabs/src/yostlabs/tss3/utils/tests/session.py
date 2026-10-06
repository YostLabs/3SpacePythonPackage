import datetime
from typing import Any, Callable

from yostlabs.tss3 import ThreespaceSensor
from yostlabs.tss3.utils.streaming import ThreespaceStreamingManager
from yostlabs.tss3.utils.tests.base import SensorTest, TestResult, Request, Step

import logging
logger = logging.getLogger(__name__)

# Creates a test: (sensor, streaming_manager=...) -> SensorTest.
# A test class, or Ex: functools.partial(ComponentTest, expected_components=[...])
TestFactory = Callable[..., SensorTest]

# A test to run: an existing test object, or a factory creating it
TestItem = SensorTest | TestFactory

def test_class(item: TestItem) -> type[SensorTest]:
    if isinstance(item, SensorTest):
        return type(item)
    return getattr(item, "func", item)   # Unwraps a functools.partial


class TestSession:
    """
    Runs tests one after another on a sensor. Driven the same way as a single SensorTest:

        session.start()
        while not session.finished:
            if session.request.blocking:
                session.respond(<the operator's answer to session.request>)
            else:
                session.update()

    session.test is the running test. A test that cannot be created (Ex: its constructor raises) ends the run,
    and is recorded in report()'s fatal_tests. Errors while a test runs are recorded in its own checks.
    """

    def __init__(self, tests: list[TestItem], sensor: ThreespaceSensor = None, streaming_manager: ThreespaceStreamingManager = None,
                 operator: str = None, context: dict = None, suite_version: str = "0.0.1"):
        """
        tests: test objects run as they are. Factories are called with sensor and streaming_manager when their turn
               comes, so a test is only created if the run gets that far.
        sensor: needed when any test is a factory. Defaults to the sensor of the first test object.
        streaming_manager: given to the tests created by factories. None: each test creates its own when it needs one.
        operator, context, suite_version: recorded in report(). context is any extra information, Ex: the operating system.
        """
        self.tests = list(tests)
        objects = [item for item in self.tests if isinstance(item, SensorTest)]
        self.sensor = sensor or (objects[0].sensor if objects else None)
        if self.sensor is None:
            raise ValueError("A sensor is needed to create the tests")
        if any(test.sensor is not self.sensor for test in objects):
            raise ValueError("Every test must be for the session's sensor")
        self.streaming_manager = streaming_manager
        self.operator = operator
        self.context = context or {}
        self.suite_version = suite_version

        self.test: SensorTest | None = None    # The running test, or the last one once finished
        self.index = -1                          # Index of the running test in tests
        self.finished = False
        self.cancelled = False
        self.results: list[TestResult] = []      # Checks of every test run so far, in order
        self.errors: list[dict] = []             # Tests that could not run: {"test_name", "error"}
        self.start_time: datetime.datetime = None
        self.end_time: datetime.datetime = None

        # Called with the test and step as each step starts, before it runs. See SensorTest.on_step_started
        self.on_step_started: Callable[[SensorTest, Step], None] | None = None

    @property
    def request(self) -> Request | None:
        """The active request, or None if the session is finished."""
        return None if self.finished or self.test is None else self.test.request

    @property
    def overall_success(self) -> bool:
        return self.finished and not self.cancelled and not self.errors and all(result.success for result in self.results)

    # ---- Runner interface, see SensorTest ----

    def start(self):
        if self.start_time is not None:
            raise RuntimeError("Test session already started")
        self.start_time = datetime.datetime.now(datetime.timezone.utc)
        self._advance()

    def update(self):
        if self.finished:
            return
        self.test.update()
        self._advance()

    def respond(self, answer: Any = None, request: Request = None) -> bool:
        """
        Answers the active request. 
        request: the request being answered. The answer is dropped unless the supplied request is still the active one (Ex: a double click answering it twice).
        Returns whether the answer was given to the test.
        """
        if request is not None and request != self.request:
            return False
        self.test.respond(answer)
        self._advance()
        return True

    def cancel(self):
        if self.finished:
            return
        self.cancelled = True
        if self.test is not None:
            self.test.cancel()
            self.results += self.test.result_flat
        self._finish()

    # ---- Results ----

    def report(self) -> dict:
        """The results document of the run"""
        return {
            "sensor_id": f"0x{self.sensor.serial_number:016X}",
            "operator": self.operator,
            "start_time": _utc_text(self.start_time),
            "end_time": _utc_text(self.end_time),
            "suite_version": self.suite_version,
            "firmware_version": self.sensor.firmware_version,
            "context": self.context,
            "overall_success": self.overall_success,
            "fatal_tests": list(self.errors),
            "failed_checks": [(result.test, result.check, result.components) for result in self.results if not result.success],
            "checks": [result.to_dict() for result in self.results],
        }

    # ---- Internals ----

    def _advance(self):
        """Once the running test has finished, starts the next one, until a test is waiting or none are left"""

        # Check if ready to advance to the next test
        while not self.finished and (self.test is None or self.test.finished):
            # Previous test has finished, collect its results before moving to the next one
            if self.test is not None:
                self.results += self.test.result_flat

            # Move to the next test in the sequence
            self.index += 1
            if self.index >= len(self.tests):
                self._finish()
                return

            # The next test, created now if given as a factory
            item = self.tests[self.index]
            try:
                test = item if isinstance(item, SensorTest) else item(self.sensor, streaming_manager=self.streaming_manager)
            except Exception as e:   # Only a factory can fail here
                logger.exception("Could not create the %s test", test_class(item).id)
                self.errors.append({"test_name": test_class(item).id, "error": str(e)})
                self._finish()
                return

            # Set up step callback forwarding to the session-level callback
            def step_started(step: Step, test=test):
                if self.on_step_started is not None:
                    self.on_step_started(test, step)
            test.on_step_started = step_started

            # Start the test
            self.test = test
            test.start()

    def _finish(self):
        self.finished = True
        self.end_time = datetime.datetime.now(datetime.timezone.utc)


def _utc_text(time: datetime.datetime | None) -> str | None:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ") if time is not None else None
