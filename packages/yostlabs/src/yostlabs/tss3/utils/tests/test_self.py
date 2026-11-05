# Generic self test
# The other tests should catch any errors this catches
# but this is still good to run in case any conditions
# were missed, and may give additional information in the
# case of failures.

from yostlabs.tss3.utils.tests.base import SensorTest, ALL_SENSORS, TestKind, TestStatus, step
from yostlabs.tss3.utils.tests.cli import main

class SelfTest(SensorTest):
    """
    Performs the generic on-board self test of the sensor.
    """


    id = "self"
    name = "Self Test"
    description = "Runs the sensor's built-in self test."
    variants = ALL_SENSORS
    kind = TestKind.FUNCTIONAL

    # Keys must be in order of the bits in the self test result bitfield.
    BIT_KEYS = ["accel", "gyro", "mag", "baro", "rtc", "gps", "bluetooth", "sd", "sms", "battery"]
    # Bits that fail without a part, by the part's name in not_installed. No coin cell doesn't fail the rtc bit over USB
    BIT_PARTS = {"sd": "sd_card", "battery": "battery"}

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
        failed = raw
        ignored = []
        for i, key in enumerate(self.BIT_KEYS):
            result.add_measurement(key, not bool(raw & (1 << i)))
            if self.BIT_PARTS.get(key) in self.not_installed:
                failed &= ~(1 << i)
                ignored.append(key)
        if ignored:
            result.message = f"Not installed yet, so not checked: {', '.join(ignored)}"
        result.set_status(TestStatus.PASS if failed == 0 else TestStatus.FAIL)

if __name__ == "__main__":
    main(SelfTest)
