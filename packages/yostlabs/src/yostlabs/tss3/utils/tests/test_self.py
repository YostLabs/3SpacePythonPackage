# Generic self test
# The other tests should catch any errors this catches
# but this is still good to run in case any conditions
# were missed, and may give additional information in the
# case of failures.

from yostlabs.tss3.utils.tests.base import SensorTestBase
from yostlabs.tss3.api import ThreespaceSensor, InvalidKeyError
from yostlabs.tss3.consts import *
import time

class SelfTest(SensorTestBase):

    def __init__(self, sensor: ThreespaceSensor):
        super().__init__(sensor)

        self.settings_cache = {}
        # Initialize dict. Keys must be in order of the bits in the result.
        self.result = {
            "raw": 0,
            "accel": True,
            "gyro": True,
            "mag": True,
            "baro": True,
            "rtc": True,
            "gps": True,
            "bluetooth": True,
            "sd": True,
            "sms": True,
            "battery": True
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

        result = self.sensor.selfTest().data
        self.result["raw"] = result

        keys = list(self.result.keys())
        keys.remove("raw")
        for i, key in enumerate(keys):
            self.result[key] = not bool(result & (1 << i))
        self.overall_success = (result == 0)
        self.__restore_settings()

    def cancel(self):
        self.__restore_settings()

def run_test(sensor: ThreespaceSensor):
    test = SelfTest(sensor)
    test.start()
    return test.overall_success, test.result

def auto_run_test():
    sensor = ThreespaceSensor()
    overall_success, results = run_test(sensor)
    sensor.cleanup()
    print(results)
    print("Overall success:", overall_success)
    return overall_success, results

if __name__ == "__main__":
    auto_run_test()