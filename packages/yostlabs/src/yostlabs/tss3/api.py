from yostlabs.communication.base import ThreespaceComClass
from yostlabs.communication.serial import ThreespaceSerialComClass

from yostlabs.tss3.consts import *
from yostlabs.tss3.commands import *
from yostlabs.tss3.settings import *
from yostlabs.tss3.types import ThreespaceCmdResult, ThreespaceBootloaderInfo, \
    ThreespaceHardwareVersion, ThreespaceHeader, ThreespaceHeaderInfo
from yostlabs.tss3.errors import (
    ThreespaceError,
    DiscoveryError,
    SensorConnectionError,
    ResponseError,
    ResponseTimeoutError,
    ChecksumMismatchError,
    SettingError,
    UnregisteredKeyError,
    InvalidKeyError,
    SettingAccessError,
    UnsupportedCommandError,
)

from enum import Enum
from collections.abc import Callable
from typing import Any

import struct
import types
import inspect
import time
import warnings

#Response codes for when awaiting a command response. Used to determine if successfully parsed a response,
#there was an error, or an intermediate response was detected.
THREESPACE_AWAIT_COMMAND_FOUND = 0
THREESPACE_AWAIT_COMMAND_TIMEOUT = 1
THREESPACE_AWAIT_BOOTLOADER = 2

#Update Response Codes
THREESPACE_UPDATE_COMMAND_PARSED = 0
THREESPACE_UPDATE_COMMAND_NOT_ENOUGH_DATA = 1
THREESPACE_UPDATE_COMMAND_MISALIGNED = 2

#Required for the API to work. The API will keep these enabled at all times.
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
                raise DiscoveryError("Failed to auto discover com port")   
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
            start_time = time.perf_counter()
            while time.perf_counter() - start_time < refresh_timeout: #Wait up to refresh time for a new message
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
        version = self.readVersionFirmware()
        if version != self.firmware_version:
            self.firmware_version = version
            self.__initialize_commands()
    
        self.__reinit_firmware()
        
        self.valid_mags = self.__str_list_to_int_list(self.readValidMags())
        self.valid_accels = self.__str_list_to_int_list(self.readValidAccels())
        self.valid_gyros = self.__str_list_to_int_list(self.readValidGyros())
        self.valid_baros = self.__str_list_to_int_list(self.readValidBaros())

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

        self.__cache_serial_number(self.readSerialNumber())
        self.__empty_debug_cache()
        self.immediate_debug = self.readDebugMode() #Needed for some startup processes when restarting

        #Now reinitialize the cached settings
        self.__cache_header_settings()
        self.__cache_streaming_settings()

    def __initialize_commands(self):
        self.commands: list[ThreespaceCommand] = [None] * 256
        self.getStreamingBatchCommand: ThreespaceGetStreamingBatchCommand = None
        self.funcs = {}

        valid_commands = self.readValidCommands()
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

    def __str_list_to_int_list(self, str_list: str):
        return [int(v) for v in str_list.split(',') if v != '']

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
        header = self.readHeader()
        #API requires these bits to be enabled, so don't let them be disabled
        required_header = header | THREESPACE_REQUIRED_HEADER
        if header == self.header_info.bitfield and header == required_header: return #Nothing to update
        
        #Don't allow the header to change while streaming
        #This is to prevent a situation where the header for streaming and commands are different
        #since streaming caches the header. This would cause an issue where the echo byte could be in separate
        #positions, causing a situation where parsing a command and streaming at the same time breaks since it thinks both are valid cmd echoes.
        if self.is_streaming:
            self.log("Preventing header change due to currently streaming")
            self.writeHeader(self.header_info.bitfield)
            return
        
        if required_header != header:
            self.log(f"Forcing header checksum, echo, and length enabled")
            self.writeHeader(required_header)
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

    def __cache_debug_mode(self):
        self.immediate_debug = self.readDebugMode()

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
                raise SensorConnectionError("Sensor connection lost")
        
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

#---------------------------------------BINARY SETTINGS PROTOCOL------------------------------------------------

    def read_settings(self, *keys: str) -> dict[str, Any]:
        """
        Read multiple settings at once using the binary settings protocol.
        The values will be returned in a dictionary with the setting keys as the keys and the parsed values as the values.
        The values will be parsed according to their data type, not just as strings.
        """
        self.check_dirty()

        keystr = ';'.join(keys)
        if len(keystr) > THREESPACE_MAX_CMD_LEN-2: #-2 for room for null terminator and checksum if using binary format
            raise ValueError("Too many settings in one read_settings call. Max str length is " + str(THREESPACE_MAX_CMD_LEN-2) + " but got " + str(len(keystr)))
        
        checksum = sum(ord(v) for v in keystr) % 256
        #StartByte, Message+Null Terminator, Checksum
        message = struct.pack(f"<B{len(keystr)}sBB", THREESPACE_BINARY_READ_SETTINGS_START_BYTE_HEADER, keystr.encode(), 0, checksum)
        self.com.write(message)

        #Figure out how much data to expect in the response.
        min_response_len = len(keystr)
        if ';' in keystr:
            min_response_len = len(keystr.split(';')[0])
        if min_response_len > len(THREESPACE_GET_SETTINGS_ERROR_RESPONSE):
            min_response_len = len(THREESPACE_GET_SETTINGS_ERROR_RESPONSE)
        min_response_len += (1 + THREESPACE_BINARY_SETTINGS_ID_SIZE) #Null terminator and header size

        if self.__await_read_settings_response(min_response_len) != THREESPACE_AWAIT_COMMAND_FOUND:
            raise ResponseTimeoutError("Failed to get read_settings response")
        
        #Read the setting header id
        self.com.read(THREESPACE_BINARY_SETTINGS_ID_SIZE) #Read pass the Header ID

        #Parse the actual settings
        return self.__parse_read_setting_response(keystr)

    def __parse_read_setting_response(self, keystring: str):
        settings = {}
        checksum = 0
        end_reached = False
        while not end_reached:
            key = self.com.read_until(b'\0')
            if key[-1] != 0:
                raise ResponseError("Failed to read setting key")
            key = key[:-1].decode()
            setting = threespace_setting_get(key)

            if setting is None:
                if key == THREESPACE_GET_SETTINGS_ERROR_RESPONSE:
                    raise InvalidKeyError(f"Failed to read setting, got error response from firmware for keystring: {keystring}")
                raise UnregisteredKeyError(f"Failed to read setting, unregistered key: {key}")
            if setting.out_format is None:
                raise SettingAccessError(f"Failed to read setting, setting does not have an out format: {key}")
            
            checksum += sum(ord(v) for v in key)

            #Now parse the value
            data, raw = setting.out_format.read_response(self.com)
            settings[key] = data
            checksum += sum(raw)

            #Check if more to read or if this is the end of the response
            buffer = self.com.read(1)
            checksum += buffer[0]
            if buffer[0] == 0:
                end_reached = True
            elif buffer[0] != ord(';'):
                raise ResponseError(f"Failed to read setting, expected ';' or null terminator but got: {buffer}")
        
        #Reading key values has finished, not check the checksum
        reported_checksum = self.com.read(1)
        if reported_checksum[0] != checksum % 256:
            raise ChecksumMismatchError(f"Failed to read setting, checksum does not match expected value. Expected {checksum % 256} but got {reported_checksum[0]} for keystring: {keystring}")

        return settings
            
    def __await_read_settings_response(self, min_len: int, check_bootloader: bool = False):
        #Minimum size. Bootloader check may not have header though so it can be smaller in that case.
        if min_len < THREESPACE_BINARY_SETTINGS_ID_SIZE and not check_bootloader:
            min_len = THREESPACE_BINARY_SETTINGS_ID_SIZE

        start_time = time.perf_counter()
        while time.perf_counter() - start_time < self.com.timeout:
            if self.com.length < min_len: continue
            
            #Check for bootloader response first since it may not have the header
            if check_bootloader:
                possible_bootloader_response = self.com.peek(2)
                if possible_bootloader_response == b"OK":
                    return THREESPACE_AWAIT_BOOTLOADER
                
                #Wasn't bootloader, wait for rest of response
                if self.com.length < THREESPACE_BINARY_SETTINGS_ID_SIZE: 
                    continue
            
            #Now check for the actual ID response
            id = self.com.peek(THREESPACE_BINARY_SETTINGS_ID_SIZE)
            id = struct.unpack("<I", id)[0]
            if id != THREESPACE_BINARY_READ_SETTINGS_ID:
                self.__internal_update(self.__try_peek_header())
                continue

            #The ID matched, now check to see if the response after looks like a setting
            possible_response = self.com.peek_until(b'\0')[THREESPACE_BINARY_SETTINGS_ID_SIZE:]
            if b'\0' not in possible_response:
                self.__internal_update(self.__try_peek_header())
                continue

            #Check to see if the response is an actual setting response
            key = possible_response[:-1].decode()
            setting = threespace_setting_get(key)
            if setting is None and key != THREESPACE_GET_SETTINGS_ERROR_RESPONSE:
                if key.isprintable():
                    raise UnregisteredKeyError(f"Failed to read setting, unregistered key: {key}")
                self.__internal_update(self.__try_peek_header())
                continue
            
            #Good enough, this is more then likely a read setting response.
            return THREESPACE_AWAIT_COMMAND_FOUND

        return THREESPACE_AWAIT_COMMAND_TIMEOUT

    #Can't just do if "header" in string because log_header_enabled exists and doesn't actually require caching the header
    HEADER_KEYS = ["header", "header_status", "header_timestamp", "header_echo", "header_checksum", "header_serial", "header_length"]
    def write_settings(self, **kwargs):
        self.check_dirty()

        #Check to see if debug mode is being updated. This must be done before sending the command so that the API can properly
        #handle the response if debug mode is being turned on
        if "debug_mode" in kwargs and int(kwargs["debug_mode"]) == 1:
            self.immediate_debug = True
        
        #Build cmd string and send
        cmd = bytearray([THREESPACE_BINARY_WRITE_SETTINGS_START_BYTE_HEADER])
        checksum = 0
        for key, value in kwargs.items():
            setting = threespace_setting_get(key)
            if setting is None:
                raise UnregisteredKeyError(f"Failed to write setting, unregistered key: {key}")
            if setting.in_format is None:
                raise SettingAccessError(f"Failed to write setting, key is not writable: {key}")
            
            #Add the key
            key_bytes = key.encode() + b'\0'
            cmd.extend(key_bytes)
            checksum += sum(key_bytes)

            #Add the value
            if hasattr(value, '__iter__') and not isinstance(value, (str, bytes, bytearray)):
                #Must unpack if list/tuple since format expects individual values
                value_bytes = setting.in_format.format_data(*value)
            else:
                #Singular Value
                value_bytes = setting.in_format.format_data(value)

            cmd.extend(value_bytes)
            checksum += sum(value_bytes)

            cmd.append(ord(';'))
            checksum += ord(';')

        #Done writing keys and values, remove the last ';' and add the null terminator
        checksum -= ord(';')
        cmd[-1] = 0
        cmd.append(checksum % 256)
        self.com.write(cmd)

        #Await Response
        if self.__await_write_settings_response(len(kwargs)) != THREESPACE_AWAIT_COMMAND_FOUND:
            raise ResponseTimeoutError("Failed to get write_settings response")

        #Read in Response
        self.com.read(THREESPACE_BINARY_SETTINGS_ID_SIZE) #Read pass the header ID
        err, num_successes, checksum = self.com.read(3)
        if err:
            self.log(f"Err setting {cmd}: {err=} {num_successes=}")

        if any(v in kwargs.keys() for v in ("default", "reboot")):
            self.log("Settings that require reboot changed, marking cache as dirty")
            self.set_cached_settings_dirty()

        #Handle caching any settings that need to be cached when changed.
        if b"header" in cmd:
            if any(v in kwargs.keys() for v in ThreespaceSensor.HEADER_KEYS):
                self.__cache_header_settings()
        
        if "stream_slots" in kwargs:
            self.__cache_streaming_settings()

        if "debug_mode" in kwargs:
            self.__cache_debug_mode()

        return err, num_successes

    def __await_write_settings_response(self, num_keys: int):
        start_time = time.perf_counter()
        while time.perf_counter() - start_time < self.com.timeout:
            if self.com.length < THREESPACE_BINARY_WRITE_SETTING_WITH_HEADER_RESPONSE_LEN:
                continue

            #Check for the ID/ECHO for writing settings
            id = self.com.peek(THREESPACE_BINARY_SETTINGS_ID_SIZE)
            id = struct.unpack("<I", id)[0]
            if id != THREESPACE_BINARY_WRITE_SETTINGS_ID:
                self.__internal_update(self.__try_peek_header())
                continue

            #Peek full response and validate checksum and proper format
            response = self.com.peek(THREESPACE_BINARY_WRITE_SETTING_WITH_HEADER_RESPONSE_LEN)[THREESPACE_BINARY_SETTINGS_ID_SIZE:]
            err, num_successes, checksum = response
            if  (checksum != err + num_successes) or        \
                (err == 0 and num_successes != num_keys) or \
                (err != 0 and num_successes >= num_keys) or \
                (num_successes > num_keys):
                self.__internal_update(self.__try_peek_header())
                continue

            return THREESPACE_AWAIT_COMMAND_FOUND
    
        return THREESPACE_AWAIT_COMMAND_TIMEOUT

#-------------------------------------ASCII SETTINGS PROTOCOL------------------------------------------------

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

    def write_settings_ascii(self, param_string: str = None, **kwargs):
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
        param_dict = threespace_settings_string_to_dict(cmd[1:-1])

        #Must enable this before sending the set so can properly handle reading the response
        if "debug_mode=1" in cmd:
            self.immediate_debug = True

        #Send cmd
        self.com.write(cmd.encode())

        #Default values
        err = 3
        num_successes = 0

        response = self.__await_set_settings_ascii(self.com.timeout)
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

    def read_settings_ascii(self, *args: str, format="Mixed") -> dict[str, str] | str:
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
        
        
        response = self.__await_get_settings_ascii(min_resp_length, timeout=self.com.timeout)
        if response == THREESPACE_AWAIT_COMMAND_TIMEOUT:
            self.log("Requested:", cmd)
            self.log("Potential response:", self.com.peekline())
            raise ResponseTimeoutError("Failed to receive get_settings response")      

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
    
    def set_settings(self, param_string: str = None, **kwargs):
        """Deprecated: Use write_settings instead."""
        warnings.warn("set_settings is deprecated, use write_settings instead.", DeprecationWarning, stacklevel=2)
        return self.write_settings_ascii(param_string, **kwargs)

    def get_settings(self, *args: str, format="Mixed") -> dict[str, str] | str:
        """Deprecated: Use read_settings instead."""
        warnings.warn("get_settings is deprecated, use read_settings instead.", DeprecationWarning, stacklevel=2)
        return self.read_settings_ascii(*args, format=format)

    #-----------Base Settings Parsing----------------

    def __await_set_settings_ascii(self, timeout=2):
        start_time = time.perf_counter()
        MINIMUM_LENGTH = len("0,0\r\n")
        MAXIMUM_LENGTH = len("255,255\r\n")

        while True:
            remaining_time = timeout - (time.perf_counter() - start_time)
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
            
    def __await_get_settings_ascii(self, min_resp_length: int, timeout=2, check_bootloader=False):
        start_time = time.perf_counter()

        while True:
            remaining_time = timeout - (time.perf_counter() - start_time)
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
        
        start_time = time.perf_counter()

        #Update the streaming until the result for this command is next in the buffer
        while True:
            if time.perf_counter() - start_time > timeout:
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
            raise ResponseTimeoutError(f"Failed to get response to command {cmd.info.name}")

        return self.read_and_parse_command(cmd)
    
    def __invalid_command(self, *args):
        raise UnsupportedCommandError("This command is not available on the connected sensor.")

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
        slots: str = self.readStreamSlots()
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
        streaming = self.readLogImmediateOutput()
        if streaming:
            self.write_settings(log_immediate_output_header_enabled=1,
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
                raise SensorConnectionError("Failed to reconnect to sensor in bootloader")
        in_bootloader = self.__check_bootloader_status()
        if not in_bootloader:
            raise SensorConnectionError("Failed to enter bootloader")
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
        response = self.__await_get_settings_ascii(2, check_bootloader=True)
        if response == THREESPACE_AWAIT_COMMAND_TIMEOUT: 
            self.log("Requested Bootloader, Got:")
            self.log(self.com.peek(self.com.length))
            raise DiscoveryError("Failed to discover bootloader or firmware.")
        if response == THREESPACE_AWAIT_BOOTLOADER:
            bootloader = True
            time.sleep(0.1) #Give time for all the OK responses to come in
            self.com.read_all() #Remove the rest of the OK responses or the rest of the <KEY_ERROR> response
        elif response == THREESPACE_AWAIT_COMMAND_FOUND:
            bootloader = False
            self.com.readline() #Clear the setting, no need to parse
        else:
            raise DiscoveryError("Failed to detect if in bootloader or firmware")
        return bootloader
    
    def bootloader_get_sn(self):
        self.com.write("Q".encode())
        result = self.com.read(9) #9 Because it includes a line feed for reasons
        if len(result) != 9:
            raise ResponseError(f"Failed to read serial number from bootloader: expected 9 bytes, got {len(result)}")
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
                raise SensorConnectionError("Failed to reconnect to sensor in firmware")
        in_bootloader = self.__check_bootloader_status()
        if in_bootloader:
            raise SensorConnectionError("Failed to exit bootloader")
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

#---------------------------------------SETTING PROTOTYPES----------------------------------------------
    def restoreDefaultSettings(self) -> int:
        return self.write_settings(default=None)[0]

    def readAllSettings(self) -> dict[str,Any]:
        return self.read_settings("all")["all"]

    def readAllWritableSettings(self) -> dict[str,Any]:
        return self.read_settings("settings")["settings"]

    def readSerialNumber(self) -> int:
        return self.read_settings("serial_number")["serial_number"]

    def writeTimestamp(self, microseconds: int) -> int:
        return self.write_settings(timestamp=microseconds)[0]

    def readTimestamp(self) -> int:
        return self.read_settings("timestamp")["timestamp"]

    def writeLedMode(self, value: int) -> int:
        return self.write_settings(led_mode=value)[0]

    def readLedMode(self) -> int:
        return self.read_settings("led_mode")["led_mode"]

    def writeLedRgb(self, value: list[float]) -> int:
        return self.write_settings(led_rgb=value)[0]

    def readLedRgb(self) -> tuple[float]:
        return self.read_settings("led_rgb")["led_rgb"]

    def readVersionFirmware(self) -> str:
        return self.read_settings("version_firmware")["version_firmware"]

    def readVersionHardware(self) -> str:
        return self.read_settings("version_hardware")["version_hardware"]

    def readUpdateRateSensor(self) -> int:
        return self.read_settings("update_rate_sensor")["update_rate_sensor"]

    def writeHeader(self, value: int) -> int:
        return self.write_settings(header=value)[0]

    def readHeader(self) -> int:
        return self.read_settings("header")["header"]

    def writeHeaderStatusEnabled(self, value: int) -> int:
        return self.write_settings(header_status=value)[0]

    def readHeaderStatusEnabled(self) -> int:
        return self.read_settings("header_status")["header_status"]

    def writeHeaderTimestampEnabled(self, value: int) -> int:
        return self.write_settings(header_timestamp=value)[0]

    def readHeaderTimestampEnabled(self) -> int:
        return self.read_settings("header_timestamp")["header_timestamp"]

    def writeHeaderEchoEnabled(self, value: int) -> int:
        return self.write_settings(header_echo=value)[0]

    def readHeaderEchoEnabled(self) -> int:
        return self.read_settings("header_echo")["header_echo"]

    def writeHeaderChecksumEnabled(self, value: int) -> int:
        return self.write_settings(header_checksum=value)[0]

    def readHeaderChecksumEnabled(self) -> int:
        return self.read_settings("header_checksum")["header_checksum"]

    def writeHeaderSerialEnabled(self, value: int) -> int:
        return self.write_settings(header_serial=value)[0]

    def readHeaderSerialEnabled(self) -> int:
        return self.read_settings("header_serial")["header_serial"]

    def writeHeaderLengthEnabled(self, value: int) -> int:
        return self.write_settings(header_length=value)[0]

    def readHeaderLengthEnabled(self) -> int:
        return self.read_settings("header_length")["header_length"]

    def readValidCommands(self) -> str:
        return self.read_settings("valid_commands")["valid_commands"]

    def writeCpuSpeed(self, value: int) -> int:
        return self.write_settings(cpu_speed=value)[0]

    def readCpuSpeed(self) -> int:
        return self.read_settings("cpu_speed")["cpu_speed"]

    def readCpuSpeedCur(self) -> int:
        return self.read_settings("cpu_speed_cur")["cpu_speed_cur"]

    def writePmMode(self, value: int) -> int:
        return self.write_settings(pm_mode=value)[0]

    def writePmIdleEnabled(self, value: int) -> int:
        return self.write_settings(pm_idle_enabled=value)[0]

    def readPmIdleEnabled(self) -> int:
        return self.read_settings("pm_idle_enabled")["pm_idle_enabled"]

    def writeStreamSlots(self, value: str) -> int:
        return self.write_settings(stream_slots=value)[0]

    def readStreamSlots(self) -> str:
        return self.read_settings("stream_slots")["stream_slots"]

    def writeStreamInterval(self, value: int) -> int:
        return self.write_settings(stream_interval=value)[0]

    def readStreamInterval(self) -> int:
        return self.read_settings("stream_interval")["stream_interval"]

    def writeStreamHz(self, value: float) -> int:
        return self.write_settings(stream_hz=value)[0]

    def readStreamHz(self) -> float:
        return self.read_settings("stream_hz")["stream_hz"]

    def writeStreamDuration(self, value: float) -> int:
        return self.write_settings(stream_duration=value)[0]

    def readStreamDuration(self) -> float:
        return self.read_settings("stream_duration")["stream_duration"]

    def writeStreamDelay(self, value: float) -> int:
        return self.write_settings(stream_delay=value)[0]

    def readStreamDelay(self) -> float:
        return self.read_settings("stream_delay")["stream_delay"]

    def writeStreamMode(self, value: int) -> int:
        return self.write_settings(stream_mode=value)[0]

    def readStreamMode(self) -> int:
        return self.read_settings("stream_mode")["stream_mode"]

    def writeStreamCount(self, value: int) -> int:
        return self.write_settings(stream_count=value)[0]

    def readStreamCount(self) -> int:
        return self.read_settings("stream_count")["stream_count"]

    def readStreamableCommands(self) -> str:
        return self.read_settings("streamable_commands")["streamable_commands"]

    def writeDebugLevel(self, value: int) -> int:
        return self.write_settings(debug_level=value)[0]

    def readDebugLevel(self) -> int:
        return self.read_settings("debug_level")["debug_level"]

    def writeDebugModule(self, value: int) -> int:
        return self.write_settings(debug_module=value)[0]

    def readDebugModule(self) -> int:
        return self.read_settings("debug_module")["debug_module"]

    def writeDebugMode(self, value: int) -> int:
        return self.write_settings(debug_mode=value)[0]

    def readDebugMode(self) -> int:
        return self.read_settings("debug_mode")["debug_mode"]

    def writeDebugLed(self, value: int) -> int:
        return self.write_settings(debug_led=value)[0]

    def readDebugLed(self) -> int:
        return self.read_settings("debug_led")["debug_led"]

    def writeDebugFault(self, value: int) -> int:
        return self.write_settings(debug_fault=value)[0]

    def readDebugFault(self) -> int:
        return self.read_settings("debug_fault")["debug_fault"]

    def writeDebugWdt(self, value: int) -> int:
        return self.write_settings(debug_wdt=value)[0]

    def readDebugWdt(self) -> int:
        return self.read_settings("debug_wdt")["debug_wdt"]

    def writeAxisOrder(self, value: str) -> int:
        return self.write_settings(axis_order=value)[0]

    def readAxisOrder(self) -> str:
        return self.read_settings("axis_order")["axis_order"]

    def writeAxisOrderC(self, value: str) -> int:
        return self.write_settings(axis_order_c=value)[0]

    def readAxisOrderC(self) -> str:
        return self.read_settings("axis_order_c")["axis_order_c"]

    def writeAxisOffsetEnabled(self, value: int) -> int:
        return self.write_settings(axis_offset_enabled=value)[0]

    def readAxisOffsetEnabled(self) -> int:
        return self.read_settings("axis_offset_enabled")["axis_offset_enabled"]

    def writeEulerOrder(self, value: str) -> int:
        return self.write_settings(euler_order=value)[0]

    def readEulerOrder(self) -> str:
        return self.read_settings("euler_order")["euler_order"]

    def readUpdateRateFilter(self) -> int:
        return self.read_settings("update_rate_filter")["update_rate_filter"]

    def readUpdateRateSms(self) -> int:
        return self.read_settings("update_rate_sms")["update_rate_sms"]

    def writeOffset(self, value: list[float]) -> int:
        return self.write_settings(offset=value)[0]

    def readOffset(self) -> tuple[float]:
        return self.read_settings("offset")["offset"]

    def writeBaseOffset(self, value: list[float]) -> int:
        return self.write_settings(base_offset=value)[0]

    def readBaseOffset(self) -> tuple[float]:
        return self.read_settings("base_offset")["base_offset"]

    def writeTareQuat(self, value: list[float]) -> int:
        return self.write_settings(tare_quat=value)[0]

    def readTareQuat(self) -> tuple[float]:
        return self.read_settings("tare_quat")["tare_quat"]

    def writeTareAutoBase(self, value: int) -> int:
        return self.write_settings(tare_auto_base=value)[0]

    def readTareAutoBase(self) -> int:
        return self.read_settings("tare_auto_base")["tare_auto_base"]

    def writeBaseTare(self, value: list[float]) -> int:
        return self.write_settings(base_tare=value)[0]

    def readBaseTare(self) -> tuple[float]:
        return self.read_settings("base_tare")["base_tare"]

    def writeTareMat(self, value: list[float]) -> int:
        return self.write_settings(tare_mat=value)[0]

    def readTareMat(self) -> tuple[float]:
        return self.read_settings("tare_mat")["tare_mat"]

    def writeRunningAvgOrient(self, value: float) -> int:
        return self.write_settings(running_avg_orient=value)[0]

    def readRunningAvgOrient(self) -> float:
        return self.read_settings("running_avg_orient")["running_avg_orient"]

    def writeFilterMode(self, value: int) -> int:
        return self.write_settings(filter_mode=value)[0]

    def readFilterMode(self) -> int:
        return self.read_settings("filter_mode")["filter_mode"]

    def writeFilterMrefMode(self, value: int) -> int:
        return self.write_settings(filter_mref_mode=value)[0]

    def readFilterMrefMode(self) -> int:
        return self.read_settings("filter_mref_mode")["filter_mref_mode"]

    def writeFilterMref(self, value: list[float]) -> int:
        return self.write_settings(filter_mref=value)[0]

    def readFilterMref(self) -> tuple[float]:
        return self.read_settings("filter_mref")["filter_mref"]

    def writeFilterMrefGps(self, value: list[float]) -> int:
        return self.write_settings(filter_mref_gps=value)[0]

    def writeFilterMrefDip(self, value: float) -> int:
        return self.write_settings(filter_mref_dip=value)[0]

    def readFilterMrefDip(self) -> float:
        return self.read_settings("filter_mref_dip")["filter_mref_dip"]

    def writeFilterConfThresholds(self, min: float, max: float, cap: float) -> int:
        return self.write_settings(filter_conf_thresholds=[min, max, cap])[0]

    def readFilterConfThresholds(self) -> tuple[float, float, float]:
        return self.read_settings("filter_conf_thresholds")["filter_conf_thresholds"]

    def readValidAccels(self) -> str:
        return self.read_settings("valid_accels")["valid_accels"]

    def readValidGyros(self) -> str:
        return self.read_settings("valid_gyros")["valid_gyros"]

    def readValidMags(self) -> str:
        return self.read_settings("valid_mags")["valid_mags"]

    def readValidBaros(self) -> str:
        return self.read_settings("valid_baros")["valid_baros"]

    def readValidComponents(self) -> str:
        return self.read_settings("valid_components")["valid_components"]

    def writePrimaryAccel(self, value: str) -> int:
        return self.write_settings(primary_accel=value)[0]

    def readPrimaryAccel(self) -> str:
        return self.read_settings("primary_accel")["primary_accel"]

    def writePrimaryGyro(self, value: str) -> int:
        return self.write_settings(primary_gyro=value)[0]

    def readPrimaryGyro(self) -> str:
        return self.read_settings("primary_gyro")["primary_gyro"]

    def writePrimaryMag(self, value: str) -> int:
        return self.write_settings(primary_mag=value)[0]

    def readPrimaryMag(self) -> str:
        return self.read_settings("primary_mag")["primary_mag"]

    def writePrimarySensorRfade(self, value: float) -> int:
        return self.write_settings(primary_sensor_rfade=value)[0]

    def readPrimarySensorRfade(self) -> float:
        return self.read_settings("primary_sensor_rfade")["primary_sensor_rfade"]

    def writeMagBiasMode(self, value: int) -> int:
        return self.write_settings(mag_bias_mode=value)[0]

    def readMagBiasMode(self) -> int:
        return self.read_settings("mag_bias_mode")["mag_bias_mode"]

    def writeOdrAll(self, value: int) -> int:
        return self.write_settings(odr_all=value)[0]

    def writeOdrAccelAll(self, value: int) -> int:
        return self.write_settings(odr_accel=value)[0]

    def writeOdrGyroAll(self, value: int) -> int:
        return self.write_settings(odr_gyro=value)[0]

    def writeOdrMagAll(self, value: int) -> int:
        return self.write_settings(odr_mag=value)[0]

    def writeOdrBaroAll(self, value: int) -> int:
        return self.write_settings(odr_baro=value)[0]

    def writeAccelEnabled(self, value: int) -> int:
        return self.write_settings(accel_enabled=value)[0]

    def readAccelEnabled(self) -> int:
        return self.read_settings("accel_enabled")["accel_enabled"]

    def writeGyroEnabled(self, value: int) -> int:
        return self.write_settings(gyro_enabled=value)[0]

    def readGyroEnabled(self) -> int:
        return self.read_settings("gyro_enabled")["gyro_enabled"]

    def writeMagEnabled(self, value: int) -> int:
        return self.write_settings(mag_enabled=value)[0]

    def readMagEnabled(self) -> int:
        return self.read_settings("mag_enabled")["mag_enabled"]

    def writeCalibMatAccel(self, id: int, value: list[float]) -> int:
        param = { "calib_mat_accel%d" % id : value }
        return self.write_settings(**param)[0]

    def readCalibMatAccel(self, id: int) -> tuple[float]:
        name = "calib_mat_accel%d" % id
        return self.read_settings(name)[name]

    def writeCalibBiasAccel(self, id: int, value: list[float]) -> int:
        param = { "calib_bias_accel%d" % id : value }
        return self.write_settings(**param)[0]

    def readCalibBiasAccel(self, id: int) -> tuple[float]:
        name = "calib_bias_accel%d" % id
        return self.read_settings(name)[name]

    def writeRangeAccel(self, id: int, value: int) -> int:
        param = { "range_accel%d" % id : value }
        return self.write_settings(**param)[0]

    def readRangeAccel(self, id: int) -> int:
        name = "range_accel%d" % id
        return self.read_settings(name)[name]

    def readValidRangesAccel(self, id: int) -> str:
        name = "valid_ranges_accel%d" % id
        return self.read_settings(name)[name]

    def writeOversampleAccel(self, id: int, value: int) -> int:
        param = { "oversample_accel%d" % id : value }
        return self.write_settings(**param)[0]

    def readOversampleAccel(self, id: int) -> int:
        name = "oversample_accel%d" % id
        return self.read_settings(name)[name]

    def writeRunningAvgAccel(self, id: int, value: float) -> int:
        param = { "running_avg_accel%d" % id : value }
        return self.write_settings(**param)[0]

    def readRunningAvgAccel(self, id: int) -> float:
        name = "running_avg_accel%d" % id
        return self.read_settings(name)[name]

    def writeOdrAccel(self, id: int, value: int) -> int:
        param = { "odr_accel%d" % id : value }
        return self.write_settings(**param)[0]

    def readOdrAccel(self, id: int) -> int:
        name = "odr_accel%d" % id
        return self.read_settings(name)[name]

    def readUpdateRateAccel(self, id: int) -> float:
        name = "update_rate_accel%d" % id
        return self.read_settings(name)[name]

    def readNoiseProfileAccel(self, id: int) -> tuple[float, float, float, float, int]:
        name = "noise_profile_accel%d" % id
        return self.read_settings(name)[name]

    def writeCalibMatGyro(self, id: int, value: list[float]) -> int:
        param = { "calib_mat_gyro%d" % id : value }
        return self.write_settings(**param)[0]

    def readCalibMatGyro(self, id: int) -> tuple[float]:
        name = "calib_mat_gyro%d" % id
        return self.read_settings(name)[name]

    def writeCalibBiasGyro(self, id: int, value: list[float]) -> int:
        param = { "calib_bias_gyro%d" % id : value }
        return self.write_settings(**param)[0]

    def readCalibBiasGyro(self, id: int) -> tuple[float]:
        name = "calib_bias_gyro%d" % id
        return self.read_settings(name)[name]

    def writeRangeGyro(self, id: int, value: int) -> int:
        param = { "range_gyro%d" % id : value }
        return self.write_settings(**param)[0]

    def readRangeGyro(self, id: int) -> int:
        name = "range_gyro%d" % id
        return self.read_settings(name)[name]

    def readValidRangesGyro(self, id: int) -> str:
        name = "valid_ranges_gyro%d" % id
        return self.read_settings(name)[name]

    def writeOversampleGyro(self, id: int, value: int) -> int:
        param = { "oversample_gyro%d" % id : value }
        return self.write_settings(**param)[0]

    def readOversampleGyro(self, id: int) -> int:
        name = "oversample_gyro%d" % id
        return self.read_settings(name)[name]

    def writeRunningAvgGyro(self, id: int, value: float) -> int:
        param = { "running_avg_gyro%d" % id : value }
        return self.write_settings(**param)[0]

    def readRunningAvgGyro(self, id: int) -> float:
        name = "running_avg_gyro%d" % id
        return self.read_settings(name)[name]

    def writeOdrGyro(self, id: int, value: int) -> int:
        param = { "odr_gyro%d" % id : value }
        return self.write_settings(**param)[0]

    def readOdrGyro(self, id: int) -> int:
        name = "odr_gyro%d" % id
        return self.read_settings(name)[name]

    def readUpdateRateGyro(self, id: int) -> float:
        name = "update_rate_gyro%d" % id
        return self.read_settings(name)[name]

    def readNoiseProfileGyro(self, id: int) -> tuple[float, float, float, float, int]:
        name = "noise_profile_gyro%d" % id
        return self.read_settings(name)[name]

    def writeCalibMatMag(self, id: int, value: list[float]) -> int:
        param = { "calib_mat_mag%d" % id : value }
        return self.write_settings(**param)[0]

    def readCalibMatMag(self, id: int) -> tuple[float]:
        name = "calib_mat_mag%d" % id
        return self.read_settings(name)[name]

    def writeCalibBiasMag(self, id: int, value: list[float]) -> int:
        param = { "calib_bias_mag%d" % id : value }
        return self.write_settings(**param)[0]

    def readCalibBiasMag(self, id: int) -> tuple[float]:
        name = "calib_bias_mag%d" % id
        return self.read_settings(name)[name]

    def writeRangeMag(self, id: int, value: int) -> int:
        param = { "range_mag%d" % id : value }
        return self.write_settings(**param)[0]

    def readRangeMag(self, id: int) -> int:
        name = "range_mag%d" % id
        return self.read_settings(name)[name]

    def readValidRangesMag(self, id: int) -> str:
        name = "valid_ranges_mag%d" % id
        return self.read_settings(name)[name]

    def writeOversampleMag(self, id: int, value: int) -> int:
        param = { "oversample_mag%d" % id : value }
        return self.write_settings(**param)[0]

    def readOversampleMag(self, id: int) -> int:
        name = "oversample_mag%d" % id
        return self.read_settings(name)[name]

    def writeRunningAvgMag(self, id: int, value: float) -> int:
        param = { "running_avg_mag%d" % id : value }
        return self.write_settings(**param)[0]

    def readRunningAvgMag(self, id: int) -> float:
        name = "running_avg_mag%d" % id
        return self.read_settings(name)[name]

    def writeOdrMag(self, id: int, value: int) -> int:
        param = { "odr_mag%d" % id : value }
        return self.write_settings(**param)[0]

    def readOdrMag(self, id: int) -> int:
        name = "odr_mag%d" % id
        return self.read_settings(name)[name]

    def readUpdateRateMag(self, id: int) -> float:
        name = "update_rate_mag%d" % id
        return self.read_settings(name)[name]

    def readNoiseProfileMag(self, id: int) -> tuple[float, float, float, float, int]:
        name = "noise_profile_mag%d" % id
        return self.read_settings(name)[name]

    def writeCalibBiasBaro(self, id: int, value: float) -> int:
        param = { "calib_bias_baro%d" % id : value }
        return self.write_settings(**param)[0]

    def readCalibBiasBaro(self, id: int) -> float:
        name = "calib_bias_baro%d" % id
        return self.read_settings(name)[name]

    def writeCalibAltitudeBaro(self, id: int, value: float) -> int:
        param = { "calib_altitude_baro%d" % id : value }
        return self.write_settings(**param)[0]

    def writeOdrBaro(self, id: int, value: int) -> int:
        param = { "odr_baro%d" % id : value }
        return self.write_settings(**param)[0]

    def readOdrBaro(self, id: int) -> int:
        name = "odr_baro%d" % id
        return self.read_settings(name)[name]

    def readUpdateRateBaro(self, id: int) -> float:
        name = "update_rate_baro%d" % id
        return self.read_settings(name)[name]

    def writePtsOffsetQuat(self, value: list[float]) -> int:
        return self.write_settings(pts_offset_quat=value)[0]

    def readPtsOffsetQuat(self) -> tuple[float]:
        return self.read_settings("pts_offset_quat")["pts_offset_quat"]

    def restorePtsDefaultSettings(self) -> int:
        return self.write_settings(pts_default=None)[0]

    def readPtsSettings(self) -> dict[str,Any]:
        return self.read_settings("pts_settings")["pts_settings"]

    def writePtsPresetHand(self, value: int) -> int:
        return self.write_settings(pts_preset_hand=value)[0]

    def writePtsPresetMotion(self, value: int) -> int:
        return self.write_settings(pts_preset_motion=value)[0]

    def writePtsPresetHeading(self, value: int) -> int:
        return self.write_settings(pts_preset_heading=value)[0]

    def writePtsDebugLevel(self, value: int) -> int:
        return self.write_settings(pts_debug_level=value)[0]

    def readPtsDebugLevel(self) -> int:
        return self.read_settings("pts_debug_level")["pts_debug_level"]

    def writePtsDebugModule(self, value: int) -> int:
        return self.write_settings(pts_debug_module=value)[0]

    def readPtsDebugModule(self) -> int:
        return self.read_settings("pts_debug_module")["pts_debug_module"]

    def writePtsHeadingMode(self, value: int) -> int:
        return self.write_settings(pts_heading_mode=value)[0]

    def readPtsHeadingMode(self) -> int:
        return self.read_settings("pts_heading_mode")["pts_heading_mode"]

    def writePtsInitialHeadingMode(self, value: int) -> int:
        return self.write_settings(pts_initial_heading_mode=value)[0]

    def readPtsInitialHeadingMode(self) -> int:
        return self.read_settings("pts_initial_heading_mode")["pts_initial_heading_mode"]

    def writePtsHandHeadingMode(self, value: int) -> int:
        return self.write_settings(pts_hand_heading_mode=value)[0]

    def readPtsHandHeadingMode(self) -> int:
        return self.read_settings("pts_hand_heading_mode")["pts_hand_heading_mode"]

    def writePtsMagDeclination(self, value: float) -> int:
        return self.write_settings(pts_mag_declination=value)[0]

    def readPtsMagDeclination(self) -> float:
        return self.read_settings("pts_mag_declination")["pts_mag_declination"]

    def writePtsAutoDeclination(self, value: int) -> int:
        return self.write_settings(pts_auto_declination=value)[0]

    def readPtsAutoDeclination(self) -> int:
        return self.read_settings("pts_auto_declination")["pts_auto_declination"]

    def writePtsDiscardSlow(self, value: int) -> int:
        return self.write_settings(pts_discard_slow=value)[0]

    def readPtsDiscardSlow(self) -> int:
        return self.read_settings("pts_discard_slow")["pts_discard_slow"]

    def writePtsSegmentAxis(self, value: int) -> int:
        return self.write_settings(pts_segment_axis=value)[0]

    def readPtsSegmentAxis(self) -> int:
        return self.read_settings("pts_segment_axis")["pts_segment_axis"]

    def writePtsSegNoise(self, value: float) -> int:
        return self.write_settings(pts_seg_noise=value)[0]

    def readPtsSegNoise(self) -> float:
        return self.read_settings("pts_seg_noise")["pts_seg_noise"]

    def writePtsClassifierMode(self, value: int) -> int:
        return self.write_settings(pts_classifier_mode=value)[0]

    def readPtsClassifierMode(self) -> int:
        return self.read_settings("pts_classifier_mode")["pts_classifier_mode"]

    def writePtsClassifierMode2(self, value: int) -> int:
        return self.write_settings(pts_classifier_mode2=value)[0]

    def readPtsClassifierMode2(self) -> int:
        return self.read_settings("pts_classifier_mode2")["pts_classifier_mode2"]

    def writePtsLocationClassifierMode(self, value: int) -> int:
        return self.write_settings(pts_location_classifier_mode=value)[0]

    def readPtsLocationClassifierMode(self) -> int:
        return self.read_settings("pts_location_classifier_mode")["pts_location_classifier_mode"]

    def writePtsHandClassifierThreshold(self, value: float) -> int:
        return self.write_settings(pts_hand_classifier_threshold=value)[0]

    def readPtsHandClassifierThreshold(self) -> float:
        return self.read_settings("pts_hand_classifier_threshold")["pts_hand_classifier_threshold"]

    def writePtsDisabledTruthMotions(self, value: int) -> int:
        return self.write_settings(pts_disabled_truth_motions=value)[0]

    def readPtsDisabledTruthMotions(self) -> int:
        return self.read_settings("pts_disabled_truth_motions")["pts_disabled_truth_motions"]

    def writePtsDynamicSegmenterEnabled(self, value: int) -> int:
        return self.write_settings(pts_dynamic_segmenter_enabled=value)[0]

    def readPtsDynamicSegmenterEnabled(self) -> int:
        return self.read_settings("pts_dynamic_segmenter_enabled")["pts_dynamic_segmenter_enabled"]

    def writePtsEstimatorScalars(self, value: list[float]) -> int:
        return self.write_settings(pts_estimator_scalars=value)[0]

    def readPtsEstimatorScalars(self) -> tuple[float]:
        return self.read_settings("pts_estimator_scalars")["pts_estimator_scalars"]

    def writePtsAutoEstimatorScalarRate(self, value: int) -> int:
        return self.write_settings(pts_auto_estimator_scalar_rate=value)[0]

    def readPtsAutoEstimatorScalarRate(self) -> int:
        return self.read_settings("pts_auto_estimator_scalar_rate")["pts_auto_estimator_scalar_rate"]

    def writePtsRunningCorrection(self, value: int) -> int:
        return self.write_settings(pts_running_correction=value)[0]

    def readPtsRunningCorrection(self) -> int:
        return self.read_settings("pts_running_correction")["pts_running_correction"]

    def writePtsHandCorrection(self, value: int) -> int:
        return self.write_settings(pts_hand_correction=value)[0]

    def readPtsHandCorrection(self) -> int:
        return self.read_settings("pts_hand_correction")["pts_hand_correction"]

    def writePtsHeadingCorrectionMode(self, value: int) -> int:
        return self.write_settings(pts_heading_correction_mode=value)[0]

    def readPtsHeadingCorrectionMode(self) -> int:
        return self.read_settings("pts_heading_correction_mode")["pts_heading_correction_mode"]

    def writePtsHeadingMinDif(self, value: float) -> int:
        return self.write_settings(pts_heading_min_dif=value)[0]

    def readPtsHeadingMinDif(self) -> float:
        return self.read_settings("pts_heading_min_dif")["pts_heading_min_dif"]

    def writePtsHeadingResetConsistencies(self, value: int) -> int:
        return self.write_settings(pts_heading_reset_consistencies=value)[0]

    def readPtsHeadingResetConsistencies(self) -> int:
        return self.read_settings("pts_heading_reset_consistencies")["pts_heading_reset_consistencies"]

    def writePtsHeadingBacktrackEnabled(self, value: int) -> int:
        return self.write_settings(pts_heading_backtrack_enabled=value)[0]

    def readPtsHeadingBacktrackEnabled(self) -> int:
        return self.read_settings("pts_heading_backtrack_enabled")["pts_heading_backtrack_enabled"]

    def writePtsMotionCorrectionRadius(self, value: int) -> int:
        return self.write_settings(pts_motion_correction_radius=value)[0]

    def readPtsMotionCorrectionRadius(self) -> int:
        return self.read_settings("pts_motion_correction_radius")["pts_motion_correction_radius"]

    def writePtsMotionCorrectionConsistencyReq(self, value: int) -> int:
        return self.write_settings(pts_motion_correction_consistency_req=value)[0]

    def readPtsMotionCorrectionConsistencyReq(self) -> int:
        return self.read_settings("pts_motion_correction_consistency_req")["pts_motion_correction_consistency_req"]

    def writePtsOrientRefYThreshold(self, value: float) -> int:
        return self.write_settings(pts_orient_ref_y_threshold=value)[0]

    def readPtsOrientRefYThreshold(self) -> float:
        return self.read_settings("pts_orient_ref_y_threshold")["pts_orient_ref_y_threshold"]

    def readPtsVersion(self) -> str:
        return self.read_settings("pts_version")["pts_version"]

    def writePtsDate(self, day: int, month: int, year: int) -> int:
        return self.write_settings(pts_date=[day, month, year])[0]

    def readPtsDate(self) -> tuple[int, int, int]:
        return self.read_settings("pts_date")["pts_date"]

    def readPtsWmmVersion(self) -> str:
        return self.read_settings("pts_wmm_version")["pts_wmm_version"]

    def writePtsWmmSet(self, value: str) -> int:
        return self.write_settings(pts_wmm_set=value)[0]

    def writePtsForceOutGps(self, value: int) -> int:
        return self.write_settings(pts_force_out_gps=value)[0]

    def readPtsForceOutGps(self) -> int:
        return self.read_settings("pts_force_out_gps")["pts_force_out_gps"]

    def writePtsInitialHeadingTolerance(self, value: float) -> int:
        return self.write_settings(pts_initial_heading_tolerance=value)[0]

    def readPtsInitialHeadingTolerance(self) -> float:
        return self.read_settings("pts_initial_heading_tolerance")["pts_initial_heading_tolerance"]

    def writePtsHeadingConsistencyReq(self, value: int) -> int:
        return self.write_settings(pts_heading_consistency_req=value)[0]

    def readPtsHeadingConsistencyReq(self) -> int:
        return self.read_settings("pts_heading_consistency_req")["pts_heading_consistency_req"]

    def writePtsHeadingRootErrMul(self, value: float) -> int:
        return self.write_settings(pts_heading_root_err_mul=value)[0]

    def readPtsHeadingRootErrMul(self) -> float:
        return self.read_settings("pts_heading_root_err_mul")["pts_heading_root_err_mul"]

    def writePtsHeadingConsistentBias(self, value: float) -> int:
        return self.write_settings(pts_heading_consistent_bias=value)[0]

    def readPtsHeadingConsistentBias(self) -> float:
        return self.read_settings("pts_heading_consistent_bias")["pts_heading_consistent_bias"]

    def writePtsStrictBiasEnabled(self, value: int) -> int:
        return self.write_settings(pts_strict_bias_enabled=value)[0]

    def readPtsStrictBiasEnabled(self) -> int:
        return self.read_settings("pts_strict_bias_enabled")["pts_strict_bias_enabled"]

    def writePinMode0(self, value: int) -> int:
        return self.write_settings(pin_mode0=value)[0]

    def readPinMode0(self) -> int:
        return self.read_settings("pin_mode0")["pin_mode0"]

    def writePinMode1(self, value: int) -> int:
        return self.write_settings(pin_mode1=value)[0]

    def readPinMode1(self) -> int:
        return self.read_settings("pin_mode1")["pin_mode1"]

    def writeUartBaudrate(self, value: int) -> int:
        return self.write_settings(uart_baudrate=value)[0]

    def readUartBaudrate(self) -> int:
        return self.read_settings("uart_baudrate")["uart_baudrate"]

    def writeI2cAddr(self, value: int) -> int:
        return self.write_settings(i2c_addr=value)[0]

    def readI2cAddr(self) -> int:
        return self.read_settings("i2c_addr")["i2c_addr"]

    def writePowerHoldTime(self, value: float) -> int:
        return self.write_settings(power_hold_time=value)[0]

    def readPowerHoldTime(self) -> float:
        return self.read_settings("power_hold_time")["power_hold_time"]

    def writePowerHoldState(self, value: int) -> int:
        return self.write_settings(power_hold_state=value)[0]

    def readPowerHoldState(self) -> int:
        return self.read_settings("power_hold_state")["power_hold_state"]

    def writePowerInitialHoldState(self, value: int) -> int:
        return self.write_settings(power_initial_hold_state=value)[0]

    def readPowerInitialHoldState(self) -> int:
        return self.read_settings("power_initial_hold_state")["power_initial_hold_state"]

    def fsCfgLoad(self) -> int:
        return self.write_settings(fs_cfg_load=None)[0]

    def writeFsMscEnabled(self, value: int) -> int:
        return self.write_settings(fs_msc_enabled=value)[0]

    def readFsMscEnabled(self) -> int:
        return self.read_settings("fs_msc_enabled")["fs_msc_enabled"]

    def writeFsMscAuto(self, value: int) -> int:
        return self.write_settings(fs_msc_auto=value)[0]

    def readFsMscAuto(self) -> int:
        return self.read_settings("fs_msc_auto")["fs_msc_auto"]

    def writeLogSlots(self, value: str) -> int:
        return self.write_settings(log_slots=value)[0]

    def readLogSlots(self) -> str:
        return self.read_settings("log_slots")["log_slots"]

    def writeLogInterval(self, value: int) -> int:
        return self.write_settings(log_interval=value)[0]

    def readLogInterval(self) -> int:
        return self.read_settings("log_interval")["log_interval"]

    def writeLogHz(self, value: float) -> int:
        return self.write_settings(log_hz=value)[0]

    def readLogHz(self) -> float:
        return self.read_settings("log_hz")["log_hz"]

    def writeLogStartEvent(self, value: str) -> int:
        return self.write_settings(log_start_event=value)[0]

    def readLogStartEvent(self) -> str:
        return self.read_settings("log_start_event")["log_start_event"]

    def writeLogStartMotionThreshold(self, value: float) -> int:
        return self.write_settings(log_start_motion_threshold=value)[0]

    def readLogStartMotionThreshold(self) -> float:
        return self.read_settings("log_start_motion_threshold")["log_start_motion_threshold"]

    def writeLogStopEvent(self, value: str) -> int:
        return self.write_settings(log_stop_event=value)[0]

    def readLogStopEvent(self) -> str:
        return self.read_settings("log_stop_event")["log_stop_event"]

    def writeLogStopMotionThreshold(self, value: float) -> int:
        return self.write_settings(log_stop_motion_threshold=value)[0]

    def readLogStopMotionThreshold(self) -> float:
        return self.read_settings("log_stop_motion_threshold")["log_stop_motion_threshold"]

    def writeLogStopMotionDelay(self, value: float) -> int:
        return self.write_settings(log_stop_motion_delay=value)[0]

    def readLogStopMotionDelay(self) -> float:
        return self.read_settings("log_stop_motion_delay")["log_stop_motion_delay"]

    def writeLogStopCount(self, value: int) -> int:
        return self.write_settings(log_stop_count=value)[0]

    def readLogStopCount(self) -> int:
        return self.read_settings("log_stop_count")["log_stop_count"]

    def writeLogStopDuration(self, value: float) -> int:
        return self.write_settings(log_stop_duration=value)[0]

    def readLogStopDuration(self) -> float:
        return self.read_settings("log_stop_duration")["log_stop_duration"]

    def writeLogStopPeriodCount(self, value: int) -> int:
        return self.write_settings(log_stop_period_count=value)[0]

    def readLogStopPeriodCount(self) -> int:
        return self.read_settings("log_stop_period_count")["log_stop_period_count"]

    def writeLogStyle(self, value: int) -> int:
        return self.write_settings(log_style=value)[0]

    def readLogStyle(self) -> int:
        return self.read_settings("log_style")["log_style"]

    def writeLogPeriodicCaptureTime(self, value: float) -> int:
        return self.write_settings(log_periodic_capture_time=value)[0]

    def readLogPeriodicCaptureTime(self) -> float:
        return self.read_settings("log_periodic_capture_time")["log_periodic_capture_time"]

    def writeLogPeriodicRestTime(self, value: float) -> int:
        return self.write_settings(log_periodic_rest_time=value)[0]

    def readLogPeriodicRestTime(self) -> float:
        return self.read_settings("log_periodic_rest_time")["log_periodic_rest_time"]

    def writeLogBaseFilename(self, value: str) -> int:
        return self.write_settings(log_base_filename=value)[0]

    def readLogBaseFilename(self) -> str:
        return self.read_settings("log_base_filename")["log_base_filename"]

    def writeLogFileMode(self, value: int) -> int:
        return self.write_settings(log_file_mode=value)[0]

    def readLogFileMode(self) -> int:
        return self.read_settings("log_file_mode")["log_file_mode"]

    def writeLogDataMode(self, value: int) -> int:
        return self.write_settings(log_data_mode=value)[0]

    def readLogDataMode(self) -> int:
        return self.read_settings("log_data_mode")["log_data_mode"]

    def writeLogOutputSettings(self, value: int) -> int:
        return self.write_settings(log_output_settings=value)[0]

    def readLogOutputSettings(self) -> int:
        return self.read_settings("log_output_settings")["log_output_settings"]

    def writeLogHeaderEnabled(self, value: int) -> int:
        return self.write_settings(log_header_enabled=value)[0]

    def readLogHeaderEnabled(self) -> int:
        return self.read_settings("log_header_enabled")["log_header_enabled"]

    def writeLogFolderMode(self, value: int) -> int:
        return self.write_settings(log_folder_mode=value)[0]

    def readLogFolderMode(self) -> int:
        return self.read_settings("log_folder_mode")["log_folder_mode"]

    def writeLogImmediateOutput(self, value: int) -> int:
        return self.write_settings(log_immediate_output=value)[0]

    def readLogImmediateOutput(self) -> int:
        return self.read_settings("log_immediate_output")["log_immediate_output"]

    def writeLogImmediateOutputHeaderEnabled(self, value: int) -> int:
        return self.write_settings(log_immediate_output_header_enabled=value)[0]

    def readLogImmediateOutputHeaderEnabled(self) -> int:
        return self.read_settings("log_immediate_output_header_enabled")["log_immediate_output_header_enabled"]

    def writeLogImmediateOutputHeaderMode(self, value: int) -> int:
        return self.write_settings(log_immediate_output_header_mode=value)[0]

    def readLogImmediateOutputHeaderMode(self) -> int:
        return self.read_settings("log_immediate_output_header_mode")["log_immediate_output_header_mode"]

    def writeRtcYear(self, value: int) -> int:
        return self.write_settings(rtc_year=value)[0]

    def readRtcYear(self) -> int:
        return self.read_settings("rtc_year")["rtc_year"]

    def writeRtcMonth(self, value: int) -> int:
        return self.write_settings(rtc_month=value)[0]

    def readRtcMonth(self) -> int:
        return self.read_settings("rtc_month")["rtc_month"]

    def writeRtcDay(self, value: int) -> int:
        return self.write_settings(rtc_day=value)[0]

    def readRtcDay(self) -> int:
        return self.read_settings("rtc_day")["rtc_day"]

    def writeRtcHour(self, value: int) -> int:
        return self.write_settings(rtc_hour=value)[0]

    def readRtcHour(self) -> int:
        return self.read_settings("rtc_hour")["rtc_hour"]

    def writeRtcMinute(self, value: int) -> int:
        return self.write_settings(rtc_minute=value)[0]

    def readRtcMinute(self) -> int:
        return self.read_settings("rtc_minute")["rtc_minute"]

    def writeRtcSecond(self, value: int) -> int:
        return self.write_settings(rtc_second=value)[0]

    def readRtcSecond(self) -> int:
        return self.read_settings("rtc_second")["rtc_second"]

    def writeRtcDatetime(self, year: int, month: int, day: int, hour: int, minute: int, second: int) -> int:
        return self.write_settings(rtc_datetime=[year, month, day, hour, minute, second])[0]

    def readRtcDatetime(self) -> tuple[int, int, int, int, int, int]:
        return self.read_settings("rtc_datetime")["rtc_datetime"]

    def writeBatChgRate(self, value: int) -> int:
        return self.write_settings(bat_chg_rate=value)[0]

    def readBatChgRate(self) -> int:
        return self.read_settings("bat_chg_rate")["bat_chg_rate"]

    def writeBatColdThreshold(self, temperature_c: float, chg_rate: float) -> int:
        return self.write_settings(bat_cold_threshold=[temperature_c, chg_rate])[0]

    def readBatColdThreshold(self) -> tuple[float, float]:
        return self.read_settings("bat_cold_threshold")["bat_cold_threshold"]

    def writeBatWarmThreshold(self, temperature_c: float, chg_rate: float) -> int:
        return self.write_settings(bat_warm_threshold=[temperature_c, chg_rate])[0]

    def readBatWarmThreshold(self) -> tuple[float, float]:
        return self.read_settings("bat_warm_threshold")["bat_warm_threshold"]

    def writeBatHotThreshold(self, temperature_c: float, chg_rate: float) -> int:
        return self.write_settings(bat_hot_threshold=[temperature_c, chg_rate])[0]

    def readBatHotThreshold(self) -> tuple[float, float]:
        return self.read_settings("bat_hot_threshold")["bat_hot_threshold"]

    def writeBatOffsetThreshold(self, value: float) -> int:
        return self.write_settings(bat_offset_threshold=value)[0]

    def readBatOffsetThreshold(self) -> float:
        return self.read_settings("bat_offset_threshold")["bat_offset_threshold"]

    def readBatMah(self) -> int:
        return self.read_settings("bat_mah")["bat_mah"]

    def writeBleName(self, value: str) -> int:
        return self.write_settings(ble_name=value)[0]

    def readBleName(self) -> str:
        return self.read_settings("ble_name")["ble_name"]

    def readBleConnected(self) -> int:
        return self.read_settings("ble_connected")["ble_connected"]

    def bleDisconnect(self) -> int:
        return self.write_settings(ble_disconnect=None)[0]

    def writeGpsStandby(self, value: int) -> int:
        return self.write_settings(gps_standby=value)[0]

    def readGpsStandby(self) -> int:
        return self.read_settings("gps_standby")["gps_standby"]

    def writeGpsLed(self, value: int) -> int:
        return self.write_settings(gps_led=value)[0]

    def readGpsLed(self) -> int:
        return self.read_settings("gps_led")["gps_led"]

