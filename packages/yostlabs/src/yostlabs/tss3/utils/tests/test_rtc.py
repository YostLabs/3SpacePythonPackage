import enum
import time
import datetime

from yostlabs.tss3.utils.tests.base import SensorTestBase, TestResult, TestStatus
from yostlabs.tss3.api import ThreespaceSensor, InvalidKeyError

import logging
logger = logging.getLogger(__name__)


class RTCTestState(enum.Enum):
    Inactive = 0
    CheckingComponents = 1
    SettingRtcSource = 2
    SettingTime = 3
    VerifyingTimeChange = 4
    PerformingReset = 5
    AwaitingPowerCycle = 6
    AwaitingPowerCycleReconnect = 7
    CheckingTimeAfterReset = 8
    Finished = 9


class RTCTest(SensorTestBase):
    """
    Tests the RTC (Real-Time Clock) functionality of the sensor.

    Steps:
    1. Verify the sensor reports an RTC component via readValidComponents().
    2. If rtc_source is available, cache it and set it to 3 (RTC). Fail if writing errors.
    3. If utc_offset is available, cache it and set it to 0.
    4. Set the sensor date/time to the current UTC time.
    5. Wait 1 second and verify the sensor time advanced by ~1 second.
    6. Record the current time then perform a hard reset (timeout=5).
       If hard reset is unsupported (InvalidKeyError), the user is prompted to
       power-cycle the sensor. The test waits for the sensor to disconnect and
       reconnect.
    7. After reconnect, verify the sensor datetime increased by approximately
       the elapsed reset duration.

    Any failure short-circuits all remaining steps.
    """

    TIME_CHANGE_TEST_DURATION = 1.0  # Seconds

    def __init__(self, sensor: ThreespaceSensor):
        super().__init__(sensor)
        self.state = RTCTestState.Inactive

        self._settings_cache: dict = {}

        self.result: dict[str, TestResult] = {
            "valid_components": TestResult("rtc", "valid_components"),
            "rtc_source": TestResult("rtc", "rtc_source"),
            "time_change": TestResult("rtc", "time_change"),
            "reset": TestResult("rtc", "reset"),
        }

        self._time_before_wait: list[int] | None = None
        self._wait_start: float | None = None
        self._pre_reset_datetime: list[int] | None = None
        self._reset_start_time: float | None = None

    def start(self):
        if self.state != RTCTestState.Inactive:
            raise Exception("RTC test already started.")
        self.__go_next_state()

    def cancel(self):
        if self.state == RTCTestState.Inactive:
            return
        self.state = RTCTestState.Inactive
        self.__cleanup()

    def update(self):
        if self.state in (RTCTestState.Inactive, RTCTestState.Finished):
            return
        match self.state:
            case RTCTestState.CheckingComponents:
                self.__update_checking_components()
            case RTCTestState.SettingRtcSource:
                self.__update_setting_rtc_source()
            case RTCTestState.SettingTime:
                self.__update_setting_time()
            case RTCTestState.VerifyingTimeChange:
                self.__update_verifying_time_change()
            case RTCTestState.PerformingReset:
                self.__update_performing_reset()
            case RTCTestState.AwaitingPowerCycle:
                self.__update_awaiting_power_cycle()
            case RTCTestState.AwaitingPowerCycleReconnect:
                self.__update_awaiting_power_cycle_reconnect()
            case RTCTestState.CheckingTimeAfterReset:
                self.__update_checking_time_after_reset()

    # ------------------------------------------------------------------
    # Private state handlers
    # ------------------------------------------------------------------

    def __update_checking_components(self):
        components = self.sensor.readValidComponents()
        result = self.result["valid_components"]
        result.measurements["components"] = components
        component_list = [c.strip() for c in components.split(',')]
        has_rtc = any(c.startswith("RTC") for c in component_list)
        if not has_rtc:
            self.__fail(result, "Sensor does not report an RTC component.")
        else:
            result.set_status(TestStatus.PASS)
            self.__go_next_state()

    def __update_setting_rtc_source(self):
        result = self.result["rtc_source"]
        if not self.sensor.has_setting("rtc_source"):
            result.set_status(TestStatus.NA)
            self.__go_next_state()
            return

        self._settings_cache["rtc_source"] = self.sensor.readRtcSource()
        err = self.sensor.writeRtcSource(3)
        if err != 0:
            self.__fail(result, f"Failed to write rtc_source: error {err}.")
            return

        result.set_status(TestStatus.PASS)
        self.__go_next_state()

    def __update_setting_time(self):
        if self.sensor.has_setting("utc_offset"):
            self._settings_cache["utc_offset"] = self.sensor.readUtcOffset()
            self.sensor.writeUtcOffset(0)
        
        now = datetime.datetime.now(datetime.timezone.utc)
        self.sensor.setDateTime(now.year, now.month, now.day,
                                now.hour, now.minute, now.second)
        self._time_before_wait = self.sensor.getDateTime().data
        self.result["time_change"].measurements["start_time"] = self._time_before_wait
        self._wait_start = time.perf_counter()
        self.__go_next_state()

    def __update_verifying_time_change(self):
        if time.perf_counter() - self._wait_start < RTCTest.TIME_CHANGE_TEST_DURATION:
            return

        after_time = self.sensor.getDateTime().data
        elapsed_seconds = time.perf_counter() - self._wait_start
        before_total = self.__datetime_to_seconds(self._time_before_wait)
        after_total = self.__datetime_to_seconds(after_time)
        delta = abs(after_total - before_total)

        result = self.result["time_change"]
        result.measurements["end_time"] = after_time
        result.measurements["delta_s"] = delta
        result.add_criteria("expected_change_s", elapsed_seconds)
        success = RTCTest.TIME_CHANGE_TEST_DURATION <= delta <= (elapsed_seconds + 1)  # Allow some tolerance based on actual elapsed time
        if not success:
            self.__fail(result, f"Sensor time changed by {delta:.2f}s, expected ~{elapsed_seconds:.2f}s.")
        else:
            result.set_status(TestStatus.PASS)
            self.__go_next_state()

    def __update_performing_reset(self):
        self._pre_reset_datetime = self.sensor.getDateTime().data
        self._reset_start_time = time.perf_counter()

        try:
            self.sensor.hardReset(timeout=5)
        except InvalidKeyError:
            self.result["reset"].measurements["method"] = "power_cycle"
            self.state = RTCTestState.AwaitingPowerCycle
            self.update()
            return
        except Exception as e:
            logger.exception("Exception occurred performing RTC reset.")
            self.__fail(self.result["reset"], str(e))
            return

        self.result["reset"].measurements["method"] = "hard_reset"
        self.__go_next_state()

    def __update_awaiting_power_cycle(self):
        # Poll until the sensor disconnects (user unplugs it)
        try:
            self.sensor.getDateTime()
        except OSError:
            self.state = RTCTestState.AwaitingPowerCycleReconnect
            self.update()

    def __update_awaiting_power_cycle_reconnect(self):
        success = self.sensor.attempt_reconnect(timeout=0)
        if success:
            self.__go_next_state()

    def __update_checking_time_after_reset(self):
        elapsed = time.perf_counter() - self._reset_start_time
        after_datetime = self.sensor.getDateTime().data

        pre_total = self.__datetime_to_seconds(self._pre_reset_datetime)
        after_total = self.__datetime_to_seconds(after_datetime)
        time_diff = after_total - pre_total

        result = self.result["reset"]
        result.measurements["pre_reset_datetime"] = self._pre_reset_datetime
        result.measurements["post_reset_datetime"] = after_datetime
        result.measurements["time_diff_s"] = time_diff
        result.add_criteria("expected_elapsed_s", elapsed)

        success = (0 <= time_diff <= (elapsed + 1))
        if not success:
            self.__fail(result, f"Sensor time after reset differed by {time_diff:.2f}s, expected ~{elapsed:.2f}s.")
            return
        
        result.set_status(TestStatus.PASS)
        self.__go_next_state()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def __fail(self, result: TestResult, message: str | None = None):
        result.failed(message)
        self.state = RTCTestState.Finished
        self.__cleanup()

    def __datetime_to_seconds(self, dt: list[int]) -> float:
        """Convert a [year, month, day, hour, minute, second] list to a POSIX timestamp."""
        year, month, day, hour, minute, second = dt
        return datetime.datetime(year, month, day, hour, minute, second,
                                 tzinfo=datetime.timezone.utc).timestamp()

    def __go_next_state(self):
        match self.state:
            case RTCTestState.Inactive:
                self.state = RTCTestState.CheckingComponents
            case RTCTestState.CheckingComponents:
                self.state = RTCTestState.SettingRtcSource
            case RTCTestState.SettingRtcSource:
                self.state = RTCTestState.SettingTime
            case RTCTestState.SettingTime:
                self.state = RTCTestState.VerifyingTimeChange
            case RTCTestState.VerifyingTimeChange:
                self.state = RTCTestState.PerformingReset
            case RTCTestState.PerformingReset:
                self.state = RTCTestState.CheckingTimeAfterReset
            case RTCTestState.AwaitingPowerCycleReconnect:
                self.state = RTCTestState.CheckingTimeAfterReset
            case RTCTestState.CheckingTimeAfterReset:
                self.state = RTCTestState.Finished
                self.__cleanup()
            case _:
                raise Exception(f"Invalid state for __go_next_state: {self.state}")

        self.update()

    def __cleanup(self):
        if self._settings_cache:
            try:
                self.sensor.write_settings(**self._settings_cache)
            except Exception:
                pass


def run_test(sensor: ThreespaceSensor):
    test = RTCTest(sensor)
    test.start()

    last_state = test.state
    try:
        while test.state != RTCTestState.Finished:
            if test.state != last_state:
                if test.state == RTCTestState.AwaitingPowerCycle:
                    print("Hard reset is not supported on this sensor.")
                    print("Please disconnect the sensor and plug it back in (power cycle it).")
                elif test.state == RTCTestState.AwaitingPowerCycleReconnect:
                    print("Sensor disconnected. Please reconnect the sensor to continue the test.")
                last_state = test.state

            test.update()
            time.sleep(0.05)
    except KeyboardInterrupt:
        test.cancel()
        print("\nTest cancelled by user.")
        return (False if not test.overall_success else None), test.result_flat

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