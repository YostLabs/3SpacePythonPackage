from yostlabs.tss3.api import ThreespaceSensor
from yostlabs.communication.serial import ThreespaceSerialComClass
from yostlabs.tss3.utils.version import ThreespaceFirmwareUploader

#Create a sensor by auto detecting a ThreespaceSerialComClass
sensor = ThreespaceSensor(ThreespaceSerialComClass)

uploader = ThreespaceFirmwareUploader(sensor, verbose=True)
uploader.set_firmware_path("embedded_2024_dec_20.xml")

def upload_callback(percent: float):
    print(f"Percent Done: {percent:.2f}%")

#Unnecessary, but nice to use
uploader.set_percent_callback(upload_callback)
uploader.upload_firmware()

#Even after uploading the connection is maintained
print(sensor.getPrimaryCorrectedAccelVec())

sensor.cleanup()