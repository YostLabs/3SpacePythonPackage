# Generic self test
# The other tests should catch any errors this catches
# but this is still good to run in case any conditions
# were missed, and may give additional information in the
# case of failures.

from yostlabs.tss3.utils.tests.base import SensorTestBase
from yostlabs.tss3.api import ThreespaceSensor
from yostlabs.tss3.consts import *

class SelfTest(SensorTestBase):

    def __init__(self, sensor: ThreespaceSensor):
        super().__init__(sensor)

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

    def start(self):
        result = self.sensor.selfTest().data
        self.result["raw"] = result

        keys = list(self.result.keys())
        keys.remove("raw")
        for i, key in enumerate(keys):
            self.result[key] = not bool(result & (1 << i))
        self.overall_success = (result == 0)

    def cancel(self):
        pass

def run_test():
    sensor = ThreespaceSensor()
    test = SelfTest(sensor)
    test.start()
    print(test.result)
    print("Overall success:", test.overall_success)

if __name__ == "__main__":
    run_test()