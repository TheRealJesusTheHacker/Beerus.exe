"""The Beerus wipe engine.

The order of checks in WipeEngine.wipe IS the safety story: the
drive is re-read fresh, its identity is compared against the
confirmed drive, and the confirmation token is verified and
consumed — all before the executor can be reached at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from beerus.errors import (
    BeerusError,
    BootDeviceError,
    MethodUnavailableError,
    RefusalError,
    UnidentifiableDriveError,
    WipeFailedError,
)
from beerus.gates import build_scope_text
from beerus.inventory import Drive, enumerate_drives, find_drive
from beerus.methods import select_plan
from beerus.progress import EventKind, ProgressEvent
from beerus.verify import VerificationResult, unverified_result

_UNIDENTIFIABLE_MESSAGE = (
    "Beerus could not identify this drive — its model or serial number "
    "could not be read — so it will not wipe it."
)

_BOOT_MESSAGE = "This is the drive Beerus booted from. It can never be wiped."


@dataclass(frozen=True)
class TargetAssessment:
    drive: Drive
    eligible: bool
    reason: str | None


def assess_target(drive) -> TargetAssessment:
    if drive.is_boot:
        return TargetAssessment(drive, False, _BOOT_MESSAGE)
    if not drive.identifiable:
        return TargetAssessment(drive, False, _UNIDENTIFIABLE_MESSAGE)
    try:
        select_plan(drive)
    except MethodUnavailableError as e:
        return TargetAssessment(drive, False, e.user_message)
    return TargetAssessment(drive, True, None)


@dataclass(frozen=True)
class WipeResult:
    ok: bool
    drive_fingerprint: str
    method_label: str
    nist_category: str
    bytes_written: int
    detail: str
    completed_at: str
    started_at: str = ""
    verification: VerificationResult | None = None


class WipeEngine:
    def __init__(self, authority, executor, enumerator=enumerate_drives, verifier=None):
        self._authority = authority
        self._executor = executor
        self._enumerator = enumerator
        self._verifier = verifier

    def list_drives(self):
        return list(self._enumerator())

    def assess_all(self):
        return [assess_target(d) for d in self.list_drives()]

    def wipe(self, target, token, emit=None) -> WipeResult:
        if emit is None:
            emit = lambda event: None  # noqa: E731 - simple no-op default
        started_at = datetime.now(timezone.utc).isoformat()

        # 1. Re-read the drives fresh; never trust the earlier listing.
        try:
            fresh = self._enumerator()
        except BeerusError:
            raise
        except Exception:
            raise BeerusError(
                "Beerus could not read the drive list. Nothing was wiped."
            ) from None
        current = find_drive(fresh, target.device_path)
        if current is None:
            raise RefusalError(
                "That drive is no longer connected. Nothing was wiped."
            )
        # 2. The drive now at that path must be the confirmed drive.
        if current.fingerprint != target.fingerprint:
            raise RefusalError(
                "The drive now connected there is not the drive that "
                "was confirmed. Nothing was wiped — check the drives "
                "and start again."
            )
        # 3. The boot drive is never a target.
        if current.is_boot:
            raise BootDeviceError(_BOOT_MESSAGE)
        # 4. The drive must be identifiable.
        if not current.identifiable:
            raise UnidentifiableDriveError(_UNIDENTIFIABLE_MESSAGE)
        # 5. Choose the wipe plan for the drive as it is right now.
        plan = select_plan(current)
        # 6. Verify and consume the confirmation token. The executor
        #    is unreachable before this line.
        self._authority.verify_and_consume(token, current, plan)
        # 7. Announce the full scope.
        emit(ProgressEvent(EventKind.SCOPE_ANNOUNCED, build_scope_text(current, plan)))
        # 8. Run the wipe.
        try:
            report = self._executor.execute(current, plan, emit)
        except WipeFailedError as e:
            emit(ProgressEvent(EventKind.FAILED, e.user_message))
            raise
        # 9. Announce completion.
        emit(
            ProgressEvent(
                EventKind.COMPLETED,
                f"Wipe complete. {report.method_label} finished for "
                f"{current.model} (serial {current.serial}).",
            )
        )
        # 10. Verify the wipe, if a verifier was wired in. A
        #     verification failure never turns a finished wipe into
        #     a crash, and never claims a verification it didn't get.
        verification = None
        if self._verifier is not None:
            try:
                verification = self._verifier.verify(
                    current,
                    plan,
                    emit,
                    actual_method_label=report.method_label,
                )
            except Exception:
                verification = unverified_result(
                    current,
                    plan,
                    "The wipe finished, but verification could not "
                    "be completed.",
                    actual_method_label=report.method_label,
                )
                emit(
                    ProgressEvent(
                        EventKind.FAILED,
                        "The wipe finished, but verification could "
                        "not be completed.",
                    )
                )
        # 11. Report honestly what was done.
        return WipeResult(
            True,
            current.fingerprint,
            report.method_label,
            plan.nist_category,
            report.bytes_written,
            report.detail,
            datetime.now(timezone.utc).isoformat(),
            started_at=started_at,
            verification=verification,
        )
