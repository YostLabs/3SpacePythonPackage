from yostlabs.tss3.api import ThreespaceSensor

sensor = ThreespaceSensor()

# Ftype can be:
# 1 - Directory
# 0 - File
# 128 - End of directory
# 255 - Error (Can sometimes be recovered by changing directory)
#   This is because if you modify the directory using fs_msc_auto the contents may change and need reloaded
#   And 255 is indicating that a change has occurred and you must refresh the directory contents by changing directory
#       Changing directory to just '.' does work to refresh the current directory contents
ftype, name, size = sensor.getNextDirectoryItem()

# Change the directory to the given path relative to the current directory.
sensor.changeDirectory("../session-05")

# Open a file for reading. Path is relative to CWD. Only one file can be open at a time.
# You can not open another file before closing the current one.
sensor.openFile("test.txt")

# Closes the currently open file
sensor.closeFile()

# Gets the number of bytes remaining after the cursor in the currently open file
remaining_size = sensor.fileGetRemainingSize()

# Reads data after cursor until up to and including the next '\n' character or EOF.
line_string = sensor.fileReadLine().data

# Reads the specified number of bytes after the cursor in the currently open file.
# If the number is greater than the remaining size, any bytes past the end will be
# filled with 0xff. The cursor will move forward by the number of bytes read.
# The max number of bytes that can be read is 4000.
data = sensor.fileReadBytes(200).data

sensor.deleteFile("test.txt")

#Sets the cursor to the specified position. 0 is the beggining of the file
sensor.setCursor(5)

# Starts file streaming (Outputs the data in chunks as fast as possible)
sensor.fileStartStream()

# Forces streaming to stop early (It will stop automatically when entire file is read out)
sensor.fileStopStream() 

# Can be used to check state of file streaming
sensor.is_file_streaming

# Common pattern for efficent reading
sensor.fileStartStream()
while sensor.is_file_streaming:
    pass
file_data = sensor.getFileStreamData()


