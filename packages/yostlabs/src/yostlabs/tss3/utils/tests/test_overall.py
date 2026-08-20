import sys
import datetime

from yostlabs.tss3 import ThreespaceSensor
from yostlabs.tss3.errors import UnsupportedTestError
from yostlabs.tss3.consts import *
from yostlabs.tss3.utils.tests.base import TestResult, TestStatus
import yostlabs.tss3.utils.tests as tests
from typing import Callable
import json

import logging
logger = logging.getLogger(__name__)

GENERIC_TESTS = {
    "self_test": tests.test_self.run_test,
    "led_test": tests.test_led.run_test,
    "component_test": tests.test_components.run_test,
}

FAMILY_TO_TESTS = {
    THREESPACE_FAMILY_EMBEDDED: {
        # Add other tests for embedded family here
    },
    THREESPACE_FAMILY_DATA_LOGGER: {
        "battery_test": tests.test_battery.run_test,
        "rtc_test": tests.test_rtc.run_test,
        "button_test": tests.test_button.run_test,
        "gps_test": tests.test_gps.run_test,
        # Add other tests for data logger family here
    },
    THREESPACE_FAMILY_LX: {
        # Add other tests for LX family here
    },
    THREESPACE_FAMILY_USB: {
        # Add other tests for USB family here
    },
}

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

def run_test(sensor: ThreespaceSensor, 
             test_table: dict[str, Callable[[ThreespaceSensor], tuple[bool,list[TestResult]]] | dict],
             operator=None, context=None, test_suite_version="0.0.1"):
    results = overall_test_initialize_results(sensor, operator=operator, context=context, test_suite_version=test_suite_version)

    test_checks = []

    for test_name, test in test_table.items():
        try:
            if isinstance(test, dict):
                func = test["func"]
                kwargs = test.get("kwargs", {})
                test_success, test_results = func(sensor, **kwargs)
            else:
                test_success, test_results = test(sensor)
            test_checks.extend(test_results)
        except UnsupportedTestError as e:
            logger.warning(f"Unsupported test: {test_name}")
        except Exception as e:
            overall_test_add_error(results, test_name, str(e))
            break
    results = overall_test_finalize_results(results, test_checks)
    overall_success = results["overall_success"]
    return overall_success, results

def auto_select_tests(sensor: ThreespaceSensor, fail_on_unknown_family=True):
    family = sensor.sensor_family
    if family == "Unknown":
        logger.warning("Unknown sensor family, cannot determine which tests to run.")
        if fail_on_unknown_family:
            return None
        else:
            return GENERIC_TESTS
    
    logger.info(f"Detected sensor family: {family}.")

    tests_to_run = GENERIC_TESTS
    if family in FAMILY_TO_TESTS:
        tests_to_run |= FAMILY_TO_TESTS[family]

    return tests_to_run

def verbose_run_tests(sensor: ThreespaceSensor, 
                      test_table: dict[str, Callable[[ThreespaceSensor], tuple[bool,dict]] | dict],
                      output_path = "test_results.json"):
    print("Running Tests:")
    for test_name in test_table.keys():
        print(f" - {test_name}")

    overall_success, results = run_test(sensor, test_table)
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
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[h])

    auto_run_tests()