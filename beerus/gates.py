"""Consent gates for Beerus.

The gates are the product: a wipe cannot start until the user has
confirmed the exact drive by serial number, acknowledged the full
scope in plain language, and typed the exact final confirmation
phrase. The GateAuthority mints a single-use, short-lived,
HMAC-signed token only after the full sequence completes.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from enum import Enum

from beerus.errors import (
    BootDeviceError,
    ConfirmationError,
    GateError,
    IdentityMismatchError,
    ScopeNotAcknowledgedError,
    TokenError,
    UnidentifiableDriveError,
)

_UNIDENTIFIABLE_MESSAGE = (
    "Beerus could not identify this drive — its model or serial number "
    "could not be read — so it will not wipe it."
)


def _human_size(size_bytes: int) -> str:
    """Render a byte count in a plain, human-readable form."""
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


def build_scope_text(drive, plan) -> str:
    """Full plain-language announcement of exactly what will happen."""
    return (
        f"Drive to be wiped: {drive.model} (serial {drive.serial}), "
        f"size {_human_size(drive.size_bytes)}.\n"
        f"Method: {plan.method_label}.\n"
        "ALL data on this drive will be permanently destroyed. "
        "This cannot be undone, and the data cannot be recovered.\n"
        "No other drive will be touched — only this one drive will be wiped.\n"
        "The boot drive is never a target: the drive Beerus booted from "
        "can never be wiped."
    )


def scope_hash_for(drive, plan) -> str:
    """Hash binding a confirmation to this drive and this exact plan."""
    material = "|".join(
        [
            drive.fingerprint,
            plan.method_label,
            plan.nist_category,
            str(drive.size_bytes),
            ",".join(step.kind.value for step in plan.steps),
            "|".join(f"{p.pattern}:{p.label}" for p in plan.passes),
        ]
    )
    return hashlib.sha256(material.encode()).hexdigest()


@dataclass(frozen=True)
class ScopeStatement:
    text: str
    drive_fingerprint: str
    model: str
    serial: str
    size_bytes: int
    method_label: str
    scope_hash: str


@dataclass(frozen=True)
class ConfirmationToken:
    fingerprint: str
    scope_hash: str
    nonce: str
    expires_at: float
    mac: str


class GateStage(Enum):
    STARTED = "started"
    IDENTITY_CONFIRMED = "identity_confirmed"
    SCOPE_ACKNOWLEDGED = "scope_acknowledged"
    COMPLETED = "completed"
    INVALIDATED = "invalidated"


_SESSION_KEY = object()


class GateAuthority:
    """Owns the signing key and the set of already-used tokens."""

    def __init__(self, token_ttl_seconds: float = 300.0, clock=time.time):
        self._key = secrets.token_bytes(32)
        self._consumed: set[str] = set()
        self._clock = clock
        self._ttl = float(token_ttl_seconds)

    def begin_session(self, drive, plan) -> "GateSession":
        if drive.is_boot:
            raise BootDeviceError(
                "This is the drive Beerus booted from. It can never be wiped."
            )
        if not drive.identifiable:
            raise UnidentifiableDriveError(_UNIDENTIFIABLE_MESSAGE)
        scope = ScopeStatement(
            text=build_scope_text(drive, plan),
            drive_fingerprint=drive.fingerprint,
            model=drive.model,
            serial=drive.serial,
            size_bytes=drive.size_bytes,
            method_label=plan.method_label,
            scope_hash=scope_hash_for(drive, plan),
        )
        deadline = self._clock() + self._ttl
        return GateSession(self, drive, plan, scope, deadline, _key=_SESSION_KEY)

    def _mac(self, fingerprint, scope_hash, nonce, expires_at) -> str:
        material = "|".join(
            [fingerprint, scope_hash, nonce, repr(expires_at)]
        )
        return hmac.new(
            self._key, material.encode(), hashlib.sha256
        ).hexdigest()

    def _mint(self, session) -> ConfirmationToken:
        nonce = secrets.token_hex(16)
        expires_at = session._deadline
        mac = self._mac(
            session.drive.fingerprint, session.scope.scope_hash, nonce, expires_at
        )
        return ConfirmationToken(
            fingerprint=session.drive.fingerprint,
            scope_hash=session.scope.scope_hash,
            nonce=nonce,
            expires_at=expires_at,
            mac=mac,
        )

    def verify_and_consume(self, token, drive, plan) -> None:
        if not isinstance(token, ConfirmationToken):
            raise TokenError(
                "That confirmation is not valid. Start the confirmation again."
            )
        if drive.is_boot:
            raise BootDeviceError(
                "This is the drive Beerus booted from. It can never be wiped."
            )
        if not drive.identifiable:
            raise UnidentifiableDriveError(_UNIDENTIFIABLE_MESSAGE)
        expected_mac = self._mac(
            token.fingerprint, token.scope_hash, token.nonce, token.expires_at
        )
        if not hmac.compare_digest(token.mac, expected_mac):
            raise TokenError(
                "That confirmation is not valid. Start the confirmation again."
            )
        if token.fingerprint != drive.fingerprint:
            raise TokenError(
                "That confirmation was for a different drive. Nothing was wiped."
            )
        if token.scope_hash != scope_hash_for(drive, plan):
            raise TokenError(
                "The wipe plan changed after confirmation. "
                "Nothing was wiped — confirm again."
            )
        if self._clock() >= token.expires_at:
            raise TokenError("That confirmation expired. Start again.")
        if token.nonce in self._consumed:
            raise TokenError(
                "That confirmation has already been used. Start again."
            )
        self._consumed.add(token.nonce)


class GateSession:
    """One in-progress confirmation sequence for one drive and plan.

    Created only via GateAuthority.begin_session.
    """

    def __init__(self, authority, drive, plan, scope, deadline, _key=None):
        if _key is not _SESSION_KEY:
            raise GateError("Confirmation sessions must be started through the gate.")
        self._authority = authority
        self.drive = drive
        self.plan = plan
        self.scope = scope
        self._deadline = deadline
        self.stage = GateStage.STARTED

    def _invalidate(self) -> None:
        self.stage = GateStage.INVALIDATED

    def _require_live(self):
        if self.stage is GateStage.INVALIDATED:
            raise GateError("This confirmation was cancelled. Start again.")
        if self._authority._clock() > self._deadline:
            raise GateError(
                "This confirmation took too long and expired. Start again."
            )

    def confirm_identity(self, typed_serial):
        self._require_live()
        if self.stage is not GateStage.STARTED:
            raise GateError("Steps must be completed in order.")
        if str(typed_serial).strip() != self.drive.serial:
            self._invalidate()
            raise IdentityMismatchError(
                "That serial number does not match this drive. For safety, "
                "this confirmation has been cancelled — check the drive "
                "and start again."
            )
        self.stage = GateStage.IDENTITY_CONFIRMED

    def acknowledge_scope(self):
        self._require_live()
        if self.stage is not GateStage.IDENTITY_CONFIRMED:
            raise ScopeNotAcknowledgedError(
                "Confirm the drive's serial number before acknowledging "
                "the wipe details."
            )
        self.stage = GateStage.SCOPE_ACKNOWLEDGED
        return self.scope

    def final_confirmation(self, typed):
        self._require_live()
        if self.stage is not GateStage.SCOPE_ACKNOWLEDGED:
            raise ScopeNotAcknowledgedError(
                "Read and acknowledge the wipe details before the final "
                "confirmation."
            )
        expected = f"ERASE {self.drive.serial}"
        if str(typed).strip() != expected:
            self._invalidate()
            raise ConfirmationError(
                "That is not the exact confirmation phrase. For safety, "
                "this confirmation has been cancelled."
            )
        self.stage = GateStage.COMPLETED
        return self._authority._mint(self)
