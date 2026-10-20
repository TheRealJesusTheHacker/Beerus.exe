"""Boot-medium tests — the image-level second layer of boot protection.

SAFETY: everything here is synthetic lsblk data and temp files.
No real device is read, mounted, or touched.
"""

from __future__ import annotations

import json

import pytest

from beerus.bootmedium import (
    DEFAULT_DOCUMENTS_DIR,
    IMAGE_MARKER_FORMAT,
    LIVE_VOLUME_LABEL,
    is_boot_medium_label,
    is_live_medium_mountpoint,
    load_image_marker,
    marker_documents_dir,
)
from beerus.engine import assess_target
from beerus.errors import BootDeviceError
from beerus.gates import GateAuthority
from beerus.inventory import LSBLK_ARGV, parse_lsblk_json
from beerus.methods import select_plan
from conftest import disk_node, lsblk_json, part_node


# ---------------------------------------------------------------- predicates


def test_label_predicate_is_exact():
    assert is_boot_medium_label("BEERUS_KEY") is True
    assert is_boot_medium_label("  BEERUS_KEY  ") is True
    assert is_boot_medium_label("beerus_key") is False
    assert is_boot_medium_label("BEERUS_KE") is False
    assert is_boot_medium_label("BEERUS_KEY2") is False
    assert is_boot_medium_label("") is False
    assert is_boot_medium_label(None) is False


def test_live_medium_mountpoint_predicate():
    assert is_live_medium_mountpoint("/run/live/medium") is True
    assert is_live_medium_mountpoint("/run/live/medium/") is True
    assert is_live_medium_mountpoint("/lib/live/mount/medium") is True
    assert is_live_medium_mountpoint("/live/medium") is True
    assert is_live_medium_mountpoint("/") is False
    assert is_live_medium_mountpoint("/run") is False
    assert is_live_medium_mountpoint("/media/user/BEERUS_KEY") is False
    assert is_live_medium_mountpoint("") is False
    assert is_live_medium_mountpoint(None) is False


# ---------------------------------------------------------------- marker file


def _marker_text(**overrides) -> str:
    marker = {
        "image": IMAGE_MARKER_FORMAT,
        "version": "0.5.0",
        "volume_label": LIVE_VOLUME_LABEL,
        "documents_dir": "/var/log/beerus/certificates",
        "built": "2026-10-20",
    }
    marker.update(overrides)
    return json.dumps(marker)


def test_load_image_marker_accepts_the_real_shape(tmp_path):
    path = tmp_path / "live-image.json"
    path.write_text(_marker_text(), encoding="utf-8")
    marker = load_image_marker(str(path))
    assert marker is not None
    assert marker["image"] == IMAGE_MARKER_FORMAT
    assert marker["volume_label"] == LIVE_VOLUME_LABEL


def test_load_image_marker_refuses_anything_else(tmp_path):
    missing = tmp_path / "nope.json"
    assert load_image_marker(str(missing)) is None

    malformed = tmp_path / "bad.json"
    malformed.write_text("{not json", encoding="utf-8")
    assert load_image_marker(str(malformed)) is None

    not_an_object = tmp_path / "list.json"
    not_an_object.write_text('["beerus-live-image"]', encoding="utf-8")
    assert load_image_marker(str(not_an_object)) is None

    other_image = tmp_path / "other.json"
    other_image.write_text(_marker_text(image="someone-elses-image"),
                           encoding="utf-8")
    assert load_image_marker(str(other_image)) is None


def test_load_image_marker_opener_errors_are_none():
    def opener(path, mode, encoding=None):
        raise OSError("no medium")

    assert load_image_marker("/etc/beerus/live-image.json", opener=opener) is None


def test_marker_documents_dir_rules():
    # Memory-backed homes inside a live system are honored.
    assert marker_documents_dir({"documents_dir": "/var/log/job"}) == "/var/log/job"
    assert marker_documents_dir({"documents_dir": "/run/job-docs"}) == "/run/job-docs"
    assert marker_documents_dir({"documents_dir": "/tmp/docs"}) == "/tmp/docs"
    assert (
        marker_documents_dir({"documents_dir": DEFAULT_DOCUMENTS_DIR})
        == DEFAULT_DOCUMENTS_DIR
    )
    # Everything else falls back: mounted drives, the key itself,
    # devices, traversal, relative and padded paths.
    for refused in (
        "/mnt/records",
        "/run/live/medium/certificates",
        "/lib/live/mount/medium/docs",
        "/dev/sdb",
        "/",
        "/var/log/../../etc/records",
        "relative/path",
        " /padded",
    ):
        assert marker_documents_dir({"documents_dir": refused}) == (
            DEFAULT_DOCUMENTS_DIR
        ), refused
    assert marker_documents_dir({}) == DEFAULT_DOCUMENTS_DIR
    assert marker_documents_dir(None) == DEFAULT_DOCUMENTS_DIR
    assert marker_documents_dir({"documents_dir": 42}) == DEFAULT_DOCUMENTS_DIR


def test_load_image_marker_requires_the_images_own_label(tmp_path):
    """M4 regression: the label stamped in the marker must be
    this image's label — it is the agreement the protection
    design stands on."""
    wrong = tmp_path / "wrong.json"
    wrong.write_text(_marker_text(volume_label="WRONG"), encoding="utf-8")
    assert load_image_marker(str(wrong)) is None

    missing = tmp_path / "missing.json"
    payload = json.loads(_marker_text())
    del payload["volume_label"]
    missing.write_text(json.dumps(payload), encoding="utf-8")
    assert load_image_marker(str(missing)) is None


# ------------------------------------------------------- inventory integration


def _disk_with_children(name="sdb", children=None, **kwargs):
    return disk_node(name, children=children, **kwargs)


def test_labelled_partition_marks_whole_disk_boot_unmounted():
    """The Phase 1 blind spot: the key's partition is labelled but
    never mounted — the disk must still be untouchable."""
    esp = part_node("sdb1", mountpoints=[None])
    esp["label"] = LIVE_VOLUME_LABEL
    text = lsblk_json([_disk_with_children(children=[esp])])
    (drive,) = parse_lsblk_json(text)
    assert drive.is_boot is True


def test_label_on_the_disk_itself_marks_boot():
    node = _disk_with_children()
    node["label"] = LIVE_VOLUME_LABEL
    (drive,) = parse_lsblk_json(lsblk_json([node]))
    assert drive.is_boot is True


def test_label_on_a_nested_descendant_marks_boot():
    inner = part_node("dm-0", mountpoints=[None])
    inner["label"] = LIVE_VOLUME_LABEL
    outer = part_node("sdb2", mountpoints=[None])
    outer["children"] = [inner]
    text = lsblk_json([_disk_with_children(children=[outer])])
    (drive,) = parse_lsblk_json(text)
    assert drive.is_boot is True


def test_live_medium_mountpoint_marks_boot():
    medium = part_node("sdb1", mountpoints=["/run/live/medium"])
    text = lsblk_json([_disk_with_children(children=[medium])])
    (drive,) = parse_lsblk_json(text)
    assert drive.is_boot is True


def test_ordinary_label_and_mount_do_not_mark_boot():
    data = part_node("sdb1", mountpoints=["/media/user/DATA"])
    data["label"] = "DATA"
    text = lsblk_json([_disk_with_children(children=[data])])
    (drive,) = parse_lsblk_json(text)
    assert drive.is_boot is False


def test_lsblk_columns_include_label_and_wwn():
    assert "LABEL" in LSBLK_ARGV[-1]
    assert "WWN" in LSBLK_ARGV[-1]


def test_parse_survives_hostile_field_types():
    """L1 regression: wrong-typed lsblk fields are treated as
    absent, never as a crash."""
    node = disk_node("sdb", children=[])
    node["children"] = 42
    (drive,) = parse_lsblk_json(lsblk_json([node]))
    assert drive.is_boot is False

    weird_mount = part_node("sdb1")
    weird_mount["mountpoints"] = [123, {"x": 1}, "/run/live/medium"]
    (drive,) = parse_lsblk_json(
        lsblk_json([disk_node("sdb", children=[weird_mount])])
    )
    assert drive.is_boot is True  # the real string entry still counts

    assert parse_lsblk_json('{"blockdevices": 42}') == []


def test_identity_strings_are_cleaned_of_control_characters():
    """M2 regression: a crafted model/serial must not be able to
    forge console lines or smuggle escape sequences — control
    characters are removed at parse time."""
    node = disk_node(
        "sdb",
        model="GoodDisk\n  1. FAKE\x1b[31m",
        serial="SER\nIAL\x1b[0m",
    )
    (drive,) = parse_lsblk_json(lsblk_json([node]))
    assert "\n" not in drive.model
    assert "\x1b" not in drive.model
    assert "\n" not in drive.serial
    assert "\x1b" not in drive.serial
    assert drive.model == "GoodDisk  1. FAKE[31m"
    assert drive.serial == "SERIAL[0m"


def test_interface_string_is_cleaned_too():
    """Round-3 regression: tran/interface is identity-adjacent —
    it gets the same control-character cleaning."""
    node = disk_node("sdb", tran="sa\nta\x1b")
    (drive,) = parse_lsblk_json(lsblk_json([node]))
    assert drive.interface == "sata"


# ------------------------------------------------------- the layers agree


def test_labelled_drive_is_refused_by_assessment_and_gates():
    esp = part_node("sdb1", mountpoints=[None])
    esp["label"] = LIVE_VOLUME_LABEL
    text = lsblk_json([_disk_with_children(children=[esp])])
    (drive,) = parse_lsblk_json(text)

    assessment = assess_target(drive)
    assert assessment.eligible is False
    assert "booted from" in assessment.reason

    authority = GateAuthority()
    with pytest.raises(BootDeviceError):
        authority.begin_session(drive, select_plan(drive))
