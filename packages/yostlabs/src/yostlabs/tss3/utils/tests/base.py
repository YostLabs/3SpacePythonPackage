import json
import inspect
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Callable, ClassVar, Generator

from yostlabs.tss3 import ThreespaceSensor
from yostlabs.tss3.errors import SettingError
from yostlabs.tss3.utils.streaming import ThreespaceStreamingManager

import logging
logger = logging.getLogger(__name__)

"""
Test results should be stored as a list of TestResult entries, one per
check/measurement performed. Each entry serializes to JSON as:

{
    "test": "self_test",
    "check": "sub_test_name",
    "components": ["Accel:8"],
    "status": "pass",
    "measurements": {
        "raw": 0
    },
    "criteria": {
        "raw_equals": 0
    },
    "message": null
}
"""


class TestStatus(str, Enum):
    PASS = "pass"   #Criteria met, test passed
    FAIL = "fail"   #Criteria not met, test failed
    ERROR = "error" #Error occurred during test, test failed
    SKIP = "skip"   #Test intentionally skipped with reason. Check message.
    NA = "n/a"      #Test not applicable to this sensor.
    INFO = "info"   #Test informational only, not a pass/fail check. Check message.
    NOT_RUN = "not_run"   #Test not run yet


@dataclass
class TestResult:
    """
    Stores a single check/measurement performed as part of a sensor test and
    can be serialized to/from the JSON format described above.
    """

    test: str
    check: str
    components: list[str] = field(default_factory=list)
    status: str = TestStatus.NOT_RUN.value
    measurements: dict[str, Any] = field(default_factory=dict)
    criteria: dict[str, Any] = field(default_factory=dict)
    message: str | None = None

    # ---- status helpers ----
    @property
    def success(self) -> bool:
        return self.status in [TestStatus.PASS.value, TestStatus.NA.value, TestStatus.INFO.value]

    def set_status(self, status: TestStatus | str) -> "TestResult":
        self.status = status.value if isinstance(status, TestStatus) else status
        return self

    def passed(self, message: str | None = None) -> "TestResult":
        self.message = message
        return self.set_status(TestStatus.PASS)

    def failed(self, message: str | None = None) -> "TestResult":
        self.message = message
        return self.set_status(TestStatus.FAIL)

    def errored(self, message: str | None = None) -> "TestResult":
        self.message = message
        return self.set_status(TestStatus.ERROR)

    def skipped(self, message: str | None = None) -> "TestResult":
        self.message = message
        return self.set_status(TestStatus.SKIP)

    # ---- content helpers ----
    def add_component(self, component: str) -> "TestResult":
        if component not in self.components:
            self.components.append(component)
        return self

    def add_measurement(self, name: str, value: Any) -> "TestResult":
        self.measurements[name] = value
        return self

    def add_criteria(self, name: str, value: Any) -> "TestResult":
        self.criteria[name] = value
        return self

    # ---- identification helpers ----
    @property
    def unique_id(self) -> str:
        return (self.test, self.check, tuple(sorted(self.components)))

    # ---- (de)serialization ----
    def to_dict(self) -> dict:
        result = asdict(self)
        result["status"] = self.status
        return result

    def to_json(self, **kwargs) -> str:
        return json.dumps(self.to_dict(), **kwargs)

    @classmethod
    def from_dict(cls, data: dict) -> "TestResult":
        return cls(
            test=data["test"],
            check=data["check"],
            components=list(data.get("components", [])),
            status=data.get("status", TestStatus.PASS.value),
            measurements=dict(data.get("measurements", {})),
            criteria=dict(data.get("criteria", {})),
            message=data.get("message"),
        )


class SensorTestBase(ABC):

    def __init__(self, sensor: ThreespaceSensor):
        self.sensor = sensor

        # Stored as a dict to make it easier to update results
        # while test is running. For final use, use the results_flat property.
        self.result: dict[Any, TestResult] = {}

    @property
    def result_flat(self) -> list[TestResult]:
        return list(self.result.values())

    @property
    def overall_success(self) -> bool:
        return all(result.success for result in self.result.values())

    @abstractmethod
    def start(self):
        """Begin the test, setting up hardware as needed."""
        ...

    def cancel(self):
        """Abort the test and restore any hardware state changed by start()."""
        ...


# ----------------------------------------------------------------------
# Requests: what a running test needs from the operator.
# A step yields one, the runner (CLI or GUI) presents it, and the answer
# comes back as the value of the yield. A GUI may give a request type its own
# page. Any type without one is shown like the request type it derives from.
# ----------------------------------------------------------------------

@dataclass
class Request:
    text: str

    # Blocking requests wait for respond(). Non-blocking ones resume the step on every update().
    blocking: ClassVar[bool] = True

    def validate(self, answer: Any):
        """Raises ValueError if answer is not a valid answer to this request."""
        if answer is not None:
            raise ValueError(f"{type(self).__name__} takes no answer, got {answer!r}")


@dataclass
class Message(Request):
    """Information the operator acknowledges. Answer: None"""


@dataclass
class Confirm(Request):
    """A yes/no question. Answer: bool"""

    def validate(self, answer: Any):
        if not isinstance(answer, bool):
            raise ValueError(f"Confirm takes a bool, got {answer!r}")


@dataclass
class Choice(Request):
    """Pick one of several options. Answer: the chosen option"""
    options: tuple[str, ...] = ()

    def validate(self, answer: Any):
        if answer not in self.options:
            raise ValueError(f"{answer!r} is not one of {self.options}")


@dataclass
class Busy(Request):
    """
    The test is working, or waiting on something it detects itself (Ex: the sensor being unplugged).
    Not answered. The step resumes on each update() and yields again to keep waiting.
    """
    status: str = ""    # Live detail, Ex: "Held 1.4 / 2.0 s"

    blocking: ClassVar[bool] = False


# ----------------------------------------------------------------------
# Steps
# ----------------------------------------------------------------------

class StepState(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    SKIPPED = "skipped"     # Never reached: an earlier step stopped the test, or it was cancelled
    ERROR = "error"         # Raised an exception


class Step:
    """
    A named stage of a test, declared with the @step decorator. A test runs its steps in the order
    they are declared. The method may yield Requests, or simply return when it needs nothing from the operator.
    """

    def __init__(self, func: Callable, title: str, check: str | None):
        self.func = func
        self.name = func.__name__
        self.title = title
        self.check = check or self.name   # The check this step reports into

    def __repr__(self):
        return f"Step({self.name!r})"


def step(title: str, check: str | None = None):
    """
    Declares a SensorTest method as a step.
    title: shown to the operator.
    check: the check (TestResult) this step reports into. Defaults to the method name.
           Several steps may share one, Ex: the battery test's disconnect and reconnect steps.
    """
    return lambda func: Step(func, title, check)


# ----------------------------------------------------------------------
# SensorTest
# ----------------------------------------------------------------------

class SensorTest:
    """
    A test made of steps. Driven by a runner the same way on the command line and in a GUI:

        test.start()
        while not test.finished:
            if test.request.blocking:
                test.respond(<the operator's answer to test.request>)
            else:
                test.update()

    test.step is the active step, test.step_states the progress of every step.
    The test only talks to the sensor inside these calls.

    However the test ends (finished, failed, raised or cancelled), every setting changed through
    change_settings() is restored, then cleanup() is called.
    """

    id: ClassVar[str]                           # Identifies the test in results, Ex: "led"
    name: ClassVar[str]                         # Shown to the operator, Ex: "LED"
    stop_on_failure: ClassVar[bool] = False     # Skip the remaining steps once a check fails
    steps: ClassVar[tuple[Step, ...]] = ()      # Collected from the @step methods, in declaration order

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.steps = tuple(value for value in cls.__dict__.values() if isinstance(value, Step))

    def __init__(self, sensor: ThreespaceSensor, streaming_manager: ThreespaceStreamingManager = None):
        self.sensor = sensor
        self._streaming_manager = streaming_manager

        # One result per check, created up front so checks never reached still appear as not run
        self.results: dict[str, TestResult] = {s.check: TestResult(self.id, s.check) for s in self.steps}
        self.step_states: dict[Step, StepState] = {s: StepState.PENDING for s in self.steps}
        self.step: Step | None = None           # The active step
        self.request: Request | None = None     # What the active step is waiting on
        self.finished = False
        self.cancelled = False

        self._settings_cache: dict[str, Any] = {}
        self._run: Generator | None = None

    @property
    def streaming_manager(self) -> ThreespaceStreamingManager:
        """The supplied streaming manager, or one created for this test (Ex: on the command line)"""
        if self._streaming_manager is None:
            self._streaming_manager = ThreespaceStreamingManager(self.sensor)
        return self._streaming_manager

    @property
    def result_flat(self) -> list[TestResult]:
        return list(self.results.values())

    @property
    def overall_success(self) -> bool:
        return all(result.success for result in self.results.values())

    # ---- Runner interface ----

    def start(self):
        if self._run is not None:
            raise RuntimeError(f"{self.name} test already started")
        self._run = self._run_steps()
        self._resume(None)

    def update(self):
        """Call periodically. Resumes the active step while it is Busy."""
        if self.finished or self.request.blocking:
            return
        self._resume(None)

    def respond(self, answer: Any = None):
        """Answers the active blocking request."""
        if self.finished or not self.request.blocking:
            raise RuntimeError(f"{self.name} test is not waiting for an answer")
        self.request.validate(answer)
        self._resume(answer)

    def cancel(self):
        if self.finished:
            return
        self.cancelled = True
        if self._run is not None:
            self._run.close()   # Raises GeneratorExit at the active step's yield, so its finally blocks run
        self._finish()

    # ---- For tests ----

    def check(self, name: str = None) -> TestResult:
        """The result of a check. Defaults to the active step's check."""
        return self.results[name or self.step.check]

    def change_settings(self, **settings):
        """Writes settings, first saving the original value of each so it is restored when the test ends."""
        new_keys = [key for key in settings if key not in self._settings_cache]
        if new_keys:
            self._settings_cache |= self.sensor.read_settings(*new_keys)
        err, _ = self.sensor.write_settings(**settings)
        if err:
            raise SettingError(f"Failed to write {settings}: error {err}")

    def cleanup(self):
        """Override for any restoring beyond settings. Called after the settings are restored."""

    # ---- Internals ----

    def _run_steps(self) -> Generator:
        for s in self.steps:
            self.step = s
            self.step_states[s] = StepState.ACTIVE
            requests = s.func(self)
            if inspect.isgenerator(requests):
                yield from requests
            self.step_states[s] = StepState.DONE
            if self.stop_on_failure and self.check().status in (TestStatus.FAIL, TestStatus.ERROR):
                return

    def _resume(self, answer: Any):
        try:
            self.request = self._run.send(answer)
        except StopIteration:
            self._finish()
        except Exception as e:
            logger.exception("%s test raised during step %r", self.name, self.step.name)
            self.step_states[self.step] = StepState.ERROR
            self.check().errored(str(e))
            self._finish()

    def _finish(self):
        self.finished = True
        self.request = None
        for s, state in self.step_states.items():
            if state in (StepState.PENDING, StepState.ACTIVE):
                self.step_states[s] = StepState.SKIPPED
        try:
            if self._settings_cache:
                self.sensor.write_settings(**self._settings_cache)
            self.cleanup()
        except Exception:
            # Ex: cancelled while the sensor was unplugged
            logger.exception("Failed to restore the sensor after the %s test", self.name)
