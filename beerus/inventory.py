"""Drive inventory: read-only discovery and identification.

This module only ever *reads* system state (``lsblk``). It never opens
a device for writing and never issues a destructive command.

Identity note: a drive is identified by its model, serial number,
capacity, and interface — never by its device path. Device paths and
kernel names (``/dev/sdb``, ``sdb``) change across reboots, cable
swaps, and re-lettering; the fingerprint deliberately excludes them
so the same physical drive keeps the same identity.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from enum import Enum

from beerus.errors import BeerusError


class DriveClass(Enum):
    NVME = "nvme"
    SATA_SSD = "sata_ssd"
    SATA_HDD = "sata_hdd"
    SATA_UNKNOWN = "sata_unknown"
    USB = "usb"
    UNKNOWN = "unknown"


# Strings firmware/tools report when they have no real value to give.
# Compared case-insensitively after stripping whitespace.
PLACEHOLDER_TEXT: frozenset = frozenset(
    {
        "",
        "unknown",
        "none",
        "n/a",
        "na",
        "null",
        "0",
        "to be filled by o.e.m.",
        "to be filled by o.e.m",
        "to be filled by oem",
        "default string",
        "not specified",
        "not available",
        "serialnumber",
        "o.e.m.",
    }
)


def _is_real_text(value) -> bool:
    """True only when *value* is a genuine, non-placeholder string."""
    if value is None:
        return False
    s = str(value).strip()
    if s.lower() in PLACEHOLDER_TEXT:
        return False
    if len(set(s)) == 1:
        return False
    if re.fullmatch(r"0[xX]0+", s):
        return False
    if not any(ch.isalnum() for ch in s):
        return False
    return True


@dataclass(frozen=True)
class Drive:
    device_path: str
    raw_name: str
    model: str = ""
    serial: str = ""
    size_bytes: int = 0
    interface: str = ""
    wwn: str = ""
    drive_class: DriveClass = DriveClass.UNKNOWN
    is_boot: bool = False

    @property
    def identifiable(self) -> bool:
        """Can Beerus say, with confidence, which physical drive this is?

        Needs a real model, a real serial number, and a known size.
        Placeholder firmware strings ("To Be Filled By O.E.M.", "0",
        blanks) do not count — a drive that cannot be identified
        cannot be safely selected for destruction.
        """
        return (
            _is_real_text(self.model)
            and _is_real_text(self.serial)
            and self.size_bytes > 0
        )

    @property
    def fingerprint(self) -> str:
        """Stable identity hash for this physical drive.

        Built from model, serial, size, and interface only. It
        DELIBERATELY excludes ``device_path`` and ``raw_name``:
        identity must survive device re-lettering, so the same drive
        plugged in as ``/dev/sdc`` tomorrow fingerprints exactly as it
        did as ``/dev/sdb`` today.
        """
        material = "|".join(
            [
                self.model.strip(),
                self.serial.strip(),
                str(self.size_bytes),
                self.interface.strip().lower(),
                self.wwn.strip(),
            ]
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


# Mountpoints that mean "the running system lives on this drive".
BOOT_MOUNTPOINTS = frozenset({"/", "/boot", "/boot/efi", "/efi"})


def _normalize_mountpoint(mountpoint: str) -> str:
    """Strip trailing slashes, except for the root mountpoint itself."""
    if mountpoint == "/":
        return mountpoint
    return mountpoint.rstrip("/")


def classify(raw_name, tran, rota) -> DriveClass:
    """Classify a drive from its kernel name, transport, and rotation flag.

    ``rota`` is True for spinning disks, False for flash, and None when
    the system did not report it — in which case we say so honestly
    (``SATA_UNKNOWN``) rather than guessing.
    """
    name = str(raw_name or "").strip().lower()
    transport = str(tran or "").strip().lower()

    if transport == "nvme" or name.startswith("nvme"):
        return DriveClass.NVME
    if transport == "usb":
        return DriveClass.USB
    if transport in ("sata", "ata", "scsi") or name.startswith(("sd", "hd", "vd")):
        if rota is None:
            return DriveClass.SATA_UNKNOWN
        return DriveClass.SATA_HDD if bool(rota) else DriveClass.SATA_SSD
    return DriveClass.UNKNOWN


def _collect_mountpoints(node) -> list:
    """Every mountpoint on *node* and its children, at any depth."""
    found: list = []

    mountpoints = node.get("mountpoints")
    if isinstance(mountpoints, (list, tuple)):
        for entry in mountpoints:
            if entry:
                found.append(entry)
    elif isinstance(mountpoints, str) and mountpoints:
        found.append(mountpoints)

    # Older lsblk versions report a single singular "mountpoint".
    legacy = node.get("mountpoint")
    if isinstance(legacy, str) and legacy:
        found.append(legacy)

    for child in node.get("children") or []:
        if isinstance(child, dict):
            found.extend(_collect_mountpoints(child))

    return found


def parse_lsblk_json(text) -> list[Drive]:
    """Parse ``lsblk --json`` output into a list of whole-disk drives.

    Only nodes with ``type == "disk"`` become drives — partitions and
    other children are inspected for boot mountpoints but are never
    returned as drives themselves.
    """
    data = json.loads(text)
    if not isinstance(data, dict):
        return []

    drives: list[Drive] = []
    for node in data.get("blockdevices") or []:
        if not isinstance(node, dict):
            continue
        if node.get("type") != "disk":
            continue

        mountpoints = _collect_mountpoints(node)
        is_boot = any(
            _normalize_mountpoint(mp) in BOOT_MOUNTPOINTS for mp in mountpoints
        )

        model = node.get("model")
        serial = node.get("serial")
        wwn = node.get("wwn")

        try:
            size_bytes = int(node.get("size"))
        except (TypeError, ValueError):
            size_bytes = 0

        tran = node.get("tran")
        raw_name = node.get("name") or ""

        drives.append(
            Drive(
                device_path=node.get("path") or "",
                raw_name=raw_name,
                # lsblk pads MODEL/SERIAL on some systems; canonicalize
                # here so the typed-serial gate compares clean values.
                model="" if model is None else str(model).strip(),
                serial="" if serial is None else str(serial).strip(),
                size_bytes=size_bytes,
                interface="" if tran is None else str(tran).strip(),
                wwn="" if wwn is None else str(wwn).strip(),
                drive_class=classify(raw_name, tran, node.get("rota")),
                is_boot=is_boot,
            )
        )
    return drives


LSBLK_ARGV = [
    "lsblk",
    "--json",
    "--bytes",
    "--output",
    "NAME,PATH,MODEL,SERIAL,SIZE,TYPE,TRAN,ROTA,MOUNTPOINTS,WWN",
]


def _default_runner(argv) -> str:
    return subprocess.run(
        argv, capture_output=True, text=True, check=True
    ).stdout


def enumerate_drives(runner=None) -> list[Drive]:
    """List the system's drives. Read-only: this only runs ``lsblk``."""
    if runner is None:
        runner = _default_runner
    try:
        return parse_lsblk_json(runner(LSBLK_ARGV))
    except BeerusError:
        raise
    except Exception:
        raise BeerusError(
            "Beerus could not read the list of drives on this machine."
        ) from None


def find_drive(drives, device_path) -> Drive | None:
    """Return the drive with exactly this ``device_path``, or None."""
    for drive in drives:
        if drive.device_path == device_path:
            return drive
    return None
