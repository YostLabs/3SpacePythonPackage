"""
Example script for parsing log data from a file/folder. This can
be used to parse both ascii (.csv) and binary (.bin) log files. This
includes files logged directly from a data logger, or logged via streaming
with the TSS-3 Suite.

This is a more high level version of the example_parsing_binary examples,
and also works on all types.
"""

from yostlabs.tss3.utils.parser import ThreespaceDataFileParser

#--------------------Automatically find log files and settings.cfg--------------------
#Searches provided folder and its subfolders for data files and a config file.
parser = ThreespaceDataFileParser(folder_path="session-01")

#--------------------Manually specify the file locations for data and configuration.--------------------
#parser = ThreespaceDataFileParser(data_paths=["data0.bin"], cfg_path="settings.cfg")

#------------------------------Manually specify required settings------------------------------
# from yostlabs.tss3.utils.parser import ThreespaceHeaderInfo, StreamableCommands
# parser = ThreespaceDataFileParser(data_paths=["data0.bin"])
# header_info = ThreespaceHeaderInfo()
# header_info.checksum_enabled = True
# header_info.echo_enabled = True
# header_info.length_enabled = True
# #...
# data = [StreamableCommands.GetTimestamp, StreamableCommands.GetTaredOrientation, StreamableCommands.GetPrimaryCorrectedAccelVec]
# parser.setup_manual(header_info, data)

#The parser will retrieve one ThreespaceCmdResult at a time until
#the end of the data is reached.
msg = parser.parse_message()
while msg is not None:
    print(msg)
    msg = parser.parse_message()
