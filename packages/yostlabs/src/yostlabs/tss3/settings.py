import re
from yostlabs.communication.base import ThreespaceInputStream, ThreespaceOutputStream
from yostlabs.tss3.commands import ThreespaceFormat

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