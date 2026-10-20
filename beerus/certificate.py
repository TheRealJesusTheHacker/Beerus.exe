"""Certificates of destruction and failure reports for Beerus.

Phase 2, part 2: the paperwork. A completed job produces exactly
one document:

- a **certificate of destruction** — issued only when the wipe
  reported success AND a genuine verification result says PASS.
  Anything less is not certified, ever.
- a **failure report** — for everything else. It states plainly
  that the destruction was NOT verified, what verification found,
  and what to do next. It is designed so it can never be mistaken
  for a certificate.

Trust rules, enforced here and not left to callers:

- A verification result that fails its HMAC authenticity check
  (hand-built, edited, or flipped) means NO document of any kind
  is issued — the records are treated as tampered.
- Records that do not belong to the drive (fingerprint mismatch)
  are refused when the job record is built.
- ``render_certificate_html`` re-checks the clean condition
  itself, so no caller can mint a clean certificate for a failed
  job by calling the renderer directly.

Documents are single self-contained HTML strings: inline CSS only,
no external assets, print-friendly, Sentinel green on black.
Certificates are business records — generated locally, on the job.

Same-run requirement: certificates must be issued in the same run
as the wipe. Verification results are signed with a key that lives
only for the current process, so a verification record from another
run cannot be confirmed as genuine in this run and is refused —
no document of any kind is issued for it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from html import escape as _escape
from pathlib import Path

from beerus.errors import CertificateError
from beerus.verify import VerificationResult, VerificationVerdict, verify_result_authentic

_SERIAL_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]")


# ---------------------------------------------------------------------------
# Job records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JobRecord:
    """Everything a document is allowed to say about one job."""

    model: str
    serial: str
    size_bytes: int
    interface: str
    method_label: str
    nist_category: str
    started_at: str
    completed_at: str
    bytes_written: int
    operator: str
    tool_version: str
    wipe_ok: bool
    drive_fingerprint: str
    verification: VerificationResult | None


def build_job_record(drive, wipe_result, operator, tool_version=None) -> JobRecord:
    """Assemble a JobRecord from a drive and its wipe result.

    Refuses (CertificateError) when the operator is unnamed or when
    the wipe/verification records do not belong to this drive.
    """
    operator_name = str(operator or "").strip()
    if not operator_name:
        raise CertificateError(
            "A certificate needs the name of the operator who ran "
            "the wipe. No document was issued."
        )
    if wipe_result.drive_fingerprint != drive.fingerprint:
        raise CertificateError(
            "The wipe record does not belong to this drive, so no "
            "document was issued.",
            detail=(
                f"wipe record fingerprint {wipe_result.drive_fingerprint} "
                f"!= drive fingerprint {drive.fingerprint}"
            ),
        )
    verification = wipe_result.verification
    if verification is not None and verification.drive_fingerprint != drive.fingerprint:
        raise CertificateError(
            "The verification record does not belong to this drive, "
            "so no document was issued.",
            detail=(
                f"verification fingerprint {verification.drive_fingerprint} "
                f"!= drive fingerprint {drive.fingerprint}"
            ),
        )
    if verification is not None and (
        wipe_result.method_label != verification.method_label
        or wipe_result.nist_category != verification.nist_category
    ):
        raise CertificateError(
            "The wipe record claims a different method than the one "
            "the verification record was signed for, so no document "
            "was issued.",
            detail=(
                f"wipe record method {wipe_result.method_label!r} / "
                f"{wipe_result.nist_category!r} != verification method "
                f"{verification.method_label!r} / "
                f"{verification.nist_category!r}"
            ),
        )
    if tool_version is None:
        import beerus  # lazy: avoids an import cycle with the package

        tool_version = beerus.__version__
    return JobRecord(
        model=drive.model,
        serial=drive.serial,
        size_bytes=drive.size_bytes,
        interface=drive.interface,
        method_label=wipe_result.method_label,
        nist_category=wipe_result.nist_category,
        started_at=wipe_result.started_at,
        completed_at=wipe_result.completed_at,
        bytes_written=wipe_result.bytes_written,
        operator=operator_name,
        tool_version=str(tool_version),
        wipe_ok=bool(wipe_result.ok),
        drive_fingerprint=drive.fingerprint,
        verification=verification,
    )


# ---------------------------------------------------------------------------
# Issuing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IssuedDocument:
    kind: str  # "certificate" | "failure_report"
    html: str
    record: JobRecord


_AUTHENTICITY_MESSAGE = (
    "The verification record cannot be confirmed as genuine in "
    "this run. Certificates must be issued in the same run as the "
    "wipe, so no document was issued."
)


def _require_verification_binding(record: JobRecord) -> None:
    """The shared binding check for every document path.

    When a verification record is present it must be (i) authentic
    — minted by Beerus in this run and unedited; (ii) bound to this
    record — same drive fingerprint and same total size; and
    (iii) signed for the very method this record claims. Any
    failure refuses the document entirely: a mismatched record is
    never demoted to a failure report, because a failure report
    built on records that do not belong together would still be a
    Beerus document saying things the run never established.
    """
    verification = record.verification
    if verification is None:
        return
    if not verify_result_authentic(verification):
        raise CertificateError(
            _AUTHENTICITY_MESSAGE,
            detail="verification authenticity (HMAC) check failed",
        )
    if (
        verification.drive_fingerprint != record.drive_fingerprint
        or verification.bytes_total != record.size_bytes
    ):
        raise CertificateError(
            "The verification record does not belong to the drive on "
            "this record, so no document was issued.",
            detail=(
                f"verification fingerprint {verification.drive_fingerprint} "
                f"!= record fingerprint {record.drive_fingerprint}, or "
                f"verification bytes_total {verification.bytes_total} "
                f"!= record size_bytes {record.size_bytes}"
            ),
        )
    if (
        record.method_label != verification.method_label
        or record.nist_category != verification.nist_category
    ):
        raise CertificateError(
            "This record claims a different method than the one the "
            "verification record was signed for, so no document was "
            "issued.",
            detail=(
                f"record method {record.method_label!r} / "
                f"{record.nist_category!r} != verification method "
                f"{verification.method_label!r} / "
                f"{verification.nist_category!r}"
            ),
        )


def _is_clean(record: JobRecord) -> bool:
    """The one and only condition under which a job is certified."""
    return (
        record.wipe_ok is True
        and record.verification is not None
        and record.verification.verdict is VerificationVerdict.PASS
    )


def _require_clean(record: JobRecord) -> None:
    _require_verification_binding(record)
    if not _is_clean(record):
        raise CertificateError(
            "This job did not pass verification, so Beerus will not "
            "issue a certificate of destruction for it. A failure "
            "report is available instead."
        )


def issue_document(record: JobRecord) -> IssuedDocument:
    """Issue the one document this job has earned.

    A tampered or forged verification record is refused entirely —
    no certificate and no failure report. A clean, verified job
    gets a certificate; every other honest job gets a failure
    report.
    """
    _require_verification_binding(record)
    if _is_clean(record):
        return IssuedDocument(
            kind="certificate",
            html=render_certificate_html(record),
            record=record,
        )
    return IssuedDocument(
        kind="failure_report",
        html=render_failure_report_html(record),
        record=record,
    )


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _human_size(size_bytes: int) -> str:
    """Render a byte count in a plain, human-readable form.

    Same formatting style as beerus/gates.py's helper.
    """
    if size_bytes <= 0:
        return "0 bytes"
    units = ["bytes", "KB", "MB", "GB", "TB", "PB"]
    value = float(size_bytes)
    unit_index = 0
    while value >= 1024.0 and unit_index < len(units) - 1:
        value /= 1024.0
        unit_index += 1
    if unit_index == 0:
        return f"{size_bytes} bytes"
    return f"{value:.2f} {units[unit_index]} ({size_bytes} bytes)"


def _record_id(record: JobRecord) -> str:
    """An ID over the FULL record, so two different jobs can never
    share one: every field a document can state is hashed, plus
    the verification's own timestamp and verdict (or "none")."""
    if record.verification is not None:
        verdict = record.verification.verdict
        verdict_value = verdict.value if isinstance(verdict, Enum) else str(verdict)
        verification_part = f"{record.verification.checked_at}|{verdict_value}"
    else:
        verification_part = "none"
    material = "|".join(
        [
            str(record.drive_fingerprint),
            str(record.serial),
            str(record.model),
            str(record.started_at),
            str(record.completed_at),
            str(record.method_label),
            str(record.nist_category),
            str(record.size_bytes),
            str(record.bytes_written),
            str(record.operator),
            str(record.tool_version),
            verification_part,
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _coverage_sentence(verification: VerificationResult) -> str:
    """Plain-language coverage sentence from the result's own fields.

    Only the numbers are interpolated (as escaped text); the prose
    is this module's own wording.
    """
    checked = _escape(str(verification.bytes_checked))
    total = _escape(str(verification.bytes_total))
    if verification.coverage == "full":
        return (
            f"the whole drive was read back and checked "
            f"({checked} of {total} bytes)"
        )
    if verification.coverage == "sampled":
        regions = _escape(str(verification.regions_checked))
        sentence = (
            f"a sample of the drive was read back and checked "
            f"({checked} of {total} bytes across {regions} regions)"
        )
        if verification.firmware_status == "confirmed":
            sentence += "; the drive's own firmware status was also checked"
        elif verification.firmware_status == "not_available":
            sentence += (
                "; this method's firmware status cannot be read, so "
                "the result rests on the read-back sample"
            )
        return sentence
    if verification.bytes_checked > 0:
        return (
            f"only {checked} of {total} bytes could be read back "
            f"before the read failed"
        )
    return "no part of the drive was read back and checked"


def _facts_items(record: JobRecord) -> str:
    """The drive/method/time/operator facts, every value escaped."""
    items = [
        f"<li>Drive model: {_escape(record.model)}</li>",
        f"<li>Serial number: {_escape(record.serial)}</li>",
        f"<li>Capacity: {_escape(_human_size(record.size_bytes))}</li>",
        f"<li>Interface: {_escape(record.interface)}</li>",
        f"<li>Method used: {_escape(record.method_label)}</li>",
        f"<li>NIST 800-88 category: {_escape(record.nist_category)}</li>",
        f"<li>Started: {_escape(record.started_at or 'Not recorded')}</li>",
        f"<li>Finished: {_escape(record.completed_at)}</li>",
        f"<li>Bytes written during the wipe: {_escape(str(record.bytes_written))}</li>",
        f"<li>Operator: {_escape(record.operator)}</li>",
    ]
    return "\n".join(items)


_CSS = """
body { font-family: Arial, Helvetica, sans-serif; color: #000000;
       background: #ffffff; margin: 2rem; line-height: 1.5; }
.header { background: #0c130d; color: #2ee06f; padding: 1.25rem 1.5rem;
          border-bottom: 6px solid #2ee06f; }
.header .brand { font-size: 0.95rem; letter-spacing: 0.08em;
                 text-transform: uppercase; }
.header h1 { margin: 0.25rem 0 0 0; font-size: 1.9rem; color: #2ee06f; }
h2 { border-bottom: 3px solid #2ee06f; padding-bottom: 0.25rem;
     margin-top: 1.75rem; }
ul.facts { list-style: none; padding-left: 0; }
ul.facts li { padding: 0.15rem 0; }
.banner { border: 6px solid #000000; padding: 1rem 1.25rem;
          margin: 1.5rem 0; font-size: 1.4rem; font-weight: bold;
          text-align: center; }
footer { margin-top: 2.5rem; border-top: 3px solid #2ee06f;
         padding-top: 0.75rem; font-size: 0.9rem; }
@media print {
  body { margin: 0; }
  .header { print-color-adjust: exact; -webkit-print-color-adjust: exact; }
}
"""


def _page(title: str, body: str) -> str:
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{_escape(title)}</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n{body}\n</body>\n</html>\n"
    )


# ---------------------------------------------------------------------------
# The documents
# ---------------------------------------------------------------------------


def render_certificate_html(record: JobRecord) -> str:
    """Render the certificate of destruction for a verified job.

    Defense in depth: the clean condition (and the verification
    record's authenticity) is re-checked here, so calling this
    renderer directly cannot mint a certificate for a failed,
    unverified, or forged job.
    """
    _require_clean(record)
    verification = record.verification
    assert verification is not None  # established by _require_clean
    body = f"""<header class="header">
<div class="brand">Sentinel Safeguard LLC</div>
<h1>Certificate of Destruction</h1>
</header>
<main>
<p>This certificate records that the drive listed below was
destroyed with BEERUS.EXE and that the destruction was verified.</p>
<h2>Drive</h2>
<ul class="facts">
{_facts_items(record)}
</ul>
<h2>Verification</h2>
<p>Verification result: PASS</p>
<p>Coverage: {_coverage_sentence(verification)}.</p>
<p>What verification found: {_escape(verification.detail)}</p>
<p>Verification checked at: {_escape(verification.checked_at)}</p>
<p>Record ID: {_escape(_record_id(record))}</p>
</main>
<footer>BEERUS.EXE v{_escape(record.tool_version)} — generated locally by Beerus. This certificate is the client's business record of this destruction.</footer>"""
    return _page("Certificate of Destruction", body)


def render_failure_report_html(record: JobRecord) -> str:
    """Render the failure report. Always available for honest records.

    The same authenticity and binding refusal as every other
    document path applies here: a forged or mismatched verification
    record gets no document of any kind, not even this one.
    """
    _require_verification_binding(record)
    verification = record.verification
    if verification is None:
        found = (
            "<p>No verification result exists for this job, so the "
            "destruction cannot be certified.</p>"
        )
    else:
        found_lines = [
            f"<p>Verification result: {_escape(verification.verdict.name)}</p>",
            f"<p>What verification found: {_escape(verification.detail)}</p>",
        ]
        if verification.remnants_found:
            offsets = ", ".join(
                _escape(str(o)) for o in verification.first_remnant_offsets
            )
            found_lines.append(
                f"<p>Remnants found: {_escape(str(verification.remnants_found))} "
                f"bytes that may still hold old data. First remnant "
                f"offsets: {offsets}.</p>"
            )
        found_lines.append(
            f"<p>Coverage: {_coverage_sentence(verification)}.</p>"
        )
        found = "\n".join(found_lines)

    if record.wipe_ok:
        attempted_wipe = (
            "The wipe itself finished and reported success, but "
            "verification did not confirm the destruction."
        )
    else:
        attempted_wipe = "The wipe itself did not report success."

    body = f"""<header class="header">
<div class="brand">Sentinel Safeguard LLC</div>
<h1>Destruction NOT Verified — Failure Report</h1>
</header>
<main>
<div class="banner">THIS IS NOT A CERTIFICATE OF DESTRUCTION</div>
<p>The destruction of the drive listed below could not be
verified. Its data must not be treated as gone.</p>
<h2>Drive and job</h2>
<ul class="facts">
{_facts_items(record)}
</ul>
<h2>What was attempted</h2>
<p>The drive was wiped using {_escape(record.method_label)} (NIST 800-88 category: {_escape(record.nist_category)}). {attempted_wipe}</p>
<h2>What verification found</h2>
{found}
<h2>What to do next</h2>
<ol>
<li>Run the wipe again and let verification complete.</li>
<li>If it fails again, use a stronger method (the roadmap's method ladder) or a different tool.</li>
<li>If the drive still cannot be verified clean, physically destroy the drive.</li>
<li>Do not return the drive to service and do not treat its data as gone until one of those is done.</li>
</ol>
<p>Record ID: {_escape(_record_id(record))}</p>
</main>
<footer>BEERUS.EXE v{_escape(record.tool_version)} — generated locally by Beerus. This failure report is the client's business record of this job.</footer>"""
    return _page("Destruction NOT Verified — Failure Report", body)


# ---------------------------------------------------------------------------
# Writing documents to disk
# ---------------------------------------------------------------------------


_DATE_HEAD_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _date_stamp(completed_at: str) -> str:
    """yyyymmdd from the record's completed_at, parsed defensively.

    Only the first ten characters are considered, and only when
    they look exactly like YYYY-MM-DD; anything else is "undated"
    rather than digits scavenged from an arbitrary string.
    """
    head = str(completed_at)[:10]
    if _DATE_HEAD_RE.fullmatch(head):
        return head.replace("-", "")
    return "undated"


def write_document(document: IssuedDocument, directory) -> Path:
    """Write an issued document as a single HTML file.

    The document is re-derived from its record first: a document
    whose kind or HTML is not exactly what Beerus would issue for
    that record is refused and nothing is written. The filename is
    ``beerus-{certificate|failure-report}-{serial}-{yyyymmdd}-{record_id}.html``
    with the serial sanitized to [A-Za-z0-9._-] so a serial can
    never smuggle a path separator into the filename, and the
    record ID in the name means two different jobs never collide.
    An existing file is never overwritten — these are business
    records, and a silent replacement would destroy one.
    """
    expected = issue_document(document.record)
    if document.kind != expected.kind or document.html != expected.html:
        raise CertificateError(
            "This document is not the document Beerus issued for "
            "this record, so nothing was written.",
            detail="document kind/HTML does not match a fresh issue",
        )
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    kind_token = (
        "certificate" if document.kind == "certificate" else "failure-report"
    )
    safe_serial = _SERIAL_SANITIZE_RE.sub("-", document.record.serial)
    filename = (
        f"beerus-{kind_token}-{safe_serial}-"
        f"{_date_stamp(document.record.completed_at)}-"
        f"{_record_id(document.record)}.html"
    )
    path = target_dir / filename
    if path.exists():
        raise CertificateError(
            "A document file for this record already exists and "
            "will not be overwritten, so nothing was written.",
            detail=f"refusing to overwrite {path.name}",
        )
    try:
        with path.open("x", encoding="utf-8") as fh:
            fh.write(document.html)
    except FileExistsError:
        raise CertificateError(
            "A document file for this record already exists and "
            "will not be overwritten, so nothing was written.",
            detail=f"refusing to overwrite {path.name}",
        ) from None
    return path
