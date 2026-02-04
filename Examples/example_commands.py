from yostlabs.tss3.api import ThreespaceSensor
from yostlabs.communication.serial import ThreespaceSerialComClass

#Create a sensor by auto detecting a ThreespaceSerialComClass
sensor = ThreespaceSensor(ThreespaceSerialComClass)

result = sensor.getPrimaryCorrectedAccelVec()

#Read out the result
accel_vec = result.data

#The result is a dataclass that allows easily accessing 
# individual parts of the data and multiple ways of interpreting it
print("Result:")
#The base ThreespaceCmdResult dataclass
print(result)
#The actual byte response from the sensor that made this result (header + data)
print(result.raw_binary)
print()
print("Header:")
#The ThreespaceHeader data class
print(result.header)
#The individual components of the header as an array
#similar to how the old API functioned
print(result.header.raw)
#The actual byte representation of the header from the sensor
print(result.header.raw_binary)
print()
print("Data:")
print(f"{result.data=}")
print(f"{result.raw_data=}")

sensor.cleanup()