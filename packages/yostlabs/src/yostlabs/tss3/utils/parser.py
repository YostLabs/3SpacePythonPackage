from yostlabs.tss3.api import *
import math

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

        self.__parsing_header: ThreespaceHeader = None  #Used to optimize preventing reading to much by cacheing the header seperately from the cmd data
        self.__parsing_command: ThreespaceCommand = None
        self.__parsing_msg_length: int = None           #Used seperately from the __parsing_header so can handle msg lengths that are static without modifying the header

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
            cmd = threespaceCommandGet(cmd)
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
        if self.data_stream.length < self.header_info.size + self.__parsing_msg_length:
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
        