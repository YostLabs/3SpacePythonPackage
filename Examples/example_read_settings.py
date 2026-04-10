from yostlabs.tss3 import ThreespaceSensor, InvalidKeyError
from yostlabs.communication.serial import ThreespaceSerialComClass

#Create a sensor by auto detecting a ThreespaceSerialComClass
sensor = ThreespaceSensor(ThreespaceSerialComClass)

#-------------------------------READING SETTINGS---------------------------

print()
print("Reading settings:")
#Reading a singular setting and accessing its result directly
checksum_enabled = sensor.read_settings("header_checksum")["header_checksum"]
print("Checksum enabled:", bool(checksum_enabled))

#Multiple keys can be specified to all be read at once, each with an entry in the returned dict
result = sensor.read_settings("debug_mode", "debug_level", "debug_module")
print("Multi-Response:", result)

#Attempting to read a key that does not exist will cause an exception to be thrown
try:
    result = sensor.read_settings("IDontExist")
except InvalidKeyError as e:
    print(e)

#If a key is invalid in the multiple response format, the valid keys BEFORE the invalid key will
#still return their values in the result field of the exception. This can be used to determine which
#key failed based on which key is the last to return a value.
try:
    result = sensor.read_settings("debug_mode", "I Dont Exist", "debug_module", "And neither do I")
except InvalidKeyError as e:
    print("Multi-Error:", e.result)


#You can also get bulk keys such as ?settings, ?all... Or querys
print("?Settings:")
print(sensor.read_settings("settings"))
print("Query ODR")
print(sensor.read_settings("{odr}"))

sensor.cleanup()