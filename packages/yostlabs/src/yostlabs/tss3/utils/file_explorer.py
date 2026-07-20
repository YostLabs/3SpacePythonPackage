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
    absolute_path: str

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

    def __init__(self, explorer: "SensorFileExplorer", file_size: int, name: str):
        self._explorer = explorer
        self.size = file_size
        self.name = name

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

    def eof(self) -> bool:
        """Return ``True`` if the cursor is at end-of-file."""
        return self._explorer.sensor.fileGetRemainingSize().data == 0

    def tell(self) -> int:
        """Return the current cursor position (bytes from the start of the file)."""
        remaining = self._explorer.sensor.fileGetRemainingSize().data
        return self.size - remaining

    def seek(self, pos: int, whence: int = 0) -> int:
        """
        Move the cursor and return the new absolute position.

        Parameters
        ----------
        pos : int
            Byte offset used together with *whence*.
        whence : int
            ``0`` — absolute position (default)
            ``1`` — relative to the current position
            ``2`` — relative to the end of the file
        """
        if whence == 0:
            target = pos
        elif whence == 1:
            target = self.tell() + pos
        elif whence == 2:
            target = self.size + pos
        else:
            raise ValueError(f"Invalid whence value: {whence!r}. Expected 0, 1, or 2.")
        target = max(0, min(target, self.size))
        self._explorer.sensor.setCursor(target)
        return target

    def readline(self) -> str:
        """
        Read and return the next line up to and including the newline,
        or an empty string at end-of-file.
        """
        return self._explorer.sensor.fileReadLine().data

    def __iter__(self) -> Iterator[str]:
        """
        Iterate over the file line by line.

        Allows ``for line in fp:`` just like Python's built-in ``open()``.
        Each yielded string includes the trailing newline (if present).
        Iteration stops at end-of-file.
        """
        line = self.readline()
        while line is not None:
            yield line
            line = self.readline()


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
    * Unlimited-size file reads.
    * File deletion via delete.
    * Terminal-style string commands (ls, cd, cat, rm, ...) via execute.

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

        # Ensure no file is left open from previous operations
        self.sensor.closeFile()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _navigate_to_cwd(self) -> None:
        """Navigate the sensor to the tracked absolute path."""
        self.sensor.changeDirectory(self._cwd)

    def resolve_path(self, path: str) -> str:
        """Resolve *path* to an absolute path using the tracked directory."""
        return posixpath.normpath(posixpath.join(self._cwd, path))

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

            yield DirItem(DirItemType(ftype_val), name, size, posixpath.join(self._cwd, name))

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
        new_cwd = self.resolve_path(path)
        self.sensor.changeDirectory(new_cwd)
        self._cwd = new_cwd

    # ------------------------------------------------------------------
    # File access
    # ------------------------------------------------------------------

    def open(self, path: str | DirItem) -> SensorFile:
        """
        Open *path* for reading and return a :class:`SensorFile` handle.

        Use as a context manager to ensure the file is always closed::

            with file_explorer.open("log.bin") as fp:
                data = fp.read()

        *path* may be a string (relative to the tracked directory) or a
        :class:`DirItem` obtained from a directory listing, in which case
        its stored absolute path is used directly.  Passing a ``DirItem``
        that is not a file raises ``ValueError``.

        Only one file may be open at a time; opening a second raises
        ``IOError``.
        """
        if self._open_file is not None:
            raise IOError(
                "A file is already open. Close it before opening another."
            )
        if isinstance(path, DirItem):
            if not path.is_file:
                raise ValueError(
                    f"{path.name!r} is not a file (ftype={path.ftype.name})."
                )
            abs_path = path.absolute_path
            filename = path.name
        else:
            abs_path = self.resolve_path(path)
            filename = posixpath.basename(abs_path)
        self.sensor.openFile(abs_path)
        file_size = self.sensor.fileGetRemainingSize().data
        self._open_file = SensorFile(self, file_size, filename)
        return self._open_file

    # ------------------------------------------------------------------
    # Recursive traversal
    # ------------------------------------------------------------------

    def walk(
        self,
        top: str = ".",
        *,
        topdown: bool = True,
    ) -> Iterator[tuple[str, list[DirItem], list[DirItem]]]:
        """
        Walk the directory tree rooted at *top*, mirroring :func:`os.walk`.

        Yields ``(dirpath, subdirs, files)`` for each directory visited,
        where *dirpath* is the absolute path of the directory, *subdirs*
        is a list of :class:`DirItem` objects for its sub-directories, and
        *files* is a list of :class:`DirItem` objects for its files.

        Parameters
        ----------
        top : str
            Starting directory (default: current directory).
        topdown : bool
            If ``True`` (default) each directory is yielded before its
            children.  If ``False`` children are yielded first.
        """
        saved_cwd = self._cwd
        try:
            self.change_directory(top)
            root = self._cwd
            items = self.list_directory()
            subdirs = [i for i in items if i.is_dir]
            files   = [i for i in items if i.is_file]

            if topdown:
                yield root, subdirs, files

            for subdir in subdirs:
                yield from self.walk(subdir.absolute_path, topdown=topdown)

            if not topdown:
                yield root, subdirs, files
        finally:
            # Always restore the original working directory.
            self.change_directory(saved_cwd)

    # ------------------------------------------------------------------
    # Path queries
    # ------------------------------------------------------------------

    def exists(self, path: str) -> bool:
        """
        Return ``True`` if *path* exists on the sensor's file system.

        The check is performed by listing the parent directory and scanning
        for an entry whose name matches the final component of *path*.
        """
        abs_path = self.resolve_path(path)
        if abs_path == "/":
            return True  # root always exists
        parent = posixpath.dirname(abs_path)
        name   = posixpath.basename(abs_path)
        saved_cwd = self._cwd
        try:
            self.change_directory(parent)
            return any(item.name == name for item in self.iter_directory())
        except Exception:
            return False
        finally:
            self.change_directory(saved_cwd)

    # ------------------------------------------------------------------
    # Deletion
    # ------------------------------------------------------------------

    def delete(self, path: str | DirItem) -> None:
        """
        Delete the file at *path*.

        *path* may be a string (relative to the tracked directory) or a
        :class:`DirItem` obtained from a directory listing, in which case
        its stored absolute path is used directly.
        """
        if isinstance(path, DirItem):
            self.sensor.deleteFile(path.absolute_path)
        else:
            self.sensor.deleteFile(self.resolve_path(path))

    # ------------------------------------------------------------------
    # Terminal-style command interface
    # ------------------------------------------------------------------

    def execute(self, command_string: str):
        """
        Parse and execute a terminal-style command string.

        Supported commands
        ------------------
        ls or dir
            List the current directory.  Returns ``list[DirItem]``.
        cd <path>
            Change directory.  Returns ``None``.
        cat or type <path>
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

        if cmd == "ls" or cmd == "dir":
            return self.list_directory()
        elif cmd == "cd":
            if arg is None:
                raise ValueError("cd requires a path argument")
            self.change_directory(arg)
            return None
        elif cmd == "cat" or cmd == "type":
            if arg is None:
                raise ValueError(f"{cmd} requires a file argument")
            with self.open(arg) as fp:
                return fp.read()
        elif cmd == "rm":
            if arg is None:
                raise ValueError("rm requires a file argument")
            self.delete(arg)
            return None
        else:
            raise ValueError(
                f"Unknown command: {cmd!r}. Supported: ls / dir, cd, cat / type, rm"
            )
    
    def execute_verbose(self, command_string: str):
        """
        Execute a command and print the result to stdout.

        This is a convenience wrapper around :meth:`execute` for interactive
        use.  It prints directory listings in a table format and decodes
        file contents as UTF-8 (with replacement for invalid bytes).
        """
        result = self.execute(command_string)
        if isinstance(result, list):
            for item in result:
                print(f"{item.ftype.name:10} {item.size:10} {item.name}")
        elif isinstance(result, bytes):
            try:
                print(result.decode())
            except:
                print(result)

def run_cmd_line():
    sensor = ThreespaceSensor()
    file_explorer = SensorFileExplorer(sensor)

    try:
        while True:
            command = input(f"{file_explorer.cwd}> ")
            file_explorer.execute_verbose(command)
    except KeyboardInterrupt:
        print("\nExiting...")

def test_file_explorer():
    sensor = ThreespaceSensor()
    file_explorer = SensorFileExplorer(sensor)

    file_explorer.change_directory("config")
    for file in file_explorer:
        print(file)
    print(file_explorer.list_directory())

    with file_explorer.open("sensor.cfg") as fp:
        print(fp.read())
        # for line in fp:
        #     print(line)

def iterate_files():
    sensor = ThreespaceSensor()
    file_explorer = SensorFileExplorer(sensor)
    for root, subdirs, files in file_explorer.walk("/"):
        print(root)
        for subdir in subdirs:
            print("  ", subdir.name)
        for file in files:
            print("  ", file.name)

if __name__ == "__main__":
    test_file_explorer()
        
