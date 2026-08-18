import sys

from yostlabs.tss3 import ThreespaceSensor
from yostlabs.tss3.errors import UnsupportedTestError
from yostlabs.tss3.consts import *
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


def run_test(sensor: ThreespaceSensor, 
             test_table: dict[str, Callable[[ThreespaceSensor], tuple[bool,dict]] | dict]):
    results = {
        "overall_success": None,
        "failed_tests": [],
    }
    failed_tests = []
    overall_success = True
    for test_name, test in test_table.items():
        try:
            if isinstance(test, dict):
                func = test["func"]
                kwargs = test.get("kwargs", {})
                test_success, test_results = func(sensor, **kwargs)
            else:
                test_success, test_results = test(sensor)
            results[test_name] = test_results
            results[test_name]["overall_success"] = test_success
            if not test_success:
                overall_success = False
                failed_tests.append(test_name)
        except UnsupportedTestError as e:
            logger.warning(f"Unsupported test: {test_name}")
        except Exception as e:
            overall_success = False
            results[test_name] = {
                "overall_success": False,
                "error": str(e)
            }
            failed_tests.append(test_name)
            break
    results["overall_success"] = overall_success
    results["failed_tests"] = failed_tests
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