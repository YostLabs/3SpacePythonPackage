# Generic self test
# The other tests should catch any errors this catches
# but this is still good to run in case any conditions
# were missed, and may give additional information in the
# case of failures.

from yostlabs.tss3.utils.tests.base import SensorTest, ALL_SENSORS, TestKind, step
from yostlabs.tss3.utils.tests.cli import main
from yostlabs.tss3.consts import THREESPACE_DEBUG_LEVEL_WRN, THREESPACE_DEBUG_LEVEL_ERR

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
    # Bits that may fail without a part, by the part's name in not_installed: their failure skips the test instead (Ex:
    # without a coin cell, the rtc bit fails off USB on some versions)
    BIT_PARTS = {"rtc": "coin_cell", "sd": "sd_card", "battery": "battery"}

    @step("Self test")
    def bitfield(self):
        # GPS self test relies on checking messages are being retrieved. This setting specifically has a bug on
        # some current devices where the self test is not properly handling it. Temporarily disabling it here
        # to fix the issue until a firmware update is released.
        if self.sensor.has_setting("gps_periodic_enabled") \
                and self.sensor.read_settings("gps_periodic_enabled")["gps_periodic_enabled"]:
            self.change_settings(gps_periodic_enabled=0)
            yield from self.wait(1.0, "Waiting for the GPS to settle.")

        self.change_settings(debug_mode=0,
                             debug_level=(THREESPACE_DEBUG_LEVEL_WRN | THREESPACE_DEBUG_LEVEL_ERR),
                             debug_module=0xFFFFFFFF)
        self.read_debug_messages()
        raw = self.sensor.selfTest().data
        result = self.check()
        messages = self.read_debug_messages()
        result.add_measurement("raw", raw)
        result.add_measurement("messages", messages)
        failed = raw
        ignored = []   # Failed bits of parts not installed yet
        for i, key in enumerate(self.BIT_KEYS):
            result.add_measurement(key, not bool(raw & (1 << i)))
            if raw & (1 << i) and self.BIT_PARTS.get(key) in self.not_installed:
                failed &= ~(1 << i)
                ignored.append(key)
        if failed:
            result.failed()
        elif ignored:
            result.skipped(f"Failed, but not installed yet: {', '.join(ignored)}")
        else:
            result.passed()

if __name__ == "__main__":
    main(SelfTest)
