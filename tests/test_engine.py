"""Engine tests — end-to-end against synthetic drives and files.

SAFETY: enumerators are lambdas returning synthetic Drive objects;
the only executor that ever writes is FileExecutor pointed at a
small regular file under tmp_path. No real device is touched.
"""

from __future__ import annotations

import pytest

from beerus.engine import WipeEngine, assess_target
from beerus.errors import (
    BeerusError,
    BootDeviceError,
    RefusalError,
    TokenError,
    UnidentifiableDriveError,
    WipeFailedError,
)
from beerus.executor import ExecutionReport, FileExecutor, RealExecutor
from beerus.gates import GateAuthority
from beerus.inventory import DriveClass
from beerus.methods import select_plan
from beerus.progress import EventKind
from conftest import (
    MIB,
    make_boot_drive,
    make_drive,
    make_nvme,
    make_unidentifiable,
)

SIZE = 2 * MIB
SENTINEL = b"\xaa"


class RecordingExecutor:
    """Records every call; optionally delegates to a real executor
    or raises a scripted error."""

    def __init__(self, delegate=None, error=None):
        self.calls: list = []
        self._delegate = delegate
        self._error = error

    def execute(self, drive, plan, emit):
        self.calls.append((drive, plan))
        if self._error is not None:
            raise self._error
        if self._delegate is not None:
            return self._delegate.execute(drive, plan, emit)
        return ExecutionReport(plan.method_label, 0, (), "recorded")


def _full_token(authority, drive):
    plan = select_plan(drive)
    session = authority.begin_session(drive, plan)
    session.confirm_identity(drive.serial)
    session.acknowledge_scope()
    return session.final_confirmation(f"ERASE {drive.serial}")


def _filled_file(fake_device_file, size=SIZE):
    path, actual_size = fake_device_file(size)
    with open(path, "wb") as fh:
        fh.write(SENTINEL * actual_size)
    return path, actual_size


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


# ------------------------------------------------------------------ happy path


def test_engine_wipe_end_to_end_with_file_executor(fake_device_file):
    path, size = _filled_file(fake_device_file)
    drive = make_drive(size_bytes=size)
    authority = GateAuthority()
    token = _full_token(authority, drive)
    executor = RecordingExecutor(delegate=FileExecutor(path))
    engine = WipeEngine(authority, executor, enumerator=lambda: [drive])

    events = []
    result = engine.wipe(drive, token, emit=events.append)

    assert result.ok is True
    assert result.nist_category == "Clear"
    assert result.method_label == select_plan(drive).method_label
    assert result.drive_fingerprint == drive.fingerprint
    assert result.bytes_written == size * 4
    assert result.completed_at  # ISO timestamp present
    assert _read(path) == b"\x00" * size
    assert len(executor.calls) == 1

    kinds = [e.kind for e in events]
    assert EventKind.SCOPE_ANNOUNCED in kinds
    assert EventKind.PROGRESS in kinds
    assert kinds.index(EventKind.SCOPE_ANNOUNCED) < kinds.index(EventKind.PROGRESS)
    assert kinds[-1] is EventKind.COMPLETED


def test_engine_wipe_defaults_to_noop_emit(fake_device_file):
    path, size = _filled_file(fake_device_file)
    drive = make_drive(size_bytes=size)
    authority = GateAuthority()
    token = _full_token(authority, drive)
    engine = WipeEngine(
        authority, FileExecutor(path), enumerator=lambda: [drive]
    )
    result = engine.wipe(drive, token)  # no emit argument at all
    assert result.ok is True


# ------------------------------------------------------------------ token is a hard gate


def test_engine_bogus_token_never_reaches_executor(fake_device_file):
    path, size = _filled_file(fake_device_file)
    drive = make_drive(size_bytes=size)
    other = make_drive(serial="WD-SOMEONE-ELSE", device_path="/dev/sdz")
    authority = GateAuthority()
    bogus_token = _full_token(authority, other)

    executor = RecordingExecutor(delegate=FileExecutor(path))
    engine = WipeEngine(authority, executor, enumerator=lambda: [drive])

    with pytest.raises(TokenError):
        engine.wipe(drive, bogus_token, emit=lambda e: None)

    assert executor.calls == []
    assert _read(path) == SENTINEL * size  # untouched


def test_engine_token_reuse_rejected(fake_device_file):
    path, size = _filled_file(fake_device_file)
    drive = make_drive(size_bytes=size)
    authority = GateAuthority()
    token = _full_token(authority, drive)
    executor = RecordingExecutor(delegate=FileExecutor(path))
    engine = WipeEngine(authority, executor, enumerator=lambda: [drive])

    first = engine.wipe(drive, token)
    assert first.ok is True
    with pytest.raises(TokenError):
        engine.wipe(drive, token)
    assert len(executor.calls) == 1  # second attempt never executed


# ------------------------------------------------------------------ fresh re-read checks


def test_engine_identity_swap_at_same_path_refused():
    confirmed = make_drive(serial="WD-CONFIRMED-ONE")
    swapped = make_drive(serial="WD-SWAPPED-TWO")  # same /dev/sdb path
    authority = GateAuthority()
    token = _full_token(authority, confirmed)
    executor = RecordingExecutor()
    engine = WipeEngine(authority, executor, enumerator=lambda: [swapped])

    with pytest.raises(RefusalError) as excinfo:
        engine.wipe(confirmed, token)
    # The plain identity RefusalError (step 2), not a token error.
    assert excinfo.type is RefusalError
    assert executor.calls == []


def test_engine_boot_drive_refused_before_token_use():
    boot = make_boot_drive()
    normal = make_drive(device_path="/dev/sdz", serial="WD-NORMAL-ONE")
    authority = GateAuthority()
    token = _full_token(authority, normal)  # any token; never reached
    executor = RecordingExecutor()
    engine = WipeEngine(authority, executor, enumerator=lambda: [boot])

    with pytest.raises(BootDeviceError):
        engine.wipe(boot, token)
    assert executor.calls == []


def test_engine_vanished_drive_refused():
    drive = make_drive()
    authority = GateAuthority()
    token = _full_token(authority, drive)
    executor = RecordingExecutor()
    engine = WipeEngine(authority, executor, enumerator=lambda: [])

    with pytest.raises(RefusalError):
        engine.wipe(drive, token)
    assert executor.calls == []


def test_engine_unidentifiable_drive_refused():
    drive = make_unidentifiable()
    normal = make_drive(device_path="/dev/sdz", serial="WD-NORMAL-ONE")
    authority = GateAuthority()
    token = _full_token(authority, normal)  # any token; never reached
    executor = RecordingExecutor()
    engine = WipeEngine(authority, executor, enumerator=lambda: [drive])

    with pytest.raises(UnidentifiableDriveError):
        engine.wipe(drive, token)
    assert executor.calls == []


# ------------------------------------------------------------------ failure reporting


def test_engine_executor_failure_emits_failed_and_reraises(fake_device_file):
    path, size = _filled_file(fake_device_file)
    drive = make_drive(size_bytes=size)
    authority = GateAuthority()
    token = _full_token(authority, drive)
    failure = WipeFailedError(
        "The wipe did not finish.", detail="synthetic failure"
    )
    executor = RecordingExecutor(error=failure)
    engine = WipeEngine(authority, executor, enumerator=lambda: [drive])

    events = []
    with pytest.raises(WipeFailedError):
        engine.wipe(drive, token, emit=events.append)

    kinds = [e.kind for e in events]
    assert EventKind.FAILED in kinds
    assert EventKind.SCOPE_ANNOUNCED in kinds
    assert kinds.index(EventKind.SCOPE_ANNOUNCED) < kinds.index(EventKind.FAILED)
    assert EventKind.COMPLETED not in kinds


# ------------------------------------------------------------------ listing / assessment


def test_engine_list_drives_and_assess_all():
    hdd = make_drive()
    boot = make_boot_drive(device_path="/dev/sdz", serial="WD-BOOT-ONE")
    authority = GateAuthority()
    engine = WipeEngine(
        authority, RecordingExecutor(), enumerator=lambda: [hdd, boot]
    )
    assert engine.list_drives() == [hdd, boot]
    assessments = engine.assess_all()
    assert [a.eligible for a in assessments] == [True, False]
    assert assessments[0].reason is None
    assert assessments[1].reason is not None


def test_assess_target_boot():
    assessment = assess_target(make_boot_drive())
    assert assessment.eligible is False
    assert assessment.reason is not None


def test_assess_target_unidentifiable():
    assessment = assess_target(make_unidentifiable())
    assert assessment.eligible is False
    assert assessment.reason is not None


def test_assess_target_usb_not_eligible_yet():
    usb = make_drive(drive_class=DriveClass.USB, interface="usb")
    assessment = assess_target(usb)
    assert assessment.eligible is False
    assert assessment.reason is not None


def test_assess_target_normal_hdd_eligible():
    assessment = assess_target(make_drive())
    assert assessment.eligible is True
    assert assessment.reason is None


# ------------------------------------------------- actual-method reporting (F1)


def test_engine_nvme_fallback_reports_format_as_actual_method(monkeypatch):
    monkeypatch.setenv(RealExecutor.ARM_ENV_VAR, "1")
    drive = make_nvme()
    authority = GateAuthority()
    token = _full_token(authority, drive)

    def runner(argv):
        if argv[1] == "sanitize":
            return (1, "")
        if argv[1] == "sanitize-log":
            return (1, "")  # log read fails: no evidence of activity
        return (0, "")

    executor = RealExecutor(armed=True, runner=runner)
    engine = WipeEngine(authority, executor, enumerator=lambda: [drive])

    events = []
    result = engine.wipe(drive, token, emit=events.append)

    assert result.ok is True
    assert "Format" in result.method_label
    assert result.nist_category == "Purge"  # category stays the plan's
    completed = [e for e in events if e.kind is EventKind.COMPLETED]
    assert completed and "Format" in completed[-1].message


# ------------------------------------------------------------ WWN clone (F12)


def test_engine_wwn_change_at_same_path_refused():
    confirmed = make_drive(wwn="0x5000cca000000001")
    clone = make_drive(wwn="0x5000cca000000002")  # same path/model/serial/size
    authority = GateAuthority()
    token = _full_token(authority, confirmed)
    executor = RecordingExecutor()
    engine = WipeEngine(authority, executor, enumerator=lambda: [clone])

    with pytest.raises(RefusalError) as excinfo:
        engine.wipe(confirmed, token)
    assert excinfo.type is RefusalError
    assert executor.calls == []


# ------------------------------------------------------- enumerator wrap (F9d)


def test_engine_enumerator_failure_wrapped_plain():
    drive = make_drive()
    authority = GateAuthority()
    token = _full_token(authority, drive)
    executor = RecordingExecutor()

    def bad_enumerator():
        raise RuntimeError("synthetic enumerator failure")

    engine = WipeEngine(authority, executor, enumerator=bad_enumerator)
    with pytest.raises(BeerusError) as excinfo:
        engine.wipe(drive, token)
    assert "could not read the drive list" in str(excinfo.value)
    assert not isinstance(excinfo.value, RuntimeError)
    assert executor.calls == []
