from yostlabs.communication.base import *
import serial
import serial.tools.list_ports
from serial.tools.list_ports_common import ListPortInfo
import time

class ThreespaceSerialComClass(ThreespaceComClass):
    PID_V3_MASK = 0x3000

    VID = 0x2476

    PID_BOOTLOADER = 0x1000
    PID_EMBED = 0x3040
    PID_DL = 0x3050
    PID_LX = 0x3090

    PID_TO_STR_DICT = {
        PID_EMBED: "EM",
        PID_DL: "DL",
        PID_BOOTLOADER: "BOOT",
        PID_LX: "LX"
    }


    DEFAULT_BAUDRATE = 115200
    DEFAULT_TIMEOUT = 2

    def __init__(self, ser: serial.Serial | str):
        if isinstance(ser, serial.Serial):
            self.ser = ser
        elif isinstance(ser, str):
            self.ser = serial.Serial(None, baudrate=ThreespaceSerialComClass.DEFAULT_BAUDRATE, timeout=ThreespaceSerialComClass.DEFAULT_TIMEOUT)
            self.ser.port = ser
        else:
            raise TypeError("Invalid type for creating a ThreespaceSerialComClass:", type(ser), ser)

        self.peek_buffer = bytearray()
        self.peek_length = 0

    def write(self, bytes: bytes):
        self.ser.write(bytes)
    
    def read(self, num_bytes: int):
        if self.peek_length >= num_bytes:
            result = self.peek_buffer[:num_bytes]
            self.peek_length -= num_bytes
            del self.peek_buffer[:num_bytes]
        else:
            result = self.peek_buffer + self.ser.read(num_bytes - self.peek_length) #Must supply the amount desired to read instead of just buffering so the timeout works
            self.peek_buffer.clear()
            self.peek_length = 0
        return result
    
    def peek(self, num_bytes: int):
        if self.peek_length >= num_bytes:
            return self.peek_buffer[:num_bytes]
        else:
            self.peek_buffer += self.ser.read(num_bytes - self.peek_length) #Must supply the amount desired to read instead of just buffering so the timeout works
            self.peek_length = len(self.peek_buffer) #The read may have timed out, so calculate new size
            return self.peek_buffer.copy()
        
    def read_until(self, expected: bytes) -> bytes:
        if expected in self.peek_buffer:
            length = self.peek_buffer.index(expected) + len(expected)
            result = self.peek_buffer[:length]
            self.peek_length -= length
            del self.peek_buffer[:length]
            return result
        #Have to actually read from serial port until the data is available
        result = self.peek_buffer + self.ser.read_until(expected)
        self.peek_buffer.clear()
        self.peek_length = 0
        return result

    def peek_until(self, expected: bytes, max_length: int = None) -> bytes:
        if expected in self.peek_buffer:
            length = self.peek_buffer.index(expected) + len(expected)
            if max_length is not None and length > max_length: 
                length = max_length
            result = self.peek_buffer[:length]
            return result
        if max_length is not None and self.peek_length >= max_length:
            return self.peek_buffer[:max_length]
        
        #Have to actually read from serial port until the data is available
        if max_length is not None:
            max_length = max(0, max_length - self.peek_length)
        self.peek_buffer += self.ser.read_until(expected, size=max_length)
        self.peek_length = len(self.peek_buffer)
        return self.peek_buffer.copy()

    def close(self):
        self.ser.close()
        self.peek_buffer.clear()
        self.peek_length = 0
    
    def open(self):
        try:
            self.ser.open()
        except:
            return False
        return True

    def check_open(self):
        try:
            self.ser.in_waiting
        except:
            return False
        return True

    @property
    def length(self):
        return self.peek_length + self.ser.in_waiting    

    @property
    def timeout(self) -> float:
        return self.ser.timeout
    
    @timeout.setter
    def timeout(self, timeout: float):
        self.ser.timeout = timeout
        #There is a bug in Windows drivers that requires a delay after setting timeout
        #When using certain Serial Interfaces
        time.sleep(0.01) 

    @property
    def reenumerates(self) -> bool:
        return True
    
    @property
    def name(self) -> str:
        return self.ser.port
    
    @property
    def suffix(self) -> str:
        return self.pid_to_str(self.get_port_info().pid) 
    
    def get_port_info(self):
        ports = serial.tools.list_ports.comports()
        for port in ports:
            if port.device == self.ser.port:
                return port
        return None

    @staticmethod
    def is_threespace_port(port: ListPortInfo):
        cls = ThreespaceSerialComClass
        return port.vid == cls.VID and (port.pid & cls.PID_V3_MASK == cls.PID_V3_MASK or port.pid == cls.PID_BOOTLOADER)

    #This is not part of the ThreespaceComClass interface, but is useful as a utility for those directly using the ThreespaceSerialComClass
    @staticmethod 
    def enumerate_ports():
        cls = ThreespaceSerialComClass
        ports = serial.tools.list_ports.comports()
        for port in ports:
            if cls.is_threespace_port(port):
                yield port

    @staticmethod
    def auto_detect(default_timeout: float = 2, default_baudrate: int = 115200) -> Generator["ThreespaceSerialComClass", None, None]:
        """
        Returns a list of com classes of the same type called on nearby.
        These ports will start unopened. This allows the caller to get a list of ports without having to connect.
        """
        cls = ThreespaceSerialComClass
        ports = serial.tools.list_ports.comports()
        for port in ports:
            if cls.is_threespace_port(port):
                ser = serial.Serial(None, baudrate=default_baudrate, timeout=default_timeout) #By setting port as None, can create an object without immediately opening the port
                ser.port = port.device #Now assign the port, allowing the serial object to exist without being opened yet
                yield ThreespaceSerialComClass(ser)

    @classmethod
    def pid_to_str(cls, pid):
        if pid not in cls.PID_TO_STR_DICT: return "Unknown"
        return cls.PID_TO_STR_DICT[pid]