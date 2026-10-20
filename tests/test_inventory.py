"""Inventory tests — all synthetic lsblk data, never a real drive."""

from __future__ import annotations

import subprocess

import pytest

from beerus.errors import BeerusError
from beerus.inventory import (
    LSBLK_ARGV,
    Drive,
    DriveClass,
    enumerate_drives,
    find_drive,
    parse_lsblk_json,
)
from conftest import (
    MIB,
    disk_node,
    lsblk_json,
    make_drive,
    part_node,
)


# ---------------------------------------------------------------- parse

def test_parse_basic_sata_hdd_checks_every_field():
    text = lsblk_json(
        [
            disk_node(
                "sda",
                model="WDC WD10EZEX-08WN4A0",
                serial="WD-WCC4N1234567",
                size=500 * MIB,
                tran="sata",
                rota=True,
            )
        ]
    )
    drives = parse_lsblk_json(text)

    assert len(drives) == 1
    drive = drives[0]
    assert drive.device_path == "/dev/sda"
    assert drive.raw_name == "sda"
    assert drive.model == "WDC WD10EZEX-08WN4A0"
    assert drive.serial == "WD-WCC4N1234567"
    assert drive.size_bytes == 500 * MIB
    assert drive.interface == "sata"
    assert drive.drive_class is DriveClass.SATA_HDD
    assert drive.is_boot is False
    assert drive.identifiable is True


def test_parse_nvme_node():
    text = lsblk_json(
        [
            disk_node(
                "nvme0n1",
                model="Samsung SSD 970 EVO Plus 500GB",
                serial="S4EVNF0M123456A",
                size=64 * MIB,
                tran="nvme",
                rota=False,
            )
        ]
    )
    (drive,) = parse_lsblk_json(text)
    assert drive.device_path == "/dev/nvme0n1"
    assert drive.raw_name == "nvme0n1"
    assert drive.interface == "nvme"
    assert drive.drive_class is DriveClass.NVME
    assert drive.identifiable is True


def test_parse_sata_ssd_rota_false():
    text = lsblk_json([disk_node("sdc", tran="sata", rota=False)])
    (drive,) = parse_lsblk_json(text)
    assert drive.drive_class is DriveClass.SATA_SSD


def test_parse_sata_rota_none_is_unknown_kind():
    text = lsblk_json([disk_node("sdd", tran="sata", rota=None)])
    (drive,) = parse_lsblk_json(text)
    assert drive.drive_class is DriveClass.SATA_UNKNOWN


def test_parse_usb_tran():
    text = lsblk_json([disk_node("sde", tran="usb", rota=False)])
    (drive,) = parse_lsblk_json(text)
    assert drive.drive_class is DriveClass.USB
    assert drive.interface == "usb"


# ----------------------------------------------------------------- boot

def test_boot_detected_via_child_partition_mounted_at_root():
    child = part_node("sda1", mountpoints=["/"])
    text = lsblk_json([disk_node("sda", children=[child])])
    (drive,) = parse_lsblk_json(text)
    assert drive.is_boot is True


def test_boot_detected_via_boot_efi_mountpoint():
    child = part_node("sda1", mountpoints=["/boot/efi"])
    text = lsblk_json([disk_node("sda", children=[child])])
    (drive,) = parse_lsblk_json(text)
    assert drive.is_boot is True


def test_boot_detected_on_disk_node_itself():
    text = lsblk_json([disk_node("sda", mountpoints=["/boot", None])])
    (drive,) = parse_lsblk_json(text)
    assert drive.is_boot is True


def test_non_boot_mountpoints_do_not_mark_boot():
    child = part_node("sda1", mountpoints=["/data", None])
    text = lsblk_json([disk_node("sda", children=[child])])
    (drive,) = parse_lsblk_json(text)
    assert drive.is_boot is False


def test_boot_detected_via_efi_mountpoint_on_child():
    child = part_node("sda1", mountpoints=["/efi"])
    text = lsblk_json([disk_node("sda", children=[child])])
    (drive,) = parse_lsblk_json(text)
    assert drive.is_boot is True


def test_boot_detected_via_boot_efi_with_trailing_slash():
    child = part_node("sda1", mountpoints=["/boot/efi/"])
    text = lsblk_json([disk_node("sda", children=[child])])
    (drive,) = parse_lsblk_json(text)
    assert drive.is_boot is True


def test_boot_detected_via_legacy_singular_mountpoint_key():
    node = disk_node("sda")
    del node["mountpoints"]
    node["mountpoint"] = "/"
    node["children"] = [part_node("sda1")]
    # Legacy key on a nested child too — recursion must still find it.
    grandchild = {"name": "sda1p1", "type": "part", "mountpoint": "/boot/efi"}
    node["children"][0]["children"] = [grandchild]

    (drive,) = parse_lsblk_json(lsblk_json([node]))
    assert drive.is_boot is True


# ------------------------------------------------------------ structure

def test_partitions_are_not_returned_as_drives():
    child = part_node("sda1", mountpoints=["/"])
    child2 = part_node("sda2", mountpoints=[None])
    text = lsblk_json(
        [
            disk_node("sda", children=[child, child2]),
            part_node("sdb1"),  # a stray top-level partition node
        ]
    )
    drives = parse_lsblk_json(text)
    assert len(drives) == 1
    assert drives[0].raw_name == "sda"


# --------------------------------------------------------- identifiable

def test_model_none_is_unidentifiable():
    text = lsblk_json([disk_node("sda", model=None)])
    (drive,) = parse_lsblk_json(text)
    assert drive.model == ""
    assert drive.identifiable is False


def test_serial_placeholder_oem_is_not_identifiable_case_insensitive():
    text = lsblk_json([disk_node("sda", serial="To Be Filled By O.E.M.")])
    (drive,) = parse_lsblk_json(text)
    assert drive.identifiable is False


def test_serial_zero_is_not_identifiable():
    text = lsblk_json([disk_node("sda", serial="0")])
    (drive,) = parse_lsblk_json(text)
    assert drive.identifiable is False


@pytest.mark.parametrize(
    "serial", ["", "unknown", "None", "N/A", " Default String ", "O.E.M."]
)
def test_other_placeholder_serials_are_not_identifiable(serial):
    text = lsblk_json([disk_node("sda", serial=serial)])
    (drive,) = parse_lsblk_json(text)
    assert drive.identifiable is False


def test_zero_size_is_not_identifiable():
    text = lsblk_json([disk_node("sda", size=0)])
    (drive,) = parse_lsblk_json(text)
    assert drive.size_bytes == 0
    assert drive.identifiable is False


@pytest.mark.parametrize(
    "serial",
    ["0x00", "0000", "00000000", "00", "-", "?", "To Be Filled By O.E.M"],
)
def test_hardened_placeholder_serials_are_not_identifiable(serial):
    drive = make_drive(serial=serial)
    assert drive.identifiable is False


@pytest.mark.parametrize("serial", ["WD-WCC4N1234567", "S4EVNF0M123456A"])
def test_real_serials_still_identifiable(serial):
    drive = make_drive(serial=serial)
    assert drive.identifiable is True


# ----------------------------------------------------------------- size

def test_size_given_as_string_is_coerced_to_int():
    text = lsblk_json([disk_node("sda", size=str(123456789))])
    (drive,) = parse_lsblk_json(text)
    assert drive.size_bytes == 123456789
    assert isinstance(drive.size_bytes, int)


def test_unparsable_size_becomes_zero():
    text = lsblk_json([disk_node("sda", size="not-a-number")])
    (drive,) = parse_lsblk_json(text)
    assert drive.size_bytes == 0


# ------------------------------------------------------------- enumerate

def test_enumerate_drives_with_fake_runner():
    seen = {}

    def fake_runner(argv):
        seen["argv"] = argv
        return lsblk_json([disk_node("sda"), disk_node("nvme0n1", tran="nvme", rota=False)])

    drives = enumerate_drives(runner=fake_runner)

    assert seen["argv"] == LSBLK_ARGV
    assert [d.raw_name for d in drives] == ["sda", "nvme0n1"]
    assert drives[0].drive_class is DriveClass.SATA_HDD
    assert drives[1].drive_class is DriveClass.NVME


def test_enumerate_runner_failure_wrapped_as_plain_beerus_error():
    def bad_runner(argv):
        raise subprocess.CalledProcessError(1, argv)

    with pytest.raises(BeerusError) as excinfo:
        enumerate_drives(runner=bad_runner)
    assert not isinstance(excinfo.value, subprocess.CalledProcessError)
    assert "could not read the list of drives" in str(excinfo.value)


# ------------------------------------------------------------------- WWN

def test_parse_emits_wwn():
    text = lsblk_json([disk_node("sda", wwn="0x5000cca123456789")])
    (drive,) = parse_lsblk_json(text)
    assert drive.wwn == "0x5000cca123456789"


def test_parse_missing_wwn_becomes_empty_string():
    text = lsblk_json([disk_node("sda")])
    (drive,) = parse_lsblk_json(text)
    assert drive.wwn == ""


def test_lsblk_argv_requests_wwn():
    assert "WWN" in LSBLK_ARGV[-1]


def test_fingerprint_changes_when_only_wwn_changes():
    original = make_drive(wwn="0x5000cca000000001")
    other = make_drive(wwn="0x5000cca000000002")
    assert other.fingerprint != original.fingerprint


# ------------------------------------------------------------ find_drive

def test_find_drive_hit_and_miss():
    drives = parse_lsblk_json(
        lsblk_json([disk_node("sda"), disk_node("sdb")])
    )
    hit = find_drive(drives, "/dev/sdb")
    assert hit is not None
    assert hit.raw_name == "sdb"
    assert find_drive(drives, "/dev/sdz") is None
    assert find_drive([], "/dev/sda") is None


# ------------------------------------------------------------ fingerprint

def test_fingerprint_stable_across_device_path_and_raw_name_changes():
    original = make_drive()
    relettered = make_drive(device_path="/dev/sdz", raw_name="sdz")
    assert relettered.fingerprint == original.fingerprint


def test_fingerprint_changes_when_serial_changes():
    original = make_drive()
    other = make_drive(serial="WD-DIFFERENT999")
    assert other.fingerprint != original.fingerprint


def test_fingerprint_changes_when_size_changes():
    original = make_drive()
    other = make_drive(size_bytes=original.size_bytes + MIB)
    assert other.fingerprint != original.fingerprint


def test_drive_is_frozen():
    drive = make_drive()
    assert isinstance(drive, Drive)
    with pytest.raises(Exception):
        drive.serial = "tampered"  # type: ignore[misc]


def test_parse_strips_padded_model_and_serial():
    """lsblk pads MODEL/SERIAL on some systems; parse must canonicalize,
    or the typed-serial consent gate could never match on real hardware."""
    node = disk_node("sdb", model="WDC WD10EZEX-08WN4A0 ", serial="WD-WCC4N1234567  ")
    (drive,) = parse_lsblk_json(lsblk_json([node]))
    assert drive.model == "WDC WD10EZEX-08WN4A0"
    assert drive.serial == "WD-WCC4N1234567"
    assert drive.identifiable
