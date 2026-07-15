import enum
import time

from yostlabs.tss3.utils.tests.base import SensorTestBase
from yostlabs.tss3.api import ThreespaceSensor

# FatFs result codes returned in the response header status field
FR_OK               =   0   # Succeeded
FR_WRITE_PROTECTED  = -10   # The physical drive is write protected
FR_NOT_ENABLED      = -12   # The volume has no work area (SD not present / not mounted)


class SdTestState(enum.Enum):
    Inactive             = 0
    CheckingSdPresent    = 1
    AwaitingSdInsert     = 2
    StartingDataLogging  = 3
    AwaitingWriteUnlock  = 4
    Logging              = 5
    Finished             = 6


class SdTest(SensorTestBase):
    """
    Tests the SD card hardware on the sensor.

    Steps:
    1. Verify the SD card is present.
       If absent, prompt the user to insert it and poll until detected.
       Call fail_current_stage() to abort at any point.
    2. Start a datalogging session.
       - If header status is FR_WRITE_PROTECTED (-10): prompt user to eject the card
         from the OS, then call notify_write_lock_removed() to retry.
       - Any other non-zero status is recorded and the test fails.
    3. Allow logging to run for 2 seconds, then stop.
    """

    LOG_DURATION = 2.0  # seconds

    def __init__(self, sensor: ThreespaceSensor):
        super().__init__(sensor)
        self.state = SdTestState.Inactive
        self.result = {
            "sd_present": {
                "success": None,
            },
            "start_logging": {
                "success": None,
                "status": None,
            },
            "stop_logging": {
                "success": None,
                "status": None
            },
        }
        self.__settings_cache = {}
        self.__log_start_time: float | None = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start(self):
        if self.state != SdTestState.Inactive:
            raise Exception("SD test already started.")
        self.__settings_cache = self.sensor.read_settings("header_status")
        self.sensor.writeHeaderStatusEnabled(True)
        self.__go_next_state()

    def cancel(self):
        if self.state == SdTestState.Inactive:
            return
        self.state = SdTestState.Inactive
        self.__cleanup()

    def update(self):
        if self.state in (SdTestState.Inactive, SdTestState.Finished):
            return
        match self.state:
            case SdTestState.CheckingSdPresent:
                self.__update_checking_sd_present()
            case SdTestState.AwaitingSdInsert:
                self.__update_awaiting_sd_insert()
            case SdTestState.StartingDataLogging:
                self.__update_starting_data_logging()
            case SdTestState.AwaitingWriteUnlock:
                pass  # Waiting for notify_write_lock_removed()
            case SdTestState.Logging:
                self.__update_logging()

    def fail_current_stage(self):
        """
        Call at any time to immediately fail the current stage and finish the test.
        Useful when a user-interaction step (e.g. inserting the SD card) cannot be
        completed and the operator wants to abort gracefully.
        """
        if self.state in (SdTestState.Inactive, SdTestState.Finished):
            return
        match self.state:
            case SdTestState.AwaitingSdInsert:
                self.result["sd_present"]["success"] = False
            case SdTestState.StartingDataLogging | SdTestState.AwaitingWriteUnlock:
                self.result["start_logging"]["success"] = False
            case SdTestState.Logging:
                self.result["stop_logging"]["success"] = False
        self.overall_success = False
        self.state = SdTestState.Finished
        self.__cleanup()

    def notify_write_lock_removed(self):
        """
        Call after the user has ejected the SD card from the OS to clear its
        write-protection state.  The test will then retry starting the
        datalogging session.
        """
        if self.state != SdTestState.AwaitingWriteUnlock:
            return
        self.state = SdTestState.StartingDataLogging
        self.update()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def __is_sd_present(self) -> bool:
        result = self.sensor.getNextDirectoryItem()
        return result.header.status != FR_NOT_ENABLED

    # ------------------------------------------------------------------
    # Private state handlers
    # ------------------------------------------------------------------

    def __update_checking_sd_present(self):
        if self.__is_sd_present():
            self.result["sd_present"]["success"] = True
            self.__go_next_state()
        else:
            # SD not present — move to the user-interaction wait state
            self.state = SdTestState.AwaitingSdInsert

    def __update_awaiting_sd_insert(self):
        if self.__is_sd_present():
            self.result["sd_present"]["success"] = True
            self.__go_next_state()
        # Otherwise keep polling; caller should call fail_current_stage() to abort.

    def __update_starting_data_logging(self):
        result = self.sensor.startDataLogging()
        status = result.header.status

        if status == FR_OK:
            self.result["start_logging"]["success"] = True
            self.result["start_logging"]["status"] = status
            self.__log_start_time = time.perf_counter()
            self.__go_next_state()
        elif status == FR_WRITE_PROTECTED:
            # Card is write-protected — ask user to eject from OS and unlock it
            self.result["start_logging"]["status"] = status
            self.state = SdTestState.AwaitingWriteUnlock
        else:
            # Any other error is a hard failure
            self.result["start_logging"]["success"] = False
            self.result["start_logging"]["status"] = status
            self.overall_success = False
            self.state = SdTestState.Finished
            self.__cleanup()

    def __update_logging(self):
        if time.perf_counter() - self.__log_start_time >= self.LOG_DURATION:
            result = self.sensor.stopDataLogging()
            self.result["stop_logging"]["success"] = (result.header.status == 0)
            self.result["stop_logging"]["status"] = result.header.status
            self.__go_next_state()

    # ------------------------------------------------------------------
    # Cleanup & state transitions
    # ------------------------------------------------------------------

    def __cleanup(self):
        if self.__settings_cache:
            self.sensor.write_settings(**self.__settings_cache)

    def __go_next_state(self):
        match self.state:
            case SdTestState.Inactive:
                self.state = SdTestState.CheckingSdPresent
            case SdTestState.CheckingSdPresent:
                self.state = SdTestState.StartingDataLogging
            case SdTestState.AwaitingSdInsert:
                self.state = SdTestState.StartingDataLogging
            case SdTestState.StartingDataLogging:
                self.state = SdTestState.Logging
            case SdTestState.Logging:
                self.state = SdTestState.Finished
                self.__cleanup()
            case _:
                raise Exception(f"Invalid state for going to next state: {self.state}")

        self.update()


def run_test(sensor: ThreespaceSensor):
    test = SdTest(sensor)
    last_state = test.state
    test.start()
    try:
        while test.state != SdTestState.Finished:
            if test.state != last_state:
                if test.state == SdTestState.AwaitingSdInsert:
                    print("SD card not detected. Please insert the SD card.")
                elif test.state == SdTestState.AwaitingWriteUnlock:
                    print("SD card is write-protected. Please eject the card from the OS to "
                          "clear write protection, then call test.notify_write_lock_removed().")
                last_state = test.state

            test.update()
    except KeyboardInterrupt:
        test.cancel()
        print("\nTest cancelled by user.")
        return (False if not test.overall_success else None), test.result

    return test.overall_success, test.result


def auto_run_test():
    sensor = ThreespaceSensor()
    overall_success, results = run_test(sensor)
    sensor.cleanup()
    print(results)
    print(f"Overall success: {overall_success}")
    return overall_success, results


if __name__ == "__main__":
    auto_run_test()