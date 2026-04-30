from enum import Enum
import re
from typing import Any, Callable
from dataclasses import dataclass, field
from yostlabs.communication.base import ThreespaceInputStream, ThreespaceOutputStream
from yostlabs.tss3.commands import ThreespaceFormat, yost_format_conversion_dict

class ThreespaceSetting:
    
    def __init__(self, name: str, in_format: str|None, out_format: str|None):
        self.name = name
        if in_format is not None:
            self.in_format = ThreespaceFormat(in_format)
        else:
            self.in_format = None

        if out_format is not None:
            self.out_format = ThreespaceFormat(out_format)
            self.out_format.precompute_segments()
        else:
            self.out_format = None

class ThreespaceCmdSetting(ThreespaceSetting):
    def __init__(self, name: str):
        super().__init__(name, "", None)

class ThreespaceAggregateSetting(ThreespaceSetting):
    def __init__(self, name: str):
        super().__init__(name, None, "")

class ThreespaceReadSetting(ThreespaceSetting):
    def __init__(self, name: str, format: str):
        super().__init__(name, None, format)

class ThreespaceWriteSetting(ThreespaceSetting):
    def __init__(self, name: str, format: str):
        super().__init__(name, format, None)

class ThreespaceReadWriteSetting(ThreespaceSetting):
    def __init__(self, name: str, format: str):
        super().__init__(name, format, format)

THREESPACE_SETTINGS_LIST: list[ThreespaceSetting] = [
    ThreespaceCmdSetting("default"),
    ThreespaceCmdSetting("commit"),
    ThreespaceCmdSetting("reboot"),
    ThreespaceAggregateSetting("all"),
    ThreespaceAggregateSetting("settings"),
    ThreespaceReadSetting("serial_number", "U"),
    ThreespaceReadWriteSetting("timestamp", "U"),
    ThreespaceReadWriteSetting("led_mode", "b"),
    ThreespaceReadWriteSetting("led_rgb", "fff"),
    ThreespaceReadSetting("version_firmware", "S"),
    ThreespaceReadSetting("version_hardware", "S"),
    ThreespaceReadSetting("update_rate_sensor", "u"),
    ThreespaceReadWriteSetting("header", "b"),
    ThreespaceReadWriteSetting("header_status", "b"),
    ThreespaceReadWriteSetting("header_timestamp", "b"),
    ThreespaceReadWriteSetting("header_echo", "b"),
    ThreespaceReadWriteSetting("header_checksum", "b"),
    ThreespaceReadWriteSetting("header_serial", "b"),
    ThreespaceReadWriteSetting("header_length", "b"),
    ThreespaceReadSetting("valid_commands", "S"),
    ThreespaceReadWriteSetting("cpu_speed", "u"),
    ThreespaceReadSetting("cpu_speed_cur", "u"),
    ThreespaceWriteSetting("pm_mode", "b"),
    ThreespaceReadWriteSetting("pm_idle_enabled", "b"),
    ThreespaceReadWriteSetting("stream_slots", "S"),
    ThreespaceReadWriteSetting("stream_interval", "U"),
    ThreespaceReadWriteSetting("stream_hz", "f"),
    ThreespaceReadWriteSetting("stream_duration", "f"),
    ThreespaceReadWriteSetting("stream_delay", "f"),
    ThreespaceReadWriteSetting("stream_mode", "b"),
    ThreespaceReadWriteSetting("stream_count", "U"),
    ThreespaceReadSetting("streamable_commands", "S"),
    ThreespaceReadWriteSetting("debug_level", "u"),
    ThreespaceReadWriteSetting("debug_module", "u"),
    ThreespaceReadWriteSetting("debug_mode", "b"),
    ThreespaceReadWriteSetting("debug_led", "b"),
    ThreespaceReadWriteSetting("debug_fault", "b"),
    ThreespaceReadWriteSetting("debug_wdt", "b"),
    ThreespaceReadWriteSetting("axis_order", "S"),
    ThreespaceReadWriteSetting("axis_order_c", "S"),
    ThreespaceReadWriteSetting("axis_offset_enabled", "b"),
    ThreespaceReadWriteSetting("euler_order", "S"),
    ThreespaceReadSetting("update_rate_filter", "u"),
    ThreespaceReadSetting("update_rate_sms", "u"),
    ThreespaceReadWriteSetting("offset", "ffff"),
    ThreespaceReadWriteSetting("base_offset", "ffff"),
    ThreespaceReadWriteSetting("tare_quat", "ffff"),
    ThreespaceReadWriteSetting("tare_auto_base", "b"),
    ThreespaceReadWriteSetting("base_tare", "ffff"),
    ThreespaceReadWriteSetting("tare_mat", "fffffffff"),
    ThreespaceReadWriteSetting("running_avg_orient", "f"),
    ThreespaceReadWriteSetting("filter_mode", "b"),
    ThreespaceReadWriteSetting("filter_mref_mode", "b"),
    ThreespaceReadWriteSetting("filter_mref", "fff"),
    ThreespaceWriteSetting("filter_mref_gps", "dd"),
    ThreespaceReadWriteSetting("filter_mref_dip", "f"),
    ThreespaceReadWriteSetting("filter_conf_thresholds", "fff"),
    ThreespaceReadSetting("valid_accels", "S"),
    ThreespaceReadSetting("valid_gyros", "S"),
    ThreespaceReadSetting("valid_mags", "S"),
    ThreespaceReadSetting("valid_baros", "S"),
    ThreespaceReadSetting("valid_components", "S"),
    ThreespaceReadWriteSetting("primary_accel", "S"),
    ThreespaceReadWriteSetting("primary_gyro", "S"),
    ThreespaceReadWriteSetting("primary_mag", "S"),
    ThreespaceReadWriteSetting("primary_sensor_rfade", "f"),
    ThreespaceReadWriteSetting("mag_bias_mode", "b"),
    ThreespaceWriteSetting("odr_all", "u"),
    ThreespaceWriteSetting("odr_accel", "u"),
    ThreespaceWriteSetting("odr_gyro", "u"),
    ThreespaceWriteSetting("odr_mag", "u"),
    ThreespaceWriteSetting("odr_baro", "u"),
    ThreespaceReadWriteSetting("accel_enabled", "b"),
    ThreespaceReadWriteSetting("gyro_enabled", "b"),
    ThreespaceReadWriteSetting("mag_enabled", "b"),
    ThreespaceReadWriteSetting("calib_mat_accel%d", "fffffffff"),
    ThreespaceReadWriteSetting("calib_bias_accel%d", "fff"),
    ThreespaceReadWriteSetting("range_accel%d", "B"),
    ThreespaceReadSetting("valid_ranges_accel%d", "S"),
    ThreespaceReadWriteSetting("oversample_accel%d", "B"),
    ThreespaceReadWriteSetting("running_avg_accel%d", "f"),
    ThreespaceReadWriteSetting("odr_accel%d", "u"),
    ThreespaceReadSetting("update_rate_accel%d", "f"),
    ThreespaceReadSetting("noise_profile_accel%d", "ffffu"),
    ThreespaceReadWriteSetting("calib_mat_gyro%d", "fffffffff"),
    ThreespaceReadWriteSetting("calib_bias_gyro%d", "fff"),
    ThreespaceReadWriteSetting("range_gyro%d", "B"),
    ThreespaceReadSetting("valid_ranges_gyro%d", "S"),
    ThreespaceReadWriteSetting("oversample_gyro%d", "B"),
    ThreespaceReadWriteSetting("running_avg_gyro%d", "f"),
    ThreespaceReadWriteSetting("odr_gyro%d", "u"),
    ThreespaceReadSetting("update_rate_gyro%d", "f"),
    ThreespaceReadSetting("noise_profile_gyro%d", "ffffu"),
    ThreespaceReadWriteSetting("calib_mat_mag%d", "fffffffff"),
    ThreespaceReadWriteSetting("calib_bias_mag%d", "fff"),
    ThreespaceReadWriteSetting("range_mag%d", "B"),
    ThreespaceReadSetting("valid_ranges_mag%d", "S"),
    ThreespaceReadWriteSetting("oversample_mag%d", "B"),
    ThreespaceReadWriteSetting("running_avg_mag%d", "f"),
    ThreespaceReadWriteSetting("odr_mag%d", "u"),
    ThreespaceReadSetting("update_rate_mag%d", "f"),
    ThreespaceReadSetting("noise_profile_mag%d", "ffffu"),
    ThreespaceReadWriteSetting("calib_bias_baro%d", "f"),
    ThreespaceWriteSetting("calib_altitude_baro%d", "f"),
    ThreespaceReadWriteSetting("odr_baro%d", "u"),
    ThreespaceReadSetting("update_rate_baro%d", "f"),
    ThreespaceReadWriteSetting("pts_offset_quat", "ffff"),
    ThreespaceCmdSetting("pts_default"),
    ThreespaceAggregateSetting("pts_settings"),
    ThreespaceWriteSetting("pts_preset_hand", "b"),
    ThreespaceWriteSetting("pts_preset_motion", "b"),
    ThreespaceWriteSetting("pts_preset_heading", "b"),
    ThreespaceReadWriteSetting("pts_debug_level", "u"),
    ThreespaceReadWriteSetting("pts_debug_module", "u"),
    ThreespaceReadWriteSetting("pts_heading_mode", "u"),
    ThreespaceReadWriteSetting("pts_initial_heading_mode", "u"),
    ThreespaceReadWriteSetting("pts_hand_heading_mode", "u"),
    ThreespaceReadWriteSetting("pts_mag_declination", "f"),
    ThreespaceReadWriteSetting("pts_auto_declination", "b"),
    ThreespaceReadWriteSetting("pts_discard_slow", "b"),
    ThreespaceReadWriteSetting("pts_segment_axis", "u"),
    ThreespaceReadWriteSetting("pts_seg_noise", "f"),
    ThreespaceReadWriteSetting("pts_classifier_mode", "u"),
    ThreespaceReadWriteSetting("pts_classifier_mode2", "u"),
    ThreespaceReadWriteSetting("pts_location_classifier_mode", "u"),
    ThreespaceReadWriteSetting("pts_hand_classifier_threshold", "f"),
    ThreespaceReadWriteSetting("pts_disabled_truth_motions", "u"),
    ThreespaceReadWriteSetting("pts_dynamic_segmenter_enabled", "b"),
    ThreespaceReadWriteSetting("pts_estimator_scalars", "fffffff"),
    ThreespaceReadWriteSetting("pts_auto_estimator_scalar_rate", "u"),
    ThreespaceReadWriteSetting("pts_running_correction", "b"),
    ThreespaceReadWriteSetting("pts_hand_correction", "b"),
    ThreespaceReadWriteSetting("pts_heading_correction_mode", "u"),
    ThreespaceReadWriteSetting("pts_heading_min_dif", "f"),
    ThreespaceReadWriteSetting("pts_heading_reset_consistencies", "b"),
    ThreespaceReadWriteSetting("pts_heading_backtrack_enabled", "b"),
    ThreespaceReadWriteSetting("pts_motion_correction_radius", "u"),
    ThreespaceReadWriteSetting("pts_motion_correction_consistency_req", "u"),
    ThreespaceReadWriteSetting("pts_orient_ref_y_threshold", "f"),
    ThreespaceReadSetting("pts_version", "S"),
    ThreespaceReadWriteSetting("pts_date", "uuu"),
    ThreespaceReadSetting("pts_wmm_version", "S"),
    ThreespaceWriteSetting("pts_wmm_set", "S"),
    ThreespaceReadWriteSetting("pts_force_out_gps", "b"),
    ThreespaceReadWriteSetting("pts_initial_heading_tolerance", "f"),
    ThreespaceReadWriteSetting("pts_heading_consistency_req", "l"),
    ThreespaceReadWriteSetting("pts_heading_root_err_mul", "f"),
    ThreespaceReadWriteSetting("pts_heading_consistent_bias", "f"),
    ThreespaceReadWriteSetting("pts_strict_bias_enabled", "b"),
    ThreespaceReadWriteSetting("pin_mode0", "b"),
    ThreespaceReadWriteSetting("pin_mode1", "b"),
    ThreespaceReadWriteSetting("uart_baudrate", "u"),
    ThreespaceReadWriteSetting("i2c_addr", "b"),
    ThreespaceReadWriteSetting("power_hold_time", "f"),
    ThreespaceReadWriteSetting("power_hold_state", "b"),
    ThreespaceReadWriteSetting("power_initial_hold_state", "b"),
    ThreespaceCmdSetting("fs_cfg_load"),
    ThreespaceReadWriteSetting("fs_msc_enabled", "b"),
    ThreespaceReadWriteSetting("fs_msc_auto", "b"),
    ThreespaceReadWriteSetting("log_slots", "S"),
    ThreespaceReadWriteSetting("log_interval", "U"),
    ThreespaceReadWriteSetting("log_hz", "f"),
    ThreespaceReadWriteSetting("log_start_event", "S"),
    ThreespaceReadWriteSetting("log_start_motion_threshold", "f"),
    ThreespaceReadWriteSetting("log_stop_event", "S"),
    ThreespaceReadWriteSetting("log_stop_motion_threshold", "f"),
    ThreespaceReadWriteSetting("log_stop_motion_delay", "f"),
    ThreespaceReadWriteSetting("log_stop_count", "U"),
    ThreespaceReadWriteSetting("log_stop_duration", "f"),
    ThreespaceReadWriteSetting("log_stop_period_count", "u"),
    ThreespaceReadWriteSetting("log_style", "b"),
    ThreespaceReadWriteSetting("log_periodic_capture_time", "f"),
    ThreespaceReadWriteSetting("log_periodic_rest_time", "f"),
    ThreespaceReadWriteSetting("log_base_filename", "S"),
    ThreespaceReadWriteSetting("log_file_mode", "b"),
    ThreespaceReadWriteSetting("log_data_mode", "b"),
    ThreespaceReadWriteSetting("log_output_settings", "b"),
    ThreespaceReadWriteSetting("log_header_enabled", "b"),
    ThreespaceReadWriteSetting("log_folder_mode", "b"),
    ThreespaceReadWriteSetting("log_immediate_output", "b"),
    ThreespaceReadWriteSetting("log_immediate_output_header_enabled", "b"),
    ThreespaceReadWriteSetting("log_immediate_output_header_mode", "b"),
    ThreespaceReadWriteSetting("rtc_year", "B"),
    ThreespaceReadWriteSetting("rtc_month", "b"),
    ThreespaceReadWriteSetting("rtc_day", "b"),
    ThreespaceReadWriteSetting("rtc_hour", "b"),
    ThreespaceReadWriteSetting("rtc_minute", "b"),
    ThreespaceReadWriteSetting("rtc_second", "b"),
    ThreespaceReadWriteSetting("rtc_datetime", "Bbbbbb"),
    ThreespaceReadWriteSetting("bat_chg_rate", "B"),
    ThreespaceReadWriteSetting("bat_cold_threshold", "ff"),
    ThreespaceReadWriteSetting("bat_warm_threshold", "ff"),
    ThreespaceReadWriteSetting("bat_hot_threshold", "ff"),
    ThreespaceReadWriteSetting("bat_offset_threshold", "f"),
    ThreespaceReadSetting("bat_mah", "B"),
    ThreespaceReadWriteSetting("ble_name", "S"),
    ThreespaceReadSetting("ble_connected", "i"),
    ThreespaceCmdSetting("ble_disconnect"),
    ThreespaceReadWriteSetting("gps_standby", "b"),
    ThreespaceReadWriteSetting("gps_led", "b"),
    ThreespaceCmdSetting("sd_cfg_load"),
    ThreespaceReadWriteSetting("sd_msc_enabled", "b"),
    ThreespaceReadWriteSetting("sd_msc_auto", "b"),
    ThreespaceReadWriteSetting("cat", "S"),
    ThreespaceReadWriteSetting("running_avg", "f"),
]
THREESPACE_SETTINGS: dict[str, ThreespaceSetting] = { setting.name : setting for setting in THREESPACE_SETTINGS_LIST }

# Pre-compiled patterns for settings whose names contain %d, allowing numeric lookups.
THREESPACE_SETTINGS_PATTERNS: list[tuple[re.Pattern, ThreespaceSetting]] = [
    (re.compile("^" + r"\d+".join(re.escape(part) for part in setting.name.split("%d")) + "$"), setting)
    for setting in THREESPACE_SETTINGS_LIST
    if "%d" in setting.name
]

def threespace_setting_get(name: str) -> ThreespaceSetting:
    #Try direct lookup first
    result = THREESPACE_SETTINGS.get(name, None)

    #Attempt pattern matching if direct lookup fails
    if result is None:
        for pattern, setting in THREESPACE_SETTINGS_PATTERNS:
            if pattern.match(name):
                return setting
    return result

def threespace_settings_string_to_dict(setting_string: str):
    d = {}
    for item in setting_string.split(';'):
        result = item.split('=')
        key = result[0]
        if len(result) == 1:
            value = None
        else:
            value = '='.join(result[1:]) #In case = was part of the value, do a join
        
        d[key] = value
    return d

#------------------------Additional setting information for display and validation purposes, not used for parsing or communication------------------------

class ThreespaceSettingParamValidationMode(Enum):
    NONE = "none"
    ENUM = "enum"
    RANGE = "range"
    BOOL = "bool" #Special version of enum that is more descriptive of the intent
    CUSTOM = "custom"

@dataclass
class ThreespaceSettingParamDescriptor:
    format_specifier: str = None
    
    #EG: Hex, Decimal, Binary, ... This is just a hint for display purposes and doesn't affect parsing or validation.
    preferred_display_mode: str = None
    unit: str = ""
    suffix: str = "" #EG: "m/s^2", "degrees", "RPM", ... This is just for display purposes and doesn't affect parsing or validation.

    #EG: "range", "enum", "custom", "None"
    validation_mode: ThreespaceSettingParamValidationMode = ThreespaceSettingParamValidationMode.NONE

    #Used for enum, mapping of value name to sensor value
    #Useful for mode fields, and for actual fields where the value is the key, easy enough to map
    valid_values: dict[str, Any] = None

    #Used for range
    min_value: float = None #Inclusive
    max_value: float = None #Inclusive

    custom_validator: Callable = None

    def __post_init__(self):
        if self.validation_mode == ThreespaceSettingParamValidationMode.ENUM:
            if self.valid_values is None:
                raise ValueError("Enum validation mode requires valid_values to be set.")
            elif isinstance(self.valid_values, (list, tuple)):
                self.valid_values = {str(i): i for i in self.valid_values}
        elif self.validation_mode == ThreespaceSettingParamValidationMode.RANGE and (self.min_value is None or self.max_value is None):
            raise ValueError("Range validation mode requires min_value and max_value to be set.")
        elif self.validation_mode == ThreespaceSettingParamValidationMode.CUSTOM and self.custom_validator is None:
            raise ValueError("Custom validation mode requires custom_validator to be set.")

    def valid_value_keys(self, suffix=True) -> list[str]:
        if self.validation_mode != ThreespaceSettingParamValidationMode.ENUM:
            raise ValueError("valid_value_keys is only applicable for ENUM validation mode.")
        return [self.value_to_string(value, suffix) for value in self.valid_values.values()]

    def string_to_value(self, s: str) -> Any:
        if self.validation_mode == ThreespaceSettingParamValidationMode.ENUM:
            if self.suffix:
                s = s.removesuffix(self.suffix)
            if s not in self.valid_values:
                raise ValueError(f"Invalid value string '{s}' for ENUM setting. Valid options are: {', '.join(self.valid_value_keys())}")
            return self.valid_values[s]
        else:
            #No special parsing for other modes, just return the string (or convert to number if preferred display mode is hex)
            if self.format_specifier in ['b', 'B', 'u', 'U', 'i', 'I', 'l', 'L']:
                 #For numeric types, attempt to parse the string as a number, supporting hex if preferred display mode is hex
                try:
                    if self.preferred_display_mode == "hex" and s.startswith("0x"):
                        return int(s, 16)
                    return int(s)
                except ValueError:
                    raise ValueError(f"Invalid value string '{s}' for numeric setting. Expected an integer.")
            elif self.format_specifier in ['f', 'd']:
                try:
                    return float(s)
                except ValueError:
                    raise ValueError(f"Invalid value string '{s}' for numeric setting. Expected a float.")
            elif self.format_specifier in ['s', 'S']:
                return s
            else:
                raise ValueError(f"Unsupported format specifier '{self.format_specifier}' for string_to_value parsing.")


    def value_to_string(self, value: Any, suffix=True) -> str:
        if self.validation_mode == ThreespaceSettingParamValidationMode.ENUM:
            reverse_map = {v: k for k, v in self.valid_values.items()}
            return reverse_map.get(value, str(value)) + (self.suffix if suffix else "")
        elif self.validation_mode == ThreespaceSettingParamValidationMode.BOOL:
            return ("True" if value else "False") + (self.suffix if suffix else "")
        else:
            if self.preferred_display_mode == "hex":
                return f"0x{value:X}{self.suffix if suffix else ''}"
            if self.format_specifier in ['f', 'd']:
                return f"{value:.6g}{self.suffix if suffix else ''}"
            return f"{value}{self.suffix if suffix else ''}"
    
    def validate(self, value: Any) -> bool:
        if self.validation_mode == ThreespaceSettingParamValidationMode.ENUM:
            return value in self.valid_values
        elif self.validation_mode == ThreespaceSettingParamValidationMode.RANGE:
            return self.min_value <= value <= self.max_value
        elif self.validation_mode == ThreespaceSettingParamValidationMode.CUSTOM:
            if self.custom_validator is not None:
                return self.custom_validator(value)
            return False #If custom validation mode is selected but no validator is provided, consider all values invalid.
        elif self.validation_mode == ThreespaceSettingParamValidationMode.BOOL:
            #Bools on the sensor are treated as U8 0 or 1, so only those values are valid.
            return value in [0, 1]
        else:
            #No validation criteria, consider valid by default
            return True
    
    @staticmethod
    def create_default_from_type(t: str):
        if t not in yost_format_conversion_dict:
            raise ValueError(f"Unsupported type character for default validation: {t}")
        
        type_to_range = {
            'b' : (0, 0xFF),
            'B' : (0, 0xFFFF),
            'u' : (0, 0xFFFFFFFF),
            'U' : (0, 0xFFFFFFFFFFFFFFFF),
            'i' : (-0x80, 0x7F),
            'I' : (-0x8000, 0x7FFF),
            'l' : (-0x80000000, 0x7FFFFFFF),
            'L' : (-0x8000000000000000, 0x7FFFFFFFFFFFFFFF),
        }
        if t in type_to_range:
            return ThreespaceSettingParamDescriptor(
                format_specifier=t,
                validation_mode=ThreespaceSettingParamValidationMode.RANGE, 
                min_value=type_to_range[t][0], 
                max_value=type_to_range[t][1])
        return ThreespaceSettingParamDescriptor()

@dataclass
class ThreespaceSettingDescriptor:
    key: str

    setting: ThreespaceSetting = None
    param_descriptors: list[ThreespaceSettingParamDescriptor] = field(default_factory=list)

    def __init__(self, key: str, descriptor: ThreespaceSettingParamDescriptor = None, descriptors: list[ThreespaceSettingParamDescriptor] = None):
        self.key = key
        self.setting = threespace_setting_get(key)

        self.format_source = None
        if self.setting.out_format is not None:
            self.format_source = self.setting.out_format
        elif self.setting.in_format is not None:
            self.format_source = self.setting.in_format

        if descriptors is not None:
            self.param_descriptors = descriptors
        elif descriptor is not None:
            if self.format_source is None:
                raise ValueError("Cannot use single descriptor for a setting with no format string.")
            self.param_descriptors = [descriptor] * len(self.format_string)
        else:
            #Default, validation will be based on setting types and no preferred display mode or suffix
            self.param_descriptors = []
            for t in self.format_string:
                self.param_descriptors.append(ThreespaceSettingParamDescriptor.create_default_from_type(t))
        
        for i, t in enumerate(self.format_string):
            self.param_descriptors[i].format_specifier = t
    
    @property
    def format_string(self):
        return self.setting.out_format.internal_format

    

def _validate_axis_order(value: str) -> bool:
    """Validate an axis order string.
    
    The string must contain each of 'x', 'y', and 'z' exactly once, in any order,
    with an optional single '-' prefix on one or more axes.
    Valid examples: "xyz", "zyx", "-xyz", "x-yz", "-x-y-z"
    Invalid examples: "--xyz", "xy", "xxz", "x-yz-"
    """
    value = value.lower() #Case doesn't matter
    letters = value.replace('-', '')
    if sorted(letters) != ['x', 'y', 'z']:
        return False
    if '--' in value or value.endswith('-'):
        return False
    return True

def _validate_axis_order_c(value: str) -> bool:
    value = value.lower() #Case doesn't matter
    if len(value) != 3:
        return False
    return ('e' in value or 'w' in value) and ('n' in value or 's' in value) and ('u' in value or 'd' in value)

def _validate_comma_separated_allowed(value: str, allowed_values: set[str]) -> bool:
    items = [item.strip() for item in value.split(',')]
    return all(item in allowed_values for item in items)

#Just to make this list less verbose, since these names are very long
TSD = ThreespaceSettingDescriptor
TSPD = ThreespaceSettingParamDescriptor
TSPDV = ThreespaceSettingParamValidationMode
#Anything NOT in this list either is default or specific to the sensor (and so is populated by the sensor object)
THREESPACE_SETTINGS_DEFAULT_DESC_LIST: list[ThreespaceSettingDescriptor] = [
    TSD("serial_number", TSPD(preferred_display_mode="hex")),
    TSD("led_mode", TSPD(validation_mode=TSPDV.ENUM, valid_values={"Dynamic": 0, "Static": 1})),
    TSD("led_rgb", TSPD(validation_mode=TSPDV.RANGE, min_value=0.0, max_value=1.0)),
    TSD("header_status", TSPD(validation_mode=TSPDV.BOOL)),
    TSD("header_timestamp", TSPD(validation_mode=TSPDV.BOOL)),
    TSD("header_echo", TSPD(validation_mode=TSPDV.BOOL)),
    TSD("header_checksum", TSPD(validation_mode=TSPDV.BOOL)),
    TSD("header_serial", TSPD(validation_mode=TSPDV.BOOL)),
    TSD("header_length", TSPD(validation_mode=TSPDV.BOOL)),
    TSD("cpu_speed", TSPD(unit="megahertz", suffix="MHz", validation_mode=TSPDV.ENUM, valid_values=[48, 96, 144, 192])),
    TSD("pm_idle_enabled", TSPD(validation_mode=TSPDV.BOOL)),
    TSD("stream_interval", TSPD(unit="microseconds", suffix="us", validation_mode=TSPDV.RANGE, min_value=500, max_value=0xFFFFFFFFFFFFFFFF)),
    TSD("stream_hz", TSPD(unit="hertz", suffix="Hz", validation_mode=TSPDV.RANGE, min_value=0, max_value=2000.0)),
    TSD("stream_duration", TSPD(unit="seconds", suffix="s", validation_mode=TSPDV.RANGE, min_value=0, max_value=int(0xFFFFFFFFFFFFFFFF / 1_000_000))),
    TSD("stream_delay", TSPD(unit="seconds", suffix="s", validation_mode=TSPDV.RANGE, min_value=0, max_value=int(0xFFFFFFFFFFFFFFFF / 1_000_000))),
    TSD("stream_mode", TSPD(validation_mode=TSPDV.ENUM, valid_values={"Duration": 0, "Count": 1})),
    TSD("stream_count", TSPD(validation_mode=TSPDV.RANGE, min_value=1, max_value=0xFFFFFFFFFFFFFFFF)),
    TSD("debug_level", TSPD(preferred_display_mode="hex")),
    TSD("debug_module", TSPD(preferred_display_mode="hex")),
    TSD("debug_mode", TSPD(validation_mode=TSPDV.ENUM, valid_values={"Buffered": 0, "Immediate": 1})),
    TSD("debug_led", TSPD(validation_mode=TSPDV.BOOL)),
    TSD("debug_fault", TSPD(validation_mode=TSPDV.BOOL)),
    TSD("debug_wdt", TSPD(validation_mode=TSPDV.BOOL)),
    TSD("axis_order", TSPD(validation_mode=TSPDV.CUSTOM, custom_validator=_validate_axis_order)),
    TSD("axis_order_c", TSPD(validation_mode=TSPDV.CUSTOM, custom_validator=_validate_axis_order_c)),
    TSD("axis_offset_enabled", TSPD(validation_mode=TSPDV.BOOL)),
    TSD("euler_order", TSPD(validation_mode=TSPDV.ENUM, 
        valid_values=[
            "XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX", "XYX", "XZX", "YXY", "YZY", "ZXZ", "ZYZ",
            "XYZi", "XZYi", "YXZi", "YZXi", "ZXYi", "ZYXi", "XYXi", "XZXi", "YXYi", "YZYi", "ZXZi", "ZYZi",
            "XYZe", "XZYe", "YXZe", "YZXe", "ZXYe", "ZYXe", "XYXe", "XZXe", "YXYe", "YZYe", "ZXZe", "ZYZe"])),
    TSD("tare_auto_base", TSPD(validation_mode=TSPDV.BOOL)),
    TSD("running_avg_orient", TSPD(validation_mode=TSPDV.RANGE, min_value=0, max_value=1)),
    TSD("filter_mode", TSPD(validation_mode=TSPDV.ENUM, valid_values={"IMU": 0, "QGRAD3": 1, "EKF": 2})),
    TSD("filter_mref_mode", TSPD(validation_mode=TSPDV.ENUM, valid_values={"Manual": 0, "Semi-Auto": 1, "Auto": 2})), #"GPS": 3 (not implemented yet)
    TSD("filter_mref_dip", TSPD(unit="degrees", suffix="degrees", validation_mode=TSPDV.RANGE, min_value=-90, max_value=90)),
    TSD("filter_conf_thresholds", TSPD(validation_mode=TSPDV.RANGE, min_value=0.0, max_value=1000.0)),
    TSD("primary_sensor_rfade", TSPD(validation_mode=TSPDV.RANGE, min_value=0.0, max_value=1.0)),
    TSD("mag_bias_mode", TSPD(validation_mode=TSPDV.ENUM, valid_values={"Manual": 0, "Auto Single": 1, "Auto Continuous": 2})),
    TSD("accel_enabled", TSPD(validation_mode=TSPDV.BOOL)),
    TSD("gyro_enabled", TSPD(validation_mode=TSPDV.BOOL)),
    TSD("mag_enabled", TSPD(validation_mode=TSPDV.BOOL)),
    #PTS Settings Here
    #PTS Settings End
    TSD("pin_mode0", TSPD(validation_mode=TSPDV.ENUM, valid_values={"UART": 1, "Orient Level": 4, "Orient Pulse": 5, "Button": 7, "TransactionIRQ": 8})),
    TSD("pin_mode1", TSPD(validation_mode=TSPDV.ENUM, valid_values={"SPI": 2, "I2C": 3, "Orient Level": 4, "Orient Pulse": 5, "Button": 7})),
    TSD("uart_baudrate", TSPD(validation_mode=TSPDV.ENUM, valid_values=[4800, 9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600, 2000000, 4000000])),
    TSD("i2c_addr", TSPD(preferred_display_mode="hex")),
    TSD("power_hold_time", TSPD(unit="seconds", suffix="s", validation_mode=TSPDV.RANGE, min_value=-1, max_value=60)), #Technically -1 is the only value less than 0 that is valid, but don't have a great way to express that right now
    TSD("power_hold_state", TSPD(validation_mode=TSPDV.BOOL)),
    TSD("power_initial_hold_state", TSPD(validation_mode=TSPDV.BOOL)),
    TSD("fs_msc_enabled", TSPD(validation_mode=TSPDV.BOOL)),
    TSD("fs_msc_auto", TSPD(validation_mode=TSPDV.BOOL)),
    TSD("log_interval", TSPD(unit="microseconds", suffix="us", validation_mode=TSPDV.RANGE, min_value=500, max_value=0xFFFFFFFFFFFFFFFF)),
    TSD("log_hz", TSPD(unit="hertz", suffix="Hz", validation_mode=TSPDV.RANGE, min_value=0.000001, max_value=2000.0)),
    TSD("log_start_event", TSPD(validation_mode=TSPDV.CUSTOM, custom_validator=lambda s: _validate_comma_separated_allowed(s, {"0", "1", "2", "3", "4"}))),
    TSD("log_start_motion_threshold", TSPD(unit="g-force", suffix="g", validation_mode=TSPDV.RANGE, min_value=0, max_value=1000.0)),
    TSD("log_stop_event", TSPD(validation_mode=TSPDV.CUSTOM, custom_validator=lambda s: _validate_comma_separated_allowed(s, {"0", "1", "2", "3", "4", "5"}))),
    TSD("log_stop_motion_threshold", TSPD(unit="g-force", suffix="g", validation_mode=TSPDV.RANGE, min_value=0, max_value=1000.0)),
    TSD("log_stop_motion_delay", TSPD(unit="seconds", suffix="s", validation_mode=TSPDV.RANGE, min_value=0, max_value=int(0xFFFFFFFFFFFFFFFF / 1_000_000))),
    TSD("log_stop_count", TSPD(validation_mode=TSPDV.RANGE, min_value=1, max_value=0xFFFFFFFFFFFFFFFF)),
    TSD("log_stop_duration", TSPD(unit="seconds", suffix="s", validation_mode=TSPDV.RANGE, min_value=0, max_value=int(0xFFFFFFFFFFFFFFFF / 1_000_000))),
    TSD("log_stop_period_count", TSPD(validation_mode=TSPDV.RANGE, min_value=1, max_value=0xFFFFFFFF)),
    TSD("log_style", TSPD(validation_mode=TSPDV.ENUM, valid_values={"Continuous": 0, "Periodic": 1})),
    TSD("log_periodic_capture_time", TSPD(unit="seconds", suffix="s", validation_mode=TSPDV.RANGE, min_value=0, max_value=int(0xFFFFFFFFFFFFFFFF / 1_000_000))),
    TSD("log_periodic_rest_time", TSPD(unit="seconds", suffix="s", validation_mode=TSPDV.RANGE, min_value=0, max_value=int(0xFFFFFFFFFFFFFFFF / 1_000_000))),
    TSD("log_file_mode", TSPD(validation_mode=TSPDV.ENUM, valid_values={"Append": 0, "New": 1})),
    TSD("log_data_mode", TSPD(validation_mode=TSPDV.ENUM, valid_values={"Ascii": 1, "Binary": 2})),
    TSD("log_output_settings", TSPD(validation_mode=TSPDV.BOOL)),
    TSD("log_header_enabled", TSPD(validation_mode=TSPDV.BOOL)),
    TSD("log_folder_mode", TSPD(validation_mode=TSPDV.ENUM, valid_values={"Session": 0, "Date Time": 1})),
    TSD("log_immediate_output", TSPD(validation_mode=TSPDV.BOOL)),
    TSD("log_immediate_output_header_enabled", TSPD(validation_mode=TSPDV.BOOL)),
    TSD("log_immediate_output_header_mode", TSPD(validation_mode=TSPDV.ENUM, valid_values={"Match": 0, "Ascii": 1, "Binary": 2})),
    TSD("rtc_year", TSPD(validation_mode=TSPDV.RANGE, min_value=2000, max_value=3000)),
    TSD("rtc_month", TSPD(validation_mode=TSPDV.RANGE, min_value=1, max_value=12)),
    TSD("rtc_day", TSPD(validation_mode=TSPDV.RANGE, min_value=1, max_value=31)),
    TSD("rtc_hour", TSPD(validation_mode=TSPDV.RANGE, min_value=0, max_value=23)),
    TSD("rtc_minute", TSPD(validation_mode=TSPDV.RANGE, min_value=0, max_value=59)),
    TSD("rtc_second", TSPD(validation_mode=TSPDV.RANGE, min_value=0, max_value=59)),
    TSD("rtc_datetime", descriptors=[
        TSPD(validation_mode=TSPDV.RANGE, min_value=2000, max_value=3000), #Year
        TSPD(validation_mode=TSPDV.RANGE, min_value=1, max_value=12), #Month
        TSPD(validation_mode=TSPDV.RANGE, min_value=1, max_value=31), #Day
        TSPD(validation_mode=TSPDV.RANGE, min_value=0, max_value=23), #Hour
        TSPD(validation_mode=TSPDV.RANGE, min_value=0, max_value=59), #Minute
        TSPD(validation_mode=TSPDV.RANGE, min_value=0, max_value=59), #Second
    ]),
    TSD("bat_cold_threshold", descriptors=[
        TSPD(unit="celsius", suffix="C", validation_mode=TSPDV.RANGE, min_value=-273.15, max_value=100), 
        TSPD(validation_mode=TSPDV.RANGE, min_value=0, max_value=1)]),
    TSD("bat_warm_threshold", descriptors=[
        TSPD(unit="celsius", suffix="C", validation_mode=TSPDV.RANGE, min_value=-273.15, max_value=100),
        TSPD(validation_mode=TSPDV.RANGE, min_value=0, max_value=1)]),
    TSD("bat_hot_threshold", descriptors=[
        TSPD(unit="celsius", suffix="C", validation_mode=TSPDV.RANGE, min_value=-273.15, max_value=100),
        TSPD(validation_mode=TSPDV.RANGE, min_value=0, max_value=1)]),
    TSD("bat_offset_threshold", TSPD(unit="celsius", suffix="C", validation_mode=TSPDV.RANGE, min_value=0, max_value=100)),
    TSD("gps_standby", TSPD(validation_mode=TSPDV.BOOL)),
    TSD("gps_led", TSPD(validation_mode=TSPDV.BOOL)),
    TSD("sd_msc_enabled", TSPD(validation_mode=TSPDV.BOOL)),
    TSD("sd_msc_auto", TSPD(validation_mode=TSPDV.BOOL))
]

THREESPACE_SETTINGS_DEFAULT_DESCRIPTORS: dict[str, ThreespaceSettingDescriptor] = { descriptor.key : descriptor for descriptor in THREESPACE_SETTINGS_DEFAULT_DESC_LIST }

#NOTE: FOR NOW, NOT INCLUDING PTS SETTINGS IN DESCRIPTORS (low priority)
#Notes:
#Make sure to ensure log_start_event works
#Calib Mat and Calib Bias are default

#Require custom implementations from the sensor only:
#stream_slots
#log_slots
#Primary accel/gyro/mag will be specific since the components are sensor specific
#range is sensor specific
#odr is sensor specific (Remember baro for ODR)

#I2C addr needs validated when set since they may be unique per sensor (And are not pollable)


