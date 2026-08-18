# Generic self test
# The other tests should catch any errors this catches
# but this is still good to run in case any conditions
# were missed, and may give additional information in the
# case of failures.

from yostlabs.tss3.utils.tests.base import SensorTestBase, TestResult, TestStatus
from yostlabs.tss3.api import ThreespaceSensor, InvalidKeyError
from yostlabs.tss3.consts import *
import time

class SelfTest(SensorTestBase):

    # Keys must be in order of the bits in the self test result bitfield.
    BIT_KEYS = ["accel", "gyro", "mag", "baro", "rtc", "gps", "bluetooth", "sd", "sms", "battery"]

    def __init__(self, sensor: ThreespaceSensor):
        super().__init__(sensor)

        self.settings_cache = {}
        self.result: dict[str, TestResult] = {
            "bitfield": TestResult("self", "bitfield"),
        }

    def __cache_settings(self):
        """
        GPS Self test relies on checking messages are being retrieved. This may fail if the GPS is not enabled.
        """

        self.settings_cache = {}
        gps_start_time = None
        
        # This setting specifically has a bug on some current devices where the self test is not properly
        # handling it. Temporarily putting this here to fix the issue until a firmware update is released.
        try:
            result = self.sensor.read_settings("gps_periodic_enabled")
            if result["gps_periodic_enabled"]:
                self.settings_cache |= result
                self.sensor.write_settings(gps_periodic_enabled=0)
                gps_start_time = time.perf_counter()
        except InvalidKeyError:
            pass

        if gps_start_time is not None:
            #TODO: Replace this with a state to avoid blocking
            time.sleep(1)
    
    def __restore_settings(self):
        if self.settings_cache:
            self.sensor.write_settings(**self.settings_cache)

    def start(self):
        self.__cache_settings()

        result = self.result["bitfield"]
        raw = self.sensor.selfTest().data
        result.measurements["raw"] = raw

        for i, key in enumerate(self.BIT_KEYS):
            result.measurements[key] = not bool(raw & (1 << i))

        result.set_status(TestStatus.PASS if raw == 0 else TestStatus.FAIL)
        self.__restore_settings()

    def cancel(self):
        self.__restore_settings()

def run_test(sensor: ThreespaceSensor):
    test = SelfTest(sensor)
    test.start()
    return test.overall_success, test.result_flat

def auto_run_test():
    sensor = ThreespaceSensor()
    overall_success, results = run_test(sensor)
    sensor.cleanup()
    for test in results:
        print(test)
    print("Overall success:", overall_success)
    return overall_success, results

if __name__ == "__main__":
    auto_run_test()