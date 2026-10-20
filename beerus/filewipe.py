"""File, folder, and free-space wiping for Beerus — Phase 2.5.

The owner's rule for this module is the same as for whole drives:
the selected data itself is destroyed, never merely unlinked.
Every selected file is overwritten in place — every pass of its
overwrite plan, over its full length including the tail — flushed
to disk, and READ BACK AND VERIFIED while its entry still exists.
Only after verification does the entry go away, as the last step,
by rename-to-random + truncate + delete. A file whose overwrite
or verification fails keeps its entry and is reported per-item
with its reason; nothing is forced and nothing is silently
skipped.

The scope is fixed before anything runs. Folders are walked at
scope-build time: the file count and total bytes the operator
confirms are exactly what execution can touch. Symbolic links
and junctions are recorded in the scope but never followed, so a
link cannot widen a wipe beyond what was announced.

Consent mirrors the drive gates: a scope acknowledgment over the
exact listing, an exact typed phrase naming the count, and a
short-lived HMAC-bound token the engines require. The report this
module issues is a wipe REPORT — signed like a certificate, with
the same anti-forgery posture — but it is not a certificate of
destruction; that document stays reserved for whole-drive work.

Honesty, in plain language, on screen and in the report:

- On an SSD, overwriting a file's logical blocks cannot guarantee
  the physical cells that held the data were rewritten
  (wear-leveling and TRIM sit between the file system and the
  cells). Whole-drive sanitize is the strong guarantee.
- Copies of a file's data can exist outside the selected files:
  shadow copies, temp files, thumbnails, search indexes, file
  system journals. Wiping the selected files does not reach those.

Nothing in this module is armed at import: :class:`FileWipeEngine`
and :class:`FreeSpaceEngine` require a gate token from this
module's authority before they write a single byte.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import html
import os
import secrets
import shutil
import stat
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from beerus.certificate import CertificateError, _page
from beerus.errors import (
    BeerusError,
    RefusalError,
    TokenError,
    WipeFailedError,
)
from beerus.executor import _pattern_chunk, run_overwrite
from beerus.methods import OVERWRITE_PASSES
from beerus.progress import EventKind, ProgressEvent

__all__ = [
    "SSD_FILE_DISCLOSURE",
    "SSD_FREE_SPACE_DISCLOSURE",
    "OTHER_COPIES_DISCLOSURE",
    "FileGateAuthority",
    "FileGateSession",
    "FileConfirmationToken",
    "FileItemOutcome",
    "FileScope",
    "FileScopeEntry",
    "FileScopeError",
    "FileWipeEngine",
    "FileWipeResult",
    "FreeSpaceEngine",
    "FreeSpaceResult",
    "FreeSpaceScope",
    "FileReport",
    "build_file_scope",
    "build_file_scope_text",
    "build_free_space_scope",
    "build_free_space_scope_text",
    "build_file_report",
    "build_free_space_report",
    "render_file_report_html",
    "verify_file_result_authentic",
    "verify_free_space_result_authentic",
    "write_file_report",
    "default_protected_roots",
    "is_protected_path",
]

# --------------------------------------------------------------------------
# Disclosures — pinned wording, shown at mode selection, in the
# scope statement, and in the report. Tests pin their presence.
# --------------------------------------------------------------------------

SSD_FILE_DISCLOSURE = (
    "About SSDs: overwriting a file on an SSD cannot guarantee the "
    "physical cells that held its data were rewritten. The drive "
    "moves data around on its own (wear-leveling) and may already "
    "have discarded the old cells (TRIM). The strong guarantee is "
    "a whole-drive sanitize from the Drive mode — Beerus says this "
    "plainly rather than pretend a file overwrite is the same thing."
)

OTHER_COPIES_DISCLOSURE = (
    "Other copies can exist outside the files you pick: shadow "
    "copies, temporary files, thumbnails, search indexes, and file "
    "system journals may hold some of the same data. Wiping the "
    "selected files does not reach those copies."
)

SSD_FREE_SPACE_DISCLOSURE = (
    "About SSDs: on an SSD the system may already have erased "
    "deleted data (TRIM), and filling free space cannot reach "
    "cells the drive keeps in reserve. The strong guarantee is a "
    "whole-drive sanitize from the Drive mode."
)

# Outcome statuses.
WIPED_VERIFIED = "wiped_verified"
WIPED_UNVERIFIED = "wiped_unverified"
FAILED = "failed"
SKIPPED = "skipped"

_CHUNK = 1024 * 1024
_FILL_CHUNK = 16 * 1024 * 1024

# The report-signing key: per process, exactly like the drive
# verification key — a report is mintable and checkable only in
# the run that produced it.
_REPORT_KEY = secrets.token_bytes(32)
_SIGNING_CONTEXT = b"beerus-file-report-v1|"


def _mac(payload: str) -> str:
    return hmac.new(
        _REPORT_KEY,
        _SIGNING_CONTEXT + payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _canonical_result(
    kind: str,
    scope_hash: str,
    started_at: float,
    completed_at: float,
    lines: tuple[str, ...],
) -> str:
    payload = "\n".join(
        [kind, scope_hash, repr(started_at), repr(completed_at), *lines]
    )
    return _mac(payload)


# --------------------------------------------------------------------------
# Protected locations
# --------------------------------------------------------------------------


def default_protected_roots() -> tuple[str, ...]:
    """Operating-system locations file mode will never enter."""
    roots = []
    system_root = os.environ.get("SystemRoot") or os.environ.get("windir")
    if system_root:
        roots.append(system_root)
    else:
        roots.append("C:\\Windows")
    program_data = os.environ.get("ProgramData")
    if program_data:
        roots.append(program_data)
    else:
        roots.append("C:\\ProgramData")
    roots.append("C:\\System Volume Information")
    roots.append("C:\\$Recycle.Bin")
    return tuple(roots)


def _normalized(path) -> str:
    text = os.path.normpath(os.path.abspath(str(path)))
    if os.name == "nt":
        text = text.lower()
    return text


def is_protected_path(path, protected_roots) -> str | None:
    """The protected root containing ``path``, or ``None``."""
    target = _normalized(path)
    for root in protected_roots:
        base = _normalized(root)
        if target == base or target.startswith(base + os.sep):
            return root
    return None


# --------------------------------------------------------------------------
# Scope
# --------------------------------------------------------------------------


class FileScopeError(RefusalError):
    """A selection that cannot become a scope. Nothing was wiped."""


@dataclass(frozen=True)
class FileScopeEntry:
    path: str
    size_bytes: int
    kind: str  # "file" | "link" | "special"


@dataclass(frozen=True)
class FileScope:
    root_paths: tuple[str, ...]
    entries: tuple[FileScopeEntry, ...]
    directories: tuple[str, ...]
    total_files: int
    total_bytes: int
    scope_hash: str

    @property
    def expected_phrase(self) -> str:
        noun = "FILE" if self.total_files == 1 else "FILES"
        return f"ERASE {self.total_files} {noun}"


@dataclass(frozen=True)
class FreeSpaceScope:
    volume_root: str
    free_bytes_estimate: int
    reserve_bytes: int
    scope_hash: str

    @property
    def expected_fill_bytes(self) -> int:
        return max(0, self.free_bytes_estimate - self.reserve_bytes)

    @property
    def expected_phrase(self) -> str:
        return "WIPE FREE SPACE"


def build_file_scope(paths, *, protected_roots=None) -> FileScope:
    """Fix the exact scope for a file/folder wipe.

    Walks folders NOW (never during execution), records links
    without following them, refuses protected OS locations
    plainly, and deduplicates — the confirmed count is the whole
    truth about what a wipe can touch.
    """
    if protected_roots is None:
        protected_roots = default_protected_roots()
    roots: list[str] = []
    entries: dict[str, FileScopeEntry] = {}
    directories: set[str] = set()

    def add_entry(path: str, kind: str, size: int) -> None:
        key = _normalized(path)
        if key not in entries:
            entries[key] = FileScopeEntry(path=path, size_bytes=size, kind=kind)

    def refuse_if_protected(path: str) -> None:
        root = is_protected_path(path, protected_roots)
        if root is not None:
            raise FileScopeError(
                f"“{path}” is inside a protected Windows folder "
                f"({root}). Beerus will not wipe operating-system "
                "files. Nothing was wiped — remove that item and "
                "build the list again."
            )

    for raw in paths:
        path = os.path.normpath(os.path.abspath(str(raw)))
        if not os.path.lexists(path):
            raise FileScopeError(
                f"“{path}” could not be found. Nothing was "
                "wiped. Check the name and build the list again."
            )
        refuse_if_protected(path)
        roots.append(path)
        if os.path.islink(path):
            add_entry(path, "link", 0)
            continue
        if os.path.isfile(path):
            add_entry(path, "file", os.path.getsize(path))
            continue
        if os.path.isdir(path):
            directories.add(path)
            for dirpath, dirnames, filenames in os.walk(
                path, followlinks=False
            ):
                refuse_if_protected(dirpath)
                real_dirs = []
                for name in sorted(dirnames):
                    full = os.path.join(dirpath, name)
                    if os.path.islink(full):
                        add_entry(full, "link", 0)
                    else:
                        real_dirs.append(name)
                        directories.add(full)
                dirnames[:] = real_dirs
                for name in sorted(filenames):
                    full = os.path.join(dirpath, name)
                    if os.path.islink(full):
                        add_entry(full, "link", 0)
                    elif os.path.isfile(full):
                        add_entry(full, "file", os.path.getsize(full))
                    else:
                        add_entry(full, "special", 0)
            continue
        add_entry(path, "special", 0)

    ordered = tuple(sorted(entries.values(), key=lambda e: e.path))
    file_entries = [e for e in ordered if e.kind == "file"]
    total_files = len(file_entries)
    total_bytes = sum(e.size_bytes for e in file_entries)
    if total_files == 0:
        raise FileScopeError(
            "That selection has no regular files to wipe in it. "
            "Nothing was wiped. Pick files, or folders that "
            "contain files."
        )
    digest = hashlib.sha256()
    for entry in ordered:
        digest.update(
            f"{entry.kind}|{entry.size_bytes}|{entry.path}\n".encode(
                "utf-8", "surrogateescape"
            )
        )
    return FileScope(
        root_paths=tuple(roots),
        entries=ordered,
        directories=tuple(sorted(directories)),
        total_files=total_files,
        total_bytes=total_bytes,
        scope_hash=digest.hexdigest(),
    )


def _human_size(n: int) -> str:
    value = float(n)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return (
                f"{int(value)} {unit}"
                if unit == "bytes"
                else f"{value:.1f} {unit}"
            )
        value /= 1024
    return f"{n} bytes"


def build_file_scope_text(scope: FileScope, *, ssd: bool) -> str:
    """The scope statement: the exact listing the operator confirms."""
    lines = [
        f"Beerus will wipe {scope.total_files} file(s), "
        f"{_human_size(scope.total_bytes)} of data in total.",
        "",
        "Every file below is overwritten in full, read back and "
        "checked, and only then removed. Folders in the list are "
        "removed once everything in them is gone. Anything not "
        "listed here is not touched.",
        "",
    ]
    for entry in scope.entries:
        if entry.kind == "file":
            lines.append(f"  {entry.path}  ({_human_size(entry.size_bytes)})")
        elif entry.kind == "link":
            lines.append(
                f"  {entry.path}  (a link — links are never "
                "followed; the link itself is removed last, its "
                "target is not touched)"
            )
        else:
            lines.append(
                f"  {entry.path}  (not a regular file — it will "
                "be reported, not wiped)"
            )
    lines += ["", OTHER_COPIES_DISCLOSURE]
    if ssd:
        lines += ["", SSD_FILE_DISCLOSURE]
    return "\n".join(lines)


def build_free_space_scope(
    volume_root, *, free_bytes_probe=None, reserve_bytes=None
) -> FreeSpaceScope:
    """Fix the exact scope for a free-space wipe of one volume."""
    root = os.path.normpath(os.path.abspath(str(volume_root)))
    if not os.path.isdir(root):
        raise FileScopeError(
            f"“{root}” is not a folder Beerus can work in. "
            "Nothing was wiped. Pick the volume again."
        )
    if free_bytes_probe is None:
        free_bytes_probe = lambda p: shutil.disk_usage(p).free  # noqa: E731
    try:
        free = int(free_bytes_probe(root))
    except OSError:
        raise FileScopeError(
            "Beerus could not read how much free space that "
            "volume has. Nothing was wiped."
        ) from None
    if reserve_bytes is None:
        reserve_bytes = max(16 * 1024 * 1024, free // 50)
    digest = hashlib.sha256(
        f"freespace|{root}|{free}|{reserve_bytes}".encode(
            "utf-8", "surrogateescape"
        )
    ).hexdigest()
    return FreeSpaceScope(
        volume_root=root,
        free_bytes_estimate=free,
        reserve_bytes=int(reserve_bytes),
        scope_hash=digest,
    )


def build_free_space_scope_text(scope: FreeSpaceScope, *, ssd: bool) -> str:
    lines = [
        f"Beerus will fill the free space on {scope.volume_root} "
        f"with overwrite passes — about "
        f"{_human_size(scope.expected_fill_bytes)} of filler "
        "data — then remove the filler.",
        "",
        "Existing files and the operating system are not touched. "
        f"A safety margin of {_human_size(scope.reserve_bytes)} "
        "is always left empty so the system keeps running "
        "normally while the wipe works.",
        "",
        "This targets data from files that were deleted earlier, "
        "so it cannot be recovered afterwards.",
    ]
    if ssd:
        lines += ["", SSD_FREE_SPACE_DISCLOSURE]
    lines += ["", OTHER_COPIES_DISCLOSURE]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Gate — the file-mode consent authority.
# --------------------------------------------------------------------------


class FileSessionStage(Enum):
    STARTED = "started"
    SCOPE_ACKNOWLEDGED = "scope_acknowledged"
    COMPLETED = "completed"
    INVALIDATED = "invalidated"


@dataclass(frozen=True)
class FileConfirmationToken:
    scope_hash: str
    session_id: str
    expires_at: float
    mac: str


class FileGateAuthority:
    """Issues file-mode consent tokens; verifies them at execution.

    The token binds to the scope hash — a scope that changes by
    one byte of one path produces a different hash and the token
    no longer authorizes anything.
    """

    def __init__(self, token_ttl_seconds: float = 300.0):
        self._key = secrets.token_bytes(32)
        self._ttl = float(token_ttl_seconds)
        self._consumed: set[str] = set()

    def begin_session(self, scope) -> "FileGateSession":
        return FileGateSession(self, scope)

    def _mac(self, scope_hash: str, session_id: str, expires_at: float) -> str:
        payload = f"{scope_hash}|{session_id}|{expires_at!r}"
        return hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()

    def _mint(self, scope, session_id: str) -> FileConfirmationToken:
        expires_at = time.time() + self._ttl
        return FileConfirmationToken(
            scope_hash=scope.scope_hash,
            session_id=session_id,
            expires_at=expires_at,
            mac=self._mac(scope.scope_hash, session_id, expires_at),
        )

    def verify(self, scope, token) -> bool:
        if token is None:
            return False
        expected = self._mac(token.scope_hash, token.session_id, token.expires_at)
        if not hmac.compare_digest(expected, token.mac):
            return False
        if token.scope_hash != scope.scope_hash:
            return False
        if time.time() > token.expires_at:
            return False
        if token.session_id in self._consumed:
            return False
        return True

    def consume(self, token) -> None:
        self._consumed.add(token.session_id)


class FileGateSession:
    """One operator's walk through the file-mode consent gates."""

    def __init__(self, authority: FileGateAuthority, scope):
        self._authority = authority
        self._scope = scope
        self.scope_hash = scope.scope_hash
        self.expected_phrase = scope.expected_phrase
        self.session_id = secrets.token_hex(16)
        self.stage = FileSessionStage.STARTED

    def _require(self, *stages: FileSessionStage) -> None:
        if self.stage is FileSessionStage.INVALIDATED:
            raise TokenError(
                "This confirmation was cancelled. Nothing was "
                "wiped. Start again from the scope screen."
            )
        if self.stage not in stages:
            raise TokenError(
                "The confirmation steps must be completed in order."
            )

    def _invalidate(self) -> None:
        self.stage = FileSessionStage.INVALIDATED

    def acknowledge_scope(self) -> str:
        self._require(FileSessionStage.STARTED)
        self.stage = FileSessionStage.SCOPE_ACKNOWLEDGED
        if isinstance(self._scope, FileScope):
            return build_file_scope_text(self._scope, ssd=False)
        return build_free_space_scope_text(self._scope, ssd=False)

    def final_confirmation(self, typed: str) -> FileConfirmationToken:
        self._require(FileSessionStage.SCOPE_ACKNOWLEDGED)
        if (typed or "").strip() != self.expected_phrase:
            self._invalidate()
            raise TokenError(
                "That is not the exact confirmation phrase. The "
                "confirmation was cancelled and nothing was wiped. "
                "Start again when you are ready."
            )
        self.stage = FileSessionStage.COMPLETED
        return self._authority._mint(self._scope, self.session_id)


# --------------------------------------------------------------------------
# File wipe execution
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FileItemOutcome:
    path: str
    status: str  # WIPED_VERIFIED | WIPED_UNVERIFIED | FAILED | SKIPPED
    detail: str
    bytes_written: int = 0


@dataclass(frozen=True)
class FileWipeResult:
    scope_hash: str
    outcomes: tuple[FileItemOutcome, ...]
    started_at: float
    completed_at: float
    directories_removed: int
    directories_left: int
    mac: str = field(repr=False)

    @property
    def wiped_verified(self) -> int:
        return sum(1 for o in self.outcomes if o.status == WIPED_VERIFIED)

    @property
    def wiped_unverified(self) -> int:
        return sum(1 for o in self.outcomes if o.status == WIPED_UNVERIFIED)

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if o.status == FAILED)

    @property
    def skipped(self) -> int:
        return sum(1 for o in self.outcomes if o.status == SKIPPED)

    @property
    def total_bytes_written(self) -> int:
        return sum(o.bytes_written for o in self.outcomes)


@dataclass(frozen=True)
class FreeSpaceResult:
    scope_hash: str
    volume_root: str
    ok: bool
    bytes_filled: int
    passes_completed: int
    bytes_checked: int
    detail: str
    started_at: float
    completed_at: float
    mac: str = field(repr=False)


def _outcome_lines(result: FileWipeResult) -> tuple[str, ...]:
    return tuple(
        f"{o.path}|{o.status}|{o.detail}|{o.bytes_written}"
        for o in result.outcomes
    ) + (
        f"dirs|{result.directories_removed}|{result.directories_left}",
    )


def verify_file_result_authentic(result: FileWipeResult) -> bool:
    expected = _canonical_result(
        "file",
        result.scope_hash,
        result.started_at,
        result.completed_at,
        _outcome_lines(result),
    )
    return hmac.compare_digest(expected, result.mac)


def verify_free_space_result_authentic(result: FreeSpaceResult) -> bool:
    lines = (
        f"{result.volume_root}|{result.ok}|{result.bytes_filled}"
        f"|{result.passes_completed}|{result.bytes_checked}"
        f"|{result.detail}",
    )
    expected = _canonical_result(
        "freespace",
        result.scope_hash,
        result.started_at,
        result.completed_at,
        lines,
    )
    return hmac.compare_digest(expected, result.mac)


_LOCKED_DETAIL = (
    "Windows has this file locked or refused access (it may be "
    "open in another program). Its data was NOT wiped and the "
    "file was left exactly as it was. Close the program using "
    "it and run the wipe again."
)

_CHANGED_DETAIL = (
    "This changed after the scope was confirmed, so it was left "
    "alone — Beerus only touches exactly what was confirmed. Run "
    "the wipe again to include its new state."
)


def _fsync_directory(path: str) -> None:
    if os.name != "posix":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _silent_emit(event) -> None:
    pass


class FileWipeEngine:
    """Overwrites, verifies, and only then removes scoped files."""

    def __init__(self, authority: FileGateAuthority, *, open_seam=None):
        self._authority = authority
        # open_seam exists so tests can simulate a Windows-locked
        # file deterministically. It receives (path, flags,
        # original_open) and returns a file descriptor.
        self._open_seam = open_seam

    def _open_write(self, path: str) -> int:
        flags = os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if self._open_seam is not None:
            return self._open_seam(path, flags, os.open)
        return os.open(path, flags)

    def execute(self, scope: FileScope, token, emit=None) -> FileWipeResult:
        if not self._authority.verify(scope, token):
            raise TokenError(
                "This wipe was not confirmed, or the confirmation "
                "expired or was already used. Nothing was wiped. "
                "Confirm the scope again."
            )
        self._authority.consume(token)
        emit = emit if emit is not None else _silent_emit
        started = time.time()
        outcomes: list[FileItemOutcome] = []
        for entry in scope.entries:
            outcomes.append(self._run_entry(entry, scope, emit))
        removed, left = self._remove_directories(scope, outcomes)
        completed = time.time()
        draft = FileWipeResult(
            scope_hash=scope.scope_hash,
            outcomes=tuple(outcomes),
            started_at=started,
            completed_at=completed,
            directories_removed=removed,
            directories_left=left,
            mac="",
        )
        mac = _canonical_result(
            "file",
            draft.scope_hash,
            draft.started_at,
            draft.completed_at,
            _outcome_lines(draft),
        )
        return dataclasses.replace(draft, mac=mac)

    # -- per item -----------------------------------------------------

    def _run_entry(self, entry: FileScopeEntry, scope, emit) -> FileItemOutcome:
        path = entry.path
        if entry.kind == "link":
            return self._remove_link_entry(entry)
        if entry.kind != "file":
            return FileItemOutcome(
                path,
                SKIPPED,
                "This is not a regular file (it is a special "
                "system entry). It was left exactly as it was.",
            )
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return FileItemOutcome(
                path,
                FAILED,
                "The file was gone before Beerus could wipe it "
                "(something else removed it first).",
            )
        except OSError:
            return FileItemOutcome(path, FAILED, _LOCKED_DETAIL)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            return FileItemOutcome(path, FAILED, _CHANGED_DETAIL)
        if st.st_size != entry.size_bytes:
            return FileItemOutcome(path, FAILED, _CHANGED_DETAIL)
        if is_protected_path(path, default_protected_roots()) is not None:
            return FileItemOutcome(
                path,
                FAILED,
                "This file is inside a protected Windows folder. "
                "It was not wiped.",
            )

        size = entry.size_bytes
        emit(
            ProgressEvent(
                EventKind.STEP_STARTED,
                f"Wiping {os.path.basename(path)}",
                path,
            )
        )
        bytes_written = 0
        if size > 0:
            try:
                fd = self._open_write(path)
            except PermissionError:
                return FileItemOutcome(path, FAILED, _LOCKED_DETAIL)
            except OSError:
                return FileItemOutcome(path, FAILED, _LOCKED_DETAIL)
            try:
                bytes_written = run_overwrite(
                    fd, size, OVERWRITE_PASSES, emit, path
                )
                os.fsync(fd)
            except BeerusError:
                return FileItemOutcome(
                    path,
                    FAILED,
                    "Writing to this file failed partway through. "
                    "Its data may be partly overwritten; the file "
                    "was left in place and was NOT verified.",
                )
            except OSError:
                return FileItemOutcome(path, FAILED, _LOCKED_DETAIL)
            finally:
                os.close(fd)
            verdict = self._verify(path, size)
            if verdict is None:
                return FileItemOutcome(
                    path,
                    FAILED,
                    "Read-back verification could not be "
                    "completed (the file could not be read after "
                    "overwriting). The file was left in place.",
                    bytes_written,
                )
            if verdict is not True:
                return FileItemOutcome(
                    path,
                    FAILED,
                    "Read-back verification found data that does "
                    f"not match the final overwrite pass (first "
                    f"mismatch at byte {verdict}). The file was "
                    "left in place — it was NOT wiped clean.",
                    bytes_written,
                )
            status = (
                WIPED_VERIFIED
                if OVERWRITE_PASSES[-1].pattern != "random"
                else WIPED_UNVERIFIED
            )
            detail = (
                "Overwritten in full and read back: every byte "
                "matches the final pass. The entry was then "
                "removed."
            )
        else:
            status = WIPED_VERIFIED
            detail = (
                "The file was already empty — there was no data "
                "to overwrite. Its entry was removed."
            )
        removal_note = self._remove_entry(path)
        if removal_note:
            detail += " " + removal_note
        emit(
            ProgressEvent(
                EventKind.STEP_COMPLETED, "File wiped", path, size, size
            )
        )
        return FileItemOutcome(path, status, detail, bytes_written)

    def _verify(self, path: str, size: int):
        """True = verified; int = first mismatch offset; None = unreadable."""
        final_pattern = OVERWRITE_PASSES[-1].pattern
        if final_pattern == "random":
            expected = None
        elif final_pattern == "zero":
            expected = b"\x00"
        elif final_pattern == "one":
            expected = b"\xff"
        else:  # pragma: no cover — plan patterns are closed
            expected = None
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(path, flags)
        except OSError:
            return None
        offset = 0
        try:
            while offset < size:
                chunk = os.read(fd, min(_CHUNK, size - offset))
                if not chunk:
                    return offset  # shrank during verification
                if expected is not None and chunk != expected * len(chunk):
                    for i, byte in enumerate(chunk):
                        if byte != expected[0]:
                            return offset + i
                offset += len(chunk)
        except OSError:
            return None
        finally:
            os.close(fd)
        return True

    def _remove_entry(self, path: str) -> str:
        """Rename-to-random + truncate + delete. Data is already gone."""
        directory = os.path.dirname(path) or "."
        new_path = os.path.join(
            directory, f".beerus-{secrets.token_hex(8)}"
        )
        try:
            os.rename(path, new_path)
            fd = os.open(new_path, os.O_WRONLY)
            try:
                os.ftruncate(fd, 0)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.unlink(new_path)
            _fsync_directory(directory)
            return ""
        except OSError:
            # Whichever name still exists: try to leave no blanked
            # entry behind, but never claim what didn't happen.
            for candidate in (new_path, path):
                try:
                    if os.path.lexists(candidate):
                        os.unlink(candidate)
                        return ""
                except OSError:
                    continue
            return (
                "Its data is destroyed, but its now-empty entry "
                "could not be removed (Windows refused) — an "
                "empty file with everything overwritten may still "
                "be listed in the folder."
            )

    def _remove_link_entry(self, entry: FileScopeEntry) -> FileItemOutcome:
        # A link carries no data of its own; removing it (last,
        # like every entry) cannot touch its target.
        try:
            os.unlink(entry.path)
            _fsync_directory(os.path.dirname(entry.path) or ".")
            return FileItemOutcome(
                entry.path,
                SKIPPED,
                "This was a link. Links are never followed — the "
                "link itself was removed and its target was not "
                "touched.",
            )
        except OSError:
            return FileItemOutcome(
                entry.path,
                SKIPPED,
                "This was a link. Links are never followed, so "
                "nothing was wiped through it; the link itself "
                "could not be removed (Windows refused). Its "
                "target was not touched.",
            )

    def _remove_directories(self, scope: FileScope, outcomes):
        failed_paths = {
            o.path for o in outcomes if o.status in (FAILED,)
        }
        removed = 0
        left = 0
        for directory in sorted(
            scope.directories, key=lambda p: p.count(os.sep), reverse=True
        ):
            if any(
                p == directory or p.startswith(directory + os.sep)
                for p in failed_paths
            ):
                left += 1
                continue
            try:
                os.rmdir(directory)
                removed += 1
            except OSError:
                left += 1
        if removed:
            _fsync_directory(str(Path(scope.directories[0]).parent))
        return removed, left


# --------------------------------------------------------------------------
# Free-space execution
# --------------------------------------------------------------------------


class FreeSpaceEngine:
    """Fills a volume's free space with overwrite passes."""

    def __init__(
        self,
        authority: FileGateAuthority,
        *,
        free_bytes_probe=None,
        chunk_bytes: int = _FILL_CHUNK,
    ):
        self._authority = authority
        self._probe = free_bytes_probe or (
            lambda p: shutil.disk_usage(p).free  # noqa: E731
        )
        self._chunk = chunk_bytes

    def execute(self, scope: FreeSpaceScope, token, emit=None) -> FreeSpaceResult:
        if not self._authority.verify(scope, token):
            raise TokenError(
                "This wipe was not confirmed, or the confirmation "
                "expired or was already used. Nothing was wiped. "
                "Confirm the scope again."
            )
        self._authority.consume(token)
        emit = emit if emit is not None else _silent_emit
        started = time.time()
        filler_dir = os.path.join(scope.volume_root, "Beerus free-space wipe")
        fillers: list[str] = []
        bytes_filled = 0
        passes_completed = 0
        detail_notes: list[str] = []
        try:
            os.makedirs(filler_dir, exist_ok=True)
        except OSError:
            raise WipeFailedError(
                "Beerus could not write to that volume, so the "
                "free-space wipe could not start. Nothing was "
                "written. Check the volume is connected and not "
                "read-only, then try again."
            ) from None
        ok = False
        bytes_checked_value = 0
        try:
            for index, spec in enumerate(OVERWRITE_PASSES):
                if index == 0:
                    bytes_filled = self._fill_pass(
                        scope, filler_dir, fillers, spec, emit, create=True
                    )
                else:
                    self._fill_pass(
                        scope, filler_dir, fillers, spec, emit, create=False
                    )
                passes_completed += 1
            bytes_checked = self._verify_fillers(fillers)
            ok = bytes_checked is not None
            if not ok:
                detail_notes.append(
                    "Read-back checks of the filler data did not "
                    "all match the final pass, so this run is "
                    "reported as NOT fully verified."
                )
            bytes_checked_value = bytes_checked or 0
        except OSError:
            detail_notes.append(
                "The free-space wipe stopped early because "
                "writing to the volume failed. The filler files "
                "were removed; whatever free space was filled "
                "before the failure kept its overwrites."
            )
        finally:
            leftovers = 0
            for filler in fillers:
                try:
                    os.unlink(filler)
                except OSError:
                    leftovers += 1
            try:
                os.rmdir(filler_dir)
            except OSError:
                leftovers += 1 if os.path.isdir(filler_dir) else 0
            if leftovers:
                detail_notes.append(
                    "Some filler files could not be removed — "
                    f"they are blank overwrite data inside "
                    f"“{filler_dir}”; you can delete that folder."
                )
        detail = (
            " ".join(detail_notes)
            if detail_notes
            else "Free space was filled through every pass and "
            "the filler was read back and removed."
        )
        completed = time.time()
        draft = FreeSpaceResult(
            scope_hash=scope.scope_hash,
            volume_root=scope.volume_root,
            ok=ok,
            bytes_filled=bytes_filled,
            passes_completed=passes_completed,
            bytes_checked=bytes_checked_value,
            detail=detail,
            started_at=started,
            completed_at=completed,
            mac="",
        )
        lines = (
            f"{draft.volume_root}|{draft.ok}|{draft.bytes_filled}"
            f"|{draft.passes_completed}|{draft.bytes_checked}"
            f"|{draft.detail}",
        )
        mac = _canonical_result(
            "freespace",
            draft.scope_hash,
            draft.started_at,
            draft.completed_at,
            lines,
        )
        return dataclasses.replace(draft, mac=mac)

    # One filler never grows past 2 GiB: several volumes (FAT32
    # sticks among them) refuse single files anywhere near their
    # per-file limit, and many medium files are safer than one
    # enormous one.
    _MAX_FILLER_BYTES = 2 * 1024 * 1024 * 1024

    def _fill_pass(self, scope, filler_dir, fillers, spec, emit, *, create):
        if create:
            target_bytes = scope.expected_fill_bytes
            written_total = 0
            number = 0
            empty_fillers = 0
            while written_total < target_bytes:
                number += 1
                filler = os.path.join(
                    filler_dir, f"filler-{number:04d}.bin"
                )
                try:
                    fd = os.open(
                        filler, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                    )
                except FileExistsError:
                    # Leftover from an interrupted run: its data
                    # is filler by definition; adopt and rewrite.
                    if filler not in fillers:
                        fillers.append(filler)
                    continue
                except OSError:
                    break
                fillers.append(filler)
                filler_target = min(
                    target_bytes - written_total, self._MAX_FILLER_BYTES
                )
                written_here = 0
                try:
                    while written_here < filler_target:
                        length = min(
                            self._chunk, filler_target - written_here
                        )
                        chunk = _pattern_chunk(spec.pattern, length)
                        view = memoryview(chunk)
                        while view:
                            n = os.write(fd, view)
                            view = view[n:]
                        written_here += length
                        written_total += length
                        emit(
                            ProgressEvent(
                                EventKind.PROGRESS,
                                f"Pass: {spec.label}",
                                scope.volume_root,
                                written_total,
                                target_bytes,
                            )
                        )
                    os.fsync(fd)
                except OSError:
                    # Out of space (or a per-file size limit) is
                    # the expected end of one filler's growth.
                    try:
                        os.fsync(fd)
                    except OSError:
                        pass
                finally:
                    os.close(fd)
                if written_here == 0:
                    empty_fillers += 1
                    if empty_fillers >= 2:
                        break
                else:
                    empty_fillers = 0
                # Stop early if the volume is genuinely full.
                try:
                    if self._probe(scope.volume_root) <= scope.reserve_bytes // 2:
                        break
                except OSError:
                    break
            return written_total
        # Rewrite passes: same fillers, same sizes, new pattern.
        for filler in fillers:
            size = os.path.getsize(filler)
            fd = os.open(filler, os.O_WRONLY)
            try:
                done = 0
                while done < size:
                    length = min(self._chunk, size - done)
                    chunk = _pattern_chunk(spec.pattern, length)
                    view = memoryview(chunk)
                    while view:
                        n = os.write(fd, view)
                        view = view[n:]
                    done += length
                os.fsync(fd)
            finally:
                os.close(fd)
            emit(
                ProgressEvent(
                    EventKind.PROGRESS,
                    f"Pass: {spec.label}",
                    scope.volume_root,
                )
            )
        return 0

    def _verify_fillers(self, fillers):
        """Sampled read-back; returns bytes checked, None on mismatch."""
        final_pattern = OVERWRITE_PASSES[-1].pattern
        if final_pattern == "random":
            expected = None
        elif final_pattern == "zero":
            expected = b"\x00"
        elif final_pattern == "one":
            expected = b"\xff"
        else:
            expected = None
        checked = 0
        window = 1024 * 1024
        for filler in fillers:
            try:
                size = os.path.getsize(filler)
                fd = os.open(filler, os.O_RDONLY)
            except OSError:
                return None
            try:
                spans = [(0, min(window, size))]
                if size > window:
                    mid = max(0, size // 2 - window // 2)
                    spans.append((mid, min(window, size - mid)))
                if size > 2 * window:
                    spans.append((size - window, window))
                for start, length in spans:
                    os.lseek(fd, start, os.SEEK_SET)
                    read = 0
                    while read < length:
                        chunk = os.read(fd, min(_CHUNK, length - read))
                        if not chunk:
                            return None
                        if (
                            expected is not None
                            and chunk != expected * len(chunk)
                        ):
                            return None
                        read += len(chunk)
                    checked += length
            except OSError:
                return None
            finally:
                os.close(fd)
        return checked


# --------------------------------------------------------------------------
# Report — a signed wipe report. NOT a certificate of destruction.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FileReport:
    kind: str  # "file-wipe-report" | "free-space-report"
    operator: str
    completed_at_text: str
    summary_lines: tuple[str, ...]
    item_lines: tuple[str, ...]
    disclosure_lines: tuple[str, ...]
    record_id: str


def _record_id(scope_hash: str, completed_at: float) -> str:
    digest = hashlib.sha256(
        f"{scope_hash}|{completed_at!r}".encode()
    ).hexdigest()
    return digest[:12]


def build_file_report(
    result: FileWipeResult,
    operator: str,
    *,
    ssd: bool,
    tool_version: str = "",
) -> FileReport:
    if not verify_file_result_authentic(result):
        raise CertificateError(
            "The wipe record cannot be confirmed as genuine in "
            "this run, so no report was issued. Reports are only "
            "issued for runs Beerus can stand behind."
        )
    completed = datetime.fromtimestamp(result.completed_at).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    summary = [
        f"Items confirmed in scope: "
        f"{result.wiped_verified + result.wiped_unverified + result.failed + result.skipped}",
        f"Wiped and verified: {result.wiped_verified}",
        f"Wiped, could not be verified (final pass is random): "
        f"{result.wiped_unverified}",
        f"Failed: {result.failed}",
        f"Skipped: {result.skipped}",
        f"Total data overwritten: {_human_size(result.total_bytes_written)}",
        f"Folders removed afterwards: {result.directories_removed} "
        f"(left standing: {result.directories_left})",
    ]
    labels = {
        WIPED_VERIFIED: "WIPED + VERIFIED",
        WIPED_UNVERIFIED: "WIPED — NOT VERIFIED",
        FAILED: "FAILED",
        SKIPPED: "SKIPPED",
    }
    items = tuple(
        f"{labels[o.status]} — {o.path} — {o.detail}"
        for o in result.outcomes
    )
    disclosures = [OTHER_COPIES_DISCLOSURE]
    if ssd:
        disclosures.insert(0, SSD_FILE_DISCLOSURE)
    return FileReport(
        kind="file-wipe-report",
        operator=operator,
        completed_at_text=completed,
        summary_lines=tuple(summary),
        item_lines=items,
        disclosure_lines=tuple(disclosures),
        record_id=_record_id(result.scope_hash, result.completed_at),
    )


def build_free_space_report(
    result: FreeSpaceResult,
    operator: str,
    *,
    ssd: bool,
    tool_version: str = "",
) -> FileReport:
    if not verify_free_space_result_authentic(result):
        raise CertificateError(
            "The wipe record cannot be confirmed as genuine in "
            "this run, so no report was issued. Reports are only "
            "issued for runs Beerus can stand behind."
        )
    completed = datetime.fromtimestamp(result.completed_at).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    summary = [
        f"Volume: {result.volume_root}",
        f"Result: {'COMPLETED' if result.ok else 'COMPLETED — NOT FULLY VERIFIED'}",
        f"Free space filled: {_human_size(result.bytes_filled)}",
        f"Passes completed: {result.passes_completed}",
        f"Filler data read back and checked: "
        f"{_human_size(result.bytes_checked)} (sampled)",
        result.detail,
    ]
    disclosures = [OTHER_COPIES_DISCLOSURE]
    if ssd:
        disclosures.insert(0, SSD_FREE_SPACE_DISCLOSURE)
    return FileReport(
        kind="free-space-report",
        operator=operator,
        completed_at_text=completed,
        summary_lines=tuple(summary),
        item_lines=(),
        disclosure_lines=tuple(disclosures),
        record_id=_record_id(result.scope_hash, result.completed_at),
    )


def render_file_report_html(report: FileReport) -> str:
    esc = html.escape
    title = (
        "File Wipe Report"
        if report.kind == "file-wipe-report"
        else "Free-Space Wipe Report"
    )
    items_html = ""
    if report.item_lines:
        lis = "\n".join(
            f"<li>{esc(line)}</li>" for line in report.item_lines
        )
        items_html = f"<h2>Item by item</h2>\n<ul>\n{lis}\n</ul>"
    summary_lis = "\n".join(
        f"<li>{esc(line)}</li>" for line in report.summary_lines
    )
    disclosures = "\n".join(
        f"<p>{esc(line)}</p>" for line in report.disclosure_lines
    )
    body = (
        f"<h1>{esc(title)}</h1>\n"
        '<p class="notice">This is a wipe report. It is not a '
        "certificate of destruction — certificates are issued "
        "only for whole-drive wipes with verified results.</p>\n"
        "<dl>"
        f"<dt>Completed</dt><dd>{esc(report.completed_at_text)}</dd>"
        f"<dt>Operator</dt><dd>{esc(report.operator)}</dd>"
        f"<dt>Report ID</dt><dd>{esc(report.record_id)}</dd>"
        "</dl>\n"
        f"<h2>Summary</h2>\n<ul>\n{summary_lis}\n</ul>\n"
        f"{items_html}\n"
        f"<h2>What this does and does not promise</h2>\n{disclosures}\n"
    )
    return _page(title, body)


def write_file_report(report: FileReport, directory) -> Path:
    """Write a report HTML file; reports are never overwritten."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    base = directory / f"beerus-{report.kind}-{day}-{report.record_id}.html"
    candidate = base
    suffix = 2
    while candidate.exists():
        candidate = directory / (
            f"beerus-{report.kind}-{day}-{report.record_id}-{suffix}.html"
        )
        suffix += 1
    candidate.write_text(render_file_report_html(report), encoding="utf-8")
    return candidate
