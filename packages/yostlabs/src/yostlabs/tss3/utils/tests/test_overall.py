import sys
from typing import Collection

from yostlabs.tss3 import ThreespaceSensor
from yostlabs.tss3.utils.tests.base import SensorTest, TestKind
from yostlabs.tss3.utils.tests.session import TestSession, TestItem, test_class
from yostlabs.tss3.utils.tests.report import SessionReport
from yostlabs.tss3.utils.tests.cli import run_cli
import yostlabs.tss3.utils.tests   # Registers every test in SensorTest.REGISTERED
import json

import logging
logger = logging.getLogger(__name__)

def run_test(sensor: ThreespaceSensor, tests: list[TestItem], operator=None, context=None, test_suite_version="0.0.1",
             not_installed: Collection[str] = ()) -> tuple[str, SessionReport]:
    """
    Runs the tests in the terminal. Returns the outcome and the results document, see TestSession.report()
    not_installed: hardware the sensor doesn't have on yet, see TestSession
    """
    session = run_cli(TestSession(tests, sensor, operator=operator, context=context, suite_version=test_suite_version,
                                  not_installed=not_installed))
    return session.outcome, session.report()

def auto_select_tests(sensor: ThreespaceSensor, fail_on_unknown_family=True, kind: TestKind = TestKind.FUNCTIONAL,
                      hardware: Collection[str] | None = None) -> list[type[SensorTest]] | None:
    """
    The registered tests of the kind that apply to the sensor, in registration order.
    hardware: what the sensor is built with, Ex: {"gps", "rtc", "battery"}, see SensorTest.required_hardware. None: not
    known, so every test of its family is selected, including ones for hardware it may not have.
    """
    family = sensor.sensor_family
    if family == "Unknown":
        logger.warning("Unknown sensor family, cannot determine which tests to run.")
        if fail_on_unknown_family:
            return None

    logger.info(f"Detected sensor family: {family}.")
    variation = sensor.hardware_version.variation
    return [test for test in SensorTest.REGISTERED if test.kind == kind and test.is_applicable(family, variation, hardware)]

def verbose_run_tests(sensor: ThreespaceSensor,
                      tests: list[TestItem],
                      output_path = "test_results.json",
                      not_installed: Collection[str] = ()):
    print("Running Tests:")
    for item in tests:
        print(f" - {test_class(item).name}")

    outcome, results = run_test(sensor, tests, not_installed=not_installed)
    sensor.cleanup()

    print(results.to_dict())
    print("Outcome:", outcome)

    with open(output_path, "w") as f:
        f.write(json.dumps(results.to_dict(), indent=4))

    return outcome, results

def auto_run_tests(hardware: Collection[str] | None = None, not_installed: Collection[str] = ()):
    """
    hardware: what the sensor is built with, see auto_select_tests()
    not_installed: what it doesn't have on yet, see TestSession
    """
    sensor = ThreespaceSensor()
    family = sensor.sensor_family
    if family == "Unknown":
        logger.error("Unknown sensor family, cannot determine which tests to run.")
        return False, {"error": "Unknown sensor family"}

    tests_to_run = auto_select_tests(sensor, fail_on_unknown_family=False, hardware=hardware)

    return verbose_run_tests(sensor, tests_to_run, not_installed=not_installed)

if __name__ == "__main__":
    # h = logging.StreamHandler(sys.stdout)
    # h.setFormatter(logging.Formatter("%(message)s"))
    # logging.basicConfig(level=logging.INFO, handlers=[h])

    auto_run_tests()
