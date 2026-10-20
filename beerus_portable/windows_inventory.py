"""Windows drive inventory for Beerus Portable.

The boot form of Beerus reads its drives from ``lsblk`` (see
``beerus.inventory``). A Windows machine has no ``lsblk``, so the
portable form asks Windows — read-only, through PowerShell's
storage cmdlets — and translates the answer into the same
``beerus.inventory.Drive`` records the engine already consumes.
The engine, the gates, and the method selection are untouched:
this module is a platform seam, not a second inventory.

Two portable refusal rules live here, because only a live
Windows system has them:

- **The drive Windows is running from can never be a target.**
  It is the drive holding the system volume.
- **The drive Beerus Portable itself is running from can never
  be a target.** The app usually arrives on a USB stick; the
  stick it is running from is protected exactly the way the boot
  key protects itself.

Both drives are marked ``is_boot`` on the records this module
returns, so the engine's and the gates' existing boot-device
refusals protect them at every layer — the GUI's plainer wording
below is presentation; the protection does not depend on it.

This module only ever *reads* system state. It never opens a
device for writing and never issues a destructive command.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass

from beerus.errors import BeerusError
from beerus.inventory import Drive, DriveClass

SYSTEM_DRIVE_MESSAGE = (
    "This is the drive Windows is running from. "
    "Beerus Portable never wipes it."
)

OWN_DRIVE_MESSAGE = (
    "This is the drive Beerus Portable is running from. "
    "It can never be wiped."
)


@dataclass(frozen=True)
class PortableDriveContext:
    """Which enumerated drives the portable rules protect, and why.

    Fingerprints, not paths: the same physical drive keeps its
    fingerprint across re-lettering, so the protection follows
    the drive, not the letter it happened to get today.
    """

    system_fingerprints: frozenset = frozenset()
    own_fingerprints: frozenset = frozenset()

    def block_reason(self, drive) -> str | None:
        """The portable refusal for *drive*, or None if none applies."""
        if drive.fingerprint in self.own_fingerprints:
            return OWN_DRIVE_MESSAGE
        if drive.fingerprint in self.system_fingerprints:
            return SYSTEM_DRIVE_MESSAGE
        return None


# ---------------------------------------------------------------------------
# PowerShell queries (read-only). Two small queries, each returning JSON:
# the physical disks, and the partitions (which carry drive letters, and so
# say which disk Windows lives on and which disk this app was started from).
# ---------------------------------------------------------------------------

DISKS_SCRIPT = (
    "Get-PhysicalDisk | "
    "Select-Object DeviceId, FriendlyName, SerialNumber, Size, "
    "BusType, MediaType | ConvertTo-Json -Compress"
)

PARTITIONS_SCRIPT = (
    "Get-Partition | "
    "Select-Object DiskNumber, DriveLetter, Type | "
    "ConvertTo-Json -Compress"
)


def _default_runner(script: str) -> str:
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise BeerusError(
            "Beerus Portable could not read the list of drives "
            "on this machine."
        )
    return completed.stdout


def _as_list(text) -> list:
    """Parse PowerShell JSON; a single result arrives as an object."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        raise BeerusError(
            "Beerus Portable could not understand the list of "
            "drives on this machine."
        ) from None
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [entry for entry in data if isinstance(entry, dict)]
    return []


def _classify_windows(bus_type, media_type) -> DriveClass:
    """Classify from the bus and media type Windows reports.

    Mirrors ``beerus.inventory.classify``: when Windows cannot
    say whether a SATA-class drive spins or is flash, the answer
    is SATA_UNKNOWN — said honestly, never guessed.
    """
    bus = str(bus_type or "").strip().lower()
    media = str(media_type or "").strip().lower()
    if bus == "nvme":
        return DriveClass.NVME
    if bus == "usb":
        return DriveClass.USB
    if bus in ("sata", "ata", "scsi", "sas", "atapi"):
        if media == "ssd":
            return DriveClass.SATA_SSD
        if media == "hdd":
            return DriveClass.SATA_HDD
        return DriveClass.SATA_UNKNOWN
    return DriveClass.UNKNOWN


def _drive_letter_of(path) -> str:
    """The Windows drive letter a path starts with, or "".

    Parsed from the path text (a leading ``X:``), not from the
    host's path rules — the answer must be the Windows answer
    regardless of where this code happens to be running.
    """
    text = str(path or "")
    if len(text) >= 2 and text[1] == ":" and text[0].isalpha():
        return text[0].upper()
    return ""


def _disk_numbers_for_letter(partitions, letter) -> set:
    """Disk numbers holding a partition with this drive letter."""
    if not letter:
        return set()
    wanted = str(letter).strip().rstrip(":").upper()
    if not wanted:
        return set()
    numbers = set()
    for part in partitions:
        drive_letter = part.get("DriveLetter")
        if drive_letter is None:
            continue
        # Get-Partition reports DriveLetter as a character code
        # on some builds and as a string on others; accept both.
        if isinstance(drive_letter, int):
            drive_letter = chr(drive_letter) if drive_letter else ""
        if str(drive_letter).strip().upper() == wanted:
            try:
                numbers.add(int(part.get("DiskNumber")))
            except (TypeError, ValueError):
                continue
    return numbers


def parse_windows_inventory(
    disks_json,
    partitions_json,
    *,
    system_letter=None,
    app_letter=None,
) -> tuple[list[Drive], PortableDriveContext]:
    """Translate PowerShell answers into engine Drive records.

    Pure function of its inputs — the test suite drives it with
    recorded-shape JSON, never with a live machine. Drives on the
    system disk or on the app's own disk are marked ``is_boot``
    so the engine's boot-device protection covers them.
    """
    disks = _as_list(disks_json)
    partitions = _as_list(partitions_json)
    system_numbers = _disk_numbers_for_letter(partitions, system_letter)
    own_numbers = _disk_numbers_for_letter(partitions, app_letter)

    drives: list[Drive] = []
    system_fingerprints = set()
    own_fingerprints = set()
    for entry in disks:
        try:
            number = int(entry.get("DeviceId"))
        except (TypeError, ValueError):
            continue
        try:
            size_bytes = int(entry.get("Size"))
        except (TypeError, ValueError):
            size_bytes = 0
        bus = entry.get("BusType")
        drive = Drive(
            device_path=f"\\\\.\\PhysicalDrive{number}",
            raw_name=f"PhysicalDrive{number}",
            # PowerShell pads some vendors' model/serial strings;
            # canonicalize here so the typed-serial gate compares
            # clean values, exactly as the lsblk parser does.
            model=str(entry.get("FriendlyName") or "").strip(),
            serial=str(entry.get("SerialNumber") or "").strip(),
            size_bytes=size_bytes,
            interface=str(bus or "").strip(),
            wwn="",
            drive_class=_classify_windows(bus, entry.get("MediaType")),
            is_boot=number in system_numbers or number in own_numbers,
        )
        drives.append(drive)
        if number in system_numbers:
            system_fingerprints.add(drive.fingerprint)
        if number in own_numbers:
            own_fingerprints.add(drive.fingerprint)

    context = PortableDriveContext(
        system_fingerprints=frozenset(system_fingerprints),
        own_fingerprints=frozenset(own_fingerprints),
    )
    return drives, context


class WindowsDriveEnumerator:
    """Callable drive enumerator for the portable app.

    Each call re-reads the machine fresh (the engine calls it
    again at wipe time and trusts nothing from the earlier
    listing) and refreshes ``self.context`` with the portable
    protection facts for the drives it just read.
    """

    def __init__(self, runner=None, *, system_letter=None, app_path=None):
        self._runner = runner if runner is not None else _default_runner
        if system_letter is None:
            system_drive = os.environ.get("SystemDrive", "C:")
            system_letter = system_drive.rstrip(":")
        if app_path is None:
            import sys

            app_path = sys.executable
        self._system_letter = system_letter
        self._app_letter = _drive_letter_of(app_path)
        self.context = PortableDriveContext()

    def __call__(self) -> list[Drive]:
        try:
            disks_json = self._runner(DISKS_SCRIPT)
            partitions_json = self._runner(PARTITIONS_SCRIPT)
        except BeerusError:
            raise
        except Exception:
            raise BeerusError(
                "Beerus Portable could not read the list of drives "
                "on this machine."
            ) from None
        drives, context = parse_windows_inventory(
            disks_json,
            partitions_json,
            system_letter=self._system_letter,
            app_letter=self._app_letter,
        )
        self.context = context
        return drives


def portable_assessment(drive, context):
    """Eligibility for one drive in the portable app.

    The portable rules speak first (their wording names the
    Windows system drive and the app's own drive for what they
    are); everything else defers to the engine's own assessment —
    boot protection, identifiability, and method availability are
    the engine's rules, reused, not reimplemented.
    """
    from beerus.engine import TargetAssessment, assess_target

    if context is not None:
        reason = context.block_reason(drive)
        if reason is not None:
            return TargetAssessment(drive, False, reason)
    return assess_target(drive)
