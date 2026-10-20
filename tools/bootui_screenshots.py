"""Render every main state of the Beerus boot UI for review.

Development/QA tool — not part of the shipped app. It builds
BootWindow (and the startup refusal window) against a scripted
fake environment — synthetic drives, a file-backed target, a
stub verifier, documents in a temp dir — walks the real window
through its states offscreen, and saves a PNG of each settled
state at 1024x768. The screenshots are the Phase 4
rendered-state verification; all data in them is synthetic, per
the house rules. Nothing here arms anything: the executors are
the file-backed one and scripted fakes, on temp files only.

The page fade is disabled through the window's own seam
(``fade_enabled``), so every render is a settled state, never a
mid-fade frame.

Usage:
    python tools/bootui_screenshots.py <output-dir>
"""

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from PySide6.QtCore import QElapsedTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from beerus.engine import WipeEngine  # noqa: E402
from beerus.executor import ExecutionReport, FileExecutor  # noqa: E402
from beerus.gates import GateAuthority  # noqa: E402
from beerus.progress import EventKind, ProgressEvent  # noqa: E402
from beerus.verify import FileSurfaceReader, WipeVerifier, unverified_result  # noqa: E402
from beerus.bootui.app import BootWindow, RefusalWindow  # noqa: E402
from conftest import (  # noqa: E402
    MIB,
    make_boot_drive,
    make_drive,
    make_nvme,
    make_unidentifiable,
)

SIZE = 8 * MIB
OPERATOR = "Skyler Test"


def settle(app, ms=300):
    timer = QElapsedTimer()
    timer.start()
    while timer.elapsed() < ms:
        app.processEvents()
        time.sleep(0.01)


def grab(app, widget, out: Path):
    settle(app)
    widget.grab().save(str(out))
    print(f"wrote {out.name}")
    return out


def wait_for_page(app, window, page, timeout_ms=15000):
    waited = 0
    while window.stack.currentWidget() is not page:
        app.processEvents()
        time.sleep(0.05)
        waited += 50
        if waited > timeout_ms:
            raise AssertionError("timed out waiting for the page")


class StubVerifier:
    """A genuine NOT_VERIFIED, minted by the verify module."""

    def verify(self, drive, plan, emit=None, actual_method_label=None):
        return unverified_result(
            drive,
            plan,
            "Verification could not be completed on this synthetic drive.",
            actual_method_label=actual_method_label,
        )


class PausingExecutor:
    """Emits one real-percent event, then waits for release."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def execute(self, drive, plan, emit):
        emit(
            ProgressEvent(
                EventKind.PROGRESS,
                "Overwriting the drive surface.",
                "Overwrite",
                SIZE // 2,
                SIZE,
            )
        )
        self.entered.set()
        self.release.wait(timeout=60)
        return ExecutionReport(
            plan.method_label, SIZE, ("Overwrite",), "Synthetic run finished."
        )


class OSErrorExecutor:
    def execute(self, drive, plan, emit):
        raise OSError(28, "No space left on device")


def make_window(engine, authority, documents_dir):
    window = BootWindow(engine, authority, documents_dir)
    window.fade_enabled = False
    window.resize(1024, 768)
    window.show()
    return window


def to_drives(app, window):
    window.operator_edit.setText(OPERATOR)
    window.begin_button.click()
    settle(app, 100)
    assert window.stack.currentWidget() is window.page_drives


def walk_to_final(window, serial, row=0):
    window.drive_list.setCurrentRow(row)
    window.start_button.click()
    assert window.stack.currentWidget() is window.page_identity
    window.serial_edit.setText(serial)
    window.continue_button.click()
    assert window.stack.currentWidget() is window.page_scope
    window.ack_button.click()
    assert window.stack.currentWidget() is window.page_final
    window.phrase_edit.setText(f"ERASE {serial}")


def run_through(app, window, serial, row=0):
    walk_to_final(window, serial, row)
    window.erase_button.click()
    wait_for_page(app, window, window.page_result)


def main(argv):
    out_dir = Path(argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    app = QApplication([])

    root = Path(tempfile.mkdtemp(prefix="beerus-shots-"))

    # --- Window A: welcome, drive list, the three gate pages,
    #     and the wrong-serial message page.
    target_a = root / "a.img"
    target_a.write_bytes(b"\xaa" * SIZE)
    docs_a = root / "docs-a"
    sata = make_drive(device_path=str(target_a))
    drives = [
        sata,
        make_nvme(),
        make_boot_drive(device_path="/dev/sdz", raw_name="sdz"),
        make_unidentifiable(device_path="/dev/sdd", raw_name="sdd"),
    ]
    authority = GateAuthority()
    verifier = WipeVerifier(
        surface_factory=lambda d: FileSurfaceReader(str(target_a))
    )
    engine = WipeEngine(
        authority,
        FileExecutor(str(target_a)),
        enumerator=lambda: list(drives),
        verifier=verifier,
    )
    window = make_window(engine, authority, docs_a)
    grab(app, window, out_dir / "01-welcome.png")
    to_drives(app, window)
    grab(app, window, out_dir / "02-drive-list.png")
    window.drive_list.setCurrentRow(0)
    window.start_button.click()
    settle(app, 100)
    grab(app, window, out_dir / "03-identity.png")
    window.serial_edit.setText(sata.serial)
    window.continue_button.click()
    settle(app, 100)
    grab(app, window, out_dir / "04-scope.png")
    window.ack_button.click()
    settle(app, 100)
    grab(app, window, out_dir / "05-final.png")
    # Back to the list, then the wrong-serial message page.
    window._cancel_gates()
    settle(app, 100)
    window.drive_list.setCurrentRow(0)
    window.start_button.click()
    window.serial_edit.setText("NOT-THE-SERIAL")
    window.continue_button.click()
    settle(app, 100)
    assert window.stack.currentWidget() is window.page_message
    grab(app, window, out_dir / "10-message-wrong-serial.png")
    window.close()

    # --- Window B: the running page, held on a real 50% event.
    executor = PausingExecutor()
    authority = GateAuthority()
    engine = WipeEngine(
        authority,
        executor,
        enumerator=lambda: [sata],
        verifier=StubVerifier(),
    )
    window = make_window(engine, authority, root / "docs-b")
    to_drives(app, window)
    walk_to_final(window, sata.serial)
    window.erase_button.click()
    assert executor.entered.wait(timeout=10)
    settle(app, 200)
    assert window.stack.currentWidget() is window.page_running
    grab(app, window, out_dir / "06-running.png")
    executor.release.set()
    wait_for_page(app, window, window.page_result)
    window.close()

    # --- Window C: a verified pass and its certificate.
    target_c = root / "c.img"
    target_c.write_bytes(b"\xaa" * SIZE)
    authority = GateAuthority()
    verifier = WipeVerifier(
        surface_factory=lambda d: FileSurfaceReader(str(target_c))
    )
    engine = WipeEngine(
        authority,
        FileExecutor(str(target_c)),
        enumerator=lambda: [make_drive(device_path=str(target_c))],
        verifier=verifier,
    )
    window = make_window(engine, authority, root / "docs-c")
    to_drives(app, window)
    run_through(app, window, make_drive().serial)
    grab(app, window, out_dir / "07-result-pass.png")
    window.close()

    # --- Window D: verification FAIL (the surface read back is
    #     a different, untouched file) and the failure report.
    target_d = root / "d.img"
    target_d.write_bytes(b"\xaa" * SIZE)
    untouched = root / "untouched.img"
    untouched.write_bytes(b"\xaa" * SIZE)
    authority = GateAuthority()
    verifier = WipeVerifier(
        surface_factory=lambda d: FileSurfaceReader(str(untouched))
    )
    engine = WipeEngine(
        authority,
        FileExecutor(str(target_d)),
        enumerator=lambda: [make_drive(device_path=str(target_d))],
        verifier=verifier,
    )
    window = make_window(engine, authority, root / "docs-d")
    to_drives(app, window)
    run_through(app, window, make_drive().serial)
    grab(app, window, out_dir / "08-result-fail.png")
    window.close()

    # --- Window E: NOT VERIFIED, via the stub verifier.
    target_e = root / "e.img"
    target_e.write_bytes(b"\xaa" * SIZE)
    authority = GateAuthority()
    engine = WipeEngine(
        authority,
        FileExecutor(str(target_e)),
        enumerator=lambda: [make_drive(device_path=str(target_e))],
        verifier=StubVerifier(),
    )
    window = make_window(engine, authority, root / "docs-e")
    to_drives(app, window)
    run_through(app, window, make_drive().serial)
    grab(app, window, out_dir / "09-result-not-verified.png")
    window.close()

    # --- The startup refusal window (the builder said no).
    refusal = RefusalWindow(
        "This copy of Beerus is not running from the Live Boot "
        "image, so it will not arm the drive wiper. The boot "
        "console only runs on the Live Boot key itself."
    )
    refusal.resize(1024, 768)
    refusal.show()
    grab(app, refusal, out_dir / "11-refusal-startup.png")
    refusal.close()

    # --- Window F: the document could not be saved.
    target_f = root / "f.img"
    target_f.write_bytes(b"\xaa" * SIZE)
    blocker = root / "blocker"
    blocker.write_bytes(b"not a directory")
    authority = GateAuthority()
    verifier = WipeVerifier(
        surface_factory=lambda d: FileSurfaceReader(str(target_f))
    )
    engine = WipeEngine(
        authority,
        FileExecutor(str(target_f)),
        enumerator=lambda: [make_drive(device_path=str(target_f))],
        verifier=verifier,
    )
    window = make_window(engine, authority, blocker / "docs")
    to_drives(app, window)
    run_through(app, window, make_drive().serial)
    grab(app, window, out_dir / "12-result-document-failed.png")
    window.close()

    # --- Window G: the wipe stopped early (an OS error).
    target_g = root / "g.img"
    target_g.write_bytes(b"\xaa" * SIZE)
    authority = GateAuthority()
    engine = WipeEngine(
        authority,
        OSErrorExecutor(),
        enumerator=lambda: [make_drive(device_path=str(target_g))],
    )
    window = make_window(engine, authority, root / "docs-g")
    to_drives(app, window)
    run_through(app, window, make_drive().serial)
    grab(app, window, out_dir / "13-result-unfinished-wipe.png")
    window.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
