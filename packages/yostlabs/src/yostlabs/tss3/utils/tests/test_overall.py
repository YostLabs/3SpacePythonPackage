import sys
import datetime

from yostlabs.tss3 import ThreespaceSensor
from yostlabs.tss3.utils.tests.base import SensorTest, TestResult
from yostlabs.tss3.utils.tests.cli import run_cli
import yostlabs.tss3.utils.tests   # Registers every test in SensorTest.REGISTERED
from typing import Callable
import json

import logging
logger = logging.getLogger(__name__)

# Creates a test for a sensor. A test class, or Ex: functools.partial(ComponentTest, expected_components=[...])
TestFactory = Callable[[ThreespaceSensor], SensorTest]

def overall_test_initialize_results(sensor: ThreespaceSensor, operator=None, context=None, test_suite_version="0.0.1"):
    results = {
        "sensor_id": None,
        "operator": operator,
        "start_time": None,
        "end_time": None,
        "suite_version": None,
        "firmware_version": None,
        "context": context or {}, #Any additional context information that may be useful for debugging to add later, such as operating system
        "overall_success": None,
        "fatal_tests": [],
        "failed_checks": [],
        "checks": []
    }

    results["sensor_id"] = f"0x{sensor.serial_number:016X}"
    results["start_time"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    results["suite_version"] = test_suite_version
    results["firmware_version"] = sensor.firmware_version

    return results

def overall_test_finalize_results(results: dict, test_checks: list[TestResult]):
    overall_success = True
    for check in test_checks:
        results["checks"].append(check.to_dict())
        if not check.success:
            results["failed_checks"].append((check.test, check.check, check.components))
            overall_success = False
    results["end_time"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    results["overall_success"] = overall_success and len(results["fatal_tests"]) == 0
    return results

def overall_test_add_error(results: dict, test_name: str, error: str):
    results["fatal_tests"].append({
        "test_name": test_name,
        "error": error
    })
    return results

def run_test(sensor: ThreespaceSensor, tests: list[TestFactory],
             operator=None, context=None, test_suite_version="0.0.1"):
    results = overall_test_initialize_results(sensor, operator=operator, context=context, test_suite_version=test_suite_version)

    test_checks = []

    for create_test in tests:
        try:
            test = run_cli(create_test(sensor))
            test_checks.extend(test.result_flat)
        except Exception as e:
            # A test records its own errors in its checks, so this is a failure outside of one, Ex: creating it
            overall_test_add_error(results, getattr(create_test, "id", repr(create_test)), str(e))
            break
    results = overall_test_finalize_results(results, test_checks)
    overall_success = results["overall_success"]
    return overall_success, results

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
                      tests: list[TestFactory],
                      output_path = "test_results.json"):
    print("Running Tests:")
    for create_test in tests:
        print(f" - {getattr(create_test, 'name', repr(create_test))}")

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
