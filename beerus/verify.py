"""Post-wipe verification for Beerus.

After a wipe finishes, Beerus reads the drive back and checks,
honestly, what it can actually confirm:

- Overwrite (Clear) plans end with a known final-pass pattern, so
  the whole surface can be read back and checked byte-for-byte.
- Firmware (Purge) plans erase inside the drive, where no read-back
  can see everything. Those are verified by the drive's own status
  where one exists (NVMe), plus a sampled read-back that reports
  exactly what was sampled — never implying full coverage.

A verification result is only worth anything if it cannot be
forged, so results are HMAC-signed with a module-level key at mint
time, mirroring the consent gates' philosophy: a hand-built or
edited result does not verify as authentic.

This module only ever *reads*. Surface readers open read-only and
never write to anything.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol

from beerus.commands import nvme_sanitize_log_command
from beerus.errors import UnsafeTargetError
from beerus.executor import CHUNK_SIZE
from beerus.methods import MethodKind
from beerus.progress import EventKind, ProgressEvent

_STEP_LABEL = "Verification"
_SAMPLE_REGIONS = 64
_MAX_REMNANT_OFFSETS = 5


class VerificationVerdict(Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_VERIFIED = "not_verified"


@dataclass(frozen=True)
class VerificationResult:
    verdict: VerificationVerdict
    drive_fingerprint: str
    method_label: str
    nist_category: str
    coverage: str  # "full" | "sampled" | "none"
    bytes_checked: int
    bytes_total: int
    regions_checked: int
    remnants_found: int
    first_remnant_offsets: tuple[int, ...]
    firmware_status: str  # "confirmed" | "not_available" | "unreadable" | "in_progress" | "not_applicable"
    detail: str
    checked_at: str
    mac: str


# ---------------------------------------------------------------------------
# Anti-forgery: results are minted only inside this module, signed
# with a key that exists only for the life of the process.
# ---------------------------------------------------------------------------

_RESULT_KEY = secrets.token_bytes(32)


def _result_mac(**fields) -> str:
    """HMAC-SHA256 over the canonical join of the signed fields."""
    verdict = fields["verdict"]
    verdict_value = verdict.value if isinstance(verdict, Enum) else str(verdict)
    material = "|".join(
        [
            str(fields["drive_fingerprint"]),
            str(fields["method_label"]),
            str(fields["nist_category"]),
            verdict_value,
            str(fields["coverage"]),
            str(fields["bytes_checked"]),
            str(fields["bytes_total"]),
            str(fields["regions_checked"]),
            str(fields["remnants_found"]),
            str(fields["firmware_status"]),
            str(fields["checked_at"]),
            str(fields["detail"]),
            ",".join(str(o) for o in fields["first_remnant_offsets"]),
        ]
    )
    return hmac.new(
        _RESULT_KEY, material.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _mint_result(
    *,
    drive,
    plan,
    verdict,
    coverage,
    bytes_checked,
    regions_checked,
    remnants_found,
    first_remnant_offsets,
    firmware_status,
    detail,
    actual_method_label=None,
) -> VerificationResult:
    """The single internal mint. Every genuine result is born here."""
    method_label = (
        actual_method_label if actual_method_label is not None else plan.method_label
    )
    offsets = tuple(first_remnant_offsets)[:_MAX_REMNANT_OFFSETS]
    checked_at = datetime.now(timezone.utc).isoformat()
    mac = _result_mac(
        drive_fingerprint=drive.fingerprint,
        method_label=method_label,
        nist_category=plan.nist_category,
        verdict=verdict,
        coverage=coverage,
        bytes_checked=bytes_checked,
        bytes_total=drive.size_bytes,
        regions_checked=regions_checked,
        remnants_found=remnants_found,
        firmware_status=firmware_status,
        checked_at=checked_at,
        detail=detail,
        first_remnant_offsets=offsets,
    )
    return VerificationResult(
        verdict=verdict,
        drive_fingerprint=drive.fingerprint,
        method_label=method_label,
        nist_category=plan.nist_category,
        coverage=coverage,
        bytes_checked=bytes_checked,
        bytes_total=drive.size_bytes,
        regions_checked=regions_checked,
        remnants_found=remnants_found,
        first_remnant_offsets=offsets,
        firmware_status=firmware_status,
        detail=detail,
        checked_at=checked_at,
        mac=mac,
    )


def verify_result_authentic(result) -> bool:
    """True only for a result minted by this module and unedited.

    Never raises: a result whose fields have the wrong types (a
    verdict that is a plain string, a MAC that is bytes, a byte
    count that is text, ...) is simply not authentic.
    """
    if not isinstance(result, VerificationResult):
        return False
    if not isinstance(result.verdict, VerificationVerdict):
        return False
    if not isinstance(result.mac, str):
        return False
    for value in (
        result.bytes_checked,
        result.bytes_total,
        result.regions_checked,
        result.remnants_found,
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            return False
    for value in (
        result.drive_fingerprint,
        result.method_label,
        result.nist_category,
        result.coverage,
        result.firmware_status,
        result.detail,
        result.checked_at,
    ):
        if not isinstance(value, str):
            return False
    if not isinstance(result.first_remnant_offsets, tuple):
        return False
    for offset in result.first_remnant_offsets:
        if isinstance(offset, bool) or not isinstance(offset, int):
            return False
    try:
        expected = _result_mac(
            drive_fingerprint=result.drive_fingerprint,
            method_label=result.method_label,
            nist_category=result.nist_category,
            verdict=result.verdict,
            coverage=result.coverage,
            bytes_checked=result.bytes_checked,
            bytes_total=result.bytes_total,
            regions_checked=result.regions_checked,
            remnants_found=result.remnants_found,
            firmware_status=result.firmware_status,
            checked_at=result.checked_at,
            detail=result.detail,
            first_remnant_offsets=result.first_remnant_offsets,
        )
        return hmac.compare_digest(result.mac, expected)
    except Exception:
        return False


def unverified_result(drive, plan, detail, actual_method_label=None) -> VerificationResult:
    """A genuine NOT_VERIFIED result: verification could not be done."""
    return _mint_result(
        drive=drive,
        plan=plan,
        verdict=VerificationVerdict.NOT_VERIFIED,
        coverage="none",
        bytes_checked=0,
        regions_checked=0,
        remnants_found=0,
        first_remnant_offsets=(),
        firmware_status="not_available",
        detail=detail,
        actual_method_label=actual_method_label,
    )


# ---------------------------------------------------------------------------
# Surface readers (read-only; mirror FileExecutor's discipline)
# ---------------------------------------------------------------------------


class SurfaceReader(Protocol):
    """Anything the verifier can read a wiped surface back from."""

    size_bytes: int

    def read_at(self, offset: int, length: int) -> bytes:
        ...

    def close(self) -> None:
        ...


def _pread(fd: int, length: int, offset: int) -> bytes:
    """os.pread where the platform has it (POSIX); on Windows,
    which has no pread, a seek + read on the reader's own
    descriptor — the readers are single-descriptor and
    sequential by design, so the fallback is exact."""
    if hasattr(os, "pread"):
        return os.pread(fd, length, offset)
    os.lseek(fd, offset, os.SEEK_SET)
    return os.read(fd, length)


class FileSurfaceReader:
    """Read-only surface over a regular file (synthetic targets).

    Same three-part discipline as FileExecutor: stat before open,
    open with O_NOFOLLOW, fstat re-verification of the descriptor.
    """

    def __init__(self, path):
        self._path = path
        try:
            st = os.stat(path)
        except FileNotFoundError:
            raise UnsafeTargetError(
                "The target file could not be found. Nothing was read."
            ) from None
        except OSError:
            raise UnsafeTargetError(
                "The target file could not be checked. Nothing was read."
            ) from None
        if not stat.S_ISREG(st.st_mode):
            raise UnsafeTargetError(
                "The target is not a regular file. The file-backed "
                "reader only ever reads regular files."
            )
        try:
            fd = os.open(
                path,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_BINARY", 0),
            )
        except OSError:
            raise UnsafeTargetError(
                "The target file could not be opened for reading."
            ) from None
        fst = os.fstat(fd)
        if not stat.S_ISREG(fst.st_mode):
            os.close(fd)
            raise UnsafeTargetError(
                "The target is not a regular file. The file-backed "
                "reader only ever reads regular files."
            )
        self._fd: int | None = fd
        self.size_bytes = fst.st_size

    def read_at(self, offset: int, length: int) -> bytes:
        if self._fd is None:
            raise OSError("The surface is closed.")
        return _pread(self._fd, length, offset)

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


class DeviceSurfaceReader:
    """Read-only surface over a device path.

    The path comes from inventory and has already passed the gates,
    exactly as RealExecutor trusts drive paths. This reader only ever
    opens read-only and only ever reads.
    """

    def __init__(self, device_path, size_bytes):
        self._device_path = device_path
        self.size_bytes = int(size_bytes)
        self._fd: int | None = None

    def _open(self) -> int:
        if self._fd is None:
            self._fd = os.open(
                self._device_path,
                os.O_RDONLY | getattr(os, "O_BINARY", 0),
            )
        return self._fd

    def read_at(self, offset: int, length: int) -> bytes:
        return _pread(self._open(), length, offset)

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


# ---------------------------------------------------------------------------
# NVMe firmware status
# ---------------------------------------------------------------------------

_SPROG_RE = re.compile(r"Sanitize Progress \(SPROG\)\s*:\s*(\d+)(?=\s|$)")


def check_nvme_firmware_status(runner, device_path) -> tuple[str, str]:
    """Ask the drive itself whether a sanitize is still running.

    Returns (firmware_status, plain-language detail). SPROG is
    "confirmed" only at exactly 65535; a value above that, or
    output that does not parse cleanly (trailing junk after the
    digits included), is "unreadable" — never a confirmation.
    All parsing happens inside the guard, and a runner that
    returns something unexpected can never raise out of here.
    """
    try:
        argv = nvme_sanitize_log_command(device_path)
        rc, out = runner(argv)
        if rc != 0:
            return (
                "unreadable",
                "The drive's sanitize status could not be read.",
            )
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        elif not isinstance(out, str):
            return (
                "unreadable",
                "The drive's sanitize status could not be understood.",
            )
        match = _SPROG_RE.search(out)
        if match is None:
            return (
                "unreadable",
                "The drive's sanitize status could not be understood.",
            )
        sprog = int(match.group(1))
        if sprog == 65535:
            return (
                "confirmed",
                "The drive's firmware reports no sanitize in progress.",
            )
        if 0 <= sprog < 65535:
            return (
                "in_progress",
                "The drive reports a sanitize is still in progress.",
            )
        return (
            "unreadable",
            "The drive's sanitize status could not be understood.",
        )
    except Exception:
        return (
            "unreadable",
            "The drive's sanitize status could not be read.",
        )


# ---------------------------------------------------------------------------
# The verifier
# ---------------------------------------------------------------------------


def _scan_remnants(data: bytes, expected: int, base_offset: int, found: list) -> int:
    """Count non-matching bytes; record the first few absolute offsets."""
    expected_byte = bytes((expected,))
    non_matching = len(data) - data.count(expected_byte)
    if non_matching and len(found) < _MAX_REMNANT_OFFSETS:
        for i, byte in enumerate(data):
            if byte != expected:
                found.append(base_offset + i)
                if len(found) >= _MAX_REMNANT_OFFSETS:
                    break
    return non_matching


class WipeVerifier:
    """Reads a wiped surface back and reports honestly what it found.

    ``surface_factory`` is a callable drive -> SurfaceReader.
    ``firmware_runner`` is the RealExecutor runner seam (argv ->
    (rc, stdout)) used only to ask NVMe drives their own status.
    """

    def __init__(self, surface_factory, firmware_runner=None):
        self._surface_factory = surface_factory
        self._firmware_runner = firmware_runner

    def verify(
        self, drive, plan, emit=None, actual_method_label=None
    ) -> VerificationResult:
        if emit is None:
            emit = lambda event: None  # noqa: E731 - simple no-op default
        emit(
            ProgressEvent(
                EventKind.STEP_STARTED,
                f"Starting: {_STEP_LABEL}",
                _STEP_LABEL,
                0,
                drive.size_bytes,
            )
        )
        result = self._verify_inner(drive, plan, emit, actual_method_label)
        emit(
            ProgressEvent(
                EventKind.STEP_COMPLETED,
                f"Finished: {_STEP_LABEL}",
                _STEP_LABEL,
                result.bytes_checked,
                result.bytes_total,
            )
        )
        return result

    # ------------------------------------------------------------ internals

    def _verify_inner(
        self, drive, plan, emit, actual_method_label=None
    ) -> VerificationResult:
        try:
            surface = self._surface_factory(drive)
        except Exception:
            return unverified_result(
                drive,
                plan,
                "Verification could not be performed: the wiped "
                "drive could not be opened for reading.",
                actual_method_label=actual_method_label,
            )
        try:
            # The surface size is captured exactly once, here, and
            # passed down: a surface whose reported size changes
            # between reads can never inflate what was checked.
            try:
                size = surface.size_bytes
            except Exception:
                return unverified_result(
                    drive,
                    plan,
                    "Verification could not be performed: the size "
                    "of what could be read could not be determined.",
                    actual_method_label=actual_method_label,
                )
            if size <= 0:
                return unverified_result(
                    drive,
                    plan,
                    "Verification could not be performed: the drive "
                    "reports no size, so verification cannot be "
                    "performed.",
                    actual_method_label=actual_method_label,
                )
            if size != drive.size_bytes:
                return unverified_result(
                    drive,
                    plan,
                    "Verification could not be performed: what could "
                    "be read does not match the size of the drive that "
                    "was wiped, so it cannot be trusted as that drive.",
                    actual_method_label=actual_method_label,
                )
            kinds = {step.kind for step in plan.steps}
            if kinds & {MethodKind.NVME_SANITIZE, MethodKind.NVME_FORMAT}:
                return self._verify_nvme(
                    drive, plan, surface, emit, size, actual_method_label
                )
            if MethodKind.ATA_SECURE_ERASE in kinds:
                return self._verify_sampled(
                    drive,
                    plan,
                    surface,
                    emit,
                    size,
                    firmware_status="not_available",
                    detail_note=(
                        "Beerus cannot read an ATA drive's internal "
                        "erase status in this version, so this result "
                        "rests on the read-back sample alone."
                    ),
                    actual_method_label=actual_method_label,
                )
            if MethodKind.OVERWRITE in kinds:
                return self._verify_overwrite(
                    drive, plan, surface, emit, size, actual_method_label
                )
            return unverified_result(
                drive,
                plan,
                "Verification could not be performed: Beerus does "
                "not know how to verify this kind of wipe.",
                actual_method_label=actual_method_label,
            )
        finally:
            try:
                surface.close()
            except Exception:
                pass

    def _not_verified_read_failure(
        self, drive, plan, *, bytes_checked, regions_checked,
        remnants_found, first_remnant_offsets, firmware_status,
        actual_method_label=None,
    ) -> VerificationResult:
        return _mint_result(
            drive=drive,
            plan=plan,
            verdict=VerificationVerdict.NOT_VERIFIED,
            coverage="none",
            bytes_checked=bytes_checked,
            regions_checked=regions_checked,
            remnants_found=remnants_found,
            first_remnant_offsets=first_remnant_offsets,
            firmware_status=firmware_status,
            detail=(
                "Verification could not be completed: reading the "
                f"drive back failed partway through. {bytes_checked} "
                f"of {drive.size_bytes} bytes were checked before "
                "the read failed, so Beerus cannot say the drive is "
                "clean."
            ),
            actual_method_label=actual_method_label,
        )

    def _verify_overwrite(
        self, drive, plan, surface, emit, size, actual_method_label=None
    ) -> VerificationResult:
        if not plan.passes:
            return unverified_result(
                drive,
                plan,
                "Verification could not be performed: the wipe plan "
                "records no overwrite passes to check against.",
                actual_method_label=actual_method_label,
            )
        try:
            final_pattern = plan.passes[-1].pattern
        except Exception:
            return _mint_result(
                drive=drive,
                plan=plan,
                verdict=VerificationVerdict.NOT_VERIFIED,
                coverage="none",
                bytes_checked=0,
                regions_checked=0,
                remnants_found=0,
                first_remnant_offsets=(),
                firmware_status="not_applicable",
                detail=(
                    "Verification could not be performed: the wipe "
                    "plan does not record a final overwrite pass "
                    "Beerus can check against."
                ),
                actual_method_label=actual_method_label,
            )
        if final_pattern == "random":
            return _mint_result(
                drive=drive,
                plan=plan,
                verdict=VerificationVerdict.NOT_VERIFIED,
                coverage="none",
                bytes_checked=0,
                regions_checked=0,
                remnants_found=0,
                first_remnant_offsets=(),
                firmware_status="not_applicable",
                detail=(
                    "Verification could not be performed: the final "
                    "overwrite pass wrote random data, and random data "
                    "cannot be confirmed byte-for-byte afterwards."
                ),
                actual_method_label=actual_method_label,
            )
        if final_pattern not in ("zero", "one"):
            return _mint_result(
                drive=drive,
                plan=plan,
                verdict=VerificationVerdict.NOT_VERIFIED,
                coverage="none",
                bytes_checked=0,
                regions_checked=0,
                remnants_found=0,
                first_remnant_offsets=(),
                firmware_status="not_applicable",
                detail=(
                    "Verification could not be performed: the final "
                    "overwrite pass used a pattern Beerus does not "
                    "know how to check."
                ),
                actual_method_label=actual_method_label,
            )
        expected = 0x00 if final_pattern == "zero" else 0xFF

        bytes_checked = 0
        regions_checked = 0
        remnants_found = 0
        first_offsets: list[int] = []
        offset = 0
        while offset < size:
            length = min(CHUNK_SIZE, size - offset)
            try:
                data = surface.read_at(offset, length)
            except Exception:
                return self._not_verified_read_failure(
                    drive, plan,
                    bytes_checked=bytes_checked,
                    regions_checked=regions_checked,
                    remnants_found=remnants_found,
                    first_remnant_offsets=first_offsets,
                    firmware_status="not_applicable",
                    actual_method_label=actual_method_label,
                )
            if not data:
                return self._not_verified_read_failure(
                    drive, plan,
                    bytes_checked=bytes_checked,
                    regions_checked=regions_checked,
                    remnants_found=remnants_found,
                    first_remnant_offsets=first_offsets,
                    firmware_status="not_applicable",
                    actual_method_label=actual_method_label,
                )
            if len(data) > length:
                # A read that returns MORE than was asked for cannot
                # be trusted: those bytes are never counted.
                return self._not_verified_read_failure(
                    drive, plan,
                    bytes_checked=bytes_checked,
                    regions_checked=regions_checked,
                    remnants_found=remnants_found,
                    first_remnant_offsets=first_offsets,
                    firmware_status="not_applicable",
                    actual_method_label=actual_method_label,
                )
            remnants_found += _scan_remnants(data, expected, offset, first_offsets)
            bytes_checked += len(data)
            regions_checked += 1
            offset += len(data)
            emit(
                ProgressEvent(
                    EventKind.PROGRESS,
                    f"Verification: checked {bytes_checked} of {size} bytes.",
                    _STEP_LABEL,
                    bytes_checked,
                    size,
                )
            )
            if len(data) < length:
                return self._not_verified_read_failure(
                    drive, plan,
                    bytes_checked=bytes_checked,
                    regions_checked=regions_checked,
                    remnants_found=remnants_found,
                    first_remnant_offsets=first_offsets,
                    firmware_status="not_applicable",
                    actual_method_label=actual_method_label,
                )

        if remnants_found:
            return _mint_result(
                drive=drive,
                plan=plan,
                verdict=VerificationVerdict.FAIL,
                coverage="full",
                bytes_checked=bytes_checked,
                regions_checked=regions_checked,
                remnants_found=remnants_found,
                first_remnant_offsets=first_offsets,
                firmware_status="not_applicable",
                detail=(
                    f"Verification failed: {remnants_found} bytes did "
                    "not match the final overwrite pass and may still "
                    f"hold old data. The first was found at byte "
                    f"{first_offsets[0]}. The whole drive was checked "
                    f"({bytes_checked} of {size} bytes)."
                ),
                actual_method_label=actual_method_label,
            )
        return _mint_result(
            drive=drive,
            plan=plan,
            verdict=VerificationVerdict.PASS,
            coverage="full",
            bytes_checked=bytes_checked,
            regions_checked=regions_checked,
            remnants_found=0,
            first_remnant_offsets=(),
            firmware_status="not_applicable",
            detail=(
                f"Verification passed: the whole drive was read back "
                f"({bytes_checked} of {size} bytes) and every byte "
                "matches the final overwrite pass."
            ),
            actual_method_label=actual_method_label,
        )

    def _verify_nvme(
        self, drive, plan, surface, emit, size, actual_method_label=None
    ) -> VerificationResult:
        if self._firmware_runner is None:
            return _mint_result(
                drive=drive,
                plan=plan,
                verdict=VerificationVerdict.NOT_VERIFIED,
                coverage="none",
                bytes_checked=0,
                regions_checked=0,
                remnants_found=0,
                first_remnant_offsets=(),
                firmware_status="unreadable",
                detail=(
                    "Verification could not be performed: the drive's "
                    "own sanitize status could not be checked, because "
                    "Beerus was given no way to ask the drive."
                ),
                actual_method_label=actual_method_label,
            )
        status, fw_detail = check_nvme_firmware_status(
            self._firmware_runner, drive.device_path
        )
        if status == "in_progress":
            return _mint_result(
                drive=drive,
                plan=plan,
                verdict=VerificationVerdict.FAIL,
                coverage="none",
                bytes_checked=0,
                regions_checked=0,
                remnants_found=0,
                first_remnant_offsets=(),
                firmware_status="in_progress",
                detail=(
                    "Verification failed: the drive itself reports "
                    "that its sanitize has not finished, so the "
                    "destruction is not complete."
                ),
                actual_method_label=actual_method_label,
            )
        if status != "confirmed":
            return _mint_result(
                drive=drive,
                plan=plan,
                verdict=VerificationVerdict.NOT_VERIFIED,
                coverage="none",
                bytes_checked=0,
                regions_checked=0,
                remnants_found=0,
                first_remnant_offsets=(),
                firmware_status="unreadable",
                detail=(
                    "Verification could not be performed: "
                    + fw_detail
                ),
                actual_method_label=actual_method_label,
            )
        return self._verify_sampled(
            drive,
            plan,
            surface,
            emit,
            size,
            firmware_status="confirmed",
            detail_note=fw_detail,
            actual_method_label=actual_method_label,
        )

    def _verify_sampled(
        self, drive, plan, surface, emit, size, *, firmware_status,
        detail_note, actual_method_label=None
    ) -> VerificationResult:
        """Sampled read-back for firmware (Purge) wipes.

        Every sampled byte must be 0x00. Only bytes in regions not
        already covered by an earlier region are read and counted,
        so overlapping regions never double-count.
        """
        chunk = CHUNK_SIZE
        if size <= chunk:
            region_offsets = [0]
        else:
            raw = [
                (i * (size - chunk)) // (_SAMPLE_REGIONS - 1)
                for i in range(_SAMPLE_REGIONS)
            ]
            seen: set[int] = set()
            region_offsets = []
            for off in raw:
                if off not in seen:
                    seen.add(off)
                    region_offsets.append(off)

        covered: list[tuple[int, int]] = []  # merged, sorted [start, end)
        bytes_checked = 0
        regions_checked = 0
        remnants_found = 0
        first_offsets: list[int] = []

        for region_offset in region_offsets:
            region_end = min(size, region_offset + chunk)
            # Uncovered sub-intervals of this region.
            uncovered: list[tuple[int, int]] = []
            cursor = region_offset
            for cov_start, cov_end in covered:
                if cov_end <= cursor:
                    continue
                if cov_start >= region_end:
                    break
                if cov_start > cursor:
                    uncovered.append((cursor, min(cov_start, region_end)))
                cursor = max(cursor, cov_end)
                if cursor >= region_end:
                    break
            if cursor < region_end:
                uncovered.append((cursor, region_end))
            if not uncovered:
                continue

            read_any = False
            for start, end in uncovered:
                try:
                    data = surface.read_at(start, end - start)
                except Exception:
                    return self._not_verified_read_failure(
                        drive, plan,
                        bytes_checked=bytes_checked,
                        regions_checked=regions_checked,
                        remnants_found=remnants_found,
                        first_remnant_offsets=first_offsets,
                        firmware_status=firmware_status,
                        actual_method_label=actual_method_label,
                    )
                if not data:
                    return self._not_verified_read_failure(
                        drive, plan,
                        bytes_checked=bytes_checked,
                        regions_checked=regions_checked,
                        remnants_found=remnants_found,
                        first_remnant_offsets=first_offsets,
                        firmware_status=firmware_status,
                        actual_method_label=actual_method_label,
                    )
                if len(data) > end - start:
                    # More bytes than were asked for cannot be
                    # trusted: they are never counted.
                    return self._not_verified_read_failure(
                        drive, plan,
                        bytes_checked=bytes_checked,
                        regions_checked=regions_checked,
                        remnants_found=remnants_found,
                        first_remnant_offsets=first_offsets,
                        firmware_status=firmware_status,
                        actual_method_label=actual_method_label,
                    )
                remnants_found += _scan_remnants(data, 0x00, start, first_offsets)
                bytes_checked += len(data)
                read_any = True
                covered.append((start, start + len(data)))
                covered.sort()
                merged: list[tuple[int, int]] = []
                for s, e in covered:
                    if merged and s <= merged[-1][1]:
                        merged[-1] = (merged[-1][0], max(merged[-1][1], e))
                    else:
                        merged.append((s, e))
                covered = merged
                if len(data) < end - start:
                    return self._not_verified_read_failure(
                        drive, plan,
                        bytes_checked=bytes_checked,
                        regions_checked=regions_checked,
                        remnants_found=remnants_found,
                        first_remnant_offsets=first_offsets,
                        firmware_status=firmware_status,
                        actual_method_label=actual_method_label,
                    )
            if read_any:
                regions_checked += 1
                emit(
                    ProgressEvent(
                        EventKind.PROGRESS,
                        f"Verification: checked {bytes_checked} of "
                        f"{size} bytes in sampled regions.",
                        _STEP_LABEL,
                        bytes_checked,
                        size,
                    )
                )

        sample_description = (
            f"Beerus checked {bytes_checked} bytes in "
            f"{regions_checked} sampled regions out of {size} total "
            "bytes — this was a sample, not a full read of the "
            "whole drive."
        )
        if remnants_found:
            return _mint_result(
                drive=drive,
                plan=plan,
                verdict=VerificationVerdict.FAIL,
                coverage="sampled",
                bytes_checked=bytes_checked,
                regions_checked=regions_checked,
                remnants_found=remnants_found,
                first_remnant_offsets=first_offsets,
                firmware_status=firmware_status,
                detail=(
                    f"Verification failed: {remnants_found} bytes "
                    "that should have been erased were found still "
                    f"holding data. The first was found at byte "
                    f"{first_offsets[0]}. {sample_description} "
                    f"{detail_note}"
                ),
                actual_method_label=actual_method_label,
            )
        return _mint_result(
            drive=drive,
            plan=plan,
            verdict=VerificationVerdict.PASS,
            coverage="sampled",
            bytes_checked=bytes_checked,
            regions_checked=regions_checked,
            remnants_found=0,
            first_remnant_offsets=(),
            firmware_status=firmware_status,
            detail=(
                "Verification passed on the sampled areas: every "
                f"sampled byte was zero. {sample_description} "
                f"{detail_note}"
            ),
            actual_method_label=actual_method_label,
        )
