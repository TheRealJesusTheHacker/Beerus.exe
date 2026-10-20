"""BEERUS.EXE — Sentinel Safeguard LLC secure drive destruction (the Destruct Key Pro).

Phase 1 + 2 library: drive inventory, NIST 800-88 method selection,
the consent-gate framework, the wipe engine, post-wipe verification,
and the certificate of destruction. The engine refuses to run
without a completed gate sequence, refuses drives it cannot
identify, and refuses the drive Beerus booted from — always. A
wipe that cannot be verified never earns a clean certificate:
failed and unverifiable wipes get a plainly-marked failure report
instead.

Phase 2.5 adds ``beerus.filewipe``: file, folder, and free-space
wiping for the portable app edition (``beerus_portable``) — the
same rule at file level: the selected data is overwritten and
read back before its entry is removed, never merely unlinked,
under the same consent discipline, with signed wipe reports.
"""

from .certificate import (
    IssuedDocument,
    JobRecord,
    build_job_record,
    issue_document,
    render_certificate_html,
    render_failure_report_html,
    write_document,
)
from .engine import TargetAssessment, WipeEngine, WipeResult, assess_target
from .errors import (
    BeerusError,
    BootDeviceError,
    CertificateError,
    ConfirmationError,
    ExecutorError,
    ExecutorNotArmedError,
    GateError,
    IdentityMismatchError,
    MethodUnavailableError,
    RefusalError,
    ScopeNotAcknowledgedError,
    TokenError,
    UnidentifiableDriveError,
    UnsafeTargetError,
    WipeFailedError,
)
from .executor import ExecutionReport, FileExecutor, RealExecutor
from .gates import (
    ConfirmationToken,
    GateAuthority,
    GateSession,
    GateStage,
    ScopeStatement,
    build_scope_text,
    scope_hash_for,
)
from .inventory import Drive, DriveClass, enumerate_drives, find_drive, parse_lsblk_json
from .methods import MethodKind, PassSpec, WipePlan, WipeStep, select_plan
from .progress import EventKind, ProgressEvent
from .verify import (
    DeviceSurfaceReader,
    FileSurfaceReader,
    SurfaceReader,
    VerificationResult,
    VerificationVerdict,
    WipeVerifier,
    check_nvme_firmware_status,
    unverified_result,
    verify_result_authentic,
)

__version__ = "0.4.0"

__all__ = [
    "BeerusError",
    "BootDeviceError",
    "CertificateError",
    "ConfirmationError",
    "ConfirmationToken",
    "DeviceSurfaceReader",
    "Drive",
    "DriveClass",
    "EventKind",
    "ExecutionReport",
    "ExecutorError",
    "ExecutorNotArmedError",
    "FileExecutor",
    "FileSurfaceReader",
    "GateAuthority",
    "GateError",
    "GateSession",
    "GateStage",
    "IdentityMismatchError",
    "IssuedDocument",
    "JobRecord",
    "MethodKind",
    "MethodUnavailableError",
    "PassSpec",
    "ProgressEvent",
    "RealExecutor",
    "RefusalError",
    "ScopeNotAcknowledgedError",
    "ScopeStatement",
    "SurfaceReader",
    "TargetAssessment",
    "TokenError",
    "UnidentifiableDriveError",
    "UnsafeTargetError",
    "VerificationResult",
    "VerificationVerdict",
    "WipeEngine",
    "WipeFailedError",
    "WipePlan",
    "WipeResult",
    "WipeStep",
    "WipeVerifier",
    "assess_target",
    "build_job_record",
    "build_scope_text",
    "check_nvme_firmware_status",
    "enumerate_drives",
    "find_drive",
    "issue_document",
    "parse_lsblk_json",
    "render_certificate_html",
    "render_failure_report_html",
    "scope_hash_for",
    "select_plan",
    "unverified_result",
    "verify_result_authentic",
    "write_document",
]
