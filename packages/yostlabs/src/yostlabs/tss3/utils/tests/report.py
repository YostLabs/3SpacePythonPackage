"""
The results document of a test session, see TestSession.report(). It holds everything needed to keep the session, 
so any run of the tests, from the command line or an application, produces the same document:

{
    "serial": "1400010101000001",       # Hex. null when the sensor has no serial number (0)
    "name": "Full Test",
    "certifies": false,
    "operator": "Unknown",
    "station": "DESKTOP-1",             # The computer the tests ran on
    "firmware_version": "1.2.3",
    "suite_version": "0.0.1",
    "context": {},
    "started_at": "2026-10-08T15:25:25Z",
    "ended_at": "2026-10-08T15:27:02Z", # null while running
    "outcome": "fail",                  # running, pass, fail, cancelled or error
    "error": null,                      # Why the session could not go on, Ex: a test could not be created
    "failed_checks": [                  # The checks that failed or errored, to see at a glance what went wrong
        {"test_id": "led", "check": "green", "components": [], "status": "fail", "message": "Did not match"}
    ],
    "tests": [                          # Every test of the session, in order, even those it never reached
        {"test_id": "led", "status": "fail", "checks": [...]}   # checks: TestResult dicts, without their "test"
    ]
}
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from yostlabs.tss3.utils.tests.base import TestResult, TestStatus, summarize

OUTCOMES = ("running", "pass", "fail", "cancelled", "error")


@dataclass
class TestReport:
    """One test of a session"""
    test_id: str
    checks: list[TestResult] = field(default_factory=list)

    @property
    def status(self) -> str:
        """The checks summarized, see summarize(). not_run without any"""
        return summarize(check.status for check in self.checks)

    def to_dict(self) -> dict:
        checks = []
        for check in self.checks:
            data = check.to_dict()
            del data["test"]   # The test's own test_id, moving it to the parent dictionary
            checks.append(data)
        return {"test_id": self.test_id, "status": self.status, "checks": checks}

    @classmethod
    def from_dict(cls, data: dict) -> "TestReport":
        """status is not read: it always follows from the checks"""
        return cls(data["test_id"], [TestResult.from_dict({**check, "test": data["test_id"]}) for check in data["checks"]])


@dataclass
class SessionReport:
    """A test session's results document, see the module docstring"""
    serial: str | None
    name: str
    certifies: bool
    operator: str | None
    station: str
    firmware_version: str | None
    suite_version: str
    started_at: datetime                # UTC
    outcome: str                        # One of OUTCOMES
    tests: list[TestReport]
    ended_at: datetime | None = None    # UTC. None while running
    error: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.outcome not in OUTCOMES:
            raise ValueError(f"Unknown outcome {self.outcome!r}")

    @property
    def failed_checks(self) -> list[TestResult]:
        return [check for test in self.tests for check in test.checks
                if check.status in (TestStatus.FAIL.value, TestStatus.ERROR.value)]

    def to_dict(self) -> dict:
        return {
            "serial": self.serial,
            "name": self.name,
            "certifies": self.certifies,
            "operator": self.operator,
            "station": self.station,
            "firmware_version": self.firmware_version,
            "suite_version": self.suite_version,
            "context": self.context,
            "started_at": _time_text(self.started_at),
            "ended_at": _time_text(self.ended_at),
            "outcome": self.outcome,
            "error": self.error,
            "failed_checks": [{"test_id": check.test, "check": check.check, "components": check.components,
                               "status": check.status, "message": check.message} for check in self.failed_checks],
            "tests": [test.to_dict() for test in self.tests],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionReport":
        """failed_checks and each test's status are not read: they always follow from the checks"""
        return cls(serial=data["serial"], name=data["name"], certifies=data["certifies"], operator=data["operator"],
                   station=data["station"], firmware_version=data["firmware_version"],
                   suite_version=data["suite_version"], started_at=_parse_time(data["started_at"]),
                   outcome=data["outcome"], tests=[TestReport.from_dict(test) for test in data["tests"]],
                   ended_at=_parse_time(data["ended_at"]), error=data["error"], context=data["context"])


def _time_text(time: datetime | None) -> str | None:
    return time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ") if time is not None else None

def _parse_time(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text is not None else None
