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

@dataclass(frozen=True, order=True)
class ThreespaceBoardRevision:
    """
    A board's revision. Compares in order: core, then major, then minor, each as stored in the serial number.
    core: 1 hex digit. major: 2 hex digits, the second always 0-9. minor: 2 hex digits, the first always a-f, or 00.
    Shown as {core}.{major}{minor}, the major without its leading zero and the minor without its trailing zero:
    3 01 a0 is 3.1a, 3 05 c0 is 3.5c, 3 00 00 is 3.0. Minors past f continue as f1, f2, ... (g = f1, h = f2, i = f3).
    """
    core: int
    major: int
    minor: int

    def __str__(self):
        minor = "" if self.minor == 0 else f"{self.minor:02x}".removesuffix("0")
        return f"{self.core:x}.{self.major:x}{minor}"

    @property
    def is_valid(self):
        """
        Whether the major and minor follow the rules above, which keep the label readable. Not checked on creation:
        a revision read from a serial number that was written wrong (Ex: a blank one) may not follow them.
        """
        return (self.major & 0xF) <= 9 and (self.minor == 0 or self.minor >> 4 >= 0xA)

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
    board_revision: ThreespaceBoardRevision

    id: int

    @staticmethod
    def from_serial_string(serial_str: str):
        return ThreespaceHardwareVersion.from_serial_number(int(serial_str, 16))

    @staticmethod
    def from_serial_number(serial_number: int):
        family_id = ((serial_number & THREESPACE_SN_FAMILY_MSK) >> THREESPACE_SN_FAMILY_POS) & 0x7F
        variation = (serial_number & THREESPACE_SN_VARIATION_MSK) >> THREESPACE_SN_VARIATION_POS
        core_version = (serial_number & THREESPACE_SN_VERSION_MSK) >> THREESPACE_SN_VERSION_POS
        major_revision = (serial_number & THREESPACE_SN_MAJOR_REVISION_MSK) >> THREESPACE_SN_MAJOR_REVISION_POS
        minor_revision = (serial_number & THREESPACE_SN_MINOR_REVISION_MSK) >> THREESPACE_SN_MINOR_REVISION_POS
        id = (serial_number & THREESPACE_SN_INCREMENTOR_MSK) >> THREESPACE_SN_INCREMENTOR_POS

        return ThreespaceHardwareVersion(serial_number, family_id, variation,
                                         ThreespaceBoardRevision(core_version, major_revision, minor_revision), id)
    
    def __str__(self):
        return f"{self.family_name} {self.variation:01X} V{self.core_version:01X}.{self.major_revision:01X}.{self.minor_revision:01X} {self.id:06X}"
    
    @property
    def family_name(self):
        family = THREESPACE_SN_FAMILY_TO_NAME.get(self.family_id, "Unknown")
        if family == "DEV":
            family = "Unknown"
        return family

    #The board revision's parts, from before it was its own type
    @property
    def core_version(self):
        return self.board_revision.core

    @property
    def major_revision(self):
        return self.board_revision.major

    @property
    def minor_revision(self):
        return self.board_revision.minor

    @property
    def short_serial_number(self):
        """
        Short SN is the 32 bit version of the u64 serial number
        It is defined as the FamilyVersion (byte) << 24 | Incrementor (24 bits) 
        """
        return (self.family_id << 24) | self.id