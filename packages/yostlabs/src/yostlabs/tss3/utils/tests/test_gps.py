from yostlabs.tss3.utils.tests.base import SensorTest, SensorVariant, Busy, step
from yostlabs.tss3.utils.tests.cli import run_cli, main
from yostlabs.tss3.api import ThreespaceSensor
from yostlabs.tss3.consts import *

import time

import logging
logger = logging.getLogger(__name__)


class GPSTest(SensorTest):
    """
    Reads the GPS output through the sensor's debug messages.
    1. With the GPS active, a position message must arrive within the expected interval.
    2. With the GPS in standby, none may arrive for the same interval.
    """

    id = "gps"
    name = "GPS"
    variants = (SensorVariant(THREESPACE_FAMILY_DATA_LOGGER),)
    stop_on_failure = True

    EXPECTED_MESSAGE_INTERVAL = 1.0 #Seconds
    MESSAGE_PADDING = 0.5 #Seconds

    @step("GPS active")
    def active(self):
        # Show the GPS output as debug info messages
        self.change_settings(debug_mode=0,
                             debug_level=THREESPACE_DEBUG_LEVEL_INFO,
                             debug_module=THREESPACE_DEBUG_MODULE_GPS)
        self.change_settings(gps_standby=0)
        self.read_debug_messages()   # Discard old messages

        start_time = time.perf_counter()
        while True:
            for message in self.read_debug_messages():
                if self._is_position_message(message):
                    self.check().add_measurement("message", message).passed()
                    return
            elapsed_time = time.perf_counter() - start_time
            if elapsed_time > self.EXPECTED_MESSAGE_INTERVAL + self.MESSAGE_PADDING:
                logger.warning("GPS test timed out waiting for a GPS message.")
                self.check().failed(f"Timed out waiting for GPS message: {elapsed_time:.2f}s elapsed.")
                return
            yield Busy("Waiting for a GPS message.")

    @step("GPS standby")
    def standby(self):
        self.change_settings(gps_standby=1)
        self.read_debug_messages()   # Discard messages from before standby

        start_time = time.perf_counter()
        while True:
            for message in self.read_debug_messages():
                if self._is_position_message(message):
                    logger.warning("GPS test received a message while in standby.")
                    self.check().add_measurement("message", message).failed(message)
                    return
            # Success is going the whole interval without receiving a GPS message
            if time.perf_counter() - start_time > self.EXPECTED_MESSAGE_INTERVAL + self.MESSAGE_PADDING:
                self.check().passed()
                return
            yield Busy("Checking the GPS stays quiet in standby.")

    def cleanup(self):
        self.read_debug_messages()   # Discard the messages produced by the test

    @staticmethod
    def _is_position_message(message: str) -> bool:
        return "$GPGGA" in message or "$GNGGA" in message


def run_test(sensor: ThreespaceSensor):
    test = run_cli(GPSTest(sensor))
    return test.overall_success, test.result_flat

if __name__ == "__main__":
    main(GPSTest)
