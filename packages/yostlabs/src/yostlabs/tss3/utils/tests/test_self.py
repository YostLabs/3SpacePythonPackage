# Generic self test
# The other tests should catch any errors this catches
# but this is still good to run in case any conditions
# were missed, and may give additional information in the
# case of failures.

from yostlabs.tss3.utils.tests.base import SensorTest, ALL_SENSORS, TestStatus, step
from yostlabs.tss3.utils.tests.cli import main

class SelfTest(SensorTest):

    id = "self"
    name = "Self Test"
    variants = ALL_SENSORS

    # Keys must be in order of the bits in the self test result bitfield.
    BIT_KEYS = ["accel", "gyro", "mag", "baro", "rtc", "gps", "bluetooth", "sd", "sms", "battery"]

    @step("Self test")
    def bitfield(self):
        # GPS self test relies on checking messages are being retrieved. This setting specifically has a bug on
        # some current devices where the self test is not properly handling it. Temporarily disabling it here
        # to fix the issue until a firmware update is released.
        if self.sensor.has_setting("gps_periodic_enabled") \
                and self.sensor.read_settings("gps_periodic_enabled")["gps_periodic_enabled"]:
            self.change_settings(gps_periodic_enabled=0)
            yield from self.wait(1.0, "Waiting for the GPS to settle.")

        raw = self.sensor.selfTest().data
        result = self.check()
        result.add_measurement("raw", raw)
        for i, key in enumerate(self.BIT_KEYS):
            result.add_measurement(key, not bool(raw & (1 << i)))
        result.set_status(TestStatus.PASS if raw == 0 else TestStatus.FAIL)

if __name__ == "__main__":
    main(SelfTest)
