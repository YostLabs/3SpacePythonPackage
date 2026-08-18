from yostlabs.tss3.utils.tests.base import SensorTestBase, TestResult, TestStatus
from yostlabs.tss3.api import ThreespaceSensor, InvalidKeyError, ResponseTimeoutError
from yostlabs.tss3.consts import *
import enum
import time

import logging
logger = logging.getLogger(__name__)

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
            "self_test":    TestResult("battery", "self_test"),
            "status":       TestResult("battery", "status"),
            "reconnect":    TestResult("battery", "reconnect",)
        }

        self.settings_cache = None
        self.manual_power_hold_state = False

    def start(self):
        if self.state != BatteryTestState.Inactive:
            raise Exception("Battery test already started.")
        
        self.__cache_settings()
        self.sensor.write_settings(debug_mode=0, 
                                   debug_level=THREESPACE_DEBUG_LEVEL_ERR, 
                                   debug_module=THREESPACE_DEBUG_MODULE_BATTERY)
        self.__go_next_state()

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
    # Generic helpers
    # ------------------------------------------------------------------

    def __clear_messages(self):
        num_messages = self.sensor.getNumDebugMessages().data
        for _ in range(num_messages):
            self.sensor.getOldestDebugMessage()

    def __cache_settings(self):
        self.settings_cache = self.sensor.read_settings(
            "debug_level", "debug_module", 
            "debug_mode"
        )
        try:
            self.settings_cache |= self.sensor.read_settings("power_hold_state")
        except InvalidKeyError:
            self.manual_power_hold_state = True

    # ------------------------------------------------------------------
    # Private state handlers
    # ------------------------------------------------------------------

    def __update_self_test(self):
        self.__clear_messages()
        self.sensor.selfTest()
        num_debug_messages = self.sensor.getNumDebugMessages().data
        if num_debug_messages > 0:
            errors = []
            self.result["self_test"].set_status(TestStatus.FAIL)
            for _ in range(num_debug_messages):
                message = self.sensor.getOldestDebugMessage()
                errors.append(message.data.strip())
            self.result["self_test"].measurements["errors"] = errors
            self.state = BatteryTestState.Finished
        else:
            self.result["self_test"].set_status(TestStatus.PASS)
            self.__go_next_state()

    def __update_checking_status(self):
        status = self.sensor.getBatteryStatus().data
        self.result["status"].measurements["status"] = status
        success = (status & ~128 ) in (1, 2) # 1 = Charged, 2 = Charging
        if not success:
            self.result["status"].set_status(TestStatus.FAIL)
            self.state = BatteryTestState.Finished
            return

        self.result["status"].set_status(TestStatus.PASS)
        self.__go_next_state()
            
    def __start_awaiting_disconnect(self):
        self.state = BatteryTestState.AwaitingDisconnect
        if not self.manual_power_hold_state:
            self.sensor.writePowerHoldState(1) #Keep the sensor powered on after disconnect to test battery
        self.last_time = self.sensor.getTimestamp().data

    def __update_awaiting_disconnect(self):
        try:
            self.last_read_attempt_time = time.perf_counter()
            self.last_time = self.sensor.getTimestamp().data
        except (OSError, ResponseTimeoutError) as e:
            self.result["reconnect"].measurements["disconnect_time"] = self.last_time
            self.last_time = self.last_read_attempt_time
            self.__go_next_state()

    def __update_awaiting_reconnect(self):
        try:
            success = self.sensor.attempt_reconnect()
            if success:
                expected_elapsed_time = time.perf_counter() - self.last_time
                cur_time = self.sensor.getTimestamp().data

                result = self.result["reconnect"]
                result.measurements["connect_time"] = cur_time
                disconnect_time = result.measurements["disconnect_time"]
                elapsed_time = (cur_time - disconnect_time) / 1_000_000  # Convert microseconds to seconds
                result.measurements["elapsed_time_s"] = elapsed_time

                TIME_TOLERANCE_S = 1.0  # 1 second tolerance
                result.criteria["expected_elapsed_time_s"] = expected_elapsed_time
                result.criteria["time_tolerance_s"] = TIME_TOLERANCE_S  # 1 second tolerance
                if cur_time < disconnect_time:
                    result.set_status(TestStatus.FAIL)
                else:
                    if abs(elapsed_time - expected_elapsed_time) > TIME_TOLERANCE_S:  # Allow some tolerance
                        result.set_status(TestStatus.FAIL)
                    else:
                        result.set_status(TestStatus.PASS)
                
                self.__go_next_state()
        except Exception as e:
            logger.exception("Exception occurred in BatteryTest")
            self.result["reconnect"].set_status(TestStatus.ERROR)
            self.result["reconnect"].message = str(e)
            self.state = BatteryTestState.Finished

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
        
        self.update()
            

def run_test(sensor: ThreespaceSensor):
    test = BatteryTest(sensor)
    last_state = test.state
    test.start()
    try:
        while test.state != BatteryTestState.Finished:
                if test.state != last_state:
                    if test.state == BatteryTestState.AwaitingDisconnect:
                        if test.manual_power_hold_state:
                            print("Please ensure the sensor will remain powered on and disconnect the sensor from the USB port.")
                        else:
                            print("Please remove the sensor from the USB port.")
                    elif test.state == BatteryTestState.AwaitingReconnect:
                        print("Please reconnect the sensor to the USB port.")
                    last_state = test.state
                
                test.update()
    except KeyboardInterrupt:
        test.cancel()
        print("\nTest cancelled by user.")
        return (False if not test.overall_success else None), test.result_flat

    return test.overall_success, test.result_flat

def auto_run_test():
    sensor = ThreespaceSensor()
    overall_success, results = run_test(sensor)
    sensor.cleanup()
    for test in results:
        print(test)
    print("Overall success:", overall_success)
    return overall_success, results

if __name__ == "__main__":
    auto_run_test()