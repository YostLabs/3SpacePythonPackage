from yostlabs.tss3.utils.tests.base import SensorTest, SensorVariant, Busy, step
from yostlabs.tss3.utils.tests.cli import main
from yostlabs.tss3.utils.streaming import ThreespaceStreamingManager
from yostlabs.tss3.api import ThreespaceSensor, ResponseTimeoutError
from yostlabs.tss3.consts import *
import time

import logging
logger = logging.getLogger(__name__)


class BatteryTest(SensorTest):
    """
    Tests the battery functionality of the sensor.

    1. The self test reports no battery errors.
    2. The battery reports charging or charged.
    3. The operator unplugs the sensor, which stays powered on by its battery.
       If the sensor can't be told to stay on (no power_hold_state), the operator must make sure it does.
    4. The operator plugs it back in. The sensor's clock must have kept running for the time it was unplugged.
    """

    id = "battery"
    name = "Battery"
    description = "Checks the battery charges, and keeps the sensor running while it is unplugged."
    variants = (SensorVariant(THREESPACE_FAMILY_DATA_LOGGER),)
    stop_on_failure = True

    TIME_TOLERANCE_S = 1.0

    def __init__(self, sensor: ThreespaceSensor, streaming_manager: ThreespaceStreamingManager = None):
        super().__init__(sensor, streaming_manager)
        self._disconnect_timestamp: int = None      # Sensor timestamp (us) of the last read before the disconnect
        self._disconnect_time: float = None         # perf_counter() of the first failed read

    @step("Self test")
    def self_test(self):
        self.change_settings(debug_mode=0,
                             debug_level=THREESPACE_DEBUG_LEVEL_ERR,
                             debug_module=THREESPACE_DEBUG_MODULE_BATTERY)
        self.read_debug_messages()   # Discard old messages
        self.sensor.selfTest()
        errors = self.read_debug_messages()
        if errors:
            self.check().add_measurement("errors", errors).failed()
        else:
            self.check().passed()

    @step("Charging Status")
    def status(self):
        status = self.sensor.getBatteryStatus().data
        self.check().add_measurement("status", status)
        if (status & ~128) in (1, 2):   # 1 = Charged, 2 = Charging
            self.check().passed()
        else:
            self.check().failed("Battery is not charging or charged.")

    @step("Disconnect", check="reconnect")
    def disconnect(self):
        if self.sensor.has_setting("power_hold_state"):
            self.change_settings(power_hold_state=1)   # Keep the sensor powered on after it is unplugged
            text = "Unplug the sensor from the USB port."
        else:
            text = "Make sure the sensor will stay powered on, then unplug it from the USB port."

        while True:
            attempt_time = time.perf_counter()
            try:
                self._disconnect_timestamp = self.sensor.getTimestamp().data
            except (OSError, ResponseTimeoutError):
                self._disconnect_time = attempt_time
                break
            yield Busy(text)
        self.check().add_measurement("disconnect_time", self._disconnect_timestamp)

    @step("Reconnect")
    def reconnect(self):
        while not self.sensor.attempt_reconnect(timeout=0):
            yield Busy("Plug the sensor back into the USB port.")

        expected_elapsed = time.perf_counter() - self._disconnect_time
        timestamp = self.sensor.getTimestamp().data
        elapsed = (timestamp - self._disconnect_timestamp) / 1_000_000

        result = self.check()
        result.add_measurement("connect_time", timestamp)
        result.add_measurement("elapsed_time_s", elapsed)
        result.add_criteria("expected_elapsed_time_s", expected_elapsed)
        result.add_criteria("time_tolerance_s", self.TIME_TOLERANCE_S)
        if timestamp < self._disconnect_timestamp:
            result.failed("Sensor clock restarted, so it lost power while unplugged.")
        elif abs(elapsed - expected_elapsed) > self.TIME_TOLERANCE_S:
            result.failed(f"Sensor clock advanced {elapsed:.2f}s while unplugged, expected {expected_elapsed:.2f}s.")
        else:
            result.passed()

    def cleanup(self):
        self.read_debug_messages()   # Discard the messages produced by the test

if __name__ == "__main__":
    main(BatteryTest)
