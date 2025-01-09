from yostlabs.tss3.api import ThreespaceSensor
from yostlabs.communication.serial import ThreespaceSerialComClass
import time

#Create a sensor by auto detecting a ThreespaceSerialComClass
sensor = ThreespaceSensor(ThreespaceSerialComClass)

print("Original:")
print(sensor.get_settings("filter_mode", "led_mode", "led_rgb", "stream_hz", "stream_duration", "stream_delay"))
print()
#------------------------------------SETTING SETTINGS-------------------------------------
#You can set multiple settings at a time via Key Value pairs. The return result is an error code (0 is success) as well as the number of settings successfully set.
#If an error occurs, the sensor will stop applying any subsequent settings. Therefore, the num_successes field can be used to identify which key caused the error
err, num_successes = sensor.set_settings(filter_mode=0, led_mode=1, led_rgb=[1, 0, 1])

#You can also set settings via a string you would normally pass directly on the command line
sensor.set_settings("stream_hz=20")
sensor.set_settings("stream_duration=200;stream_delay=2")

#Showing that the settings changed
print("Changed:")
print(sensor.get_settings("filter_mode", "led_mode", "led_rgb", "stream_hz", "stream_duration", "stream_delay"))
print()

print("Sleeping to show LED change")
print()
time.sleep(1) #This is just to give time to see the LED change
#For settable settings that have no input, such as '!default' or '!reboot', you must supply a string
sensor.set_settings("default")

#Showing they were restored
print("Defaults:")
print(sensor.get_settings("filter_mode", "led_mode", "led_rgb", "stream_hz", "stream_duration", "stream_delay"))

sensor.cleanup()