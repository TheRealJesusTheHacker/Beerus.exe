"""Tests for file, folder, and free-space wiping (beerus.filewipe).

The safety invariants under test, in the owner's words: the
selected files' DATA is destroyed — overwritten in full and read
back BEFORE the entry is removed — never merely unlinked; the
confirmed scope is the whole scope (links never widen it); and
anything that cannot be wiped is reported per-item with its
reason, never forced, never silently skipped.
"""

from __future__ import annotations

import dataclasses
import os

import pytest

from beerus.certificate import CertificateError
from beerus.errors import TokenError
from beerus.filewipe import (
    FAILED,
    OTHER_COPIES_DISCLOSURE,
    SKIPPED,
    SSD_FILE_DISCLOSURE,
    WIPED_VERIFIED,
    FileGateAuthority,
    FileScopeError,
    FileWipeEngine,
    FreeSpaceEngine,
    build_file_report,
    build_file_scope,
    build_file_scope_text,
    build_free_space_report,
    build_free_space_scope,
    build_free_space_scope_text,
    verify_file_result_authentic,
    verify_free_space_result_authentic,
    write_file_report,
)

MIB = 1024 * 1024
DATA = bytes(range(256)) * 64  # 16 KiB of unmistakable content


def write(path, data=DATA):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def confirm(scope, authority=None):
    authority = authority or FileGateAuthority()
    session = authority.begin_session(scope)
    session.acknowledge_scope()
    return authority, session.final_confirmation(scope.expected_phrase)


def run_wipe(scope, **engine_kwargs):
    authority, token = confirm(scope)
    engine = FileWipeEngine(authority, **engine_kwargs)
    return engine.execute(scope, token)


# ---------------------------------------------------------------- scope


def test_scope_counts_nested_tree_exactly(tmp_path):
    write(tmp_path / "a.txt")
    write(tmp_path / "sub" / "b.txt", DATA * 2)
    write(tmp_path / "sub" / "deep" / "c.txt", b"x")
    scope = build_file_scope([tmp_path])
    assert scope.total_files == 3
    assert scope.total_bytes == len(DATA) + len(DATA) * 2 + 1
    assert scope.expected_phrase == "ERASE 3 FILES"


def test_scope_deduplicates_overlapping_selections(tmp_path):
    f = write(tmp_path / "a.txt")
    write(tmp_path / "b.txt")
    scope = build_file_scope([tmp_path, f, tmp_path / "b.txt"])
    assert scope.total_files == 2
    paths = [e.path for e in scope.entries]
    assert len(paths) == len(set(paths))


def test_scope_missing_path_is_a_plain_refusal(tmp_path):
    with pytest.raises(FileScopeError) as caught:
        build_file_scope([tmp_path / "not-there.txt"])
    assert "could not be found" in str(caught.value)


def test_scope_protected_location_is_refused_plainly(tmp_path):
    protected = tmp_path / "protected"
    write(protected / "os-file.dll")
    with pytest.raises(FileScopeError) as caught:
        build_file_scope(
            [protected / "os-file.dll"],
            protected_roots=(str(protected),),
        )
    assert "protected Windows folder" in str(caught.value)


def test_scope_with_no_files_is_refused(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileScopeError):
        build_file_scope([empty])


@pytest.mark.skipif(os.name == "nt", reason="symlink semantics differ")
def test_symlink_escape_never_expands_scope(tmp_path):
    outside = write(tmp_path.parent / "outside-secret.txt")
    folder = tmp_path / "folder"
    write(folder / "real.txt")
    os.symlink(outside, folder / "escape-link")
    os.symlink(tmp_path.parent, folder / "dir-link")
    scope = build_file_scope([folder])
    assert scope.total_files == 1
    kinds = {e.path: e.kind for e in scope.entries}
    assert kinds[str(folder / "escape-link")] == "link"
    assert kinds[str(folder / "dir-link")] == "link"
    result = run_wipe(scope)
    # The outside file was never touched through either link.
    assert outside.read_bytes() == DATA
    outcomes = {o.path: o for o in result.outcomes}
    assert outcomes[str(folder / "real.txt")].status == WIPED_VERIFIED
    assert outcomes[str(folder / "escape-link")].status == SKIPPED
    assert outcomes[str(folder / "dir-link")].status == SKIPPED
    assert not os.path.lexists(folder / "escape-link")


def test_scope_text_lists_everything_and_discloses(tmp_path):
    write(tmp_path / "a.txt")
    text = build_file_scope_text(build_file_scope([tmp_path]), ssd=True)
    assert "a.txt" in text
    assert "ERASE" not in text  # the phrase lives on its own screen
    assert SSD_FILE_DISCLOSURE in text
    assert OTHER_COPIES_DISCLOSURE in text
    plain = build_file_scope_text(build_file_scope([tmp_path]), ssd=False)
    assert SSD_FILE_DISCLOSURE not in plain
    assert OTHER_COPIES_DISCLOSURE in plain


# ---------------------------------------------------------------- gate


def test_gate_wrong_phrase_invalidates_and_mints_nothing(tmp_path):
    write(tmp_path / "a.txt")
    scope = build_file_scope([tmp_path])
    authority = FileGateAuthority()
    session = authority.begin_session(scope)
    with pytest.raises(TokenError):
        session.final_confirmation(scope.expected_phrase)  # skipped ack
    session2 = authority.begin_session(scope)
    session2.acknowledge_scope()
    with pytest.raises(TokenError):
        session2.final_confirmation("ERASE EVERYTHING")
    with pytest.raises(TokenError):
        session2.acknowledge_scope()  # invalidated sessions stay dead


def test_token_is_bound_to_the_exact_scope(tmp_path):
    write(tmp_path / "a.txt")
    write(tmp_path / "b.txt")
    scope_a = build_file_scope([tmp_path / "a.txt"])
    scope_b = build_file_scope([tmp_path / "a.txt", tmp_path / "b.txt"])
    authority, token = confirm(scope_a)
    engine = FileWipeEngine(authority)
    with pytest.raises(TokenError):
        engine.execute(scope_b, token)
    assert (tmp_path / "a.txt").read_bytes() == DATA


def test_token_is_single_use(tmp_path):
    write(tmp_path / "a.txt")
    scope = build_file_scope([tmp_path])
    authority, token = confirm(scope)
    engine = FileWipeEngine(authority)
    engine.execute(scope, token)
    with pytest.raises(TokenError):
        engine.execute(scope, token)


# ---------------------------------------------------------------- engine


def test_files_are_wiped_verified_then_removed(tmp_path):
    write(tmp_path / "a.txt")
    write(tmp_path / "sub" / "b.txt", DATA * 4)
    write(tmp_path / "unicodé-名前.txt")
    scope = build_file_scope([tmp_path])
    result = run_wipe(scope)
    assert verify_file_result_authentic(result)
    assert result.wiped_verified == 3
    assert result.failed == 0
    from beerus.methods import OVERWRITE_PASSES

    assert result.total_bytes_written == (
        (len(DATA) + len(DATA) * 4 + len(DATA)) * len(OVERWRITE_PASSES)
    )
    assert not (tmp_path / "a.txt").exists()
    assert not (tmp_path / "unicodé-名前.txt").exists()
    # Emptied folders went too.
    assert result.directories_removed >= 2
    assert not tmp_path.exists()


def test_large_file_wipes_fully(tmp_path):
    big = write(tmp_path / "big.bin", os.urandom(5 * MIB))
    scope = build_file_scope([big])
    result = run_wipe(scope)
    assert result.wiped_verified == 1
    assert not big.exists()


def test_zero_byte_file_is_removed_honestly(tmp_path):
    empty = write(tmp_path / "empty.txt", b"")
    scope = build_file_scope([empty])
    result = run_wipe(scope)
    outcome = result.outcomes[0]
    assert outcome.status == WIPED_VERIFIED
    assert "already empty" in outcome.detail
    assert not empty.exists()


def test_verification_failure_keeps_the_file(tmp_path):
    # Verification finding surviving data must stop the entry
    # removal: data first, entry last — and a failed read-back
    # means the entry stays.
    target = write(tmp_path / "stubborn.txt")
    scope = build_file_scope([target])
    authority, token = confirm(scope)
    engine = FileWipeEngine(authority)
    engine._verify = lambda path, size: 5  # mismatch at byte 5
    result = engine.execute(scope, token)
    outcome = result.outcomes[0]
    assert outcome.status == FAILED
    assert "byte 5" in outcome.detail
    assert target.exists()
    # The overwrite DID run before verification (zeros, not DATA).
    assert target.read_bytes() == b"\x00" * len(DATA)


def test_locked_file_is_reported_and_left_byte_identical(tmp_path):
    locked = write(tmp_path / "locked.txt")
    free = write(tmp_path / "free.txt")

    def seam(path, flags, original_open):
        if os.path.basename(path) == "locked.txt":
            raise PermissionError("simulated Windows lock")
        return original_open(path, flags)

    scope = build_file_scope([tmp_path])
    authority, token = confirm(scope)
    engine = FileWipeEngine(authority, open_seam=seam)
    result = engine.execute(scope, token)
    outcomes = {o.path: o for o in result.outcomes}
    assert outcomes[str(locked)].status == FAILED
    assert "locked" in outcomes[str(locked)].detail
    assert locked.read_bytes() == DATA  # untouched, never forced
    assert outcomes[str(free)].status == WIPED_VERIFIED
    assert not free.exists()


def test_file_changed_after_confirmation_is_left_alone(tmp_path):
    target = write(tmp_path / "changing.txt")
    scope = build_file_scope([target])
    with open(target, "ab") as f:
        f.write(b"new data after confirmation")
    result = run_wipe(scope)
    outcome = result.outcomes[0]
    assert outcome.status == FAILED
    assert "changed after the scope was confirmed" in outcome.detail
    assert target.read_bytes() == DATA + b"new data after confirmation"


def test_folder_with_a_failed_file_is_left_standing(tmp_path):
    write(tmp_path / "folder" / "locked.txt")

    def seam(path, flags, original_open):
        raise PermissionError("simulated Windows lock")

    scope = build_file_scope([tmp_path / "folder"])
    authority, token = confirm(scope)
    result = FileWipeEngine(authority, open_seam=seam).execute(scope, token)
    assert result.failed == 1
    assert result.directories_left >= 1
    assert (tmp_path / "folder").is_dir()


# ---------------------------------------------------------------- free space


def _free_space_run(tmp_path, **kwargs):
    live = write(tmp_path / "keep-me.txt", b"important live data" * 100)
    probe = kwargs.pop("probe", lambda p: 24 * MIB)
    scope = build_free_space_scope(
        tmp_path, free_bytes_probe=probe, reserve_bytes=MIB
    )
    authority, token = confirm(scope)
    engine = FreeSpaceEngine(authority, free_bytes_probe=probe,
                             chunk_bytes=MIB)
    result = engine.execute(scope, token)
    return live, scope, result


def test_free_space_fills_verifies_and_leaves_live_files(tmp_path):
    live, scope, result = _free_space_run(tmp_path)
    assert verify_free_space_result_authentic(result)
    assert result.ok is True
    from beerus.methods import OVERWRITE_PASSES

    assert result.bytes_filled == 23 * MIB
    assert result.passes_completed == len(OVERWRITE_PASSES)
    assert result.bytes_checked > 0
    assert live.read_bytes() == b"important live data" * 100
    # The filler folder is gone afterwards.
    assert not (tmp_path / "Beerus free-space wipe").exists()


def test_free_space_scope_text_announces_margin_and_discloses(tmp_path):
    scope = build_free_space_scope(
        tmp_path, free_bytes_probe=lambda p: 24 * MIB, reserve_bytes=MIB
    )
    text = build_free_space_scope_text(scope, ssd=True)
    assert "safety margin" in text
    assert "TRIM" in text
    assert OTHER_COPIES_DISCLOSURE in text
    assert scope.expected_phrase == "WIPE FREE SPACE"
    assert scope.expected_fill_bytes == 23 * MIB


def test_free_space_needs_a_token(tmp_path):
    scope = build_free_space_scope(
        tmp_path, free_bytes_probe=lambda p: 24 * MIB, reserve_bytes=MIB
    )
    engine = FreeSpaceEngine(
        FileGateAuthority(), free_bytes_probe=lambda p: 24 * MIB
    )
    with pytest.raises(TokenError):
        engine.execute(scope, None)


# ---------------------------------------------------------------- reports


def test_report_is_per_item_signed_and_disclosed(tmp_path):
    write(tmp_path / "a.txt")
    write(tmp_path / "b.txt")
    result = run_wipe(build_file_scope([tmp_path]))
    report = build_file_report(result, "Skyler Test", ssd=True)
    assert report.kind == "file-wipe-report"
    joined = "\n".join(report.item_lines)
    assert "WIPED + VERIFIED" in joined
    assert "a.txt" in joined and "b.txt" in joined
    assert SSD_FILE_DISCLOSURE in report.disclosure_lines
    assert OTHER_COPIES_DISCLOSURE in report.disclosure_lines
    path = write_file_report(report, tmp_path / "reports")
    html_text = path.read_text(encoding="utf-8")
    assert "File Wipe Report" in html_text
    assert "not a certificate of destruction" in html_text
    # A second write never overwrites the first.
    path2 = write_file_report(report, tmp_path / "reports")
    assert path2 != path and path.exists() and path2.exists()


def test_report_refuses_a_tampered_result(tmp_path):
    write(tmp_path / "a.txt")
    result = run_wipe(build_file_scope([tmp_path]))
    fake = dataclasses.replace(
        result,
        outcomes=(
            dataclasses.replace(result.outcomes[0], status=FAILED),
        ),
    )
    assert not verify_file_result_authentic(fake)
    with pytest.raises(CertificateError):
        build_file_report(fake, "Skyler Test", ssd=False)


def test_free_space_report(tmp_path):
    _live, _scope, result = _free_space_run(tmp_path)
    report = build_free_space_report(result, "Skyler Test", ssd=True)
    assert report.kind == "free-space-report"
    joined = "\n".join(report.summary_lines)
    assert "COMPLETED" in joined
    assert "Free space filled" in joined
