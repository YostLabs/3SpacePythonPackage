from yostlabs.tss3.utils.tests.base import SensorTest, SensorVariant, TestKind, Busy, CONNECTION_LOST, step
from yostlabs.tss3.utils.tests.cli import main
from yostlabs.tss3.utils.streaming import ThreespaceStreamingManager
from yostlabs.tss3.api import ThreespaceSensor
from yostlabs.tss3.consts import *
import time

import logging
logger = logging.getLogger(__name__)


# getBatteryStatus() values (bit 7 aside) meaning the sensor is on its charger. Off it, it reads neither (Ex: standby)
CHARGED, CHARGING = 1, 2


def on_charger(sensor: ThreespaceSensor) -> bool:
    """On its charger, or plugged in: on external power"""
    return (sensor.getBatteryStatus().data & ~128) in (CHARGED, CHARGING)


class BatteryTest(SensorTest):
    """
    Tests the battery functionality of the sensor, around its charger: a USB cable, or a charger it's put on.

    1. The self test reports no battery errors.
    2. The operator starts charging it, unless it is already: the battery reports charging or charged.
    3. The operator takes it off the charger, and it stays powered on by its battery: the battery reports neither, or,
       unplugged from USB, its connection is lost. If the sensor can't be told to stay on once unplugged (no
       power_hold_state), the operator must make sure it does.
    4. The operator charges it again, reconnecting to it first if its connection was lost. The sensor's clock must have
       kept running for the time it was off the charger.

    Over Bluetooth, the connection stays on and off the charger, so only the battery status tells, though on some
    sensors taking it off the charger can drop the link, which is then reconnected like a USB one. While waiting on any
    of them, the operator can fail the step if the app never notices.
    """

    id = "battery"
    name = "Battery"
    description = "Checks the battery charges, and keeps the sensor running while it is off its charger."
    variants = (SensorVariant(THREESPACE_FAMILY_DATA_LOGGER),)
    required_hardware = frozenset({"battery"})
    kind = TestKind.FUNCTIONAL
    stop_on_failure = True

    TIME_TOLERANCE_S = 1.0

    def __init__(self, sensor: ThreespaceSensor, streaming_manager: ThreespaceStreamingManager = None):
        super().__init__(sensor, streaming_manager)
        self._off_timestamp: int = None      # Sensor timestamp (us) of the last read before it was off the charger
        self._off_time: float = None         # perf_counter() of the read finding it off
        self._connection_lost = False        # Taking it off the charger unplugged it (USB), or dropped the link

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

    @step("On the charger")
    def charging(self):
        while not on_charger(self.sensor):
            if (yield Busy("Start charging the sensor: plug it in, or put it on its charger.",
                           actions=("Fail",))) == "Fail":
                self.check().failed("Failed by the operator: the sensor charging was not detected.")
                return
        self.check().passed()

    @step("Off the charger", check="off_charger")
    def off_charger(self):
        if self.sensor.has_setting("power_hold_state"):
            self.change_settings(power_hold_state=1)   # Keep the sensor powered on after it is unplugged from USB
            text = "Take the sensor off its charger: unplug it, or take it off the charger."
        else:
            text = ("Make sure the sensor will stay powered on, then take it off its charger: unplug it, or take it off "
                    "the charger.")

        while True:
            attempt_time = time.perf_counter()
            try:
                timestamp = self.sensor.getTimestamp().data
                if not on_charger(self.sensor):   # Ex: over Bluetooth, which stays connected
                    self._off_timestamp, self._off_time = timestamp, attempt_time
                    break
                self._off_timestamp = timestamp
            except CONNECTION_LOST:   # Unplugged from USB, or the link dropped
                self._off_time, self._connection_lost = attempt_time, True
                break
            if (yield Busy(text, actions=("Fail",))) == "Fail":
                self.check().failed("Failed by the operator: the sensor being taken off its charger was not detected.")
                return
        self.check().add_measurement("off_time", self._off_timestamp)
        self.check().add_measurement("connection_lost", self._connection_lost)

    @step("Back on the charger", check="off_charger")
    def back_on_charger(self):
        text = "Charge the sensor again: plug it back in, or put it back on its charger."
        lost = self._connection_lost
        while True:
            # Shown before checking: reconnecting can block a while (Ex: a Bluetooth connection attempt)
            if (yield Busy(text, actions=("Fail",))) == "Fail":
                if lost:
                    self.check().failed("Failed by the operator: the sensor being plugged back in was not detected.")
                    self.stop_session = True   # The sensor is gone, so no later test can run
                else:
                    self.check().failed("Failed by the operator: the sensor charging again was not detected.")
                return
            if lost and not self.sensor.attempt_reconnect(timeout=0):
                continue
            lost = False   # Found again: its status next
            try:
                if on_charger(self.sensor):
                    break
            except CONNECTION_LOST:   # Unplugged from USB or the link dropped, though its status read off first
                lost = True

        expected_elapsed = time.perf_counter() - self._off_time
        timestamp = self.sensor.getTimestamp().data
        elapsed = (timestamp - self._off_timestamp) / 1_000_000

        result = self.check()
        result.add_measurement("on_time", timestamp)
        result.add_measurement("elapsed_time_s", elapsed)
        result.add_criteria("expected_elapsed_time_s", expected_elapsed)
        result.add_criteria("time_tolerance_s", self.TIME_TOLERANCE_S)
        if timestamp < self._off_timestamp:
            result.failed("Sensor clock restarted, so it lost power while off its charger.")
        elif abs(elapsed - expected_elapsed) > self.TIME_TOLERANCE_S:
            result.failed(f"Sensor clock advanced {elapsed:.2f}s while off its charger, expected {expected_elapsed:.2f}s.")
        else:
            result.passed()

    def cleanup(self):
        self.read_debug_messages()   # Discard the messages produced by the test

if __name__ == "__main__":
    main(BatteryTest)
