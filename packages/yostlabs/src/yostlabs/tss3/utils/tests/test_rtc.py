import time
import datetime

from yostlabs.tss3.utils.tests.base import SensorTest, SensorVariant, TestKind, TestStatus, Busy, step
from yostlabs.tss3.consts import THREESPACE_FAMILY_DATA_LOGGER
from yostlabs.tss3.utils.tests.cli import main
from yostlabs.tss3.api import ResponseTimeoutError
from yostlabs.tss3.errors import SettingError

import logging
logger = logging.getLogger(__name__)


class RTCTest(SensorTest):
    """
    Tests the RTC (Real-Time Clock) functionality of the sensor.

    1. Verify the sensor reports an RTC component via readValidComponents().
    2. If rtc_source is available, set it to 3 (RTC). Fail if writing errors.
    3. Set utc_offset to 0 if available, and the sensor date/time to the current UTC time.
       Wait 1 second and verify the sensor time advanced by ~1 second.
    4. The operator unplugs the sensor, which turns off, so only the coin cell keeps the clock
       running, and plugs it back in. A hard reset can't do this: on some boards the RTC is powered
       from USB, before the charger. Verify the sensor did turn off, and that its datetime increased
       by approximately the time it was unplugged. While waiting, the operator can fail the step if
       the app never notices.
    """

    id = "rtc"
    name = "Clock"
    description = "Checks the real-time clock keeps time, including while the sensor has no power."
    variants = (SensorVariant(THREESPACE_FAMILY_DATA_LOGGER),)
    required_hardware = frozenset({"rtc"})
    kind = TestKind.FUNCTIONAL
    stop_on_failure = True

    TIME_CHANGE_TEST_DURATION = 1.0  # Seconds
    POWER_OFF_TIME = 1.0             # Seconds. Long enough for the board's capacitors not to keep the clock running
    POWER_LOSS_TOLERANCE_S = 2.0     # The sensor's time is in whole seconds, read twice
    RTC_SOURCE = 3                   # Of the rtc_source setting

    @step("RTC present")
    def valid_components(self):
        components = self.sensor.readValidComponents()
        self.check().add_measurement("components", components)
        if any(c.strip().startswith("RTC") for c in components.split(',')):
            self.check().passed()
        else:
            self.check().failed("Sensor does not report an RTC component.")

    @step("Time source")
    def rtc_source(self):
        if not self.sensor.has_setting("rtc_source"):
            self.check().set_status(TestStatus.NA)
            return
        try:
            self.change_settings(rtc_source=self.RTC_SOURCE)
        except SettingError as e:
            self.check().failed(f"Failed to write rtc_source: {e}")
            return
        self.check().passed()

    @step("Clock running")
    def time_change(self):
        if self.sensor.has_setting("utc_offset"):
            self.change_settings(utc_offset=0)
        now = datetime.datetime.now(datetime.timezone.utc)
        self.sensor.setDateTime(now.year, now.month, now.day, now.hour, now.minute, now.second)
        start = self.sensor.getDateTime().data
        wait_start = time.perf_counter()
        yield from self.wait(self.TIME_CHANGE_TEST_DURATION, "Letting the clock run.")

        end = self.sensor.getDateTime().data
        elapsed = time.perf_counter() - wait_start
        delta = abs(_datetime_to_seconds(end) - _datetime_to_seconds(start))

        result = self.check()
        result.add_measurement("start_time", start)
        result.add_measurement("end_time", end)
        result.add_measurement("delta_s", delta)
        result.add_criteria("expected_change_s", elapsed)
        if self.TIME_CHANGE_TEST_DURATION <= delta <= elapsed + 1:   # Whole seconds, so allow 1 s of rounding
            result.passed()
        else:
            result.failed(f"Sensor time changed by {delta:.2f}s, expected ~{elapsed:.2f}s.")

    @step("Survives power loss")
    def power_loss(self):
        result = self.check()
        if "coin_cell" in self.not_installed:
            result.skipped("Not installed yet: coin cell")
            return
        if self.sensor.has_setting("power_hold_state"):
            self.change_settings(power_hold_state=0)   # Turn off once unplugged, or the battery may power the clock
        before = self.sensor.getDateTime().data
        uptime_before = self.sensor.getTimestamp().data
        start = time.perf_counter()

        while True:
            try:
                self.sensor.getDateTime()
            except (OSError, ResponseTimeoutError):
                break
            if (yield Busy("Unplug the sensor from the USB port.", actions=("Fail",))) == "Fail":
                result.failed("Failed by the operator: the sensor being unplugged was not detected.")
                return
        yield from self.wait(self.POWER_OFF_TIME, "Leave the sensor unplugged.")
        while not self.sensor.attempt_reconnect(timeout=0):
            if (yield Busy("Plug the sensor back into the USB port.", actions=("Fail",))) == "Fail":
                result.failed("Failed by the operator: the sensor being plugged back in was not detected.")
                self.stop_session = True   # The sensor is gone, so no later test can run
                return

        # Turning off lost these, which reading the time relies on
        if self.sensor.has_setting("rtc_source"):
            self.change_settings(rtc_source=self.RTC_SOURCE)
        if self.sensor.has_setting("utc_offset"):
            self.change_settings(utc_offset=0)

        after = self.sensor.getDateTime().data
        uptime_after = self.sensor.getTimestamp().data
        elapsed = time.perf_counter() - start
        time_diff = _datetime_to_seconds(after) - _datetime_to_seconds(before)

        result.add_measurement("datetime_before", before)
        result.add_measurement("datetime_after", after)
        result.add_measurement("time_diff_s", time_diff)
        result.add_measurement("uptime_before", uptime_before)
        result.add_measurement("uptime_after", uptime_after)
        result.add_criteria("expected_elapsed_s", elapsed)
        result.add_criteria("time_tolerance_s", self.POWER_LOSS_TOLERANCE_S)
        if uptime_after >= uptime_before:
            result.failed("Sensor stayed on while unplugged, so the coin cell wasn't tested.")
        elif abs(time_diff - elapsed) > self.POWER_LOSS_TOLERANCE_S:
            result.failed(f"Sensor time changed by {time_diff:.2f}s while it had no power, expected ~{elapsed:.2f}s.")
        else:
            result.passed()


def _datetime_to_seconds(dt: list[int]) -> float:
    """Convert a [year, month, day, hour, minute, second] list to a POSIX timestamp."""
    year, month, day, hour, minute, second = dt
    return datetime.datetime(year, month, day, hour, minute, second, tzinfo=datetime.timezone.utc).timestamp()

if __name__ == "__main__":
    main(RTCTest)
