import sys

from yostlabs.tss3 import ThreespaceSensor
from yostlabs.tss3.utils.tests.base import SensorTest
from yostlabs.tss3.utils.tests.session import TestSession, TestItem, test_class
from yostlabs.tss3.utils.tests.cli import run_cli
import yostlabs.tss3.utils.tests   # Registers every test in SensorTest.REGISTERED
import json

import logging
logger = logging.getLogger(__name__)

def run_test(sensor: ThreespaceSensor, tests: list[TestItem],
             operator=None, context=None, test_suite_version="0.0.1") -> tuple[bool, dict]:
    """Runs the tests in the terminal. Returns the overall success and the results document, see TestSession.report()"""
    session = run_cli(TestSession(tests, sensor, operator=operator, context=context, suite_version=test_suite_version))
    return session.overall_success, session.report()

def auto_select_tests(sensor: ThreespaceSensor, fail_on_unknown_family=True) -> list[type[SensorTest]] | None:
    """The registered tests that apply to the sensor, in registration order"""
    family = sensor.sensor_family
    if family == "Unknown":
        logger.warning("Unknown sensor family, cannot determine which tests to run.")
        if fail_on_unknown_family:
            return None

    logger.info(f"Detected sensor family: {family}.")
    variation = sensor.hardware_version.variation
    return [test for test in SensorTest.REGISTERED if test.is_applicable(family, variation)]

def verbose_run_tests(sensor: ThreespaceSensor,
                      tests: list[TestItem],
                      output_path = "test_results.json"):
    print("Running Tests:")
    for item in tests:
        print(f" - {test_class(item).name}")

    overall_success, results = run_test(sensor, tests)
    sensor.cleanup()

    print(results)
    print("Overall success:", overall_success)

    with open(output_path, "w") as f:
        f.write(json.dumps(results, indent=4))

    return overall_success, results

def auto_run_tests():
    sensor = ThreespaceSensor()
    family = sensor.sensor_family
    if family == "Unknown":
        logger.error("Unknown sensor family, cannot determine which tests to run.")
        return False, {"error": "Unknown sensor family"}

    tests_to_run = auto_select_tests(sensor, fail_on_unknown_family=False)

    return verbose_run_tests(sensor, tests_to_run)

if __name__ == "__main__":
    # h = logging.StreamHandler(sys.stdout)
    # h.setFormatter(logging.Formatter("%(message)s"))
    # logging.basicConfig(level=logging.INFO, handlers=[h])

    auto_run_tests()
