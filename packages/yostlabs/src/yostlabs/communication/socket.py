from yostlabs.communication.base import *
import socket
import time
from typing import Callable

class ThreespaceSocketComClass(ThreespaceComClass):

    """
    Inheriting classes must implement auto_detect() functionality.
    Should also implement 'name' functionality
    """

    def __init__(self, sock: socket.socket, *connection_params, connection_timeout=None):
        self.socket = sock
        self._opened = False

        self.buffer = bytearray()
        self.__timeout = 2
        self.__connection_timeout = connection_timeout
        self.__connection_params = connection_params

    @property
    def timeout(self) -> float:
        return self.__timeout
    
    @timeout.setter
    def timeout(self, timeout: float):
        self.__timeout = timeout

    def open(self):
        if self._opened: return True
        try:
            if self.__connection_timeout is None:
                self.socket.setblocking(True)
            else:
                self.socket.settimeout(self.__connection_timeout)
            self.socket.connect(*self.__connection_params)
        except Exception as e:
            try:
                self.socket.close()
            except: pass
            print(e)
            return False
        self._opened = True
        return True

    def close(self):
        if not self._opened: return
        self.socket.close()
        self._opened = False

    def check_open(self):
        return self._opened

    def write(self, bytes: bytes):
        self.socket.send(bytes)

    def read(self, num_bytes: int):
        self.__update_while(lambda: len(self.buffer) < num_bytes)
        amount = min(len(self.buffer), num_bytes)
        result = self.buffer[:amount]
        del self.buffer[:amount]
        return result
    
    def peek(self, num_bytes: int):
        self.__update_while(lambda: len(self.buffer) < num_bytes)
        amount = min(len(self.buffer), num_bytes)
        return self.buffer[:amount]
        
    def read_until(self, expected: bytes):
        self.__update_while(lambda: expected not in self.buffer)
        if expected in self.buffer:
            length = self.buffer.index(expected) + len(expected)
            result = self.buffer[:length]
            del self.buffer[:length]
        else:
            result = self.buffer.copy()
            self.buffer.clear()
        return result
    
    def peek_until(self, expected: bytes, max_length: int = None):
        self.__update_while(lambda: expected not in self.buffer and (max_length is None or len(self.buffer) < max_length))
        if expected in self.buffer:
            length = self.buffer.index(expected) + len(expected)
            if max_length is not None:
                length = min(length, max_length)
            result = self.buffer[:length]
        else:
            length = len(self.buffer)
            if max_length is not None:
                length = min(length, max_length)
            result = self.buffer[length]
        return result

    def __update_while(self, condition: Callable):
        start_time = time.perf_counter()
        while condition():
            self.__update_buffer()
            if time.perf_counter() - start_time >= self.timeout:
                return

    def __update_buffer(self, max_bytes: int = 1000):
        self.socket.setblocking(False)
        try:
            self.buffer += self.socket.recv(max_bytes)
        except BlockingIOError:
            return False
        self.socket.settimeout(self.timeout)
        return True

    @property
    def length(self):
        while self.__update_buffer(): pass
        return len(self.buffer)

    @property
    def reenumerates(self) -> bool:
        return False