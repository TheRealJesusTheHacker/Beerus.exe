"""BEERUS.EXE — Sentinel Safeguard LLC secure drive destruction (the Destruct Key Pro).

Phase 1 library: drive inventory, NIST 800-88 method selection, the
consent-gate framework, and the wipe engine. The engine refuses to
run without a completed gate sequence, refuses drives it cannot
identify, and refuses the drive Beerus booted from — always.
"""

from .engine import TargetAssessment, WipeEngine, WipeResult, assess_target
from .errors import (
    BeerusError,
    BootDeviceError,
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

__version__ = "0.2.0"

__all__ = [
    "BeerusError",
    "BootDeviceError",
    "ConfirmationError",
    "ConfirmationToken",
    "Drive",
    "DriveClass",
    "EventKind",
    "ExecutionReport",
    "ExecutorError",
    "ExecutorNotArmedError",
    "FileExecutor",
    "GateAuthority",
    "GateError",
    "GateSession",
    "GateStage",
    "IdentityMismatchError",
    "MethodKind",
    "MethodUnavailableError",
    "PassSpec",
    "ProgressEvent",
    "RealExecutor",
    "RefusalError",
    "ScopeNotAcknowledgedError",
    "ScopeStatement",
    "TargetAssessment",
    "TokenError",
    "UnidentifiableDriveError",
    "UnsafeTargetError",
    "WipeEngine",
    "WipeFailedError",
    "WipePlan",
    "WipeResult",
    "WipeStep",
    "assess_target",
    "build_scope_text",
    "enumerate_drives",
    "find_drive",
    "parse_lsblk_json",
    "scope_hash_for",
    "select_plan",
]
