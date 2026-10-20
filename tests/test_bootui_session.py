"""Session tests for the boot UI entry point and its launcher.

Two halves:

- ``beerus.bootui.app.main`` seams: the builder's refusal shows
  a refusal window and exits 2; a graphical session that cannot
  start at all exits 76 with one plain line on stderr; a working
  factory pair exits 0. No test arms anything — the engine
  factory is always a fake or a refusal.

- The ``beerus-live`` launcher script, run for real with PATH
  stubs: a stub ``startx`` and a stub ``python3`` that log their
  arguments and exit with the code the test chooses. The
  launcher's own exit code and its fallback notice are what is
  asserted — the designed contract systemd relies on.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from beerus.bootui import app as bootui_app  # noqa: E402
from beerus.engine import WipeEngine  # noqa: E402
from beerus.errors import BeerusError, RefusalError  # noqa: E402
from beerus.executor import FileExecutor  # noqa: E402
from beerus.gates import GateAuthority  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = (
    REPO_ROOT
    / "image/config/includes.chroot/usr/local/bin/beerus-live"
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# ------------------------------------------------------------------ main()


def test_main_shows_the_refusal_and_exits_2(qapp):
    seen = {}

    attempts = {"left": 20}

    def close_refusal_window():
        for widget in QApplication.topLevelWidgets():
            labels = [
                label.text() for label in widget.findChildren(QLabel)
            ]
            if any("will not arm" in text for text in labels):
                seen["labels"] = labels
                seen["window"] = widget
                widget.close()
                return
        attempts["left"] -= 1
        if attempts["left"] > 0:
            QTimer.singleShot(300, close_refusal_window)
        else:
            # Never hang the suite on a mismatch: close everything
            # so main() returns and the assertions below fail plainly.
            for widget in QApplication.topLevelWidgets():
                widget.close()

    def factory():
        raise RefusalError(
            "This copy of Beerus is not running from the Live Boot "
            "image, so it will not arm the drive wiper."
        )

    QTimer.singleShot(300, close_refusal_window)
    code = bootui_app.main([], engine_factory=factory)
    assert code == bootui_app.EXIT_REFUSAL == 2
    assert seen["labels"], "the refusal window carried the reason"
    assert any(
        "not running from the Live Boot image" in text
        for text in seen["labels"]
    )


def _run_main_with_factory(qapp, factory, needle):
    """Run main() with a raising engine factory; close the
    refusal window when it shows *needle*. Returns
    (exit_code, labels_seen)."""
    seen = {}
    attempts = {"left": 20}

    def close_window():
        for widget in QApplication.topLevelWidgets():
            if not widget.isVisible():
                # A refusal window an earlier test already
                # closed is still a top-level widget; matching
                # it again would leave this run's real window
                # open and hang exec() — only visible windows
                # are this run's.
                continue
            labels = [
                label.text() for label in widget.findChildren(QLabel)
            ]
            if any(needle in text for text in labels):
                seen["labels"] = labels
                widget.close()
                return
        attempts["left"] -= 1
        if attempts["left"] > 0:
            QTimer.singleShot(300, close_window)
        else:
            for widget in QApplication.topLevelWidgets():
                widget.close()

    QTimer.singleShot(300, close_window)
    code = bootui_app.main([], engine_factory=factory)
    return code, seen.get("labels", [])


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("synthetic builder explosion"),
        OSError(5, "synthetic input/output error"),
        ValueError("synthetic bad value"),
    ],
)
def test_main_lands_unexpected_factory_errors_on_the_refusal_window(
    qapp, capsys, error
):
    """F-07 regression: an engine factory raising anything that
    is not a designed refusal must not escape main() as a
    traceback — in the boot context there is no traceback
    channel. It lands on the designed refusal window with the
    honest could-not-start line plus the error's own text, and
    exits 2, with nothing on stderr."""

    def factory():
        raise error

    code, labels = _run_main_with_factory(
        qapp, factory, "could not start on this machine"
    )
    assert code == bootui_app.EXIT_REFUSAL == 2
    assert labels, "the refusal window was shown"
    assert any(
        "Beerus could not start on this machine, so nothing "
        "will be wiped." in text
        for text in labels
    )
    assert any(str(error) in text for text in labels)
    assert capsys.readouterr().err == ""


def test_main_shows_a_plain_beerus_errors_own_message(qapp, capsys):
    """F-07: a plain BeerusError from the factory is a designed
    Beerus message — it is shown as its own text, not dressed
    in the could-not-start line for random failures."""

    def factory():
        raise BeerusError("A plain Beerus problem, stated plainly.")

    code, labels = _run_main_with_factory(
        qapp, factory, "A plain Beerus problem"
    )
    assert code == bootui_app.EXIT_REFUSAL == 2
    assert labels, "the refusal window was shown"
    assert any("stated plainly" in text for text in labels)
    assert not any(
        "could not start on this machine" in text for text in labels
    )
    assert capsys.readouterr().err == ""


def test_main_exits_76_when_no_graphical_session_can_start(capsys):
    def broken_app_factory(args):
        raise RuntimeError("no display")

    code = bootui_app.main(
        [], engine_factory=lambda: None, app_factory=broken_app_factory
    )
    assert code == bootui_app.EXIT_NO_GRAPHICAL == 76
    captured = capsys.readouterr()
    assert (
        "The graphical session could not start on this machine."
        in captured.err
    )


def test_main_runs_a_session_through_the_factory_seams(qapp, tmp_path):
    seen = {}

    class ExecSeam:
        """Stands in for the event loop; widgets still need the
        real QApplication behind it, which the factory built."""

        def __init__(self, real):
            self._real = real

        def exec(self):
            seen["windows"] = [
                widget
                for widget in QApplication.topLevelWidgets()
                if isinstance(widget, bootui_app.BootWindow)
            ]
            return 0

    def app_factory(args):
        real = QApplication.instance() or QApplication(args)
        return ExecSeam(real)

    def engine_factory():
        authority = GateAuthority()
        engine = WipeEngine(
            authority,
            FileExecutor("/nonexistent"),
            enumerator=lambda: [],
        )
        return engine, authority, str(tmp_path / "documents")

    code = bootui_app.main(
        [], engine_factory=engine_factory, app_factory=app_factory
    )
    assert code == bootui_app.EXIT_OK == 0
    assert seen["windows"], "the boot window was built and shown"
    seen["windows"][0].close()


# ------------------------------------------------------------------ launcher


def _stub(directory: Path, name: str, log: Path, code: int) -> None:
    script = directory / name
    script.write_text(
        "#!/bin/sh\n"
        f'echo "{name} $@" >> "{log}"\n'
        f"exit {code}\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def _run_launcher(tmp_path, *, startx_code, python_code):
    """Run the real launcher with stubbed startx/python3.

    startx_code=None means startx is absent from PATH entirely.
    Returns (CompletedProcess, log_text).
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    if startx_code is not None:
        _stub(bin_dir, "startx", log, startx_code)
    _stub(bin_dir, "python3", log, python_code)
    env = {"PATH": str(bin_dir)}
    proc = subprocess.run(
        ["/bin/sh", str(LAUNCHER)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return proc, log.read_text(encoding="utf-8") if log.exists() else ""


_NOTICE_LINES = (
    "The graphical session could not start on this machine.\n"
    "Beerus is continuing in its text console — the same "
    "confirmation steps, the same engine, the same job documents.\n"
)


def test_launcher_graphical_success_stays_graphical(tmp_path):
    proc, log = _run_launcher(tmp_path, startx_code=0, python_code=0)
    assert proc.returncode == 0
    assert "startx /usr/local/bin/beerus-ui" in log
    assert "python3" not in log  # the console stub never ran
    assert proc.stdout == ""


def test_launcher_graphical_refusal_is_final(tmp_path):
    proc, log = _run_launcher(tmp_path, startx_code=2, python_code=0)
    assert proc.returncode == 2
    assert "startx /usr/local/bin/beerus-ui" in log
    assert "python3" not in log  # no fallback repeats the refusal
    assert proc.stdout == ""


def test_launcher_falls_back_when_graphics_fail(tmp_path):
    proc, log = _run_launcher(tmp_path, startx_code=1, python_code=0)
    assert proc.returncode == 0
    assert proc.stdout == _NOTICE_LINES
    assert "python3 -m beerus" in log


def test_launcher_fallback_exit_code_is_the_consoles(tmp_path):
    proc, log = _run_launcher(tmp_path, startx_code=1, python_code=2)
    assert proc.returncode == 2
    assert proc.stdout == _NOTICE_LINES
    assert "python3 -m beerus" in log


def test_launcher_falls_back_when_startx_is_missing(tmp_path):
    proc, log = _run_launcher(tmp_path, startx_code=None, python_code=0)
    assert proc.returncode == 0
    assert proc.stdout == _NOTICE_LINES
    assert "startx" not in log
    assert "python3 -m beerus" in log
