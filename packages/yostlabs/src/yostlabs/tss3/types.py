from dataclasses import dataclass, field
from typing import TypeVar, Generic

from yostlabs.tss3.consts import *
from yostlabs.tss3.header import ThreespaceHeader, ThreespaceHeaderInfo

T = TypeVar('T')

@dataclass
class ThreespaceCmdResult(Generic[T]):
    raw: tuple = field(default=None, repr=False)

    header: ThreespaceHeader = None
    data: T = None
    raw_data: bytes = field(default=None, repr=False)

    def __init__(self, data: T, header: ThreespaceHeader, data_raw_binary: bytes = None):
        self.header = header
        self.data = data
        self.raw = (header.raw, data)
        self.raw_data = data_raw_binary

    def __getitem__(self, key):
        return self.raw[key]
    
    def __len__(self):
        return len(self.raw)
    
    def __iter__(self):
        return iter(self.raw)   
    
    @property
    def raw_binary(self):
        bin = bytearray([])
        if self.header is not None and self.header.raw_binary is not None:
            bin += self.header.raw_binary
        if self.raw_data is not None:
            bin += self.raw_data
        return bin

@dataclass
class ThreespaceBootloaderInfo:
    memstart: int
    memend: int
    pagesize: int
    bootversion: int

@dataclass
class ThreespaceHardwareVersion:
    """
    Format from serial number:
    XX iii C VV MM IIIIII
    X = Family
    i = Variation
    C = Core version
    V = Major version
    M = Minor version
    I = ID
    """
    serial_number: int

    family_id: int
    variation: int
    core_version: int
    major_revision: int
    minor_revision: int

    id: int

    @staticmethod
    def from_serial_string(serial_str: str):
        return ThreespaceHardwareVersion.from_serial_number(int(serial_str, 16))

    @staticmethod
    def from_serial_number(serial_number: int):
        family_id = (serial_number & THREESPACE_SN_FAMILY_MSK) >> THREESPACE_SN_FAMILY_POS
        variation = (serial_number & THREESPACE_SN_VARIATION_MSK) >> THREESPACE_SN_VARIATION_POS
        core_version = (serial_number & THREESPACE_SN_VERSION_MSK) >> THREESPACE_SN_VERSION_POS
        major_revision = (serial_number & THREESPACE_SN_MAJOR_REVISION_MSK) >> THREESPACE_SN_MAJOR_REVISION_POS
        minor_revision = (serial_number & THREESPACE_SN_MINOR_REVISION_MSK) >> THREESPACE_SN_MINOR_REVISION_POS
        id = (serial_number & THREESPACE_SN_INCREMENTOR_MSK) >> THREESPACE_SN_INCREMENTOR_POS

        return ThreespaceHardwareVersion(serial_number, family_id, variation, core_version, major_revision, minor_revision, id)
    
    def __str__(self):
        return f"{self.family_name} {self.variation:01X} V{self.core_version:01X}.{self.major_revision:01X}.{self.minor_revision:01X} {self.id:06X}"
    
    @property
    def family_name(self):
        return THREESPACE_SN_FAMILY_TO_NAME.get(self.family_id, "Unknown")
    
    @property
    def short_serial_number(self):
        """
        Short SN is the 32 bit version of the u64 serial number
        It is defined as the FamilyVersion (byte) << 24 | Incrementor (24 bits) 
        """
        return (self.family_id << 24) | self.id