"""Method-selection tests — planning only, nothing is ever executed."""

from __future__ import annotations

import pytest

from beerus.errors import MethodUnavailableError, UnidentifiableDriveError
from beerus.inventory import DriveClass
from beerus.methods import OVERWRITE_PASSES, MethodKind, select_plan
from beerus.progress import EventKind, ProgressEvent
from conftest import make_drive, make_nvme, make_ssd, make_unidentifiable


def test_nvme_gets_sanitize_purge():
    drive = make_nvme()
    plan = select_plan(drive)
    assert plan.nist_category == "Purge"
    assert [step.kind for step in plan.steps] == [MethodKind.NVME_SANITIZE]
    assert plan.steps[0].label == "NVMe Sanitize"
    assert plan.passes == ()
    assert plan.drive_fingerprint == drive.fingerprint


def test_sata_ssd_gets_ata_secure_erase_purge():
    drive = make_ssd()
    plan = select_plan(drive)
    assert plan.nist_category == "Purge"
    assert [step.kind for step in plan.steps] == [MethodKind.ATA_SECURE_ERASE]
    assert plan.steps[0].label == "ATA Secure Erase"
    assert plan.passes == ()
    assert plan.drive_fingerprint == drive.fingerprint


def test_sata_hdd_gets_four_pass_overwrite_clear():
    drive = make_drive()  # default fixture is a SATA HDD
    assert drive.drive_class is DriveClass.SATA_HDD
    plan = select_plan(drive)
    assert plan.nist_category == "Clear"
    assert [step.kind for step in plan.steps] == [MethodKind.OVERWRITE]
    assert [p.pattern for p in plan.passes] == ["zero", "one", "random", "zero"]
    assert plan.passes == OVERWRITE_PASSES
    assert plan.drive_fingerprint == drive.fingerprint


def test_unidentifiable_drive_is_refused():
    with pytest.raises(UnidentifiableDriveError):
        select_plan(make_unidentifiable())


def test_unidentifiable_check_comes_before_method_choice():
    # Even an NVMe-shaped drive is refused if it cannot be identified.
    with pytest.raises(UnidentifiableDriveError):
        select_plan(make_nvme(model="", serial=""))


@pytest.mark.parametrize(
    "drive_class",
    [DriveClass.SATA_UNKNOWN, DriveClass.USB, DriveClass.UNKNOWN],
)
def test_unsafe_or_unknown_classes_are_refused(drive_class):
    drive = make_drive(drive_class=drive_class)
    with pytest.raises(MethodUnavailableError):
        select_plan(drive)


def test_refusal_messages_are_plain_and_honest():
    drive = make_drive(drive_class=DriveClass.USB)
    with pytest.raises(MethodUnavailableError) as excinfo:
        select_plan(drive)
    message = str(excinfo.value)
    assert "Nothing was wiped" in message
    assert excinfo.value.user_message == message


# ------------------------------------------------------------- progress
# ProgressEvent.percent: derived only from real byte counters.

def test_progress_percent_is_none_without_a_total():
    event = ProgressEvent(kind=EventKind.PROGRESS, message="Working", bytes_done=10, bytes_total=0)
    assert event.percent is None


def test_progress_percent_at_half():
    event = ProgressEvent(
        kind=EventKind.PROGRESS,
        message="Halfway",
        step_label="Overwrite the whole drive",
        bytes_done=500,
        bytes_total=1000,
    )
    assert event.percent == 50.0


def test_progress_percent_rounds_to_one_decimal():
    event = ProgressEvent(kind=EventKind.PROGRESS, message="Third", bytes_done=1, bytes_total=3)
    assert event.percent == 33.3
