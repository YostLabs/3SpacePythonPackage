"""
Custom exception hierarchy for the Threespace sensor API.

Hierarchy
---------
ThreespaceError
├── DiscoveryError          - Could not find / identify a sensor
├── SensorConnectionError   - Connection to a known sensor was lost or failed
├── ResponseError           - Sensor responded but the response was wrong/unexpected
│   ├── ResponseTimeoutError    - No response received within the timeout window
│   └── ChecksumMismatchError   - Response arrived but data integrity check failed
├── SettingError            - Base for all setting key/access problems
│   ├── UnregisteredKeyError    - Key is not in the API's own settings table
│   ├── InvalidKeyError         - Firmware rejected the key (e.g. <KEY_ERROR> response)
│   └── SettingAccessError      - Setting exists but cannot be read or written that way
└── UnsupportedCommandError - Command is not available on this particular sensor
"""


class ThreespaceError(Exception):
    """Base class for all Threespace API errors."""


class DiscoveryError(ThreespaceError):
    """Raised when a sensor cannot be found or its mode (firmware/bootloader) cannot be determined."""


class SensorConnectionError(ThreespaceError):
    """Raised when a connection to a previously identified sensor is lost or cannot be established."""


# --- Response errors ---

class ResponseError(ThreespaceError):
    """Raised when the sensor responds but the response is malformed or unexpected."""


class ResponseTimeoutError(ResponseError):
    """Raised when no response is received from the sensor within the allowed timeout."""


class ChecksumMismatchError(ResponseError):
    """Raised when a response is received but its checksum does not match the expected value."""


# --- Setting errors ---

class SettingError(ThreespaceError):
    """Base class for errors related to sensor settings keys."""


class UnregisteredKeyError(SettingError):
    """Raised when a setting key is not recognised by the API's own settings table."""


class InvalidKeyError(SettingError):
    """Raised when the firmware explicitly rejects a setting key (e.g. returns <KEY_ERROR>)."""


class SettingAccessError(SettingError):
    """Raised when a setting exists but cannot be accessed in the requested way (e.g. write-only read attempt)."""


# --- Command errors ---

class UnsupportedCommandError(ThreespaceError):
    """Raised when a command is not available on the connected sensor model."""
