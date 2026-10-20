"""Executor tests — synthetic targets only.

SAFETY: the FileExecutor is only ever pointed at small regular files
under tmp_path (via the fake_device_file fixture), plus one stat-only
check against /dev/null which is refused BEFORE any open. The
RealExecutor is only ever run with a recording fake runner, except
for its overwrite path against a regular tmp file. No test sends a
destructive command to a real device.
"""

from __future__ import annotations

import os

import pytest

from beerus.commands import (
    ata_secure_erase_commands,
    nvme_format_command,
    nvme_sanitize_command,
    nvme_sanitize_log_command,
)
from beerus.errors import (
    BootDeviceError,
    ExecutorNotArmedError,
    MethodUnavailableError,
    RefusalError,
    UnidentifiableDriveError,
    UnsafeTargetError,
    WipeFailedError,
)
from beerus.executor import FileExecutor, RealExecutor, run_overwrite
from beerus.methods import MethodKind, PassSpec, WipePlan, WipeStep, select_plan
from beerus.progress import EventKind
from conftest import MIB, make_boot_drive, make_drive, make_nvme, make_ssd, make_unidentifiable

SIZE = 2 * MIB


def _overwrite_plan(drive, passes):
    return WipePlan(
        drive_fingerprint=drive.fingerprint,
        method_label="Test overwrite",
        nist_category="Clear",
        steps=(WipeStep(MethodKind.OVERWRITE, "Overwrite the whole drive", "test"),),
        passes=passes,
    )


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def _no_emit(event):
    pass


# ------------------------------------------------------------------ FileExecutor


def test_file_executor_one_pass_fills_with_ff(fake_device_file):
    path, size = fake_device_file(SIZE)
    drive = make_drive(size_bytes=size)
    plan = _overwrite_plan(drive, (PassSpec("one", "Write ones"),))
    report = FileExecutor(path).execute(drive, plan, _no_emit)
    assert _read(path) == b"\xff" * size
    assert report.bytes_written == size
    assert report.method_label == plan.method_label
    assert report.steps_completed == ("Overwrite the whole drive",)


def test_file_executor_zero_pass_fills_with_00(fake_device_file):
    path, size = fake_device_file(SIZE)
    # Pre-fill with non-zero data so the zero pass is what does the work.
    with open(path, "wb") as fh:
        fh.write(b"\xaa" * size)
    drive = make_drive(size_bytes=size)
    plan = _overwrite_plan(drive, (PassSpec("zero", "Write zeros"),))
    FileExecutor(path).execute(drive, plan, _no_emit)
    assert _read(path) == b"\x00" * size


def test_file_executor_random_pass_is_non_uniform(fake_device_file):
    path, size = fake_device_file(SIZE)
    drive = make_drive(size_bytes=size)
    plan = _overwrite_plan(drive, (PassSpec("random", "Write random data"),))
    FileExecutor(path).execute(drive, plan, _no_emit)
    data = _read(path)
    assert len(data) == size
    assert not all(b == 0 for b in data)
    assert len(set(data)) > 1


def test_file_executor_full_hdd_plan_ends_all_zeros(fake_device_file):
    path, size = fake_device_file(SIZE)
    with open(path, "wb") as fh:
        fh.write(b"\xaa" * size)
    drive = make_drive(size_bytes=size)
    plan = select_plan(drive)  # zero, one, random, zero (final)
    report = FileExecutor(path).execute(drive, plan, _no_emit)
    assert _read(path) == b"\x00" * size
    assert report.bytes_written == size * len(plan.passes)


def test_file_executor_emits_progress_with_real_totals(fake_device_file):
    path, size = fake_device_file(SIZE)
    drive = make_drive(size_bytes=size)
    plan = _overwrite_plan(drive, (PassSpec("zero", "Write zeros"),))
    events = []
    FileExecutor(path).execute(drive, plan, events.append)

    progress = [e for e in events if e.kind is EventKind.PROGRESS]
    assert progress, "expected PROGRESS events"
    assert all(e.bytes_total == size for e in progress)
    assert progress[-1].bytes_done == size
    kinds = [e.kind for e in events]
    assert kinds[0] is EventKind.STEP_STARTED
    assert kinds[-1] is EventKind.STEP_COMPLETED


def test_file_executor_size_mismatch_refused(fake_device_file):
    path, size = fake_device_file(SIZE)
    drive = make_drive(size_bytes=size + 1)
    plan = _overwrite_plan(drive, (PassSpec("zero", "Write zeros"),))
    with pytest.raises(UnsafeTargetError):
        FileExecutor(path).execute(drive, plan, _no_emit)


def test_file_executor_dev_null_refused_before_open():
    # /dev/null is a character device: the stat check must refuse it
    # before any open. This test never writes to it by construction.
    drive = make_drive(size_bytes=SIZE)
    plan = _overwrite_plan(drive, (PassSpec("zero", "Write zeros"),))
    with pytest.raises(UnsafeTargetError):
        FileExecutor("/dev/null").execute(drive, plan, _no_emit)


def test_file_executor_fingerprint_mismatch_refused(fake_device_file):
    path, size = fake_device_file(SIZE)
    drive = make_drive(size_bytes=size)
    plan = WipePlan(
        drive_fingerprint="0" * 64,
        method_label="Wrong drive plan",
        nist_category="Clear",
        steps=(WipeStep(MethodKind.OVERWRITE, "Overwrite", "test"),),
        passes=(PassSpec("zero", "Write zeros"),),
    )
    with pytest.raises(RefusalError):
        FileExecutor(path).execute(drive, plan, _no_emit)
    assert _read(path) == b"\x00" * size  # untouched (sparse zeros)


def test_file_executor_rejects_nvme_plan(fake_device_file):
    path, _size = fake_device_file(16 * MIB)
    drive = make_nvme()
    plan = select_plan(drive)
    with pytest.raises(MethodUnavailableError):
        FileExecutor(path).execute(drive, plan, _no_emit)


# ------------------------------------------------------------------ RealExecutor arming


class RecordingRunner:
    """Fake command runner: records argv, returns scripted results.

    Scripted results may be plain return codes or (returncode, stdout)
    tuples; plain codes are normalized to (code, "") so the runner
    always honours the real runner contract.
    """

    def __init__(self, results=0):
        self.calls: list[list[str]] = []
        # int/tuple -> always that result; callable(argv) -> result;
        # list -> pop the next result
        self._results = results

    def __call__(self, argv):
        self.calls.append(list(argv))
        if callable(self._results):
            result = self._results(argv)
        elif isinstance(self._results, list):
            result = self._results.pop(0)
        else:
            result = self._results
        if isinstance(result, tuple):
            return result
        return (result, "")


def test_real_executor_disarmed_by_default_makes_no_calls():
    runner = RecordingRunner(0)
    executor = RealExecutor(runner=runner)
    drive = make_nvme()
    with pytest.raises(ExecutorNotArmedError):
        executor.execute(drive, select_plan(drive), _no_emit)
    assert runner.calls == []


def test_real_executor_armed_flag_without_env_still_refuses(monkeypatch):
    monkeypatch.delenv(RealExecutor.ARM_ENV_VAR, raising=False)
    runner = RecordingRunner(0)
    executor = RealExecutor(armed=True, runner=runner)
    drive = make_nvme()
    with pytest.raises(ExecutorNotArmedError):
        executor.execute(drive, select_plan(drive), _no_emit)
    assert runner.calls == []


def test_real_executor_env_without_armed_flag_still_refuses(monkeypatch):
    monkeypatch.setenv(RealExecutor.ARM_ENV_VAR, "1")
    runner = RecordingRunner(0)
    executor = RealExecutor(armed=False, runner=runner)
    drive = make_nvme()
    with pytest.raises(ExecutorNotArmedError):
        executor.execute(drive, select_plan(drive), _no_emit)
    assert runner.calls == []


# ------------------------------------------------------------------ RealExecutor armed (fake runner only)


def test_real_executor_armed_nvme_sanitize_exact_argv(monkeypatch):
    monkeypatch.setenv(RealExecutor.ARM_ENV_VAR, "1")
    runner = RecordingRunner(0)
    executor = RealExecutor(armed=True, runner=runner)
    drive = make_nvme()
    plan = select_plan(drive)
    report = executor.execute(drive, plan, _no_emit)
    assert runner.calls == [nvme_sanitize_command(drive.device_path)]
    assert report.bytes_written == 0  # firmware erase: no byte counter
    assert report.method_label == plan.method_label
    assert "Sanitize" in report.method_label


def test_real_executor_sanitize_fallback_to_format(monkeypatch):
    monkeypatch.setenv(RealExecutor.ARM_ENV_VAR, "1")

    def results(argv):
        return 1 if argv[1] == "sanitize" else 0

    runner = RecordingRunner(results)
    executor = RealExecutor(armed=True, runner=runner)
    drive = make_nvme()
    report = executor.execute(drive, select_plan(drive), _no_emit)
    # The sanitize-log check runs between the refused Sanitize and
    # the fallback Format; it reports no activity here (rc 0, no
    # progress output), so the fallback is allowed.
    assert runner.calls == [
        nvme_sanitize_command(drive.device_path),
        nvme_sanitize_log_command(drive.device_path),
        nvme_format_command(drive.device_path),
    ]
    assert "fallback" in report.detail.lower()
    assert "Format" in report.method_label


def test_real_executor_sanitize_log_failure_still_falls_back(monkeypatch):
    monkeypatch.setenv(RealExecutor.ARM_ENV_VAR, "1")

    def results(argv):
        # Sanitize refused; the log read itself fails (rc 1), which
        # is not evidence of sanitize activity -> fallback proceeds.
        return (1, "") if argv[1] in ("sanitize", "sanitize-log") else (0, "")

    runner = RecordingRunner(results)
    executor = RealExecutor(armed=True, runner=runner)
    drive = make_nvme()
    report = executor.execute(drive, select_plan(drive), _no_emit)
    assert runner.calls == [
        nvme_sanitize_command(drive.device_path),
        nvme_sanitize_log_command(drive.device_path),
        nvme_format_command(drive.device_path),
    ]
    assert report.method_label == (
        "NVMe Format — used because the drive refused Sanitize"
    )


def test_real_executor_sanitize_in_progress_never_sends_format(monkeypatch):
    monkeypatch.setenv(RealExecutor.ARM_ENV_VAR, "1")

    def results(argv):
        if argv[1] == "sanitize":
            return (1, "")
        if argv[1] == "sanitize-log":
            return (0, "Sanitize Progress (SPROG) : 50")
        return (0, "")

    runner = RecordingRunner(results)
    executor = RealExecutor(armed=True, runner=runner)
    drive = make_nvme()
    with pytest.raises(WipeFailedError) as excinfo:
        executor.execute(drive, select_plan(drive), _no_emit)
    assert "second erase" in excinfo.value.user_message
    # Format was NEVER sent: only sanitize + sanitize-log ran.
    assert runner.calls == [
        nvme_sanitize_command(drive.device_path),
        nvme_sanitize_log_command(drive.device_path),
    ]


def test_real_executor_sanitize_and_format_both_fail(monkeypatch):
    monkeypatch.setenv(RealExecutor.ARM_ENV_VAR, "1")
    runner = RecordingRunner(1)
    executor = RealExecutor(armed=True, runner=runner)
    drive = make_nvme()
    with pytest.raises(WipeFailedError) as excinfo:
        executor.execute(drive, select_plan(drive), _no_emit)
    assert "both the Sanitize and Format" in excinfo.value.user_message
    assert runner.calls == [
        nvme_sanitize_command(drive.device_path),
        nvme_sanitize_log_command(drive.device_path),
        nvme_format_command(drive.device_path),
    ]


def test_real_executor_armed_ata_secure_erase_argv(monkeypatch):
    monkeypatch.setenv(RealExecutor.ARM_ENV_VAR, "1")
    runner = RecordingRunner(0)
    executor = RealExecutor(armed=True, runner=runner)
    drive = make_ssd()
    report = executor.execute(drive, select_plan(drive), _no_emit)
    assert runner.calls == ata_secure_erase_commands(drive.device_path)
    assert report.bytes_written == 0


def test_real_executor_ata_failure_raises_wipe_failed(monkeypatch):
    monkeypatch.setenv(RealExecutor.ARM_ENV_VAR, "1")
    runner = RecordingRunner(1)
    executor = RealExecutor(armed=True, runner=runner)
    drive = make_ssd()
    with pytest.raises(WipeFailedError):
        executor.execute(drive, select_plan(drive), _no_emit)


def test_real_executor_ata_first_command_failure_message(monkeypatch):
    monkeypatch.setenv(RealExecutor.ARM_ENV_VAR, "1")
    runner = RecordingRunner([(1, "")])
    executor = RealExecutor(armed=True, runner=runner)
    drive = make_ssd()
    with pytest.raises(WipeFailedError) as excinfo:
        executor.execute(drive, select_plan(drive), _no_emit)
    assert "setup command" in excinfo.value.user_message
    assert "Nothing was erased" in excinfo.value.user_message
    assert excinfo.value.detail == "hdparm --security-set-pass exited 1"
    assert len(runner.calls) == 1  # erase command never sent


def test_real_executor_ata_second_command_failure_mentions_password(monkeypatch):
    monkeypatch.setenv(RealExecutor.ARM_ENV_VAR, "1")
    runner = RecordingRunner([(0, ""), (1, "")])
    executor = RealExecutor(armed=True, runner=runner)
    drive = make_ssd()
    with pytest.raises(WipeFailedError) as excinfo:
        executor.execute(drive, select_plan(drive), _no_emit)
    message = excinfo.value.user_message
    assert "temporary password" in message
    assert '"beerus"' in message
    assert "data is unchanged" in message
    assert excinfo.value.detail == (
        "hdparm --security-erase exited 1 after --security-set-pass succeeded"
    )
    assert len(runner.calls) == 2


def test_real_executor_armed_overwrite_against_regular_file_only(
    monkeypatch, fake_device_file
):
    """The real overwrite writer, exercised against a tmp file only."""
    monkeypatch.setenv(RealExecutor.ARM_ENV_VAR, "1")
    path, size = fake_device_file(SIZE)
    with open(path, "wb") as fh:
        fh.write(b"\xaa" * size)
    runner = RecordingRunner(0)
    executor = RealExecutor(armed=True, runner=runner)
    # device_path IS the tmp regular file — never a real device.
    drive = make_drive(device_path=path, size_bytes=size)
    report = executor.execute(drive, select_plan(drive), _no_emit)
    assert _read(path) == b"\x00" * size  # final pass is zeros
    assert report.bytes_written == size * 4
    assert runner.calls == []  # overwrite path runs no external command


def test_run_overwrite_write_failure_wrapped_plain(tmp_path):
    path = tmp_path / "readonly.img"
    path.write_bytes(b"\x00" * 1024)
    fd = os.open(str(path), os.O_RDONLY)
    try:
        with pytest.raises(WipeFailedError) as excinfo:
            run_overwrite(
                fd, 1024, (PassSpec("zero", "Write zeros"),), _no_emit, "step"
            )
    finally:
        os.close(fd)
    assert "did NOT finish" in excinfo.value.user_message
    assert excinfo.value.detail.startswith("write failed with errno")


# ------------------------------------------------------- FileExecutor hardening


def test_file_executor_symlink_target_refused_and_content_unchanged(
    tmp_path, fake_device_file
):
    path, size = fake_device_file(SIZE)
    with open(path, "wb") as fh:
        fh.write(b"\xaa" * size)
    link = tmp_path / "linked-drive.img"
    link.symlink_to(path)
    drive = make_drive(size_bytes=size)
    plan = _overwrite_plan(drive, (PassSpec("zero", "Write zeros"),))
    with pytest.raises(UnsafeTargetError):
        FileExecutor(str(link)).execute(drive, plan, _no_emit)
    assert _read(path) == b"\xaa" * size  # linked file untouched


def test_file_executor_missing_file_is_plain_unsafe_target(tmp_path):
    missing = str(tmp_path / "no-such-file.img")
    drive = make_drive(size_bytes=SIZE)
    plan = _overwrite_plan(drive, (PassSpec("zero", "Write zeros"),))
    with pytest.raises(UnsafeTargetError) as excinfo:
        FileExecutor(missing).execute(drive, plan, _no_emit)
    assert "could not be found" in str(excinfo.value)


def test_file_executor_zero_size_target_refused(tmp_path):
    path = tmp_path / "empty.img"
    path.write_bytes(b"")
    drive = make_drive(size_bytes=0)
    plan = _overwrite_plan(drive, (PassSpec("zero", "Write zeros"),))
    with pytest.raises(UnsafeTargetError) as excinfo:
        FileExecutor(str(path)).execute(drive, plan, _no_emit)
    assert "no size" in str(excinfo.value)


# ------------------------------------------------------- RealExecutor self-checks


def test_real_executor_plan_mismatch_refused_with_no_runner_calls(monkeypatch):
    monkeypatch.setenv(RealExecutor.ARM_ENV_VAR, "1")
    runner = RecordingRunner(0)
    executor = RealExecutor(armed=True, runner=runner)
    drive = make_nvme()
    other_plan = select_plan(make_nvme(serial="S4EVNF0MOTHER999"))
    with pytest.raises(RefusalError):
        executor.execute(drive, other_plan, _no_emit)
    assert runner.calls == []


def test_real_executor_boot_drive_refused_and_file_untouched(
    monkeypatch, fake_device_file
):
    monkeypatch.setenv(RealExecutor.ARM_ENV_VAR, "1")
    path, size = fake_device_file(SIZE)
    with open(path, "wb") as fh:
        fh.write(b"\xaa" * size)
    runner = RecordingRunner(0)
    executor = RealExecutor(armed=True, runner=runner)
    drive = make_boot_drive(device_path=path, size_bytes=size)
    with pytest.raises(BootDeviceError):
        executor.execute(drive, select_plan(drive), _no_emit)
    assert _read(path) == b"\xaa" * size  # untouched
    assert runner.calls == []


def test_real_executor_unidentifiable_drive_refused(monkeypatch):
    monkeypatch.setenv(RealExecutor.ARM_ENV_VAR, "1")
    runner = RecordingRunner(0)
    executor = RealExecutor(armed=True, runner=runner)
    drive = make_unidentifiable()
    plan = _overwrite_plan(drive, (PassSpec("zero", "Write zeros"),))
    with pytest.raises(UnidentifiableDriveError):
        executor.execute(drive, plan, _no_emit)
    assert runner.calls == []
