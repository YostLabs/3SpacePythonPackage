from yostlabs.tss3 import StreamableCommands, ThreespaceHeaderInfo
from yostlabs.tss3.commands import threespace_command_get
from yostlabs.tss3.utils.parser import ThreespaceBinaryParser
from pathlib import Path

DATA_FILE = "data0.bin" #Path to the binary file containing the gathered data, EX: "session-01/data0.bin"
SETTINGS_CFG = "settings.cfg" #Path to settings.cfg file, EX: "session-01/settings.cfg"
COMMAND_SOURCE = "log_slots" #Should be stream_slots or log_slots based on how the data was gathered.

#------------------------Gather configuration for the parser------------------------
header_info = ThreespaceHeaderInfo()
commands = []

if SETTINGS_CFG is None:
    #Set configuration settings manually
    header_info.status_enabled = 0
    header_info.timestamp_enabled = 0
    header_info.echo_enabled = 0
    header_info.checksum_enabled = 0
    header_info.serial_enabled = 0
    header_info.length_enabled = 0

    commands = [threespace_command_get(StreamableCommands.GetTaredOrientation.value), 
                threespace_command_get(StreamableCommands.GetPrimaryCorrectedAccelVec.value)]
else:
    #Load settings from file
    path = Path(SETTINGS_CFG)
    if not path.exists():
        print(f"Error: {SETTINGS_CFG} does not exist. Please provide a valid path to the settings.cfg file.")
        exit(1)
    
    settings = {}
    with path.open('r') as fp:
        for line in fp:
            if line.startswith("#") or line.strip() == "" or "=" not in line:
                continue
            data = line.split("=", 1)
            if len(data) != 2:
                continue
            key, value = data
            settings[key.strip()] = value.strip()
    
    #Find the header and log_slots
    header_info.status_enabled = int(settings["header_status"])
    header_info.timestamp_enabled = int(settings["header_timestamp"])
    header_info.echo_enabled = int(settings["header_echo"])
    header_info.checksum_enabled = int(settings["header_checksum"])
    header_info.serial_enabled = int(settings["header_serial"])
    header_info.length_enabled = int(settings["header_length"])

    command_string: str = settings[COMMAND_SOURCE]
    command_string = command_string.split(",")
    for command in command_string:
        #Some commands have additional info after a ":" that is not needed for parsing, so split and take the first part
        command = command.split(":")[0].strip()
        if command == "" or command == "255":
            continue
        try:
            command_id = int(command)
            commands.append(threespace_command_get(command_id))
        except ValueError:
            print(f"Warning: {command} is not a valid command ID. Skipping.")


#--------------------Create the parser and configure it--------------------
parser = ThreespaceBinaryParser(verbose=True)
parser.set_header(header_info)

#Register all command responses that could have been included. This includes more then streaming data,
#but this example is purely for parsing streaming/logging data, so it must register the getStreamingBatch command.
parser.register_command(84, stream_slots=commands)

#--------------------------------Load Data--------------------------------

#Once all commands are registered, simply feed the parser data and attempt to retrieve messages
with open(DATA_FILE, "rb") as fp:
    parser.insert_data(fp.read())

#--------------------------------Parse Data--------------------------------

print("Parsing the gathered data from the binary file")
msg = parser.parse_message()
while msg is not None:
    print(msg)
    msg = parser.parse_message()
print("Done.")
