import posixpath
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
# SensorFile
# ---------------------------------------------------------------------------

class SensorFile:
    """
    File-like handle for a file opened on the sensor.

    Obtain one via ``SensorFileExplorer.open()``.  Use as a context manager
    so the file is always closed, even if an exception occurs::

        with file_explorer.open("data.bin") as fp:
            header = fp.read(16)
            rest   = fp.read()       # rest of file

    Only one ``SensorFile`` may be open at a time per sensor.
    """

    _MAX_CHUNK = 4000  # hardware limit for a single fileReadBytes call

    def __init__(self, explorer: "SensorFileExplorer"):
        self._explorer = explorer

    def __enter__(self) -> "SensorFile":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def close(self) -> None:
        """Close the file and release the sensor's file handle."""
        if self._explorer._open_file is self:
            self._explorer.sensor.closeFile()
            self._explorer._open_file = None

    def read(self, n: int = -1) -> bytes:
        """
        Read and return up to *n* bytes from the current cursor position.

        Parameters
        ----------
        n : int
            Number of bytes to read.  Pass ``-1`` (default) to read
            everything from the current position to end-of-file.
        """
        sensor = self._explorer.sensor
        if n == 0:
            return b""
        if n == -1:
            # Streaming reads from cursor to EOF with no size limit.
            sensor.fileStartStream()
            while sensor.is_file_streaming:
                sensor.updateStreaming()
            return bytes(sensor.getFileStreamData())
        
        # Partial read: cap at remaining bytes to avoid 0xff padding past EOF.
        remaining = sensor.fileGetRemainingSize().data
        to_read = min(n, remaining)
        data = bytearray()
        while to_read > 0:
            chunk = sensor.fileReadBytes(min(to_read, self._MAX_CHUNK)).data
            data.extend(chunk)
            to_read -= len(chunk)
        return bytes(data)

    def readline(self) -> str:
        """
        Read and return the next line up to and including the newline,
        or an empty string at end-of-file.
        """
        f = open("test.txt", "wb")
        return self._explorer.sensor.fileReadLine().data


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

    An internal absolute path is tracked and the sensor is always navigated
    to that location before any operation.  This guards against the sensor's
    CWD being reset to the root when the OS accesses the drive concurrently.

    Parameters
    ----------
    sensor : ThreespaceSensor
        An already-connected sensor instance.
    """

    def __init__(self, sensor: ThreespaceSensor):
        self.sensor = sensor
        self._cwd: str = "/"
        self._open_file: SensorFile | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _navigate_to_cwd(self) -> None:
        """Navigate the sensor to the tracked absolute path."""
        self.sensor.changeDirectory(self._cwd)

    @property
    def cwd(self) -> str:
        """The current tracked absolute directory path."""
        return self._cwd

    # ------------------------------------------------------------------
    # Directory iteration
    # ------------------------------------------------------------------

    def iter_directory(self) -> Iterator[DirItem]:
        """
        Iterate over every item in the current directory.

        Navigates to the tracked absolute path before listing so that an
        OS-triggered CWD reset does not produce stale results.

        Yields
        ------
        DirItem
            One entry per file or subdirectory.
        """
        self._navigate_to_cwd()
        while True:
            result = self.sensor.getNextDirectoryItem()
            ftype_val, name, size = result.data
            ftype_val = int(ftype_val)

            if ftype_val == DirItemType.ERROR:
                raise RuntimeError("Directory listing failed")

            if ftype_val == DirItemType.END:
                break

            yield DirItem(DirItemType(ftype_val), name, size)

    def list_directory(self) -> list[DirItem]:
        """Return all items in the current directory as a list."""
        return list(self.iter_directory())

    def __iter__(self) -> Iterator[DirItem]:
        """Iterate over the current directory; delegates to iter_directory()."""
        return self.iter_directory()

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def change_directory(self, path: str) -> None:
        """
        Change the tracked directory to *path*.

        *path* may be relative (e.g. ``"session-01"``, ``"../other"``) or
        absolute (e.g. ``"/CONFIG"``).  It is resolved against the current
        tracked path and the result is sent to the sensor as an absolute
        path, updating the internal tracker on success.
        """
        new_cwd = posixpath.normpath(posixpath.join(self._cwd, path))
        self.sensor.changeDirectory(new_cwd)
        self._cwd = new_cwd

    # ------------------------------------------------------------------
    # File access
    # ------------------------------------------------------------------

    def open(self, path: str) -> SensorFile:
        """
        Open *path* for reading and return a :class:`SensorFile` handle.

        Use as a context manager to ensure the file is always closed::

            with file_explorer.open("log.bin") as fp:
                data = fp.read()

        Only one file may be open at a time; opening a second raises
        ``IOError``.

        Parameters
        ----------
        path : str
            Path to the file, relative to the tracked directory.
        """
        if self._open_file is not None:
            raise IOError(
                "A file is already open. Close it before opening another."
            )
        self._navigate_to_cwd()
        self.sensor.openFile(path)
        self._open_file = SensorFile(self)
        return self._open_file

    # ------------------------------------------------------------------
    # Deletion
    # ------------------------------------------------------------------

    def delete(self, path: str) -> None:
        """Delete the file at *path* (relative to the tracked directory)."""
        self._navigate_to_cwd()
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
            with self.open(arg) as fp:
                return fp.read()
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
    sensor = ThreespaceSensor()

    fp = open("test.txt", "wb")

    file_explorer = SensorFileExplorer(sensor)
    file_explorer.change_directory("CONFIG")
    for file in file_explorer:
        print(file)

    import pathlib
    import os
    file_path = pathlib.Path(__file__).parent / "file_explorer.py"
    with open(file_path, "rb") as fp:
        print(fp.tell())
        print(fp.read(10))
        print(fp.tell())
        fp.seek(-10, os.SEEK_END)
        print(fp.tell())
    with file_explorer.open("sensor.cfg") as fp:
        #print(fp.read().decode())
        pass
    # print(file_explorer.list_directory())
    # print(file_explorer.read_file("sensor.cfg").decode())

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


