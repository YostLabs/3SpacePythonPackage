from yostlabs.communication.socket import ThreespaceSocketComClass
import time
import threading
import sys
import math

from dataclasses import dataclass
from typing import Callable, Generator

#Bluetooth is an optional install, so we need to handle the case where pybluez2 is not installed
#pip install pybluez2 (May need to do git+https://github.com/airgproducts/pybluez2.git instead for it to work)
#pybluez2 is not included as an optional dependency because the pypi installation is likely to fail.
try:
    import bluetooth
    BLUETOOTH_AVAILABLE = True
except ImportError:
    BLUETOOTH_AVAILABLE = False
    bluetooth = None
    print("pybluez2 is not installed. Bluetooth functionality will be unavailable. Install it with:\n" \
          "    pip install pybluez2\n" \
          "    pip install git+https://github.com/airgproducts/pybluez2.git (May be required for newer versions of python)")

@dataclass
class COD:
    raw: int
    services: list[str]
    major_class: str
    minor_class: str

def decode_class_of_device(cod: int) -> dict:
    """Decode a Bluetooth Class of Device (CoD) integer into its components."""

    # Service Class bit masks (11 bits total)
    services = {
        0x002000: "Limited Discoverable Mode",
        0x004000: "Positioning (Location Identification)",
        0x008000: "Networking",
        0x010000: "Rendering",
        0x020000: "Capturing",
        0x040000: "Object Transfer",
        0x080000: "Audio",
        0x100000: "Telephony",
        0x200000: "Information",
    }

    # Major Device Classes (5 bits)
    major_classes = {
        0x00: "Miscellaneous",
        0x01: "Computer",
        0x02: "Phone",
        0x03: "LAN/Network Access Point",
        0x04: "Audio/Video",
        0x05: "Peripheral",
        0x06: "Imaging",
        0x07: "Wearable",
        0x08: "Toy",
        0x09: "Health",
        0x1F: "Uncategorized",
    }

    # Minor Class mappings for some major classes (for simplicity)
    minor_classes = {
        0x01: {
            0x00: "Uncategorized",
            0x01: "Desktop workstation",
            0x02: "Server-class computer",
            0x03: "Laptop",
            0x04: "Handheld PC/PDA",
            0x05: "Palm-size PC/PDA",
        },
        0x02: {
            0x00: "Uncategorized",
            0x01: "Cellular",
            0x02: "Cordless",
            0x03: "Smartphone",
            0x04: "Wired modem or voice gateway",
            0x05: "Common ISDN access",
        },
        0x05: {
            0x00: "Uncategorized",
            0x01: "Keyboard",
            0x02: "Pointing device",
            0x03: "Combo keyboard/pointing device",
        },
    }

    # Extract fields
    service_bits = cod & 0xFFE000  # top 11 bits
    major_class = (cod >> 8) & 0x1F
    minor_class = (cod >> 2) & 0x3F

    # Decode services
    decoded_services = [name for bit, name in services.items() if service_bits & bit]

    # Decode major class
    major_name = major_classes.get(major_class, "Unknown")

    # Decode minor class (context-dependent)
    minor_name = minor_classes.get(major_class, {}).get(minor_class, f"Minor code {minor_class}")

    return COD(cod, decoded_services or ["None"], major_name, minor_name)

@dataclass
class ScannerResult:
    address: str
    name: str
    class_of_device: COD

class Scanner:

    def __init__(self, desired_scan_time=5):
        self.enabled = False
        self.done = True

        self.continous = False      #If true, will constantly update the nearby devices
        self.execute = False        #Set to true to trigger a read when not in continous mode
        self.nearby = None          #The list of nearby devices
        self.thread = None          #The thread running the asynchronous scanner
        self.updated = False        #Set to true when self.nearby has been updated
        self.duration = math.ceil(desired_scan_time / 1.28) * 1.28  #The time, in second, each scan lasts. Scanning is in 1.28 second intervals, so this may differ from the desired scan time

    def start(self):
        if not self.done:
            self.execute = True
            return
        self.thread = threading.Thread(target=self.process, daemon=True)
        self.enabled = True
        self.done = False
        self.updated = False
        self.thread.start()

    def stop(self):
        self.enabled = False

    def set_continous(self, continous: bool):
        self.continous = continous

    def get_most_recent(self):
        if not self.updated: return None
        self.updated = False
        return self.nearby

    @property
    def is_running(self):
        return self.done

    def process(self):
        if not BLUETOOTH_AVAILABLE:
            raise ImportError("pybluez2 is not installed. Install it with: pip install git+https://github.com/airgproducts/pybluez2.git")
        while self.enabled:
            if not self.continous and not self.execute: continue
            nearby = bluetooth.discover_devices(duration=math.ceil(self.duration/1.28), lookup_names=True, lookup_class=True)
            self.nearby = [ScannerResult(addr, name, decode_class_of_device(cod)) for addr, name, cod in nearby]
            self.execute = False
            self.updated = True
        self.done = True

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    # Define BLUETOOTH_ADDRESS structure
    class BLUETOOTH_ADDRESS(ctypes.Structure):
        _fields_ = [("rgBytes", ctypes.c_byte * 6)]

    # Load Bluetooth API
    win_bluetooth = ctypes.WinDLL("BluetoothAPIs.dll")

    # Define function prototype
    BluetoothRemoveDevice = win_bluetooth.BluetoothRemoveDevice
    BluetoothRemoveDevice.argtypes = [ctypes.POINTER(BLUETOOTH_ADDRESS)]
    BluetoothRemoveDevice.restype = wintypes.BOOL

    def remove_device(mac_address: str):
        # Convert MAC string "XX:XX:XX:XX:XX:XX" -> bytes reversed
        addr_bytes = bytes.fromhex(mac_address.replace(":", ""))[::-1]
        addr = BLUETOOTH_ADDRESS()
        ctypes.memmove(addr.rgBytes, addr_bytes, 6)

        err = BluetoothRemoveDevice(ctypes.byref(addr))
        print("Remove device status:", err)
        if not err:
            print(f"Successfully removed device {mac_address}")
        else:
            if err == 1168:  # ERROR_NOT_FOUND
                print(f"Device {mac_address} not found or already unpaired.")
            else:
                print(f"Failed to remove device {mac_address}. Error code: {err}")
else:
    def remove_device(mac_address: str):
        raise NotImplementedError("Only implemented on windows")

class ThreespaceBluetoothComClass(ThreespaceSocketComClass):

    SCANNER: Scanner = None

    def __init__(self, addr: str, name: str = None, connection_timeout=None):
        if not BLUETOOTH_AVAILABLE:
            raise ImportError("pybluez2 is not installed. Install it with: pip install pybluez2 (May need to do git+https://github.com/airgproducts/pybluez2.git instead for it to work)")
        super().__init__(bluetooth.BluetoothSocket(bluetooth.Protocols.RFCOMM), (addr, 1), connection_timeout=connection_timeout)
        self.address = addr
        self.__name = name or addr

    @property
    def name(self) -> str:
        return self.__name

    @classmethod
    def __lazy_init_scanner(cls):
        if cls.SCANNER is None:
            cls.SCANNER = Scanner()
            cls.SCANNER.start()

    @classmethod
    def set_scanner_continous(cls, continous: bool):
        cls.__lazy_init_scanner()
        cls.SCANNER.set_continous(continous)

    @staticmethod
    def __default_filter(result: ScannerResult):
        return result.class_of_device.major_class == "Wearable" and result.class_of_device.minor_class == "Minor code 0"

    @staticmethod
    def auto_detect(wait_for_update=True, filter: Callable[[ScannerResult],bool] = None) -> Generator["ThreespaceBluetoothComClass", None, None]:
        """
        Returns a list of com classes of the same type called on nearby
        """
        cls = ThreespaceBluetoothComClass
        cls.__lazy_init_scanner()
        if filter is None: 
            filter = cls.__default_filter
        cls.SCANNER.start()
        if wait_for_update:
            cls.SCANNER.updated = False
            while not cls.SCANNER.updated: time.sleep(0.1)
        if cls.SCANNER.nearby is None: return
        for device_info in cls.SCANNER.nearby:
            if not filter(device_info): continue
            name = device_info.name or None
            yield ThreespaceBluetoothComClass(device_info.address, name)

    @staticmethod
    def unpair(address: str):
        """
        It is recommended to call this after done with a ThreespaceBluetoothComClass object
        so that windows will not report the device as still being available when powered off.
        This occurs because windows Bluetooth detect functions report nearby devices & paired devices,
        regardless of their power status. By removing the device from the list of paired devices, auto detect
        will only report actual nearby and powered devices.
        EX: ThreespaceBluetoothComClass.unpair(com.address)
        """
        remove_device(address)


if __name__ == "__main__":
    from yostlabs.tss3.api import ThreespaceSensor

    com = None
    for device in ThreespaceBluetoothComClass.auto_detect():
        com = device

    if com is None:
        print("Failed to detect a bluetooth sensor")
        exit()
    print("Connecting to:", com.name)

    sensor = ThreespaceSensor(com, verbose=True)

    sensor.set_settings(stream_slots=0)
    print(sensor.get_settings("stream_slots", "stream_hz"))
    print(sensor.getTaredOrientation())

    sensor.startStreaming()

    start_time = time.perf_counter()
    while time.perf_counter() - start_time < 5:
        sensor.updateStreaming()
        packet = sensor.getOldestStreamingPacket()
        while packet is not None:
            print(packet)
            packet = sensor.getOldestStreamingPacket()
    sensor.stopStreaming()
    sensor.cleanup()