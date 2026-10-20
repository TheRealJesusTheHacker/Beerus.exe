"""Consent-gate tests — the gates are the product.

All drives are synthetic data objects from conftest. No device is
ever opened here.
"""

from __future__ import annotations

import dataclasses

import pytest

from beerus.errors import (
    BootDeviceError,
    ConfirmationError,
    GateError,
    IdentityMismatchError,
    ScopeNotAcknowledgedError,
    TokenError,
    UnidentifiableDriveError,
)
from beerus.gates import (
    GateAuthority,
    GateSession,
    GateStage,
    build_scope_text,
    scope_hash_for,
)
from beerus.methods import PassSpec, select_plan
from conftest import make_boot_drive, make_drive, make_nvme, make_unidentifiable


class FakeClock:
    def __init__(self, start: float = 1_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _full_token(authority, drive):
    plan = select_plan(drive)
    session = authority.begin_session(drive, plan)
    session.confirm_identity(drive.serial)
    session.acknowledge_scope()
    return session.final_confirmation(f"ERASE {drive.serial}")


# ------------------------------------------------------------------ happy path


def test_happy_path_full_gate_sequence_mints_usable_token():
    drive = make_drive()
    plan = select_plan(drive)
    authority = GateAuthority()

    session = authority.begin_session(drive, plan)
    assert session.stage is GateStage.STARTED
    assert session.drive is drive
    assert session.plan is plan
    assert session.scope.drive_fingerprint == drive.fingerprint
    assert session.scope.scope_hash == scope_hash_for(drive, plan)

    session.confirm_identity(drive.serial)
    assert session.stage is GateStage.IDENTITY_CONFIRMED

    returned_scope = session.acknowledge_scope()
    assert returned_scope is session.scope
    assert session.stage is GateStage.SCOPE_ACKNOWLEDGED

    token = session.final_confirmation(f"ERASE {drive.serial}")
    assert session.stage is GateStage.COMPLETED
    assert token.fingerprint == drive.fingerprint
    assert token.scope_hash == scope_hash_for(drive, plan)

    # Verifying a fresh, correct token passes without raising.
    authority.verify_and_consume(token, drive, plan)


def test_scope_text_names_drive_method_and_permanence():
    drive = make_drive()
    plan = select_plan(drive)
    text = build_scope_text(drive, plan)
    assert drive.model in text
    assert drive.serial in text
    assert plan.method_label in text
    assert "permanent" in text.lower()
    assert "cannot be undone" in text.lower()


def test_scope_hash_changes_with_plan():
    drive = make_drive()
    plan_a = select_plan(drive)
    nvme = make_nvme()
    plan_b = select_plan(nvme)
    assert scope_hash_for(drive, plan_a) != scope_hash_for(nvme, plan_b)


# ------------------------------------------------------------------ identity


def test_wrong_serial_raises_and_invalidates_session():
    drive = make_drive()
    plan = select_plan(drive)
    authority = GateAuthority()
    session = authority.begin_session(drive, plan)

    with pytest.raises(IdentityMismatchError):
        session.confirm_identity("NOT-THE-SERIAL")

    assert session.stage is GateStage.INVALIDATED
    # A later step on the cancelled session raises GateError.
    with pytest.raises(GateError):
        session.confirm_identity(drive.serial)
    with pytest.raises(GateError):
        session.acknowledge_scope()


def test_acknowledge_before_identity_raises():
    drive = make_drive()
    authority = GateAuthority()
    session = authority.begin_session(drive, select_plan(drive))
    with pytest.raises(ScopeNotAcknowledgedError):
        session.acknowledge_scope()


def test_final_confirmation_before_acknowledge_raises():
    drive = make_drive()
    authority = GateAuthority()
    session = authority.begin_session(drive, select_plan(drive))
    session.confirm_identity(drive.serial)
    with pytest.raises(ScopeNotAcknowledgedError):
        session.final_confirmation(f"ERASE {drive.serial}")


def test_wrong_final_phrase_raises_and_invalidates():
    drive = make_drive()
    authority = GateAuthority()
    session = authority.begin_session(drive, select_plan(drive))
    session.confirm_identity(drive.serial)
    session.acknowledge_scope()

    with pytest.raises(ConfirmationError):
        session.final_confirmation("ERASE everything please")

    assert session.stage is GateStage.INVALIDATED
    with pytest.raises(GateError):
        session.final_confirmation(f"ERASE {drive.serial}")


def test_steps_out_of_order_raise_gate_error():
    drive = make_drive()
    authority = GateAuthority()
    session = authority.begin_session(drive, select_plan(drive))
    session.confirm_identity(drive.serial)
    with pytest.raises(GateError):
        session.confirm_identity(drive.serial)


# ------------------------------------------------------------------ begin refusals


def test_begin_session_on_boot_drive_refused():
    drive = make_boot_drive()
    authority = GateAuthority()
    with pytest.raises(BootDeviceError):
        authority.begin_session(drive, select_plan(make_drive()))


def test_begin_session_on_unidentifiable_drive_refused():
    drive = make_unidentifiable()
    authority = GateAuthority()
    with pytest.raises(UnidentifiableDriveError):
        authority.begin_session(drive, select_plan(make_drive()))


# ------------------------------------------------------------------ tokens


def test_token_consumed_twice_raises():
    drive = make_drive()
    plan = select_plan(drive)
    authority = GateAuthority()
    token = _full_token(authority, drive)

    authority.verify_and_consume(token, drive, plan)
    with pytest.raises(TokenError):
        authority.verify_and_consume(token, drive, plan)


def test_token_for_drive_a_fails_for_drive_b():
    drive_a = make_drive()
    drive_b = make_drive(serial="WD-OTHER-SERIAL", device_path="/dev/sdc")
    authority = GateAuthority()
    token = _full_token(authority, drive_a)

    with pytest.raises(TokenError):
        authority.verify_and_consume(token, drive_b, select_plan(drive_b))


def test_tampered_mac_fails():
    drive = make_drive()
    plan = select_plan(drive)
    authority = GateAuthority()
    token = _full_token(authority, drive)
    forged = dataclasses.replace(token, mac="0" * 64)
    with pytest.raises(TokenError):
        authority.verify_and_consume(forged, drive, plan)


def test_tampered_scope_hash_fails():
    drive = make_drive()
    plan = select_plan(drive)
    authority = GateAuthority()
    token = _full_token(authority, drive)
    forged = dataclasses.replace(token, scope_hash="f" * 64)
    with pytest.raises(TokenError):
        authority.verify_and_consume(forged, drive, plan)


def test_token_from_another_authority_fails():
    drive = make_drive()
    plan = select_plan(drive)
    token = _full_token(GateAuthority(), drive)
    with pytest.raises(TokenError):
        GateAuthority().verify_and_consume(token, drive, plan)


# ------------------------------------------------------------------ expiry


def test_token_expiry_with_fake_clock():
    clock = FakeClock()
    authority = GateAuthority(token_ttl_seconds=60.0, clock=clock)
    drive = make_drive()
    plan = select_plan(drive)
    token = _full_token(authority, drive)

    clock.advance(61.0)
    with pytest.raises(TokenError):
        authority.verify_and_consume(token, drive, plan)


def test_session_deadline_with_fake_clock():
    clock = FakeClock()
    authority = GateAuthority(token_ttl_seconds=60.0, clock=clock)
    drive = make_drive()
    session = authority.begin_session(drive, select_plan(drive))
    session.confirm_identity(drive.serial)

    clock.advance(61.0)
    with pytest.raises(GateError):
        session.acknowledge_scope()


def test_token_expires_at_session_deadline_not_mint_time():
    clock = FakeClock()  # starts at 1000.0
    authority = GateAuthority(token_ttl_seconds=60.0, clock=clock)
    drive = make_drive()
    plan = select_plan(drive)
    session = authority.begin_session(drive, plan)

    clock.advance(59.0)  # complete the sequence at t+59
    session.confirm_identity(drive.serial)
    session.acknowledge_scope()
    token = session.final_confirmation(f"ERASE {drive.serial}")

    # Expiry is the session deadline (begin + 60), not mint + 60.
    assert token.expires_at == 1000.0 + 60.0

    clock.advance(1.0)  # now exactly at the deadline: expired
    with pytest.raises(TokenError):
        authority.verify_and_consume(token, drive, plan)


# ------------------------------------------------------- scope binds plan (F2)


def test_token_rejected_when_passes_differ_from_confirmed_plan():
    drive = make_drive()  # SATA HDD: standard plan has 4 passes
    standard_plan = select_plan(drive)
    authority = GateAuthority()
    token = _full_token(authority, drive)

    one_pass_plan = dataclasses.replace(
        standard_plan, passes=(PassSpec("random", "Write random data"),)
    )
    # Same label, same step kinds — only the passes differ.
    assert one_pass_plan.method_label == standard_plan.method_label
    assert [s.kind for s in one_pass_plan.steps] == [
        s.kind for s in standard_plan.steps
    ]
    with pytest.raises(TokenError):
        authority.verify_and_consume(token, drive, one_pass_plan)


def test_token_rejected_when_nist_category_differs():
    drive = make_drive()
    standard_plan = select_plan(drive)
    authority = GateAuthority()
    token = _full_token(authority, drive)

    other_category = dataclasses.replace(standard_plan, nist_category="Purge")
    with pytest.raises(TokenError):
        authority.verify_and_consume(token, drive, other_category)


# ---------------------------------------------- session construction / verify (F7)


def test_direct_gate_session_construction_raises_gate_error():
    drive = make_drive()
    plan = select_plan(drive)
    authority = GateAuthority()
    session = authority.begin_session(drive, plan)
    with pytest.raises(GateError):
        GateSession(authority, drive, plan, session.scope, 0.0)


@pytest.mark.parametrize("bad_token", [None, "x"])
def test_verify_rejects_non_token_values(bad_token):
    drive = make_drive()
    plan = select_plan(drive)
    authority = GateAuthority()
    with pytest.raises(TokenError):
        authority.verify_and_consume(bad_token, drive, plan)


def test_verify_against_boot_twin_with_same_fingerprint_refused():
    drive = make_drive()
    plan = select_plan(drive)
    authority = GateAuthority()
    token = _full_token(authority, drive)

    boot_twin = make_boot_drive()  # same model/serial/size/interface/wwn
    assert boot_twin.fingerprint == drive.fingerprint
    assert boot_twin.is_boot is True
    with pytest.raises(BootDeviceError):
        authority.verify_and_consume(token, boot_twin, plan)
