"""GUI tests for the Beerus boot UI, run offscreen.

The full gate flow is driven through the real widgets against
synthetic targets, exactly like the console and portable
suites: a small regular file stands in for a drive, the
engine's FileExecutor and FileSurfaceReader do the real
overwrite and the real read-back verification, and the real
certificate generator issues the document. Nothing here can
touch a real drive — the executor under test only ever writes
to regular files under tmp_path, and the boot UI builds no
executor of its own at all.
"""

from __future__ import annotations

import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from beerus.engine import WipeEngine  # noqa: E402
from beerus.errors import BeerusError  # noqa: E402
from beerus.executor import ExecutionReport, FileExecutor, RealExecutor  # noqa: E402
from beerus.gates import GateAuthority  # noqa: E402
from beerus.progress import EventKind, ProgressEvent  # noqa: E402
from beerus.verify import (  # noqa: E402
    FileSurfaceReader,
    WipeVerifier,
    unverified_result,
)
from beerus.bootui.app import BootWindow  # noqa: E402
from conftest import (  # noqa: E402
    MIB,
    make_boot_drive,
    make_drive,
    make_nvme,
    make_unidentifiable,
)

SIZE = 8 * MIB
SENTINEL = b"\xaa"


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def wait_for_page(window, page, timeout_ms=15000):
    waited = 0
    while window.stack.currentWidget() is not page:
        QTest.qWait(50)
        waited += 50
        if waited > timeout_ms:
            raise AssertionError("timed out waiting for the page")


@pytest.fixture()
def device_file(tmp_path):
    path = tmp_path / "fake-drive.img"
    path.write_bytes(SENTINEL * SIZE)
    return path


@pytest.fixture()
def documents_dir(tmp_path):
    return tmp_path / "documents"


def make_window(
    drives,
    executor,
    documents_dir,
    authority=None,
    verifier=None,
    enumerator=None,
):
    if authority is None:
        authority = GateAuthority()
    engine = WipeEngine(
        authority,
        executor,
        enumerator=enumerator or (lambda: list(drives)),
        verifier=verifier,
    )
    window = BootWindow(engine, authority, documents_dir)
    window.fade_enabled = False
    return window


def verified_window(device_file, documents_dir, drives=None, **kwargs):
    if drives is None:
        drives = [make_drive(device_path=str(device_file))]
    verifier = WipeVerifier(
        surface_factory=lambda drive: FileSurfaceReader(str(device_file))
    )
    return make_window(
        drives,
        FileExecutor(str(device_file)),
        documents_dir,
        verifier=verifier,
        **kwargs,
    )


def open_drives(window, operator="Skyler Test"):
    """Through the welcome page, onto the drive list."""
    window.operator_edit.setText(operator)
    window.begin_button.click()
    assert window.stack.currentWidget() is window.page_drives


def begin_gates(window, row=0):
    """Select a drive row and press Start the confirmation."""
    window.drive_list.setCurrentRow(row)
    assert window.start_button.isEnabled()
    window.start_button.click()


def walk_gates(window, serial):
    begin_gates(window)
    assert window.stack.currentWidget() is window.page_identity
    window.serial_edit.setText(serial)
    window.continue_button.click()
    assert window.stack.currentWidget() is window.page_scope
    window.ack_button.click()
    assert window.stack.currentWidget() is window.page_final
    window.phrase_edit.setText(f"ERASE {serial}")
    window.erase_button.click()


def click_button_on(page, text):
    for button in page.findChildren(QPushButton):
        if button.text() == text:
            button.click()
            return
    raise AssertionError(f"no button {text!r} on the page")


# ------------------------------------------------------------------ welcome


def test_begin_stays_disabled_until_an_operator_name_is_typed(
    qapp, device_file, documents_dir
):
    window = verified_window(device_file, documents_dir)
    assert window.stack.currentWidget() is window.page_welcome
    from PySide6.QtWidgets import QLabel

    texts = [
        label.text() for label in window.page_welcome.findChildren(QLabel)
    ]
    assert "Graphical session" in texts
    assert any(
        "kept in memory only" in text and str(documents_dir) in text
        for text in texts
    )
    assert not window.begin_button.isEnabled()
    window.operator_edit.setText("   ")
    assert not window.begin_button.isEnabled()
    window.operator_edit.setText("Skyler Test")
    assert window.begin_button.isEnabled()


def test_begin_handler_rechecks_the_operator_name(
    qapp, device_file, documents_dir
):
    """F-09 regression: the Begin gate is re-checked inside the
    handler, not only in the button's enabled state. Emitting
    the button's signal bypasses enablement (the button is
    disabled with a blank name); the handler itself must still
    refuse to advance — a run with no operator could wipe and
    then have no name for its job document."""
    window = verified_window(device_file, documents_dir)
    assert window.stack.currentWidget() is window.page_welcome
    assert not window.begin_button.isEnabled()
    window.begin_button.clicked.emit()
    assert window.stack.currentWidget() is window.page_welcome
    assert window._session is None
    # A name typed and then cleared is blank again at the handler.
    window.operator_edit.setText("Skyler Test")
    window.operator_edit.setText("   ")
    window.begin_button.clicked.emit()
    assert window.stack.currentWidget() is window.page_welcome
    assert window._session is None
    # The direct handler path refuses too.
    window._show_drives()
    assert window.stack.currentWidget() is window.page_welcome
    # And with a real name, the same handler advances as before.
    window.operator_edit.setText("Skyler Test")
    window.begin_button.clicked.emit()
    assert window.stack.currentWidget() is window.page_drives


def test_gate_start_rechecks_the_operator_name(
    qapp, device_file, documents_dir
):
    """F-09, second seam: a name cleared after Begin (reachable
    programmatically) must not let a confirmation start."""
    window = verified_window(device_file, documents_dir)
    open_drives(window)
    window.operator_edit.setText("")
    window.drive_list.setCurrentRow(0)
    window.start_button.clicked.emit()
    assert window.stack.currentWidget() is window.page_drives
    assert window._session is None
    assert device_file.read_bytes() == SENTINEL * SIZE


# ------------------------------------------------------------------ drives


def test_drive_rows_show_ineligible_reasons_and_start_stays_off(
    qapp, device_file, documents_dir
):
    target = make_drive(device_path=str(device_file))
    boot = make_boot_drive(device_path="/dev/sdz", raw_name="sdz")
    mystery = make_unidentifiable(device_path="/dev/sdd", raw_name="sdd")
    window = verified_window(
        device_file, documents_dir, drives=[target, boot, mystery]
    )
    open_drives(window)
    texts = [window.drive_list.item(i).text() for i in range(3)]
    assert "This drive cannot be wiped:" in texts[1]
    assert "booted from" in texts[1]
    assert "could not identify" in texts[2]
    assert window.no_eligible_note.isHidden()
    for i in (1, 2):
        item = window.drive_list.item(i)
        assert not (item.flags() & Qt.ItemFlag.ItemIsEnabled)
    # Even a programmatic landing on a greyed row must not light
    # the Start button (the portable's N3 lesson).
    window.drive_list.setCurrentRow(1)
    assert not window.start_button.isEnabled()
    window.drive_list.setCurrentRow(0)
    assert window.start_button.isEnabled()


def test_no_eligible_drives_shows_the_designed_note(
    qapp, device_file, documents_dir
):
    boot = make_boot_drive(device_path="/dev/sdz", raw_name="sdz")
    window = verified_window(device_file, documents_dir, drives=[boot])
    open_drives(window)
    assert not window.no_eligible_note.isHidden()
    assert "No drive on this machine can be wiped right now" in (
        window.no_eligible_note.text()
    )
    assert not window.start_button.isEnabled()


def test_drive_list_failure_is_a_designed_message_with_a_real_retry(
    qapp, documents_dir
):
    state = {"fail": True}

    def enumerator():
        if state["fail"]:
            raise BeerusError("Beerus could not read the list of drives.")
        return [make_drive()]

    window = make_window(
        [], FileExecutor("/nonexistent"), documents_dir,
        enumerator=enumerator,
    )
    window.operator_edit.setText("Skyler Test")
    window.begin_button.click()
    assert window.stack.currentWidget() is window.page_message
    assert "could not read the list of drives" in window.message_label.text()
    assert (
        "Beerus cannot continue without a drive list."
        in window.message_label.text()
    )
    assert not window.try_again_button.isHidden()
    # The retry genuinely re-runs the assessment.
    state["fail"] = False
    window.try_again_button.click()
    assert window.stack.currentWidget() is window.page_drives
    assert window.drive_list.count() == 1


# ------------------------------------------------------------------ happy path


def test_full_flow_wipes_verifies_and_certifies(
    qapp, device_file, documents_dir
):
    window = verified_window(device_file, documents_dir)
    drive = make_drive()
    open_drives(window)
    walk_gates(window, drive.serial)
    wait_for_page(window, window.page_result)

    assert device_file.read_bytes() == b"\x00" * SIZE
    assert window.result_method.text() == (
        "Wipe finished: Multi-pass overwrite (4 passes)."
    )
    assert window.verdict_label.text().startswith("Verification: PASSED")
    assert window.verdict_label.objectName() == "verdictPass"
    assert "Certificate of destruction saved:" in window.document_label.text()
    assert "stored in memory only" in window.document_label.text()
    certificates = list(documents_dir.glob("beerus-certificate-*.html"))
    assert len(certificates) == 1
    assert drive.serial in certificates[0].read_text(encoding="utf-8")


# ------------------------------------------------------------------ gate refusals


def test_wrong_serial_lands_on_the_message_page_and_drops_the_session(
    qapp, device_file, documents_dir
):
    window = verified_window(device_file, documents_dir)
    open_drives(window)
    begin_gates(window)
    window.serial_edit.setText("NOT-THE-SERIAL")
    window.continue_button.click()
    assert window.stack.currentWidget() is window.page_message
    assert "does not match this drive" in window.message_label.text()
    assert window._session is None
    assert device_file.read_bytes() == SENTINEL * SIZE


def test_wrong_final_phrase_lands_on_the_message_page(
    qapp, device_file, documents_dir
):
    window = verified_window(device_file, documents_dir)
    drive = make_drive()
    open_drives(window)
    begin_gates(window)
    window.serial_edit.setText(drive.serial)
    window.continue_button.click()
    window.ack_button.click()
    window.phrase_edit.setText("ERASE THE WRONG THING")
    window.erase_button.click()
    assert window.stack.currentWidget() is window.page_message
    assert "not the exact confirmation phrase" in window.message_label.text()
    assert window._session is None
    assert device_file.read_bytes() == SENTINEL * SIZE


def test_cancel_at_identity_returns_to_the_drive_list(
    qapp, device_file, documents_dir
):
    window = verified_window(device_file, documents_dir)
    open_drives(window)
    begin_gates(window)
    assert window.stack.currentWidget() is window.page_identity
    click_button_on(window.page_identity, "Cancel — back to the drive list")
    assert window.stack.currentWidget() is window.page_drives
    assert window._session is None
    assert device_file.read_bytes() == SENTINEL * SIZE


def test_cancel_at_final_returns_to_the_drive_list(
    qapp, device_file, documents_dir
):
    window = verified_window(device_file, documents_dir)
    drive = make_drive()
    open_drives(window)
    begin_gates(window)
    window.serial_edit.setText(drive.serial)
    window.continue_button.click()
    window.ack_button.click()
    assert window.stack.currentWidget() is window.page_final
    click_button_on(window.page_final, "Cancel — back to the drive list")
    assert window.stack.currentWidget() is window.page_drives
    assert window._session is None
    assert device_file.read_bytes() == SENTINEL * SIZE


def test_refresh_drops_an_in_progress_session(
    qapp, device_file, documents_dir
):
    """F-10 regression: refreshing the drive list mid-session
    drops the in-progress gate session — the operator restarts
    the confirmation against the fresh assessment. The window
    must not hold a resumable session built on a stale list."""
    window = verified_window(device_file, documents_dir)
    drive = make_drive()
    open_drives(window)
    begin_gates(window)
    assert window.stack.currentWidget() is window.page_identity
    assert window._session is not None
    window.serial_edit.setText(drive.serial)
    # Refresh, as the drives page's Refresh button invokes it.
    window._show_drives()
    assert window.stack.currentWidget() is window.page_drives
    assert window._session is None
    assert window._drive is None
    assert window._plan is None
    assert device_file.read_bytes() == SENTINEL * SIZE
    # The flow restarts cleanly from the list with a fresh session.
    begin_gates(window)
    assert window.stack.currentWidget() is window.page_identity
    assert window._session is not None


def test_an_expired_session_lands_on_the_message_page(
    qapp, device_file, documents_dir
):
    clock = [1000.0]
    authority = GateAuthority(token_ttl_seconds=120.0, clock=lambda: clock[0])
    window = verified_window(
        device_file, documents_dir, authority=authority
    )
    drive = make_drive()
    open_drives(window)
    begin_gates(window)
    assert window.stack.currentWidget() is window.page_identity
    clock[0] += 3600.0  # the confirmation sat too long
    window.serial_edit.setText(drive.serial)
    window.continue_button.click()
    assert window.stack.currentWidget() is window.page_message
    assert "expired" in window.message_label.text()
    assert device_file.read_bytes() == SENTINEL * SIZE


def test_the_disarmed_executor_refuses_through_the_window(
    qapp, device_file, documents_dir
):
    # The shipped posture: a RealExecutor in its default
    # disarmed construction refuses at wipe time, in plain
    # language, with the target untouched — through this window
    # exactly as through the portable's.
    drive = make_drive(device_path=str(device_file))
    window = make_window([drive], RealExecutor(), documents_dir)
    open_drives(window)
    walk_gates(window, drive.serial)
    wait_for_page(window, window.page_message)
    assert "not armed" in window.message_label.text()
    assert device_file.read_bytes() == SENTINEL * SIZE


# ------------------------------------------------------------------ running


class BlockingExecutor:
    """Emits one event, then waits until the test releases it."""

    def __init__(self, event):
        self._event = event
        self.entered = threading.Event()
        self.release = threading.Event()

    def execute(self, drive, plan, emit):
        emit(self._event)
        self.entered.set()
        self.release.wait(timeout=30)
        return ExecutionReport(
            plan.method_label, 0, ("step",), "Synthetic run finished."
        )


class StubVerifier:
    """Returns a genuine NOT_VERIFIED result, minted by the
    verify module itself (the engine tests' construction)."""

    def verify(self, drive, plan, emit=None, actual_method_label=None):
        return unverified_result(
            drive,
            plan,
            "Verification could not be completed on this synthetic drive.",
            actual_method_label=actual_method_label,
        )


def test_nvme_progress_without_a_percentage_shows_no_percentage(
    qapp, tmp_path, documents_dir
):
    nvme = make_nvme()
    event = ProgressEvent(
        EventKind.PROGRESS,
        "The drive's own firmware is erasing it.",
        "NVMe Sanitize",
        0,
        0,  # no total: percent is None, by the event's own law
    )
    executor = BlockingExecutor(event)
    authority = GateAuthority()
    engine = WipeEngine(
        authority,
        executor,
        enumerator=lambda: [nvme],
        verifier=StubVerifier(),
    )
    window = BootWindow(engine, authority, documents_dir)
    window.fade_enabled = False
    open_drives(window)
    walk_gates(window, nvme.serial)
    assert executor.entered.wait(timeout=10)
    QTest.qWait(100)
    assert window.stack.currentWidget() is window.page_running
    assert window.progress_bar.minimum() == 0
    assert window.progress_bar.maximum() == 0  # busy, not a number
    assert window.progress_bar.format() == ""
    assert "%" not in window.running_status.text()
    assert "%" not in window.progress_bar.format()
    assert "firmware is erasing" in window.running_status.text()
    executor.release.set()
    wait_for_page(window, window.page_result)
    # The stub verifier's genuine NOT_VERIFIED, stated as such,
    # with a failure report — never a certificate.
    assert window.verdict_label.text().startswith(
        "Verification: NOT VERIFIED"
    )
    assert window.verdict_label.objectName() == "verdictBad"
    assert "failure report was saved instead" in window.document_label.text()
    reports = list(documents_dir.glob("beerus-failure-report-*.html"))
    assert len(reports) == 1


def test_close_is_refused_while_a_wipe_runs(qapp, device_file, documents_dir):
    event = ProgressEvent(
        EventKind.STEP_STARTED, "Starting: synthetic step", "step", 0, SIZE
    )
    executor = BlockingExecutor(event)
    drive = make_drive(device_path=str(device_file))
    authority = GateAuthority()
    engine = WipeEngine(
        authority, executor, enumerator=lambda: [drive]
    )
    window = BootWindow(engine, authority, documents_dir)
    window.fade_enabled = False
    window.show()
    open_drives(window)
    walk_gates(window, drive.serial)
    assert executor.entered.wait(timeout=10)
    assert window.stack.currentWidget() is window.page_running
    window.close()
    QTest.qWait(50)
    assert window.isVisible()  # the close was ignored
    executor.release.set()
    wait_for_page(window, window.page_result)
    window.close()
    QTest.qWait(50)
    assert not window.isVisible()


# ------------------------------------------------------------------ result states


def test_verification_failure_earns_a_failure_report(
    qapp, tmp_path, device_file, documents_dir
):
    # The surface the verifier reads back is a different file,
    # still full of data: the wipe happened, verification
    # honestly fails, and a failure report is issued.
    tampered = tmp_path / "tampered.img"
    tampered.write_bytes(SENTINEL * SIZE)
    verifier = WipeVerifier(
        surface_factory=lambda drive: FileSurfaceReader(str(tampered))
    )
    window = make_window(
        [make_drive(device_path=str(device_file))],
        FileExecutor(str(device_file)),
        documents_dir,
        verifier=verifier,
    )
    open_drives(window)
    walk_gates(window, make_drive().serial)
    wait_for_page(window, window.page_result)
    assert device_file.read_bytes() == b"\x00" * SIZE
    assert window.verdict_label.text().startswith("Verification: FAILED")
    assert window.verdict_label.objectName() == "verdictBad"
    assert "did not earn a certificate" in window.document_label.text()
    reports = list(documents_dir.glob("beerus-failure-report-*.html"))
    assert len(reports) == 1
    assert not list(documents_dir.glob("beerus-certificate-*.html"))


def test_not_verified_state_issues_a_failure_report(
    qapp, device_file, documents_dir
):
    window = make_window(
        [make_drive(device_path=str(device_file))],
        FileExecutor(str(device_file)),
        documents_dir,
        verifier=StubVerifier(),
    )
    open_drives(window)
    walk_gates(window, make_drive().serial)
    wait_for_page(window, window.page_result)
    assert window.verdict_label.text().startswith(
        "Verification: NOT VERIFIED"
    )
    assert window.verdict_label.objectName() == "verdictBad"
    assert "failure report was saved instead" in window.document_label.text()
    assert list(documents_dir.glob("beerus-failure-report-*.html"))


def test_a_wipe_with_no_verifier_issues_no_document(
    qapp, device_file, documents_dir
):
    window = make_window(
        [make_drive(device_path=str(device_file))],
        FileExecutor(str(device_file)),
        documents_dir,
    )
    open_drives(window)
    walk_gates(window, make_drive().serial)
    wait_for_page(window, window.page_result)
    assert "No document was issued: without verification" in (
        window.verdict_label.text()
    )
    assert not list(documents_dir.glob("*.html"))


def test_document_save_failure_keeps_the_verdict_and_says_so(
    qapp, tmp_path, device_file
):
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"not a directory")
    documents_dir = blocker / "docs"
    window = verified_window(device_file, documents_dir)
    open_drives(window)
    walk_gates(window, make_drive().serial)
    wait_for_page(window, window.page_result)
    assert window.verdict_label.text().startswith("Verification: PASSED")
    assert "could not be saved" in window.document_label.text()
    assert "cannot be recreated" in window.document_label.text()
    assert str(documents_dir) in window.document_label.text()
    assert device_file.read_bytes() == b"\x00" * SIZE


class OSErrorExecutor:
    def execute(self, drive, plan, emit):
        raise OSError(28, "No space left on device")


class ExplodingExecutor:
    def execute(self, drive, plan, emit):
        raise RuntimeError("synthetic explosion")


def test_wipe_os_error_is_a_designed_not_sanitized_result(
    qapp, device_file, documents_dir
):
    window = make_window(
        [make_drive(device_path=str(device_file))],
        OSErrorExecutor(),
        documents_dir,
    )
    open_drives(window)
    walk_gates(window, make_drive().serial)
    wait_for_page(window, window.page_result)
    assert window.result_method.text() == (
        "The wipe stopped before it finished: No space left on device."
    )
    assert "NOT sanitized" in window.verdict_label.text()
    assert device_file.read_bytes() == SENTINEL * SIZE


def test_wipe_unexpected_error_is_a_designed_not_sanitized_result(
    qapp, device_file, documents_dir
):
    window = make_window(
        [make_drive(device_path=str(device_file))],
        ExplodingExecutor(),
        documents_dir,
    )
    open_drives(window)
    walk_gates(window, make_drive().serial)
    wait_for_page(window, window.page_result)
    assert window.result_method.text() == (
        "The wipe stopped before it finished, for a reason "
        "Beerus could not read."
    )
    assert "NOT sanitized" in window.verdict_label.text()
    assert device_file.read_bytes() == SENTINEL * SIZE
