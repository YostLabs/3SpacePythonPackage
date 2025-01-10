from typing import Generator

class ThreespaceInputStream:

    """
    Reads specified number of bytes. 
    If that many bytes are not available after timeout, less data will be returned
    """
    def read(self, num_bytes: int) -> bytes:
        raise NotImplementedError()
    
    def read_all(self):
        return self.read(self.length)

    def read_until(self, expected: bytes) -> bytes:
        raise NotImplementedError()

    """Allows reading without removing the data from the buffer"""
    def peek(self, num_bytes: int) -> bytes:
        raise NotImplementedError()
    
    def peek_until(self, expected: bytes, max_length: int = None) -> bytes:
        raise NotImplementedError()
    
    def readline(self) -> bytes:
        return self.read_until(b"\n")
    
    def peekline(self, max_length: int = None) -> bytes:
        return self.peek_until(b"\n", max_length=max_length)    
    
    @property
    def length(self) -> int:
        raise NotImplementedError()
    
    @property
    def timeout(self) -> float:
        raise NotImplementedError()

    @timeout.setter
    def timeout(self, timeout: float):
        raise NotImplementedError()    

class ThreespaceOutputStream:

    """Write the given bytes"""
    def write(self, bytes):
        raise NotImplementedError()

class ThreespaceComClass(ThreespaceInputStream, ThreespaceOutputStream):
    """
    Base class for a com class to use with the sensor object.
    Com classes should be initialized without connection and require
    there open called before use
    """
    
    def close(self):
        raise NotImplementedError()
    
    def open(self) -> bool:
        """
        Should return True on success, False on failure
        If already open, should stay open
        """
        raise NotImplementedError()
    
    def check_open(self) -> bool:
        """
        Should return True if the port is currently open, False otherwise.
        Must give the current state, not a cached state
        """
        raise NotImplementedError()
    
    @staticmethod
    def auto_detect() -> Generator["ThreespaceComClass", None, None]:
        """
        Returns a list of com classes of the same type called on nearby
        """
        raise NotImplementedError()
    
    @property
    def reenumerates(self) -> bool:
        """
        If the device Re-Enumerates when going from bootloader to firmware or vice versa, this must return True.
        This indicates to the API that it must search for the new com class representing the object when switching between bootloader and firmware
        """
        raise NotImplementedError
    
    @property
    def name(self) -> str:
        raise NotImplementedError()