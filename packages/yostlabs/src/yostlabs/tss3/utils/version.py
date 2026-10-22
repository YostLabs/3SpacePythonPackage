"""
Firmware files: reading what they are for, picking the firmware for a sensor, and uploading it (ThreespaceFirmwareUploader).

A firmware file is XML. The root's children before the first programming instruction are the header:

<?xml version="1.0"?>
<Firmware>                                          Any root name, but folders only find <Firmware> files (older ones use <root>)
  <Version>Sep 30 2026 20:02:51</Version>           Exactly the version_firmware the sensor reports for this build
  <Edition>compatibility</Edition>                  Optional: standard when missing. Any case
  <Hash algorithm="sha256">3f9a...</Hash>           Optional: of the decoded MemProgC bytes, all of them in order
  <Compatibility>                                   Optional, but files without it are never auto picked for a sensor
    <Hardware family="16" variation="000">          Hex as in the serial number
      <MinBoard core="3" major="0" minor="00"/>     Never for an older board
      <TargetBoard core="3" major="1" minor="a0"/>  The board it was built for
      <ExcludedBoard core="3" major="0" minor="c0"/>    Never for this board; any number of these
    </Hardware>
  </Compatibility>
  <SetAddr/> <MemProgC>hex</MemProgC> <Finish/> <Run/>    Erase, program, end of the file (no action), boot
</Firmware>

The instructions follow the header. They must stay the root's children: older uploaders only look there.
"""
import hashlib
import os
import re
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Iterable

from yostlabs.tss3.api import ThreespaceSensor
from yostlabs.tss3.errors import FirmwareFileError, FirmwareUploadError
from yostlabs.tss3.types import ThreespaceBoardRevision, ThreespaceHardwareVersion

STANDARD_EDITION = "standard"

_ROOT = "Firmware"   # What a folder's firmware files are recognized by
_INSTRUCTIONS = ("SetAddr", "MemProgC", "Finish", "Run")
_HEADER = ("Version", "Edition", "Hash", "Compatibility")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

class ThreespaceFirmwareCompatibility(Enum):
    """How firmware fits a sensor's hardware"""
    UNKNOWN = "unknown"             # The file says nothing about its hardware (made before the Compatibility section)
    INCOMPATIBLE = "incompatible"   # Not for the sensor's family or variation, below the minimum board, or an excluded board
    OLDER_TARGET = "older_target"   # Built for an older board than the sensor's: likely fine, but deserves a warning
    TARGETED = "targeted"           # Built for the sensor's board or a newer one

@dataclass(frozen=True)
class ThreespaceFirmwareHardware:
    """A <Hardware> entry: the boards of one family and variation the firmware is for"""
    family_id: int
    variation: int
    min_board: ThreespaceBoardRevision
    target_board: ThreespaceBoardRevision
    excluded_boards: tuple[ThreespaceBoardRevision, ...] = ()

    def fit(self, hardware: ThreespaceHardwareVersion) -> ThreespaceFirmwareCompatibility:
        if (hardware.family_id, hardware.variation) != (self.family_id, self.variation):
            return ThreespaceFirmwareCompatibility.INCOMPATIBLE
        board = hardware.board_revision
        if board < self.min_board or board in self.excluded_boards:
            return ThreespaceFirmwareCompatibility.INCOMPATIBLE
        if board > self.target_board:
            return ThreespaceFirmwareCompatibility.OLDER_TARGET
        return ThreespaceFirmwareCompatibility.TARGETED

@dataclass(frozen=True)
class ThreespaceFirmwareInfo:
    """What a firmware file's header says about it"""
    path: str
    version: str | None             # With its whitespace collapsed. None: older files without one
    build_time: datetime | None     # From the version. None: older files whose version isn't the sensor's format
    edition: str                    # Lowercase, so editions match in any case
    hash: str | None                # Lowercase hex. None: older files without one
    hash_algorithm: str | None
    compatible_hardware: tuple[ThreespaceFirmwareHardware, ...] | None   # None: no Compatibility section

    def fit(self, hardware: ThreespaceHardwareVersion) -> ThreespaceFirmwareCompatibility:
        """How this firmware fits a sensor's hardware"""
        if self.compatible_hardware is None:
            return ThreespaceFirmwareCompatibility.UNKNOWN
        # At most one entry is for the sensor's family and variation (parsing refuses duplicates):
        # every other one says INCOMPATIBLE
        for entry in self.compatible_hardware:
            fit = entry.fit(hardware)
            if fit != ThreespaceFirmwareCompatibility.INCOMPATIBLE:
                return fit
        return ThreespaceFirmwareCompatibility.INCOMPATIBLE

    def is_version(self, sensor_version: str) -> bool:
        """Whether this is the firmware a sensor reports as its version_firmware"""
        return self.version is not None and self.version == _collapse(sensor_version)

@dataclass(frozen=True)
class ThreespaceFirmwareFile:
    """A whole firmware file: its header, and its programming instructions in order"""
    info: ThreespaceFirmwareInfo
    instructions: tuple[tuple[str, bytes | None], ...]   # (name, the decoded data for MemProgC, else None)

    @property
    def program_size(self) -> int:
        return sum(len(data) for name, data in self.instructions if name == "MemProgC")

    def compute_hash(self, algorithm: str = "sha256") -> str:
        """The hash of the programming data, as the header's <Hash> holds it"""
        hasher = hashlib.new(algorithm)
        for name, data in self.instructions:
            if name == "MemProgC":
                hasher.update(data)
        return hasher.hexdigest()

# ---- Reading files -----------------------------------------------------------
# ElementTree (ET) basics, for what's used here. An ET.Element is one tag:
#   element.tag             its name, Ex: "Hardware"
#   element.get("family")   an attribute's text, or None when the tag doesn't have it
#   element.text            the text directly inside it, or None when there is none (Ex: <SetAddr/>)
#   for child in element    its child elements, in file order (text and comments aren't children)
#   element.findall("Hardware")   its child elements with that tag, in file order
# The <?xml ...?> declaration isn't an element: the root is the one element holding everything else (<Firmware>).
# Malformed XML raises ET.ParseError, turned into FirmwareFileError.
# Elements and attributes not used here are ignored, so a file with later additions still reads.
# Errors name the file: the two readers below add its path to the FirmwareFileErrors raised inside them
# (_prefix_errors_with), so the parsing helpers needn't take it.

def read_firmware_info(path: str) -> ThreespaceFirmwareInfo:
    """
    Gathers all elements of a firmware file up to where its instructions start.
    Returns:
    ThreespaceFirmwareInfo: The parsed firmware information from the header elements.
    """
    with _prefix_errors_with(path):
        _, header = _read_header(path)
        return _parse_header(path, header)

def _read_header(path: str) -> tuple[str, list[ET.Element]]:
    """The root's tag, and the root's children before the first instruction (each complete)"""
    # iterparse reads the file as a stream, reporting each tag as it gets to it instead of building the whole tree first:
    #   "start": its opening tag was read. Its name and attributes are there, its text and children may not be yet.
    #   "end":   its closing tag was read, so it's complete, children included.
    # depth counts the tags open: the root's start makes it 1, so the root's children start at depth 2, and are
    # complete when their end brings it back to 1. Stopping at the first instruction's start means the large
    # programming data is never parsed (the file is read in chunks, so a little past it is read from disk).
    root_tag = None
    header = []
    depth = 0
    with open(path, "rb") as source:
        try:
            for event, element in ET.iterparse(source, events=("start", "end")):
                if event == "start":
                    depth += 1
                    if depth == 1:
                        root_tag = element.tag
                    # Stop parsing when the first instruction is encountered
                    elif depth == 2 and element.tag in _INSTRUCTIONS:
                        break
                else:
                    depth -= 1
                    if depth == 1:
                        header.append(element)
        except ET.ParseError as error:
            raise FirmwareFileError(str(error)) from error
    return root_tag, header

def load_firmware_file(path: str) -> ThreespaceFirmwareFile:
    """A whole firmware file. Raises FirmwareFileError when it's malformed, or its programming data doesn't match its hash"""
    with _prefix_errors_with(path):
        # parse reads the whole file into a tree of elements; getroot is its root element
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as error:
            raise FirmwareFileError(str(error)) from error
        # Split the root's children where the instructions start, as read_firmware_info does: the header is what comes before
        children = list(root)
        start = next((index for index, child in enumerate(children) if child.tag in _INSTRUCTIONS), len(children))
        info = _parse_header(path, children[:start])
        # A header element after the instructions would be missed by read_firmware_info: Ex, a late <Compatibility> would
        # leave the file looking like an old one, uploading to anything
        misplaced = [child.tag for child in children[start:] if child.tag in _HEADER]
        if misplaced:
            raise FirmwareFileError(f"<{misplaced[0]}> must come before the instructions")
        # Anything else among the instructions is skipped, as older uploaders do
        instructions = tuple((child.tag, _program_data(child) if child.tag == "MemProgC" else None)
                             for child in children[start:] if child.tag in _INSTRUCTIONS)
        # Without it, uploading would erase the firmware and put nothing in its place
        if not any(name == "MemProgC" for name, _ in instructions):
            raise FirmwareFileError("no <MemProgC>: nothing to program")
        firmware = ThreespaceFirmwareFile(info, instructions)
        if info.hash is not None and firmware.compute_hash(info.hash_algorithm) != info.hash:
            raise FirmwareFileError("the programming data doesn't match the file's hash (corrupted or edited)")
        return firmware

def read_firmware_folder(folder: str, on_error: Callable[[str, Exception], None] = None) -> list[ThreespaceFirmwareInfo]:
    """
    The headers of the firmware files directly in a folder: the *.xml files whose root is <Firmware>. Others are left out,
    including older firmware files (their root is <root>), which can still be read and uploaded by their path.
    A file that can't be read raises, or when on_error is given, is passed to it with the error and left out.
    The error's message already starts with the file's path (an OSError's names it too), so show it on its own.
    Reads only the headers: a file listed can still fail to load for uploading (see verify in compatible_firmware()).
    """
    firmware = []
    for path in sorted(entry.path for entry in os.scandir(folder) if entry.is_file() and entry.name.lower().endswith(".xml")):
        try:
            with _prefix_errors_with(path):
                root_tag, header = _read_header(path)
                if root_tag == _ROOT:
                    firmware.append(_parse_header(path, header))
        except (FirmwareFileError, OSError) as error:
            if on_error is None:
                raise
            on_error(path, error)
    return firmware

# ---- Picking firmware --------------------------------------------------------

def compatible_firmware(firmware: Iterable[ThreespaceFirmwareInfo], hardware: ThreespaceHardwareVersion,
                        edition: str | None = STANDARD_EDITION, verify: bool = False) -> list[ThreespaceFirmwareInfo]:
    """
    The firmware for a sensor's hardware, in the order it would be picked:
    the files built for its board or a newer one, newest first, then the files built for an older board, newest first.
    edition: only that edition, or any when None. Never the files that are incompatible or say nothing about their hardware.
    verify: also leave out the files that won't load for uploading (see _loads()). Loads every file found, so it's slow.
    """
    fits = [(info, info.fit(hardware)) for info in firmware if edition is None or info.edition == edition.lower()]
    def ordered_by_newest(wanted: ThreespaceFirmwareCompatibility):
        return sorted((info for info, fit in fits if fit == wanted), key=lambda info: info.build_time, reverse=True)
    found = (ordered_by_newest(ThreespaceFirmwareCompatibility.TARGETED)
             + ordered_by_newest(ThreespaceFirmwareCompatibility.OLDER_TARGET))
    return [info for info in found if _loads(info)] if verify else found

def pick_firmware(firmware: Iterable[ThreespaceFirmwareInfo], hardware: ThreespaceHardwareVersion,
                  edition: str | None = STANDARD_EDITION, verify: bool = False) -> ThreespaceFirmwareInfo | None:
    """
    The firmware to put on a sensor automatically, or None when there is none for its hardware. edition: as for
    compatible_firmware(). Check its fit(): OLDER_TARGET means it was built for an older board, which deserves a warning.
    verify: the first file that loads for uploading (see _loads()), passing over any that don't. Loads only until one
    does, so usually just the one picked.
    """
    found = compatible_firmware(firmware, hardware, edition)
    return next((info for info in found if not verify or _loads(info)), None)

def _loads(info: ThreespaceFirmwareInfo) -> bool:
    """
    Whether a file loads for uploading: read_firmware_info() reads only its header, so this is what finds the
    files that are corrupted (Ex: don't match their hash) or malformed after the header, and the ones gone since
    """
    try:
        load_firmware_file(info.path)
        return True
    except (FirmwareFileError, OSError):
        return False

# ---- Parsing -----------------------------------------------------------------

@contextmanager
def _prefix_errors_with(path: str):
    """Adds the file's path to the start of any FirmwareFileError raised inside the with block"""
    try:
        yield
    except FirmwareFileError as error:
        error.args = (f"{path}: {error}",)   # Changing the message keeps the error's traceback and cause
        raise

def _collapse(text: str) -> str:
    return " ".join(text.split())

def _build_time(version: str) -> datetime | None:
    """The build time in a version (C's __DATE__ __TIME__, Ex: Sep 30 2026 20:02:51), or None when it isn't one"""
    parts = version.split()
    if len(parts) != 4 or parts[0] not in _MONTHS:
        return None
    try:
        hour, minute, second = parts[3].split(":")
        return datetime(int(parts[2]), _MONTHS.index(parts[0]) + 1, int(parts[1]), int(hour), int(minute), int(second))
    except ValueError:
        return None

def _parse_header(path: str, header: list[ET.Element]) -> ThreespaceFirmwareInfo:
    """path: the file's, kept in the info. header: the root's children before the instructions"""
    # The header elements by name. Others are skipped: older files have elements of their own (Ex: <FirmwareType>)
    elements: dict[str, ET.Element] = {}
    for element in header:
        if element.tag in _HEADER:
            if element.tag in elements:
                raise FirmwareFileError(f"more than one <{element.tag}>")
            elements[element.tag] = element

    version = build_time = None
    if "Version" in elements:
        version = _collapse(elements["Version"].text or "")
        build_time = _build_time(version)
    edition = STANDARD_EDITION
    if "Edition" in elements:
        edition = (elements["Edition"].text or "").strip().lower()
        if not edition:
            raise FirmwareFileError("<Edition> is empty")
    hash = hash_algorithm = None
    if "Hash" in elements:
        hash_algorithm = (elements["Hash"].get("algorithm") or "").lower()
        # The shake algorithms are left out: their hexdigest() needs a length
        if hash_algorithm not in hashlib.algorithms_available or hash_algorithm.startswith("shake_"):
            raise FirmwareFileError(f"<Hash> needs an algorithm this computer has (Ex: sha256), not {hash_algorithm or 'none'}")
        hash = (elements["Hash"].text or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]+", hash):
            raise FirmwareFileError("<Hash> isn't hex")
    compatible_hardware = None
    if "Compatibility" in elements:
        # Picking sorts by build time, so firmware that can be picked needs one
        if build_time is None:
            raise FirmwareFileError(f"<Version> must be the sensor's version_firmware (Ex: Sep 30 2026 20:02:51), not {version}")
        compatible_hardware = tuple(_parse_hardware(element) for element in elements["Compatibility"].findall("Hardware"))
        # Ex: a misspelled <Hardware>, which would make the firmware refuse every sensor without saying why
        if not compatible_hardware:
            raise FirmwareFileError("<Compatibility> has no <Hardware>")
        families = [(entry.family_id, entry.variation) for entry in compatible_hardware]
        if len(set(families)) != len(families):
            raise FirmwareFileError("more than one <Hardware> for the same family and variation")
    return ThreespaceFirmwareInfo(path, version, build_time, edition, hash, hash_algorithm, compatible_hardware)

def _parse_hardware(element: ET.Element) -> ThreespaceFirmwareHardware:
    """element: a <Hardware>"""
    family_id = _hex(element, "family", (2,))
    variation = _hex(element, "variation", (1, 2, 3))
    # Its board elements by name, in file order
    boards = {tag: [_parse_board(board) for board in element.findall(tag)]
              for tag in ("MinBoard", "TargetBoard", "ExcludedBoard")}
    for tag in ("MinBoard", "TargetBoard"):
        if len(boards[tag]) != 1:
            raise FirmwareFileError(f"<Hardware> needs one <{tag}>, not {len(boards[tag])}")
    return ThreespaceFirmwareHardware(family_id, variation, boards["MinBoard"][0], boards["TargetBoard"][0],
                                      tuple(boards["ExcludedBoard"]))

def _parse_board(element: ET.Element) -> ThreespaceBoardRevision:
    """element: a <MinBoard>, <TargetBoard> or <ExcludedBoard>"""
    board = ThreespaceBoardRevision(_hex(element, "core", (1,)), _hex(element, "major", (1, 2)), _hex(element, "minor", (1, 2)))
    # Also catches a minor written as its label (c for c0): read as 0c, it starts with a number
    if not board.is_valid:
        raise FirmwareFileError(f"<{element.tag}> major {board.major:02x} minor {board.minor:02x} isn't a board revision: "
                                "a major ends in 0-9, and a minor starts with a-f or is 00")
    return board

def _hex(element: ET.Element, attribute: str, digits: tuple[int, ...]) -> int:
    """A hex attribute, with one of the numbers of digits given"""
    value = element.get(attribute)
    # fullmatch: the whole text must be hex digits (int() alone would also take "0x1f", "+1f" and " 1f")
    if value is None or not re.fullmatch(r"[0-9a-fA-F]+", value) or len(value) not in digits:
        raise FirmwareFileError(f"<{element.tag}> {attribute} must be {' or '.join(map(str, digits))} hex digits, not {value}")
    return int(value, 16)

def _program_data(element: ET.Element) -> bytes:
    """element: a <MemProgC>, whose text is the data as hex (fromhex skips the whitespace between digit pairs)"""
    try:
        data = bytes.fromhex(element.text or "")
    except ValueError as error:
        raise FirmwareFileError(f"<MemProgC> isn't hex: {error}") from error
    if not data:
        raise FirmwareFileError("<MemProgC> is empty")
    return data

# ---- Uploading ---------------------------------------------------------------

class ThreespaceFirmwareUploader:

    def __init__(self, sensor: ThreespaceSensor, file_path: str = None, percentage_callback: Callable[[float],None] = None, verbose: bool = False):
        self.sensor = sensor
        self.firmware: ThreespaceFirmwareFile | None = None
        self.set_firmware_path(file_path)
        self.verbose = verbose

        self.percent_complete = 0
        self.callback = percentage_callback

    def set_firmware_path(self, file_path: str):
        """Raises FirmwareFileError when the file is malformed or doesn't match its hash"""
        self.firmware = None if file_path is None else load_firmware_file(file_path)

    def set_percent_callback(self, callback: Callable[[float],None]):
        self.callback = callback

    def set_verbose(self, verbose: bool):
        self.verbose = verbose

    def get_percent_done(self):
        return self.percent_complete

    def __set_percent_complete(self, percent: float):
        self.percent_complete = percent
        if self.callback:
            self.callback(percent)

    def log(self, *args):
        if not self.verbose: return
        print(*args)

    def upload_firmware(self, allow_incompatible: bool = False):
        """
        Puts the firmware on the sensor, entering the bootloader first if needed. Raises FirmwareUploadError:
        - Before changing anything, when the firmware isn't for the sensor's hardware. allow_incompatible uploads it
          anyway: Ex, a sensor whose serial number doesn't tell its hardware.
        - When the bootloader refuses to erase or program, leaving the sensor in bootloader.
        """
        if self.firmware is None:
            raise ValueError("No firmware file set")
        fit = self.firmware.info.fit(self.sensor.hardware_version)
        if fit == ThreespaceFirmwareCompatibility.INCOMPATIBLE and not allow_incompatible:
            raise FirmwareUploadError(f"{self.firmware.info.path} isn't for this sensor's hardware "
                                      f"({self.sensor.hardware_version}, serial number {self.sensor.serial_number:016X})")
        if fit == ThreespaceFirmwareCompatibility.OLDER_TARGET:
            self.log("Firmware was built for an older board than this sensor's")

        self.percent_complete = 0
        if not self.sensor.in_bootloader:
            self.sensor.enterBootloader()
        self.__set_percent_complete(5)

        boot_info = self.sensor.bootloader_get_info()

        program_size = self.firmware.program_size
        programmed = 0
        for name, mem in self.firmware.instructions:
            if name == "SetAddr":
                self.log("Write S")
                error = self.sensor.bootloader_erase_firmware()
                if error:
                    raise FirmwareUploadError(f"Failed to erase firmware: {_bootloader_error(error)}")
                self.log("Successfully erased firmware")
                self.__set_percent_complete(20)
            elif name == "MemProgC":
                self.log("Attempting to program", len(mem), "bytes to the chip")
                offset = 0   # Within this MemProgC's data; programmed counts across all of them, for the progress
                while offset < len(mem):
                    memchunk = mem[offset : min(len(mem), offset + boot_info.pagesize)]
                    error = self.sensor.bootloader_prog_mem(memchunk)
                    if error:
                        raise FirmwareUploadError(f"Failed to program {len(memchunk)} bytes at offset {offset}: "
                                                  f"{_bootloader_error(error)}")
                    self.log("Wrote", len(memchunk), "bytes successfully to offset", offset)
                    offset += len(memchunk)
                    programmed += len(memchunk)
                    self.__set_percent_complete(20 + programmed / program_size * 79)
            elif name == "Run":
                self.log("Resetting with new firmware.")
                self.sensor.bootloader_boot_firmware()
                self.__set_percent_complete(100)
            # Finish: only marks the end of the file, nothing to do

def _bootloader_error(error: int) -> str:
    return "no response" if error == -1 else f"error {error}"
