
from yostlabs.tss3.api import ThreespaceSensor
from yostlabs.communication.serial import ThreespaceSerialComClass

"""
It is possible for a board to have multiple types of the same component on it. For example there may be two accelerometers, such as a low-G and high-G component.
Because of this, each component is recognized via an ID that is constant to that specific component. To set settings for those their ids needs to be used in the key.
"""

#Create a sensor by auto detecting a ThreespaceSerialComClass
sensor = ThreespaceSensor(ThreespaceSerialComClass)

#To discover a list of valid components, send the following.
#The number after the component type is the ID of that component. The name also has the max range of each component.
print("Valid Components:")
print(sensor.get_settings("valid_components"))
print()

#The API caches these, so it is not necessary to actually send the above, their IDS can be accessed like so
print("Valid Accels:", sensor.valid_accels)
print("Valid Gyros:", sensor.valid_gyros)
print("Valid Mags:", sensor.valid_mags)
print("Valid Baros:", sensor.valid_baros)

#Then to read a value of a component you use the base setting key and append on the ID number'
def print_component_settings(base_key: str, ids: list[int]):
    for id in ids:
        key = f"{base_key}{sensor.valid_accels[0]}"
        print(f"{key}={sensor.get_settings(key)}")

print_component_settings("range_accel", sensor.valid_accels)

#An easier way to do the above if just trying to read all ranges of all accels is to use query strings
print(sensor.get_settings("{range_}"))
print(sensor.get_settings("{valid_range}"))

#Some commands also let you access individual components, those take the ID as well
if len(sensor.valid_accels) > 0:
    print(f"{sensor.getCorrectedAccelVec(sensor.valid_accels[0])=}")

sensor.cleanup()
