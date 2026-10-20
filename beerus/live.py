"""The Destruct Key Pro boot console.

This is the entry point the live image starts at boot (see
``image/`` in this repository): a plain terminal session that runs
the whole job — list the drives, run the consent gates, wipe,
verify, and issue the job document. It is deliberately *not* a
second interface over the engine: every step goes through the same
``GateAuthority``, ``WipeEngine``, ``WipeVerifier``, and
certificate code the portable app uses, and the Phase 4 boot UI
will sit on these same flows.

Arming law, enforced in ``build_live_engine`` itself:

* The real-drive executor is armed only inside
  ``build_live_engine``, and that function demands the proofs
  before it arms anything: the build-time image marker
  (``beerus.bootmedium``) must load and validate in the running
  system, and the session must be root. There is no way to call
  it "just to build the engine" and skip the proofs — a caller
  that lacks them gets a plain-language refusal, and no engine.
* ``main()`` is the module's caller: it hands the builder the
  seams it was given, prints the refusal, and exits 2.
* Nothing in this module arms on import, and the test suite only
  ever reaches the console through injected fakes — the executor
  the console runs with in tests is the file-backed one, against
  synthetic file targets, exactly like the engine tests. The one
  builder test passes the builder a throwaway environment
  mapping; the executor's own check of the real process
  environment remains the final word at wipe time.

Job documents are written to the directory the image marker
names — memory-backed storage inside the live system, never the
key itself — and the console tells the operator, at start and
after every document, that the files are gone when the machine
shuts down unless they are copied off first.
"""

from __future__ import annotations

import os
from pathlib import Path

from beerus.bootmedium import (
    IMAGE_MARKER_PATH,
    load_image_marker,
    marker_documents_dir,
)
from beerus.certificate import build_job_record, issue_document, write_document
from beerus.engine import WipeEngine
from beerus.errors import BeerusError, RefusalError
from beerus.executor import RealExecutor, _default_runner
from beerus.gates import GateAuthority
from beerus.methods import select_plan
from beerus.verify import DeviceSurfaceReader, VerificationVerdict, WipeVerifier

_NOT_LIVE_MESSAGE = (
    "This copy of Beerus is not running from the Destruct Key Pro "
    "live image, so it will not arm the drive wiper. The boot "
    "console only runs on the key itself."
)

_NOT_ROOT_MESSAGE = (
    "The boot console must run as the system's root session, the "
    "way the Destruct Key Pro starts it. It will not arm the "
    "drive wiper from a lesser session."
)

_VERDICT_WORDS = {
    VerificationVerdict.PASS: "PASSED",
    VerificationVerdict.FAIL: "FAILED",
    VerificationVerdict.NOT_VERIFIED: "NOT VERIFIED",
}


class ConsoleIO:
    """Terminal I/O behind a seam, so sessions are testable.

    ``ask`` returns the operator's raw line; EOF and Ctrl+C
    propagate as exceptions, and the console treats both as the
    operator walking away — a safe state at every prompt, because
    nothing destructive can be in flight while Beerus is waiting
    for an answer.
    """

    def __init__(self, input_fn=input, output_fn=print):
        self._input_fn = input_fn
        self._output_fn = output_fn

    def say(self, text: str = "") -> None:
        self._output_fn(text)

    def ask(self, prompt: str) -> str:
        return self._input_fn(prompt)


def _user_message(exc: BeerusError) -> str:
    return getattr(exc, "user_message", None) or str(exc)


def _os_reason(exc: OSError) -> str:
    """A plain-language rendering of an OS error's reason."""
    return exc.strerror or "the system refused the operation"


def _say_wipe_unfinished(io: ConsoleIO, reason: str) -> None:
    """The one wording for any wipe that did not complete.

    However a wipe stops early — interrupted, refused by the
    system, failed in a way Beerus did not foresee — the operator
    hears the same honest sentence: this drive is NOT sanitized,
    and nothing about it may be relied on until a full wipe and
    verification complete.
    """
    io.say("")
    io.say(reason)
    io.say(
        "Treat this drive as NOT sanitized: its data may be "
        "partly or fully intact. Run the wipe again and let "
        "verification complete before relying on it."
    )
    io.say("")


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


def _say_welcome(io: ConsoleIO, documents_dir: str) -> None:
    io.say("BEERUS — Destruct Key Pro")
    io.say("Secure drive destruction — Sentinel Safeguard LLC")
    io.say("")
    io.say(
        "This machine booted from the Destruct Key Pro. The key "
        "itself can never be wiped, and no drive is touched until "
        "you choose one and complete the confirmation steps."
    )
    io.say("")
    io.say("Job documents from this session are kept in memory only, at:")
    io.say(f"  {documents_dir}")
    io.say(
        "Copy each document somewhere safe before this machine is "
        "shut down — when it turns off, they are gone."
    )
    io.say("")


def _ask_operator(io: ConsoleIO) -> str | None:
    """The operator name for the job documents; None if they leave."""
    while True:
        try:
            name = io.ask("Operator name (printed on the job documents): ")
        except (EOFError, KeyboardInterrupt):
            io.say("")
            io.say("Finished. Nothing was wiped.")
            return None
        name = name.strip()
        if name:
            return name
        io.say("Beerus needs an operator name for the job documents.")


def _say_drive_list(io: ConsoleIO, assessments) -> None:
    io.say("Drives Beerus can see:")
    for index, assessment in enumerate(assessments, start=1):
        drive = assessment.drive
        model = drive.model or "Model not readable"
        serial = drive.serial or "serial not readable"
        io.say(
            f"  {index}. {model} — serial {serial} — "
            f"{_human_size(drive.size_bytes)}"
        )
        if assessment.eligible:
            io.say("     This drive can be wiped.")
        else:
            io.say(f"     This drive cannot be wiped: {assessment.reason}")
    io.say("")


def _confirm_and_wipe(io, engine, authority, drive, operator, documents_dir) -> None:
    """Run the gate sequence for *drive*, then the wipe and document.

    Every refusal along the way has already printed its reason by
    the time this returns; returning means "back to the drive
    list," never "something silently failed."
    """
    try:
        plan = select_plan(drive)
        session = authority.begin_session(drive, plan)
    except BeerusError as exc:
        io.say(_user_message(exc))
        io.say("")
        return
    except Exception:
        io.say(
            "Beerus could not start the confirmation for that "
            "drive. Nothing was wiped."
        )
        io.say("")
        return

    try:
        io.say(f"You chose: {drive.model} (serial {drive.serial}).")
        io.say("")
        io.say(session.scope.text)
        io.say("")
        typed_serial = io.ask(
            "Type the serial number of the drive you chose, "
            "exactly as shown above: "
        )
        session.confirm_identity(typed_serial)
        answer = io.ask(
            "If you have read the wipe details above and accept "
            "them, type ACKNOWLEDGE: "
        )
        if answer.strip() != "ACKNOWLEDGE":
            io.say(
                "Cancelled — the wipe details were not acknowledged. "
                "Nothing was wiped."
            )
            io.say("")
            return
        session.acknowledge_scope()
        typed_phrase = io.ask(
            f"Final confirmation — type ERASE {drive.serial} to "
            "destroy all data on this drive: "
        )
        token = session.final_confirmation(typed_phrase)
    except (EOFError, KeyboardInterrupt):
        io.say("")
        io.say("Cancelled. Nothing was wiped.")
        io.say("")
        return
    except BeerusError as exc:
        io.say(_user_message(exc))
        io.say("")
        return
    except Exception:
        io.say(
            "The confirmation could not be completed. "
            "Nothing was wiped."
        )
        io.say("")
        return

    io.say("")
    io.say("Starting the wipe. Progress is reported as it happens.")
    io.say("")

    def emit(event) -> None:
        percent = event.percent
        if percent is not None:
            io.say(f"  {event.message} ({percent}%)")
        else:
            io.say(f"  {event.message}")

    try:
        result = engine.wipe(drive, token, emit=emit)
    except KeyboardInterrupt:
        _say_wipe_unfinished(
            io, "The wipe was interrupted before it finished."
        )
        return
    except BeerusError as exc:
        io.say(_user_message(exc))
        io.say("")
        return
    except OSError as exc:
        _say_wipe_unfinished(
            io,
            "The wipe stopped before it finished: "
            f"{_os_reason(exc)}.",
        )
        return
    except Exception:
        _say_wipe_unfinished(
            io,
            "The wipe stopped before it finished, for a reason "
            "Beerus could not read.",
        )
        return

    io.say("")
    io.say(f"Wipe finished: {result.method_label}.")
    verification = result.verification
    if verification is None:
        io.say(
            "No document was issued: without verification, there "
            "is nothing Beerus can certify."
        )
        io.say("")
        return
    io.say(
        "Verification: "
        f"{_VERDICT_WORDS.get(verification.verdict, 'NOT VERIFIED')} — "
        f"{verification.detail}"
    )
    try:
        record = build_job_record(drive, result, operator)
        document = issue_document(record)
        path = Path(write_document(document, documents_dir))
    except BeerusError as exc:
        io.say(
            "The wipe finished, but its document could not be "
            f"issued: {_user_message(exc)}"
        )
        io.say("")
        return
    except OSError as exc:
        io.say(
            "The wipe finished, and the verification above stands "
            "— but its document could not be saved: "
            f"{_os_reason(exc)}. A document can only be issued in "
            "the same run as its wipe, so this run's document "
            "cannot be recreated. Check the documents location "
            f"({documents_dir}) before the next job."
        )
        io.say("")
        return
    except Exception:
        io.say(
            "The wipe finished, and the verification above stands "
            "— but its document could not be issued, for a reason "
            "Beerus could not read. A document can only be issued "
            "in the same run as its wipe, so this run's document "
            "cannot be recreated."
        )
        io.say("")
        return
    if document.kind == "certificate":
        io.say(f"Certificate of destruction saved: {path.name}")
    else:
        io.say(
            "This wipe did not earn a certificate. A failure "
            f"report was saved instead: {path.name}"
        )
    io.say(f"Saved in: {path.parent}")
    io.say(
        "Copy it somewhere safe before this machine is shut down — "
        "it is stored in memory only."
    )
    io.say("")


def run_console(io: ConsoleIO, engine, authority, documents_dir: str) -> int:
    """Run one boot-console session. Returns the process exit code."""
    _say_welcome(io, documents_dir)
    operator = _ask_operator(io)
    if operator is None:
        return 0

    while True:
        try:
            assessments = engine.assess_all()
        except BeerusError as exc:
            io.say(_user_message(exc))
            io.say("Beerus cannot continue without a drive list.")
            return 0
        except Exception:
            io.say(
                "Beerus could not read the list of drives on this "
                "machine."
            )
            io.say("Beerus cannot continue without a drive list.")
            return 0
        _say_drive_list(io, assessments)
        if not any(a.eligible for a in assessments):
            io.say(
                "No drive on this machine can be wiped right now. "
                "Connect a drive to destroy, then list the drives "
                "again — or quit and shut the machine down."
            )
            io.say("")
        try:
            choice = io.ask(
                "Type a drive number to choose it, R to list the "
                "drives again, or Q to quit: "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            io.say("")
            io.say("Finished. Nothing else was wiped.")
            return 0
        if choice.upper() == "Q":
            io.say("Finished. Nothing else was wiped.")
            return 0
        if choice.upper() == "R":
            continue
        # A menu number is digits only, and must name a listed
        # drive. Anything else — "0", "-1", "+1", words — selects
        # nothing: in a destruction tool, a mistyped number must
        # never land on a drive the operator did not point at
        # (Python's negative indexing would happily do exactly
        # that with the naive form of this parse).
        number = (
            int(choice)
            if choice and all(ch in "0123456789" for ch in choice)
            else None
        )
        if number is None or not 1 <= number <= len(assessments):
            io.say(
                "That is not one of the choices. Type a drive "
                "number, R, or Q."
            )
            io.say("")
            continue
        picked = assessments[number - 1]
        if not picked.eligible:
            io.say(f"That drive cannot be wiped: {picked.reason}")
            io.say("")
            continue
        _confirm_and_wipe(
            io, engine, authority, picked.drive, operator, documents_dir
        )


def _default_geteuid():
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None:
        return -1
    return geteuid()


def build_live_engine(
    environ=None,
    *,
    marker_path: str = IMAGE_MARKER_PATH,
    opener=None,
    geteuid=None,
):
    """Build the live session's engine — the one arming site.

    The arming proofs are enforced HERE, not trusted to callers:
    the image marker must load and validate from the running
    system, and the session must be root. If either proof fails,
    this raises ``RefusalError`` — before anything is armed, and
    before an engine exists. Only with both proofs does it set
    the executor's environment factor in *environ* (the process
    environment by default) and construct the executor with its
    constructor factor set.

    Construction alone still sends nothing to any drive: the
    executor re-checks both factors at execute time, and every
    wipe still needs the engine's gate-token flow in front of it.
    A custom *environ* is a test seam: the factor is recorded
    there for inspection, and the executor's own check of the
    real process environment remains the final word at wipe time.

    Returns ``(engine, authority, documents_dir)`` — the
    documents directory the marker names, constrained by
    ``beerus.bootmedium`` to the live system's memory.
    """
    marker = load_image_marker(marker_path, opener=opener)
    if marker is None:
        raise RefusalError(_NOT_LIVE_MESSAGE)
    if geteuid is None:
        geteuid = _default_geteuid
    if geteuid() != 0:
        raise RefusalError(_NOT_ROOT_MESSAGE)
    if environ is None:
        environ = os.environ
    environ[RealExecutor.ARM_ENV_VAR] = "1"
    authority = GateAuthority()
    executor = RealExecutor(armed=True)
    verifier = WipeVerifier(
        surface_factory=lambda drive: DeviceSurfaceReader(
            drive.device_path, drive.size_bytes
        ),
        # NVMe verification asks the drive its own sanitize
        # status through the same runner seam the executor uses —
        # the wiring the portable launcher ships with. Without it
        # an NVMe wipe could never be verified, and never earn a
        # certificate, on the key.
        firmware_runner=_default_runner,
    )
    engine = WipeEngine(authority, executor, verifier=verifier)
    return engine, authority, marker_documents_dir(marker)


def main(
    argv=None,
    *,
    io: ConsoleIO | None = None,
    marker_path: str = IMAGE_MARKER_PATH,
    opener=None,
    environ=None,
    geteuid=None,
    engine_factory=None,
) -> int:
    """Boot-console entry point. Returns the process exit code.

    Exit code 2 means Beerus refused to start the console at all
    (not a live image, or not the root session) — a designed
    refusal with the reason printed, never a traceback. The
    proofs themselves are enforced by the engine builder (the
    arming site); ``main`` hands it the caller's seams and
    reports its verdict.
    """
    if io is None:
        io = ConsoleIO()
    if engine_factory is None:
        engine_factory = build_live_engine
    try:
        engine, authority, documents_dir = engine_factory(
            environ,
            marker_path=marker_path,
            opener=opener,
            geteuid=geteuid,
        )
    except RefusalError as exc:
        io.say(exc.user_message)
        return 2
    return run_console(io, engine, authority, documents_dir)


if __name__ == "__main__":
    raise SystemExit(main())
