THREESPACE_HEADER_STATUS_BIT_POS = 0
THREESPACE_HEADER_TIMESTAMP_BIT_POS = 1
THREESPACE_HEADER_ECHO_BIT_POS = 2
THREESPACE_HEADER_CHECKSUM_BIT_POS = 3
THREESPACE_HEADER_SERIAL_BIT_POS = 4
THREESPACE_HEADER_LENGTH_BIT_POS = 5
THREESPACE_HEADER_NUM_BITS = 6

THREESPACE_HEADER_STATUS_BIT = (1 << THREESPACE_HEADER_STATUS_BIT_POS)
THREESPACE_HEADER_TIMESTAMP_BIT = (1 << THREESPACE_HEADER_TIMESTAMP_BIT_POS)
THREESPACE_HEADER_ECHO_BIT = (1 << THREESPACE_HEADER_ECHO_BIT_POS)
THREESPACE_HEADER_CHECKSUM_BIT = (1 << THREESPACE_HEADER_CHECKSUM_BIT_POS)
THREESPACE_HEADER_SERIAL_BIT = (1 << THREESPACE_HEADER_SERIAL_BIT_POS)
THREESPACE_HEADER_LENGTH_BIT = (1 << THREESPACE_HEADER_LENGTH_BIT_POS)

FIRMWARE_VALID_BIT = 0x01

PASSIVE_CALIBRATE_GYRO = (1 << 0)
PASSIVE_CALIBRATE_MAG_REF = (1 << 1)

STREAMING_MAX_HZ = 2000

THREESPACE_OUTPUT_MODE_ASCII = 1
THREESPACE_OUTPUT_MODE_BINARY = 2

THREESPACE_GET_SETTINGS_ERROR_RESPONSE = "<KEY_ERROR>"

#This is not comprehensive, just enough to seperate keys from debug messages
THREESPACE_SETTING_KEY_INVALID_CHARS = " :;"

THREESPACE_FILE_STREAMING_MAX_PACKET_SIZE = 512

THREESPACE_SN_FAMILY_POS = 14 * 4
THREESPACE_SN_VARIATION_POS = 11 * 4
THREESPACE_SN_VERSION_POS = 10 * 4
THREESPACE_SN_MAJOR_REVISION_POS = 8 * 4
THREESPACE_SN_MINOR_REVISION_POS = 6 * 4
THREESPACE_SN_INCREMENTOR_POS = 0 * 4

THREESPACE_SN_FAMILY_MSK = 0xFF << THREESPACE_SN_FAMILY_POS
THREESPACE_SN_VARIATION_MSK = 0xFFF << THREESPACE_SN_VARIATION_POS
THREESPACE_SN_VERSION_MSK = 0xF << THREESPACE_SN_VERSION_POS
THREESPACE_SN_MAJOR_REVISION_MSK = 0xFF << THREESPACE_SN_MAJOR_REVISION_POS
THREESPACE_SN_MINOR_REVISION_MSK = 0xFF << THREESPACE_SN_MINOR_REVISION_POS
THREESPACE_SN_INCREMENTOR_MSK = 0xFFFFFF << THREESPACE_SN_INCREMENTOR_POS

THREESPACE_FAMILY_DEV = "DEV"
THREESPACE_FAMILY_USB = "USB"
THREESPACE_FAMILY_WIRELESS = "WL"
THREESPACE_FAMILY_EMBEDDED = "EM"
THREESPACE_FAMILY_BLUETOOTH = "BT"
THREESPACE_FAMILY_DATA_LOGGER = "DL"
THREESPACE_FAMILY_MICRO_USB = "MUSB"

THREESPACE_SN_FAMILY_TO_NAME = {
    0x00 : THREESPACE_FAMILY_DEV,
    0x11 : THREESPACE_FAMILY_USB,
    0x12 : THREESPACE_FAMILY_WIRELESS,
    0x14 : THREESPACE_FAMILY_EMBEDDED,
    0x15 : THREESPACE_FAMILY_BLUETOOTH,
    0x16 : THREESPACE_FAMILY_DATA_LOGGER,
    0x17 : THREESPACE_FAMILY_MICRO_USB
}