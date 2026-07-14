from yostlabs.tss3.utils.tests.base import SensorTestBase
from yostlabs.tss3.api import ThreespaceSensor, InvalidKeyError
from yostlabs.tss3.utils.streaming import ThreespaceStreamingManager, ThreespaceStreamingStatus, StreamableCommands, threespace_command_get
from typing import Any
import time

class ButtonTestState:
    Inactive = 0
    AwaitingButtonHeld = 1

    AwaitingButtonRelease = 2
    Finished = 3

class ButtonTest(SensorTestBase):
    """
    To pass this test, the user must hold the button down for 2 seconds,
    and then release it for 2 seconds. The test will fail if 10 seconds elapse
    without the process finishing. Alternatively, the test can be fail()ed early
    """

    HOLD_TIME = 2.0
    RELEASE_TIME = 2.0
    TIMEOUT = 10.0

    def __init__(self, sensor: ThreespaceSensor, streaming_manager: ThreespaceStreamingManager = None):
        super().__init__(sensor)
        self.cache = {}
        self.was_streaming_enabled = False

        #Streaming to lessen the chance of very quick blips in button state being missed.
        self.streaming_manager = streaming_manager
        if self.streaming_manager is None:
            self.streaming_manager = ThreespaceStreamingManager(sensor)
        
        self.state = ButtonTestState.Inactive
        self.previous_button_state = False #Treat button as initially not pressed

        self.result: dict[str,list[float]] = {
            "held": False,
            "released": False,
            "press_times": [],
            "release_times": []
        }

        self.button_state_changed_time = None
        self.start_time = None
        self.time_offset = None
        self.last_time = None


    def start(self):
        if not self.sensor.has_command(threespace_command_get(StreamableCommands.GetButtonState.value)):
            raise Exception("Sensor does not support button state command.")
        
        #This is done so that the button readings can be verified 
        #without the button triggering any other actions on the sensor.
        self.__disable_button_interactions()
        
        
        self.streaming_manager.register_command(self, StreamableCommands.GetButtonState, immediate_update=False)
        self.streaming_manager.register_command(self, StreamableCommands.GetTimestamp)
        self.streaming_manager.register_callback(self.__streaming_callback, hz=100)

        self.was_streaming_enabled = self.streaming_manager.enabled
        if not self.was_streaming_enabled:
            self.streaming_manager.enable()

        self.state = ButtonTestState.AwaitingButtonHeld
        self.start_time = time.perf_counter()
    
    def update(self):
        if self.state == ButtonTestState.Inactive or self.state == ButtonTestState.Finished:
            return
        self.streaming_manager.update()
        if not self.state == ButtonTestState.Finished and time.perf_counter() - self.start_time > self.TIMEOUT:
            self.overall_success = False
            self.state = ButtonTestState.Finished
            self.fail()

    def fail(self):
        if self.state in [ButtonTestState.Inactive, ButtonTestState.Finished]:
            raise Exception("Button test not active.")
        self.overall_success = False
        self.state = ButtonTestState.Finished
        self.__cleanup()

    def cancel(self):
        self.state = ButtonTestState.Inactive
        self.__cleanup()
    
    @property
    def button_state(self) -> bool:
        return self.previous_button_state
    
    @property
    def button_match_time(self) -> float:
        if self.button_state_changed_time is None:
            return 0.0
        return self.last_time - self.button_state_changed_time

    @property
    def desired_button_state(self) -> bool:
        match self.state:
            case ButtonTestState.AwaitingButtonHeld:
                return True
            case ButtonTestState.AwaitingButtonRelease:
                return False
            case _:
                return None

    def __streaming_callback(self, status: ThreespaceStreamingStatus, user_data: Any):
        match status:
            case ThreespaceStreamingStatus.Data:
                time = self.streaming_manager.get_value(StreamableCommands.GetTimestamp)
                time = time / 1_000_000 #Convert to seconds
                if self.time_offset is None:
                    self.time_offset = time
                time = time - self.time_offset
                button_state = bool(self.streaming_manager.get_value(StreamableCommands.GetButtonState))
                self.__update_state(time, button_state)
                self.previous_button_state = button_state
                self.last_time = time
            case ThreespaceStreamingStatus.Reset:
                self.streaming_manager.unregister_command(self, StreamableCommands.GetButtonState)
                self.streaming_manager.unregister_command(self, StreamableCommands.GetTimestamp)
                self.streaming_manager.unregister_callback(self.__streaming_callback)
                self.overall_success = False

    def __update_state(self, time: float, button_state: bool):
        #Record changes in the button state
        if button_state != self.previous_button_state:
            if button_state:
                self.result["press_times"].append(time)
            else:
                self.result["release_times"].append(time)
        
        match self.state:
            case ButtonTestState.AwaitingButtonHeld:
                self.__hold_button_state_update(time, button_state)
            case ButtonTestState.AwaitingButtonRelease:
                self.__release_button_state_update(time, button_state)
            case _:
                pass

    def __hold_button_state_update(self, time: float, button_state: bool):
        if button_state:
            if self.previous_button_state == False:
                self.button_state_changed_time = time
            elapsed_time = time - self.button_state_changed_time
            if elapsed_time > self.HOLD_TIME:
                self.result["held"] = True
                self.state = ButtonTestState.AwaitingButtonRelease
                self.button_state_changed_time = None
        else:
            self.button_state_changed_time = None

    def __release_button_state_update(self, time: float, button_state: bool):
        if not button_state:
            if self.previous_button_state == True:
                self.button_state_changed_time = time
            elapsed_time = time - self.button_state_changed_time
            if elapsed_time > self.RELEASE_TIME:
                self.result["released"] = True
                self.state = ButtonTestState.Finished
                self.__cleanup()
        else:
            self.button_state_changed_time = None

    def __cleanup(self):
        if self.state == ButtonTestState.Inactive:
            return
        
        if not self.was_streaming_enabled:
            self.streaming_manager.disable()

        self.streaming_manager.unregister_callback(self.__streaming_callback)
        self.streaming_manager.unregister_command(self, StreamableCommands.GetButtonState, immediate_update=False)
        self.streaming_manager.unregister_command(self, StreamableCommands.GetTimestamp)
        if len(self.cache) > 0:
            self.sensor.write_settings(**self.cache)
        

    def __cache_button_interactions(self):
        self.cache = {}
        try:
            hold_time = self.sensor.readPowerHoldTime()
            self.cache["power_hold_time"] = hold_time
        except InvalidKeyError:
            pass

        try:
            start_events = self.sensor.readLogStartEvent()
            parsed_events = start_events.strip().split(',')
            parsed_events = [int(event) for event in parsed_events]
            if 0 in parsed_events: #0 is the button event
                self.cache["log_start_event"] = start_events
        except InvalidKeyError:
            pass

    def __disable_button_interactions(self):
        self.__cache_button_interactions()
        if "power_hold_time" in self.cache:
            self.sensor.writePowerHoldTime(-1)
        if "log_start_event" in self.cache:
            self.sensor.writeLogStartEvent("2") #Command only

def run_test(sensor: ThreespaceSensor):
    test = ButtonTest(sensor)

    last_state = test.state

    test.start()
    print("\033[?25l", end="", flush=True) #Hide the cursor in the terminal
    try:
        while not test.state == ButtonTestState.Finished:
            if last_state != test.state:
                last_state = test.state
                match test.state:
                    case ButtonTestState.AwaitingButtonHeld:
                        print("\nPlease hold the button down for 2 seconds.")
                    case ButtonTestState.AwaitingButtonRelease:
                        print("\nPlease release the button for 2 seconds.")
            print(f"Desired State: {test.desired_button_state}, Button State: {test.button_state}, Match Time: {test.button_match_time:.02f}".ljust(80), end="\r", flush=True)
            test.update()
    except KeyboardInterrupt:
        print("\nTest failed by user.")
        test.fail()
    finally:
        print("\033[?25h", end="", flush=True) #Show the cursor in the terminal
        print("Completed button test.")
    return test.overall_success, test.result

def auto_run_test():
    sensor = ThreespaceSensor()
    overall_success, results = run_test(sensor)
    sensor.cleanup()
    print(f"\nResults: {results}")
    print(f"Overall success: {overall_success}")
    return overall_success, results

if __name__ == "__main__":
    auto_run_test()
