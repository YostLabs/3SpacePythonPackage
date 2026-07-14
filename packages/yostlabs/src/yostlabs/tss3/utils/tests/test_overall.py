from yostlabs.tss3 import ThreespaceSensor
from yostlabs.tss3.errors import UnsupportedTestError
from yostlabs.tss3.consts import *
import yostlabs.tss3.utils.tests as tests
from typing import Callable
import json

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
            print("Unsupported test:", test_name)
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
        print("Unknown sensor family, cannot determine which tests to run.")
        return False, {"error": "Unknown sensor family"}
    print(f"Detected sensor family: {family}.")
    
    # Define the tests to run based on the sensor family
    GENERIC_TESTS = {
        "self_test": tests.test_self.run_test,
        "led_test": tests.test_led.run_test,
        "component_test": tests.test_components.run_test,
    }

    family_to_tests = {
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

    tests_to_run = GENERIC_TESTS
    if family in family_to_tests:
        tests_to_run |= family_to_tests[family]

    return verbose_run_tests(sensor, tests_to_run)

if __name__ == "__main__":
    auto_run_tests()