from yostlabs.tss3.api import ThreespaceSensor
from xml.dom import minidom, Node

from typing import Callable

class ThreespaceFirmwareUploader:

    def __init__(self, sensor: ThreespaceSensor, file_path: str = None, percentage_callback: Callable[[int],None] = None, verbose: bool = False):
        self.sensor = sensor
        self.set_firmware_path(file_path)
        self.verbose = verbose

        self.percent_complete = 0
        self.callback = percentage_callback

    def set_firmware_path(self, file_path: str):
        if file_path is None: 
            self.firmware = None
            return
        self.firmware = minidom.parse(file_path)

    def set_percent_callback(self, callback: Callable[[float],None]):
        self.callback = callback

    def set_verbose(self, verbose: bool):
        self.verbose = verbose

    def get_percent_done(self):
        return self.percent_complete
    
    def __set_percent_complete(self, percent: float):
        self.percent_complete = percent
        if self.callback:
            self.callback(percent)

    def log(self, *args):
        if not self.verbose: return
        print(*args)

    def upload_firmware(self):
        self.percent_complete = 0
        if not self.sensor.in_bootloader:
            self.sensor.enterBootloader()
        self.__set_percent_complete(5)
        
        boot_info = self.sensor.bootloader_get_info()

        root = self.firmware.firstChild
        for c in root.childNodes:
            if c.nodeType == Node.ELEMENT_NODE:
                name = c.nodeName
                if name == "SetAddr":
                    self.log("Write S")
                    error = self.sensor.bootloader_erase_firmware()
                    if error:
                        self.log("Failed to erase firmware:", error)
                    else:
                        self.log("Successfully erased firmware")
                    self.__set_percent_complete(20)
                elif name == "MemProgC":
                    mem = bytes.fromhex(c.firstChild.nodeValue)
                    self.log("Attempting to program", len(mem), "bytes to the chip")
                    cpos = 0
                    while cpos < len(mem):
                        memchunk = mem[cpos : min(len(mem), cpos + boot_info.pagesize)]
                        error = self.sensor.bootloader_prog_mem(memchunk)
                        if error:
                            self.log("Failed upload:", error)
                        else:
                            self.log("Wrote", len(memchunk), "bytes successfully to offset", cpos)
                        cpos += len(memchunk)
                        self.__set_percent_complete(20 + cpos / len(mem) * 79)
                elif name == "Run":
                    self.log("Resetting with new firmware.")
                    self.sensor.bootloader_boot_firmware()
                    self.__set_percent_complete(100)