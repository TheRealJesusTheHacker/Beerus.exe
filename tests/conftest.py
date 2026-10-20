"""Shared synthetic fixtures for the Beerus test suite.

SAFETY: everything here is synthetic. Fake drives are data objects;
fake device targets are small regular files created under pytest's
tmp_path. No fixture touches a real block device, ever.
"""

from __future__ import annotations

import json
import os

import pytest

from beerus.inventory import Drive, DriveClass

MIB = 1024 * 1024


def make_drive(**overrides) -> Drive:
    """A plain, identifiable SATA hard drive by default."""
    fields = dict(
        device_path="/dev/sdb",
        raw_name="sdb",
        model="WDC WD10EZEX-08WN4A0",
        serial="WD-WCC4N1234567",
        size_bytes=8 * MIB,
        interface="sata",
        drive_class=DriveClass.SATA_HDD,
        is_boot=False,
    )
    fields.update(overrides)
    return Drive(**fields)


def make_nvme(**overrides) -> Drive:
    base = dict(
        device_path="/dev/nvme0n1",
        raw_name="nvme0n1",
        model="Samsung SSD 970 EVO Plus 500GB",
        serial="S4EVNF0M123456A",
        size_bytes=16 * MIB,
        interface="nvme",
        drive_class=DriveClass.NVME,
    )
    base.update(overrides)
    return make_drive(**base)


def make_ssd(**overrides) -> Drive:
    base = dict(
        device_path="/dev/sdc",
        raw_name="sdc",
        model="Samsung SSD 860 EVO 500GB",
        serial="S3Z8NB0K123456B",
        interface="sata",
        drive_class=DriveClass.SATA_SSD,
    )
    base.update(overrides)
    return make_drive(**base)


def make_boot_drive(**overrides) -> Drive:
    base = dict(is_boot=True)
    base.update(overrides)
    return make_drive(**base)


def make_unidentifiable(**overrides) -> Drive:
    base = dict(model="", serial="")
    base.update(overrides)
    return make_drive(**base)


def lsblk_json(entries) -> str:
    """Build lsblk -J style JSON text from a list of node dicts."""
    return json.dumps({"blockdevices": entries})


def disk_node(name, *, model="Some Model", serial="SERIAL123", size=8 * MIB,
              tran="sata", rota=True, mountpoints=None, children=None, wwn=None):
    node = {
        "name": name,
        "path": f"/dev/{name}",
        "model": model,
        "serial": serial,
        "size": size,
        "type": "disk",
        "tran": tran,
        "rota": rota,
        "mountpoints": mountpoints if mountpoints is not None else [None],
        "wwn": wwn,
    }
    if children:
        node["children"] = children
    return node


def part_node(name, *, mountpoints=None):
    return {
        "name": name,
        "path": f"/dev/{name}",
        "model": None,
        "serial": None,
        "size": 1024,
        "type": "part",
        "tran": None,
        "rota": None,
        "mountpoints": mountpoints if mountpoints is not None else [None],
    }


@pytest.fixture()
def fake_device_file(tmp_path):
    """Create a sparse regular file standing in for a drive.

    Returns (path_str, size_bytes). The file is created by truncate,
    so it reads back as zeros until a pass writes over it.
    """
    def _make(size_bytes: int = 8 * MIB) -> tuple[str, int]:
        path = tmp_path / "fake-drive.img"
        with open(path, "wb") as fh:
            fh.truncate(size_bytes)
        return str(path), size_bytes
    return _make


@pytest.fixture()
def drive_factory():
    return make_drive
