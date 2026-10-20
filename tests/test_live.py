"""Boot-console tests — scripted sessions, synthetic targets only.

SAFETY: the console is only ever driven with engines backed by
the FileExecutor against a small regular file under tmp_path, or
with main()'s engine factory replaced by a fake. The live
engine builder is checked for its arming posture but never asked
to wipe. No test can reach a real device.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import beerus
from beerus.bootmedium import IMAGE_MARKER_FORMAT
from beerus.engine import WipeEngine
from beerus.errors import BeerusError, RefusalError
from beerus.executor import FileExecutor, RealExecutor
from beerus.gates import GateAuthority
from beerus.live import ConsoleIO, build_live_engine, main, run_console
from beerus.methods import select_plan
from beerus.verify import FileSurfaceReader, VerificationVerdict, WipeVerifier
from conftest import (
    MIB,
    make_boot_drive,
    make_drive,
    make_nvme,
    make_unidentifiable,
)

SIZE = 2 * MIB
SENTINEL = b"\xaa"


class ScriptedIO(ConsoleIO):
    """Console I/O fed from a script; prints captured in .lines."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.lines: list[str] = []

        def _input(prompt):
            self.lines.append(prompt)
            if not self.answers:
                raise EOFError
            answer = self.answers.pop(0)
            if isinstance(answer, BaseException):
                raise answer
            return answer

        super().__init__(input_fn=_input, output_fn=self.lines.append)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _filled_device(fake_device_file, size=SIZE):
    path, actual = fake_device_file(size)
    with open(path, "wb") as fh:
        fh.write(SENTINEL * actual)
    return path, actual


def _console_engine(authority, executor, drives, verifier=None):
    return WipeEngine(
        authority, executor, enumerator=lambda: list(drives), verifier=verifier
    )


def _run_session(tmp_path, fake_device_file, answers, drives=None, size=SIZE):
    path, actual = _filled_device(fake_device_file, size)
    if drives is None:
        drives = [make_drive(size_bytes=actual)]
    authority = GateAuthority()
    verifier = WipeVerifier(
        surface_factory=lambda drive: FileSurfaceReader(path)
    )
    engine = _console_engine(
        authority, FileExecutor(path), drives, verifier=verifier
    )
    io = ScriptedIO(answers)
    documents_dir = tmp_path / "documents"
    code = run_console(io, engine, authority, str(documents_dir))
    with open(path, "rb") as fh:
        contents = fh.read()
    return io, code, contents, documents_dir


# ------------------------------------------------------------------ happy path


def test_console_full_run_wipes_verifies_and_certifies(
    tmp_path, fake_device_file
):
    drive = make_drive()
    answers = [
        "Operator One",          # operator name
        "1",                     # choose the drive
        drive.serial,            # typed serial
        "ACKNOWLEDGE",           # scope acknowledgment
        f"ERASE {drive.serial}",  # final phrase
        "Q",                     # quit at the next list
    ]
    io, code, contents, documents_dir = _run_session(
        tmp_path, fake_device_file, answers
    )
    assert code == 0
    assert contents == b"\x00" * SIZE
    assert "PASSED" in io.text
    assert "Certificate of destruction saved:" in io.text
    certificates = list(documents_dir.glob("beerus-certificate-*.html"))
    assert len(certificates) == 1
    assert "stored in memory only" in io.text


# ------------------------------------------------------------------ refusals


def test_console_boot_drive_cannot_be_chosen(tmp_path, fake_device_file):
    boot = make_boot_drive(device_path="/dev/sdz", raw_name="sdz")
    answers = ["Operator One", "1", "Q"]
    io, code, contents, _ = _run_session(
        tmp_path, fake_device_file, answers, drives=[boot]
    )
    assert code == 0
    assert "This drive cannot be wiped:" in io.text
    assert "booted from" in io.text
    assert contents == SENTINEL * SIZE


def test_console_unidentifiable_drive_cannot_be_chosen(
    tmp_path, fake_device_file
):
    mystery = make_unidentifiable()
    answers = ["Operator One", "1", "Q"]
    io, code, contents, _ = _run_session(
        tmp_path, fake_device_file, answers, drives=[mystery]
    )
    assert code == 0
    assert "could not identify this drive" in io.text
    assert contents == SENTINEL * SIZE


def test_console_wrong_serial_cancels_and_wipes_nothing(
    tmp_path, fake_device_file
):
    drive = make_drive()
    answers = ["Operator One", "1", "WRONG-SERIAL", "Q"]
    io, code, contents, _ = _run_session(tmp_path, fake_device_file, answers)
    assert code == 0
    assert "does not match this drive" in io.text
    assert contents == SENTINEL * SIZE


def test_console_unacknowledged_scope_cancels(tmp_path, fake_device_file):
    drive = make_drive()
    answers = ["Operator One", "1", drive.serial, "not really", "Q"]
    io, code, contents, _ = _run_session(tmp_path, fake_device_file, answers)
    assert code == 0
    assert "were not acknowledged" in io.text
    assert contents == SENTINEL * SIZE


def test_console_wrong_final_phrase_cancels(tmp_path, fake_device_file):
    drive = make_drive()
    answers = [
        "Operator One", "1", drive.serial, "ACKNOWLEDGE", "erase it", "Q",
    ]
    io, code, contents, _ = _run_session(tmp_path, fake_device_file, answers)
    assert code == 0
    assert "not the exact confirmation phrase" in io.text
    assert contents == SENTINEL * SIZE


def test_console_invalid_choice_is_explained(tmp_path, fake_device_file):
    answers = ["Operator One", "banana", "9", "Q"]
    io, code, _, _ = _run_session(tmp_path, fake_device_file, answers)
    assert code == 0
    assert io.text.count("That is not one of the choices") == 2


def test_console_zero_and_negative_numbers_select_nothing(
    tmp_path, fake_device_file
):
    """H3 regression: with two drives listed, "0" and "-1" must
    not land on a drive through negative indexing."""
    first = make_drive(device_path="/dev/sdb", raw_name="sdb")
    second = make_drive(
        device_path="/dev/sdc",
        raw_name="sdc",
        serial="WD-SECOND222",
    )
    answers = ["Operator One", "0", "-1", "Q"]
    io, code, contents, _ = _run_session(
        tmp_path, fake_device_file, answers, drives=[first, second]
    )
    assert code == 0
    assert io.text.count("That is not one of the choices") == 2
    assert "You chose:" not in io.text
    assert contents == SENTINEL * SIZE


def test_console_document_save_failure_is_plain_and_the_wipe_stands(
    tmp_path, fake_device_file
):
    """M1 regression: documents_dir under an existing file makes
    the save raise OSError — the operator must get a plain
    sentence, not a traceback, and the wipe itself stands."""
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"not a directory")
    path, actual = _filled_device(fake_device_file)
    drive = make_drive(size_bytes=actual)
    authority = GateAuthority()
    verifier = WipeVerifier(
        surface_factory=lambda d: FileSurfaceReader(path)
    )
    engine = _console_engine(
        authority, FileExecutor(path), [drive], verifier=verifier
    )
    io = ScriptedIO(
        [
            "Operator One",
            "1",
            drive.serial,
            "ACKNOWLEDGE",
            f"ERASE {drive.serial}",
            "Q",
        ]
    )
    code = run_console(io, engine, authority, str(blocker / "docs"))
    assert code == 0
    assert "PASSED" in io.text
    assert "could not be saved" in io.text
    with open(path, "rb") as fh:
        assert fh.read() == b"\x00" * SIZE


def test_console_unexpected_executor_error_warns_not_sanitized(
    tmp_path, fake_device_file
):
    """M1 regression: a non-BeerusError from the executor must
    not escape as a traceback; the drive is NOT sanitized."""
    _path, actual = _filled_device(fake_device_file)
    drive = make_drive(size_bytes=actual)
    authority = GateAuthority()

    class ExplodingExecutor:
        def execute(self, drive_, plan, emit):
            raise RuntimeError("synthetic explosion")

    engine = _console_engine(authority, ExplodingExecutor(), [drive])
    io = ScriptedIO(
        [
            "Operator One",
            "1",
            drive.serial,
            "ACKNOWLEDGE",
            f"ERASE {drive.serial}",
            "Q",
        ]
    )
    code = run_console(io, engine, authority, str(tmp_path / "docs"))
    assert code == 0
    assert "NOT sanitized" in io.text


def test_console_unexpected_enumeration_error_is_plain(tmp_path):
    def broken_enumerator():
        raise RuntimeError("synthetic explosion")

    authority = GateAuthority()
    engine = WipeEngine(
        authority,
        FileExecutor("/nonexistent"),
        enumerator=broken_enumerator,
    )
    io = ScriptedIO(["Operator One"])
    code = run_console(io, engine, authority, str(tmp_path / "docs"))
    assert code == 0
    assert "could not read the list of drives" in io.text


def test_console_unexpected_gate_error_is_plain(tmp_path, fake_device_file):
    """Round-3 regression: even the gate seam answers an
    unexpected error in plain language — nothing was wiped."""
    _path, actual = _filled_device(fake_device_file)
    drive = make_drive(size_bytes=actual)

    class ExplodingAuthority:
        def begin_session(self, drive_, plan):
            raise RuntimeError("synthetic explosion")

    authority = GateAuthority()
    engine = _console_engine(authority, FileExecutor(_path), [drive])
    io = ScriptedIO(["Operator One", "1", "Q"])
    code = run_console(
        io, engine, ExplodingAuthority(), str(tmp_path / "docs")
    )
    assert code == 0
    assert "could not start the confirmation" in io.text


def test_console_scope_display_failure_is_plain(
    tmp_path, fake_device_file
):
    """Round-4 regression: even presenting the session's scope
    answers an unexpected error plainly — nothing was wiped."""

    class ExplodingScope:
        @property
        def text(self):
            raise RuntimeError("synthetic explosion")

    class ExplodingSession:
        scope = ExplodingScope()

    class StubAuthority:
        def begin_session(self, drive_, plan):
            return ExplodingSession()

    _path, actual = _filled_device(fake_device_file)
    drive = make_drive(size_bytes=actual)
    authority = GateAuthority()
    engine = _console_engine(authority, FileExecutor(_path), [drive])
    io = ScriptedIO(["Operator One", "1", "Q"])
    code = run_console(io, engine, StubAuthority(), str(tmp_path / "docs"))
    assert code == 0
    assert "could not be completed" in io.text


def test_console_unexpected_document_error_is_plain(
    tmp_path, fake_device_file, monkeypatch
):
    """Round-3 regression: a non-BeerusError while issuing the
    document is a plain sentence; the wipe still stands."""
    import beerus.live as live_module

    def explode(drive, result, operator):
        raise RuntimeError("synthetic explosion")

    monkeypatch.setattr(live_module, "build_job_record", explode)
    io, code, contents, _docs = _run_session(
        tmp_path,
        fake_device_file,
        [
            "Operator One",
            "1",
            make_drive().serial,
            "ACKNOWLEDGE",
            f"ERASE {make_drive().serial}",
            "Q",
        ],
    )
    assert code == 0
    assert "could not be issued" in io.text
    assert contents == b"\x00" * SIZE


def test_console_no_eligible_drives_is_a_designed_state(
    tmp_path, fake_device_file
):
    boot = make_boot_drive(device_path="/dev/sdz", raw_name="sdz")
    answers = ["Operator One", "R", "Q"]
    io, code, _, _ = _run_session(
        tmp_path, fake_device_file, answers, drives=[boot]
    )
    assert code == 0
    assert "No drive on this machine can be wiped right now" in io.text


def test_console_enumeration_failure_is_plain(tmp_path):
    def broken_enumerator():
        raise BeerusError("Beerus could not read the list of drives.")

    authority = GateAuthority()
    engine = WipeEngine(
        authority,
        FileExecutor("/nonexistent"),
        enumerator=broken_enumerator,
    )
    io = ScriptedIO(["Operator One"])
    code = run_console(io, engine, authority, str(tmp_path / "docs"))
    assert code == 0
    assert "could not read the list of drives" in io.text


# ------------------------------------------------------------- walking away


def test_console_eof_at_operator_prompt_exits_cleanly(tmp_path):
    io = ScriptedIO([])
    authority = GateAuthority()
    engine = _console_engine(authority, FileExecutor("/nonexistent"), [])
    code = run_console(io, engine, authority, str(tmp_path / "docs"))
    assert code == 0
    assert "Finished. Nothing was wiped." in io.text


def test_console_eof_mid_gates_cancels_and_wipes_nothing(
    tmp_path, fake_device_file
):
    drive = make_drive()
    answers = ["Operator One", "1"]  # EOF at the serial prompt
    io, code, contents, _ = _run_session(tmp_path, fake_device_file, answers)
    assert code == 0
    assert "Cancelled. Nothing was wiped." in io.text
    assert contents == SENTINEL * SIZE


def test_console_interrupt_during_wipe_warns_not_sanitized(
    tmp_path, fake_device_file
):
    path, actual = _filled_device(fake_device_file)
    drive = make_drive(size_bytes=actual)
    authority = GateAuthority()

    class InterruptingExecutor:
        def execute(self, drive_, plan, emit):
            raise KeyboardInterrupt

    engine = _console_engine(
        authority, InterruptingExecutor(), [drive]
    )
    io = ScriptedIO(
        [
            "Operator One",
            "1",
            drive.serial,
            "ACKNOWLEDGE",
            f"ERASE {drive.serial}",
            "Q",
        ]
    )
    code = run_console(io, engine, authority, str(tmp_path / "docs"))
    assert code == 0
    assert "NOT sanitized" in io.text


# ------------------------------------------------------------- main() gating


def _marker_opener(payload):
    def opener(path, mode, encoding=None):
        return _TextStream(payload)

    return opener


class _TextStream:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        import io as _io

        return _io.StringIO(self._payload)

    def __exit__(self, *exc):
        return False


def _valid_marker_payload(documents_dir="/var/log/beerus/certificates"):
    return json.dumps(
        {
            "image": IMAGE_MARKER_FORMAT,
            "version": "0.5.0",
            "volume_label": "BEERUS_KEY",
            "documents_dir": documents_dir,
            "built": "2026-10-20",
        }
    )


def _missing_opener(path, mode, encoding=None):
    raise FileNotFoundError(path)


def test_main_refuses_without_a_marker():
    """The REAL builder is the arming site: with no marker, main
    reports its refusal and nothing is armed or built."""
    environ = {}
    io = ScriptedIO([])
    code = main(
        io=io,
        opener=_missing_opener,
        environ=environ,
        geteuid=lambda: 0,
    )
    assert code == 2
    assert "not running from the Live Boot image" in io.text
    assert environ == {}


def test_main_refuses_a_foreign_marker():
    io = ScriptedIO([])
    code = main(
        io=io,
        opener=_marker_opener(
            '{"image": "someone-elses-image", "volume_label": "BEERUS_KEY"}'
        ),
        environ={},
        geteuid=lambda: 0,
    )
    assert code == 2
    assert "not running from the Live Boot image" in io.text


def test_main_refuses_a_non_root_session():
    environ = {}
    io = ScriptedIO([])
    code = main(
        io=io,
        opener=_marker_opener(_valid_marker_payload()),
        environ=environ,
        geteuid=lambda: 1000,
    )
    assert code == 2
    assert "root session" in io.text
    assert environ == {}


def test_main_runs_the_console_through_the_factory_seams():
    seen = {}

    def factory(environ, *, marker_path, opener, geteuid):
        seen["environ"] = environ
        seen["marker_path"] = marker_path
        authority = GateAuthority()
        engine = _console_engine(
            authority, FileExecutor("/nonexistent"), []
        )
        return engine, authority, "/var/log/marker-docs"

    environ = {}
    io = ScriptedIO(["Operator One", "Q"])
    code = main(
        io=io,
        opener=_marker_opener(_valid_marker_payload("/var/log/marker-docs")),
        environ=environ,
        geteuid=lambda: 0,
        engine_factory=factory,
    )
    assert code == 0
    assert seen["environ"] is environ
    assert "/var/log/marker-docs" in io.text


# ------------------------------------------------------------- arming posture


def test_build_live_engine_arms_both_factors_with_proofs():
    environ = {}
    engine, authority, documents_dir = build_live_engine(
        environ,
        opener=_marker_opener(_valid_marker_payload()),
        geteuid=lambda: 0,
    )
    assert environ[RealExecutor.ARM_ENV_VAR] == "1"
    assert isinstance(engine._executor, RealExecutor)
    assert engine._executor.armed is True
    assert isinstance(engine._verifier, WipeVerifier)
    assert isinstance(authority, GateAuthority)
    assert documents_dir == "/var/log/beerus/certificates"


def test_build_live_engine_refuses_without_proofs():
    """There is no way to call the builder 'just for the engine':
    without the marker proof it raises before arming anything."""
    environ = {}
    with pytest.raises(RefusalError):
        build_live_engine(
            environ, opener=_missing_opener, geteuid=lambda: 0
        )
    assert environ == {}

    environ = {}
    with pytest.raises(RefusalError):
        build_live_engine(
            environ,
            opener=_marker_opener(_valid_marker_payload()),
            geteuid=lambda: 1000,
        )
    assert environ == {}


def test_build_live_engine_defaults_documents_dir():
    payload = json.dumps(
        {"image": IMAGE_MARKER_FORMAT, "volume_label": "BEERUS_KEY"}
    )
    _engine, _authority, documents_dir = build_live_engine(
        {},
        opener=_marker_opener(payload),
        geteuid=lambda: 0,
    )
    assert documents_dir == "/var/log/beerus/certificates"


def test_live_verifier_can_verify_an_nvme_wipe(fake_device_file):
    """H2 regression: the live verifier must carry the firmware
    runner, or no NVMe wipe on the key could ever be certified.
    Seams are stubbed here so nothing real is asked or read."""
    engine, _authority, _docs = build_live_engine(
        {},
        opener=_marker_opener(_valid_marker_payload()),
        geteuid=lambda: 0,
    )
    verifier = engine._verifier
    assert verifier._firmware_runner is not None

    path, size = fake_device_file(SIZE)  # reads back as zeros
    drive = make_nvme(size_bytes=size)
    plan = select_plan(drive)
    verifier._surface_factory = lambda d: FileSurfaceReader(path)
    verifier._firmware_runner = lambda argv: (
        0,
        "Sanitize Progress (SPROG) : 65535",
    )
    result = verifier.verify(drive, plan)
    assert result.verdict is VerificationVerdict.PASS


def test_importing_the_package_arms_nothing(monkeypatch):
    monkeypatch.delenv(RealExecutor.ARM_ENV_VAR, raising=False)
    import os

    assert beerus.__version__ == "0.6.1"
    assert os.environ.get(RealExecutor.ARM_ENV_VAR) is None


def test_module_entry_point_refuses_cleanly_off_image():
    """`python3 -m beerus` on an ordinary machine: the designed
    refusal, exit code 2, no warning noise, no traceback."""
    root = Path(beerus.__file__).resolve().parent.parent
    proc = subprocess.run(
        [sys.executable, "-m", "beerus"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 2
    assert "not running from the Live Boot" in proc.stdout
    assert "RuntimeWarning" not in proc.stderr
    assert "Traceback" not in proc.stderr
