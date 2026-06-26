from abc import ABC, abstractmethod
from yostlabs.tss3 import ThreespaceSensor


class SensorTestBase(ABC):

    def __init__(self, sensor: ThreespaceSensor):
        self.sensor = sensor
        self.overall_success: bool = True
        self.result: dict = {}

    @abstractmethod
    def start(self):
        """Begin the test, setting up hardware as needed."""
        ...

    def cancel(self):
        """Abort the test and restore any hardware state changed by start()."""
        ...
