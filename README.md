<center><h4>API and Resources for Yost Labs 3.0 Threespace sensors.</h4></center>

## Installation

`python -m pip install yostlabs`

## Basic Usage

```Python
from yostlabs.tss3.api import ThreespaceSensor

#Will auto detect a 3-Space sensor connected to the machine via a USB connection
sensor = ThreespaceSensor()

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
