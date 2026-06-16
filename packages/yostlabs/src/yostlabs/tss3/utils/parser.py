from yostlabs.tss3.api import *
from yostlabs.tss3.settings import threespace_setting_get
from yostlabs.tss3.utils.streaming import get_stream_options_from_str
import math
from pathlib import Path

class ThreespaceBufferInputStream(ThreespaceInputStream):
    """
    Default Input Stream for the binary parser.
    Ignores timeout since this is only used synchronously for now.
    """

    def __init__(self):
        self.buffer = bytearray()

    """Reads specified number of bytes."""
    def read(self, num_bytes) -> bytes:
        num_bytes = min(len(self.buffer), num_bytes)
        result = self.buffer[:num_bytes]
        del self.buffer[:num_bytes]
        return result
    
    def read_all(self):
        return self.read(self.length)

    def read_until(self, expected: bytes) -> bytes:
        if expected not in self.buffer:
            return self.read_all()
        
        length = self.buffer.index(expected) + len(expected)
        result = self.buffer[:length]
        del self.buffer[:length]
        return result

    """Allows reading without removing the data from the buffer"""
    def peek(self, num_bytes) -> bytes:
        num_bytes = min(len(self.buffer), num_bytes)
        return self.buffer[:num_bytes]
    
    def peek_until(self, expected: bytes, max_length=None) -> bytes:
        if expected in self.buffer: #Read until the expected
            length = self.buffer.index(expected) + len(expected)
            if max_length is not None and length > max_length:
                length = max_length
            return self.buffer[:length]
        
        #There is no expected, so read as far as possible
        length = len(self.buffer)
        if max_length is not None:
            length = min(length, max_length)
        return self.buffer[:length]
    
    def readline(self) -> bytes:
        return self.read_until(b"\n")
    
    def peekline(self, max_length=None) -> bytes:
        return self.peek_until(b"\n", max_length=max_length)    
    
    def insert(self, data: bytes):
        self.buffer.extend(data)

    @property
    def length(self) -> int:
        return len(self.buffer)
    
    @property
    def timeout(self) -> float:
        raise NotImplementedError()

    @timeout.setter
    def timeout(self, timeout: float):
        raise NotImplementedError()    

class ThreespaceBinaryParser:
    """
    A class that can be used to parse a stream of binary data
    that could contain multiple different command responses and validates
    the responses to handle misalignment/data corruption.

    Requires all expected responses to have the same header enabled.

    The header should contain the cmd_echo, checksum, and data_length fields
    for full functionality. The lack of any of those fields could limit functionality
    of the parser.

    If cmd_echo is missing, only one command can be registered with the binary parser as it has no way
    of knowing what of verifying what the current incoming response is.

    If checksum is missing, data integrity can not be checked.

    If data_length is missing, commands that do not return a static length may cause blocking operations
    or errors while parsing. (These are planned to be fixed in a future version)

    NOTE: For speed, a custom implementation will be better then this parser class. This parser handles allowing
    multiple commands as well as data validation and misalignment correction. It also formats the data into the ThreespaceCmdResult
    response type. The overhead added because of all these additional checks/calculations can add a significant amount of time
    to processing binary data compared to just reading a known amount of data and instantly unpacking it to a tuple in the desired format.
    """

    COMMAND_EXCEPTIONS = [84, 177] #getStreamingBatch and fileReadBytes need additional info and so need registered via the 

    def __init__(self, data_stream: ThreespaceInputStream = None, verbose=False):
        """
        Parameters
        ----------
        data_stream - (optional) The data stream to use with the Binary Parser. If not supplied, will default to a new ThreespaceBufferInputStream
        """
        self.data_stream = data_stream
        self.registered_commands: dict[int,ThreespaceCommand] = {}

        if self.data_stream is None:
            self.data_stream = ThreespaceBufferInputStream()
        self.header_info = None

        self.__parsing_header: ThreespaceHeader = None  #Used to optimize preventing reading to much by caching the header separately from the cmd data
        self.__parsing_command: ThreespaceCommand = None
        self.__parsing_msg_length: int = None           #Used separately from the __parsing_header so can handle msg lengths that are static without modifying the header

        self.misaligned = False
        self.verbose = verbose

    def register_command(self, cmd: int|ThreespaceCommand, **kwargs):
        """
        Registers the given cmd number/cmd with the binary parser.

        Some commands may require additional information:
        stream_slots - list[int] Required when registering command 84 (getStreamingBatch) a list of command numbers that are being streamed.
        read_size - 'auto' or int Required when registering a command that requires a given length such as fileReadBytes. If 'auto' will use the header length to determine length.
        """
        if isinstance(cmd, int):
            cmd = threespace_command_get(cmd)
            if cmd is None:
                raise ValueError(f"Invalid Cmd {cmd}")
            
        if cmd.info.num in self.registered_commands:
            return False
        
        #These command types are special and need additional info
        if cmd.info.num == THREESPACE_FILE_READ_BYTES_COMMAND_NUM:
            if "read_size" not in kwargs:
                raise ValueError("Missing arguement 'read_size' when registering the fileReadBytes command with the binary parser")
            raise NotImplementedError("The fileReadBytes command has yet to be implemented for the ThreespaceBinaryParser")
        elif cmd.info.num == THREESPACE_GET_STREAMING_BATCH_COMMAND_NUM and not isinstance(cmd, ThreespaceGetStreamingBatchCommand):
            if "stream_slots" not in kwargs:
                raise ValueError("Missing arguement 'stream_slots' when registering the getStreamingBatch command with the binary parser")
            cmd = ThreespaceGetStreamingBatchCommand(kwargs['stream_slots'])

        self.registered_commands[cmd.info.num] = cmd
        return True

    def unregister_command(self, cmd: int|ThreespaceCommand):
        if cmd.info.num not in self.registered_commands:
            return False
        del self.registered_commands[cmd.info.num]
        return True

    def set_header(self, header_info: ThreespaceHeaderInfo):
        self.header_info = header_info

    def insert_data(self, data: bytes):
        """
        Add the given data to the default ThreespaceBufferInputStream.
        This method will raise an exception if used on a different type of InputStream
        """
        if not isinstance(self.data_stream, ThreespaceBufferInputStream):
            raise Exception("Insert data with Binary Parser only valid when using the default data_stream")
        self.data_stream.insert(data)
    
    def parse_message(self) -> ThreespaceCmdResult:
        if self.__parsing_header is None:
            self.__parse_header()
        
        if self.__parsing_command is None:
            return None
        
        return self.__parse_command()

    def __parse_header(self):
        if self.data_stream.length < self.header_info.size:
            return
        
        header = self.data_stream.peek(self.header_info.size)
        header = ThreespaceHeader.from_bytes(header, self.header_info)

        cmd_found = False
        if self.header_info.echo_enabled: #Search for the command to parse
            for command in self.registered_commands.values():
                if header.echo != command.info.num: continue

                #Command matches! Attempt to parse
                self.__parsing_command = command
                self.__parsing_header = header

                cmd_found = True
        else: #Can only parse one command
            if len(self.registered_commands) > 1:
                raise Exception("Only one command type can be parsed when the 'cmd echo' is not enabled in the header")
            self.__parsing_command = list(self.registered_commands.values())[0]
            self.__parsing_header = header
            cmd_found = True

        if cmd_found:
            if self.header_info.length_enabled:
                self.__parsing_msg_length = self.__parsing_header.length
            else:
                self.__parsing_msg_length = self.__parsing_command.info.out_size
            return

        #This header is not related to any command, so it needs skipped
        if self.verbose and not self.misaligned:
            print("Unexpected header:", header)
        self.misaligned = True
        self.data_stream.read(1)

    def __peek_checksum(self):
        header_len = len(self.__parsing_header.raw_binary)
        data = self.data_stream.peek(header_len + self.__parsing_msg_length)[header_len:]
        checksum = sum(data) % 256
        return checksum == self.__parsing_header.checksum

    def __parse_command(self):
        #Not enough data to parse yet
        minimum_message_size = self.header_info.size + self.__parsing_msg_length
        if math.isnan(minimum_message_size):
            minimum_message_size = self.header_info.size + struct.calcsize(f"<{self.__parsing_command.out_format.struct_format}")
        
        if self.data_stream.length < minimum_message_size:
            return None
        
        if self.header_info.checksum_enabled and not math.isnan(self.__parsing_msg_length): #Can validate checksum before parsing
            if not self.__peek_checksum():
                #Data corruption/Misalignment error
                if self.verbose and not self.misaligned:
                    print("Checksum mismatch for command", self.__parsing_command.info.num)
                self.misaligned = True
                self.data_stream.read(1)
                self.__parsing_command = None
                self.__parsing_header = None
                return None
        
        #Header and pre validation checksum checks out! Now just parse the actual command result and return it
        header = self.__parsing_header
        self.data_stream.read(len(header.raw_binary)) #Skip these bytes since they are already parsed
        result, raw = self.__parsing_command.read_command(self.data_stream)

        #Validate checksum if couldn't pre-validate due to unknown message length
        if math.isnan(self.__parsing_msg_length) and self.header_info.checksum_enabled:
            checksum = sum(raw) % 256
            if checksum != header.checksum:
                if self.verbose and not self.misaligned:
                    print("Checksum mismatch for command", self.__parsing_command.info.num)
                self.misaligned = True
                self.__parsing_command = None
                self.__parsing_header = None
                return None

        #Reset and return
        self.__parsing_header = None
        self.__parsing_command = None
        self.misaligned = False
        return ThreespaceCmdResult(result, header, data_raw_binary=raw)


def search_folder(
    folder_path: str | Path,
    pattern: str = "*",
    max_depth: int | None = None,
    max_results: int | None = None,
) -> list[Path]:
    """
    Recursively search *folder_path* for files whose name matches *pattern*.

    Parameters
    ----------
    folder_path : str or Path
        Root directory to search.
    pattern : str
        Glob-style filename pattern, e.g. ``"*.bin"`` or ``"*.csv"``.
        Defaults to ``"*"`` (all files).
    max_depth : int or None
        Maximum folder depth to descend into relative to *folder_path*.
        ``0`` means only files directly inside *folder_path*; ``None``
        means unlimited depth.
    max_results : int or None
        Stop and return early once this many matches have been found.
        ``None`` means collect all matches.

    Returns
    -------
    list[Path]
        Sorted list of matching file paths.
    """
    root = Path(folder_path)
    matches: list[Path] = []

    def _recurse(current: Path, depth: int):
        if max_results is not None and len(matches) >= max_results:
            return
        try:
            entries = list(current.iterdir())
        except PermissionError:
            return
        for entry in entries:
            if max_results is not None and len(matches) >= max_results:
                return
            if entry.is_file() and entry.match(pattern):
                matches.append(entry)
            elif entry.is_dir() and (max_depth is None or depth < max_depth):
                _recurse(entry, depth + 1)

    _recurse(root, 0)
    return sorted(matches)

class ThreespaceConfigDictionary:

    def __init__(self, cfg_path: str | Path):
        self.cfg_path = Path(cfg_path)
        if not self.cfg_path.suffix == ".cfg":
            raise ValueError(f"{cfg_path} is not a .cfg file")
        if not self.cfg_path.is_file():
            raise ValueError(f"{cfg_path} is not a valid file path")
        
        self.settings: dict[str, Any] = {}
        self.comments: list[str] = []
        with self.cfg_path.open('r') as fp:
            for line in fp:
                line = line.strip()
                if line.startswith("#"): #Comment
                    self.comments.append(line[1:])
                elif '=' in line: #Key Value
                    data = line.split("=")
                    if len(data) != 2:
                        raise ValueError(f"Invalid line in config file: {line}")
                    key, value = data
                    key = key.strip()
                    value = value.strip()
                    setting = threespace_setting_get(key)
                    if setting is None:
                        print("Warning: Unrecognized setting in config file:", key)
                        self.settings[key] = value
                    else:
                        result = setting.out_format.parse_response_ascii(value)
                        if len(result) == 1:
                            result = result[0]
                        self.settings[key] = result
                elif line: #Improper formatted line that is not empty
                    raise ValueError(f"Invalid line in config file: {line}")
        
    def __str__(self):
        return str(self.settings)

    def __getitem__(self, key: str):
        return self.settings[key]
    
    def __contains__(self, key: str):
        return key in self.settings

class ThreespaceDataFileParser:
    """
    A class used for parsing both binary and ascii data
    files created via logging either from the sensor or
    the 3-Space Suite.

    Unlike ThreespaceBinaryParser, this class is not designed
    to parse a generic stream of data that could contain multiple
    different response types. Instead, this class is purely for the
    common task of parsing recorded data files.
    """

    def __init__(self, data_paths: list[str|Path] = None, cfg_path: str=None, folder_path: str=None):
        self.data_paths: list[Path] = None
        self.cfg_path: Path = None

        #Filled out by setup
        self.cfg = None
        self.header = None
        self.header_format = None
        self.command = None
        self.buffer = None
        self.binary_parser = None

        if folder_path is not None:
            self.set_folder(folder_path)
        if data_paths is not None:
            self.set_data_files(data_paths)
        if cfg_path is not None:
            self.set_config_file(cfg_path)
        
        if self.cfg_path is not None and self.data_paths is not None:
            self.setup()

    def parse_message(self) -> ThreespaceCmdResult:
        if self.mode == "binary":
            return self.__parse_binary_message()
        else:
            return self.__parse_ascii_message()

    def __parse_binary_message(self):
        return self.binary_parser.parse_message()
    
    def __parse_ascii_message(self):
        if self.buffer.length == 0:
            return None
        result, raw = self.header_format.read_response_ascii(self.buffer)
        header = ThreespaceHeader.from_tuple(result, self.header)

        result, raw = self.command.read_response_ascii(self.buffer)
        return ThreespaceCmdResult(result, header=header, data_raw_binary=raw)

    def setup(self, force_slots=None, force_header=None):
        """
        Parameters
        ----------
        force_slots : optional
            Can be set to "stream_slots" or "log_slots" to force the parser to parse the data file with the given slots.
            If None, the parser will automatically determine based on the config file format.
        force_header : optional
            Can be set to True or False to force the parser to parse with or without the header. Generally, this will
            be provided by the config file, and is assumed on otherwise. The only time this would be required is if
            the data file was gathered via streaming without the header enabled (In which case set to False).
        """
        self.cfg = ThreespaceConfigDictionary(self.cfg_path)

        #Determining if from a suite logging session (streaming) or a regular logging session (logging)
        from_suite = False
        for comment in self.cfg.comments:
            if comment.startswith("Suite"):
                from_suite = True
                break

        #Load the header object
        self.header = ThreespaceHeaderInfo()
        if force_header != False and (from_suite or self.cfg["log_header_enabled"] or force_header):
            try:
                self.header.status_enabled = self.cfg["header_status"]
                self.header.timestamp_enabled = self.cfg["header_timestamp"]
                self.header.echo_enabled = self.cfg["header_echo"]
                self.header.checksum_enabled = self.cfg["header_checksum"]
                self.header.serial_enabled = self.cfg["header_serial"]
                self.header.length_enabled = self.cfg["header_length"]
            except KeyError:
                raise ValueError("Config file is missing header information.")

        #Load the stream/log slots
        slot_key = None
        if force_slots is not None:
            if force_slots not in ["stream_slots", "log_slots"]:
                raise ValueError("force_slots must be either 'stream_slots' or 'log_slots'")
            slot_key = force_slots
        else:
            slot_key = "stream_slots" if from_suite else "log_slots"

        try:
            slots = self.cfg[slot_key]
        except KeyError:
             raise ValueError(f"Config file is missing {slot_key} information.")

        slots = get_stream_options_from_str(slots)
        self.command = ThreespaceGetStreamingBatchCommand([threespace_command_get(slot.cmd.value) for slot in slots])

        self.buffer = ThreespaceBufferInputStream()
        for data_path in self.data_paths:
            with data_path.open('rb') as f:
                self.buffer.insert(f.read())

        self.mode = "binary" if self.data_paths[0].suffix == ".bin" else "ascii"
        if self.mode == "ascii":
            self.__setup_ascii()
        else:
            self.__setup_binary()
    
    def __setup_binary(self):
        self.binary_parser = ThreespaceBinaryParser(self.buffer)
        self.binary_parser.set_header(self.header)
        self.binary_parser.register_command(self.command)

    def __setup_ascii(self):
        #header.format is a string, but for parsing it is useful to have it as a ThreespaceFormat object
        self.header_format = ThreespaceFormat(self.header.format.strip('<>'), from_struct=True)
        self.buffer.readline() #Skip the first line since it is just the header format

    def set_data_files(self, data_paths: list[str|Path]):
        if len(data_paths) == 0:
            raise ValueError("At least one data file must be provided")
        paths = [Path(p) for p in data_paths]
        if any(p.suffix not in [".bin", ".csv"] for p in paths):
            raise ValueError("Data files must be .bin or .csv files")
        if any(p.suffix != paths[0].suffix for p in paths):
            raise ValueError("All data files must have the same file extension")
        self.data_paths = paths

    def set_config_file(self, cfg_path: str):
        path = Path(cfg_path)
        if not path.suffix == ".cfg":
            raise ValueError(f"{cfg_path} is not a .cfg file")
        if not path.is_file():
            raise ValueError(f"{cfg_path} is not a valid file path")
        
        self.cfg_path = path

    def set_folder(self, folder_path: str):
        folder_path: Path = Path(folder_path)
        if not folder_path.is_dir():
            raise ValueError(f"{folder_path} is not a valid folder path")

        ascii_files = search_folder(folder_path, "*.csv", max_depth=None, max_results=1)
        binary_files = search_folder(folder_path, "*.bin", max_depth=None, max_results=1)
        cfg_files  = search_folder(folder_path, "*.cfg", max_depth=None, max_results=1)

        if len(ascii_files) == 0 and len(binary_files) == 0:
            raise ValueError(f"No data file (.bin or .csv) found under {folder_path}")
        elif len(ascii_files) > 0 and len(binary_files) > 0:
            raise ValueError(f"Multiple data file types found under {folder_path}. Please ensure only .bin or .csv files are present.")
        
        if len(ascii_files) > 0:
            self.set_data_files(ascii_files)
        else:
            self.set_data_files(binary_files)

        if len(cfg_files) == 0:
            raise ValueError(f"No config file (.cfg) found under {folder_path}")
        elif len(cfg_files) > 1:
            raise ValueError(f"Multiple config files found under {folder_path}. Please ensure only one .cfg file is present.")
        else:
            self.set_config_file(cfg_files[0])
        
        