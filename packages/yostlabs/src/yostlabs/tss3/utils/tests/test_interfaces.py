import time
from typing import ClassVar

from yostlabs.tss3.api import ThreespaceSensor
from yostlabs.tss3.utils.tests.base import SensorTest, TestKind, ALL_SENSORS, Busy, Connect, step
from yostlabs.tss3.utils.streaming import ThreespaceStreamingManager

import logging
logger = logging.getLogger(__name__)


class InterfaceTest(SensorTest, abstract=True):
    """
    Tests one of the sensor's communication interfaces, named by a subclass (one test per interface): the runner supplies
    a connection over it (see Connect), and it must reach the same sensor.

    1. Connect: a second ThreespaceSensor over that connection, while the test's own stays open. The sensor answers both
       from one protocol buffer, so their commands may interleave but never overlap: both are only used here, in turn.
    2. Serial number: the second connection's matches the test's own.

    When the test's own connection is over that interface already (see ThreespaceComClass.interface), both checks pass
    without asking for another: a port can't be opened twice. So do they when it's over one of same_module: that module
    is working, and it couldn't be reached over this interface meanwhile.
    """

    interface: ClassVar[str]   # The interface tested, named as in required_hardware, Ex: "ble"
    # Interfaces served by the same module as this one, which can't be connected over both at once
    same_module: ClassVar[frozenset[str]] = frozenset()
    variants = ALL_SENSORS
    kind = TestKind.FUNCTIONAL
    stop_on_failure = True

    def __init_subclass__(cls, **kwargs):
        cls.required_hardware = frozenset({cls.interface})
        cls.description = f"Connects to the sensor over {cls.name}, and checks it's the same sensor."
        super().__init_subclass__(**kwargs)

    def __init__(self, sensor: ThreespaceSensor, streaming_manager: ThreespaceStreamingManager = None):
        super().__init__(sensor, streaming_manager)
        self._other: ThreespaceSensor | None = None   # The sensor over the interface, once connected
        # Why both checks pass without another connection, see the class docstring. None: one is made
        self._checked_through: str | None = None

    @step("Connect")
    def connect(self):
        through = self.sensor.com.interface
        if through == self.interface:
            self._checked_through = f"The test is connected over {self.name}."
        elif through in self.same_module:
            self._checked_through = (f"Checked through the test's {_interface_name(through)} connection: this sensor "
                                     f"can't connect over both at once.")
        if self._checked_through is not None:
            self.check().add_measurement("checked_through", through).passed(self._checked_through)
            return
        com = yield Connect(f"Connect to the sensor over {self.name}.", self.interface)
        if com is None:
            self.check().failed(f"Failed by the operator: the sensor wasn't found over {self.name}.")
            return
        self.check().add_measurement("connection", com.name)
        yield Busy(f"Connecting over {self.name}...")   # Shown while connecting blocks
        start = time.perf_counter()
        try:
            self._other = ThreespaceSensor(com)
        except Exception as e:   # Whatever the com class raises, Ex: a timeout, or a failed pairing
            logger.warning("Could not connect over %s", self.name, exc_info=True)
            self.check().failed(f"Could not connect over {self.name}: {e}")
            return
        self.check().add_measurement("connect_time_s", round(time.perf_counter() - start, 2)).passed()

    @step("Serial number")
    def serial_number(self):
        if self._checked_through is not None:
            self.check().passed(self._checked_through)
            return
        expected = f"{self.sensor.serial_number:016X}"
        found = f"{self._other.serial_number:016X}"
        self.check().add_measurement("serial_number", found).add_criteria("serial_number", expected)
        if found == expected:
            self.check().passed()
        else:
            self.check().failed(f"Reached another sensor over {self.name}: {found}, expected {expected}.")

    def cleanup(self):
        if self._other is not None:
            # Only the connection: ThreespaceSensor.cleanup() would also close any file open on the sensor
            self._other.com.close()


def _interface_name(interface: str) -> str:
    """As the operator sees it, Ex: "Bluetooth Classic": the name of the test of that interface"""
    return next(test.name for test in SensorTest.REGISTERED
                if issubclass(test, InterfaceTest) and test.interface == interface)


class BleInterfaceTest(InterfaceTest):
    id = "ble_interface"
    name = "BLE"
    interface = "ble"
    same_module = frozenset({"bluetooth_classic"})


class BluetoothClassicInterfaceTest(InterfaceTest):
    id = "bluetooth_classic_interface"
    name = "Bluetooth Classic"
    interface = "bluetooth_classic"
    same_module = frozenset({"ble"})
