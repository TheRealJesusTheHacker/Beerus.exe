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
    # stamp in the scope identity catches it.
    target = write(tmp_path / "changing.txt")
    scope = build_file_scope([target])
    target.write_bytes(b"\x41" * len(DATA))
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
    # "authentic" ones.
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
