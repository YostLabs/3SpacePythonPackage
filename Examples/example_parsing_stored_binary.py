from yostlabs.tss3.api import ThreespaceSensor, StreamableCommands
from yostlabs.communication.serial import ThreespaceSerialComClass
from yostlabs.tss3.utils.parser import ThreespaceBinaryParser
import time

#Create a sensor by auto detecting a ThreespaceSerialComClass
sensor = ThreespaceSensor(ThreespaceSerialComClass)

#First, gather some data and store it in a file for this example
sensor.set_settings(stream_slots=f"{StreamableCommands.GetTimestamp.value},{StreamableCommands.GetAllPrimaryCorrectedData.value}")
sensor.set_settings(stream_hz=500)

print("Gathering data for 5 seconds...")
sensor.startStreaming()
start_time = time.time()
FNAME = "logged_data.bin"
file = open(FNAME, 'wb')
while time.time() - start_time < 5:
    sensor.updateStreaming()
    packet = sensor.getOldestStreamingPacket()
    while packet is not None:
        file.write(packet.raw_binary) #Store the raw binary for parsing later
        packet = sensor.getOldestStreamingPacket()
sensor.stopStreaming()

file.close()

#Create the parser
parser = ThreespaceBinaryParser(verbose=True)

#Configure it to have the same settings used so it knows how to read the data
parser.set_header(sensor.header_info)

#Register all command responses that could have been included. This includes more then streaming data,
#however because this example did streaming, it is going to register the getStreamingBatch command to parse.
#Some commands take additional KWARGS to know how to parse. Ex: getStreamingBatch requires knowing what the streaming slots were
parser.register_command(84, stream_slots=sensor.streaming_slots)

#Once all commands are registered, simply feed the parser data and attempt to retrieve messages
file = open(FNAME, "rb")

print("Parsing the gathered data from the binary file")
parser.insert_data(file.read())
msg = parser.parse_message()
while msg is not None:
    print(msg)
    msg = parser.parse_message()

file.close()