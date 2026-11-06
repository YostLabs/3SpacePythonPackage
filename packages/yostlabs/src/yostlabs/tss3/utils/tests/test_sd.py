import time

from yostlabs.tss3.utils.tests.base import SensorTest, SensorVariant, TestKind, Busy, Confirm, step
from yostlabs.tss3.utils.tests.cli import main
from yostlabs.tss3.utils.file_explorer import DirItemType
from yostlabs.tss3.utils.streaming import ThreespaceStreamingManager
from yostlabs.tss3.api import ThreespaceSensor, StreamableCommands
from yostlabs.tss3.consts import THREESPACE_DEBUG_LEVEL_INFO, THREESPACE_DEBUG_MODULE_SD, THREESPACE_FAMILY_DATA_LOGGER

# FatFs result codes returned in the response header status field
FR_OK               =   0   # Succeeded
FR_WRITE_PROTECTED  = -10   # The physical drive is write protected
FR_NOT_ENABLED      = -12   # The volume has no work area (SD not present / not mounted)


class SdTest(SensorTest):
    """
    Tests the SD card hardware on the sensor.

    1. Verify the SD card is present. If absent, the operator inserts it, or fails the step.
    2. Start a datalogging session. If the card is write protected (FR_WRITE_PROTECTED), the operator
       ejects it from the OS and retries, or gives up. Any other non-zero status fails the test.
    3. Logging stops by itself after LOG_STOP_COUNT samples, which should take EXPECTED_LOG_DURATION.
    4. Find the session's CSV file and open it.
    5. Its header must be the labels of LOG_COMMANDS.
    6. It must have LOG_STOP_COUNT lines of VALUES_PER_LINE values, with timestamps strictly increasing.
    7. Delete the session's folder with its files, leaving the card as it was. After a failure the test stops
       first, so they stay on the card to look at.
    """

    id = "sd"
    name = "SD Card"
    description = "Checks the SD card is detected and can be logged to."
    variants = (SensorVariant(THREESPACE_FAMILY_DATA_LOGGER),)
    required_hardware = frozenset({"sd_card"})
    kind = TestKind.FUNCTIONAL
    stop_on_failure = True

    EXPECTED_LOG_DURATION = 2.0  # seconds
    LOG_FINISH_MARGIN = 1.0      # seconds logging may take beyond EXPECTED_LOG_DURATION to stop by itself
    LOG_STOP_COUNT = 100         # Samples logged, one line each

    LOG_COMMANDS: list[StreamableCommands] = [StreamableCommands.GetTimestamp, StreamableCommands.GetUntaredOrientation]
    VALUES_PER_LINE = 5          # Of LOG_COMMANDS: the timestamp, and the orientation's 4

    def __init__(self, sensor: ThreespaceSensor, streaming_manager: ThreespaceStreamingManager = None):
        super().__init__(sensor, streaming_manager)
        self._logging: bool = False
        self._folder_output_location: str | None = None   # The session's folder, once logging started
        self._file_open: bool = False

    @step("SD card present")
    def sd_present(self):
        self.change_settings(header_status=1)   # The SD commands report their result in the header status
        while not self._is_sd_present():
            if (yield Busy("SD card not detected. Insert the SD card.", actions=("Fail",))) == "Fail":
                self.check().failed("Failed by the operator: the SD card was not detected.")
                return
        self.check().passed()

    @step("Start logging")
    def start_logging(self):
        # Command only, Count, Continuous, Session#, Ascii
        self.change_settings(log_start_event="2", log_stop_event="4", log_stop_count=self.LOG_STOP_COUNT,
                             log_slots=self.LOG_COMMANDS,
                             log_hz=self.LOG_STOP_COUNT / self.EXPECTED_LOG_DURATION, log_style=0,
                             log_base_filename="sd_test", log_folder_mode=0, log_data_mode=1, log_output_settings=1,
                             log_header_enabled=0)

        # Prepare to read info debug message to store folder output location
        self.read_debug_messages()
        self.change_settings(debug_mode=0,
                             debug_level=THREESPACE_DEBUG_LEVEL_INFO,
                             debug_module=THREESPACE_DEBUG_MODULE_SD)

        while True:
            status = self.sensor.startDataLogging().header.status
            self.check().add_measurement("status", status)
            if status == FR_OK:
                self._logging = True
                messages = self.read_debug_messages()
                # Newest first: each folder created on the way logs a path, and the session's is the last
                for message in reversed(messages):
                    if "Created path:" in message:
                        self._folder_output_location = message.split(":")[-1].strip()
                        self.check().add_measurement("folder_output_location", self._folder_output_location)
                        break
                if self._folder_output_location is None:
                    self.check().failed("Failed to determine folder output location.")
                    return
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

    @step("Stop logging")
    def stop_logging(self):
        yield from self.wait(self.EXPECTED_LOG_DURATION, "Logging to the SD card.")

        # Check to make sure logging automatically completed after the expected time. It started a little after the
        # wait did and counts samples, not time, so it may need a moment longer
        end = time.perf_counter() + self.LOG_FINISH_MARGIN
        while True:
            finished = not bool(self.sensor.getLoggingStatus().data)
            if finished or time.perf_counter() > end:
                break
            yield Busy("Waiting for logging to finish.")
        self.check().add_measurement("logging_finished", finished)

        if finished:
            self._logging = False
            self.check().passed()
        else:
            status = self.sensor.stopDataLogging().header.status
            self._logging = False
            self.check().add_measurement("stop_status", status)
            self.check().failed(f"Logging did not stop by itself after {self.LOG_STOP_COUNT} samples.")

    @step("Log file found")
    def file_found(self):
        status = self.sensor.changeDirectory(self._folder_output_location).header.status
        self.check().add_measurement("directory_status", status)
        if status != FR_OK:
            self.check().failed(f"Failed to enter {self._folder_output_location}: status {status}.")
            return

        log_file: str | None = None
        while log_file is None:
            item_type, name, _ = self.sensor.getNextDirectoryItem().data
            if item_type == DirItemType.END:
                self.check().failed("No CSV file found.")
                return
            if item_type == DirItemType.ERROR:
                self.check().failed("Failed to list the session's folder.")
                return
            if name.endswith(".csv"):
                log_file = name
        self.check().add_measurement("file_found", log_file)

        # Attempt to open the CSV file for reading
        status = self.sensor.openFile(log_file).header.status
        self.check().add_measurement("open_status", status)
        if status != FR_OK:
            self.check().failed(f"Failed to open file: status {status}.")
            return
        self._file_open = True
        self.check().passed()

    @step("Log file header")
    def file_header(self):
        header = self._read_line()
        labels = ','.join(self.sensor.getStreamingLabel(command.value).data for command in self.LOG_COMMANDS)
        self.check().add_measurement("actual_header", header)
        self.check().add_criteria("expected_header", labels)
        if header == labels:
            self.check().passed()
        else:
            self.check().failed("CSV header does not match expected labels.")

    @step("Log file data")
    def file_data(self):
        data_lines: list[str] = []
        while line := self._read_line():
            data_lines.append(line)
        self.sensor.closeFile()
        self._file_open = False

        self.check().add_measurement("data_lines_count", len(data_lines))
        self.check().add_criteria("expected_line_count", self.LOG_STOP_COUNT)
        if len(data_lines) != self.LOG_STOP_COUNT:
            self.check().failed(f"Unexpected number of data lines in CSV file. Expected {self.LOG_STOP_COUNT}, "
                                f"got {len(data_lines)}.")
            return

        # Ensure the timestamp is strictly increasing and correct number of readings per line
        previous_timestamp: int | None = None
        for i, line in enumerate(data_lines):
            values = line.split(',')
            if len(values) != self.VALUES_PER_LINE:
                self.check().failed(f"Unexpected number of values in line {i}. Expected {self.VALUES_PER_LINE}, "
                                    f"got {len(values)}.")
                self.check().add_measurement("line_index", i)
                self.check().add_measurement("line", line)
                return

            try:
                timestamp = int(values[0])
            except ValueError:
                self.check().failed(f"Line {i} has an invalid timestamp.")
                self.check().add_measurement("line_index", i)
                self.check().add_measurement("line", line)
                return
            if previous_timestamp is not None and timestamp <= previous_timestamp:
                self.check().failed("Timestamps are not strictly increasing.")
                self.check().add_measurement("previous_timestamp", previous_timestamp)
                self.check().add_measurement("current_timestamp", timestamp)
                self.check().add_measurement("line_index", i)
                return
            previous_timestamp = timestamp

        self.check().passed()

    @step("Log deleted")
    def delete_log(self):
        self.sensor.changeDirectory("/")   # Out of the folder being deleted
        status = self.sensor.deleteFile(self._folder_output_location).header.status   # A folder goes with its contents
        self.check().add_measurement("delete_status", status)
        if status == FR_OK:
            self.check().passed()
        else:
            self.check().failed(f"Failed to delete {self._folder_output_location}: status {status}.")

    def cleanup(self):
        if self._logging:   # Ended while logging, Ex: cancelled
            self.sensor.stopDataLogging()
        if self._file_open:
            self.sensor.closeFile()
        if self._folder_output_location is not None:   # Started logging, so it may be in the session's folder
            self.sensor.changeDirectory("/")
            self.read_debug_messages()   # Discard the messages produced by the test

    def _is_sd_present(self) -> bool:
        return self.sensor.getNextDirectoryItem().header.status != FR_NOT_ENABLED

    def _read_line(self) -> str:
        """The next line of the open file without its line ending, "" at its end"""
        return (self.sensor.fileReadLine().data or "").strip()

if __name__ == "__main__":
    main(SdTest)
