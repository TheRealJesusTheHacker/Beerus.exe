"""Destruction method selection (NIST 800-88).

Given an identified drive, pick the strongest honest method its
hardware supports:

- NVMe            -> NVMe Sanitize           (Purge)
- SATA SSD        -> ATA Secure Erase        (Purge)
- SATA HDD        -> Multi-pass overwrite    (Clear)
- Anything unsure -> refuse, plainly, and wipe nothing.

This module only *plans*. It never executes anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .errors import MethodUnavailableError, UnidentifiableDriveError
from .inventory import Drive, DriveClass


class MethodKind(Enum):
    ATA_SECURE_ERASE = "ata_secure_erase"
    NVME_SANITIZE = "nvme_sanitize"
    NVME_FORMAT = "nvme_format"
    OVERWRITE = "overwrite"


@dataclass(frozen=True)
class PassSpec:
    pattern: str  # "zero" | "one" | "random"
    label: str


OVERWRITE_PASSES = (
    PassSpec("zero", "Write zeros"),
    PassSpec("one", "Write ones"),
    PassSpec("random", "Write random data"),
    PassSpec("zero", "Write zeros (final pass)"),
)


@dataclass(frozen=True)
class WipeStep:
    kind: MethodKind
    label: str
    detail: str


@dataclass(frozen=True)
class WipePlan:
    drive_fingerprint: str
    method_label: str
    nist_category: str
    steps: tuple[WipeStep, ...]
    passes: tuple[PassSpec, ...] = ()


def select_plan(drive: Drive) -> WipePlan:
    """Choose the destruction plan for *drive*, or refuse plainly.

    Raises UnidentifiableDriveError when the drive cannot be
    identified, and MethodUnavailableError when Beerus cannot tell
    what kind of drive it is or cannot erase it safely. On any raise,
    nothing was wiped.
    """
    if not drive.identifiable:
        raise UnidentifiableDriveError(
            "Beerus could not identify this drive — its model or serial "
            "number could not be read — so it will not wipe it."
        )

    fingerprint = drive.fingerprint

    if drive.drive_class is DriveClass.NVME:
        return WipePlan(
            drive_fingerprint=fingerprint,
            method_label="NVMe Sanitize — the drive's own built-in erase",
            nist_category="Purge",
            steps=(
                WipeStep(
                    MethodKind.NVME_SANITIZE,
                    "NVMe Sanitize",
                    "The drive's firmware erases every block, including "
                    "areas overwriting cannot reach.",
                ),
            ),
        )

    if drive.drive_class is DriveClass.SATA_SSD:
        return WipePlan(
            drive_fingerprint=fingerprint,
            method_label="ATA Secure Erase — the drive's own built-in erase",
            nist_category="Purge",
            steps=(
                WipeStep(
                    MethodKind.ATA_SECURE_ERASE,
                    "ATA Secure Erase",
                    "The drive's firmware erases every block, including "
                    "areas overwriting cannot reach.",
                ),
            ),
        )

    if drive.drive_class is DriveClass.SATA_HDD:
        return WipePlan(
            drive_fingerprint=fingerprint,
            method_label="Multi-pass overwrite (4 passes)",
            nist_category="Clear",
            steps=(
                WipeStep(
                    MethodKind.OVERWRITE,
                    "Overwrite the whole drive",
                    "Every byte of the drive is overwritten, pass by "
                    "pass, across its full capacity.",
                ),
            ),
            passes=OVERWRITE_PASSES,
        )

    if drive.drive_class is DriveClass.SATA_UNKNOWN:
        raise MethodUnavailableError(
            "Beerus can see this drive but cannot tell whether it spins "
            "or is flash memory, so it cannot pick a safe destruction "
            "method. Nothing was wiped."
        )

    if drive.drive_class is DriveClass.USB:
        raise MethodUnavailableError(
            "Drives connected over USB are not wiped by this version of "
            "Beerus — the connection cannot pass the drive's own erase "
            "commands through reliably. Nothing was wiped."
        )

    # DriveClass.UNKNOWN (and anything unforeseen): refuse honestly.
    raise MethodUnavailableError(
        "Beerus cannot tell what kind of drive this is, so it cannot "
        "pick a safe destruction method. Nothing was wiped."
    )
