"""Error types for Beerus.

Every error that can reach the user carries a plain-language
``user_message``. Raw OS errors, tracebacks, and command output never
leak into those messages; technical detail, when it exists, lives in a
separate attribute (``reason`` / ``detail``) for logs and reports.
"""

from __future__ import annotations


class BeerusError(Exception):
    """Base class for every error Beerus raises on purpose."""


class RefusalError(BeerusError):
    """Beerus refused to act — nothing was wiped.

    A refusal is a safety outcome, not a failure: the tool looked at
    what it was asked to do and said no, in plain language.
    """

    def __init__(self, user_message: str, *, reason: str = "") -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.reason = reason

    def __str__(self) -> str:
        return self.user_message


class UnidentifiableDriveError(RefusalError):
    """The drive's model/serial/size could not be read reliably."""


class BootDeviceError(RefusalError):
    """The target is the drive the system booted from."""


class GateError(RefusalError):
    """A consent gate was not satisfied."""


class IdentityMismatchError(GateError):
    """The typed identity did not match the selected drive."""


class ScopeNotAcknowledgedError(GateError):
    """The destruction scope was not explicitly acknowledged."""


class ConfirmationError(GateError):
    """The final confirmation was not given (or was wrong)."""


class TokenError(GateError):
    """A consent token was missing, stale, or did not match."""


class MethodUnavailableError(RefusalError):
    """No safe destruction method exists for this drive (yet)."""


class ExecutorError(BeerusError):
    """The wipe executor refused or failed to run safely."""


class ExecutorNotArmedError(ExecutorError):
    """A destructive command was attempted without arming first."""


class UnsafeTargetError(ExecutorError):
    """The executor's target did not pass its safety checks."""


class WipeFailedError(BeerusError):
    """A wipe started but did not complete successfully."""

    def __init__(self, user_message: str, *, detail: str = "") -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail

    def __str__(self) -> str:
        return self.user_message


class CertificateError(BeerusError):
    """Beerus refused to issue a document.

    Raised when the records behind a certificate or failure report
    are inconsistent, tampered with, or incomplete — Beerus will not
    put its name on a document the records do not support.
    """

    def __init__(self, user_message: str, *, detail: str = "") -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail

    def __str__(self) -> str:
        return self.user_message
