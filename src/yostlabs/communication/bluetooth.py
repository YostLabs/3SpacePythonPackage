from yostlabs.communication.socket import ThreespaceSocketComClass
import bluetooth
import time
import threading

from dataclasses import dataclass
from typing import Callable, Generator

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

    def __init__(self, interval=5):
        self.enabled = False
        self.done = True

        self.nearby = None
        self.thread = None
        self.updated = False
        self.duration = int(interval / 1.2)

    def start(self):
        if not self.done: return
        self.thread = threading.Thread(target=self.process, daemon=True)
        self.enabled = True
        self.done = False
        self.updated = False
        self.thread.start()

    def stop(self):
        self.enabled = False

    def get_most_recent(self):
        if not self.updated: return None
        self.updated = False
        return self.nearby

    @property
    def is_running(self):
        return self.done

    def process(self):
        while self.enabled:
            nearby = bluetooth.discover_devices(duration=self.duration, lookup_names=True, lookup_class=True)
            self.nearby = [ScannerResult(addr, name, decode_class_of_device(cod)) for addr, name, cod in nearby]
            self.updated = True
        self.done = True

class ThreespaceBluetoothComClass(ThreespaceSocketComClass):

    SCANNER = None

    def __init__(self, addr: str, name: str = None, connection_timeout=None):
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
        if wait_for_update:
            cls.SCANNER.updated = False
            while not cls.SCANNER.updated: time.sleep(0.1)
        if cls.SCANNER.nearby is None: return
        for device_info in cls.SCANNER.nearby:
            if not filter(device_info): continue
            name = device_info.name or None
            yield ThreespaceBluetoothComClass(device_info.address, name)


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