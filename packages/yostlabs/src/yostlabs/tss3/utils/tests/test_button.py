from collections import deque
from typing import Any
import time

from yostlabs.tss3.utils.tests.base import SensorTest, SensorVariant, Busy, step
from yostlabs.tss3.consts import THREESPACE_FAMILY_DATA_LOGGER
from yostlabs.tss3.utils.tests.cli import main
from yostlabs.tss3.api import ThreespaceSensor
from yostlabs.tss3.errors import UnsupportedTestError
from yostlabs.tss3.utils.streaming import ThreespaceStreamingManager, ThreespaceStreamingStatus, StreamableCommands, threespace_command_get

import logging
logger = logging.getLogger(__name__)


class ButtonTest(SensorTest):
    """
    Tests the button functionality of the sensor.
    
    To pass this test, the operator must hold the button down for 2 seconds, and then release it for 2 seconds.
    Each stage fails if 10 seconds elapse without it finishing.
    The button state is streamed, so very quick blips in the button state are not missed.
    """

    id = "button"
    name = "Button"
    description = "Checks the button reads pressed while held down, and released when let go."
    variants = (SensorVariant(THREESPACE_FAMILY_DATA_LOGGER),)
    stop_on_failure = True

    HOLD_TIME = 2.0
    RELEASE_TIME = 2.0
    TIMEOUT = 10.0

    def __init__(self, sensor: ThreespaceSensor, streaming_manager: ThreespaceStreamingManager = None):
        super().__init__(sensor, streaming_manager)
        self._samples: deque[tuple[float, bool]] = deque()   # (sensor time in seconds, pressed), oldest first
        self._time_offset: float = None
        self._pressed = False                                 # Treat the button as initially not pressed
        self._streaming_reset = False

    @step("Button pressed")
    def held(self):
        if not self.sensor.has_command(threespace_command_get(StreamableCommands.GetButtonState.value)):
            raise UnsupportedTestError("Sensor does not support button state command.")

        self._disable_button_actions()
        self.start_streaming([(StreamableCommands.GetButtonState, None), (StreamableCommands.GetTimestamp, None)],
                             self._on_streaming_data, hz=100)
        self.check().add_criteria("hold_time_s", self.HOLD_TIME)
        yield from self._await_button(True, self.HOLD_TIME, "Hold the button down for 2 seconds.")

    @step("Button released")
    def released(self):
        self.check().add_criteria("release_time_s", self.RELEASE_TIME)
        yield from self._await_button(False, self.RELEASE_TIME, "Release the button for 2 seconds.")

    def _await_button(self, pressed: bool, duration: float, text: str):
        """Passes the active check once the button stays in the pressed state for duration seconds."""
        result = self.check()
        deadline = time.perf_counter() + self.TIMEOUT
        since = None        # Sensor time the button entered the wanted state
        elapsed = 0.0
        while True:
            self.streaming_manager.update()
            if self._streaming_reset:
                logger.warning("Streaming reset occurred during button test.")
                result.failed("Streaming reset occurred.")
                return

            # Samples left over when the check passes belong to the next stage
            while self._samples:
                sample_time, state = self._samples.popleft()
                if state != self._pressed:
                    if state:
                        self.check("held").measurements.setdefault("press_times", []).append(sample_time)
                    else:
                        self.check("released").measurements.setdefault("release_times", []).append(sample_time)
                    self._pressed = state

                if state != pressed:
                    since = None
                    continue
                if since is None:
                    since = sample_time
                elapsed = sample_time - since
                result.add_measurement("elapsed_time_s", elapsed)
                if elapsed > duration:
                    result.passed()
                    return

            if time.perf_counter() > deadline:
                logger.warning("Button test timed out after %.1f seconds.", self.TIMEOUT)
                result.failed("Timed out waiting for button input.")
                return
            yield Busy(text, f"Button {'pressed' if self._pressed else 'released'}, {elapsed:.2f} / {duration:.2f} s")

    def _on_streaming_data(self, status: ThreespaceStreamingStatus, user_data: Any):
        match status:
            case ThreespaceStreamingStatus.Data:
                sample_time = self.streaming_manager.get_value(StreamableCommands.GetTimestamp) / 1_000_000
                if self._time_offset is None:
                    self._time_offset = sample_time
                pressed = bool(self.streaming_manager.get_value(StreamableCommands.GetButtonState))
                self._samples.append((sample_time - self._time_offset, pressed))
            case ThreespaceStreamingStatus.Reset:
                self.stop_streaming()
                self._streaming_reset = True

    def _disable_button_actions(self):
        """So the button can be tested without it triggering any other actions on the sensor"""
        if self.sensor.has_setting("power_hold_time"):
            self.change_settings(power_hold_time=-1)
        if self.sensor.has_setting("log_start_event"):
            events = [int(event) for event in self.sensor.readLogStartEvent().strip().split(',')]
            if 0 in events:   # 0 is the button event
                self.change_settings(log_start_event="2")   # Command only

if __name__ == "__main__":
    main(ButtonTest)
