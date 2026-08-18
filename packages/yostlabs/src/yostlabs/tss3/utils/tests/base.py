import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any

from yostlabs.tss3 import ThreespaceSensor

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
