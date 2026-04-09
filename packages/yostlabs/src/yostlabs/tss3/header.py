from dataclasses import dataclass, field
import struct
from yostlabs.tss3.consts import *

@dataclass
class ThreespaceHeaderInfo:
    __bitfield: int = 0
    format: str = ""
    size: int = 0

    def get_start_byte(self, header_field: int):
        """
        Given a header field, give the initial byte offset for that field when
        using binary mode
        """
        if not header_field & self.__bitfield: return None #The bit is not enabled, no start byte
        #Get the index of the bit
        bit_pos = 0
        header_field >>= 1
        while header_field > 0:
            bit_pos += 1
            header_field >>= 1

        #Add up the size of everything before this field
        start = 0
        for i in range(bit_pos):
            if (1 << i) & self.__bitfield:
                start += struct.calcsize(THREESPACE_HEADER_FORMAT_CHARS[i])
        return start
    
    def get_index(self, header_field: int):
        if not header_field & self.__bitfield: return None
        index = 0
        bit = 1
        while bit < header_field:
            if bit & self.__bitfield:
                index += 1
            bit <<= 1
        return index

    def __update(self):
        self.format = "<"
        for i in range(THREESPACE_HEADER_NUM_BITS):
            if self.__bitfield & (1 << i):
                self.format += THREESPACE_HEADER_FORMAT_CHARS[i]
        self.size = struct.calcsize(self.format)

    @property
    def bitfield(self):
        return self.__bitfield
    
    @bitfield.setter
    def bitfield(self, value):
        self.__bitfield = value
        self.__update()
    
    @property
    def status_enabled(self):
        return bool(self.__bitfield & THREESPACE_HEADER_STATUS_BIT)
    
    @status_enabled.setter
    def status_enabled(self, value: bool):
        if value: self.__bitfield |= THREESPACE_HEADER_STATUS_BIT
        else: self.__bitfield &= ~THREESPACE_HEADER_STATUS_BIT
        self.__update()
    
    @property
    def timestamp_enabled(self):
        return bool(self.__bitfield & THREESPACE_HEADER_TIMESTAMP_BIT)
    
    @timestamp_enabled.setter
    def timestamp_enabled(self, value: bool):
        if value: self.__bitfield |= THREESPACE_HEADER_TIMESTAMP_BIT
        else: self.__bitfield &= ~THREESPACE_HEADER_TIMESTAMP_BIT
        self.__update()

    @property
    def echo_enabled(self):
        return bool(self.__bitfield & THREESPACE_HEADER_ECHO_BIT)
    
    @echo_enabled.setter
    def echo_enabled(self, value: bool):
        if value: self.__bitfield |= THREESPACE_HEADER_ECHO_BIT
        else: self.__bitfield &= ~THREESPACE_HEADER_ECHO_BIT
        self.__update()       

    @property
    def checksum_enabled(self):
        return bool(self.__bitfield & THREESPACE_HEADER_CHECKSUM_BIT)
    
    @checksum_enabled.setter
    def checksum_enabled(self, value: bool):
        if value: self.__bitfield |= THREESPACE_HEADER_CHECKSUM_BIT
        else: self.__bitfield &= ~THREESPACE_HEADER_CHECKSUM_BIT     
        self.__update()

    @property
    def serial_enabled(self):
        return bool(self.__bitfield & THREESPACE_HEADER_SERIAL_BIT)
    
    @serial_enabled.setter
    def serial_enabled(self, value: bool):
        if value: self.__bitfield |= THREESPACE_HEADER_SERIAL_BIT
        else: self.__bitfield &= ~THREESPACE_HEADER_SERIAL_BIT  
        self.__update()

    @property
    def length_enabled(self):
        return bool(self.__bitfield & THREESPACE_HEADER_LENGTH_BIT)
    
    @length_enabled.setter
    def length_enabled(self, value: bool):
        if value: self.__bitfield |= THREESPACE_HEADER_LENGTH_BIT
        else: self.__bitfield &= ~THREESPACE_HEADER_LENGTH_BIT      
        self.__update()   

    def get_labels(self):
        order = []
        if self.status_enabled:
            order.append("status")
        if self.timestamp_enabled:
            order.append("timestamp")
        if self.echo_enabled:
            order.append("echo")
        if self.checksum_enabled:
            order.append("checksum")
        if self.serial_enabled:
            order.append("serial#")
        if self.length_enabled:
            order.append("len")
        return order           

@dataclass
class ThreespaceHeader:
    raw: tuple = field(default=None, repr=False)

    #Order here matters
    status: int = None
    timestamp: int = None
    echo: int = None
    checksum: int = None
    serial: int = None
    length: int = None

    raw_binary: bytes = field(repr=False, default_factory=lambda: bytes([]))
    info: ThreespaceHeaderInfo = field(default_factory=lambda: ThreespaceHeaderInfo(), repr=False)

    @staticmethod
    def from_tuple(data, info: ThreespaceHeaderInfo):
        raw_expanded = []
        cur_index = 0
        for i in range(THREESPACE_HEADER_NUM_BITS):
            if info.bitfield & (1 << i): 
                raw_expanded.append(data[cur_index])
                cur_index += 1
            else:
                raw_expanded.append(None)
        return ThreespaceHeader(data, *raw_expanded, info=info)

    @staticmethod
    def from_bytes(byte_data: bytes, info: ThreespaceHeaderInfo):
        if info.size == 0: return ThreespaceHeader()
        header = ThreespaceHeader.from_tuple(struct.unpack(info.format, byte_data[:info.size]), info)
        header.raw_binary = byte_data
        return header

    def __getitem__(self, key):
        return self.raw[key]
    
    def __len__(self):
        return len(self.raw)
    
    def __iter__(self):
        return iter(self.raw)