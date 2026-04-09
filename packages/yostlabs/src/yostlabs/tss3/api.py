from yostlabs.tss3.consts import *
from yostlabs.communication.base import ThreespaceInputStream, ThreespaceOutputStream, ThreespaceComClass
from yostlabs.communication.serial import ThreespaceSerialComClass
from yostlabs.tss3.commands import *

from enum import Enum
from dataclasses import dataclass, field
from typing import TypeVar, Generic
from collections.abc import Callable
import struct
import types
import inspect
import time
import math

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

THREESPACE_AWAIT_COMMAND_FOUND = 0
THREESPACE_AWAIT_COMMAND_TIMEOUT = 1
THREESPACE_AWAIT_BOOTLOADER = 2

THREESPACE_UPDATE_COMMAND_PARSED = 0
THREESPACE_UPDATE_COMMAND_NOT_ENOUGH_DATA = 1
THREESPACE_UPDATE_COMMAND_MISALIGNED = 2

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
    
    def __init__(self, com = None, timeout=2, verbose=False, initial_clear_timeout=None):
        if com is None: #Default to attempting to use the serial com class if none is provided
            com = ThreespaceSerialComClass
        self.verbose = verbose

        manually_opened_com = False
        #Auto discover using the supplied com class type
        if inspect.isclass(com) and issubclass(com, ThreespaceComClass):
            new_com = None
            self.log("Auto-Discovering Sensor")
            for serial_com in com.auto_detect():
                new_com = serial_com
                break #Exit after getting 1
            if new_com is None:
                raise RuntimeError("Failed to auto discover com port")   
            self.com = new_com
            manually_opened_com = True
            self.com.open()
        #The supplied com already was a com class, nothing to do
        elif inspect.isclass(type(com)) and issubclass(type(com), ThreespaceComClass):
            self.com = com
            if not self.com.check_open():
                self.com.open()
                manually_opened_com = True
        else: #Unknown type, try making a ThreespaceSerialComClass out of this
            try:
                self.com = ThreespaceSerialComClass(com)
            except:
                raise ValueError("Failed to create default ThreespaceSerialComClass from parameter:", type(com), com)

        self.restart_delay = 0.5

        self.log("Configuring sensor communication")
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
        self.log("Stopping potential streaming")
        self._force_stop_streaming()
        #Clear out the buffer to allow faster initializing
        #Ex: If a large buffer build up due to streaming, especially if using a slower interface like BLE,
        #it may take a while before the entire garbage data can be parsed when checking for bootloader, causing a timeout
        #even though it would have eventually succeeded   
        self.log("Clearing com")     
        self.__clear_com(initial_clear_timeout)


        #Used to ensure connecting to the correct sensor when reconnecting
        self.serial_number = None
        self.hardware_version: ThreespaceHardwareVersion = None
        self.short_serial_number = None
        self.sensor_family = None
        self.firmware_version = None

        self.commands: list[ThreespaceCommand] = [None] * 256
        self.getStreamingBatchCommand: ThreespaceGetStreamingBatchCommand = None
        self.funcs = {}

        self.log("Checking firmware status")
        try:
            self.__cached_in_bootloader = self.__check_bootloader_status()
            if not self.in_bootloader:
                self.log("Initializing firmware")
                self.__firmware_init()
            else:
                self.log("Initializing bootloader")
                self.__cache_serial_number(self.bootloader_get_sn())
                self.__empty_debug_cache()
        #This is just to prevent a situation where instantiating the API creates and fails to release a com class on failure when user catches the exception
        #If user provides the com class, it is up to them to handle its state on error
        except Exception as e:
            self.log("Failed to initialize sensor")
            if manually_opened_com:
                self.com.close()
            raise e
        self.log("Successfully initialized sensor")

    #Just a helper for outputting information
    def log(self, *args):
        if self.verbose:
            print(*args)

#-----------------------INITIALIZIATION & REINITIALIZATION-----------------------------------

    def __clear_com(self, refresh_timeout=None):
        data = self.com.read_all()
        if refresh_timeout is None: return
        while len(data) > 0: #Continue until all data is cleared
            self.log(f"Refresh clear Length: {len(data)}")
            start_time = time.time()
            while time.time() - start_time < refresh_timeout: #Wait up to refresh time for a new message
                data = self.com.read_all()
                if len(data) > 0:
                    break #Refresh the start time and wait for more data

    def __firmware_init(self):
        """
        Should only be called when not streaming and known in firmware.
        Called for powerup events when booting into firmware
        """
        self.dirty_cache = False #No longer dirty cause initializing
        
        #Only reinitialize settings if detected firmware version changed (Or on startup)
        version = self.get_settings("version_firmware")
        if version != self.firmware_version:
            self.firmware_version = version
            self.__initialize_commands()
    
        self.__reinit_firmware()
        
        self.valid_mags = self.__get_valid_components("valid_mags")
        self.valid_accels = self.__get_valid_components("valid_accels")
        self.valid_gyros = self.__get_valid_components("valid_gyros")
        self.valid_baros = self.__get_valid_components("valid_baros")

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

        self.__cache_serial_number(int(self.get_settings("serial_number"), 16))
        self.__empty_debug_cache()
        self.immediate_debug = int(self.get_settings("debug_mode")) == 1 #Needed for some startup processes when restarting

        #Now reinitialize the cached settings
        self.__cache_header_settings()
        self.__cache_streaming_settings()

    def __initialize_commands(self):
        self.commands: list[ThreespaceCommand] = [None] * 256
        self.getStreamingBatchCommand: ThreespaceGetStreamingBatchCommand = None
        self.funcs = {}

        valid_commands = self.get_settings("valid_commands")
        if valid_commands == THREESPACE_GET_SETTINGS_ERROR_RESPONSE:
            #Treat all commands as valid because firmware is too old to have this setting
            valid_commands = list(range(256))
            self.log("Please update firmware to a version that contains ?valid_commands")
        else:
            valid_commands = list(int(v) for v in valid_commands.split(','))
        
        for command in THREESPACE_COMMANDS:
            #Skip commands that are not valid for this sensor
            if command.info.num not in valid_commands:
                #Register as invalid.
                setattr(self, command.info.name, self.__invalid_command)
                continue

            #Some commands are special and need added specially
            if command.info.num == THREESPACE_GET_STREAMING_BATCH_COMMAND_NUM:
                self.getStreamingBatchCommand = ThreespaceGetStreamingBatchCommand([])
                command = self.getStreamingBatchCommand
            
            self.__add_command(command)

#------------------------------INITIALIZATION HELPERS--------------------------------------------

    def __get_valid_components(self, key: str):
        valid = self.get_settings(key)
        if len(valid) == 0: return []
        return [int(v) for v in valid.split(',')]

    def __add_command(self, command: ThreespaceCommand):
        if self.commands[command.info.num] != None:
            self.log(f"Registering duplicate command: {command.info.num} {self.commands[command.info.num].info.name} {command.info.name}")
        self.commands[command.info.num] = command

        #This command type has special logic that requires its own function.
        #Make that function be called instead of using the generic execute that gets built
        custom_name = f"_{type(self).__name__}__{command.info.name}"
        custom = getattr(self, custom_name, None)
        if custom is not None:
            method = custom
        else:
            #Build the actual method for executing the command
            code = f"def {command.info.name}(self, *args, **kwargs):\n"
            code += f"    return self.execute_command(self.commands[{command.info.num}], *args, **kwargs)"
            exec(code, globals(), self.funcs)
            method = types.MethodType(self.funcs[command.info.name], self)

        setattr(self, command.info.name, method)

    def has_command(self, command: ThreespaceCommand):
        return self.commands[command.info.num] is not None 

    def __get_command(self, command_name: str):
        for command in self.commands:
            if command is None: continue
            if command.info.name == command_name:
                return command
        return None
    
    def __attempt_rediscover_self(self):
        """
        Trys to change the com class currently being used to be a detected
        com class with the same serial number. Useful for re-enumeration, such as when
        entering bootloader and using USB.
        """
        for potential_com in self.com.auto_detect():
            potential_com.open()
            sensor = ThreespaceSensor(potential_com)
            if sensor.serial_number == self.serial_number:
                self.com = potential_com
                return True
            sensor.cleanup() #Handles closing the potential_com
        return False

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
        #since streaming caches the header. This would cause an issue where the echo byte could be in separate
        #positions, causing a situation where parsing a command and streaming at the same time breaks since it thinks both are valid cmd echoes.
        if self.is_streaming:
            self.log("Preventing header change due to currently streaming")
            self.set_settings(header=self.header_info.bitfield)
            return
        
        if required_header != header:
            self.log(f"Forcing header checksum, echo, and length enabled")
            self.set_settings(header=required_header)
            return
        
        #Current/New header is valid, so can cache it
        self.header_info.bitfield = header
        self.cmd_echo_byte_index = self.header_info.get_start_byte(THREESPACE_HEADER_ECHO_BIT) #Needed for cmd validation while streaming

    def __cache_serial_number(self, serial_number: int):
        """
        Doesn't actually retrieve the serial number, rather sets various properties based on the serial number
        """
        self.serial_number = serial_number
        self.hardware_version = ThreespaceHardwareVersion.from_serial_number(serial_number)

        self.short_serial_number = self.hardware_version.short_serial_number
        self.sensor_family = self.hardware_version.family_name
        if self.sensor_family == "Unknown":
            self.log(f"Unknown Sensor Family detected, {self.hardware_version.family_id}")
            

#--------------------------------REINIT/DIRTY Helpers-----------------------------------------------
    def set_cached_settings_dirty(self):
        """
        Could be streaming settings, header settings...
        Basically the sensor needs reinitialized
        """
        self.dirty_cache = True

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

#-----------------------------------DEBUG COMMANDS---------------------------------------------------
    def __default_debug_callback(self, msg: str, sensor: "ThreespaceSensor"):
        if self.serial_number is None:
            self.__debug_cache.append(msg.strip())
        else:
            print(f"DEBUG {hex(self.serial_number)}:", msg.strip())

    def __empty_debug_cache(self):
        for msg in self.__debug_cache:
            print(f"DEBUG {hex(self.serial_number)}:", msg)
        self.__debug_cache.clear()

    def set_debug_callback(self, callback: Callable[[str, "ThreespaceSensor"], None]):
        self.debug_callback = callback

#-----------------------------------------------BASE SETTINGS PROTOCOL------------------------------------------------

    #Helper for converting python types to strings that set_settings can understand
    def __internal_str(self, value):
        if isinstance(value, float):
            return f"{value:.10f}"
        elif isinstance(value, bool):
            return int(value)
        elif isinstance(value, Enum):
            return str(value.value)
        else:
            return str(value)        

    #Can't just do if "header" in string because log_header_enabled exists and doesn't actually require caching the header
    HEADER_KEYS = ["header", "header_status", "header_timestamp", "header_echo", "header_checksum", "header_serial", "header_length"]
    def set_settings(self, param_string: str = None, **kwargs):
        self.check_dirty()
        #Build cmd string
        params = []
        if param_string is not None:
            params.append(param_string)
        
        for key, value in kwargs.items():
            if isinstance(value, list):
                value = [self.__internal_str(v) for v in value]
                value = ','.join(value)
            else:
                value = self.__internal_str(value)
            params.append(f"{key}={value}")
        cmd = f"!{';'.join(params)}\n"

        if len(cmd) > 2048:
            self.log("Too many settings in one set_settings call. Max str length is 2048 but got", len(cmd))
            return 0xFF, 0xFF

        #For dirty check
        param_dict = threespaceSetSettingsStringToDict(cmd[1:-1])

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
            self.log("Failed to get set_settings response")
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
            if any(v in param_dict.keys() for v in ThreespaceSensor.HEADER_KEYS): #Then do a longer check
                self.__cache_header_settings()
        
        if "stream_slots" in cmd.lower():
            self.__cache_streaming_settings()
        
        #All the settings changed, just need to mark dirty
        if any(v in param_dict.keys() for v in ("default", "reboot")):
            self.set_cached_settings_dirty()

        if err:
            self.log(f"Err setting {cmd}: {err=} {num_successes=}")
        return err, num_successes

    def get_settings(self, *args: str, format="Mixed") -> dict[str, str] | str:
        """
        Gets the values for all requested settings. Settings are request by their string name. The result will be
        the string response to that setting.

        Params
        -----
        *args : Any number of string keys
        format : "Mixed" (Dictionary if multiple settings requested, else just the response string) or "Dict" (Always a dictionary even if only one key)
        """
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
            self.log("Requested:", cmd)
            self.log("Potential response:", self.com.peekline())
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
                self.log("Failed to parse get value:", i, v, len(v))
        
        #Format response
        if len(response_dict) == 1 and format == "Mixed":
            return list(response_dict.values())[0]
        return response_dict

    """
    WIP. Currently does not work with string values or have full validation.
    """
    def get_setting_binary(self, key: str, format: str, use_threespace_format=True):
        if use_threespace_format:
            format = yost_format_to_struct_format(format)
        checksum = sum(ord(v) for v in key) % 256
        #Format = StartByte + Key + Null Terminator of key + Checksum
        cmd = struct.pack(f"<B{len(key)}sBB", ThreespaceCommand.BINARY_READ_SETTINGS_START_BYTE_HEADER, key.encode(), 0, checksum)
        self.com.write(cmd)
        try:
            min_response_len = key.index(';')
        except ValueError:
            min_response_len = len(key)
        if min_response_len < len(THREESPACE_GET_SETTINGS_ERROR_RESPONSE):
            min_response_len = len(THREESPACE_GET_SETTINGS_ERROR_RESPONSE)
        min_response_len += (1 + THREESPACE_BINARY_SETTINGS_ID_SIZE) #Null terminator and header size
        if self.__await_get_settings_binary(min_response_len) != THREESPACE_AWAIT_COMMAND_FOUND:
            raise RuntimeError(f"Failed to get binary setting: {key}")
        self.com.read(THREESPACE_BINARY_SETTINGS_ID_SIZE) #Read pass the Header ID
        
        try:
            echoed_key = self.com.read_until(b'\0')[:-1].decode()
            if echoed_key != key.lower():
                raise ValueError()
        except:
            raise ValueError()
        result = struct.unpack(f"<{format}", self.com.read(struct.calcsize(format)))
        self.com.read(2) #Remove the null terminator and the checksum
        if len(result) == 1:
            return result[0]
        return result


    #-----------Base Settings Parsing----------------

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
                v2 = int(values[1].decode())
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

    """
    Incomplete, binary settings protocol is Work In Progress
    """
    def __await_get_settings_binary(self, min_resp_length: int, timeout=2, check_bootloader=False):
        start_time = time.time()

        while True:
            remaining_time = timeout - (time.time() - start_time)
            if remaining_time <= 0:
                return THREESPACE_AWAIT_COMMAND_TIMEOUT
            
            if self.com.length < min_resp_length: continue
            if check_bootloader and self.com.peek(2) == b'OK':
                return THREESPACE_AWAIT_BOOTLOADER
            
            id = self.com.peek(THREESPACE_BINARY_SETTINGS_ID_SIZE)
            if struct.unpack("<L", id)[0] != THREESPACE_BINARY_READ_SETTINGS_ID:
                self.__internal_update(self.__try_peek_header())
                continue
            
            #TODO: Check the rest of the message structure, not just the ID

            self.misaligned = False
            return THREESPACE_AWAIT_COMMAND_FOUND

#---------------------------------BASE COMMAND PARSING--------------------------------------
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

    #TODO: max_data_length is not sufficient. Need MINIMUM length as well. Can have a situation where len = 0 and checksum = 0
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
            if not self.misaligned:
                self.log("DATA TOO BIG:", header.length)
            return False
        data = self.com.peek(header_len + header.length)[header_len:]
        if len(data) != header.length: 
            if not self.misaligned:
                self.log(f"Data Length Mismatch - Got: {len(data)} Expected: {header.length}")
            return False
        checksum = sum(data) % 256
        if checksum != header.checksum and not self.misaligned:
            self.log(f"Checksum Mismatch - Got: {checksum} Expected: {header.checksum}")
            self.log(f"Data: {data}")
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
                    self.log(f"Checksum mismatch for command {cmd.info.num}")
                    self.misaligned = True
                self.com.read(1)
            else:
                #It wasn't a response to the command, so may be a response to some internal system
                self.__internal_update(header)        

#------------------------------BASE INPUT PARSING--------------------------------------------

    def __internal_update(self, header: ThreespaceHeader = None, blocking=True):
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
        0 : Internal Data Found/Parsed
        1 : Not enough data (Only possible when blocking == False)
        2 : Misalignment
        """
        checksum_match = False #Just for debugging

        if header is not None:
            #NOTE: FOR THIS TO WORK IT IS REQUIRED THAT THE HEADER DOES NOT CHANGE WHILE STREAMING ANY FORM OF DATA.
            #IT IS UP TO THE API TO ENFORCE NOT ALLOWING HEADER CHANGES WHILE ANY OF THOSE THINGS ARE HAPPENING
            if self.is_data_streaming and header.echo == THREESPACE_GET_STREAMING_BATCH_COMMAND_NUM:
                if not blocking:
                    expected_output_size = len(header.raw_binary) + self.getStreamingBatchCommand.info.out_size
                    if self.com.length < expected_output_size: return THREESPACE_UPDATE_COMMAND_NOT_ENOUGH_DATA
                if checksum_match := self.__peek_checksum(header, max_data_length=self.getStreamingBatchCommand.info.out_size):
                    self.__update_base_streaming()
                    self.misaligned = False
                    return THREESPACE_UPDATE_COMMAND_PARSED
            elif self.is_log_streaming and header.echo == THREESPACE_FILE_READ_BYTES_COMMAND_NUM:
                if not blocking:
                    expected_output_size = len(header.raw_binary) + min(header.length, THREESPACE_LIVE_LOG_STREAM_MAX_PACKET_SIZE)
                    if self.com.length < expected_output_size: return THREESPACE_UPDATE_COMMAND_NOT_ENOUGH_DATA
                if checksum_match := self.__peek_checksum(header, max_data_length=THREESPACE_LIVE_LOG_STREAM_MAX_PACKET_SIZE):
                    self.__update_log_streaming()
                    self.misaligned = False
                    return THREESPACE_UPDATE_COMMAND_PARSED
            elif self.is_file_streaming and header.echo == THREESPACE_FILE_READ_BYTES_COMMAND_NUM:
                if not blocking:
                    expected_output_size = len(header.raw_binary) + min(header.length, THREESPACE_FILE_STREAMING_MAX_PACKET_SIZE)
                    if self.com.length < expected_output_size: return THREESPACE_UPDATE_COMMAND_NOT_ENOUGH_DATA
                if checksum_match := self.__peek_checksum(header, max_data_length=THREESPACE_FILE_STREAMING_MAX_PACKET_SIZE):
                    self.__update_file_streaming()
                    self.misaligned = False
                    return THREESPACE_UPDATE_COMMAND_PARSED
        
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
                    return THREESPACE_UPDATE_COMMAND_PARSED

        #The response didn't match any of the expected asynchronous streaming API responses, so assume a misalignment
        if header is not None:
            msg = f"Possible Misalignment or corruption/debug message, header {header} raw {header.raw_binary} {[hex(v) for v in header.raw_binary]}" \
            f" Checksum match? {checksum_match}"
            #f"{self.com.peek(min(self.com.length, 10))}"
        else:
            msg = "Possible Misalignment or corruption/debug message"
        #self.log("Misaligned:", self.com.peek(1))
        self.__handle_misalignment(msg)
        return THREESPACE_UPDATE_COMMAND_MISALIGNED

    def __handle_misalignment(self, message: str = None):
        if not self.misaligned and message is not None:
            self.log(message)
        self.misaligned = True
        self.com.read(1) #Because of expected misalignment, go through buffer 1 by 1 until realigned

#-----------------------------BASE COMMAND EXECUTION-------------------------------------

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
    
    def __invalid_command(self, *args):
        raise NotImplementedError("This method is not available.")

    def read_and_parse_command(self, cmd: ThreespaceCommand):
        if self.header_enabled:
            header = ThreespaceHeader.from_bytes(self.com.read(self.header_info.size), self.header_info)
        else:
            header = ThreespaceHeader()
        result, raw = cmd.read_command(self.com, verbose=self.verbose)
        return ThreespaceCmdResult(result, header, data_raw_binary=raw)

#-----------------------------------BASE STREAMING COMMMANDS----------------------------------------------

    @property
    def is_streaming(self):
        return self.is_data_streaming or self.is_log_streaming or self.is_file_streaming

    def __cache_streaming_settings(self):
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

    def __startStreaming(self) -> ThreespaceCmdResult[None]:
        if not self.is_data_streaming:
            self.streaming_packets.clear()
            self.__cache_streaming_settings()

        result = self.execute_command(self.commands[THREESPACE_START_STREAMING_COMMAND_NUM])
        self.is_data_streaming = True
        return result

    def __stopStreaming(self) -> ThreespaceCmdResult[None]:
        result = self.execute_command(self.commands[THREESPACE_STOP_STREAMING_COMMAND_NUM])
        self.is_data_streaming = False
        return result

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
    
    #This is called for all streaming types
    def updateStreaming(self, max_checks=float('inf'), timeout=None, blocking=False):
        """
        Returns true if any amount of data was processed whether valid or not. This is called for all streaming types.

        Parameters
        ----------
        max_checks : Will only attempt to read up to max_checks packets
        timeout : Will only attempt to read packets for this duration. It is possible for this function to take longer then this timeout \
        if blocking = True, in which case it could take up to timeout + com.timeout 
        blocking : If False, will immediately stop when not enough data is available. If true, will immediately stop if not enough data \
        for a header, but will block when trying to retrieve the data associated with that header. For most com classes, this does not matter. \
        But for communication such as BLE where the header and data may be split between different packets, this will have a clear effect.
        """
        if not self.is_streaming: return False
        if timeout is None: timeout = float('inf')
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
            result = self.__internal_update(header, blocking=blocking)
            if result == THREESPACE_UPDATE_COMMAND_PARSED:
                data_processed = True
            elif result == THREESPACE_UPDATE_COMMAND_NOT_ENOUGH_DATA:
                return data_processed
            
            num_checks += 1
        
        return data_processed

    #This is more so used for initialization. Its a way of stopping streaming without having to worry about parsing.
    #That way it can clean up the data stream that won't match the expected state if not already configured.
    def _force_stop_streaming(self):
        """
        This function attempts to stop all possible streaming without knowing anything about the state of the sensor.
        This includes trying to stop before any commands are even registered as valid. This is to ensure the sensor can properly
        start and recover from error conditions.

        This will stop streaming without validating it was streaming and ignoring any output of the
        communication line. This is a destructive call that will lose data, but will guarantee stopping streaming
        and leave the communication line in a clean state.
        """
        cached_header_enabled = self.header_enabled
        cached_dirty = self.dirty_cache

        #Must set these to guarantee it doesn't try and parse a response from anything since don't know the state of header
        self.dirty_cache = False
        self.header_enabled = False #Keep off for the attempt at stop streaming since if in an invalid state, won't be able to get response

        #NOTE that commands are accessed directly from the global table instead of commands registered to this sensor object
        #since this sensor object may have yet to register these commands when calling force_stop_streaming

        #Stop base Streaming
        self.execute_command(threespace_command_get_by_name("stopStreaming"))
        self.is_data_streaming = False

        #Stop file streaming
        self.execute_command(threespace_command_get_by_name("fileStopStream"))
        self.is_file_streaming = False  

        #Stop logging streaming
        # #TODO: Change this to pause the data logging instead, then check the state and update
        self.execute_command(threespace_command_get_by_name("stopDataLogging"))
        self.is_log_streaming = False              
        
        #Restore
        self.header_enabled = cached_header_enabled
        self.dirty_cache = cached_dirty

#-------------------------------------FILE STREAMING----------------------------------------------

    def __fileStartStream(self) -> ThreespaceCmdResult[int]:
        result = self.execute_command(self.__get_command("fileStartStream"))
        self.file_stream_length = result.data
        if self.file_stream_length > 0:
            self.is_file_streaming = True
        return result

    def __fileStopStream(self) -> ThreespaceCmdResult[None]:
        result = self.execute_command(self.__get_command("fileStopStream"))
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
                self.log(f"File streaming stopped due to last packet. However still expected {self.file_stream_length} more bytes.")

    def __fileReadBytes(self, num_bytes: int) -> ThreespaceCmdResult[bytes]:    
        self.check_dirty()
        cmd = self.commands[THREESPACE_FILE_READ_BYTES_COMMAND_NUM]
        cmd.send_command(self.com, num_bytes, header_enabled=self.header_enabled)
        self.__await_command(cmd)
        if self.header_enabled:
            header = ThreespaceHeader.from_bytes(self.com.read(self.header_info.size), self.header_info)
            num_bytes = min(num_bytes, header.length) #Its possible for less bytes to be returned when an error occurs (EX: Reading from unopened file)
        else:
            header = ThreespaceHeader()

        response = self.com.read(num_bytes)
        return ThreespaceCmdResult(response, header, data_raw_binary=response)

#----------------------------DATA LOGGING--------------------------------------

    def __startDataLogging(self) -> ThreespaceCmdResult[None]:
        self.__cache_streaming_settings()

        #Must check whether streaming is being done alongside logging or not. Also configure required settings if it is
        streaming = bool(int(self.get_settings("log_immediate_output")))
        if streaming:
            self.set_settings(log_immediate_output_header_enabled=1,
                                log_immediate_output_header_mode=THREESPACE_OUTPUT_MODE_BINARY) #Must have header enabled in the log messages for this to work and must use binary for the header
        
        result = self.execute_command(self.__get_command("startDataLogging"))
        self.is_log_streaming = streaming 
        return result

    def __stopDataLogging(self) -> ThreespaceCmdResult[None]:
        result = self.execute_command(self.__get_command("stopDataLogging"))
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

#---------------------------------POWER STATE CHANGING COMMANDS & BOOTLOADER------------------------------------

    def __softwareReset(self):
        self.check_dirty()
        cmd = self.commands[THREESPACE_SOFTWARE_RESET_COMMAND_NUM]
        cmd.send_command(self.com)
        self.com.close()
        #TODO: Make this actually wait instead of an arbitrary sleep length
        time.sleep(self.restart_delay) #Give it time to restart
        self.com.open()
        self.__firmware_init()

    def __enterBootloader(self):
        if self.in_bootloader: return

        cmd = self.commands[THREESPACE_ENTER_BOOTLOADER_COMMAND_NUM]
        cmd.send_command(self.com)
        #TODO: Make this actually wait instead of an arbitrary sleep length
        time.sleep(self.restart_delay) #Give it time to boot into bootloader
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
            self.log("Requested Bootloader, Got:")
            self.log(self.com.peek(self.com.length))
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
        return struct.unpack(f">{yost_format_to_struct_format('U')}", result[:8])[0]

    def bootloader_boot_firmware(self):
        if not self.in_bootloader: return
        self.com.write("B".encode())
        time.sleep(self.restart_delay) #Give time to boot into firmware
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

        start_time = time.perf_counter()
        response = []
        while len(response) == 0 and time.perf_counter() - start_time < timeout:
            response = self.com.read(1)
        if len(response) == 0:
            return -1
        return response[0]
    
    def bootloader_get_info(self):
        self.com.write('I'.encode())
        memstart = struct.unpack(f">{yost_format_to_struct_format('l')}", self.com.read(4))[0]
        memend = struct.unpack(f">{yost_format_to_struct_format('l')}", self.com.read(4))[0]
        pagesize = struct.unpack(f">{yost_format_to_struct_format('I')}", self.com.read(2))[0]
        bootversion = struct.unpack(f">{yost_format_to_struct_format('I')}", self.com.read(2))[0]
        return ThreespaceBootloaderInfo(memstart, memend, pagesize, bootversion)

    def bootloader_prog_mem(self, bytes: bytearray, timeout=5):
        memsize = len(bytes)
        checksum = sum(bytes)
        self.com.write('C'.encode())
        self.com.write(struct.pack(f">{yost_format_to_struct_format('I')}", memsize))
        self.com.write(bytes)
        self.com.write(struct.pack(f">{yost_format_to_struct_format('B')}", checksum & 0xFFFF))
        start_time = time.perf_counter()
        result = []
        while len(result) == 0 and time.perf_counter() - start_time < timeout:
            result = self.com.read(1)
        if len(result) > 0:
            return result[0]
        return -1

    def bootloader_get_state(self):
        self.com.write('OO'.encode()) #O is sent twice to compensate for a bug in some versions of the bootloader where the next character is ignored (except for R, do NOT send R after O, it will erase all settings)
        state = struct.unpack(f">{yost_format_to_struct_format('u')}", self.com.read(4))[0]
        self.com.read_all() #Once the bootloader is fixed, it will respond twice instead of once. So consume any remainder
        return state

    def bootloader_restore_factory_settings(self):
        self.com.write("RR".encode())

    def cleanup(self):
        error = None
        try:
            if not self.in_bootloader:
                if self.is_data_streaming:
                    self.stopStreaming()
                if self.is_file_streaming:
                    self.fileStopStream()
                if self.is_log_streaming:
                    self.stopDataLogging()

                #The sensor may or may not have this command registered. So just try it
                try:
                    #May not be opened, but also not caching that so just attempt to close.
                    self.closeFile()
                except: pass
        except Exception as e:
            error = e
        self.com.close() #Ensuring the close gets called, that way com ports can't get stuck open. Also makes calling cleanup() "safe" even after disconnect
        if error:
            raise error

#-------------------------START ALL PROTOTYPES------------------------------------

#To actually see how commands work, look at __initialize_commands and __add_command
#But basically, these are all just prototypes. Information about the commands is in the table
#beneath here, and the API simply calls its execute_command function on the Command information objects defined.

#If there is a function in the class named __{command_name} it will be used as the function 
#for that command. Otherwise, a default function will be used based on the available command info.
#Most commands do not require a custom function like this.

#Also note that all commands sent to the sensor are camelCase instead of snake_case to more
#closely match the actual source.

    def getTaredOrientation(self) -> ThreespaceCmdResult[list[float]]: ...
    def getTaredOrientationAsEulerAngles(self) -> ThreespaceCmdResult[list[float]]: ...                        
    def getTaredOrientationAsRotationMatrix(self) -> ThreespaceCmdResult[list[float]]: ...
    def getTaredOrientationAsAxisAngles(self) -> ThreespaceCmdResult[list[float]]: ...
    def getTaredOrientationAsTwoVector(self) -> ThreespaceCmdResult[list[float]]: ...

    def getDifferenceQuaternion(self) -> ThreespaceCmdResult[list[float]]: ... 

    def getUntaredOrientation(self) -> ThreespaceCmdResult[list[float]]: ...  
    def getUntaredOrientationAsEulerAngles(self) -> ThreespaceCmdResult[list[float]]: ... 
    def getUntaredOrientationAsRotationMatrix(self) -> ThreespaceCmdResult[list[float]]: ...
    def getUntaredOrientationAsAxisAngles(self) -> ThreespaceCmdResult[list[float]]: ... 
    def getUntaredOrientationAsTwoVector(self) -> ThreespaceCmdResult[list[float]]: ...
    def getTaredTwoVectorInSensorFrame(self) -> ThreespaceCmdResult[list[float]]: ...    
    def getUntaredTwoVectorInSensorFrame(self) -> ThreespaceCmdResult[list[float]]: ...  

    def getPrimaryBarometerPressure(self) -> ThreespaceCmdResult[float]: ...
    def getPrimaryBarometerAltitude(self) -> ThreespaceCmdResult[float]: ...    
    def getBarometerAltitude(self, id: int) -> ThreespaceCmdResult[float]: ...   
    def getBarometerPressure(self, id: int) -> ThreespaceCmdResult[float]: ... 

    def setOffsetWithCurrentOrientation(self) -> ThreespaceCmdResult[None]: ...
    def resetBaseOffset(self) -> ThreespaceCmdResult[None]: ...
    def setBaseOffsetWithCurrentOrientation(self) -> ThreespaceCmdResult[None]: ...

    def getAllPrimaryNormalizedData(self) -> ThreespaceCmdResult[list[float]]: ...
    def getPrimaryNormalizedGyroRate(self) -> ThreespaceCmdResult[list[float]]: ...
    def getPrimaryNormalizedAccelVec(self) -> ThreespaceCmdResult[list[float]]: ...
    def getPrimaryNormalizedMagVec(self) -> ThreespaceCmdResult[list[float]]: ...

    def getAllPrimaryCorrectedData(self) -> ThreespaceCmdResult[list[float]]: ...
    def getPrimaryCorrectedGyroRate(self) -> ThreespaceCmdResult[list[float]]: ...
    def getPrimaryCorrectedAccelVec(self) -> ThreespaceCmdResult[list[float]]: ...
    def getPrimaryCorrectedMagVec(self) -> ThreespaceCmdResult[list[float]]: ...

    def getPrimaryGlobalLinearAccel(self) -> ThreespaceCmdResult[list[float]]: ... 
    def getPrimaryLocalLinearAccel(self) -> ThreespaceCmdResult[list[float]]: ... 

    def getTemperatureCelsius(self) -> ThreespaceCmdResult[float]: ... 
    def getTemperatureFahrenheit(self) -> ThreespaceCmdResult[float]: ...   

    def getMotionlessConfidenceFactor(self) -> ThreespaceCmdResult[float]: ...

    def correctRawGyroData(self, x: float, y: float, z: float, id: int) -> ThreespaceCmdResult[list[float]]: ...
    def correctRawAccelData(self, x: float, y: float, z: float, id: int) -> ThreespaceCmdResult[list[float]]: ...
    def correctRawMagData(self, x: float, y: float, z: float, id: int) -> ThreespaceCmdResult[list[float]]: ...

    def getNormalizedGyroRate(self, id: int) -> ThreespaceCmdResult[list[float]]: ...
    def getNormalizedAccelVec(self, id: int) -> ThreespaceCmdResult[list[float]]: ...
    def getNormalizedMagVec(self, id: int) -> ThreespaceCmdResult[list[float]]: ...

    def getCorrectedGyroRate(self, id: int) -> ThreespaceCmdResult[list[float]]: ...
    def getCorrectedAccelVec(self, id: int) -> ThreespaceCmdResult[list[float]]: ...
    def getCorrectedMagVec(self, id: int) -> ThreespaceCmdResult[list[float]]: ...

    def enableMSC(self) -> ThreespaceCmdResult[None]: ...
    def disableMSC(self) -> ThreespaceCmdResult[None]: ...

    def formatSd(self) -> ThreespaceCmdResult[None]: ...
    def startDataLogging(self) -> ThreespaceCmdResult[None]: ...
    def stopDataLogging(self) -> ThreespaceCmdResult[None]: ...

    def setDateTime(self, year: int, month: int, day: int, hour: int, minute: int, second: int) -> ThreespaceCmdResult[None]: ...
    def getDateTime(self) -> ThreespaceCmdResult[list[int]]: ...

    def getRawGyroRate(self, id: int) -> ThreespaceCmdResult[list[float]]: ...
    def getRawAccelVec(self, id: int) -> ThreespaceCmdResult[list[float]]: ...
    def getRawMagVec(self, id: int) -> ThreespaceCmdResult[list[float]]: ...

    def eeptsStart(self) -> ThreespaceCmdResult[None]: ...
    def eeptsStop(self) -> ThreespaceCmdResult[None]: ...
    def eeptsGetOldestStep(self) -> ThreespaceCmdResult[list]: ...
    def eeptsGetNewestStep(self) -> ThreespaceCmdResult[list]: ...   
    def eeptsGetNumStepsAvailable(self) -> ThreespaceCmdResult[int]: ...    
    def eeptsInsertGPS(self, latitude: float, longitude: float) -> ThreespaceCmdResult[None]: ...
    def eeptsAutoOffset(self) -> ThreespaceCmdResult[None]: ...

    def getStreamingLabel(self, cmd_num: int) -> ThreespaceCmdResult[str]: ...
    def getStreamingBatch(self) -> ThreespaceCmdResult[list]: ...
    def startStreaming(self) -> ThreespaceCmdResult[None]: ...
    def stopStreaming(self) -> ThreespaceCmdResult[None]: ...
    def pauseLogStreaming(self, pause: bool) -> ThreespaceCmdResult[None]: ...

    def getDateTimeString(self) -> ThreespaceCmdResult[str]: ...
    def getTimestamp(self) -> ThreespaceCmdResult[int]: ...

    def tareWithCurrentOrientation(self) -> ThreespaceCmdResult[None]: ...
    def setBaseTareWithCurrentOrientation(self) -> ThreespaceCmdResult[None]: ...

    def resetFilter(self) -> ThreespaceCmdResult[None]: ...
    def getNumDebugMessages(self) -> ThreespaceCmdResult[int]: ...
    def getOldestDebugMessage(self) -> ThreespaceCmdResult[str]: ...
    def selfTest(self) -> ThreespaceCmdResult[int]: ...

    def beginPassiveAutoCalibration(self, enabled_bitfield: int) -> ThreespaceCmdResult[None]: ...
    def getActivePassiveAutoCalibration(self) -> ThreespaceCmdResult[int]: ...
    def beginActiveAutoCalibration(self) -> ThreespaceCmdResult[None]: ...
    def isActiveAutoCalibrationActive(self) -> ThreespaceCmdResult[int]: ... 

    def getLastLogCursorInfo(self) -> ThreespaceCmdResult[tuple[int,str]]: ... 
    def getNextDirectoryItem(self) -> ThreespaceCmdResult[list[int,str,int]]: ...
    def changeDirectory(self, path: str) -> ThreespaceCmdResult[None]: ...   
    def openFile(self, path: str) -> ThreespaceCmdResult[None]: ... 
    def closeFile(self) -> ThreespaceCmdResult[None]: ...  
    def fileGetRemainingSize(self) -> ThreespaceCmdResult[int]: ...
    def fileReadLine(self) -> ThreespaceCmdResult[str]: ... 
    def fileReadBytes(self, num_bytes: int) -> ThreespaceCmdResult[bytes]: ...
    def deleteFile(self, path: str) -> ThreespaceCmdResult[None]: ...
    def setCursor(self, cursor_index: int) -> ThreespaceCmdResult[None]: ...
    def fileStartStream(self) -> ThreespaceCmdResult[int]: ...
    def fileStopStream(self) -> ThreespaceCmdResult[None]: ...

    def getBatteryCurrent(self) -> ThreespaceCmdResult[float]: ...
    def getBatteryVoltage(self) -> ThreespaceCmdResult[float]: ...
    def getBatteryPercent(self) -> ThreespaceCmdResult[int]: ...
    def getBatteryStatus(self) -> ThreespaceCmdResult[int]: ... 

    def getGpsActiveState(self) -> ThreespaceCmdResult[bool]: ...
    def getGpsCoord(self) -> ThreespaceCmdResult[list[float]]: ...
    def getGpsAltitude(self) -> ThreespaceCmdResult[float]: ...
    def getGpsFixState(self) -> ThreespaceCmdResult[int]: ...
    def getGpsHdop(self) -> ThreespaceCmdResult[float]: ...
    def getGpsSatellites(self) -> ThreespaceCmdResult[int]: ...

    def commitSettings(self) -> ThreespaceCmdResult[None]: ...
    def softwareReset(self): ...
    def enterBootloader(self): ...      

    def getLedColor(self) -> ThreespaceCmdResult[list[float]]: ...
    def getButtonState(self) -> ThreespaceCmdResult[int]: ...
                                               
               

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

def threespaceSetSettingsStringToDict(setting_string: str):
    d = {}
    for item in setting_string.split(';'):
        result = item.split('=')
        key = result[0]
        if len(result) == 1:
            value = None
        else:
            value = '='.join(result[1:]) #In case = was part of the value, do a join
        
        d[key] = value
    return d