from yostlabs.tss3.utils.tests.base import SensorTest, ALL_SENSORS, TestStatus, Confirm, step
from yostlabs.tss3.utils.tests.cli import main

import logging
logger = logging.getLogger(__name__)

class LEDTest(SensorTest):
    """Visual confirmation of the 3 primary LED colors."""

    id = "led"
    name = "LED"
    description = "Shows each color on the LED for user confirmation."
    variants = ALL_SENSORS

    @step("Red LED")
    def red(self):
        yield from self._check_color("red", [1.0, 0.0, 0.0])

    @step("Green LED")
    def green(self):
        yield from self._check_color("green", [0.0, 1.0, 0.0])

    @step("Blue LED")
    def blue(self):
        yield from self._check_color("blue", [0.0, 0.0, 1.0])

    def _check_color(self, color: str, rgb: list[float]):
        self.change_settings(led_mode=1, led_rgb=rgb)
        matches = yield Confirm(f"Is the LED {color}?")
        if not matches:
            logger.warning("LED color %s did not match user expectation.", color)
        self.check().set_status(TestStatus.PASS if matches else TestStatus.FAIL)

if __name__ == "__main__":
    main(LEDTest)
