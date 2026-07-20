from dataclasses import dataclass
from enum import IntEnum
from typing import Iterator

from yostlabs.tss3.api import ThreespaceSensor


# ---------------------------------------------------------------------------
# DirItemType / DirItem
# ---------------------------------------------------------------------------

class DirItemType(IntEnum):
    FILE = 0
    DIRECTORY = 1
    END = 128       # Sentinel returned by getNextDirectoryItem at end of listing
    ERROR = 255     # Directory contents changed; recover by calling changeDirectory(".")


@dataclass
class DirItem:
    """Represents a single entry returned by a directory listing."""
    ftype: DirItemType
    name: str
    size: int

    @property
    def is_dir(self) -> bool:
        return self.ftype == DirItemType.DIRECTORY

    @property
    def is_file(self) -> bool:
        return self.ftype == DirItemType.FILE


# ---------------------------------------------------------------------------
# SensorFileExplorer
# ---------------------------------------------------------------------------

class SensorFileExplorer:
    """
    High-level file system interface for a ThreespaceSensor.

    Wraps the low-level file commands (getNextDirectoryItem, changeDirectory,
    openFile, fileStartStream, …) to provide:

    * Iteration over current-directory contents via iter_directory / list_directory.
    * Navigation via change_directory.
    * Unlimited-size file reads via read_file (uses the streaming mechanism
      internally so single-call 4 000-byte cap does not apply).
    * File deletion via delete.
    * Terminal-style string commands (ls, cd, cat, rm) via execute.

    Parameters
    ----------
    sensor : ThreespaceSensor
        An already-connected sensor instance.
    """

    def __init__(self, sensor: ThreespaceSensor):
        self.sensor = sensor

    # ------------------------------------------------------------------
    # Directory iteration
    # ------------------------------------------------------------------

    def iter_directory(self) -> Iterator[DirItem]:
        """
        Iterate over every item in the current directory.

        Yields
        ------
        DirItem
            One entry per file or subdirectory.

        Notes
        -----
        If the sensor reports a directory-changed error (ftype 255) the
        listing is refreshed automatically by issuing ``changeDirectory(".")``
        and restarted once.  A second consecutive error raises RuntimeError.
        """
        refreshed = False
        while True:
            result = self.sensor.getNextDirectoryItem()
            ftype_val, name, size = result.data
            ftype_val = int(ftype_val)

            if ftype_val == DirItemType.ERROR:
                if refreshed:
                    raise RuntimeError(
                        "Directory listing failed after refresh. "
                        "The directory contents may be unstable."
                    )
                self.sensor.changeDirectory(".")
                refreshed = True
                continue

            if ftype_val == DirItemType.END:
                break

            yield DirItem(DirItemType(ftype_val), name, size)

    def list_directory(self) -> list[DirItem]:
        """Return all items in the current directory as a list."""
        return list(self.iter_directory())

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def change_directory(self, path: str) -> None:
        """Change the current directory to *path* (relative to CWD)."""
        self.sensor.changeDirectory(path)

    # ------------------------------------------------------------------
    # File reading
    # ------------------------------------------------------------------

    def read_file(self, path: str) -> bytes:
        """
        Read the entire contents of *path* and return them as bytes.

        There is no limit on file size.  The sensor's file-streaming
        mechanism is used so data is transferred in chunks automatically.
        The file is opened and closed around the operation.

        Parameters
        ----------
        path : str
            Path to the file, relative to the current directory.

        Returns
        -------
        bytes
            Raw file contents.
        """
        self.sensor.openFile(path)
        try:
            self.sensor.fileStartStream()
            while self.sensor.is_file_streaming:
                self.sensor.updateStreaming()
            return bytes(self.sensor.getFileStreamData())
        finally:
            self.sensor.closeFile()

    # ------------------------------------------------------------------
    # Deletion
    # ------------------------------------------------------------------

    def delete(self, path: str) -> None:
        """Delete the file at *path* (relative to CWD)."""
        self.sensor.deleteFile(path)

    # ------------------------------------------------------------------
    # Terminal-style command interface
    # ------------------------------------------------------------------

    def execute(self, command_string: str):
        """
        Parse and execute a terminal-style command string.

        Supported commands
        ------------------
        ls
            List the current directory.  Returns ``list[DirItem]``.
        cd <path>
            Change directory.  Returns ``None``.
        cat <path>
            Read a file and return its contents as ``bytes``.
        rm <path>
            Delete a file.  Returns ``None``.

        Parameters
        ----------
        command_string : str
            The full command string, e.g. ``"cd session-01"`` or ``"ls"``.

        Returns
        -------
        list[DirItem] | bytes | None
        """
        parts = command_string.strip().split(None, 1)
        if not parts:
            return None

        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else None

        if cmd == "ls":
            return self.list_directory()
        elif cmd == "cd":
            if arg is None:
                raise ValueError("cd requires a path argument")
            self.change_directory(arg)
            return None
        elif cmd == "cat":
            if arg is None:
                raise ValueError("cat requires a file argument")
            return self.read_file(arg)
        elif cmd == "rm":
            if arg is None:
                raise ValueError("rm requires a file argument")
            self.delete(arg)
            return None
        else:
            raise ValueError(
                f"Unknown command: {cmd!r}. Supported: ls, cd, cat, rm"
            )

if __name__ == "__main__":
    

# # ---------------------------------------------------------------------------
# # Reference: low-level sensor file commands
# # ---------------------------------------------------------------------------

# sensor = ThreespaceSensor()

# # Ftype can be:
# # 1 - Directory
# # 0 - File
# # 128 - End of directory
# # 255 - Error (Can sometimes be recovered by changing directory)
# #   This is because if you modify the directory using fs_msc_auto the contents may change and need reloaded
# #   And 255 is indicating that a change has occurred and you must refresh the directory contents by changing directory
# #       Changing directory to just '.' does work to refresh the current directory contents
# ftype, name, size = sensor.getNextDirectoryItem()

# # Change the directory to the given path relative to the current directory.
# sensor.changeDirectory("../session-05")

# # Open a file for reading. Path is relative to CWD. Only one file can be open at a time.
# # You can not open another file before closing the current one.
# sensor.openFile("test.txt")

# # Closes the currently open file
# sensor.closeFile()

# # Gets the number of bytes remaining after the cursor in the currently open file
# remaining_size = sensor.fileGetRemainingSize()

# # Reads data after cursor until up to and including the next '\n' character or EOF.
# line_string = sensor.fileReadLine().data

# # Reads the specified number of bytes after the cursor in the currently open file.
# # If the number is greater than the remaining size, any bytes past the end will be
# # filled with 0xff. The cursor will move forward by the number of bytes read.
# # The max number of bytes that can be read is 4000.
# data = sensor.fileReadBytes(200).data

# sensor.deleteFile("test.txt")

# #Sets the cursor to the specified position. 0 is the beggining of the file
# sensor.setCursor(5)

# # Starts file streaming (Outputs the data in chunks as fast as possible)
# sensor.fileStartStream()

# # Forces streaming to stop early (It will stop automatically when entire file is read out)
# sensor.fileStopStream() 

# # Can be used to check state of file streaming
# sensor.is_file_streaming

# # Common pattern for efficent reading
# sensor.fileStartStream()
# while sensor.is_file_streaming:
#     pass
# file_data = sensor.getFileStreamData()


