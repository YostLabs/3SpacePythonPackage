import socket
import datetime
from typing import Any, Callable

from yostlabs.tss3 import ThreespaceSensor
from yostlabs.tss3.utils.streaming import ThreespaceStreamingManager
from yostlabs.tss3.utils.tests.base import SensorTest, TestResult, TestStatus, Request, Step
from yostlabs.tss3.utils.tests.report import SessionReport, TestReport

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
    and is recorded as the session's error. Errors while a test runs are recorded in its own checks.
    """

    def __init__(self, tests: list[TestItem], sensor: ThreespaceSensor = None, streaming_manager: ThreespaceStreamingManager = None,
                 name: str = None, certifies: bool = False, operator: str = None, station: str = None,
                 context: dict = None, suite_version: str = "0.0.1"):
        """
        tests: test objects run as they are. Factories are called with sensor and streaming_manager when their turn
               comes, so a test is only created if the run gets that far.
        sensor: needed when any test is a factory. Defaults to the sensor of the first test object.
        streaming_manager: given to the tests created by factories. None: each test creates its own when it needs one.
        name, certifies, operator, station, context, suite_version: recorded in report(), see report.py.
            name defaults to the test's name, or "Sensor Tests" for several. station defaults to this computer's name.
            context is any extra information, Ex: the operating system.
        """
        self.tests = list(tests)
        objects = [item for item in self.tests if isinstance(item, SensorTest)]
        self.sensor = sensor or (objects[0].sensor if objects else None)
        if self.sensor is None:
            raise ValueError("A sensor is needed to create the tests")
        if any(test.sensor is not self.sensor for test in objects):
            raise ValueError("Every test must be for the session's sensor")
        self.streaming_manager = streaming_manager
        self.name = name or (test_class(self.tests[0]).name if len(self.tests) == 1 else "Sensor Tests")
        self.certifies = certifies
        self.operator = operator
        self.station = station or socket.gethostname()
        self.context = context or {}
        self.suite_version = suite_version

        self.test: SensorTest | None = None    # The running test, or the last one once finished
        self.index = -1                          # Index of the running test in tests
        self.finished = False
        self.cancelled = False
        self.results: list[TestResult] = []      # Checks of every test run so far, in order
        self.error: str | None = None            # Why the session could not go on, Ex: a test could not be created
        self.started_at: datetime.datetime = None
        self.ended_at: datetime.datetime = None

        # Called with the test and step as each step starts, before it runs. See SensorTest.on_step_started
        self.on_step_started: Callable[[SensorTest, Step], None] | None = None

    @property
    def request(self) -> Request | None:
        """The active request, or None if the session is finished."""
        return None if self.finished or self.test is None else self.test.request

    @property
    def outcome(self) -> str:
        """running, pass, fail, cancelled or error"""
        if not self.finished:
            return "running"
        if self.error is not None:
            return "error"
        if self.cancelled:
            return "cancelled"
        if all(result.success for result in self.results):
            return "pass"
        return "error" if any(result.status == TestStatus.ERROR.value for result in self.results) else "fail"

    # ---- Runner interface, see SensorTest ----

    def start(self):
        if self.started_at is not None:
            raise RuntimeError("Test session already started")
        self.started_at = datetime.datetime.now(datetime.timezone.utc)
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
        """The results document of the session as it stands, see report.py. Only once started"""
        if self.started_at is None:
            raise RuntimeError("The test session has not started")
        firmware = self.sensor.firmware_version
        return SessionReport(
            serial=f"{self.sensor.serial_number:016X}" if self.sensor.serial_number else None,
            name=self.name, certifies=self.certifies, operator=self.operator, station=self.station,
            firmware_version=str(firmware) if firmware is not None else None, suite_version=self.suite_version,
            started_at=self.started_at, ended_at=self.ended_at, outcome=self.outcome, error=self.error,
            context=self.context,
            tests=[TestReport(test_class(item).id, self._checks(index)) for index, item in enumerate(self.tests)],
        ).to_dict()

    # ---- Internals ----

    def _checks(self, index: int) -> list[TestResult]:
        """The checks of the test at index so far. [] for a test the session has not reached"""
        test_id = test_class(self.tests[index]).id
        collected = [result for result in self.results if result.test == test_id]
        if not collected and index == self.index and self.test is not None and self.test.id == test_id:
            return self.test.result_flat   # Running, so not collected yet
        return collected

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
                self.error = f"Could not create the {test_class(item).id} test: {e}"
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
        self.ended_at = datetime.datetime.now(datetime.timezone.utc)
