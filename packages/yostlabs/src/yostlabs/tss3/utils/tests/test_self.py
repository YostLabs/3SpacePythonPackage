# Generic self test
# The other tests should catch any errors this catches
# but this is still good to run in case any conditions
# were missed, and may give additional information in the
# case of failures.

from yostlabs.tss3.utils.tests.base import SensorTestBase
from yostlabs.tss3.api import ThreespaceSensor

class SelfTest(SensorTestBase):

    def __init__(self, sensor: ThreespaceSensor):
        super().__init__(sensor)
        self.result = {
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
        self.result["self_test"] = self.sensor.runSelfTest()

    def cancel(self):
        pass