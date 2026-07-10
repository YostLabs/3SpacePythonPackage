from yostlabs.tss3.utils.tests.base import SensorTestBase
from yostlabs.tss3.api import ThreespaceSensor
from yostlabs.tss3.consts import *
import enum
import time


class BatteryTestState(enum.Enum):
    Inactive = 0
    SelfTest = 1
    CheckingStatus = 2
    AwaitingDisconnect = 3
    AwaitingReconnect = 4
    Finished = 5


class BatteryTest(SensorTestBase):
    """
    Test the battery status of the sensor
    First checks the self test. If errors, stops there.
    If not, checks the status to be charging or charged.
    If that passes, it tests the user disconnecting and keeping the sensor powered on.
        If the sensor has BLE, it will connect over BLE and auto validate everything
        Otherwise, the user will need to manually verify and give input to the test.
    At this point the battery is confirmed working.
    """

    def __init__(self, sensor: ThreespaceSensor):
        super().__init__(sensor)
        self.state = BatteryTestState.Inactive
        self.result = {
            "self_test": {
                "success": None,
                "errors": []
            },
            "status": {
                "success": None,
                "status": None
            },
            "reconnect": {
                "success": None,
                "disconnect_time": None,
                "connect_time": None
            },
        }

        self.settings_cache = None

    def start(self):
        if self.state != BatteryTestState.Inactive:
            raise Exception("Battery test already started.")
        
        self.__cache_settings()
        self.sensor.write_settings(debug_mode=0, 
                                   debug_level=THREESPACE_DEBUG_LEVEL_ERR, 
                                   debug_module=THREESPACE_DEBUG_MODULE_BATTERY)

        self.__go_next_state()
        self.update()

    def cancel(self):
        if self.state == BatteryTestState.Inactive:
            return
        self.state = BatteryTestState.Inactive
        self.__cleanup()          

    def update(self):
        if self.state == BatteryTestState.Inactive or self.state == BatteryTestState.Finished:
            return
        match self.state:
            case BatteryTestState.SelfTest:
                self.__update_self_test()
            case BatteryTestState.CheckingStatus:
                self.__update_checking_status()
            case BatteryTestState.AwaitingDisconnect:
                self.__update_awaiting_disconnect()
            case BatteryTestState.AwaitingReconnect:
                self.__update_awaiting_reconnect()

    # ------------------------------------------------------------------
    # Disconnect state helpers
    # ------------------------------------------------------------------

    def can_auto_validate_disconnect(self) -> bool:
        """Returns True if the sensor supports automatic disconnect validation (e.g. via BLE),
        False if the user must manually confirm."""
        pass

    def verify_disconnect(self, passed: bool):
        """Manually report whether the disconnect-while-powered test passed.
        Only call this when can_auto_validate_disconnect() returns False."""
        pass

    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------

    def __clear_messages(self):
        num_messages = self.sensor.getNumDebugMessages().data
        for _ in range(num_messages):
            self.sensor.getOldestDebugMessage()

    def __cache_settings(self):
        self.settings_cache = self.sensor.read_settings(
            "debug_level", "debug_module", 
            "debug_mode", "power_hold_state"
        )

    # ------------------------------------------------------------------
    # Private state handlers
    # ------------------------------------------------------------------

    def __update_self_test(self):
        self.__clear_messages()
        self.sensor.selfTest()
        num_debug_messages = self.sensor.getNumDebugMessages().data
        if num_debug_messages > 0:
            self.result["self_test"]["success"] = False
            for _ in range(num_debug_messages):
                message = self.sensor.getOldestDebugMessage()
                self.result["self_test"]["errors"].append(message.data.strip())
            
            self.overall_success = False
            self.state = BatteryTestState.Finished
        else:
            self.result["self_test"]["success"] = True
            self.__go_next_state()
            self.update()

    def __update_checking_status(self):
        self.result["status"]["status"] = self.sensor.getBatteryStatus().data
        self.result["status"]["success"] = (self.result["status"]["status"] & ~128 ) in (1, 2) # 1 = Charged, 2 = Charging
        if not self.result["status"]["success"]:
            self.overall_success = False
            self.state = BatteryTestState.Finished
        else:
            self.__go_next_state()
            self.update()

    def __start_awaiting_disconnect(self):
        self.state = BatteryTestState.AwaitingDisconnect
        self.sensor.writePowerHoldState(1) #Keep the sensor powered on after disconnect to test battery
        self.last_time = self.sensor.getTimestamp().data

    def __update_awaiting_disconnect(self):
        try:
            self.last_time = self.sensor.getTimestamp().data
        except OSError as e:
            self.result["reconnect"]["disconnect_time"] = self.last_time
            self.last_time = time.perf_counter()
            self.__go_next_state()
            self.update()

    def __update_awaiting_reconnect(self):
        try:
            success = self.sensor.attempt_reconnect()
            if success:
                cur_time = self.sensor.getTimestamp().data
                disconnect_time = self.result["reconnect"]["disconnect_time"]
                self.result["reconnect"]["connect_time"] = cur_time
    
                if cur_time < disconnect_time:
                    self.result["reconnect"]["success"] = False
                    self.overall_success = False
                else:
                    expected_elapsed_time = time.perf_counter() - self.last_time
                    elapsed_time = (cur_time - disconnect_time) / 1_000_000  # Convert microseconds to seconds
                    if abs(elapsed_time - expected_elapsed_time) > 1:  # Allow some tolerance
                        self.result["reconnect"]["success"] = False
                        self.overall_success = False
                    else:
                        self.result["reconnect"]["success"] = True
                
                self.__go_next_state()
                self.update()
        except Exception as e:
            print("Exception occurred in BatteryTest:", e)
    

    def __cleanup(self):
        if self.settings_cache is not None:
            self.sensor.write_settings(**self.settings_cache)
            self.__clear_messages()  

    def __go_next_state(self):
        match self.state:
            case BatteryTestState.Inactive:
                self.state = BatteryTestState.SelfTest
            case BatteryTestState.SelfTest:
                self.state = BatteryTestState.CheckingStatus
            case BatteryTestState.CheckingStatus:
                self.__start_awaiting_disconnect()
            case BatteryTestState.AwaitingDisconnect:
                self.state = BatteryTestState.AwaitingReconnect
            case BatteryTestState.AwaitingReconnect:
                self.state = BatteryTestState.Finished
                self.__cleanup()
            case _:
                raise Exception("Invalid state for going to the next state.")
            

def run_test():
    sensor = ThreespaceSensor()
    test = BatteryTest(sensor)
    last_state = test.state
    test.start()
    while test.state != BatteryTestState.Finished:
        if test.state != last_state:
            if test.state == BatteryTestState.AwaitingDisconnect:
                print("Please remove the sensor from the USB port.")
            elif test.state == BatteryTestState.AwaitingReconnect:
                print("Please reconnect the sensor to the USB port.")
            last_state = test.state
        
        test.update()
    print(test.result)
    print("Overall success:", test.overall_success)

if __name__ == "__main__":
    run_test()