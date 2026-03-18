from yostlabs.communication.base import ThreespaceInputStream, ThreespaceOutputStream

import struct
from enum import Enum
from dataclasses import dataclass, field

#The internal specifiers used are different than the struct module specifiers.
#This dictionary maps the internal specifiers to the corresponding struct module specifiers and their sizes.
yost_format_conversion_dict = {
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

    #Both string types are null-terminated in binary mode and parsed identically here.
    #They are kept distinct to match the firmware's own specifiers:
    #  's' - string requiring quotes or backslash escapes for special chars in ASCII mode
    #  'S' - string that automatically escapes separators (e.g. commas) in ASCII mode
    #The distinction only matters for ASCII mode input; binary mode always uses null-termination.
    "s" : {"c": 's', "size": float('nan')},
    "S" : {"c": 's', "size": float('nan')}
}

def yost_format_get_size(format_str: str):
    return sum(yost_format_conversion_dict[c]["size"] for c in format_str)

def yost_format_to_struct_format(format_str: str):
    return ''.join(yost_format_conversion_dict[c]['c'] for c in format_str)

@dataclass
class ThreespaceCommandInfo:
    name: str
    num: int

    #These formats are in the internal specifier format
    #They should not include any characters other than the
    #specifiers defined in yost_format_conversion_dict as keys.
    #This means no endianess or size specifiers.
    in_format: str
    out_format: str

    num_out_params: int = field(init=False)
    out_size: int|float = field(init=False)

    def __post_init__(self):
        self.compute_param_properties()

    def compute_param_properties(self):
        self.num_out_params = len(self.out_format)
        self.out_size = yost_format_get_size(self.out_format)

class ThreespaceCommand:

    BINARY_START_BYTE = 0xf7
    BINARY_START_BYTE_HEADER = 0xf9

    BINARY_READ_SETTINGS_START_BYTE = 0xFA
    BINARY_READ_SETTINGS_START_BYTE_HEADER = 0xFC

    def __init__(self, name: str, num: int, in_format: str, out_format: str):
        self.info = ThreespaceCommandInfo(name, num, in_format, out_format)
        #These formats are in the struct module format
        self.in_format = yost_format_to_struct_format(self.info.in_format)
        self.out_format = yost_format_to_struct_format(self.info.out_format)
        self.precompute_output_segments()

    def precompute_output_segments(self):
        """     
        Precompute output format segments used by parse_response and read_command.
        Each entry is (struct_fmt, byte_size) for a bulk run of non-string fields,
        or None to indicate a single null-terminated string field.
        This allows avoiding repeated parsing of the format string and a simple
        loop for parsing regardless of output format complexity.
        """

        self.out_segments = []
        if self.out_format:
            i = 0
            fmt = self.out_format
            while i < len(fmt):
                if fmt[i] != 's':
                    end = fmt.find('s', i)
                    if end == -1: end = len(fmt)
                    seg = f"<{fmt[i:end]}"
                    self.out_segments.append((seg, struct.calcsize(seg)))
                    i = end
                else:
                    self.out_segments.append(None)
                    i += 1

    def format_cmd(self, *args, header_enabled=False):
        #Gather the different data portions
        data_parts = [struct.pack("<B", self.info.num)]
        for i, c in enumerate(self.in_format):
            if c != 's':
                data_parts.append(struct.pack(f"<{c}", args[i]))
            else:
                data_parts.append(struct.pack(f"<{len(args[i])}sb", bytes(args[i], 'ascii'), 0))
        
        #Create the full command with the start, data, and checksum
        cmd_data = b''.join(data_parts)
        checksum = sum(cmd_data) % 256
        start_byte = ThreespaceCommand.BINARY_START_BYTE_HEADER if header_enabled else ThreespaceCommand.BINARY_START_BYTE
        return struct.pack(f"<B{len(cmd_data)}sB", start_byte, cmd_data, checksum)

    def send_command(self, com: ThreespaceOutputStream, *args, header_enabled = False):
        cmd = self.format_cmd(*args, header_enabled=header_enabled)
        com.write(cmd) 

    #Read the command result from an already read buffer. This will modify the given buffer to remove
    #that data as well
    def parse_response(self, response: bytes):
        """
        Reads command result from an already prepared buffer.
        The given buffer will be modified to remove the data read as well.
        """
        if self.info.num_out_params == 0: return None
        output = []

        for seg in self.out_segments:
            if seg is not None: #Regular format string
                fmt, size = seg
                output.extend(struct.unpack(fmt, response[:size]))
                #TODO: Switch to using numpy views instead of slicing
                response = response[size:]
            else: #Null-terminated string
                str_len = response.index(0)
                output.append(response[:str_len].decode())
                response = response[str_len + 1:]

        if self.info.num_out_params == 1:
            return output[0]
        return output

    #Read the command dynamically from an input stream
    def read_command(self, com: ThreespaceInputStream, verbose=False):
        raw = bytearray()
        if self.info.num_out_params == 0: return None, raw
        output = []

        for seg in self.out_segments:
            if seg is not None: #Regular format string
                fmt, size = seg
                response = com.read(size)
                raw += response
                if len(response) != size:
                    if verbose:
                        print(f"Failed to read {self.info.name} {len(response)} / {size}. Aborting...")
                    return None, raw
                output.extend(struct.unpack(fmt, response))
            else: #Null-terminated string
                response = com.read_until(b'\0')
                raw += response
                if response[-1] != 0:
                    if verbose:
                        print(f"Failed to read string from {self.info.name}. Aborting...")
                    return None, raw
                output.append(response[:-1].decode())

        if self.info.num_out_params == 1:
            return output[0], raw
        return output, raw

#Command numbers with special handling. For quick lookup
#of the command info.
THREESPACE_GET_STREAMING_BATCH_COMMAND_NUM = 84
THREESPACE_START_STREAMING_COMMAND_NUM = 85
THREESPACE_STOP_STREAMING_COMMAND_NUM = 86
THREESPACE_FILE_READ_BYTES_COMMAND_NUM = 177
THREESPACE_SOFTWARE_RESET_COMMAND_NUM = 226
THREESPACE_ENTER_BOOTLOADER_COMMAND_NUM = 229

#Command definitions
THREESPACE_COMMANDS: list[ThreespaceCommand] = [
    ThreespaceCommand("getTaredOrientation", 0, "", "ffff"),
    ThreespaceCommand("getTaredOrientationAsEulerAngles", 1, "", "fff"),
    ThreespaceCommand("getTaredOrientationAsRotationMatrix", 2, "", "fffffffff"),
    ThreespaceCommand("getTaredOrientationAsAxisAngles", 3, "", "ffff"),
    ThreespaceCommand("getTaredOrientationAsTwoVector", 4, "", "ffffff"),

    ThreespaceCommand("getDifferenceQuaternion", 5, "", "ffff"),

    ThreespaceCommand("getUntaredOrientation", 6, "", "ffff"),
    ThreespaceCommand("getUntaredOrientationAsEulerAngles", 7, "", "fff"),
    ThreespaceCommand("getUntaredOrientationAsRotationMatrix", 8, "", "fffffffff"),
    ThreespaceCommand("getUntaredOrientationAsAxisAngles", 9, "", "ffff"),
    ThreespaceCommand("getUntaredOrientationAsTwoVector", 10, "", "ffffff"),
    
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
    ThreespaceCommand("startDataLogging", 60, "", ""),
    ThreespaceCommand("stopDataLogging", 61, "", ""),

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
    ThreespaceCommand("getStreamingBatch", THREESPACE_GET_STREAMING_BATCH_COMMAND_NUM, "", "S"),
    ThreespaceCommand("startStreaming", THREESPACE_START_STREAMING_COMMAND_NUM, "", ""),
    ThreespaceCommand("stopStreaming", THREESPACE_STOP_STREAMING_COMMAND_NUM, "", ""),
    ThreespaceCommand("pauseLogStreaming", 87, "b", ""),
    
    ThreespaceCommand("getDateTimeString", 93, "", "S"),
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
    ThreespaceCommand("fileReadBytes", THREESPACE_FILE_READ_BYTES_COMMAND_NUM, "B", "S"), #This has to be handled specially as the output is variable length BYTES not STRING
    ThreespaceCommand("deleteFile", 178, "S", ""),
    ThreespaceCommand("setCursor", 179, "U", ""),
    ThreespaceCommand("fileStartStream", 180, "", "U"),
    ThreespaceCommand("fileStopStream", 181, "", ""),

    ThreespaceCommand("getBatteryCurrent", 200, "", "I"),
    ThreespaceCommand("getBatteryVoltage", 201, "", "f"),
    ThreespaceCommand("getBatteryPercent", 202, "", "b"),
    ThreespaceCommand("getBatteryStatus", 203, "", "b"),

    ThreespaceCommand("getGpsActiveState", 214, "", "b"),
    ThreespaceCommand("getGpsCoord", 215, "", "dd"),
    ThreespaceCommand("getGpsAltitude", 216, "", "f"),
    ThreespaceCommand("getGpsFixState", 217, "", "b"),
    ThreespaceCommand("getGpsHdop", 218, "", "f"),
    ThreespaceCommand("getGpsSatellites", 219, "", "b"),

    ThreespaceCommand("commitSettings", 225, "", ""),
    ThreespaceCommand("softwareReset", THREESPACE_SOFTWARE_RESET_COMMAND_NUM, "", ""),
    ThreespaceCommand("enterBootloader", THREESPACE_ENTER_BOOTLOADER_COMMAND_NUM, "", ""),

    ThreespaceCommand("getLedColor", 238, "", "fff"),

    ThreespaceCommand("getButtonState", 250, "", "b"),
]

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

    GetDateTime = 63

    GetRawGyroRate = 65
    GetRawAccelVec = 66
    GetRawMagVec = 67

    GetEeptsOldestStep = 70
    GetEeptsNewestStep = 71
    GetEeptsNumStepsAvailable = 72

    GetDateTimeString = 93
    GetTimestamp = 94

    GetBatteryCurrent = 200
    GetBatteryVoltage = 201
    GetBatteryPercent = 202
    GetBatteryStatus = 203

    GetGpsActiveState = 214
    GetGpsCoord = 215
    GetGpsAltitude = 216
    GetGpsFixState = 217
    GetGpsHdop = 218
    GetGpsSatellites = 219

    GetLedColor = 238

    GetButtonState = 250


#-----------------------------------CUSTOM COMMAND TYPES-----------------------------------

class ThreespaceGetStreamingBatchCommand(ThreespaceCommand):
    """
    Batches multiple commands together into one command response that will be returned
    as a list of the individual command responses. This is primarily used for streaming.

    The command can have its stream slots changed via the set_stream_slots function.
    """

    def __init__(self, streaming_slots: list[ThreespaceCommand]):
        self.commands = streaming_slots
        combined_out_format = ''.join(slot.info.out_format for slot in streaming_slots if slot is not None)
        super().__init__("getStreamingBatch", THREESPACE_GET_STREAMING_BATCH_COMMAND_NUM, "", combined_out_format)

    def set_stream_slots(self, streaming_slots: list[ThreespaceCommand]):
        self.commands = streaming_slots
        self.info.out_format = ''.join(slot.info.out_format for slot in streaming_slots if slot is not None)
        self.info.compute_param_properties()

        #Update command formats for quick parsing
        self.out_format = yost_format_to_struct_format(self.info.out_format)
        self.precompute_output_segments()

    def parse_response(self, response: bytes):
        data = []
        for command in self.commands:
            if command is None: continue
            cmd_response_size = command.info.out_size
            data.append(command.parse_response(response))
            response = response[cmd_response_size:]
        
        return data
    
    def read_command(self, com: ThreespaceInputStream, verbose=False):
        #Get the response to all the streaming commands
        response = []
        raw_response = bytearray()
        for command in self.commands:
            if command is None: continue
            out, raw = command.read_command(com, verbose=verbose)
            raw_response += raw
            response.append(out)
        
        return response, raw_response

#------------------------------COMMAND LOOKUP FUNCTIONS------------------------------

def threespace_command_get(cmd_num: int):
    for command in THREESPACE_COMMANDS:
        if command.info.num == cmd_num:
            return command
    return None

def threespace_command_get_by_name(name: str):
    for command in THREESPACE_COMMANDS:
        if command.info.name == name:
            return command
    return None

def threespace_command_get_info(cmd_num: int):
    command = threespace_command_get(cmd_num)
    if command is None: return None
    return command.info