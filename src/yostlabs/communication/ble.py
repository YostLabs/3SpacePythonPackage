import asyncio
import async_timeout
import time
from bleak import BleakScanner, BleakClient
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.exc import BleakDeviceNotFoundError

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

class TssBLENoConnectionError(Exception): ...

from yostlabs.communication.base import *
class ThreespaceBLEComClass(ThreespaceComClass):

    DEFAULT_TIMEOUT = 2

    def __init__(self, ble: BleakClient | BLEDevice | str, discover_name: bool = True, discovery_timeout=5, error_on_disconnect=True):
        """
        Parameters
        ----------
        ble : Can be either a BleakClient, BleakDevice, MacAddress String, or localName string
        discover_name : If true, a string ble parameter is interpreted as a localName, else as a MacAddress
        discovery_timeout : Max amount of time in seconds to discover the BLE device for the corresponding MacAddress/localName
        error_on_disconnect : If trying to read while the sensor is disconnected, an exception will be generated. This may be undesirable \
        if it is expected that the sensor will frequently go in and out of range and the user wishes to preserve data (such as streaming)
        """
        bleak_options = { "timeout": discovery_timeout, "disconnected_callback": self.__on_disconnect }
        if isinstance(ble, BleakClient):    #Actual client
            self.client = ble
            self.__name = ble.address
        elif isinstance(ble, str): 
            if discover_name: #Local Name stirng
                self.__lazy_init_scanner()
                device = ThreespaceBLEComClass.SCANNER_EVENT_LOOP.run_until_complete(BleakScanner.find_device_by_name(ble, timeout=discovery_timeout))
                if device is None:
                    raise BleakDeviceNotFoundError(ble)
                self.client = BleakClient(device, **bleak_options)
                self.__name = ble
            else: #Address string
                self.client = BleakClient(ble, **bleak_options)
                self.__name = self.client.address
        elif isinstance(ble, BLEDevice):
            self.client = BleakClient(ble, **bleak_options)
            self.__name = ble.name #Use the local name instead of the address
        else:
            raise TypeError("Invalid type for creating a ThreespaceBLEComClass:", type(ble), ble)

        self.__timeout = self.DEFAULT_TIMEOUT

        self.buffer = bytearray()
        self.event_loop: asyncio.AbstractEventLoop = None
        self.data_read_event: asyncio.Event = None

        #Default to 20, will update on open
        self.max_packet_size = 20
        
        self.error_on_disconnect = error_on_disconnect
        #is_connected is different from open.
        #check_open() should return is_connected as that is what the user likely wants.
        #open is whether or not the client will auto connect to the device when rediscovered.
        #This file is set up to automatically close the connection if a method is called and is_connected is False
        #This behavior might be specific to Windows.
        self.__opened = False
        #client.is_connected is really slow (noticeable when called in bulk, which happens do to the assert_connected)... 
        #So instead using the disconnected callback and this variable to manage tracking the state without the delay
        self.__connected = False
        #Writing functions will naturally throw an exception if disconnected. Reading ones don't because they use notifications rather
        #then direct reads. This means reading functions will need to assert the connection status but writing does not.

    async def __async_open(self):
        await self.client.connect()
        await self.client.start_notify(NORDIC_UART_TX_UUID, self.__on_data_received)

    def open(self):
        #If trying to open while already open, this infinitely loops
        if self.__opened: 
            if not self.__connected and self.error_on_disconnect:
                self.close()
            return
        self.event_loop = asyncio.new_event_loop()
        self.data_read_event = asyncio.Event()
        self.event_loop.run_until_complete(self.__async_open())
        self.max_packet_size = self.client.mtu_size - 3 #-3 to account for the opcode and attribute handle stored in the data packet
        self.__opened = True
        self.__connected = True

    async def __async_close(self):
        #There appears to be a bug where if you call close too soon after is_connected returns false,
        #the disconnect call will hang on Windows. It seems similar to this issue: https://github.com/hbldh/bleak/issues/1359
        await asyncio.sleep(0.5)
        await self.client.disconnect()

    def close(self):
        if not self.__opened: return
        self.event_loop.run_until_complete(self.__async_close())
        self.buffer.clear()
        self.event_loop.close()
        self.event_loop = None
        self.data_read_event = None
        self.__opened = False

    def __on_disconnect(self, client: BleakClient):
        self.__connected = False

    #Goal is that this is always called after something that would have already performed an async callback
    #to prevent needing to run the event loop. Running the event loop frequently is slow. Which is also why this
    #comclass will eventually have a threaded asyncio version.
    def __assert_connected(self):
        if not self.__connected and self.error_on_disconnect:
            raise TssBLENoConnectionError(f"{self.name} is not connected")

    def check_open(self):
        #Checking this, while slow, isn't much difference in speed as allowing the disconnect callback to update via
        #running the empty async function. So just going to use this here. Repeated calls to check_open are not a good
        #idea from a speed perspective until a fix is found. We will probably make a version of this BLEComClass that uses
        #a background thread for asyncio to allow for speed increases.
        self.__connected = self.client.is_connected
        if not self.__connected and self.__opened and self.error_on_disconnect:
            self.close()
        return self.__connected

    #Bleak does run a thread to read data on notification after calling start_notify, however on notification
    #it schedules a callback using loop.call_soon_threadsafe() so the actual notification can't happen unless we
    #run the event loop. Therefore, this async function that does nothing is used just to trigger an event loop updated
    #so the read callbacks __on_data_received can occur
    @staticmethod
    async def __wait_for_callbacks_async():
        pass

    def __read_all_data(self):
        self.event_loop.run_until_complete(self.__wait_for_callbacks_async())
        self.data_read_event.clear()
        self.__assert_connected()

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
        self.__assert_connected()
        try:
            async with async_timeout.timeout_at(timeout_time):
                await self.data_read_event.wait()
            self.data_read_event.clear()
            return True
        except:
            return False

    async def __await_num_bytes(self, num_bytes: int):
        start_time = self.event_loop.time()
        while len(self.buffer) < num_bytes and self.event_loop.time() - start_time < self.timeout:
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
        start_time = self.event_loop.time()
        while pattern not in self.buffer and self.event_loop.time() - start_time < self.timeout and len(self.buffer) < max_length:
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
    def name(self) -> str | None:
        """
        The name of the device. This may be the Address or the Local Name of the device
        depending on how discovery was done.
        May also be None
        """
        return self.__name
    
    @property
    def address(self) -> str:
        return self.client.address    

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
