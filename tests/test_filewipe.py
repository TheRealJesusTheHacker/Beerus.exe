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
    LINK_LEFT,
    LINK_REMOVED,
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
    from beerus.filewipe import LINK_REMOVED

    assert outcomes[str(folder / "escape-link")].status == LINK_REMOVED
    assert outcomes[str(folder / "dir-link")].status == LINK_REMOVED
    assert result.links_removed == 2
    assert result.skipped == 0
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


def test_file_token_expires_at_session_deadline_not_mint(tmp_path):
    # Re-audit M4: the token must die at the SESSION deadline
    # (like the drive gates'), not a fresh TTL from mint time.
    import time

    target = write(tmp_path / "victim.txt", b"12345678")
    authority = FileGateAuthority(token_ttl_seconds=1.0)
    scope = build_file_scope([target])
    started = time.time()
    session = authority.begin_session(scope)
    time.sleep(0.5)
    session.acknowledge_scope()
    token = session.final_confirmation(scope.expected_phrase)
    assert token.expires_at <= started + 1.05
    time.sleep(0.6)  # past the deadline, inside the old window
    engine = FileWipeEngine(authority)
    with pytest.raises(TokenError):
        engine.execute(scope, token)
    assert target.read_bytes() == b"12345678"


def test_link_swapped_for_regular_file_is_never_unlinked(tmp_path):
    # Re-audit N1: a link entry replaced by a regular file
    # after confirmation must be left alone — its data was
    # never in the confirmed scope as a file.
    target = write(tmp_path / "outside" / "target.txt", b"target data")
    hold = tmp_path / "hold"
    write(hold / "real.txt", b"real data")
    link = hold / "link.txt"
    os.symlink(target, link)
    scope = build_file_scope([hold])
    authority, token = confirm(scope)
    os.unlink(link)
    secret = write(hold / "link.txt", b"never confirmed secret")
    result = FileWipeEngine(authority).execute(scope, token)
    by_path = {o.path: o for o in result.outcomes}
    assert by_path[str(secret)].status == FAILED
    assert secret.read_bytes() == b"never confirmed secret"
    assert by_path[str(hold / "real.txt")].status == WIPED_VERIFIED


def test_link_replaced_by_another_link_is_left_alone(tmp_path):
    # Re-audit N1: a swapped-in link is a different entry
    # than the one confirmed — left in place, targets intact.
    first = write(tmp_path / "outside" / "first.txt", b"first")
    second = write(tmp_path / "outside" / "second.txt", b"second")
    hold = tmp_path / "hold"
    write(hold / "real.txt", b"real data")
    link = hold / "link.txt"
    os.symlink(first, link)
    scope = build_file_scope([hold])
    authority, token = confirm(scope)
    os.unlink(link)
    os.symlink(second, link)
    result = FileWipeEngine(authority).execute(scope, token)
    by_path = {o.path: o for o in result.outcomes}
    assert by_path[str(link)].status == LINK_LEFT
    assert os.path.islink(link)
    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"


def test_result_authenticity_guards_wrong_types():
    # Re-audit N2: the sibling verify functions get the same
    # isinstance discipline as beerus.verify.
    assert verify_file_result_authentic(None) is False
    assert verify_file_result_authentic(object()) is False
    assert verify_free_space_result_authentic("x") is False
    assert verify_free_space_result_authentic(None) is False
    with pytest.raises(CertificateError):
        build_file_report(object(), "Tech", ssd=False)
    with pytest.raises(CertificateError):
        build_free_space_report(123, "Tech", ssd=False)


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


# ------------------------------------------------------- audit regressions
# One test per finding from the independent audit of v0.4.0
# (H1, H2, M1-M6, L1, L2). These stay in the suite permanently.


@pytest.mark.skipif(os.name == "nt", reason="symlink semantics differ")
def test_protected_check_resolves_symlinked_parent(tmp_path):
    # H1: a protected file reached through a symlinked parent
    # folder must be refused by its RESOLVED location.
    protected = tmp_path / "protected-real"
    secret = write(protected / "os.dll")
    alias = tmp_path / "innocent"
    os.symlink(protected, alias)
    with pytest.raises(FileScopeError) as caught:
        build_file_scope(
            [alias / "os.dll"], protected_roots=(str(protected),)
        )
    assert "protected Windows folder" in str(caught.value)
    assert secret.read_bytes() == DATA


@pytest.mark.skipif(os.name == "nt", reason="hardlink semantics differ")
def test_hardlinked_file_is_refused_at_scope_build(tmp_path):
    # H2: the same data under two names — wiping one name would
    # destroy the other, which is not in the announced scope.
    first = write(tmp_path / "first.txt")
    os.link(first, tmp_path / "second.txt")
    with pytest.raises(FileScopeError) as caught:
        build_file_scope([first])
    assert "more than one" in str(caught.value)
    assert first.read_bytes() == DATA


@pytest.mark.skipif(os.name == "nt", reason="hardlink semantics differ")
def test_file_gaining_a_link_after_confirmation_is_left_alone(tmp_path):
    target = write(tmp_path / "solo.txt")
    scope = build_file_scope([target])
    os.link(target, tmp_path / "twin.txt")
    result = run_wipe(scope)
    assert result.outcomes[0].status == FAILED
    assert "another name" in result.outcomes[0].detail
    assert target.read_bytes() == DATA


@pytest.mark.skipif(os.name == "nt", reason="symlink semantics differ")
def test_aliased_paths_count_once(tmp_path):
    # M3: one physical file reached by two spellings is one file.
    real = write(tmp_path / "real" / "f.txt")
    os.symlink(tmp_path / "real", tmp_path / "alias")
    scope = build_file_scope([real, tmp_path / "alias" / "f.txt"])
    assert scope.total_files == 1
    assert scope.expected_phrase == "ERASE 1 FILE"


def test_swap_detected_by_open_seam_fstat(tmp_path):
    # M1: the file is replaced between the engine's stat and its
    # open; the descriptor re-verification must catch it and the
    # replacement must survive untouched.
    target = write(tmp_path / "victim.txt")
    replacement = b"REPLACEMENT" * 100

    def seam(path, flags, original_open):
        os.unlink(path)
        with open(path, "wb") as f:
            f.write(replacement)
        return original_open(path, flags)

    scope = build_file_scope([target])
    authority, token = confirm(scope)
    result = FileWipeEngine(authority, open_seam=seam).execute(
        scope, token
    )
    assert result.outcomes[0].status == FAILED
    assert target.read_bytes() == replacement


def test_same_size_in_place_change_is_detected(tmp_path):
    # M2: same inode, same size, new content — the modification
    # stamp in the scope identity catches it. The stamp is moved
    # explicitly: on Windows the filesystem's write-time tick
    # is coarse and lazily updated, so a bare rewrite can land
    # in the SAME tick the scope recorded — the documented
    # bound the identity check cannot see through (module
    # docstring) — and the test then asserts timing luck
    # instead of the check (kit-hunt: intermittent failure on
    # windows-latest, run 39440012742). A moved stamp is the
    # change this test means, on every platform.
    target = write(tmp_path / "changing.txt")
    scope = build_file_scope([target])
    target.write_bytes(b"\x41" * len(DATA))
    st = os.lstat(target)
    os.utime(
        target,
        ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000),
    )
    result = run_wipe(scope)
    assert result.outcomes[0].status == FAILED
    assert target.read_bytes() == b"\x41" * len(DATA)


def test_file_gate_session_expires():
    # M4: the session itself has a deadline, not just the token.
    import time as time_module

    authority = FileGateAuthority(token_ttl_seconds=0.2)
    session = authority.begin_session(_tiny_scope())
    time_module.sleep(0.3)
    with pytest.raises(TokenError):
        session.acknowledge_scope()


def _tiny_scope():
    import tempfile
    from pathlib import Path

    d = Path(tempfile.mkdtemp())
    (d / "one.txt").write_bytes(b"x" * 10)
    return build_file_scope([d / "one.txt"])


def test_session_scope_text_carries_the_ssd_flag():
    # L3: the session's own scope statement must not silently
    # drop the SSD disclosure.
    authority = FileGateAuthority()
    session = authority.begin_session(_tiny_scope(), ssd=True)
    text = session.acknowledge_scope()
    assert SSD_FILE_DISCLOSURE in text


def test_cross_type_tokens_refuse_cleanly(tmp_path):
    # L2: wrong-kind scopes and tokens are TokenErrors, never
    # AttributeErrors — and nothing is wiped either way.
    target = write(tmp_path / "a.txt")
    file_scope = build_file_scope([target])
    free_scope = build_free_space_scope(
        tmp_path, free_bytes_probe=lambda p: 24 * MIB, reserve_bytes=MIB
    )
    file_authority, file_token = confirm(file_scope)
    free_authority, free_token = confirm(free_scope)
    with pytest.raises(TokenError):
        FileWipeEngine(free_authority).execute(free_scope, free_token)
    with pytest.raises(TokenError):
        FreeSpaceEngine(file_authority).execute(file_scope, file_token)
    with pytest.raises(TokenError):
        FileWipeEngine(file_authority).execute(file_scope, object())
    assert target.read_bytes() == DATA


def test_repartitioned_result_fails_verification(tmp_path):
    # L1: a filename crafted with the old encoding's delimiters
    # must not let one genuine outcome be repartitioned into two
    # "authentic" ones. NTFS forbids '|' and control characters
    # in names outright, so on Windows the crafted name is the
    # closest legal equivalent — the repartition attempt below
    # is in-memory and identical on every platform.
    if os.name == "nt":
        crafted = tmp_path / "x wiped_verified SHINY 999 EVIL"
    else:
        crafted = tmp_path / "x|wiped_verified|SHINY|999\nEVIL"
    crafted.write_bytes(DATA)

    def seam(path, flags, original_open):
        raise PermissionError("simulated Windows lock")

    scope = build_file_scope([crafted])
    authority, token = confirm(scope)
    result = FileWipeEngine(authority, open_seam=seam).execute(
        scope, token
    )
    assert result.outcomes[0].status == FAILED
    assert verify_file_result_authentic(result)
    fake = dataclasses.replace(
        result,
        outcomes=(
            dataclasses.replace(
                result.outcomes[0],
                path="x",
                status=WIPED_VERIFIED,
                detail="SHINY",
                bytes_written=999,
            ),
            dataclasses.replace(
                result.outcomes[0], path="EVIL"
            ),
        ),
    )
    assert not verify_file_result_authentic(fake)
    with pytest.raises(CertificateError):
        build_file_report(fake, "Skyler Test", ssd=False)


def test_free_space_shortfall_is_not_reported_complete(tmp_path):
    # M5: a fill that reaches only part of the confirmed target
    # is NOT a complete wipe.
    write(tmp_path / "keep.txt", b"live" * 10)
    scope = build_free_space_scope(
        tmp_path, free_bytes_probe=lambda p: 24 * MIB, reserve_bytes=MIB
    )
    authority, token = confirm(scope)
    engine = FreeSpaceEngine(
        authority, free_bytes_probe=lambda p: 24 * MIB, chunk_bytes=MIB
    )
    real_fill = engine._fill_pass

    def partial_fill(scope_, dir_, fillers, spec, emit, *, create):
        if create:
            return 512  # the volume "shrank": almost nothing filled
        return real_fill(scope_, dir_, fillers, spec, emit, create=False)

    engine._fill_pass = partial_fill
    result = engine.execute(scope, token)
    assert result.ok is False
    assert "could be filled" in result.detail


def test_free_space_refuses_a_foreign_filler_folder(tmp_path):
    # M6: a pre-existing filler folder holding a user's file is
    # refused — its contents are never adopted or deleted.
    from beerus.errors import WipeFailedError

    filler_dir = tmp_path / "Beerus free-space wipe"
    filler_dir.mkdir()
    user_file = write(filler_dir / "filler-0001.bin", b"USER DATA")
    scope = build_free_space_scope(
        tmp_path, free_bytes_probe=lambda p: 24 * MIB, reserve_bytes=MIB
    )
    authority, token = confirm(scope)
    engine = FreeSpaceEngine(
        authority, free_bytes_probe=lambda p: 24 * MIB, chunk_bytes=MIB
    )
    with pytest.raises(WipeFailedError):
        engine.execute(scope, token)
    assert user_file.read_bytes() == b"USER DATA"


@pytest.mark.skipif(os.name == "nt", reason="symlink semantics differ")
def test_free_space_refuses_a_symlinked_filler_folder(tmp_path):
    # M6: a filler folder that is a link would relocate every
    # write to another volume. Refused before anything is written.
    from beerus.errors import WipeFailedError

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    volume = tmp_path / "volume"
    volume.mkdir()
    os.symlink(elsewhere, volume / "Beerus free-space wipe",
               target_is_directory=True)
    scope = build_free_space_scope(
        volume, free_bytes_probe=lambda p: 24 * MIB, reserve_bytes=MIB
    )
    authority, token = confirm(scope)
    engine = FreeSpaceEngine(
        authority, free_bytes_probe=lambda p: 24 * MIB, chunk_bytes=MIB
    )
    with pytest.raises(WipeFailedError):
        engine.execute(scope, token)
    assert list(elsewhere.iterdir()) == []


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


def test_free_space_leftovers_are_counted_and_reported(
    tmp_path, monkeypatch
):
    # Re-audit N4: filler that cannot be removed is counted
    # in the signed result and described honestly — never
    # waved through as if nothing were left.
    real_unlink = os.unlink

    def stubborn(path):
        if "filler-" in os.path.basename(str(path)):
            raise OSError("locked")
        return real_unlink(path)

    monkeypatch.setattr(os, "unlink", stubborn)
    _live, _scope, result = _free_space_run(tmp_path)
    assert result.ok is True
    assert result.leftovers >= 1
    assert verify_free_space_result_authentic(result)
    assert "check the folder before deleting it" in result.detail
    report = build_free_space_report(result, "Skyler Test", ssd=False)
    assert "left behind" in "\n".join(report.summary_lines)


def test_free_space_report(tmp_path):
    _live, _scope, result = _free_space_run(tmp_path)
    report = build_free_space_report(result, "Skyler Test", ssd=True)
    assert report.kind == "free-space-report"
    joined = "\n".join(report.summary_lines)
    assert "COMPLETED" in joined
    assert "Free space filled" in joined


# ------------------------------------------------------- kit-hunt regressions
# Findings from the kit-wide Windows bug hunt (v0.5.1).


def test_scope_counts_distinct_files_that_have_no_inode(tmp_path):
    # Pre-fix: dedupe keyed files on (dev, inode) alone, so on a
    # filesystem that reports inode 0 for every file (FAT/exFAT
    # volumes have no file index to give), a folder of distinct
    # files collapsed into ONE scope entry — the announced
    # count and byte total were a fraction of what the operator
    # picked, and the rest would silently not be wiped.
    import os as _os

    for name in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / name).write_bytes(b"x" * 100)
    real_lstat = _os.lstat

    def inoless_lstat(path, *args, **kwargs):
        st = real_lstat(path, *args, **kwargs)
        return _os.stat_result(
            (
                st.st_mode,
                0,
                st.st_dev,
                st.st_nlink,
                st.st_uid,
                st.st_gid,
                st.st_size,
                int(st.st_atime),
                int(st.st_mtime),
                int(st.st_ctime),
            )
        )

    import unittest.mock as _mock

    with _mock.patch.object(_os, "lstat", inoless_lstat):
        scope = build_file_scope([str(tmp_path)], protected_roots=())
    assert scope.total_files == 3
    assert scope.total_bytes == 300


def test_free_space_scope_refuses_when_margin_leaves_nothing(tmp_path):
    # Pre-fix: a volume with less free space than the safety
    # margin produced a confirmable scope whose fill was zero
    # bytes — and the engine then reported a fully verified
    # COMPLETE over those zero bytes.
    with pytest.raises(FileScopeError) as excinfo:
        build_free_space_scope(
            tmp_path, free_bytes_probe=lambda p: 8 * MIB
        )
    assert "Nothing was wiped" in str(excinfo.value)


def test_scope_records_a_junction_as_a_link_and_never_descends(
    tmp_path, monkeypatch
):
    # Kit-hunt regression (probe-proven on windows-latest):
    # os.path.islink reports a Windows junction as NOT a link,
    # so the scope walk descended into it and scoped a file
    # that lives OUTSIDE the selected folder. Junctions are
    # simulated here by patching os.path.isjunction, which is
    # the API the fix consults; the real-junction behavior on
    # Windows itself is pinned by the nt-only test below.
    import os as _os

    root = tmp_path / "root"
    junction = root / "linkdir"
    junction.mkdir(parents=True)
    (root / "inner.txt").write_bytes(b"i" * 50)
    (junction / "secret.txt").write_bytes(b"s" * 50)
    real_isjunction = getattr(_os.path, "isjunction", lambda p: False)
    monkeypatch.setattr(
        _os.path,
        "isjunction",
        lambda p: str(p) == str(junction) or real_isjunction(p),
    )
    scope = build_file_scope([str(root)], protected_roots=())
    assert scope.total_files == 1
    kinds = {entry.path: entry.kind for entry in scope.entries}
    assert kinds[str(junction)] == "link"
    assert not any("secret.txt" in path for path in kinds)


def test_junction_target_data_is_never_wiped(tmp_path, monkeypatch):
    # The execution half of the junction finding: even through
    # the full gated engine, the file behind a junction keeps
    # its data and its entry; only the confirmed real file is
    # wiped, and the junction itself is treated as a link.
    import os as _os

    root = tmp_path / "root"
    junction = root / "linkdir"
    junction.mkdir(parents=True)
    inner = root / "inner.txt"
    inner.write_bytes(b"i" * 50)
    secret = junction / "secret.txt"
    secret.write_bytes(b"s" * 50)
    real_isjunction = getattr(_os.path, "isjunction", lambda p: False)
    monkeypatch.setattr(
        _os.path,
        "isjunction",
        lambda p: str(p) == str(junction) or real_isjunction(p),
    )
    scope = build_file_scope([str(root)], protected_roots=())
    result = run_wipe(scope, protected_roots=())
    assert secret.read_bytes() == b"s" * 50
    assert not inner.exists()
    link_outcomes = [o for o in result.outcomes if o.kind == "link"]
    assert len(link_outcomes) == 1
    assert link_outcomes[0].status in (LINK_REMOVED, LINK_LEFT)


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows reparse point")
def test_real_windows_junction_is_a_link_in_scope_and_execution(tmp_path):
    # The gold pin, run on windows-latest: a real mklink /J
    # junction inside a selected folder is recorded as a link,
    # its target's file is never scoped or wiped, and the
    # junction itself is removed as a link at execution.
    import subprocess as _sp

    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "inner.txt").write_bytes(b"i" * 50)
    secret = outside / "secret.txt"
    secret.write_bytes(b"s" * 50)
    junction = root / "linkdir"
    created = _sp.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        text=True,
    )
    assert created.returncode == 0
    scope = build_file_scope([str(root)], protected_roots=())
    assert scope.total_files == 1
    result = run_wipe(scope, protected_roots=())
    assert secret.read_bytes() == b"s" * 50
    outcomes = {o.path: o for o in result.outcomes}
    assert outcomes[str(junction)].status == LINK_REMOVED


# ------------------------------------------------------- sweep-5 regressions
# Findings from Fleet Sweep #5 (2026-10-24).


def test_free_space_zero_progress_write_never_spins(tmp_path, monkeypatch):
    # Sweep #5: both write loops in FreeSpaceEngine._fill_pass
    # advanced `view = view[n:]` with no guard on n — an os.write
    # that returns 0 (a full/odd volume can do exactly that)
    # looped forever on the same buffer and the wipe hung with
    # the volume full of filler. The drive executor's
    # run_overwrite already guarded this case; the filler loops
    # now raise OSError on a no-progress write, which the create
    # pass treats as the end of that filler's growth and the run
    # reports as a shortfall — never a hang, never a success.
    real_write = os.write

    def zero_write(fd, data):
        if fd in (1, 2):
            return real_write(fd, data)
        return 0

    monkeypatch.setattr(os, "write", zero_write)
    scope = build_free_space_scope(
        tmp_path, free_bytes_probe=lambda p: 24 * MIB, reserve_bytes=MIB
    )
    authority, token = confirm(scope)
    engine = FreeSpaceEngine(
        authority, free_bytes_probe=lambda p: 24 * MIB, chunk_bytes=MIB
    )
    result = engine.execute(scope, token)
    assert result.ok is False
    assert result.bytes_filled == 0
    assert verify_free_space_result_authentic(result)


def test_write_file_report_never_overwrites_an_existing_file(
    tmp_path, monkeypatch
):
    # Sweep #5: write_file_report claimed "reports are never
    # overwritten" but claimed its name with an exists-check
    # followed by a plain write_text — a file landing on the
    # name between the check and the write was silently
    # replaced. The name is now claimed with an exclusive
    # create (the certificate writer's discipline). Simulate
    # the race deterministically: exists() always answers "no"
    # (the stale check the old code trusted) while the base
    # name is already taken by a sentinel file.
    result = run_wipe(build_file_scope([write(tmp_path / "a.txt")]))
    report = build_file_report(result, "Skyler Test", ssd=False)
    out_dir = tmp_path / "reports"
    out_dir.mkdir()
    from datetime import datetime as _dt

    day = _dt.now().strftime("%Y%m%d")
    sentinel = (
        out_dir / f"beerus-{report.kind}-{day}-{report.record_id}.html"
    )
    sentinel.write_text("SENTINEL — an earlier record", encoding="utf-8")
    monkeypatch.setattr(
        "pathlib.Path.exists", lambda self: False
    )
    path = write_file_report(report, out_dir)
    assert path != sentinel
    assert sentinel.read_text(encoding="utf-8") == (
        "SENTINEL — an earlier record"
    )
    assert "File Wipe Report" in path.read_text(encoding="utf-8")
