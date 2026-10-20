"""Pure command builders for firmware wipe methods.

Nothing in this module executes anything. Each builder only returns
an argv list; the executor decides whether (and how) to run it.
"""

from __future__ import annotations

import os
import posixpath

# Temporary ATA security password, set only so the erase command can
# run; standard practice for Secure Erase.
TEMP_PASSWORD = "beerus"


def _validate_device_path(device_path) -> str:
    if not isinstance(device_path, str):
        raise ValueError("Device path must be a path under /dev/.")
    if not device_path.startswith("/dev/"):
        raise ValueError("Device path must start with /dev/.")
    if any(ch.isspace() for ch in device_path):
        raise ValueError("Device path must not contain whitespace.")
    if len(device_path) <= 5:
        raise ValueError("Device path is too short to be a device.")
    if "\x00" in device_path:
        raise ValueError("Device path must not contain a null byte.")
    # The paths validated here are POSIX /dev paths by
    # definition (these builders produce Linux commands), so
    # the cleanliness check uses posixpath on every host —
    # os.path.normpath on a Windows host would rewrite the
    # separators and reject every legitimate device path.
    if posixpath.normpath(device_path) != device_path:
        raise ValueError("Device path must be a clean, direct device path.")
    if any(component in (".", "..") for component in device_path.split("/")):
        raise ValueError("Device path must be a clean, direct device path.")
    return device_path


def ata_secure_erase_commands(device_path) -> list[list[str]]:
    dp = _validate_device_path(device_path)
    return [
        [
            "hdparm",
            "--user-master",
            "u",
            "--security-set-pass",
            TEMP_PASSWORD,
            dp,
        ],
        [
            "hdparm",
            "--user-master",
            "u",
            "--security-erase",
            TEMP_PASSWORD,
            dp,
        ],
    ]


def nvme_sanitize_command(device_path) -> list[str]:
    dp = _validate_device_path(device_path)
    return ["nvme", "sanitize", dp, "--sanact=2"]


def nvme_format_command(device_path) -> list[str]:
    dp = _validate_device_path(device_path)
    return ["nvme", "format", dp, "--ses=1"]


def nvme_sanitize_log_command(device_path) -> list[str]:
    dp = _validate_device_path(device_path)
    return ["nvme", "sanitize-log", dp]
