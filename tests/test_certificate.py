"""Certificate tests — certificates of destruction and failure reports.

SAFETY: everything is synthetic. Drives are data objects, surfaces
are in-memory byte strings or small regular files under tmp_path,
operators and serials are invented for these tests, and documents
are written only under pytest tmp dirs. No real device is ever
opened, and the RealExecutor is never armed.

Genuine verification results are only ever minted by running
WipeVerifier (or unverified_result) — never built by hand. The
hand-built ones in this file exist to prove they are refused.
"""

from __future__ import annotations

import dataclasses
import hashlib
import html as html_module

import pytest

import beerus
from beerus.certificate import (
    IssuedDocument,
    JobRecord,
    build_job_record,
    issue_document,
    render_certificate_html,
    render_failure_report_html,
    write_document,
)
from beerus.engine import WipeEngine, WipeResult
from beerus.errors import CertificateError
from beerus.executor import FileExecutor, RealExecutor
from beerus.gates import GateAuthority
from beerus.methods import select_plan
from beerus.verify import (
    FileSurfaceReader,
    VerificationResult,
    VerificationVerdict,
    WipeVerifier,
    unverified_result,
)
from conftest import MIB, make_drive, make_nvme, make_ssd

OPERATOR = "Synthetic Operator"


class FakeSurface:
    """In-memory read-only surface (mirrors tests/test_verify.py)."""

    def __init__(self, data: bytes):
        self._data = bytes(data)
        self.size_bytes = len(self._data)

    def read_at(self, offset: int, length: int) -> bytes:
        return self._data[offset : offset + length]

    def close(self) -> None:
        pass


def _nvme_runner(rc=0, sprog=None):
    def runner(argv):
        assert argv[0] == "nvme"
        assert argv[1] == "sanitize-log"
        if sprog is None:
            return (rc, "")
        return (rc, f"Sanitize Progress (SPROG)              : {sprog}\n")

    return runner


def _full_token(authority, drive):
    plan = select_plan(drive)
    session = authority.begin_session(drive, plan)
    session.confirm_identity(drive.serial)
    session.acknowledge_scope()
    return session.final_confirmation(f"ERASE {drive.serial}")


def _wipe_result(drive, plan, verification, *, ok=True, **overrides):
    fields = dict(
        ok=ok,
        drive_fingerprint=drive.fingerprint,
        method_label=plan.method_label,
        nist_category=plan.nist_category,
        bytes_written=drive.size_bytes,
        detail="synthetic wipe result",
        completed_at="2026-10-20T03:00:00+00:00",
        started_at="2026-10-20T02:55:00+00:00",
        verification=verification,
    )
    fields.update(overrides)
    return WipeResult(**fields)


def _run_hdd_job(tmp_path, *, operator=OPERATOR):
    """A real engine wipe + verifier run on a tmp-file HDD."""
    size = 2 * MIB
    path = tmp_path / "fake-drive.img"
    with open(path, "wb") as fh:
        fh.write(b"\xaa" * size)
    drive = make_drive(size_bytes=size)
    authority = GateAuthority()
    token = _full_token(authority, drive)
    verifier = WipeVerifier(lambda d: FileSurfaceReader(str(path)))
    engine = WipeEngine(
        authority,
        FileExecutor(str(path)),
        enumerator=lambda: [drive],
        verifier=verifier,
    )
    result = engine.wipe(drive, token)
    assert result.ok is True
    assert result.verification is not None
    assert result.verification.verdict is VerificationVerdict.PASS
    record = build_job_record(drive, result, operator)
    return drive, result, record


def _sampled_pass_record(drive, surface_data, firmware_runner=None):
    plan = select_plan(drive)
    verifier = WipeVerifier(
        lambda d: FakeSurface(surface_data), firmware_runner=firmware_runner
    )
    verification = verifier.verify(drive, plan)
    assert verification.verdict is VerificationVerdict.PASS
    assert verification.coverage == "sampled"
    wipe_result = _wipe_result(drive, plan, verification)
    return build_job_record(drive, wipe_result, OPERATOR), verification


# ------------------------------------------------------- clean certificates


def test_pass_job_issues_complete_certificate(tmp_path):
    drive, result, record = _run_hdd_job(tmp_path)
    document = issue_document(record)

    assert isinstance(document, IssuedDocument)
    assert document.kind == "certificate"
    assert document.record is record
    doc = document.html
    assert "Certificate of Destruction" in doc
    assert "Sentinel Safeguard LLC" in doc
    assert drive.model in doc
    assert drive.serial in doc
    assert result.method_label in doc
    assert "NIST 800-88 category: Clear" in doc
    assert OPERATOR in doc
    assert result.started_at in doc
    assert result.completed_at in doc
    # Capacity: human-readable plus exact bytes.
    assert f"{drive.size_bytes} bytes" in doc
    # Verification section: PASS + full-coverage sentence, real numbers.
    assert "PASS" in doc
    assert "the whole drive was read back and checked" in doc
    assert f"({drive.size_bytes} of {drive.size_bytes} bytes)" in doc
    # Record ID: a hash over the FULL record (audit F6), so it is
    # checked against the module's own derivation, not a partial
    # fingerprint|completed_at|serial formula.
    from beerus.certificate import _record_id

    assert f"Record ID: {_record_id(record)}" in doc
    # Footer with the default tool version.
    assert record.tool_version == beerus.__version__
    assert f"BEERUS.EXE v{beerus.__version__}" in doc
    assert "client's business record of this destruction" in doc
    # A certificate never looks like a failure report.
    assert "Failure Report" not in doc
    assert "THIS IS NOT A CERTIFICATE" not in doc


def test_sampled_pass_certificate_coverage_sentence_nvme():
    drive = make_nvme()
    record, verification = _sampled_pass_record(
        drive,
        bytes(drive.size_bytes),
        firmware_runner=_nvme_runner(0, 65535),
    )
    assert verification.firmware_status == "confirmed"
    document = issue_document(record)
    assert document.kind == "certificate"
    doc = document.html
    assert "NIST 800-88 category: Purge" in doc
    assert "a sample of the drive was read back and checked" in doc
    assert (
        f"({verification.bytes_checked} of {verification.bytes_total} bytes "
        f"across {verification.regions_checked} regions)"
    ) in doc
    assert "the drive's own firmware status was also checked" in doc


def test_sampled_pass_certificate_firmware_sentence_ata():
    drive = make_ssd()
    record, verification = _sampled_pass_record(drive, bytes(drive.size_bytes))
    assert verification.firmware_status == "not_available"
    document = issue_document(record)
    assert document.kind == "certificate"
    doc = document.html
    assert "a sample of the drive was read back and checked" in doc
    assert (
        "this method's firmware status cannot be read, so the result "
        "rests on the read-back sample"
    ) in doc


# ------------------------------------------------------------- failure reports


def test_fail_verification_issues_failure_report_with_remnants():
    size = 2 * MIB
    half = size // 2
    drive = make_drive(size_bytes=size)
    plan = select_plan(drive)
    data = bytes(half) + b"\xaa" * half
    verification = WipeVerifier(lambda d: FakeSurface(data)).verify(drive, plan)
    assert verification.verdict is VerificationVerdict.FAIL
    record = build_job_record(drive, _wipe_result(drive, plan, verification), OPERATOR)

    document = issue_document(record)
    assert document.kind == "failure_report"
    doc = document.html
    assert "Destruction NOT Verified — Failure Report" in doc
    assert "THIS IS NOT A CERTIFICATE OF DESTRUCTION" in doc
    assert "FAIL" in doc
    # The remnant specifics from the result's own detail text.
    assert html_module.escape(verification.detail) in doc
    assert str(verification.remnants_found) in doc
    assert str(verification.first_remnant_offsets[0]) in doc
    # Same job facts as a certificate would carry.
    assert drive.model in doc
    assert drive.serial in doc
    assert plan.method_label in doc
    assert OPERATOR in doc
    # What to do next.
    assert "What to do next" in doc
    assert "Run the wipe again and let verification complete" in doc
    assert "use a stronger method" in doc
    assert "physically destroy the drive" in doc
    assert "Do not return the drive to service" in doc
    assert "failure report is the client's business record" in doc
    # The renderer itself refuses to certify this record.
    with pytest.raises(CertificateError):
        render_certificate_html(record)


def test_not_verified_issues_failure_report():
    drive = make_drive()
    plan = select_plan(drive)
    verification = unverified_result(drive, plan, "synthetic: could not verify")
    assert verification.verdict is VerificationVerdict.NOT_VERIFIED
    record = build_job_record(drive, _wipe_result(drive, plan, verification), OPERATOR)

    document = issue_document(record)
    assert document.kind == "failure_report"
    assert "NOT_VERIFIED" in document.html
    assert "synthetic: could not verify" in document.html
    with pytest.raises(CertificateError):
        render_certificate_html(record)


def test_no_verification_issues_failure_report_stating_none_exists():
    drive = make_drive()
    plan = select_plan(drive)
    record = build_job_record(drive, _wipe_result(drive, plan, None), OPERATOR)

    document = issue_document(record)
    assert document.kind == "failure_report"
    assert "No verification result exists" in document.html
    assert "cannot be certified" in document.html
    with pytest.raises(CertificateError):
        render_certificate_html(record)


def test_wipe_not_ok_issues_failure_report():
    drive = make_nvme()
    record, verification = _sampled_pass_record(
        drive,
        bytes(drive.size_bytes),
        firmware_runner=_nvme_runner(0, 65535),
    )
    plan = select_plan(drive)
    failed = build_job_record(
        drive, _wipe_result(drive, plan, verification, ok=False), OPERATOR
    )
    document = issue_document(failed)
    assert document.kind == "failure_report"
    assert "did not report success" in document.html


# ------------------------------------------------------------------- forgery


def test_forged_hand_built_pass_result_is_refused_entirely():
    drive = make_drive()
    plan = select_plan(drive)
    forged = VerificationResult(
        verdict=VerificationVerdict.PASS,
        drive_fingerprint=drive.fingerprint,
        method_label=plan.method_label,
        nist_category=plan.nist_category,
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
    record = build_job_record(drive, _wipe_result(drive, plan, forged), OPERATOR)
    with pytest.raises(CertificateError):
        issue_document(record)
    with pytest.raises(CertificateError):
        render_certificate_html(record)


def test_flipped_verdict_on_genuine_result_is_refused_entirely():
    drive = make_drive()
    plan = select_plan(drive)
    genuine = unverified_result(drive, plan, "synthetic")
    assert genuine.verdict is VerificationVerdict.NOT_VERIFIED
    forged = dataclasses.replace(genuine, verdict=VerificationVerdict.PASS)
    # Fingerprint still matches the drive — only authenticity can catch this.
    record = build_job_record(drive, _wipe_result(drive, plan, forged), OPERATOR)
    with pytest.raises(CertificateError):
        issue_document(record)
    with pytest.raises(CertificateError):
        render_certificate_html(record)


# -------------------------------------------------------- build_job_record


def test_build_job_record_blank_operator_refused():
    drive = make_drive()
    plan = select_plan(drive)
    with pytest.raises(CertificateError) as excinfo:
        build_job_record(drive, _wipe_result(drive, plan, None), "   ")
    assert "operator" in excinfo.value.user_message
    assert str(excinfo.value) == excinfo.value.user_message


def test_build_job_record_wipe_fingerprint_mismatch_refused():
    drive = make_drive()
    other = make_drive(serial="WD-OTHER9999999")
    plan = select_plan(drive)
    wipe_result = _wipe_result(
        drive, plan, None, drive_fingerprint=other.fingerprint
    )
    with pytest.raises(CertificateError):
        build_job_record(drive, wipe_result, OPERATOR)


def test_build_job_record_verification_fingerprint_mismatch_refused():
    drive = make_drive()
    other = make_drive(serial="WD-OTHER9999999")
    other_plan = select_plan(other)
    foreign = unverified_result(other, other_plan, "synthetic: other drive")
    plan = select_plan(drive)
    # Wipe record belongs to this drive; the verification does not.
    wipe_result = _wipe_result(drive, plan, foreign)
    with pytest.raises(CertificateError):
        build_job_record(drive, wipe_result, OPERATOR)


def test_build_job_record_fields_and_started_at_kept_empty():
    # Audit F13: a missing started_at stays missing — build_job_record
    # must not substitute completed_at for it.
    drive = make_drive()
    plan = select_plan(drive)
    wipe_result = _wipe_result(drive, plan, None, started_at="")
    record = build_job_record(drive, wipe_result, f"  {OPERATOR}  ")
    assert isinstance(record, JobRecord)
    assert record.model == drive.model
    assert record.serial == drive.serial
    assert record.size_bytes == drive.size_bytes
    assert record.interface == drive.interface
    assert record.method_label == plan.method_label
    assert record.nist_category == plan.nist_category
    assert record.started_at == ""
    assert record.completed_at == wipe_result.completed_at
    assert record.bytes_written == wipe_result.bytes_written
    assert record.operator == OPERATOR  # stripped
    assert record.tool_version == beerus.__version__
    assert record.wipe_ok is True
    assert record.drive_fingerprint == drive.fingerprint
    assert record.verification is None


def test_certificate_error_carries_user_message_and_detail():
    err = CertificateError("plain message", detail="technical detail")
    assert err.user_message == "plain message"
    assert err.detail == "technical detail"
    assert str(err) == "plain message"


# -------------------------------------------------------------- XSS hygiene


def test_operator_script_tag_is_escaped(tmp_path):
    _, _, record = _run_hdd_job(
        tmp_path, operator="<script>alert(1)</script>"
    )
    document = issue_document(record)
    assert document.kind == "certificate"
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in document.html
    assert "<script>alert" not in document.html


def test_model_html_is_escaped():
    drive = make_nvme(model="Fake <b>Model</b>")
    record, _ = _sampled_pass_record(
        drive,
        bytes(drive.size_bytes),
        firmware_runner=_nvme_runner(0, 65535),
    )
    document = issue_document(record)
    assert document.kind == "certificate"
    assert "Fake &lt;b&gt;Model&lt;/b&gt;" in document.html
    assert "<b>" not in document.html


# ----------------------------------------------------------------- branding


def test_certificate_branding_snapshot(tmp_path):
    _, _, record = _run_hdd_job(tmp_path)
    doc = issue_document(record).html
    assert "#2ee06f" in doc
    assert "#0c130d" in doc
    assert "Sentinel Safeguard" in doc
    assert "purple" not in doc.lower()
    assert "#800080" not in doc.lower()


# ------------------------------------------------------------ write_document


def test_write_document_writes_certificate_file(tmp_path):
    _, result, record = _run_hdd_job(tmp_path)
    document = issue_document(record)
    out_dir = tmp_path / "paperwork"  # does not exist yet
    path = write_document(document, out_dir)
    assert path.exists()
    assert path.parent == out_dir
    assert path.read_text(encoding="utf-8") == document.html
    assert path.name.startswith("beerus-certificate-")
    assert record.serial in path.name
    date = result.completed_at[:10].replace("-", "")
    # Audit F6/F7: the filename now ends with the full-record ID.
    from beerus.certificate import _record_id

    assert path.name.endswith(f"-{date}-{_record_id(record)}.html")


def test_write_document_sanitizes_serial_with_slashes_and_spaces(tmp_path):
    drive = make_drive(serial="WD /TEST 123")
    plan = select_plan(drive)
    record = build_job_record(drive, _wipe_result(drive, plan, None), OPERATOR)
    document = issue_document(record)
    assert document.kind == "failure_report"
    path = write_document(document, tmp_path / "out")
    assert path.exists()
    assert "WD--TEST-123" in path.name
    assert " " not in path.name
    assert "/" not in path.name
    assert "failure-report" in path.name
    assert path.read_text(encoding="utf-8") == document.html


def test_failure_report_renderer_always_available():
    drive = make_drive()
    plan = select_plan(drive)
    record = build_job_record(drive, _wipe_result(drive, plan, None), OPERATOR)
    doc = render_failure_report_html(record)
    assert "Failure Report" in doc
    assert "THIS IS NOT A CERTIFICATE OF DESTRUCTION" in doc


# ------------------------------------------------------- audit regressions


def test_hand_assembled_job_record_is_refused_everywhere():
    # Audit probe 1 / F1d: a genuine PASS for one drive stapled onto
    # a hand-built record claiming another drive, 8 TB, and Purge
    # must be refused by issue_document AND the certificate renderer.
    drive = make_drive(size_bytes=2 * MIB)
    plan = select_plan(drive)
    genuine_pass = WipeVerifier(
        lambda d: FakeSurface(bytes(drive.size_bytes))
    ).verify(drive, plan)
    assert genuine_pass.verdict is VerificationVerdict.PASS
    other = make_drive(serial="WD-OTHER9999999")
    hand_rec = JobRecord(
        model="Victim Corp 8TB Enterprise",
        serial="VICTIM-SERIAL-001",
        size_bytes=8_000_000_000_000,
        interface="sata",
        method_label="NVMe Sanitize — the drive's own built-in erase",
        nist_category="Purge",
        started_at="2026-10-20T02:55:00+00:00",
        completed_at="2026-10-20T03:00:00+00:00",
        bytes_written=8_000_000_000_000,
        operator="Attacker",
        tool_version="0.2.0",
        wipe_ok=True,
        drive_fingerprint=other.fingerprint,
        verification=genuine_pass,
    )
    with pytest.raises(CertificateError):
        issue_document(hand_rec)
    with pytest.raises(CertificateError):
        render_certificate_html(hand_rec)
    with pytest.raises(CertificateError):
        render_failure_report_html(hand_rec)


def test_build_job_record_method_relabel_clear_to_purge_refused():
    # Audit F1c: an ATA (Purge) sampled PASS relabelled at the wipe
    # record layer — Clear/Purge mismatch against the signed
    # verification — is refused while the record is built.
    drive = make_ssd()
    plan = select_plan(drive)
    verification = WipeVerifier(
        lambda d: FakeSurface(bytes(drive.size_bytes))
    ).verify(drive, plan)
    assert verification.verdict is VerificationVerdict.PASS
    lying = _wipe_result(
        drive,
        plan,
        verification,
        method_label="NVMe Sanitize — the drive's own built-in erase",
        nist_category="Purge",
    )
    # The SSD plan IS Purge, so the label alone must be the mismatch:
    assert plan.nist_category == "Purge"
    with pytest.raises(CertificateError):
        build_job_record(drive, lying, OPERATOR)
    clear_lie = _wipe_result(drive, plan, verification, nist_category="Clear")
    with pytest.raises(CertificateError):
        build_job_record(drive, clear_lie, OPERATOR)


def test_nvme_fallback_genuine_flow_issues_format_certificate(monkeypatch):
    # Audit F1a/F1b regression guard: Sanitize refused, Format ran,
    # the verifier is wired through the engine, firmware confirms —
    # the clean certificate must carry the FORMAT label that the
    # verification was actually signed for.
    monkeypatch.setenv(RealExecutor.ARM_ENV_VAR, "1")
    drive = make_nvme()
    authority = GateAuthority()
    token = _full_token(authority, drive)

    def runner(argv):
        if argv[1] == "sanitize":
            return (1, "")
        if argv[1] == "sanitize-log":
            return (0, "Sanitize Progress (SPROG)              : 65535\n")
        if argv[1] == "format":
            return (0, "")
        raise AssertionError(f"unexpected argv {argv!r}")

    executor = RealExecutor(armed=True, runner=runner)
    verifier = WipeVerifier(
        lambda d: FakeSurface(bytes(drive.size_bytes)), firmware_runner=runner
    )
    engine = WipeEngine(
        authority, executor, enumerator=lambda: [drive], verifier=verifier
    )
    result = engine.wipe(drive, token)
    assert result.ok is True
    assert "Format" in result.method_label
    assert result.verification is not None
    assert result.verification.verdict is VerificationVerdict.PASS
    assert result.verification.method_label == result.method_label

    record = build_job_record(drive, result, OPERATOR)
    document = issue_document(record)
    assert document.kind == "certificate"
    assert result.method_label in document.html


def test_edited_detail_on_genuine_result_is_refused():
    # Audit F3: detail is signed now, so softened wording on an
    # otherwise genuine FAIL breaks authenticity and gets no document.
    size = 2 * MIB
    half = size // 2
    drive = make_drive(size_bytes=size)
    plan = select_plan(drive)
    data = bytes(half) + b"\xaa" * half
    genuine_fail = WipeVerifier(lambda d: FakeSurface(data)).verify(drive, plan)
    softened = dataclasses.replace(
        genuine_fail,
        detail="Verification almost passed; only a trivial anomaly.",
        first_remnant_offsets=(),
    )
    record = build_job_record(drive, _wipe_result(drive, plan, softened), OPERATOR)
    with pytest.raises(CertificateError):
        issue_document(record)


def test_mac_as_bytes_raises_certificate_error_not_type_error():
    # Audit F11: verify_result_authentic returns False for a
    # bytes MAC, so issuing surfaces CertificateError, never TypeError.
    drive = make_drive(size_bytes=2 * MIB)
    plan = select_plan(drive)
    genuine = WipeVerifier(
        lambda d: FakeSurface(bytes(drive.size_bytes))
    ).verify(drive, plan)
    wrong_type = dataclasses.replace(genuine, mac=genuine.mac.encode())
    record = build_job_record(drive, _wipe_result(drive, plan, wrong_type), OPERATOR)
    with pytest.raises(CertificateError):
        issue_document(record)


def test_failure_renderer_refuses_forged_verification():
    # Audit F8: the failure renderer applies the same authenticity
    # refusal — a forged FAIL (mac="") gets no document at all.
    drive = make_drive()
    plan = select_plan(drive)
    forged_fail = VerificationResult(
        verdict=VerificationVerdict.FAIL,
        drive_fingerprint=drive.fingerprint,
        method_label=plan.method_label,
        nist_category=plan.nist_category,
        coverage="full",
        bytes_checked=drive.size_bytes,
        bytes_total=drive.size_bytes,
        regions_checked=1,
        remnants_found=123456,
        first_remnant_offsets=(42,),
        firmware_status="not_applicable",
        detail="FORGED FAILURE DETAIL",
        checked_at="2026-01-01T00:00:00+00:00",
        mac="",
    )
    record = JobRecord(
        model=drive.model,
        serial=drive.serial,
        size_bytes=drive.size_bytes,
        interface=drive.interface,
        method_label=plan.method_label,
        nist_category=plan.nist_category,
        started_at="2026-10-20T02:55:00+00:00",
        completed_at="2026-10-20T03:00:00+00:00",
        bytes_written=1,
        operator=OPERATOR,
        tool_version="0.2.0",
        wipe_ok=True,
        drive_fingerprint=drive.fingerprint,
        verification=forged_fail,
    )
    with pytest.raises(CertificateError):
        render_failure_report_html(record)
    with pytest.raises(CertificateError):
        issue_document(record)


class FailAfterSurface:
    """Reads honestly until a byte budget is used up, then raises."""

    def __init__(self, data: bytes, fail_after: int):
        self._data = bytes(data)
        self.size_bytes = len(self._data)
        self._fail_after = fail_after
        self._returned = 0

    def read_at(self, offset: int, length: int) -> bytes:
        if self._returned >= self._fail_after:
            raise OSError("synthetic read failure")
        chunk = self._data[offset : offset + length]
        self._returned += len(chunk)
        return chunk

    def close(self) -> None:
        pass


def test_partial_read_failure_report_states_partial_coverage():
    # Audit F4: coverage "none" with bytes_checked > 0 must state
    # the partial truth, never "no part of the drive".
    size = 2 * MIB
    drive = make_drive(size_bytes=size)
    plan = select_plan(drive)
    surface = FailAfterSurface(bytes(size), fail_after=MIB)
    verification = WipeVerifier(lambda d: surface).verify(drive, plan)
    assert verification.verdict is VerificationVerdict.NOT_VERIFIED
    assert verification.coverage == "none"
    assert verification.bytes_checked == MIB
    record = build_job_record(drive, _wipe_result(drive, plan, verification), OPERATOR)
    document = issue_document(record)
    assert document.kind == "failure_report"
    assert (
        f"only {MIB} of {size} bytes could be read back before the read failed"
        in document.html
    )
    assert "no part of the drive was read back" not in document.html


def test_missing_started_at_renders_not_recorded():
    # Audit F13: both renderers say "Not recorded" for an empty
    # started_at instead of silently showing the finish time.
    drive = make_drive(size_bytes=2 * MIB)
    plan = select_plan(drive)
    genuine = WipeVerifier(
        lambda d: FakeSurface(bytes(drive.size_bytes))
    ).verify(drive, plan)
    record = build_job_record(
        drive, _wipe_result(drive, plan, genuine, started_at=""), OPERATOR
    )
    assert record.started_at == ""
    document = issue_document(record)
    assert document.kind == "certificate"
    assert "Started: Not recorded" in document.html

    no_verification = build_job_record(
        drive, _wipe_result(drive, plan, None, started_at=""), OPERATOR
    )
    failure_html = render_failure_report_html(no_verification)
    assert "Started: Not recorded" in failure_html


def test_record_id_changes_with_any_record_field():
    # Audit F6: the ID hashes the full record, so jobs differing in
    # one stated field never share an ID.
    from beerus.certificate import _record_id

    drive = make_drive()
    plan = select_plan(drive)
    base = build_job_record(drive, _wipe_result(drive, plan, None), OPERATOR)
    for overrides in (
        dict(operator="Operator Two"),
        dict(method_label="Other Method"),
        dict(started_at="2020-01-01T00:00:00+00:00"),
        dict(bytes_written=1),
        dict(size_bytes=999),
        dict(tool_version="9.9.9"),
    ):
        assert _record_id(dataclasses.replace(base, **overrides)) != _record_id(base)


def test_write_document_serial_collision_pair_produces_different_files(tmp_path):
    # Audit F6/F7: 'A/B' and 'A-B' sanitize to the same token, but
    # the record ID in the filename keeps the two jobs apart.
    records = []
    for serial in ("A/B", "A-B"):
        drive = make_drive(serial=serial)
        plan = select_plan(drive)
        records.append(build_job_record(drive, _wipe_result(drive, plan, None), OPERATOR))
    paths = [write_document(issue_document(r), tmp_path) for r in records]
    assert paths[0] != paths[1]
    assert paths[0].exists() and paths[1].exists()
    assert paths[0].read_text(encoding="utf-8") == issue_document(records[0]).html
    assert paths[1].read_text(encoding="utf-8") == issue_document(records[1]).html


def test_write_document_refuses_to_overwrite_existing_file(tmp_path):
    # Audit F7: a second write of the same document raises and the
    # original file is left untouched.
    drive = make_drive()
    plan = select_plan(drive)
    record = build_job_record(drive, _wipe_result(drive, plan, None), OPERATOR)
    document = issue_document(record)
    path = write_document(document, tmp_path)
    original = path.read_text(encoding="utf-8")
    with pytest.raises(CertificateError):
        write_document(document, tmp_path)
    assert path.read_text(encoding="utf-8") == original


def test_write_document_refuses_hand_built_document(tmp_path):
    # Audit F9: write_document re-derives the document from the
    # record; a hand-built IssuedDocument with fake HTML (or a
    # certificate kind for a failed record) writes nothing.
    drive = make_drive()
    plan = select_plan(drive)
    record = build_job_record(drive, _wipe_result(drive, plan, None), OPERATOR)
    fake_html = IssuedDocument(
        kind="failure_report", html="<html>FAKE</html>", record=record
    )
    with pytest.raises(CertificateError):
        write_document(fake_html, tmp_path)
    fake_kind = IssuedDocument(
        kind="certificate",
        html=issue_document(record).html,
        record=record,
    )
    with pytest.raises(CertificateError):
        write_document(fake_kind, tmp_path)
    assert list(tmp_path.iterdir()) == []
