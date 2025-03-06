from yostlabs.tss3.api import ThreespaceSensor
from yostlabs.communication.ble import ThreespaceBLEComClass

auto_detect = False

if auto_detect:
    #Create a sensor by auto detecting a ThreespaceBLEComClass.
    #It does this by attempting to connect to a device with the Nordic Uart Service
    sensor = ThreespaceSensor(ThreespaceBLEComClass)
else:
    #PUT YOUR SENSORS BLE_NAME HERE
    ble_name = "YL-TSS-####" #Defaults to the lowest 4 hex digits of the sensors serial number
    print("Attempting to discover and connect to a sensor with the name:", ble_name)
    com_class = ThreespaceBLEComClass(ble_name)
    sensor = ThreespaceSensor(com_class)

ble_name = sensor.get_settings("ble_name")
print("Connected to:", ble_name)

result = sensor.getPrimaryCorrectedAccelVec()
print(result)

sensor.cleanup()