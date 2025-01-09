from yostlabs.tss3.api import ThreespaceSensor, StreamableCommands
from yostlabs.communication.serial import ThreespaceSerialComClass
import time

#Create a sensor by auto detecting a ThreespaceSerialComClass
sensor = ThreespaceSensor(ThreespaceSerialComClass)

#Setup streaming
sensor.set_settings(stream_hz=1000) #caps at 2000
sensor.set_settings(stream_delay=0, stream_duration=0) #stream instantly on command, stream forever

#To set stream slots, provide a comma seperated list of command numbers. Up to 16 commands can be streamed at once.
#Some commands may require a parameter passed to them. To do so, put a ':' after the command number and then the parameter
#The below set_settings call has an example of this.
#255 is considered an empty stream slot
stream_slots_string = f"{StreamableCommands.GetAllPrimaryCorrectedData.value},{StreamableCommands.GetTaredOrientation.value},{StreamableCommands.GetRawAccelVec.value}:{sensor.valid_accels[0]}"
sensor.set_settings(stream_slots=stream_slots_string)
print("Stream Slots:", sensor.get_settings("stream_slots"))

sensor.set_settings(header_timestamp=1) #Enable the timestamp in the header to see the time at which the message was generated
sensor.set_settings(timestamp=0) #Set the timestamp back to 0 so the messages start from 0

#Start the streaming
sensor.startStreaming()

#Get results for 5 seconds then stop
start_time = time.time()
while time.time() - start_time < 5:
    #This must be called repeatedly while streaming to allow the sensor to read in the results
    sensor.updateStreaming()

    #Check for any streaming packets that got read in and print them out.
    packet = sensor.getOldestStreamingPacket()
    while packet is not None:
        print(packet)
        packet = sensor.getOldestStreamingPacket()

sensor.stopStreaming()

sensor.cleanup()
