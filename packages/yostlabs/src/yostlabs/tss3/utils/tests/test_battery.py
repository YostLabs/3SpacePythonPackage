from yostlabs.tss3.utils.tests.base import SensorTestBase
from yostlabs.tss3.api import ThreespaceSensor, InvalidKeyError

class BatteryTest(SensorTestBase):

    def __init__(self, sensor: ThreespaceSensor):
        super().__init__(sensor)
        self.result = {
            "charging_status": None,
            "battery_percentage": None
        }

    def start(self):
        try:
            self.result["battery_voltage"] = self.sensor.readBatteryVoltage()
            self.result["battery_percentage"] = self.sensor.readBatteryPercentage()
        except InvalidKeyError:
            self.overall_success = False
            self.result["battery_voltage"] = "N/A"
            self.result["battery_percentage"] = "N/A"

    def cancel(self):
        pass