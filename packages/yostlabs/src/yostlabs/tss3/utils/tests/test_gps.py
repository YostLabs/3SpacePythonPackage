from yostlabs.tss3.utils.tests.base import SensorTestBase
from yostlabs.tss3.api import ThreespaceSensor
from yostlabs.tss3.consts import *

import enum
import time

class GPSTestState(enum.Enum):
    Inactive = 0
    AwaitingFirstMessage = 1
    AwaitingNoMessage = 2
    Finished = 3


class GPSTest(SensorTestBase):

    EXPECTED_MESSAGE_INTERVAL = 1.0 #Seconds
    MESSAGE_PADDING = 0.5 #Seconds

    def __init__(self, sensor: ThreespaceSensor):
        super().__init__(sensor)
        self.gps_settings_cache = None

        self.result = {
            "gps_active": {
                "success": False,
                "message": None
            },
            "gps_standby": {
                "success": False,
                "message": None
            }
        }
    
        self.state_start_time = None
        self.state = GPSTestState.Inactive

    def start(self):

        #Cache Debug Messages and Standby
        self.gps_settings_cache = self.sensor.read_settings(
            "debug_level", "debug_module", 
            "debug_mode", "gps_standby"
        )

        #Empty any current debug messages
        self.__clear_messages()

        #Enable Debug Info messages for GPS
        self.sensor.writeDebugMode(0)
        self.sensor.writeDebugLevel(THREESPACE_DEBUG_LEVEL_INFO)
        self.sensor.writeDebugModule(THREESPACE_DEBUG_MODULE_GPS)

        #Ensure not in standby
        self.state_start_time = time.perf_counter()
        self.sensor.writeGpsStandby(0)

        self.state = GPSTestState.AwaitingFirstMessage

    def cancel(self):
        if self.state == GPSTestState.Inactive:
            return
        
        self.state = GPSTestState.Inactive
        self.__cleanup()

    def update(self):
        if self.state == GPSTestState.Inactive or self.state == GPSTestState.Finished:
            return
        
        match self.state:
            case GPSTestState.AwaitingFirstMessage:
                self.__update_await_message()
            case GPSTestState.AwaitingNoMessage:
                self.__update_await_no_message()
            case _:
                raise Exception("Invalid state in GPS test update.")
        
        #Finished during update
        if self.state == GPSTestState.Finished:
            self.__cleanup()
    
    def __clear_messages(self):
        num_messages = self.sensor.getNumDebugMessages().data
        for _ in range(num_messages):
            self.sensor.getOldestDebugMessage()

    def __update_await_message(self):
        elapsed_time = time.perf_counter() - self.state_start_time
        num_messages = self.sensor.getNumDebugMessages().data
        for _ in range(num_messages):
            message = self.sensor.getOldestDebugMessage().data
            if "$GPGGA" in message or "$GNGGA" in message:
                self.result["gps_active"]["success"] = True
                self.result["gps_active"]["message"] = message

                #Transition to AwaitingNoMessage state
                self.sensor.writeGpsStandby(1)
                self.__clear_messages()
                self.state = GPSTestState.AwaitingNoMessage
                self.state_start_time = time.perf_counter()
                return

        
        if elapsed_time > self.EXPECTED_MESSAGE_INTERVAL + self.MESSAGE_PADDING:
            self.overall_success = False
            self.state = GPSTestState.Finished

    def __update_await_no_message(self):
        elapsed_time = time.perf_counter() - self.state_start_time
        num_messages = self.sensor.getNumDebugMessages().data
        for _ in range(num_messages):
            message = self.sensor.getOldestDebugMessage().data
            if "$GPGGA" in message:
                self.overall_success = False
                self.state = GPSTestState.Finished
                self.result["gps_standby"]["message"] = message
                return
        
        #Success is going the whole interval without receiving a GPS message
        if elapsed_time > self.EXPECTED_MESSAGE_INTERVAL + self.MESSAGE_PADDING:
            self.result["gps_standby"]["success"] = True
            self.state = GPSTestState.Finished

    def __cleanup(self):
        self.sensor.write_settings(**self.gps_settings_cache)
        self.__clear_messages()

def run_test(sensor: ThreespaceSensor):
    test = GPSTest(sensor)
    test.start()

    try:
        while test.state != GPSTestState.Finished:
            test.update()
            time.sleep(0.01)
    except KeyboardInterrupt:
        test.cancel()
        print("\nTest cancelled by user.")
        return (False if not test.overall_success else None), test.result

    return test.overall_success, test.result

def auto_run_test():
    sensor = ThreespaceSensor()
    overall_success, results = run_test(sensor)
    sensor.cleanup()
    print(f"Results: {results}")
    print(f"Overall Success: {overall_success}")
    return overall_success, results

if __name__ == "__main__":
    auto_run_test()

    