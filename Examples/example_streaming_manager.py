from yostlabs.tss3.api import ThreespaceSensor, StreamableCommands
from yostlabs.communication.serial import ThreespaceSerialComClass
from yostlabs.tss3.utils.streaming import ThreespaceStreamingManager, ThreespaceStreamingStatus
import time

"""
The streaming manager (still has some WIP features) is intended to be used in applications to allow
multiple clients that care about streaming different data to all stream the data they desire at the rate (rate part is WIP) 
they want at the same time. It works by managing registration of streaming items and callbacks.
The DPG suite heavily utilizes this tool.

This is a basic example that only utilizes the basic features.
To see a more complex use of this tool, please look at the source for the open source DPG suite
"""

#Create a sensor by auto detecting a ThreespaceSerialComClass
sensor = ThreespaceSensor(ThreespaceSerialComClass)

manager = ThreespaceStreamingManager(sensor)

#Everything is registered to an owner object that way something that way each client
#can only register a command and unregister it once
client = object()
manager.register_command(client, StreamableCommands.GetPrimaryCorrectedAccelVec)
manager.register_command(client, StreamableCommands.GetTimestamp)

def stream_callback(status: ThreespaceStreamingStatus):
    if status == ThreespaceStreamingStatus.Data:
        #Can either get the entire last streaming response
        response = manager.get_last_response()

        #Or by type.
        accel = manager.get_value(StreamableCommands.GetPrimaryCorrectedAccelVec)
        timestamp = manager.get_value(StreamableCommands.GetTimestamp)

        #The by type method is to be preferred, as if any other client has registered other commands, this
        #callback would not know how to parse the data part of the response. But requesting by type removes that issue.

        #If the header is desired, get the last response and retrieve its header.
        header = manager.get_last_response().header

        print(f"{timestamp=} {accel=} {header}")
    

manager.register_callback(stream_callback, hz=200) #Stream the data at 200HZ
manager.enable()

#Get results for 5 seconds then stop
start_time = time.time()
while time.time() - start_time < 5:
    manager.update() #Must call the managers update similar to calling sensor.updateStreaming

manager.disable()

sensor.cleanup()
