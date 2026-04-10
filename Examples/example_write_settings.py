from yostlabs.tss3.api import ThreespaceSensor
from yostlabs.communication.serial import ThreespaceSerialComClass
import time

#Create a sensor by auto detecting a ThreespaceSerialComClass
sensor = ThreespaceSensor(ThreespaceSerialComClass)

print("Original:")
print(sensor.read_settings("filter_mode", "led_mode", "led_rgb", "stream_hz", "stream_duration", "stream_delay"))
print()
#------------------------------------SETTING SETTINGS-------------------------------------
#You can set multiple settings at a time via Key Value pairs. The return result is an error code (0 is success) as well as the number of settings successfully set.
#If an error occurs, the sensor will stop applying any subsequent settings. Therefore, the num_successes field can be used to identify which key caused the error
err, num_successes = sensor.write_settings(filter_mode=0, led_rgb=[1, 0, 1])

#Settings can also be written directly using their function
sensor.writeLedMode(1)

#You can also set settings via a string you would normally pass directly on the command line using ascii
sensor.write_settings_ascii("stream_hz=20")
sensor.write_settings_ascii("stream_duration=200;stream_delay=2")

#Showing that the settings changed
print("Changed:")
print(sensor.read_settings("filter_mode", "led_mode", "led_rgb", "stream_hz", "stream_duration", "stream_delay"))
print()

print("Sleeping to show LED change")
print()
time.sleep(1) #This is just to give time to see the LED change
#For settable settings that have no input, such as '!default' or '!reboot', simply pass None as the type
sensor.write_settings(default=None)

#Showing they were restored
print("Defaults:")
print(sensor.read_settings("filter_mode", "led_mode", "led_rgb", "stream_hz", "stream_duration", "stream_delay"))
sensor.cleanup()