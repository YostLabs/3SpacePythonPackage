from yostlabs.tss3.consts import *
from yostlabs.communication.base import ThreespaceInputStream, ThreespaceOutputStream, ThreespaceComClass
from yostlabs.communication.serial import ThreespaceSerialComClass

from enum import Enum
from dataclasses import dataclass, field
from typing import TypeVar, Generic
from collections.abc import Callable
import struct
import types
import inspect
import time
import math


#For converting from internal format specifiers to struct module specifiers
__3space_format_conversion_dictionary = {
    'f': {"c": 'f', "size": 4},
    'd' : {"c": 'd', "size": 8},

    'b' : {"c": 'B', "size": 1},
    'B' : {"c": 'H', "size": 2},
    "u" : {"c": 'L', "size": 4},
    "U" : {"c": 'Q', "size": 8},

    "i" : {"c": 'b', "size": 1},
    "I" : {"c": 'h', "size": 2},
    "l" : {"c": 'l', "size": 4},
    "L" : {"c": 'q', "size": 8},

    #Strings actually don't convert, they need handled special because
    #struct unpack assumes static length strings, whereas the sensors
    #use variable length null terminated strings
    "s" : {"c": 's', "size": float('nan')},
    "S" : {"c": 's', "size": float('nan')}
}

def _3space_format_get_size(format_str: str):
    size = 0
    for c in format_str:
        size += __3space_format_conversion_dictionary[c]["size"]
    return size

def _3space_format_to_external(format_str: str):
    return ''.join(__3space_format_conversion_dictionary[c]['c'] for c in format_str)

@dataclass
class ThreespaceCommandInfo:
    name: str
    num: int
    in_format: str
    out_format: str

    num_out_params: int = field(init=False)
    out_size: int = field(init=False,)

    def __post_init__(self):
        self.num_out_params = len(self.out_format)
        self.out_size = _3space_format_get_size(self.out_format)

class ThreespaceCommand:

    BINARY_START_BYTE = 0xf7
    BINARY_START_BYTE_HEADER = 0xf9

    def __init__(self, name: str, num: int, in_format: str, out_format: str):
        self.info = ThreespaceCommandInfo(name, num, in_format, out_format)
        self.in_format = _3space_format_to_external(self.info.in_format)
        self.out_format = _3space_format_to_external(self.info.out_format)

    def format_cmd(self, *args, header_enabled=False):
        cmd_data = struct.pack("<B", self.info.num)
        for i, c in enumerate(self.in_format):
            if c != 's':
                cmd_data += struct.pack(f"<{c}", args[i])
            else:
                cmd_data += struct.pack(f"<{len(args[i])}sb", bytes(args[i], 'ascii'), 0)
        checksum = sum(cmd_data) % 256
        start_byte = ThreespaceCommand.BINARY_START_BYTE_HEADER if header_enabled else ThreespaceCommand.BINARY_START_BYTE
        return struct.pack(f"<B{len(cmd_data)}sB", start_byte, cmd_data, checksum)

    def send_command(self, com: ThreespaceOutputStream, *args, header_enabled = False):
        cmd = self.format_cmd(*args, header_enabled=header_enabled)
        com.write(cmd)

    #Read the command result from an already read buffer. This will modify the given buffer to remove
    #that data as well
    def parse_response(self, response: bytes):
        if self.info.num_out_params == 0: return None
        output = []
        
        if math.isnan(self.info.out_size): #Has strings in it, must slow parse
            for c in self.out_format:
                if c != 's':
                    format_str = f"<{c}"
                    size = struct.calcsize(format_str)
                    output.append(struct.unpack(format_str, response[:size])[0])
                    #TODO: Switch to using numpy views instead of slicing
                    response = response[size:]
                else: #Strings are special, find the null terminator
                    str_len = response.index(0)
                    output.append(struct.unpack(f"<{str_len}s", response[str_len])[0])
                    response = response[str_len + 1:] #+1 to skip past the null terminator character too
        else: #Fast parse because no strings
            output.extend(struct.unpack(f"<{self.out_format}", response[:self.info.out_size]))

        
        if self.info.num_out_params == 1:
            return output[0]
        return output

    #Read the command dynamically from an input stream
    def read_command(self, com: ThreespaceInputStream):
        raw = bytearray([])
        if self.info.num_out_params == 0: return None, raw
        output = []
        for c in self.out_format:
            if c != 's':
                format_str = f"<{c}"
                size = struct.calcsize(format_str)
                response = com.read(size)
                raw += response
                if len(response) != size:
                    print(f"Failed to read {c} type. Aborting...")
                    return None
                output.append(struct.unpack(format_str, response)[0])
            else: #Strings are special, find the null terminator
                response = com.read(1)
                raw += response
                if len(response) != 1:
                    print(f"Failed to read string. Aborting...")
                    return None
                byte = chr(response[0])
                string = ""
                while byte != '\0':
                    string += byte
                    #Get next byte
                    response = com.read(1)
                    raw += response
                    if len(response) != 1:
                        print(f"Failed to read string. Aborting...")
                        return None
                    byte = chr(response[0])
                output.append(string)
        
        if self.info.num_out_params == 1:
            return output[0], raw
        return output, raw

class ThreespaceGetStreamingBatchCommand(ThreespaceCommand):

    def __init__(self, streaming_slots: list[ThreespaceCommand]):
        self.commands = streaming_slots
        combined_out_format = ''.join(slot.info.out_format for slot in streaming_slots if slot is not None)
        super().__init__("getStreamingBatch", THREESPACE_GET_STREAMING_BATCH_COMMAND_NUM, "", combined_out_format)
        self.out_format = ''.join(slot.out_format for slot in streaming_slots if slot is not None)

    def set_stream_slots(self, streaming_slots: list[ThreespaceCommand]):
        self.commands = streaming_slots
        self.out_format = ''.join(slot.out_format for slot in streaming_slots if slot is not None)
        self.info.out_size = struct.calcsize(f"<{self.out_format}")

    def parse_response(self, response: bytes):
        data = []
        for command in self.commands:
            if command is None: continue
            cmd_response_size = command.info.out_size
            data.append(command.parse_response(response))
            response = response[cmd_response_size:]
        
        return data
    
    def read_command(self, com: ThreespaceInputStream):
        #Get the response to all the streaming commands
        response = []
        raw_response = bytearray([])
        for command in self.commands:
            if command is None: continue
            binary = com.read(command.info.out_size)
            raw_response += binary
            out = command.parse_response(binary)
            response.append(out)
        
        return response, raw_response

THREESPACE_HEADER_FORMAT_CHARS = ['b', 'L', 'B', 'B', 'L', 'H']

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

class StreamableCommands(Enum):
    GetTaredOrientation = 0
    GetTaredOrientationAsEuler = 1
    GetTaredOrientationAsMatrix = 2
    GetTaredOrientationAsAxisAngle = 3
    GetTaredOrientationAsTwoVector = 4

    GetDifferenceQuaternion = 5

    GetUntaredOrientation = 6
    GetUntaredOrientationAsEuler = 7
    GetUntaredOrientationAsMatrix = 8
    GetUntaredOrientationAsAxisAngle = 9
    GetUntaredOrientationAsTwoVector = 10

    GetTaredOrientationAsTwoVectorSensorFrame = 11
    GetUntaredOrientationAsTwoVectorSensorFrame = 12

    GetPrimaryBarometerPressure = 13
    GetPrimaryBarometerAltitude = 14
    GetBarometerAltitudeById = 15
    GetBarometerPressureById = 16

    GetAllPrimaryNormalizedData = 32
    GetPrimaryNormalizedGyroRate = 33
    GetPrimaryNormalizedAccelVec = 34
    GetPrimaryNormalizedMagVec = 35
    
    GetAllPrimaryCorrectedData = 37
    GetPrimaryCorrectedGyroRate = 38
    GetPrimaryCorrectedAccelVec = 39
    GetPrimaryCorrectedMagVec = 40

    GetPrimaryGlobalLinearAccel = 41
    GetPrimaryLocalLinearAccel = 42

    GetTemperatureCelsius = 43
    GetTemperatureFahrenheit = 44
    GetMotionlessConfidenceFactor = 45

    GetNormalizedGyroRate = 51
    GetNormalizedAccelVec = 52
    GetNormalizedMagVec = 53

    GetCorrectedGyroRate = 54
    GetCorrectedAccelVec = 55
    GetCorrectedMagVec = 56

    GetRawGyroRate = 65
    GetRawAccelVec = 66
    GetRawMagVec = 67

    GetEeptsOldestStep = 70
    GetEeptsNewestStep = 71
    GetEeptsNumStepsAvailable = 72

    GetTimestamp = 94

    GetBatteryVoltage = 201
    GetBatteryPercent = 202
    GetBatteryStatus = 203

    GetGpsCoord = 215
    GetGpsAltitude = 216
    GetGpsFixState = 217
    GetGpsHdop = 218
    GetGpsSattelites = 219

    GetButtonState = 250

THREESPACE_AWAIT_COMMAND_FOUND = 0
THREESPACE_AWAIT_COMMAND_TIMEOUT = 1
THREESPACE_AWAIT_BOOTLOADER = 2

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

#Required for the API to work. The API will attempt to keep these enabled at all times.
THREESPACE_REQUIRED_HEADER = THREESPACE_HEADER_ECHO_BIT | THREESPACE_HEADER_CHECKSUM_BIT | THREESPACE_HEADER_LENGTH_BIT
class ThreespaceSensor:
    
    def __init__(self, com = None, timeout=2):
        if com is None: #Default to attempting to use the serial com class if none is provided
            com = ThreespaceSerialComClass
        
        #Auto discover using the supplied com class type
        if inspect.isclass(com) and issubclass(com, ThreespaceComClass):
            new_com = None
            for serial_com in com.auto_detect():
                new_com = serial_com
                break #Exit after getting 1
            if new_com is None:
                raise RuntimeError("Failed to auto discover com port")
            self.com = new_com
            self.com.open()
        #The supplied com already was a com class, nothing to do
        elif inspect.isclass(type(com)) and issubclass(type(com), ThreespaceComClass):
            self.com = com
        else: #Unknown type, try making a ThreespaceSerialComClass out of this
            try:
                self.com = ThreespaceSerialComClass(com)
            except:
                raise ValueError("Failed to create default ThreespaceSerialComClass from parameter:", type(com), com)

        self.commands: list[ThreespaceCommand] = [None] * 256
        self.getStreamingBatchCommand: ThreespaceGetStreamingBatchCommand = None
        self.funcs = {}
        for command in _threespace_commands:
            #Some commands are special and need added specially
            if command.info.num == THREESPACE_GET_STREAMING_BATCH_COMMAND_NUM:
                self.getStreamingBatchCommand = ThreespaceGetStreamingBatchCommand([])
                command = self.getStreamingBatchCommand
            
            self.__add_command(command)

        self.immediate_debug = True #Assume it is on from the start. May cause it to take slightly longer to initialize, but prevents breaking if it is on
        #Callback gives the debug message and sensor object that caused it
        self.__debug_cache: list[str] = [] #Used for storing startup debug messages until sensor state is confirmed
        
        self.debug_callback: Callable[[str, ThreespaceSensor],None] = self.__default_debug_callback
        self.misaligned = False
        self.dirty_cache = False
        self.header_info = ThreespaceHeaderInfo()
        self.header_enabled = True 

        #All the different streaming options
        self.is_data_streaming = False
        self.is_log_streaming = False
        self.is_file_streaming = False
        self._force_stop_streaming()

        #Used to ensure connecting to the correct sensor when reconnecting
        self.serial_number = None

        self.__cached_in_bootloader = self.__check_bootloader_status()
        if not self.in_bootloader:
            self.__firmware_init()
        else:
            self.serial_number = self.bootloader_get_sn()
            self.__empty_debug_cache()

    def __firmware_init(self):
        """
        Should only be called when not streaming and known in firmware.
        Called for powerup events when booting into firmware
        """
        self.dirty_cache = False #No longer dirty cause initializing
        
        self.__reinit_firmware()
        
        self.valid_mags = self.__get_valid_components("valid_mags")
        self.valid_accels = self.__get_valid_components("valid_accels")
        self.valid_gyros = self.__get_valid_components("valid_gyros")
        self.valid_baros = self.__get_valid_components("valid_baros")

    def __get_valid_components(self, key: str):
        valid = self.get_settings(key)
        if len(valid) == 0: return []
        return [int(v) for v in valid.split(',')]

    def __reinit_firmware(self):
        """
        Called when settings may have changed but a full reboot did not occur
        """
        self.dirty_cache = False #No longer dirty cause initializing
        
        self.header_info = ThreespaceHeaderInfo()
        self.cmd_echo_byte_index = None
        self.streaming_slots: list[ThreespaceCommand] = [None] * 16
        self.streaming_packets: list[ThreespaceCmdResult[list]] = []

        self.file_stream_data = bytearray([])
        self.file_stream_length = 0

        self.streaming_packet_size = 0
        self._force_stop_streaming()

        #Now reinitialize the cached settings
        self.__cache_header_settings()
        self.cache_streaming_settings()

        self.serial_number = int(self.get_settings("serial_number"), 16)
        self.__empty_debug_cache()
        self.immediate_debug = int(self.get_settings("debug_mode")) == 1 #Needed for some startup processes when restarting

    def __default_debug_callback(self, msg: str, sensor: "ThreespaceSensor"):
        if self.serial_number is None:
            self.__debug_cache.append(msg.strip())
        else:
            print(f"DEBUG {hex(self.serial_number)}:", msg.strip())

    def __empty_debug_cache(self):
        for msg in self.__debug_cache:
            print(f"DEBUG {hex(self.serial_number)}:", msg)
        self.__debug_cache.clear()

    def __add_command(self, command: ThreespaceCommand):
        if self.commands[command.info.num] != None:
            print(f"Registering duplicate command: {command.info.num} {self.commands[command.info.num].info.name} {command.info.name}")
        self.commands[command.info.num] = command

        #Build the actual method for executing the command
        code = f"def {command.info.name}(self, *args):\n"
        code += f"    return self.execute_command(self.commands[{command.info.num}], *args)"
        exec(code, globals(), self.funcs)
        setattr(self, command.info.name, types.MethodType(self.funcs[command.info.name], self))

    def __get_command(self, command_name: str):
        for command in self.commands:
            if command is None: continue
            if command.info.name == command_name:
                return command
        return None

    @property
    def is_streaming(self):
        return self.is_data_streaming or self.is_log_streaming or self.is_file_streaming

    #Can't just do if "header" in string because log_header_enabled exists and doesn't actually require cacheing the header
    HEADER_KEYS = ["header", "header_status", "header_timestamp", "header_echo", "header_checksum", "header_serial", "header_length"]
    def set_settings(self, param_string: str = None, **kwargs):
        self.check_dirty()
        #Build cmd string
        params = []
        if param_string is not None:
            params.append(param_string)
        
        for key, value in kwargs.items():
            if isinstance(value, list):
                value = [str(v) for v in value]
                value = ','.join(value)
            elif isinstance(value, bool):
                value = int(value)
            params.append(f"{key}={value}")
        cmd = f"!{';'.join(params)}\n"

        if len(cmd) > 2048:
            print("Too many settings in one set_settings call. Max str length is 2048 but got", len(cmd))
            return 0xFF, 0xFF

        #For dirty check
        keys = cmd[1:-1].split(';')
        keys = [v.split('=')[0] for v in keys]

        #Must enable this before sending the set so can properly handle reading the response
        if "debug_mode=1" in cmd:
            self.immediate_debug = True

        #Send cmd
        self.com.write(cmd.encode())

        #Default values
        err = 3
        num_successes = 0

        response = self.__await_set_settings(self.com.timeout)
        if response == THREESPACE_AWAIT_COMMAND_TIMEOUT:
            print("Failed to get set_settings response")
            return err, num_successes

        #Decode response
        response = self.com.readline()
        response = response.decode().strip()
        err, num_successes = response.split(',')
        err = int(err)
        num_successes = int(num_successes)    

        #Handle updating state variables based on settings
        #If the user modified the header, need to cache the settings so the API knows how to interpret responses
        if "header" in cmd.lower(): #First do a quick check
            if any(v in keys for v in ThreespaceSensor.HEADER_KEYS): #Then do a longer check
                self.__cache_header_settings()
        
        if "stream_slots" in cmd.lower():
            self.cache_streaming_settings()
        
        #All the settings changed, just need to mark dirty
        if any(v in keys for v in ("default", "reboot")):
            self.set_cached_settings_dirty()

        if err:
            print(f"Err setting {cmd}: {err=} {num_successes=}")
        return err, num_successes

    def get_settings(self, *args: str) -> dict[str, str] | str:
        self.check_dirty()
        #Build and send the cmd
        params = list(args)
        cmd = f"?{';'.join(params)}\n"
        self.com.write(cmd.encode())

        keys = cmd[1:-1].split(';')
        error_response_len = len(THREESPACE_GET_SETTINGS_ERROR_RESPONSE)

        min_resp_length = 0
        for key in keys:
            min_resp_length += min(len(key) + 1, error_response_len)
        
        
        response = self.__await_get_settings(min_resp_length, timeout=self.com.timeout)
        if response == THREESPACE_AWAIT_COMMAND_TIMEOUT:
            print("Requested:", cmd)
            print("Potential response:", self.com.peekline())
            raise RuntimeError("Failed to receive get_settings response")      

        response = self.com.readline()
        response = response.decode().strip().split(';')
        
        #Build the response dict
        response_dict = {}
        for i, v in enumerate(response):
            if v == THREESPACE_GET_SETTINGS_ERROR_RESPONSE:
                response_dict[keys[i]] = THREESPACE_GET_SETTINGS_ERROR_RESPONSE
                continue
            try:
                key, value = v.split('=')
                response_dict[key] = value
            except:
                print("Failed to parse get value:", i, v, len(v))
        
        #Format response
        if len(response_dict) == 1:
            return list(response_dict.values())[0]
        return response_dict

    def set_debug_callback(self, callback: Callable[[str, "ThreespaceSensor"], None]):
        self.debug_callback = callback

    def execute_command(self, cmd: ThreespaceCommand, *args):
        self.check_dirty()

        retries = 0
        MAX_RETRIES = 3

        while retries < MAX_RETRIES:
            cmd.send_command(self.com, *args, header_enabled=self.header_enabled)
            result = self.__await_command(cmd)
            if result == THREESPACE_AWAIT_COMMAND_FOUND:
                break
            retries += 1
        
        if retries == MAX_RETRIES:
            raise RuntimeError(f"Failed to get response to command {cmd.info.name}")

        return self.read_and_parse_command(cmd)
    
    def read_and_parse_command(self, cmd: ThreespaceCommand):
        if self.header_enabled:
            header = ThreespaceHeader.from_bytes(self.com.read(self.header_info.size), self.header_info)
        else:
            header = ThreespaceHeader()
        result, raw = cmd.read_command(self.com)
        return ThreespaceCmdResult(result, header, data_raw_binary=raw)

    def __peek_checksum(self, header: ThreespaceHeader, max_data_length=4096):
        """
        Using a header that contains the checksum and data length, calculate the checksum of the expected
        data and verify if with the checksum in the header.

        Params
        ------
        header : The header to verify
        max_data_length : The maximum size to allow from header_length. This should be set to avoid a corrupted header with an extremely large length causing a lockup/timeout
        """
        header_len = len(header.raw_binary)
        if header.length > max_data_length:
            print("DATA TOO BIG:", header.length)
            return False
        data = self.com.peek(header_len + header.length)[header_len:]
        if len(data) != header.length: return False
        checksum = sum(data) % 256
        return checksum == header.checksum

    def __await_command(self, cmd: ThreespaceCommand, timeout=2):
        #Header isn't enabled, nothing can do. Just pretend we found it
        if not self.header_enabled: return THREESPACE_AWAIT_COMMAND_FOUND

        start_time = time.time()

        #Update the streaming until the result for this command is next in the buffer
        while True:
            if time.time() - start_time > timeout:
                return THREESPACE_AWAIT_COMMAND_TIMEOUT
            
            #Get potential header
            header = self.__try_peek_header()
            if header is None:
                continue

            echo = header.echo

            if echo == cmd.info.num: #Cmd matches
                if self.__peek_checksum(header, max_data_length=cmd.info.out_size):
                    self.misaligned = False
                    return THREESPACE_AWAIT_COMMAND_FOUND
                
                #Error in packet, go start realigning
                if not self.misaligned:
                    print(f"Checksum mismatch for command {cmd.info.num}")
                    self.misaligned = True
                self.com.read(1)
            else:
                #It wasn't a response to the command, so may be a response to some internal system
                self.__internal_update(header)
        
    def __await_set_settings(self, timeout=2):
        start_time = time.time()
        MINIMUM_LENGTH = len("0,0\r\n")
        MAXIMUM_LENGTH = len("255,255\r\n")

        while True:
            remaining_time = timeout - (time.time() - start_time)
            if remaining_time <= 0:
                return THREESPACE_AWAIT_COMMAND_TIMEOUT
            if self.com.length < MINIMUM_LENGTH: continue
            
            possible_response = self.com.peekline()
            if b'\r\n' not in possible_response: continue

            if len(possible_response) < MINIMUM_LENGTH:
                self.__internal_update(self.__try_peek_header())
                continue

            #Attempt to parse the line
            values = possible_response.split(b',')
            if len(values) != 2:
                self.__internal_update(self.__try_peek_header())
                continue

            v1 = 0
            v2 = 0
            try:
                v1 = int(values[0].decode())
                v2 = int(values[0].decode())
            except:
                self.__internal_update(self.__try_peek_header())
                continue

            if v1 < 0 or v1 > 255 or v2 < 0 or v2 > 255:
                self.__internal_update(self.__try_peek_header())
                continue
            
            self.misaligned = False
            return THREESPACE_AWAIT_COMMAND_FOUND
            
    def __await_get_settings(self, min_resp_length: int, timeout=2, check_bootloader=False):
        start_time = time.time()

        while True:
            remaining_time = timeout - (time.time() - start_time)
            if remaining_time <= 0:
                return THREESPACE_AWAIT_COMMAND_TIMEOUT
            
            if self.com.length < min_resp_length: continue
            if check_bootloader and self.com.peek(2) == b'OK':
                return THREESPACE_AWAIT_BOOTLOADER
            
            possible_response = self.com.peekline()
            if b'\r\n' not in possible_response: #failed to get newline
                continue

            if len(possible_response) < min_resp_length:
                self.__internal_update(self.__try_peek_header())
                continue

            #Make sure the line is all ascii data
            if not possible_response.isascii():
                self.__internal_update(self.__try_peek_header())
                continue

            #Check to make sure each potential key conforms to the standard
            key_value_pairs = possible_response.decode().split(';')
            err = False
            for kvp in key_value_pairs:
                if kvp.strip() == THREESPACE_GET_SETTINGS_ERROR_RESPONSE: continue
                split = kvp.split('=')
                if len(split) != 2:
                    err = True
                    break
                k, v = split
                if any(c in k for c in THREESPACE_SETTING_KEY_INVALID_CHARS):
                    err = True
                    break
            if err:
                self.__internal_update(self.__try_peek_header())
                continue
            
            self.misaligned = False
            return THREESPACE_AWAIT_COMMAND_FOUND        
        
    def __try_peek_header(self):
        """
        Attempts to retrieve a header from the com class immediately.

        Returns
        -------
        The header retrieved, or None
        """
        if not self.header_enabled: return None
        if self.com.length < self.header_info.size: return None
        header = self.com.peek(self.header_info.size)
        if len(header) != self.header_info.size: return None
        header = ThreespaceHeader.from_bytes(header, self.header_info)
        return header

    def __internal_update(self, header: ThreespaceHeader = None):
        """
        Manages checking the datastream for asynchronous responses (Streaming, Immediate Debug Messages).
        If no data is found to match these responses, the data buffer will be considered corrupted/misaligned
        and start advancing 1 byte at a time until a message is retrieved.
        For this reason, if waiting for a synchronous command response, this should be only checked after confirming the data
        is not in response to any synchronously queued commands to avoid removing actual data bytes from the com class.

        Parameters
        ----------
        header : ThreespaceHeader
            The header to use for checking if streaming results exist. Can optionally leave None if don't want to check streaming responses.

        Returns
        --------
        False : Misalignment
        True : Internal Data Found/Parsed
        """
        checksum_match = False #Just for debugging

        if header is not None:
            #NOTE: FOR THIS TO WORK IT IS REQUIRED THAT THE HEADER DOES NOT CHANGE WHILE STREAMING ANY FORM OF DATA.
            #IT IS UP TO THE API TO ENFORCE NOT ALLOWING HEADER CHANGES WHILE ANY OF THOSE THINGS ARE HAPPENING
            if self.is_data_streaming and header.echo == THREESPACE_GET_STREAMING_BATCH_COMMAND_NUM: #Its a streaming packet, so update streaming
                if checksum_match := self.__peek_checksum(header, max_data_length=self.getStreamingBatchCommand.info.out_size):
                    self.__update_base_streaming()
                    self.misaligned = False
                    return True
            elif self.is_log_streaming and header.echo == THREESPACE_FILE_READ_BYTES_COMMAND_NUM:
                if checksum_match := self.__peek_checksum(header, max_data_length=2560): #TODO: Confirm this number can be 2048 and then pound define it. Currently set to 2560 because I know that is big enough, but not optimal
                    self.__update_log_streaming()
                    self.misaligned = False
                    return True
            elif self.is_file_streaming and header.echo == THREESPACE_FILE_READ_BYTES_COMMAND_NUM:
                if checksum_match := self.__peek_checksum(header, max_data_length=THREESPACE_FILE_STREAMING_MAX_PACKET_SIZE):
                    self.__update_file_streaming()
                    self.misaligned = False
                    return True
        
        #Debug messages are possible and there is enough data to potentially be a debug message
        #NOTE: Firmware should avoid putting more then one \r\n in a debug message as they will be treated as unprocessed/misaligned characters
        if self.immediate_debug and self.com.length >= 7:
            #This peek can't be blocking so peekline can't be used
            potential_message = self.com.peek(min(self.com.length, 27)) #27 is 20 digit timestamp + " Level:"
            if b"Level:" in potential_message: #There is a debug message somewhere in the data, must validate it is the next item
                level_index = potential_message.index(b" Level:")
                partial = potential_message[:level_index]
                #There should not be a newline until the end of the message, so it shouldn't be in partial
                if partial.isascii() and partial.decode('ascii').isnumeric() and b'\r\n' not in partial:
                    message = self.com.readline() #Read out the whole message!
                    self.debug_callback(message.decode('ascii'), self)
                    self.misaligned = False
                    return True

        #The response didn't match any of the expected asynchronous streaming API responses, so assume a misalignment
        if header is not None:
            msg = f"Possible Misalignment or corruption/debug message, header {header} raw {header.raw_binary} {[hex(v) for v in header.raw_binary]}" \
            f" Checksum match? {checksum_match}"
            #f"{self.com.peek(min(self.com.length, 10))}"
        else:
            msg = "Possible Misalignment or corruption/debug message"
        #print("Misaligned:", self.com.peek(1))
        self.__handle_misalignment(msg)
        return False

    def __handle_misalignment(self, message: str = None):
        if not self.misaligned and message is not None:
            print(message)
        self.misaligned = True
        self.com.read(1) #Because of expected misalignment, go through buffer 1 by 1 until realigned

    def updateStreaming(self, max_checks=float('inf')):
        """
        Returns true if any amount of data was processed whether valid or not
        """
        if not self.is_streaming: return False

        #I may need to make this have a max num bytes it will process before exiting to prevent locking up on slower machines
        #due to streaming faster then the program runs
        num_checks = 0
        data_processed = False
        while num_checks < max_checks:
            if self.com.length < self.header_info.size:
                return data_processed
            
            #Get header
            header = self.com.peek(self.header_info.size)

            #Get the header and send it to the internal update
            header = ThreespaceHeader.from_bytes(header, self.header_info)
            self.__internal_update(header)
            data_processed = True #Internal update always processes data. Either reads a streaming message, or advances buffer due to misalignment
            num_checks += 1
        
        return data_processed

    def startStreaming(self):
        if self.is_data_streaming: return
        self.streaming_packets.clear()
        self.cache_streaming_settings()

        result = self.execute_command(self.commands[85])
        self.is_data_streaming = True
        return result

    def _force_stop_streaming(self):
        """
        This function is used to stop streaming without validating it was streaming and ignoring any output of the
        communication line. This is a destructive call that will lose data, but will gurantee stopping streaming
        and leave the communication line in a clean state
        """
        cached_header_enabled = self.header_enabled
        cahched_dirty = self.dirty_cache

        #Must set these to gurantee it doesn't try and parse a response from anything since don't know the state of header
        self.dirty_cache = False
        self.header_enabled = False #Keep off for the attempt at stop streaming since if in an invalid state, won't be able to get response
        self.stopStreaming() #Just in case was streaming
        self.fileStopStream()

        #TODO: Change this to pause the data logging instead, then check the state and update
        self.stopDataLogging()
        
        #Restore
        self.header_enabled = cached_header_enabled
        self.dirty_cache = cahched_dirty

    def stopStreaming(self):
        result = self.execute_command(self.commands[86])
        self.is_data_streaming = False
        return result

    def set_cached_settings_dirty(self):
        """
        Could be streaming settings, header settings...
        Basically the sensor needs reinitialized
        """
        self.dirty_cache = True

    def __attempt_rediscover_self(self):
        """
        Trys to change the com class currently being used to be a detected
        com class with the same serial number. Useful for re-enumeration, such as when
        entering bootloader and using USB
        """
        for potential_com in self.com.auto_detect():
            potential_com.open()
            sensor = ThreespaceSensor(potential_com)
            if sensor.serial_number == self.serial_number:
                self.com = potential_com
                return True
            sensor.cleanup() #Handles closing the potential_com
        return False

    def check_dirty(self):
        if not self.dirty_cache: return
        if self.com.reenumerates and not self.com.check_open(): #Must check this, as could have transitioned from bootloader to firmware or vice versa and just needs re-opened/detected
            success = self.__attempt_rediscover_self()
            if not success:
                raise RuntimeError("Sensor connection lost")
        
        self._force_stop_streaming() #Can't be streaming when checking the dirty cache. If you want to stream, don't do things that cause the object to go dirty.
        was_in_bootloader = self.__cached_in_bootloader
        self.__cached_in_bootloader = self.__check_bootloader_status()
        
        if was_in_bootloader and not self.__cached_in_bootloader: #Just Exited bootloader, need to fully reinit
            self.__firmware_init()
        elif not self.__cached_in_bootloader:   #Was already in firmware, so only need to partially reinit
            self.__reinit_firmware()    #Partially init when just naturally dirty
        self.dirty_cache = False

    def cache_streaming_settings(self):
        cached_slots: list[ThreespaceCommand] = []
        slots: str = self.get_settings("stream_slots")
        slots = slots.split(',')
        for slot in slots:
            slot = int(slot.split(':')[0]) #Ignore parameters if any
            if slot != 255:
                cached_slots.append(self.commands[slot])
            else:
                cached_slots.append(None)
        self.streaming_slots = cached_slots.copy()
        self.getStreamingBatchCommand.set_stream_slots(self.streaming_slots)
        self.streaming_packet_size = 0
        for command in self.streaming_slots:
            if command == None: continue
            self.streaming_packet_size += command.info.out_size

    def __cache_header_settings(self):
        """
        Should be called any time changes are made to the header. Will normally be called via the check_dirty/reinit
        """
        result = self.get_settings("header")
        header = int(result)
        #API requires these bits to be enabled, so don't let them be disabled
        required_header = header | THREESPACE_REQUIRED_HEADER
        if header == self.header_info.bitfield and header == required_header: return #Nothing to update
        
        #Don't allow the header to change while streaming
        #This is to prevent a situation where the header for streaming and commands are different
        #since streaming caches the header. This would cause an issue where the echo byte could be in seperate
        #positions, causing a situation where parsing a command and streaming at the same time breaks since it thinks both are valid cmd echoes.
        if self.is_streaming:
            print("PREVENTING HEADER CHANGE DUE TO CURRENTLY STREAMING")
            self.set_settings(header=self.header_info.bitfield)
            return
        
        if required_header != header:
            print(f"Forcing header checksum, echo, and length enabled")
            self.set_settings(header=required_header)
            return
        
        #Current/New header is valid, so can cache it
        self.header_info.bitfield = header
        self.cmd_echo_byte_index = self.header_info.get_start_byte(THREESPACE_HEADER_ECHO_BIT) #Needed for cmd validation while streaming

    def __update_base_streaming(self):
        """
        Should be called after the packet is validated
        """
        self.streaming_packets.append(self.read_and_parse_command(self.getStreamingBatchCommand))

    def getOldestStreamingPacket(self):
        if len(self.streaming_packets) == 0:
            return None
        return self.streaming_packets.pop(0)
    
    def getNewestStreamingPacket(self):
        if len(self.streaming_packets) == 0:
            return None
        return self.streaming_packets.pop()   
    
    def clearStreamingPackets(self):
        self.streaming_packets.clear()
    
    def fileStartStream(self) -> ThreespaceCmdResult[int]:
        result = self.execute_command(self.__get_command("__fileStartStream"))
        self.file_stream_length = result.data
        self.is_file_streaming = True
        return result
    
    def fileStopStream(self) -> ThreespaceCmdResult[None]:
        self.check_dirty()
        result = self.execute_command(self.__get_command("__fileStopStream"))
        self.is_file_streaming = False
        return result

    def getFileStreamData(self):
        to_return = self.file_stream_data.copy()
        self.file_stream_data.clear()
        return to_return

    def clearFileStreamData(self):
        self.file_stream_data.clear()

    def __update_file_streaming(self):
        """
        Should be called after the packet is validated
        """
        header = ThreespaceHeader.from_bytes(self.com.read(self.header_info.size), self.header_info)
        data = self.com.read(header.length)
        self.file_stream_data += data
        self.file_stream_length -= header.length
        if header.length < THREESPACE_FILE_STREAMING_MAX_PACKET_SIZE or self.file_stream_length == 0: #File streaming sends in chunks of 512. If not 512, it must be the last packet
            self.is_file_streaming = False
            if self.file_stream_length != 0:
                print(f"File streaming stopped due to last packet. However still expected {self.file_stream_length} more bytes.")

    def startDataLogging(self) -> ThreespaceCmdResult[None]:
        self.cache_streaming_settings()

        #Must check whether streaming is being done alongside logging or not. Also configure required settings if it is
        streaming = bool(int(self.get_settings("log_immediate_output")))
        if streaming:
            self.set_settings(log_immediate_output_header_enabled=1,
                                log_immediate_output_header_mode=THREESPACE_OUTPUT_MODE_BINARY) #Must have header enabled in the log messages for this to work and must use binary for the header
        
        result = self.execute_command(self.__get_command("__startDataLogging"))
        self.is_log_streaming = streaming 
        return result

    def stopDataLogging(self) -> ThreespaceCmdResult[None]:
        result = self.execute_command(self.__get_command("__stopDataLogging"))
        self.is_log_streaming = False
        return result

    def __update_log_streaming(self):
        """
        Should be called after the packet is validated
        Log streaming is essentially file streaming done as the file is recorded. So uses file
        streaming logistics. Will update this later to also parse the response maybe.
        """
        header = ThreespaceHeader.from_bytes(self.com.read(self.header_info.size), self.header_info)
        data = self.com.read(header.length)
        self.file_stream_data += data

    def softwareReset(self):
        self.check_dirty()
        cmd = self.commands[226]
        cmd.send_command(self.com)
        self.com.close()
        time.sleep(0.5) #Give it time to restart
        self.com.open()
        self.__firmware_init()

    def enterBootloader(self):
        if self.in_bootloader: return

        cmd = self.commands[229]
        cmd.send_command(self.com)
        time.sleep(0.5) #Give it time to boot into bootloader
        if self.com.reenumerates:
            self.com.close()
            success = self.__attempt_rediscover_self()
            if not success:
                raise RuntimeError("Failed to reconnect to sensor in bootloader")
        in_bootloader = self.__check_bootloader_status()
        if not in_bootloader:
            raise RuntimeError("Failed to enter bootloader")
        self.__cached_in_bootloader = True
        self.com.read_all() #Just in case any garbage floating around


    @property
    def in_bootloader(self):
        #This function should not be used internally when solving dirty checks
        self.check_dirty() #If dirty, this we reobtain the value of __cached_in_bootloader.
        return self.__cached_in_bootloader

    def __check_bootloader_status(self):
        """
        Checks if in the bootloader via command. If wanting via cache, just check .in_bootloader
        This function both updates .in_bootloader and returns the value
        
        Must not call this function while streaming. It is only used internally and should be able to meet these conditions
        A user of this class should use .in_bootloader instead of this function        .

        To check, ? is sent, the bootloader will respond with OK. However, to avoid needing to wait
        for the timeout, we send a setting query at the same time. If the response is to the setting, in firmware,
        else if ok, in bootloader. If times out, something funky is happening.
        All bootloader commands are CAPITAL letters. Firmware commands are case insensitive. So as long as send no capitals, its fine.
        """
        #If sending commands over BT to the bootloader, it does an Auto Baudrate Detection
        #for the BT module that requires sending 3 U's. This will respond with 1-2 OK responses if in bootloader.
        #By then adding a ?UUU, that will trigger a <KEY_ERROR> if in firmware. So, can tell if in bootloader or firmware by checking for OK or <KEY_ERROR>
        bootloader = False
        self.com.write("UUU?UUU\n".encode())
        response = self.__await_get_settings(2, check_bootloader=True)
        if response == THREESPACE_AWAIT_COMMAND_TIMEOUT: 
            print("Requested Bootloader, Got:")
            print(self.com.peek(self.com.length))
            raise RuntimeError("Failed to discover bootloader or firmware.")
        if response == THREESPACE_AWAIT_BOOTLOADER:
            bootloader = True
            time.sleep(0.1) #Give time for all the OK responses to come in
            self.com.read_all() #Remove the rest of the OK responses or the rest of the <KEY_ERROR> response
        elif response == THREESPACE_AWAIT_COMMAND_FOUND:
            bootloader = False
            self.com.readline() #Clear the setting, no need to parse
        else:
            raise Exception("Failed to detect if in bootloader or firmware")
        return bootloader
    
    def bootloader_get_sn(self):
        self.com.write("Q".encode())
        result = self.com.read(9) #9 Because it includes a line feed for reasons
        if len(result) != 9:
            raise Exception()
        #Note bootloader uses big endian instead of little for reasons
        return struct.unpack(f">{_3space_format_to_external('U')}", result[:8])[0]

    def bootloader_boot_firmware(self):
        if not self.in_bootloader: return
        self.com.write("B".encode())
        time.sleep(0.5) #Give time to boot into firmware
        if self.com.reenumerates:
            self.com.close()
            success = self.__attempt_rediscover_self()
            if not success:
                raise RuntimeError("Failed to reconnect to sensor in firmware")
        in_bootloader = self.__check_bootloader_status()
        if in_bootloader:
            raise RuntimeError("Failed to exit bootloader")
        self.__cached_in_bootloader = False
        self.__firmware_init() 
    
    def bootloader_erase_firmware(self, timeout=20):
        """
        This may take a long time
        """
        self.com.write('S'.encode())
        if timeout is not None:
            cached_timeout = self.com.timeout
            self.com.timeout = timeout
        response = self.com.read(1)[0]
        if timeout is not None:
            self.com.timeout = cached_timeout
        return response
    
    def bootloader_get_info(self):
        self.com.write('I'.encode())
        memstart = struct.unpack(f">{_3space_format_to_external('l')}", self.com.read(4))[0]
        memend = struct.unpack(f">{_3space_format_to_external('l')}", self.com.read(4))[0]
        pagesize = struct.unpack(f">{_3space_format_to_external('I')}", self.com.read(2))[0]
        bootversion = struct.unpack(f">{_3space_format_to_external('I')}", self.com.read(2))[0]
        return ThreespaceBootloaderInfo(memstart, memend, pagesize, bootversion)

    def bootloader_prog_mem(self, bytes: bytearray):
        memsize = len(bytes)
        checksum = sum(bytes)
        self.com.write('C'.encode())
        self.com.write(struct.pack(f">{_3space_format_to_external('I')}", memsize))
        self.com.write(bytes)
        self.com.write(struct.pack(f">{_3space_format_to_external('B')}", checksum & 0xFFFF))
        return self.com.read(1)[0]

    def bootloader_get_state(self):
        self.com.write('OO'.encode()) #O is sent twice to compensate for a bug in some versions of the bootloader where the next character is ignored (except for R, do NOT send R after O, it will erase all settings)
        state = struct.unpack(f">{_3space_format_to_external('u')}", self.com.read(4))[0]
        self.com.read_all() #Once the bootloader is fixed, it will respond twice instead of once. So consume any remainder
        return state

    def bootloader_restore_factory_settings(self):
        self.com.write("RR".encode())

    def cleanup(self):
        if not self.in_bootloader:
            if self.is_data_streaming:
                self.stopStreaming()
            if self.is_file_streaming:
                self.fileStopStream()
            if self.is_log_streaming:
                self.stopDataLogging()
            #self.closeFile() #May not be opened, but also not cacheing that so just attempt to close. Currently commented out because breaks embedded
        self.com.close()

#-------------------------START ALL PROTOTYPES------------------------------------

    def eeptsStart(self) -> ThreespaceCmdResult[None]:
        raise NotImplementedError("This method is not available.")

    def eeptsStop(self) -> ThreespaceCmdResult[None]:
        raise NotImplementedError("This method is not available.")    
    
    def eeptsGetOldestStep(self) -> ThreespaceCmdResult[list]:
        raise NotImplementedError("This method is not available.")  

    def eeptsGetNewestStep(self) -> ThreespaceCmdResult[list]:
        raise NotImplementedError("This method is not available.")      

    def eeptsGetNumStepsAvailable(self) -> ThreespaceCmdResult[int]:
        raise NotImplementedError("This method is not available.")       

    def eeptsInsertGPS(self, latitude: float, longitude: float) -> ThreespaceCmdResult[None]:
        raise NotImplementedError("This method is not available.")      
       
    def eeptsAutoOffset(self) -> ThreespaceCmdResult[None]:
        raise NotImplementedError("This method is not available.")    
    
    def getRawGyroRate(self, id: int) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")

    def getRawAccelVec(self, id: int) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")

    def getRawMagVec(self, id: int) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")   

    def getTaredOrientation(self) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.") 

    def getTaredOrientationAsEulerAngles(self) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")  
                                    
    def getTaredOrientationAsRotationMatrix(self) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")

    def getTaredOrientationAsAxisAngles(self) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")

    def getTaredOrientationAsTwoVector(self) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")  
    
    def getDifferenceQuaternion(self) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")   

    def getUntaredOrientation(self) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")  

    def getUntaredOrientationAsEulerAngles(self) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")  

    def getUntaredOrientationAsRotationMatrix(self) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")  

    def getUntaredOrientationAsAxisAngles(self) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")  

    def getUntaredOrientationAsTwoVector(self) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")  
    
    def commitSettings(self) -> ThreespaceCmdResult[None]:
        raise NotImplementedError("This method is not available.")    

    def getMotionlessConfidenceFactor(self) -> ThreespaceCmdResult[float]:
        raise NotImplementedError("This method is not available.")
    
    def enableMSC(self) -> ThreespaceCmdResult[None]:
        raise NotImplementedError("This method is not available.")

    def disableMSC(self) -> ThreespaceCmdResult[None]:
        raise NotImplementedError("This method is not available.")    
    
    def getNextDirectoryItem(self) -> ThreespaceCmdResult[list[int,str,int]]:    
        raise NotImplementedError("This method is not available.")
    
    def changeDirectory(self, path: str) -> ThreespaceCmdResult[None]:    
        raise NotImplementedError("This method is not available.")    

    def openFile(self, path: str) -> ThreespaceCmdResult[None]:    
        raise NotImplementedError("This method is not available.") 
    
    def closeFile(self) -> ThreespaceCmdResult[None]:    
        raise NotImplementedError("This method is not available.")    

    def fileGetRemainingSize(self) -> ThreespaceCmdResult[int]:    
        raise NotImplementedError("This method is not available.")    

    def fileReadLine(self) -> ThreespaceCmdResult[str]:    
        raise NotImplementedError("This method is not available.")     

    def fileReadBytes(self, num_bytes: int) -> ThreespaceCmdResult[bytes]:    
        self.check_dirty()
        cmd = self.commands[THREESPACE_FILE_READ_BYTES_COMMAND_NUM]
        cmd.send_command(self.com, num_bytes, header_enabled=self.header_enabled)
        self.__await_command(cmd)
        if self.header_enabled:
            header = ThreespaceHeader.from_bytes(self.com.read(self.header_info.size), self.header_info)
        else:
            header = ThreespaceHeader()

        response = self.com.read(num_bytes)
        return ThreespaceCmdResult(response, header, data_raw_binary=response)

    def deleteFile(self, path: str) -> ThreespaceCmdResult[None]:    
        raise NotImplementedError("This method is not available.") 

    def getStreamingBatch(self):
        raise NotImplementedError("This method is not available.")

    def setOffsetWithCurrentOrientation(self) -> ThreespaceCmdResult[None]:
        raise NotImplementedError("This method is not available.")
    
    def resetBaseOffset(self) -> ThreespaceCmdResult[None]:
        raise NotImplementedError("This method is not available.")

    def setBaseOffsetWithCurrentOrientation(self) -> ThreespaceCmdResult[None]:
        raise NotImplementedError("This method is not available.")    

    def getTaredTwoVectorInSensorFrame(self) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")    

    def getUntaredTwoVectorInSensorFrame(self) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")        

    def getPrimaryBarometerPressure(self) -> ThreespaceCmdResult[float]:
        raise NotImplementedError("This method is not available.")
    
    def getPrimaryBarometerAltitude(self) -> ThreespaceCmdResult[float]:
        raise NotImplementedError("This method is not available.")    

    def getBarometerAltitude(self, id: int) -> ThreespaceCmdResult[float]:
        raise NotImplementedError("This method is not available.")   
    
    def getBarometerPressure(self, id: int) -> ThreespaceCmdResult[float]:
        raise NotImplementedError("This method is not available.")      

    def getAllPrimaryNormalizedData(self) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")
    
    def getPrimaryNormalizedGyroRate(self) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")

    def getPrimaryNormalizedAccelVec(self) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")

    def getPrimaryNormalizedMagVec(self) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")
    
    def getAllPrimaryCorrectedData(self) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")
    
    def getPrimaryCorrectedGyroRate(self) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")

    def getPrimaryCorrectedAccelVec(self) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")

    def getPrimaryCorrectedMagVec(self) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")    
    
    def getPrimaryGlobalLinearAccel(self) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.") 

    def getPrimaryLocalLinearAccel(self) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")         

    def getTemperatureCelsius(self) -> ThreespaceCmdResult[float]:
        raise NotImplementedError("This method is not available.") 
    
    def getTemperatureFahrenheit(self) -> ThreespaceCmdResult[float]:
        raise NotImplementedError("This method is not available.")     

    def getNormalizedGyroRate(self, id: int) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")
    
    def getNormalizedAccelVec(self, id: int) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")

    def getNormalizedMagVec(self, id: int) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")        

    def getCorrectedGyroRate(self, id: int) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")
    
    def getCorrectedAccelVec(self, id: int) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")

    def getCorrectedMagVec(self, id: int) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")        

    def enableMSC(self) -> ThreespaceCmdResult[None]:
        raise NotImplementedError("This method is not available.")  
    
    def disableMSC(self) -> ThreespaceCmdResult[None]:
        raise NotImplementedError("This method is not available.")    

    def getTimestamp(self) -> ThreespaceCmdResult[int]:
        raise NotImplementedError("This method is not available.")  

    def getBatteryVoltage(self) -> ThreespaceCmdResult[float]:
        raise NotImplementedError("This method is not available.") 
    
    def getBatteryPercent(self) -> ThreespaceCmdResult[int]:
        raise NotImplementedError("This method is not available.") 

    def getBatteryStatus(self) -> ThreespaceCmdResult[int]:
        raise NotImplementedError("This method is not available.")         

    def getGpsCoord(self) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")
    
    def getGpsAltitude(self) -> ThreespaceCmdResult[float]:
        raise NotImplementedError("This method is not available.") 

    def getGpsFixState(self) -> ThreespaceCmdResult[int]:
        raise NotImplementedError("This method is not available.") 

    def getGpsHdop(self) -> ThreespaceCmdResult[float]:
        raise NotImplementedError("This method is not available.") 

    def getGpsSatellites(self) -> ThreespaceCmdResult[int]:
        raise NotImplementedError("This method is not available.")                 

    def getButtonState(self) -> ThreespaceCmdResult[int]:
        raise NotImplementedError("This method is not available.")

    def correctRawGyroData(self, x: float, y: float, z: float, id: int) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")
    
    def correctRawAccelData(self, x: float, y: float, z: float, id: int) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")

    def correctRawMagData(self, x: float, y: float, z: float, id: int) -> ThreespaceCmdResult[list[float]]:
        raise NotImplementedError("This method is not available.")

    def formatSd(self) -> ThreespaceCmdResult[None]:
        raise NotImplementedError("This method is not available.")    

    def setDateTime(self, year: int, month: int, day: int, hour: int, minute: int, second: int) -> ThreespaceCmdResult[None]:
        raise NotImplementedError("This method is not available.")

    def getDateTime(self) -> ThreespaceCmdResult[list[int]]:
        raise NotImplementedError("This method is not available.")  

    def tareWithCurrentOrientation(self) -> ThreespaceCmdResult[None]:
        raise NotImplementedError("This method is not available.") 
    
    def setBaseTareWithCurrentOrientation(self) -> ThreespaceCmdResult[None]:
        raise NotImplementedError("This method is not available.")     

    def resetFilter(self) -> ThreespaceCmdResult[None]:
        raise NotImplementedError("This method is not available.")
    
    def getNumDebugMessages(self) -> ThreespaceCmdResult[int]:
        raise NotImplementedError("This method is not available.")
    
    def getOldestDebugMessage(self) -> ThreespaceCmdResult[str]:
        raise NotImplementedError("This method is not available.")
    
    def selfTest(self) -> ThreespaceCmdResult[int]:
        raise NotImplementedError("This method is not available.")
    
    def beginPassiveAutoCalibration(self, enabled_bitfield: int) -> ThreespaceCmdResult[None]:
        raise NotImplementedError("This method is not available.")

    def getActivePassiveAutoCalibration(self) -> ThreespaceCmdResult[int]:
        raise NotImplementedError("This method is not available.")

    def beginActiveAutoCalibration(self) -> ThreespaceCmdResult[None]:
        raise NotImplementedError("This method is not available.")

    def isActiveAutoCalibrationActive(self) -> ThreespaceCmdResult[int]:
        raise NotImplementedError("This method is not available.")                
    
    def getStreamingLabel(self, cmd_num: int) -> ThreespaceCmdResult[str]:
        raise NotImplementedError("This method is not available.")   

    def setCursor(self, cursor_index: int) -> ThreespaceCmdResult[None]:
        raise NotImplementedError("This method is not available.") 

    def getLastLogCursorInfo(self) -> ThreespaceCmdResult[tuple[int,str]]:
        raise NotImplementedError("This method is not available.")   

    def pauseLogStreaming(self, pause: bool) -> ThreespaceCmdResult[None]:
        raise NotImplementedError("This method is not available.")               

THREESPACE_GET_STREAMING_BATCH_COMMAND_NUM = 84
THREESPACE_FILE_READ_BYTES_COMMAND_NUM = 177

#Acutal command definitions
_threespace_commands: list[ThreespaceCommand] = [
    #Tared Orientation
    ThreespaceCommand("getTaredOrientation", 0, "", "ffff"),
    ThreespaceCommand("getTaredOrientationAsEulerAngles", 1, "", "fff"),
    ThreespaceCommand("getTaredOrientationAsRotationMatrix", 2, "", "fffffffff"),
    ThreespaceCommand("getTaredOrientationAsAxisAngles", 3, "", "ffff"),
    ThreespaceCommand("getTaredOrientationAsTwoVector", 4, "", "ffffff"),

    #Weird
    ThreespaceCommand("getDifferenceQuaternion", 5, "", "ffff"),

    #Untared Orientation
    ThreespaceCommand("getUntaredOrientation", 6, "", "ffff"),
    ThreespaceCommand("getUntaredOrientationAsEulerAngles", 7, "", "fff"),
    ThreespaceCommand("getUntaredOrientationAsRotationMatrix", 8, "", "fffffffff"),
    ThreespaceCommand("getUntaredOrientationAsAxisAngles", 9, "", "ffff"),
    ThreespaceCommand("getUntaredOrientationAsTwoVector", 10, "", "ffffff"),
    
    #Late orientation additions
    ThreespaceCommand("getTaredTwoVectorInSensorFrame", 11, "", "ffffff"),
    ThreespaceCommand("getUntaredTwoVectorInSensorFrame", 12, "", "ffffff"),

    ThreespaceCommand("getPrimaryBarometerPressure", 13, "", "f"),
    ThreespaceCommand("getPrimaryBarometerAltitude", 14, "", "f"),
    ThreespaceCommand("getBarometerAltitude", 15, "b", "f"),
    ThreespaceCommand("getBarometerPressure", 16, "b", "f"),

    ThreespaceCommand("setOffsetWithCurrentOrientation", 19, "", ""),
    ThreespaceCommand("resetBaseOffset", 20, "", ""),
    ThreespaceCommand("setBaseOffsetWithCurrentOrientation", 22, "", ""),

    ThreespaceCommand("getAllPrimaryNormalizedData", 32, "", "fffffffff"),
    ThreespaceCommand("getPrimaryNormalizedGyroRate", 33, "", "fff"),
    ThreespaceCommand("getPrimaryNormalizedAccelVec", 34, "", "fff"),
    ThreespaceCommand("getPrimaryNormalizedMagVec", 35, "", "fff"),

    ThreespaceCommand("getAllPrimaryCorrectedData", 37, "", "fffffffff"),
    ThreespaceCommand("getPrimaryCorrectedGyroRate", 38, "", "fff"),
    ThreespaceCommand("getPrimaryCorrectedAccelVec", 39, "", "fff"),
    ThreespaceCommand("getPrimaryCorrectedMagVec", 40, "", "fff"),

    ThreespaceCommand("getPrimaryGlobalLinearAccel", 41, "", "fff"),
    ThreespaceCommand("getPrimaryLocalLinearAccel", 42, "", "fff"),

    ThreespaceCommand("getTemperatureCelsius", 43, "", "f"),
    ThreespaceCommand("getTemperatureFahrenheit", 44, "", "f"),

    ThreespaceCommand("getMotionlessConfidenceFactor", 45, "", "f"),

    ThreespaceCommand("correctRawGyroData", 48, "fffb", "fff"),
    ThreespaceCommand("correctRawAccelData", 49, "fffb", "fff"),
    ThreespaceCommand("correctRawMagData", 50, "fffb", "fff"),

    ThreespaceCommand("getNormalizedGyroRate", 51, "b", "fff"),
    ThreespaceCommand("getNormalizedAccelVec", 52, "b", "fff"),
    ThreespaceCommand("getNormalizedMagVec", 53, "b", "fff"),

    ThreespaceCommand("getCorrectedGyroRate", 54, "b", "fff"),
    ThreespaceCommand("getCorrectedAccelVec", 55, "b", "fff"),
    ThreespaceCommand("getCorrectedMagVec", 56, "b", "fff"),

    ThreespaceCommand("enableMSC", 57, "", ""),
    ThreespaceCommand("disableMSC", 58, "", ""),

    ThreespaceCommand("formatSd", 59, "", ""),
    ThreespaceCommand("__startDataLogging", 60, "", ""),
    ThreespaceCommand("__stopDataLogging", 61, "", ""),

    ThreespaceCommand("setDateTime", 62, "Bbbbbb", ""),
    ThreespaceCommand("getDateTime", 63, "", "Bbbbbb"),

    ThreespaceCommand("getRawGyroRate", 65, "b", "fff"),
    ThreespaceCommand("getRawAccelVec", 66, "b", "fff"),
    ThreespaceCommand("getRawMagVec", 67, "b", "fff"),

    ThreespaceCommand("eeptsStart", 68, "", ""),
    ThreespaceCommand("eeptsStop", 69, "", ""),
    ThreespaceCommand("eeptsGetOldestStep", 70, "", "uuddffffffbbff"),
    ThreespaceCommand("eeptsGetNewestStep", 71, "", "uuddffffffbbff"),
    ThreespaceCommand("eeptsGetNumStepsAvailable", 72, "", "b"),
    ThreespaceCommand("eeptsInsertGPS", 73, "dd", ""),
    ThreespaceCommand("eeptsAutoOffset", 74, "", ""),

    ThreespaceCommand("getStreamingLabel", 83, "b", "S"),
    ThreespaceCommand("__getStreamingBatch", THREESPACE_GET_STREAMING_BATCH_COMMAND_NUM, "", "S"),
    ThreespaceCommand("__startStreaming", 85, "", ""),
    ThreespaceCommand("__stopStreaming", 86, "", ""),
    ThreespaceCommand("pauseLogStreaming", 87, "b", ""),
    
    ThreespaceCommand("getTimestamp", 94, "", "U"),

    ThreespaceCommand("tareWithCurrentOrientation", 96, "", ""),
    ThreespaceCommand("setBaseTareWithCurrentOrientation", 97, "", ""),

    ThreespaceCommand("resetFilter", 120, "", ""),
    ThreespaceCommand("getNumDebugMessages", 126, "", "B"),
    ThreespaceCommand("getOldestDebugMessage", 127, "", "S"),
    ThreespaceCommand("selfTest", 128, "", "u"),

    ThreespaceCommand("beginPassiveAutoCalibration", 165, "b", ""),
    ThreespaceCommand("getActivePassiveAutoCalibration", 166, "", "b"),
    ThreespaceCommand("beginActiveAutoCalibration", 167, "", ""),
    ThreespaceCommand("isActiveAutoCalibrationActive", 168, "", "b"),

    ThreespaceCommand("getLastLogCursorInfo", 170, "", "US"),
    ThreespaceCommand("getNextDirectoryItem", 171, "", "bsU"),
    ThreespaceCommand("changeDirectory", 172, "S", ""),
    ThreespaceCommand("openFile", 173, "S", ""),
    ThreespaceCommand("closeFile", 174, "", ""),
    ThreespaceCommand("fileGetRemainingSize", 175, "", "U"),
    ThreespaceCommand("fileReadLine", 176, "", "S"),
    ThreespaceCommand("__fileReadBytes", THREESPACE_FILE_READ_BYTES_COMMAND_NUM, "B", "S"), #This has to be handled specially as the output is variable length BYTES not STRING
    ThreespaceCommand("deleteFile", 178, "S", ""),
    ThreespaceCommand("setCursor", 179, "U", ""),
    ThreespaceCommand("__fileStartStream", 180, "", "U"),
    ThreespaceCommand("__fileStopStream", 181, "", ""),

    ThreespaceCommand("getBatteryVoltage", 201, "", "f"),
    ThreespaceCommand("getBatteryPercent", 202, "", "b"),
    ThreespaceCommand("getBatteryStatus", 203, "", "b"),

    ThreespaceCommand("getGpsCoord", 215, "", "dd"),
    ThreespaceCommand("getGpsAltitude", 216, "", "f"),
    ThreespaceCommand("getGpsFixState", 217, "", "b"),
    ThreespaceCommand("getGpsHdop", 218, "", "f"),
    ThreespaceCommand("getGpsSatellites", 219, "", "b"),

    ThreespaceCommand("commitSettings", 225, "", ""),
    ThreespaceCommand("__softwareReset", 226, "", ""),
    ThreespaceCommand("__enterBootloader", 229, "", ""),

    ThreespaceCommand("getButtonState", 250, "", "b"),
]

def threespaceCommandGet(cmd_num: int):
    for command in _threespace_commands:
        if command.info.num == cmd_num:
            return command
    return None

def threespaceCommandGetInfo(cmd_num: int):
    command = threespaceCommandGet(cmd_num)
    if command is None: return None
    return command.info

def threespaceGetHeaderLabels(header_info: ThreespaceHeaderInfo):
    order = []
    if header_info.status_enabled:
        order.append("status")
    if header_info.timestamp_enabled:
        order.append("timestamp")
    if header_info.echo_enabled:
        order.append("echo")
    if header_info.checksum_enabled:
        order.append("checksum")
    if header_info.serial_enabled:
        order.append("serial#")
    if header_info.length_enabled:
        order.append("len")
    return order