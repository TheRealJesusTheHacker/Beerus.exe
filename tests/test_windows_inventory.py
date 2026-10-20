"""Tests for the Windows inventory seam of Beerus Portable.

Everything here runs on recorded-shape PowerShell JSON. No test
touches a live machine, a real disk, or PowerShell itself.
"""

from __future__ import annotations

import json

import pytest

from beerus.errors import BeerusError
from beerus.inventory import DriveClass
from beerus_portable.windows_inventory import (
    DISKS_SCRIPT,
    OWN_DRIVE_MESSAGE,
    PARTITIONS_SCRIPT,
    SYSTEM_DRIVE_MESSAGE,
    PortableDriveContext,
    WindowsDriveEnumerator,
    parse_windows_inventory,
    portable_assessment,
)

MIB = 1024 * 1024


def disks_json(*entries):
    return json.dumps(list(entries))


def parts_json(*entries):
    return json.dumps(list(entries))


def physical_disk(number, *, model="WDC WD10EZEX-08WN4A0",
                  serial="WD-WCC4N1234567", size=8 * MIB,
                  bus="SATA", media="HDD"):
    return {
        "DeviceId": str(number),
        "FriendlyName": model,
        "SerialNumber": serial,
        "Size": size,
        "BusType": bus,
        "MediaType": media,
    }


def partition(disk, letter):
    return {"DiskNumber": disk, "DriveLetter": letter, "Type": "Basic"}


def three_disk_inventory(**kwargs):
    disks = disks_json(
        physical_disk(0, model="Samsung SSD 860 EVO 500GB",
                      serial="S3Z8NB0K123456B", bus="SATA", media="SSD"),
        physical_disk(1, model="SanDisk Ultra USB", serial="4C530001",
                      bus="USB", media="Unspecified"),
        physical_disk(2),
    )
    parts = parts_json(partition(0, "C"), partition(1, "E"))
    kwargs.setdefault("system_letter", "C")
    kwargs.setdefault("app_letter", "E")
    return parse_windows_inventory(disks, parts, **kwargs)


def test_system_and_own_drives_are_marked_boot():
    drives, context = three_disk_inventory()
    system, own, target = drives
    assert system.is_boot is True
    assert own.is_boot is True
    assert target.is_boot is False
    assert context.system_fingerprints == {system.fingerprint}
    assert context.own_fingerprints == {own.fingerprint}


def test_volume_letters_map_letters_to_drives():
    drives, context = three_disk_inventory()
    system, own, _target = drives
    mapping = dict(context.volume_letters)
    assert mapping["C"] == system.fingerprint
    assert mapping["E"] == own.fingerprint


def test_device_path_and_classification():
    drives, _ = three_disk_inventory()
    system, own, target = drives
    assert system.device_path == "\\\\.\\PhysicalDrive0"
    assert system.drive_class is DriveClass.SATA_SSD
    assert own.drive_class is DriveClass.USB
    assert target.drive_class is DriveClass.SATA_HDD
    assert target.serial == "WD-WCC4N1234567"


def test_nvme_and_unknown_classification():
    disks = disks_json(
        physical_disk(0, bus="NVMe", media="SSD"),
        physical_disk(1, bus="RAID", media="Unspecified"),
        physical_disk(2, bus="SATA", media="Unspecified"),
    )
    drives, _ = parse_windows_inventory(disks, "[]")
    assert drives[0].drive_class is DriveClass.NVME
    assert drives[1].drive_class is DriveClass.UNKNOWN
    assert drives[2].drive_class is DriveClass.SATA_UNKNOWN


def test_single_result_object_form_is_accepted():
    # PowerShell returns a lone object (not an array) for one result.
    drives, context = parse_windows_inventory(
        json.dumps(physical_disk(0)), json.dumps(partition(0, "C")),
        system_letter="C",
    )
    assert len(drives) == 1
    assert drives[0].is_boot is True
    assert context.system_fingerprints == {drives[0].fingerprint}


def test_drive_letter_as_character_code():
    disks = disks_json(physical_disk(0))
    parts = parts_json(
        {"DiskNumber": 0, "DriveLetter": 67, "Type": "Basic"}  # 'C'
    )
    drives, context = parse_windows_inventory(
        disks, parts, system_letter="C"
    )
    assert drives[0].is_boot is True
    assert context.system_fingerprints == {drives[0].fingerprint}


def test_padded_serial_is_canonicalized():
    disks = disks_json(physical_disk(0, serial="  WD-1234   "))
    drives, _ = parse_windows_inventory(disks, "[]")
    assert drives[0].serial == "WD-1234"


def test_malformed_json_is_a_plain_refusal():
    with pytest.raises(BeerusError):
        parse_windows_inventory("{not json", "[]")
    with pytest.raises(BeerusError):
        parse_windows_inventory("[]", "{not json")


def test_no_letters_means_no_protection():
    drives, context = parse_windows_inventory(
        disks_json(physical_disk(0)), "[]"
    )
    assert drives[0].is_boot is False
    assert context.block_reason(drives[0]) is None


def test_block_reason_wording_and_precedence():
    drives, _ = three_disk_inventory()
    system, own, target = drives
    context = PortableDriveContext(
        system_fingerprints=frozenset({system.fingerprint}),
        own_fingerprints=frozenset({own.fingerprint}),
    )
    assert context.block_reason(system) == SYSTEM_DRIVE_MESSAGE
    assert context.block_reason(own) == OWN_DRIVE_MESSAGE
    assert context.block_reason(target) is None
    both = PortableDriveContext(
        system_fingerprints=frozenset({target.fingerprint}),
        own_fingerprints=frozenset({target.fingerprint}),
    )
    # A drive that is somehow both: the app's own drive speaks first.
    assert both.block_reason(target) == OWN_DRIVE_MESSAGE


def test_portable_assessment_defers_to_engine_rules():
    drives, _ = three_disk_inventory()
    system, own, target = drives
    context = PortableDriveContext(
        system_fingerprints=frozenset({system.fingerprint}),
        own_fingerprints=frozenset({own.fingerprint}),
    )
    assert portable_assessment(system, context).reason == (
        SYSTEM_DRIVE_MESSAGE
    )
    assert portable_assessment(own, context).reason == OWN_DRIVE_MESSAGE
    # A USB drive that is NOT the app's own (not boot-marked) is
    # still refused — by the engine's method rules, reached through
    # the deferral, not by the portable layer.
    from beerus.inventory import Drive, DriveClass

    plain_usb = Drive(
        device_path="\\\\.\\PhysicalDrive9",
        raw_name="PhysicalDrive9",
        model="Generic USB Drive",
        serial="USB123456789",
        size_bytes=8 * MIB,
        interface="USB",
        drive_class=DriveClass.USB,
        is_boot=False,
    )
    usb = portable_assessment(plain_usb, PortableDriveContext())
    assert usb.eligible is False
    assert "USB" in (usb.reason or "")
    # A plain internal drive passes through to the engine: eligible.
    assert portable_assessment(target, context).eligible is True


def test_enumerator_uses_both_queries_and_refreshes_context():
    seen = []

    def runner(script):
        seen.append(script)
        if script == DISKS_SCRIPT:
            return disks_json(physical_disk(0), physical_disk(1))
        assert script == PARTITIONS_SCRIPT
        return parts_json(partition(0, "C"), partition(1, "F"))

    enumerator = WindowsDriveEnumerator(
        runner=runner, system_letter="C", app_path="F:\\Beerus\\Beerus.exe"
    )
    drives = enumerator()
    assert [d.is_boot for d in drives] == [True, True]
    assert enumerator.context.system_fingerprints == {
        drives[0].fingerprint
    }
    assert enumerator.context.own_fingerprints == {drives[1].fingerprint}
    assert seen == [DISKS_SCRIPT, PARTITIONS_SCRIPT]


def test_enumerator_failure_is_a_plain_error():
    def runner(script):
        raise RuntimeError("powershell exploded")

    enumerator = WindowsDriveEnumerator(
        runner=runner, system_letter="C", app_path="C:\\app\\Beerus.exe"
    )
    with pytest.raises(BeerusError):
        enumerator()
