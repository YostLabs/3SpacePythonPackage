from yostlabs.tss3.utils.tests.base import SensorTestBase
from yostlabs.tss3.api import ThreespaceSensor
import enum

class LEDTestState(enum.Enum):
    Inactive = 0
    AwaitingRedResponse = 1
    AwaitingGreenResponse = 2
    AwaitingBlueResponse = 3
    Finished = 4

class LEDTest(SensorTestBase):

    RED = [1.0, 0.0, 0.0]
    GREEN = [0.0, 1.0, 0.0]
    BLUE = [0.0, 0.0, 1.0]

    COLORS_TO_TEST: dict[str, list[float]] = {
        "red": RED,
        "green": GREEN,
        "blue": BLUE
    }

    def __init__(self, sensor: ThreespaceSensor):
        super().__init__(sensor)

        self.state = LEDTestState.Inactive

        self.expected_color_name = None

        self.result = { color: False for color in LEDTest.COLORS_TO_TEST.keys() }
    
    def start(self):
        if self.state != LEDTestState.Inactive:
            raise Exception("LED test already started.")
        
        self.led_settings_cache = self.sensor.read_settings("led_mode", "led_rgb")
        self.sensor.writeLedMode(1)

        self.__go_next_state()

    def cancel(self):
        if self.state == LEDTestState.Inactive:
            return
        self.state = LEDTestState.Inactive
        self.sensor.write_settings(**self.led_settings_cache)

    def verify_match(self, matches: bool):
        if not matches:
            self.overall_success = False
        self.result[self.expected_color_name] = matches
        self.__go_next_state()

        if self.state == LEDTestState.Finished:
            return True
        return False

    def __go_next_state(self):
        match self.state:
            case LEDTestState.Inactive:
                self.__set_color("red")
                self.state = LEDTestState.AwaitingRedResponse
            case LEDTestState.AwaitingRedResponse:
                self.__set_color("green")
                self.state = LEDTestState.AwaitingGreenResponse
            case LEDTestState.AwaitingGreenResponse:
                self.__set_color("blue")
                self.state = LEDTestState.AwaitingBlueResponse
            case LEDTestState.AwaitingBlueResponse:
                self.state = LEDTestState.Finished
                self.expected_color_name = None
                self.sensor.write_settings(**self.led_settings_cache)
            case _:
                raise Exception("Invalid state for going to the next state.")
    
    @property
    def expected_color(self) -> list[float]:
        if self.expected_color_name is None:
            return None
        return LEDTest.COLORS_TO_TEST[self.expected_color_name]

    def __set_color(self, name: str):
        self.expected_color_name = name
        self.sensor.writeLedRgb(self.expected_color)


if __name__ == "__main__":
    sensor = ThreespaceSensor()

    test = LEDTest(sensor)
    test.start()
    while test.state != LEDTestState.Finished:
        result = input(f"Is the LED {test.expected_color_name}? (Y/n)")
        if result.lower() == "n":
            test.verify_match(False)
        elif result.lower() == "y" or result == "":
            test.verify_match(True)
    print(f"Results: {test.result}")
    print(f"Overall success: {test.overall_success}")