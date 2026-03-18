from typing import Generator
from abc import ABC, abstractmethod

class ThreespaceInputStream(ABC):

    @abstractmethod
    def read(self, num_bytes: int) -> bytes:
        """
        Reads specified number of bytes. 
        If that many bytes are not available after timeout, less data will be returned
        """
        ...
    
    def read_all(self):
        return self.read(self.length)

    @abstractmethod
    def read_until(self, expected: bytes) -> bytes: ...

    @abstractmethod
    def peek(self, num_bytes: int) -> bytes: 
        """Allows reading without removing the data from the buffer"""
        ...
    
    @abstractmethod
    def peek_until(self, expected: bytes, max_length: int = None) -> bytes: ...
    
    def readline(self) -> bytes:
        return self.read_until(b"\r\n")
    
    def peekline(self, max_length: int = None) -> bytes:
        #Lines from the sensor are defined to have a \r\n not just a \n
        return self.peek_until(b"\r\n", max_length=max_length)    
    
    @property
    @abstractmethod
    def length(self) -> int: ...
    
    @property
    @abstractmethod
    def timeout(self) -> float: ...

    @timeout.setter
    @abstractmethod
    def timeout(self, timeout: float): ...  

class ThreespaceOutputStream(ABC):

    @abstractmethod
    def write(self, bytes):
        """Write the given bytes"""
        ...

class ThreespaceComClass(ThreespaceInputStream, ThreespaceOutputStream):
    """
    Base class for a com class to use with the sensor object.
    Com classes should be initialized without connection and require
    there open called before use
    """
    
    @abstractmethod
    def close(self): ...
    
    @abstractmethod
    def open(self) -> bool:
        """
        Should return True on success, False on failure
        If already open, should stay open
        """
        ...
    
    @abstractmethod
    def check_open(self) -> bool:
        """
        Should return True if the port is currently open, False otherwise.
        Must give the current state, not a cached state
        """
        ...
    
    #Not abstract because not making hard required.
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
        This indicates to the API that it must search for the new com class representing the object when switching between bootloader and firmware.

        This is usually False, so returning that as the default. If this behavior is needed,
        it should be overridden to return True in the com class implementation
        """
        return False
    
    @property
    def name(self) -> str:
        return type(self).__name__