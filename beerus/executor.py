"""Executors for Beerus wipe plans.

FileExecutor writes only to regular files (synthetic targets). Its
guarantee is a stat check before opening, opening with O_NOFOLLOW,
and an fstat re-verification of the opened descriptor — so a block
or character device, or a symlink swapped in after the check, can
never pass.

RealExecutor ships DISARMED. It refuses to run unless it was both
constructed with armed=True AND the BEERUS_REAL_WIPE_ARMED
environment variable is set to "1" at execute time. Arming belongs
to the boot-image entry point, never to tests.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from dataclasses import dataclass

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
from beerus.methods import MethodKind
from beerus.progress import EventKind, ProgressEvent

CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class ExecutionReport:
    method_label: str
    bytes_written: int
    steps_completed: tuple[str, ...]
    detail: str


def _pattern_chunk(pattern: str, length: int) -> bytes:
    if pattern == "zero":
        return b"\x00" * length
    if pattern == "one":
        return b"\xff" * length
    if pattern == "random":
        return os.urandom(length)
    raise ValueError(f"Unknown overwrite pattern: {pattern!r}")


def run_overwrite(fd, size_bytes, passes, emit, step_label) -> int:
    """Write every pass over an open file descriptor.

    Returns the total bytes written across all passes.
    """
    if size_bytes <= 0:
        raise UnsafeTargetError("The target reports no size. Nothing was written.")
    total_written = 0
    for i, spec in enumerate(passes):
        os.lseek(fd, 0, os.SEEK_SET)
        done_in_pass = 0
        while done_in_pass < size_bytes:
            chunk_len = min(CHUNK_SIZE, size_bytes - done_in_pass)
            chunk = _pattern_chunk(spec.pattern, chunk_len)
            view = memoryview(chunk)
            while view:
                try:
                    written = os.write(fd, view)
                except OSError as e:
                    raise WipeFailedError(
                        "Writing to the target failed partway through. "
                        "The wipe did NOT finish.",
                        detail=f"write failed with errno {e.errno}",
                    ) from None
                if written <= 0:
                    raise OSError("Writing to the target made no progress.")
                view = view[written:]
            done_in_pass += chunk_len
            total_written += chunk_len
            emit(
                ProgressEvent(
                    EventKind.PROGRESS,
                    f"Pass {i + 1} of {len(passes)}: {spec.label}",
                    step_label,
                    done_in_pass,
                    size_bytes,
                )
            )
        os.fsync(fd)
    return total_written


class FileExecutor:
    """Executor for synthetic, file-backed targets only.

    The safety guarantee is three parts together: a stat check of
    the target before opening, opening with O_NOFOLLOW so a symlink
    can never be followed, and an fstat re-verification of the
    opened descriptor before anything is written.
    """

    def __init__(self, target_path: str):
        self.target_path = target_path

    def execute(self, drive, plan, emit) -> ExecutionReport:
        if plan.drive_fingerprint != drive.fingerprint:
            raise RefusalError(
                "The wipe plan does not match this drive. Nothing was written."
            )
        overwrite_steps = [
            step for step in plan.steps if step.kind is MethodKind.OVERWRITE
        ]
        if len(plan.steps) != 1 or len(overwrite_steps) != 1 or not plan.passes:
            raise MethodUnavailableError(
                "The file-backed target can only run an overwrite plan."
            )
        step = overwrite_steps[0]

        # This stat check MUST run before any open: a block or
        # character device can never be a regular file, so it can
        # never pass here.
        try:
            st = os.stat(self.target_path)
        except FileNotFoundError:
            raise UnsafeTargetError(
                "The target file could not be found. Nothing was written."
            ) from None
        except OSError:
            raise UnsafeTargetError(
                "The target file could not be checked. Nothing was written."
            ) from None
        if not stat.S_ISREG(st.st_mode):
            raise UnsafeTargetError(
                "The target is not a regular file. The file-backed "
                "executor only ever writes to regular files — never "
                "to a device."
            )
        if st.st_size != drive.size_bytes:
            raise UnsafeTargetError(
                "The target file's size does not match the drive "
                "record. Nothing was written."
            )

        try:
            fd = os.open(
                self.target_path,
                os.O_WRONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_BINARY", 0),
            )
        except OSError:
            raise UnsafeTargetError(
                "The target file could not be opened for wiping. "
                "Nothing was written."
            ) from None
        # Re-verify the opened descriptor itself: the path could have
        # been swapped between the stat above and this open.
        fst = os.fstat(fd)
        if not stat.S_ISREG(fst.st_mode):
            os.close(fd)
            raise UnsafeTargetError(
                "The target is not a regular file. The file-backed "
                "executor only ever writes to regular files — never "
                "to a device."
            )
        if fst.st_size != drive.size_bytes:
            os.close(fd)
            raise UnsafeTargetError(
                "The target file's size does not match the drive "
                "record. Nothing was written."
            )
        try:
            emit(
                ProgressEvent(
                    EventKind.STEP_STARTED,
                    f"Starting: {step.label}",
                    step.label,
                    0,
                    drive.size_bytes,
                )
            )
            total = run_overwrite(
                fd, drive.size_bytes, plan.passes, emit, step.label
            )
        finally:
            os.close(fd)
        emit(
            ProgressEvent(
                EventKind.STEP_COMPLETED,
                f"Finished: {step.label}",
                step.label,
                drive.size_bytes,
                drive.size_bytes,
            )
        )
        return ExecutionReport(
            plan.method_label,
            total,
            (step.label,),
            "Overwrite completed on the file-backed target.",
        )


def _default_runner(argv) -> tuple[int, str]:
    completed = subprocess.run(argv, capture_output=True, text=True)
    return (completed.returncode, completed.stdout)


class RealExecutor:
    """Executor for real drives. Ships DISARMED (see module docs)."""

    ARM_ENV_VAR = "BEERUS_REAL_WIPE_ARMED"

    def __init__(self, armed: bool = False, runner=None):
        self.armed = armed
        self._runner = runner if runner is not None else _default_runner

    def execute(self, drive, plan, emit) -> ExecutionReport:
        if not (self.armed and os.environ.get(self.ARM_ENV_VAR) == "1"):
            raise ExecutorNotArmedError(
                "The real-drive executor is not armed, so nothing was "
                "sent to any drive. Arming belongs to the boot-image "
                "entry point, never to tests."
            )
        if plan.drive_fingerprint != drive.fingerprint:
            raise RefusalError(
                "The wipe plan does not match this drive. Nothing was written."
            )
        if drive.is_boot:
            raise BootDeviceError(
                "This is the drive Beerus booted from. It can never be wiped."
            )
        if not drive.identifiable:
            raise UnidentifiableDriveError(
                "Beerus could not identify this drive — its model or "
                "serial number could not be read — so it will not wipe it."
            )

        total_bytes = 0
        completed_labels: list[str] = []
        details: list[str] = []
        actual_method_label = plan.method_label

        for step in plan.steps:
            if step.kind is MethodKind.OVERWRITE:
                emit(
                    ProgressEvent(
                        EventKind.STEP_STARTED,
                        f"Starting: {step.label}",
                        step.label,
                        0,
                        drive.size_bytes,
                    )
                )
                fd = os.open(
                    drive.device_path,
                    os.O_WRONLY | getattr(os, "O_BINARY", 0),
                )
                try:
                    written = run_overwrite(
                        fd, drive.size_bytes, plan.passes, emit, step.label
                    )
                finally:
                    os.close(fd)
                total_bytes += written
                emit(
                    ProgressEvent(
                        EventKind.STEP_COMPLETED,
                        f"Finished: {step.label}",
                        step.label,
                        drive.size_bytes,
                        drive.size_bytes,
                    )
                )
                details.append("Overwrite passes completed on the drive.")

            elif step.kind is MethodKind.ATA_SECURE_ERASE:
                emit(
                    ProgressEvent(
                        EventKind.STEP_STARTED,
                        f"Starting: {step.label}",
                        step.label,
                    )
                )
                for index, argv in enumerate(
                    ata_secure_erase_commands(drive.device_path)
                ):
                    rc, _out = self._runner(argv)
                    if rc != 0:
                        if index == 0:
                            raise WipeFailedError(
                                "The drive refused the secure erase setup "
                                "command. Nothing was erased.",
                                detail=f"hdparm --security-set-pass exited {rc}",
                            )
                        raise WipeFailedError(
                            "The erase did not run, but the setup step set "
                            'a temporary password on the drive: "beerus". '
                            "The drive's data is unchanged. That password "
                            "is the standard temporary one for this "
                            "operation and is printed in the Beerus source.",
                            detail=(
                                f"hdparm --security-erase exited {rc} "
                                "after --security-set-pass succeeded"
                            ),
                        )
                emit(
                    ProgressEvent(
                        EventKind.STEP_COMPLETED,
                        f"Finished: {step.label}",
                        step.label,
                    )
                )
                details.append(
                    "The drive's own firmware performed the secure erase "
                    "internally, so there is no byte count for this step."
                )

            elif step.kind is MethodKind.NVME_SANITIZE:
                emit(
                    ProgressEvent(
                        EventKind.STEP_STARTED,
                        f"Starting: {step.label}",
                        step.label,
                    )
                )
                argv = nvme_sanitize_command(drive.device_path)
                rc, _out = self._runner(argv)
                used_fallback = False
                if rc != 0:
                    # Before sending a second erase command, check
                    # whether the drive is already sanitizing: if it
                    # is, a second command must never be sent.
                    log_argv = nvme_sanitize_log_command(drive.device_path)
                    rc2, out2 = self._runner(log_argv)
                    match = re.search(
                        r"Sanitize Progress \(SPROG\)\s*:\s*(\d+)", out2 or ""
                    )
                    activity = (
                        rc2 == 0
                        and match is not None
                        and int(match.group(1)) < 65535
                    )
                    if activity:
                        raise WipeFailedError(
                            "The drive did not accept the Sanitize command "
                            "cleanly, and it reports sanitize activity, so "
                            "Beerus will not send a second erase command. "
                            "Check the drive, then try again.",
                            detail=(
                                f"nvme sanitize exited {rc}; sanitize-log "
                                "reports a sanitize in progress"
                            ),
                        )
                    used_fallback = True
                    actual_method_label = (
                        "NVMe Format — used because the drive refused Sanitize"
                    )
                    fallback_argv = nvme_format_command(drive.device_path)
                    fallback_rc, _fallback_out = self._runner(fallback_argv)
                    if fallback_rc != 0:
                        raise WipeFailedError(
                            "The drive refused both the Sanitize and Format "
                            "commands. Nothing more was sent to it.",
                            detail=(
                                f"nvme sanitize exited {rc}; "
                                f"nvme format exited {fallback_rc}"
                            ),
                        )
                emit(
                    ProgressEvent(
                        EventKind.STEP_COMPLETED,
                        f"Finished: {step.label}",
                        step.label,
                    )
                )
                if used_fallback:
                    details.append(
                        "The drive refused the Sanitize command, so the "
                        "fallback Format command was used instead and "
                        "completed. The drive's own firmware performed "
                        "the erase internally, so there is no byte count "
                        "for this step."
                    )
                else:
                    details.append(
                        "The drive's Sanitize command completed. The "
                        "drive's own firmware performed the erase "
                        "internally, so there is no byte count for this step."
                    )

            elif step.kind is MethodKind.NVME_FORMAT:
                emit(
                    ProgressEvent(
                        EventKind.STEP_STARTED,
                        f"Starting: {step.label}",
                        step.label,
                    )
                )
                argv = nvme_format_command(drive.device_path)
                rc, _out = self._runner(argv)
                if rc != 0:
                    raise WipeFailedError(
                        "The drive refused the format command. "
                        "Nothing more was sent to it.",
                        detail=f"{argv[0]} exited {rc}",
                    )
                emit(
                    ProgressEvent(
                        EventKind.STEP_COMPLETED,
                        f"Finished: {step.label}",
                        step.label,
                    )
                )
                details.append(
                    "The drive's Format command completed. The drive's "
                    "own firmware performed the erase internally, so "
                    "there is no byte count for this step."
                )

            else:  # pragma: no cover - defensive
                raise WipeFailedError(
                    "That wipe step is not supported. Nothing more was "
                    "sent to the drive.",
                    detail=f"Unsupported step kind: {step.kind!r}",
                )

            completed_labels.append(step.label)

        return ExecutionReport(
            actual_method_label,
            total_bytes,
            tuple(completed_labels),
            " ".join(details),
        )
