"""Verification tests — post-wipe read-back, firmware status, authenticity.

SAFETY: everything is synthetic. Surfaces are in-memory byte strings
or small regular files under tmp_path; NVMe firmware status comes
from fake runners only. No real device is ever opened, and the
RealExecutor is never armed.
"""

from __future__ import annotations

import dataclasses

import pytest

from beerus.engine import WipeEngine
from beerus.errors import UnsafeTargetError
from beerus.executor import FileExecutor
from beerus.gates import GateAuthority
from beerus.methods import MethodKind, PassSpec, WipePlan, WipeStep, select_plan
from beerus.progress import EventKind
from beerus.verify import (
    DeviceSurfaceReader,
    FileSurfaceReader,
    VerificationResult,
    VerificationVerdict,
    WipeVerifier,
    check_nvme_firmware_status,
    unverified_result,
    verify_result_authentic,
)
from conftest import MIB, make_drive, make_nvme, make_ssd


class FakeSurface:
    """In-memory surface; optionally raises OSError once a byte
    budget of successful reads has been used up."""

    def __init__(self, data: bytes, fail_after: int | None = None):
        self._data = bytes(data)
        self.size_bytes = len(self._data)
        self._fail_after = fail_after
        self._returned = 0
        self.closed = False

    def read_at(self, offset: int, length: int) -> bytes:
        if self._fail_after is not None and self._returned >= self._fail_after:
            raise OSError("synthetic read failure")
        chunk = self._data[offset : offset + length]
        self._returned += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def _full_token(authority, drive):
    plan = select_plan(drive)
    session = authority.begin_session(drive, plan)
    session.confirm_identity(drive.serial)
    session.acknowledge_scope()
    return session.final_confirmation(f"ERASE {drive.serial}")


def _nvme_runner(rc=0, sprog=None):
    def runner(argv):
        assert argv[0] == "nvme"
        assert argv[1] == "sanitize-log"
        if sprog is None:
            return (rc, "")
        return (rc, f"Sanitize Progress (SPROG)              : {sprog}\n")

    return runner


# ------------------------------------------------------------- end-to-end


def test_engine_wipe_with_verifier_passes_full_coverage(fake_device_file):
    size = 2 * MIB
    path, _ = fake_device_file(size)
    with open(path, "wb") as fh:
        fh.write(b"\xaa" * size)
    drive = make_drive(size_bytes=size)
    authority = GateAuthority()
    token = _full_token(authority, drive)
    verifier = WipeVerifier(lambda d: FileSurfaceReader(path))
    engine = WipeEngine(
        authority,
        FileExecutor(path),
        enumerator=lambda: [drive],
        verifier=verifier,
    )

    events = []
    result = engine.wipe(drive, token, emit=events.append)

    assert result.ok is True
    assert result.started_at  # ISO timestamp captured at entry
    assert result.started_at <= result.completed_at
    verification = result.verification
    assert verification is not None
    assert verification.verdict is VerificationVerdict.PASS
    assert verification.coverage == "full"
    assert verification.bytes_checked == size
    assert verification.bytes_total == size
    assert verification.remnants_found == 0
    assert verification.firmware_status == "not_applicable"
    assert verify_result_authentic(verification) is True

    verify_events = [e for e in events if e.step_label == "Verification"]
    kinds = [e.kind for e in verify_events]
    assert kinds[0] is EventKind.STEP_STARTED
    assert EventKind.PROGRESS in kinds
    assert kinds[-1] is EventKind.STEP_COMPLETED
    progress = [e for e in verify_events if e.kind is EventKind.PROGRESS]
    assert progress[-1].bytes_done == size
    assert progress[-1].bytes_total == size


def test_verifier_events_step_label_and_progress():
    size = 2 * MIB
    drive = make_drive(size_bytes=size)
    plan = select_plan(drive)
    verifier = WipeVerifier(lambda d: FakeSurface(bytes(size)))
    events = []
    result = verifier.verify(drive, plan, emit=events.append)
    assert result.verdict is VerificationVerdict.PASS
    assert [e.kind for e in events][0] is EventKind.STEP_STARTED
    assert events[-1].kind is EventKind.STEP_COMPLETED
    assert all(e.step_label == "Verification" for e in events)


# ------------------------------------------------------------ overwrite leg


def test_half_wiped_surface_fails_with_first_remnant_offset():
    size = 2 * MIB
    half = size // 2
    data = bytes(half) + b"\xaa" * half
    drive = make_drive(size_bytes=size)
    plan = select_plan(drive)
    verifier = WipeVerifier(lambda d: FakeSurface(data))
    result = verifier.verify(drive, plan)
    assert result.verdict is VerificationVerdict.FAIL
    assert result.coverage == "full"
    assert result.bytes_checked == size
    assert result.remnants_found == half
    assert result.first_remnant_offsets[0] == half
    assert len(result.first_remnant_offsets) <= 5
    assert verify_result_authentic(result) is True


def test_surface_factory_raising_is_not_verified():
    drive = make_drive()
    plan = select_plan(drive)

    def bad_factory(d):
        raise RuntimeError("synthetic factory failure")

    result = WipeVerifier(bad_factory).verify(drive, plan)
    assert result.verdict is VerificationVerdict.NOT_VERIFIED
    assert result.coverage == "none"
    assert result.bytes_checked == 0
    assert verify_result_authentic(result) is True


def test_surface_size_mismatch_is_not_verified():
    drive = make_drive(size_bytes=4 * MIB)
    plan = select_plan(drive)
    verifier = WipeVerifier(lambda d: FakeSurface(bytes(2 * MIB)))
    result = verifier.verify(drive, plan)
    assert result.verdict is VerificationVerdict.NOT_VERIFIED
    assert result.bytes_checked == 0


def test_reader_failing_mid_read_is_not_verified_with_honest_bytes():
    size = 4 * MIB
    drive = make_drive(size_bytes=size)
    plan = select_plan(drive)
    surface = FakeSurface(bytes(size), fail_after=MIB)
    result = WipeVerifier(lambda d: surface).verify(drive, plan)
    assert result.verdict is VerificationVerdict.NOT_VERIFIED
    assert result.bytes_checked == MIB
    assert result.bytes_checked > 0


def test_random_final_pass_cannot_be_verified():
    drive = make_drive(size_bytes=2 * MIB)
    plan = WipePlan(
        drive_fingerprint=drive.fingerprint,
        method_label="Custom overwrite",
        nist_category="Clear",
        steps=(
            WipeStep(MethodKind.OVERWRITE, "Overwrite", "synthetic"),
        ),
        passes=(PassSpec("random", "Write random data (final pass)"),),
    )
    surface = FakeSurface(bytes(drive.size_bytes))
    result = WipeVerifier(lambda d: surface).verify(drive, plan)
    assert result.verdict is VerificationVerdict.NOT_VERIFIED
    assert result.bytes_checked == 0
    assert "random" in result.detail


def test_one_final_pass_verifies_ff_surface():
    size = 2 * MIB
    drive = make_drive(size_bytes=size)
    plan = WipePlan(
        drive_fingerprint=drive.fingerprint,
        method_label="Custom overwrite",
        nist_category="Clear",
        steps=(WipeStep(MethodKind.OVERWRITE, "Overwrite", "synthetic"),),
        passes=(PassSpec("one", "Write ones (final pass)"),),
    )
    good = WipeVerifier(lambda d: FakeSurface(b"\xff" * size)).verify(drive, plan)
    assert good.verdict is VerificationVerdict.PASS
    bad = WipeVerifier(lambda d: FakeSurface(bytes(size))).verify(drive, plan)
    assert bad.verdict is VerificationVerdict.FAIL
    assert bad.remnants_found == size


# ----------------------------------------------------------------- ATA leg


def test_ata_all_zero_surface_passes_sampled():
    drive = make_ssd()  # 8 MiB
    plan = select_plan(drive)
    surface = FakeSurface(bytes(drive.size_bytes))
    result = WipeVerifier(lambda d: surface).verify(drive, plan)
    assert result.verdict is VerificationVerdict.PASS
    assert result.coverage == "sampled"
    assert result.firmware_status == "not_available"
    assert "sample" in result.detail


def test_ata_one_nonzero_byte_in_sample_fails_with_offset():
    drive = make_ssd()
    plan = select_plan(drive)
    data = bytearray(drive.size_bytes)
    data[12345] = 0xFF
    surface = FakeSurface(bytes(data))
    result = WipeVerifier(lambda d: surface).verify(drive, plan)
    assert result.verdict is VerificationVerdict.FAIL
    assert result.coverage == "sampled"
    assert result.remnants_found == 1
    assert result.first_remnant_offsets == (12345,)


# ---------------------------------------------------------------- NVMe leg


def test_nvme_confirmed_and_clean_sample_passes():
    drive = make_nvme()
    plan = select_plan(drive)
    surface = FakeSurface(bytes(drive.size_bytes))
    verifier = WipeVerifier(
        lambda d: surface, firmware_runner=_nvme_runner(0, 65535)
    )
    result = verifier.verify(drive, plan)
    assert result.verdict is VerificationVerdict.PASS
    assert result.coverage == "sampled"
    assert result.firmware_status == "confirmed"


def test_nvme_confirmed_but_dirty_sample_fails():
    drive = make_nvme()
    plan = select_plan(drive)
    data = bytearray(drive.size_bytes)
    data[10] = 0xAA
    verifier = WipeVerifier(
        lambda d: FakeSurface(bytes(data)),
        firmware_runner=_nvme_runner(0, 65535),
    )
    result = verifier.verify(drive, plan)
    assert result.verdict is VerificationVerdict.FAIL
    assert result.firmware_status == "confirmed"
    assert result.first_remnant_offsets == (10,)


def test_nvme_sanitize_in_progress_fails():
    drive = make_nvme()
    plan = select_plan(drive)
    verifier = WipeVerifier(
        lambda d: FakeSurface(bytes(drive.size_bytes)),
        firmware_runner=_nvme_runner(0, 100),
    )
    result = verifier.verify(drive, plan)
    assert result.verdict is VerificationVerdict.FAIL
    assert result.firmware_status == "in_progress"
    assert result.bytes_checked == 0


def test_nvme_status_command_failure_is_not_verified():
    drive = make_nvme()
    plan = select_plan(drive)
    verifier = WipeVerifier(
        lambda d: FakeSurface(bytes(drive.size_bytes)),
        firmware_runner=_nvme_runner(1),
    )
    result = verifier.verify(drive, plan)
    assert result.verdict is VerificationVerdict.NOT_VERIFIED
    assert result.firmware_status == "unreadable"


def test_nvme_without_runner_is_not_verified():
    drive = make_nvme()
    plan = select_plan(drive)
    verifier = WipeVerifier(lambda d: FakeSurface(bytes(drive.size_bytes)))
    result = verifier.verify(drive, plan)
    assert result.verdict is VerificationVerdict.NOT_VERIFIED
    assert result.firmware_status == "unreadable"


def test_check_nvme_firmware_status_parsing():
    status, _ = check_nvme_firmware_status(
        _nvme_runner(0, 65535), "/dev/nvme0n1"
    )
    assert status == "confirmed"
    status, _ = check_nvme_firmware_status(_nvme_runner(0, 42), "/dev/nvme0n1")
    assert status == "in_progress"
    status, _ = check_nvme_firmware_status(_nvme_runner(1), "/dev/nvme0n1")
    assert status == "unreadable"
    status, _ = check_nvme_firmware_status(
        lambda argv: (0, "no progress field here"), "/dev/nvme0n1"
    )
    assert status == "unreadable"


# ------------------------------------------------------------ sampling math


def test_sampling_100_mib_checks_exactly_64_regions():
    size = 100 * MIB
    drive = make_ssd(size_bytes=size)
    plan = select_plan(drive)
    surface = FakeSurface(bytes(size))
    result = WipeVerifier(lambda d: surface).verify(drive, plan)
    assert result.verdict is VerificationVerdict.PASS
    assert result.coverage == "sampled"
    assert result.regions_checked == 64
    assert result.bytes_checked == 64 * MIB
    assert result.bytes_total == size


def test_sampling_3_mib_covers_whole_surface_without_double_counting():
    size = 3 * MIB
    drive = make_ssd(size_bytes=size)
    plan = select_plan(drive)
    surface = FakeSurface(bytes(size))
    result = WipeVerifier(lambda d: surface).verify(drive, plan)
    assert result.verdict is VerificationVerdict.PASS
    assert result.coverage == "sampled"
    assert result.bytes_checked == size


def test_sampling_small_drive_single_region():
    size = 512 * 1024  # below one chunk
    drive = make_ssd(size_bytes=size)
    plan = select_plan(drive)
    surface = FakeSurface(bytes(size))
    result = WipeVerifier(lambda d: surface).verify(drive, plan)
    assert result.verdict is VerificationVerdict.PASS
    assert result.regions_checked == 1
    assert result.bytes_checked == size


# -------------------------------------------------------------- authenticity


def test_authenticity_genuine_result_verifies():
    drive = make_drive()
    plan = select_plan(drive)
    genuine = unverified_result(drive, plan, "synthetic")
    assert genuine.verdict is VerificationVerdict.NOT_VERIFIED
    assert genuine.coverage == "none"
    assert genuine.bytes_checked == 0
    assert verify_result_authentic(genuine) is True


def test_authenticity_flipped_verdict_fails():
    drive = make_drive()
    plan = select_plan(drive)
    genuine = unverified_result(drive, plan, "synthetic")
    forged = dataclasses.replace(genuine, verdict=VerificationVerdict.PASS)
    assert verify_result_authentic(forged) is False


def test_authenticity_hand_built_result_fails():
    drive = make_drive()
    hand = VerificationResult(
        verdict=VerificationVerdict.PASS,
        drive_fingerprint=drive.fingerprint,
        method_label="forged",
        nist_category="Clear",
        coverage="full",
        bytes_checked=drive.size_bytes,
        bytes_total=drive.size_bytes,
        regions_checked=1,
        remnants_found=0,
        first_remnant_offsets=(),
        firmware_status="not_applicable",
        detail="trust me",
        checked_at="2026-01-01T00:00:00+00:00",
        mac="",
    )
    assert verify_result_authentic(hand) is False
    assert verify_result_authentic("not a result") is False


# ------------------------------------------------------------------ readers


def test_device_surface_reader_reads_slices(tmp_path):
    path = tmp_path / "fake-device.img"
    path.write_bytes(b"abcdefghij")
    reader = DeviceSurfaceReader(str(path), 10)
    assert reader.size_bytes == 10
    assert reader.read_at(0, 4) == b"abcd"
    assert reader.read_at(2, 3) == b"cde"
    assert reader.read_at(8, 10) == b"ij"  # short at EOF
    reader.close()
    with DeviceSurfaceReader(str(path), 10) as ctx:
        assert ctx.read_at(9, 1) == b"j"


def test_file_surface_reader_refuses_directory_and_missing(tmp_path):
    with pytest.raises(UnsafeTargetError):
        FileSurfaceReader(str(tmp_path))  # a directory
    with pytest.raises(UnsafeTargetError):
        FileSurfaceReader(str(tmp_path / "does-not-exist.img"))


def test_file_surface_reader_reads_real_file(tmp_path):
    path = tmp_path / "surface.img"
    path.write_bytes(bytes(range(256)) * 4)
    with FileSurfaceReader(str(path)) as reader:
        assert reader.size_bytes == 1024
        assert reader.read_at(0, 4) == bytes([0, 1, 2, 3])
        assert reader.read_at(255, 3) == bytes([255, 0, 1])


# ------------------------------------------------------- engine guard rails


class _ExplodingVerifier:
    def verify(self, drive, plan, emit=None):
        raise RuntimeError("synthetic verifier explosion")


def test_engine_survives_exploding_verifier(fake_device_file):
    size = 2 * MIB
    path, _ = fake_device_file(size)
    with open(path, "wb") as fh:
        fh.write(b"\xaa" * size)
    drive = make_drive(size_bytes=size)
    authority = GateAuthority()
    token = _full_token(authority, drive)
    engine = WipeEngine(
        authority,
        FileExecutor(path),
        enumerator=lambda: [drive],
        verifier=_ExplodingVerifier(),
    )
    events = []
    result = engine.wipe(drive, token, emit=events.append)
    assert result.ok is True
    assert result.started_at
    assert result.verification is not None
    assert result.verification.verdict is VerificationVerdict.NOT_VERIFIED
    assert verify_result_authentic(result.verification) is True
    kinds = [e.kind for e in events]
    assert EventKind.COMPLETED in kinds
    assert kinds.index(EventKind.COMPLETED) < kinds.index(EventKind.FAILED)


def test_engine_without_verifier_has_none_verification(fake_device_file):
    size = 2 * MIB
    path, _ = fake_device_file(size)
    drive = make_drive(size_bytes=size)
    authority = GateAuthority()
    token = _full_token(authority, drive)
    engine = WipeEngine(
        authority, FileExecutor(path), enumerator=lambda: [drive]
    )
    result = engine.wipe(drive, token)
    assert result.ok is True
    assert result.verification is None
    assert result.started_at  # always captured now


# ------------------------------------------------------- audit regressions


class OverReturningSurface:
    """Returns MORE bytes than were asked for (zeros)."""

    def __init__(self, size: int, extra: int = 100):
        self.size_bytes = size
        self._extra = extra

    def read_at(self, offset: int, length: int) -> bytes:
        return bytes(length + self._extra)

    def close(self) -> None:
        pass


class RuntimeErrorSurface:
    def __init__(self, size: int):
        self.size_bytes = size

    def read_at(self, offset: int, length: int) -> bytes:
        raise RuntimeError("synthetic non-OSError read failure")

    def close(self) -> None:
        pass


class GrowingSurface:
    """Reports the real size once, then double forever after."""

    def __init__(self, real_size: int):
        self._real = real_size
        self._calls = 0

    @property
    def size_bytes(self):  # type: ignore[override]
        self._calls += 1
        return self._real if self._calls == 1 else self._real * 2

    def read_at(self, offset: int, length: int) -> bytes:
        return bytes(length)

    def close(self) -> None:
        pass


def test_over_returning_reads_are_not_verified_and_never_counted():
    # Audit F5: an over-returning read is a read failure, and its
    # bytes are never counted — on both the full and sampled legs.
    drive = make_drive(size_bytes=2 * MIB)
    plan = select_plan(drive)
    result = WipeVerifier(lambda d: OverReturningSurface(drive.size_bytes)).verify(
        drive, plan
    )
    assert result.verdict is VerificationVerdict.NOT_VERIFIED
    assert result.bytes_checked == 0
    assert result.bytes_checked <= result.bytes_total

    ssd = make_ssd()
    splan = select_plan(ssd)
    sampled = WipeVerifier(
        lambda d: OverReturningSurface(ssd.size_bytes)
    ).verify(ssd, splan)
    assert sampled.verdict is VerificationVerdict.NOT_VERIFIED
    assert sampled.bytes_checked <= sampled.bytes_total
    assert verify_result_authentic(sampled) is True


def test_non_oserror_read_failure_is_not_verified():
    # Audit F11: read_at raising something other than OSError still
    # routes to the honest read-failure result, never a crash.
    drive = make_drive(size_bytes=2 * MIB)
    plan = select_plan(drive)
    result = WipeVerifier(
        lambda d: RuntimeErrorSurface(drive.size_bytes)
    ).verify(drive, plan)
    assert result.verdict is VerificationVerdict.NOT_VERIFIED
    assert result.bytes_checked == 0


def test_surface_size_is_captured_once():
    # Audit F5: the size read in _verify_inner is the size used;
    # a surface that later reports double cannot inflate the count.
    size = 2 * MIB
    drive = make_drive(size_bytes=size)
    plan = select_plan(drive)
    result = WipeVerifier(lambda d: GrowingSurface(size)).verify(drive, plan)
    assert result.verdict is VerificationVerdict.PASS
    assert result.bytes_checked == size
    assert result.bytes_total == size


def test_zero_size_surface_is_not_verified():
    # Audit F12: a surface reporting no size at all can never be
    # verified. (A zero-size *drive* cannot even be planned — the
    # size<=0 branch is what a zero-size surface reaches first.)
    drive = make_ssd()
    plan = select_plan(drive)
    result = WipeVerifier(lambda d: FakeSurface(b"")).verify(drive, plan)
    assert result.verdict is VerificationVerdict.NOT_VERIFIED
    assert result.bytes_checked == 0
    assert verify_result_authentic(result) is True


def test_malformed_passes_record_is_not_verified_not_a_crash():
    # Audit F11: passes of the wrong type (a bare string here) must
    # produce a plain NOT_VERIFIED, not an exception.
    drive = make_drive(size_bytes=2 * MIB)
    plan = WipePlan(
        drive_fingerprint=drive.fingerprint,
        method_label="Custom overwrite",
        nist_category="Clear",
        steps=(WipeStep(MethodKind.OVERWRITE, "Overwrite", "synthetic"),),
        passes="zero",  # type: ignore[arg-type]
    )
    result = WipeVerifier(
        lambda d: FakeSurface(bytes(drive.size_bytes))
    ).verify(drive, plan)
    assert result.verdict is VerificationVerdict.NOT_VERIFIED


def test_authenticity_detail_and_remnant_offsets_are_signed():
    # Audit F3: detail and first_remnant_offsets are inside the MAC.
    size = 2 * MIB
    half = size // 2
    drive = make_drive(size_bytes=size)
    plan = select_plan(drive)
    data = bytes(half) + b"\xaa" * half
    genuine = WipeVerifier(lambda d: FakeSurface(data)).verify(drive, plan)
    assert genuine.verdict is VerificationVerdict.FAIL
    assert verify_result_authentic(genuine) is True
    softened = dataclasses.replace(
        genuine, detail="Verification almost passed; only a trivial anomaly."
    )
    assert verify_result_authentic(softened) is False
    no_offsets = dataclasses.replace(genuine, first_remnant_offsets=())
    assert verify_result_authentic(no_offsets) is False


def test_authenticity_wrong_typed_fields_return_false_never_raise():
    # Audit F11.
    drive = make_drive()
    plan = select_plan(drive)
    genuine = unverified_result(drive, plan, "synthetic")
    assert verify_result_authentic(
        dataclasses.replace(genuine, mac=genuine.mac.encode())
    ) is False
    assert verify_result_authentic(
        dataclasses.replace(genuine, verdict="pass")
    ) is False
    assert verify_result_authentic(
        dataclasses.replace(genuine, bytes_checked=str(genuine.bytes_checked))
    ) is False
    assert verify_result_authentic(
        dataclasses.replace(genuine, remnants_found=True)
    ) is False


def test_check_nvme_firmware_status_above_range_and_trailing_junk():
    # Audit F10: SPROG is confirmed only at exactly 65535.
    status, _ = check_nvme_firmware_status(_nvme_runner(0, 65536), "/dev/nvme0n1")
    assert status == "unreadable"
    status, _ = check_nvme_firmware_status(_nvme_runner(0, 655350), "/dev/nvme0n1")
    assert status == "unreadable"
    status, _ = check_nvme_firmware_status(
        lambda argv: (0, "Sanitize Progress (SPROG) : 65535abc"), "/dev/nvme0n1"
    )
    assert status == "unreadable"
    status, _ = check_nvme_firmware_status(_nvme_runner(0, 0), "/dev/nvme0n1")
    assert status == "in_progress"


def test_check_nvme_firmware_status_runner_output_coercion():
    # Audit F11: bytes stdout is decoded; anything else odd is
    # "unreadable", never an exception.
    status, _ = check_nvme_firmware_status(
        lambda argv: (0, b"Sanitize Progress (SPROG) : 65535"), "/dev/nvme0n1"
    )
    assert status == "confirmed"
    status, _ = check_nvme_firmware_status(
        lambda argv: (0, None), "/dev/nvme0n1"
    )
    assert status == "unreadable"
    status, _ = check_nvme_firmware_status(
        lambda argv: None, "/dev/nvme0n1"
    )
    assert status == "unreadable"
    status, _ = check_nvme_firmware_status(
        lambda argv: (0, "x", "y"), "/dev/nvme0n1"
    )
    assert status == "unreadable"


def test_verify_signs_the_actual_method_label_when_given():
    # Audit F1a: the minted (and signed) label is the actual method.
    drive = make_drive(size_bytes=2 * MIB)
    plan = select_plan(drive)
    result = WipeVerifier(
        lambda d: FakeSurface(bytes(drive.size_bytes))
    ).verify(drive, plan, actual_method_label="Actual method that ran")
    assert result.verdict is VerificationVerdict.PASS
    assert result.method_label == "Actual method that ran"
    assert verify_result_authentic(result) is True

    fallback = unverified_result(
        drive, plan, "synthetic", actual_method_label="Actual method that ran"
    )
    assert fallback.method_label == "Actual method that ran"
    assert verify_result_authentic(fallback) is True


# ------------------------------------------------------- kit-hunt regression


class AlignedOnlySurface(FakeSurface):
    """A surface with Windows raw-device manners: any read whose
    offset is not a multiple of the 512-byte sector size is
    refused with EINVAL, exactly as a real PhysicalDrive
    read behaved in the kit-hunt probe on windows-latest."""

    def read_at(self, offset: int, length: int) -> bytes:
        if offset % 512 != 0:
            raise OSError(22, "Invalid argument")
        return super().read_at(offset, length)


def test_sampled_verification_aligns_its_read_offsets():
    # Pre-fix: sampled region starts were spread arithmetically
    # over the surface and landed unaligned, so on a raw
    # Windows drive every sampled read would fail and the
    # verdict would be NOT VERIFIED despite a clean erase.
    drive = make_ssd(size_bytes=8 * MIB + 12345)
    plan = select_plan(drive)
    surface = AlignedOnlySurface(bytes(drive.size_bytes))
    verifier = WipeVerifier(lambda d: surface)
    result = verifier.verify(drive, plan)
    assert result.verdict is VerificationVerdict.PASS
    assert result.coverage == "sampled"
    assert verify_result_authentic(result)
