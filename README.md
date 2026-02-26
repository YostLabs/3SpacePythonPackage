<center><h4>API and Resources for Yost Labs 3.0 Threespace sensors.</h4></center>

## YostLabs Namespace Package System

This package is part of the YostLabs namespace package environment for working with Yost Labs sensors. Available packages include:

- **yostlabs** - Sensor communication and API for 3Space sensors
- **yostlabs-math** - Math utilities for quaternions, vectors, and coordinate transformations
- **yostlabs-graphics** - Graphic API for sensor data visualization


## Installation

`python -m pip install yostlabs`

**⚠️ Important Upgrade Notice:** If you are upgrading from a previous version, the namespace structure has changed and may cause installation conflicts. If you encounter issues during the upgrade, uninstall all existing `yostlabs` packages first and then reinstall the newest versions:

```bash
python -m pip uninstall yostlabs yostlabs-math yostlabs-graphics
python -m pip install yostlabs
```

## Basic Usage

#### USB

```Python
from yostlabs.tss3.api import ThreespaceSensor

#Will auto detect a 3-Space sensor connected to the machine via a USB connection
sensor = ThreespaceSensor()

result = sensor.getPrimaryCorrectedAccelVec()
print(result)

sensor.cleanup()
```

#### BLE

```Python
from yostlabs.tss3.api import ThreespaceSensor
from yostlabs.communication.ble import ThreespaceBLEComClass

#PUT YOUR SENSORS BLE_NAME HERE
ble_name = "YL-TSS-####" #Defaults to the lowest 4 hex digits of the sensors serial number
com_class = ThreespaceBLEComClass(ble_name)
sensor = ThreespaceSensor(com_class)
#sensor = ThreespaceSensor(ThreespaceBLEComClass) #Attempt to auto discover nearby sensor

result = sensor.getPrimaryCorrectedAccelVec()
print(result)

sensor.cleanup()
```

Click [here](https://github.com/YostLabs/3SpacePythonPackage/tree/main/Examples) for more examples.

## Communication

The ThreespaceSensor class utilizes a ThreespaceComClass to define the hardware communication interface between the device utlizing this API and the Threespace Sensor. Currently only the ThreespaceSerialComClass is available for use with the API. New ComClasses for different interfaces will be added to the [communication package](https://github.com/YostLabs/3SpacePythonPackage/tree/main/src/yostlabs/communication) in the future.

To create your own ThreespaceComClass, take a look at the necessary interface definitions [here](https://github.com/YostLabs/3SpacePythonPackage/blob/main/src/yostlabs/communication/base.py) and the Serial implementation [here](https://github.com/YostLabs/3SpacePythonPackage/blob/main/src/yostlabs/communication/serial.py).

## Documentation

WIP. Please review the example scripts. For further assistance contact techsupport@yostlabs.com.
