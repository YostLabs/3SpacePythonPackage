import time
import datetime

from yostlabs.tss3.utils.tests.base import SensorTest, SensorVariant, TestStatus, Busy, step
from yostlabs.tss3.consts import THREESPACE_FAMILY_DATA_LOGGER
from yostlabs.tss3.utils.tests.cli import main
from yostlabs.tss3.api import InvalidKeyError, ResponseTimeoutError
from yostlabs.tss3.errors import SettingError

import logging
logger = logging.getLogger(__name__)


class RTCTest(SensorTest):
    """
    Tests the RTC (Real-Time Clock) functionality of the sensor. Any failure stops the test.

    1. Verify the sensor reports an RTC component via readValidComponents().
    2. If rtc_source is available, set it to 3 (RTC). Fail if writing errors.
    3. Set utc_offset to 0 if available, and the sensor date/time to the current UTC time.
       Wait 1 second and verify the sensor time advanced by ~1 second.
    4. Hard reset the sensor. If hard reset is unsupported (InvalidKeyError), the operator
       power cycles the sensor instead. Verify the sensor datetime increased by approximately
       the elapsed reset duration.
    """

    id = "rtc"
    name = "Clock"
    variants = (SensorVariant(THREESPACE_FAMILY_DATA_LOGGER),)
    stop_on_failure = True

    TIME_CHANGE_TEST_DURATION = 1.0  # Seconds

    @step("Check for an RTC")
    def valid_components(self):
        components = self.sensor.readValidComponents()
        self.check().add_measurement("components", components)
        if any(c.strip().startswith("RTC") for c in components.split(',')):
            self.check().passed()
        else:
            self.check().failed("Sensor does not report an RTC component.")

    @step("Use the RTC as the time source")
    def rtc_source(self):
        if not self.sensor.has_setting("rtc_source"):
            self.check().set_status(TestStatus.NA)
            return
        try:
            self.change_settings(rtc_source=3)
        except SettingError as e:
            self.check().failed(f"Failed to write rtc_source: {e}")
            return
        self.check().passed()

    @step("Check the clock advances")
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

    @step("Check the clock keeps time through a reset")
    def reset(self):
        result = self.check()
        before = self.sensor.getDateTime().data
        reset_start = time.perf_counter()

        try:
            self.sensor.hardReset(timeout=5)
            result.add_measurement("method", "hard_reset")
        except InvalidKeyError:
            result.add_measurement("method", "power_cycle")
            yield from self._power_cycle()

        elapsed = time.perf_counter() - reset_start
        after = self.sensor.getDateTime().data
        time_diff = _datetime_to_seconds(after) - _datetime_to_seconds(before)

        result.add_measurement("pre_reset_datetime", before)
        result.add_measurement("post_reset_datetime", after)
        result.add_measurement("time_diff_s", time_diff)
        result.add_criteria("expected_elapsed_s", elapsed)
        if 0 <= time_diff <= elapsed + 1:
            result.passed()
        else:
            result.failed(f"Sensor time after reset differed by {time_diff:.2f}s, expected ~{elapsed:.2f}s.")

    def _power_cycle(self):
        """For sensors without hard reset: the operator unplugs the sensor and plugs it back in."""
        while True:
            try:
                self.sensor.getDateTime()
            except (OSError, ResponseTimeoutError):
                break
            yield Busy("Hard reset is not supported on this sensor. Unplug the sensor to power cycle it.")
        while not self.sensor.attempt_reconnect(timeout=0):
            yield Busy("Plug the sensor back in.")


def _datetime_to_seconds(dt: list[int]) -> float:
    """Convert a [year, month, day, hour, minute, second] list to a POSIX timestamp."""
    year, month, day, hour, minute, second = dt
    return datetime.datetime(year, month, day, hour, minute, second, tzinfo=datetime.timezone.utc).timestamp()

if __name__ == "__main__":
    main(RTCTest)
