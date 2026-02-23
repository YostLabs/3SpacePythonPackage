from dataclasses import dataclass, field, fields
from typing import ClassVar

YL_SENSOR_BIT_GYRO: int = 1
YL_SENSOR_BIT_ACCEL: int = 2
YL_SENSOR_BIT_MAG: int = 4
YL_SENSOR_BIT_BARO: int = 8
YL_SENSOR_BIT_TEMP: int = 16
YL_SENSOR_BIT_GPS: int = 32

@dataclass
class YL_EEPTS_INPUT_DATA:
    gyro_data: list[float] = None  # XYZ in radians/sec. only needs to be float32
    accel_data: list[float] = None # XYZ in G force units(9.8 m/s^2). only needs to be float32
    mag_data: list[float] = None  # XYZ in Gauss. only needs to be float32
    orient_data: list[float] = None # XYZW Optional, may not be here if YL_BUILTIN_ORIENT is false
    barometer_data: float = 0  # in hPa. only needs to be float32
    temperature: float = 0  # in degrees C. only needs to be float32
    gps_longitude: float = 0  # in decimal degrees (positive east, negative west). needs to be float64
    gps_latitude: float = 0  # in decimal degrees (positive north, negative south). needs to be float64
    gps_altitude: float = 0  # in meters. only needs to be float32
    timestamp: int = 0 # in microseconds. uint32_t

#Locomotion Modes
YL_LOCOMOTION_IDLE: int = 0
YL_LOCOMOTION_WALKING: int = 1
YL_LOCOMOTION_JOGGING: int = 2
YL_LOCOMOTION_RUNNING: int = 3
YL_LOCOMOTION_CRAWLING: int = 4
YL_LOCOMOTION_OTHER: int = 6
YL_LOCOMOTION_UNKNOWN: int = 5

#Sensor Locations
YL_SENSOR_RSHOULDER: int = 5
YL_SENSOR_BACK: int = 3
YL_SENSOR_RHAND: int = 4
YL_SENSOR_WAIST: int = 2
YL_SENSOR_CHEST: int = 1
YL_SENSOR_UNKNOWN: int = 0

LocomotionModes = {
    YL_LOCOMOTION_IDLE: "Idle",
    YL_LOCOMOTION_WALKING: "Walking",
    YL_LOCOMOTION_JOGGING: "Jogging",
    YL_LOCOMOTION_RUNNING: "Running",
    YL_LOCOMOTION_CRAWLING: "Crawling",
    YL_LOCOMOTION_OTHER: "Other",
    YL_LOCOMOTION_UNKNOWN: "Unknown"
}

SensorLocations = {
    YL_SENSOR_RSHOULDER: "RShoulder",
    YL_SENSOR_BACK: "Back",
    YL_SENSOR_RHAND: "RHand",
    YL_SENSOR_WAIST: "Waist",
    YL_SENSOR_CHEST: "Chest",
    YL_SENSOR_UNKNOWN: "Unknown"
}

PresetMotionWR = 0
PresetMotionWRC = 1

PresetHandDisabled = 0
PresetHandEnabled = 1
PresetHandOnly = 2

PresetHeadingDynamic = 0
PresetHeadingStatic = 1

@dataclass
class YL_EEPTS_OUTPUT_DATA:

    segment_count: int = 0                  #number of segments (a segment refers to a single step action)
    timestamp: int = 0                      #in microseconds, set to end of last estimated segment
    estimated_gps_longitude: float = 0      #DDMM.MMMM (positive east, negative west)
    estimated_gps_latitude: float = 0       #DDMM.MMMM (positive north, negative south)
    estimated_gps_altitude: float = 0       #in meters
    estimated_heading_angle: float = 0      #in degrees (0 north, 90 east, 180 south, 270 west)
    estimated_distance_travelled: float = 0 #in meters, since last reset (A reset is a start command)
    estimated_distance_x: float = 0         #in meters since last update, along positive east/negative west (An update is a read)
    estimated_distance_y: float = 0         #in meters, since last update, along positive north/negative south
    estimated_distance_z: float = 0         #in meters, since last update, along positive up/negative down
    estimated_locomotion_mode: int = 0      #0=idle, 1=walking, etc...
    estimated_receiver_location: int = 0    #0=unknown, 1=chest, etc...
    last_event_confidence: float = 1
    overall_confidence: float = 1

    LOCMOTION_DICT: ClassVar[dict[int, str]] = { YL_LOCOMOTION_IDLE : "Idle", YL_LOCOMOTION_WALKING : "Walking", YL_LOCOMOTION_JOGGING : "Jogging", YL_LOCOMOTION_RUNNING : "Running", YL_LOCOMOTION_CRAWLING: "Crawling", YL_LOCOMOTION_OTHER: "Other", YL_LOCOMOTION_UNKNOWN: "Unknown" } 
    LOCATION_DICT: ClassVar[dict[int, str]] = { YL_SENSOR_UNKNOWN : "Unknown", YL_SENSOR_CHEST : "Chest", YL_SENSOR_WAIST : "Waist", YL_SENSOR_BACK : "Back", YL_SENSOR_RHAND : "RHand", YL_SENSOR_RSHOULDER : "RShoulder" }

    def clone(self, other: "YL_EEPTS_OUTPUT_DATA"):
        self.segment_count = other.segment_count
        self.timestamp = other.timestamp
        self.estimated_gps_longitude = other.estimated_gps_longitude
        self.estimated_gps_latitude = other.estimated_gps_latitude
        self.estimated_gps_altitude = other.estimated_gps_altitude
        self.estimated_heading_angle = other.estimated_heading_angle
        self.estimated_distance_travelled = other.estimated_distance_travelled
        self.estimated_distance_x = other.estimated_distance_x
        self.estimated_distance_y = other.estimated_distance_y
        self.estimated_distance_z = other.estimated_distance_z
        self.estimated_locomotion_mode = other.estimated_locomotion_mode
        self.estimated_receiver_location = other.estimated_receiver_location
        self.last_event_confidence = other.last_event_confidence
        self.overall_confidence = other.overall_confidence

    def get_locomotion_string(self):
        return YL_EEPTS_OUTPUT_DATA.LOCMOTION_DICT[self.estimated_locomotion_mode]
    
    def get_location_string(self):
        return YL_EEPTS_OUTPUT_DATA.LOCATION_DICT[self.estimated_receiver_location]

    def __str__(self):
        return f"{self.segment_count},{self.timestamp}," \
               f"{self.estimated_gps_longitude},{self.estimated_gps_latitude}," \
               f"{self.estimated_gps_altitude},{self.estimated_heading_angle}," \
               f"{self.estimated_distance_travelled},{self.estimated_distance_x}," \
               f"{self.estimated_distance_y},{self.estimated_distance_z}," \
               f"{self.estimated_locomotion_mode},{self.estimated_receiver_location}"
    
    def print_fancy(self):
        print("Segment Count:", self.segment_count)
        print("Timestamp:", self.timestamp)
        print("Longitude:", self.estimated_gps_longitude)
        print("Latitude:", self.estimated_gps_latitude)
        print("Altitude:", self.estimated_gps_altitude)
        print("Heading:", self.estimated_heading_angle)
        print("Distance Travelled:", self.estimated_distance_travelled)
        print("Delta_X:", self.estimated_distance_x)
        print("Delta_Y:", self.estimated_distance_y)
        print("Delta_Z:", self.estimated_distance_z)
        print("Locomotion:", self.get_locomotion_string())
        print("Sensor Location:", self.get_location_string())
        print("Confidence:", self.last_event_confidence)
        print("Overall Confidence:", self.overall_confidence)

@dataclass
class SensorData:
    accel_x : float
    accel_y : float
    accel_z : float

    gyro_x : float
    gyro_y : float
    gyro_z : float

    mag_x : float
    mag_y : float
    mag_z : float

    accel : list[float] = field(init=False, default_factory=list)
    gyro : list[float] = field(init=False, default_factory=list)
    mag : list[float] = field(init=False, default_factory=list)

    def __post_init__(self):
        self.accel = [self.accel_x, self.accel_y, self.accel_z]
        self.gyro = [self.gyro_x, self.gyro_y, self.gyro_z]
        self.mag = [self.mag_x, self.mag_y, self.mag_z]
    
    def __str__(self):
        return f"{self.accel_x},{self.accel_y},{self.accel_z}," \
               f"{self.gyro_x},{self.gyro_y}, {self.gyro_z}," \
               f"{self.mag_x},{self.mag_y},{self.mag_z}"

    def print_fancy(self):
        print("Accel:", self.accel)
        print("Gyro:", self.gyro)
        print("Mag:", self.mag)

@dataclass
class DebugMessage:
    level: int = 0
    module: int = 0
    msg: str = ""

    def get_display_str(self):
        return f"Level: {self.level} Module: {self.module}    {self.msg}"

@dataclass
class Segment:
    #Output Structure
    segment_count: int = 0
    timestamp: int = 0
    estimated_gps_longitude: float = 0
    estimated_gps_latitude: float = 0
    estimated_gps_altitude: float = 0
    estimated_heading_angle: float = 0
    estimated_distance_travelled: float = 0
    estimated_distance_x: float = 0
    estimated_distance_y: float = 0
    estimated_distance_z: float = 0
    estimated_locomotion_mode: int = 0
    estimated_receiver_location: int = 0
    last_event_confidence: float = 0
    overall_confidence: float = 0

    #Segment Info Structure
    start_global_index: int = 0
    end_global_index: int = 0
    f_start_index_offset: float = 0
    f_end_index_offset: float = 0
    len: int = 0
    f_len: float = 0
    dir: int = 0

    #Debug Messages:
    debug_msgs: list[DebugMessage] = field(default_factory=lambda: [])
    
    @classmethod
    def from_only_output_obj(cls, output: YL_EEPTS_OUTPUT_DATA):
        new_obj = cls()
        output_fields = [f for f in dir(output) if not f.startswith('__') and not callable(getattr(output, f))]
        for field in output_fields: #Can NOT just assign. That will cause unintentional problems if ever saving segment info
            setattr(new_obj, field, getattr(output, field))

        return new_obj
    
    def __str__(self):
        s = ""
        for field in fields(self):
            attr = getattr(self, field.name)
            if field.name == "estimated_locomotion_mode":
                s += f"{field.name}: {LocomotionModes[attr]}\n"
            elif field.name == "estimated_receiver_location":
                s += f"{field.name}: {SensorLocations[attr]}\n"
            else:
                s += f"{field.name}: {attr}\n"
        return s