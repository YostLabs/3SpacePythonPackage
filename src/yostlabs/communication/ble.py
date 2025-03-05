import asyncio
import async_timeout
import time
from bleak import BleakScanner, BleakClient
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.backends.characteristic import BleakGATTCharacteristic

#Services
NORDIC_UART_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"

#Characteristics
NORDIC_UART_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NORDIC_UART_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

DEVICE_NAME_UUID = "00002a00-0000-1000-8000-00805f9b34fb"
APPEARANCE_UUID = "00002a01-0000-1000-8000-00805f9b34fb"

FIRMWARE_REVISION_STRING_UUID = "00002a26-0000-1000-8000-00805f9b34fb"
HARDWARE_REVISION_STRING_UUID = "00002a27-0000-1000-8000-00805f9b34fb"
SERIAL_NUMBER_STRING_UUID = "00002a25-0000-1000-8000-00805f9b34fb"
MANUFACTURER_NAME_STRING_UUID = "00002a29-0000-1000-8000-00805f9b34fb"

from yostlabs.communication.base import *
class ThreespaceBLEComClass(ThreespaceComClass):

    DEFAULT_TIMEOUT = 2

    def __init__(self, ble: BleakClient | BLEDevice | str):
        if isinstance(ble, BleakClient):    #Actual client
            self.client = ble
            self.__name = ble.address
        elif isinstance(ble, str): #Address string
            self.client = BleakClient(ble)
            self.__name = self.client.address
        elif isinstance(ble, BLEDevice):
            self.client = BleakClient(ble)
            self.__name = ble.name #Use the local name instead of the address
        else:
            raise TypeError("Invalid type for creating a ThreespaceBLEComClass:", type(ble), ble)

        self.__timeout = self.DEFAULT_TIMEOUT

        self.buffer = bytearray()
        self.event_loop = asyncio.new_event_loop()
        self.data_read_event = asyncio.Event()

        #Default to 20, will update on open
        self.max_packet_size = 20

    async def __async_open(self):
        await self.client.connect()
        await self.client.start_notify(NORDIC_UART_TX_UUID, self.__on_data_received)

    def open(self):
        if self.check_open(): return
        self.event_loop.run_until_complete(self.__async_open())
        self.max_packet_size = self.client.mtu_size - 3 #-3 to account for the opcode and attribute handle stored in the data packet

    def close(self):
        if not self.check_open(): return
        self.event_loop.run_until_complete(self.client.disconnect())
        self.buffer.clear()

    def check_open(self):
        return self.client.is_connected

    #Bleak does run a thread to read data on notification after calling start_notify, however on notification
    #it schedules a callback using loop.call_soon_threadsafe() so the actual notification can't happen unless we
    #run the event loop. Therefore, this async function that does nothing is used just to trigger an event loop updated
    #so the read callbacks __on_data_received can occur
    @staticmethod
    async def __wait_for_callbacks_async():
        pass

    def __read_all_data(self):
        self.event_loop.run_until_complete(self.__wait_for_callbacks_async())

    def __on_data_received(self, sender: BleakGATTCharacteristic, data: bytearray):
        self.buffer += data
        self.data_read_event.set()

    def write(self, bytes: bytes):
        start_index = 0
        while start_index < len(bytes):
            end_index = min(len(bytes), start_index + self.max_packet_size) #Can only send max_packet_size data per call to write_gatt_char
            self.event_loop.run_until_complete(self.client.write_gatt_char(NORDIC_UART_RX_UUID, bytes[start_index:end_index], response=False))
            start_index = end_index
    
    async def __await_read(self, timeout_time: int):
        self.data_read_event.clear()
        try:
            async with async_timeout.timeout_at(timeout_time):
                await self.data_read_event.wait()
            return True
        except:
            return False

    async def __await_num_bytes(self, num_bytes: int):
        start_time = time.time()
        while len(self.buffer) < num_bytes and time.time() - start_time < self.timeout:
            await self.__await_read(start_time + self.timeout)

    def read(self, num_bytes: int):
        self.event_loop.run_until_complete(self.__await_num_bytes(num_bytes))
        num_bytes = min(num_bytes, len(self.buffer))
        data = self.buffer[:num_bytes]
        del self.buffer[:num_bytes]
        return data

    def peek(self, num_bytes: int):
        self.event_loop.run_until_complete(self.__await_num_bytes(num_bytes))
        num_bytes = min(num_bytes, len(self.buffer))
        data = self.buffer[:num_bytes]
        return data        
    
    #Reads until the pattern is received, max_length is exceeded, or timeout occurs
    async def __await_pattern(self, pattern: bytes, max_length: int = None):
        if max_length is None: max_length = float('inf')
        start_time = time.time()
        while pattern not in self.buffer and time.time() - start_time < self.timeout and len(self.buffer) < max_length:
            await self.__await_read(start_time + self.timeout)
        return pattern in self.buffer

    def read_until(self, expected: bytes) -> bytes:
        self.event_loop.run_until_complete(self.__await_pattern(expected))
        if expected in self.buffer: #Found the pattern
            length = self.buffer.index(expected) + len(expected)
            result = self.buffer[:length]
            del self.buffer[:length]
            return result
        #Failed to find the pattern, just return whatever is there
        result = self.buffer.copy()
        self.buffer.clear()
        return result

    def peek_until(self, expected: bytes, max_length: int = None) -> bytes:
        self.event_loop.run_until_complete(self.__await_pattern(expected, max_length=max_length))
        if expected in self.buffer:
            length = self.buffer.index(expected) + len(expected)
        else:
            length = len(self.buffer)

        if max_length is not None and length > max_length:
            length = max_length

        return self.buffer[:length]

    @property
    def length(self):
        self.__read_all_data() #Gotta update the data before knowing the length
        return len(self.buffer) 

    @property
    def timeout(self) -> float:
        return self.__timeout
    
    @timeout.setter
    def timeout(self, timeout: float):
        self.__timeout = timeout    

    @property
    def reenumerates(self) -> bool:
        return False
    
    @property
    def name(self) -> str:
        return self.__name

    SCANNER = None
    SCANNER_EVENT_LOOP = None

    SCANNER_CONTINOUS = False   #Controls if scanning will continously run
    SCANNER_TIMEOUT = 5         #Controls the scanners timeout
    SCANNER_FIND_COUNT = 1      #When continous=False, will stop scanning after at least this many devices are found. Set to None to search the entire timeout.
    SCANNER_EXPIRATION_TIME = 5 #Controls the timeout for detected BLE sensors. If a sensor hasn't been detected again in this amount of time, its removed from discovered devices

    #Format: Address - dict = { device: ..., adv: ..., last_found: ... }
    discovered_devices: dict[str,dict] = {}

    @classmethod
    def __lazy_init_scanner(cls):
        if cls.SCANNER is None:
            cls.SCANNER = BleakScanner(detection_callback=cls.__detection_callback, service_uuids=[NORDIC_UART_SERVICE_UUID])
            cls.SCANNER_EVENT_LOOP = asyncio.new_event_loop()

    @classmethod
    def __detection_callback(cls, device: BLEDevice, adv: AdvertisementData):
        cls.discovered_devices[device.address] = {"device": device, "adv": adv, "last_found": time.time()}
    
    @classmethod
    def set_scanner_continous(cls, continous: bool):
        """
        If not using continous mode, functions like update_nearby_devices and auto_detect are blocking with the following rules:
        - Will search for at most SCANNER_TIMEOUT time
        - Will stop searching immediately once SCANNER_FIND_COUNT is reached

        If using continous mode, no scanning functions are blocking. However, the user must continously call 
        update_nearby_devices to ensure up to date information.
        """
        cls.__lazy_init_scanner()
        cls.SCANNER_CONTINOUS = continous
        if continous: cls.SCANNER_EVENT_LOOP.run_until_complete(cls.SCANNER.start())
        else: cls.SCANNER_EVENT_LOOP.run_until_complete(cls.SCANNER.stop())

    @classmethod
    def update_nearby_devices(cls):
        """
        Updates ThreespaceBLEComClass.discovered_devices using the current configuration.
        """
        cls.__lazy_init_scanner()
        if cls.SCANNER_CONTINOUS:
            #Allow the callbacks for nearby devices to trigger
            cls.SCANNER_EVENT_LOOP.run_until_complete(cls.__wait_for_callbacks_async())
            #Remove expired devices
            cur_time = time.time()
            to_remove = [] #Avoiding concurrent list modification
            for device in cls.discovered_devices:
                if cur_time - cls.discovered_devices[device]["last_found"] > cls.SCANNER_EXPIRATION_TIME:
                    to_remove.append(device) 
            for device in to_remove:
                del cls.discovered_devices[device]

        else:
            #Mark all devices as invalid before searching for nearby devices
            cls.discovered_devices.clear()
            start_time = time.time()
            end_time = cls.SCANNER_TIMEOUT or float('inf')
            end_count = cls.SCANNER_FIND_COUNT or float('inf')
            cls.SCANNER_EVENT_LOOP.run_until_complete(cls.SCANNER.start())
            while time.time() - start_time < end_time and len(cls.discovered_devices) < end_count:
                cls.SCANNER_EVENT_LOOP.run_until_complete(cls.__wait_for_callbacks_async())
            cls.SCANNER_EVENT_LOOP.run_until_complete(cls.SCANNER.stop())
        
        return cls.discovered_devices
    
    @classmethod
    def get_discovered_nearby_devices(cls):
        """
        A helper to get a copy of the discovered devices
        """
        return cls.discovered_devices.copy()

    @staticmethod
    def auto_detect() -> Generator["ThreespaceBLEComClass", None, None]:
        """
        Returns a list of com classes of the same type called on nearby.
        These ports will start unopened. This allows the caller to get a list of ports without having to connect.
        """
        cls = ThreespaceBLEComClass
        cls.update_nearby_devices()
        for device_info in cls.discovered_devices.values():
            yield(ThreespaceBLEComClass(device_info["device"]))
