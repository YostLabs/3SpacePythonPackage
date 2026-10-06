import json
import time
import inspect
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Callable, ClassVar, Generator

from yostlabs.tss3 import ThreespaceSensor, ThreespaceHardwareVersion
from yostlabs.tss3.errors import SettingError, UnsupportedTestError
from yostlabs.tss3.utils.streaming import ThreespaceStreamingManager, StreamableCommands

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

    @property
    def answerable(self) -> bool:
        """Whether respond() may be called while this is the active request"""
        return self.blocking

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
    The step resumes on each update() and yields again to keep waiting, so the yield gives None.
    The operator may also pick one of the actions at any time. The yield then gives that action.
    """
    status: str = ""                    # Live detail, Ex: "Held 1.4 / 2.0 s"
    actions: tuple[str, ...] = ()       # Ex: ("Flipped",)

    blocking: ClassVar[bool] = False

    @property
    def answerable(self) -> bool:
        return bool(self.actions)

    def validate(self, answer: Any):
        if answer not in self.actions:
            raise ValueError(f"{answer!r} is not one of {self.actions}")


# ----------------------------------------------------------------------
# Steps
# ----------------------------------------------------------------------

class StepState(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    SKIPPED = "skipped"     # Not run: skipped by the test, an earlier step stopped the test, or it was cancelled
    ERROR = "error"         # Raised an exception


class SkipStep(Exception):
    """Raised by a step that does not apply, Ex: the barometer steps on a sensor without a barometer."""


class Step:
    """
    A named stage of a test, declared with the @step decorator. A test runs its steps in the order
    they are declared. The method may yield Requests, or simply return when it needs nothing from the operator.
    """

    def __init__(self, func: Callable, title: str, check: str | None):
        self.func = func
        self.name = func.__name__
        self.title = title
        self.check = check   # The check this step reports into, None if it creates its own

    def __repr__(self):
        return f"Step({self.name!r})"


_METHOD_NAME = object()

def step(title: str, check: str | None = _METHOD_NAME):
    """
    Declares a SensorTest method as a step.
    title: shown to the operator.
    check: the check (TestResult) this step reports into. Defaults to the method name.
           Several steps may share one, Ex: the battery test's disconnect and reconnect steps.
           None: the step creates its own checks in test.results, Ex: one per detected component.
    """
    return lambda func: Step(func, title, func.__name__ if check is _METHOD_NAME else check)


# ----------------------------------------------------------------------
# Sensor variants
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class SensorVariant:
    """
    Sensors a test applies to. family is a THREESPACE_FAMILY_* name, Ex: THREESPACE_FAMILY_DATA_LOGGER.
    None for family: every sensor. None for variation: every variation of the family.
    """
    family: str | None = None
    variation: int | None = None

    def matches(self, family: str, variation: int | None) -> bool:
        if self.family is None:
            return True
        if family != self.family:
            return False
        # A specific variation is only confirmed when the variation is known
        return self.variation is None or self.variation == variation

ALL_SENSORS = (SensorVariant(),)


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
                test.update()   # Or test.respond(<action>) when the operator picks one of the Busy request's actions

    test.step is the active step, test.step_states the progress of every step.
    The test only talks to the sensor inside these calls.

    However the test ends (finished, failed, raised or cancelled), streaming started through start_streaming()
    is stopped, cleanup() is called, then every setting changed through change_settings() is restored.
    A step raising UnsupportedTestError ends the test, marking every check not yet run as not applicable.
    """

    id: ClassVar[str]                           # Identifies the test in results, Ex: "led"
    name: ClassVar[str]                         # Shown to the operator, Ex: "LED"
    variants: ClassVar[tuple[SensorVariant, ...]]   # The sensors it applies to, Ex: ALL_SENSORS
    stop_on_failure: ClassVar[bool] = False     # Skip the remaining steps once a check fails
    steps: ClassVar[tuple[Step, ...]] = ()      # Collected from the @step methods, in declaration order

    # Every test class, in the order they were defined. Importing yostlabs.tss3.utils.tests defines them all.
    REGISTERED: ClassVar[list[type["SensorTest"]]] = []

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for attribute in ("id", "name", "variants"):
            if not hasattr(cls, attribute):
                raise TypeError(f"{cls.__name__} must define {attribute}")
        cls.steps = tuple(value for value in cls.__dict__.values() if isinstance(value, Step))
        SensorTest.REGISTERED.append(cls)

    @classmethod
    def is_applicable(cls, family: str | ThreespaceHardwareVersion, variation: int | None = None) -> bool:
        """
        Whether the test applies to a sensor, given its family name and variation, or its hardware version.
        A test for a specific variation needs the variation, the family alone is not enough.
        """
        if isinstance(family, ThreespaceHardwareVersion):
            family, variation = family.family_name, family.variation
        return any(variant.matches(family, variation) for variant in cls.variants)

    def __init__(self, sensor: ThreespaceSensor, streaming_manager: ThreespaceStreamingManager = None):
        self.sensor = sensor
        self._streaming_manager = streaming_manager

        # One result per check, created up front so checks never reached still appear as not run.
        # Steps without a check of their own add theirs as they go, keyed however suits them.
        self.results: dict[Any, TestResult] = {s.check: TestResult(self.id, s.check) for s in self.steps if s.check is not None}
        self.step_states: dict[Step, StepState] = {s: StepState.PENDING for s in self.steps}
        self.step: Step | None = None           # The active step
        self.request: Request | None = None     # What the active step is waiting on
        self.finished = False
        self.cancelled = False

        # Called with each step as it starts, before it runs. A runner reading test.step only after
        # update() returns would see it late when the step blocks first, Ex: during a hard reset.
        self.on_step_started: Callable[[Step], None] | None = None

        self._settings_cache: dict[str, Any] = {}
        self._run: Generator | None = None
        self._streaming_callback: Callable | None = None
        self._enabled_streaming = False

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
        """Answers the active request: a blocking one, or one of a Busy request's actions."""
        if self.finished or not self.request.answerable:
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

    def wait(self, seconds: float, text: str) -> Generator:
        """Waits without blocking the thread. Use as `yield from self.wait(1.0, "Waiting for the GPS")`"""
        end = time.perf_counter() + seconds
        while (remaining := end - time.perf_counter()) > 0:
            yield Busy(text, f"{remaining:.1f} s")

    def read_debug_messages(self) -> list[str]:
        """Reads and removes every queued debug message"""
        count = self.sensor.getNumDebugMessages().data
        return [self.sensor.getOldestDebugMessage().data.strip() for _ in range(count)]

    def start_streaming(self, commands: list[tuple[StreamableCommands, int | None]], callback: Callable, hz: int):
        """
        Streams the commands, given as (command, param), calling callback(status, user_data) for every packet.
        The test must call self.streaming_manager.update() while streaming. Stopped by stop_streaming() or when the test ends.
        """
        if self._streaming_callback is not None:
            raise RuntimeError("Already streaming, call stop_streaming() first")
        manager = self.streaming_manager
        for command, param in commands:
            if not manager.register_command(self, command, param=param, immediate_update=False):
                raise RuntimeError(f"No streaming slot left for {command.name}")
        manager.register_callback(callback, hz=hz)
        self._streaming_callback = callback
        if not manager.enabled:
            manager.enable()
            self._enabled_streaming = True

    def stop_streaming(self):
        if self._streaming_callback is None:
            return
        manager = self.streaming_manager
        manager.unregister_callback(self._streaming_callback)
        manager.unregister_all_commands_from_owner(self)
        self._streaming_callback = None
        if self._enabled_streaming:   # Leave a supplied manager streaming if it already was
            manager.disable()
            self._enabled_streaming = False

    def cleanup(self):
        """Override for any restoring beyond streaming and settings. Called after streaming stops, before the settings are restored."""

    # ---- Internals ----

    def _run_steps(self) -> Generator:
        for s in self.steps:
            self.step = s
            self.step_states[s] = StepState.ACTIVE
            if self.on_step_started is not None:
                self.on_step_started(s)
            try:
                requests = s.func(self)
                if inspect.isgenerator(requests):
                    yield from requests
            except SkipStep:
                self.step_states[s] = StepState.SKIPPED
                continue
            except UnsupportedTestError as e:
                for result in self.results.values():
                    if result.status == TestStatus.NOT_RUN:
                        result.set_status(TestStatus.NA).message = str(e)
                return
            self.step_states[s] = StepState.DONE
            if self.stop_on_failure and s.check is not None and self.check().status in (TestStatus.FAIL, TestStatus.ERROR):
                return

    def _resume(self, answer: Any):
        try:
            self.request = self._run.send(answer)
        except StopIteration:
            self._finish()
        except Exception as e:
            logger.exception("%s test raised during step %r", self.name, self.step.name)
            self.step_states[self.step] = StepState.ERROR
            if self.step.check is None:   # The step's own checks are unknown, so record the error under the step
                self.results[self.step.name] = TestResult(self.id, self.step.name)
            self.results[self.step.check or self.step.name].errored(str(e))
            self._finish()

    def _finish(self):
        self.finished = True
        self.request = None
        for s, state in self.step_states.items():
            if state in (StepState.PENDING, StepState.ACTIVE):
                self.step_states[s] = StepState.SKIPPED
        try:
            self.stop_streaming()
            self.cleanup()
            if self._settings_cache:
                self.sensor.write_settings(**self._settings_cache)
        except Exception:
            # Ex: cancelled while the sensor was unplugged
            logger.exception("Failed to restore the sensor after the %s test", self.name)
