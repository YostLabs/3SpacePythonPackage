import time

from yostlabs.tss3.utils.tests.base import SensorTest, Busy, Confirm, step
from yostlabs.tss3.utils.tests.cli import run_cli, main
from yostlabs.tss3.api import ThreespaceSensor, StreamableCommands

# FatFs result codes returned in the response header status field
FR_OK               =   0   # Succeeded
FR_WRITE_PROTECTED  = -10   # The physical drive is write protected
FR_NOT_ENABLED      = -12   # The volume has no work area (SD not present / not mounted)


class SdTest(SensorTest):
    """
    Tests the SD card hardware on the sensor. Any failure stops the test.

    1. Verify the SD card is present. If absent, the operator inserts it.
    2. Start a datalogging session. If the card is write protected (FR_WRITE_PROTECTED), the operator
       ejects it from the OS and retries, or gives up. Any other non-zero status fails the test.
    3. Allow logging to run for 2 seconds, then stop.
    """

    id = "sd"
    name = "SD Card"
    stop_on_failure = True

    EXPECTED_LOG_DURATION = 2.0  # seconds

    def __init__(self, sensor: ThreespaceSensor, streaming_manager=None):
        super().__init__(sensor, streaming_manager)
        self._logging = False

    @step("Check for an SD card")
    def sd_present(self):
        self.change_settings(header_status=1)   # The SD commands report their result in the header status
        while not self._is_sd_present():
            yield Busy("SD card not detected. Insert the SD card.")
        self.check().passed()

    @step("Start logging")
    def start_logging(self):
        # Command only, Count, Continuous, Session#, Ascii
        self.change_settings(log_start_event=2, log_stop_event=4, log_stop_count=int(50 * self.EXPECTED_LOG_DURATION),
                             log_slots=[StreamableCommands.GetTimestamp, StreamableCommands.GetUntaredOrientation],
                             log_rate=100, log_style=0, log_base_filename="sd_test",
                             log_folder_mode=0, log_data_mode=1, log_output_settings=1)
        while True:
            status = self.sensor.startDataLogging().header.status
            self.check().add_measurement("status", status)
            if status == FR_OK:
                self._logging = True
                self.check().passed()
                return
            if status != FR_WRITE_PROTECTED:
                self.check().failed(f"Failed to start logging: status {status}.")
                return
            retry = yield Confirm("The SD card is write protected. Eject it from the computer to clear "
                                  "the write protection, then retry. Retry?")
            if not retry:
                self.check().failed("SD card is write protected.")
                return

    @step("Log for 2 seconds")
    def stop_logging(self):
        yield from self.wait(self.EXPECTED_LOG_DURATION, "Logging to the SD card.")
        self.sensor.getLoggingStatus()
        status = self.sensor.stopDataLogging().header.status
        self._logging = False
        self.check().add_measurement("status", status)
        if status == FR_OK:
            self.check().passed()
        else:
            self.check().failed(f"Failed to stop logging: status {status}.")

    def cleanup(self):
        if self._logging:   # Ended while logging, Ex: cancelled
            self.sensor.stopDataLogging()

    def _is_sd_present(self) -> bool:
        return self.sensor.getNextDirectoryItem().header.status != FR_NOT_ENABLED


def run_test(sensor: ThreespaceSensor):
    test = run_cli(SdTest(sensor))
    return test.overall_success, test.result_flat

if __name__ == "__main__":
    main(SdTest)
