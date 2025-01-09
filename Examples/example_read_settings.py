from yostlabs.tss3.api import ThreespaceSensor
from yostlabs.communication.serial import ThreespaceSerialComClass

#Create a sensor by auto detecting a ThreespaceSerialComClass
sensor = ThreespaceSensor(ThreespaceSerialComClass)

#-------------------------------READING SETTINGS---------------------------

print()
print("Reading settings:")
#If only one value is passed, the result is a singular object. Results are returned as strings
checksum_enabled = sensor.get_settings("header_checksum")
print("Checksum enabled:", bool(checksum_enabled))

#If multiple values are passed, the result is a dictionary with the key as the setting key, and the value as the result
result = sensor.get_settings("debug_mode", "debug_level", "debug_module")
print("Multi-Response:", result)

#With multiple values it is the same but the response is stored still using the key that was requested. This way, you can easily see what key failed.
result = sensor.get_settings("IDontExist")
print("Error Response:", result)

#If a key is invalid in the multiple response format
result = sensor.get_settings("debug_mode", "I Dont Exist", "debug_module", "And neither do I")
print("Multi-Error:", result)


#You can also get bulk keys such as ?settings, ?all... Or querys
#NOTE: If streaming, these bulk setting types will not work via the API unless a normal singular setting is sent first in the list.
#This is because the API requires a known identifier key for the start of the message to parse it out from the streaming data,
#but these have no known static key.
print("?Settings:")
print(sensor.get_settings("settings"))
print("Query ODR")
print(sensor.get_settings("{odr}"))

sensor.cleanup()