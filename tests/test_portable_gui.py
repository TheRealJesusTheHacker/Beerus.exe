"""GUI tests for Beerus Portable, run offscreen.

The full gate flow is driven through the real widgets against
synthetic targets: a small regular file stands in for a drive,
the engine's FileExecutor and FileSurfaceReader do the real
overwrite and the real read-back verification, and the real
certificate generator issues the document. Nothing here can
touch a real drive — the executor under test only ever writes
to regular files, and the shipped-state test proves the
disarmed RealExecutor refuses through this same window.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from beerus.engine import WipeEngine  # noqa: E402
from beerus.executor import FileExecutor, RealExecutor  # noqa: E402
from beerus.gates import GateAuthority  # noqa: E402
from beerus.inventory import DriveClass  # noqa: E402
from beerus.verify import FileSurfaceReader, WipeVerifier  # noqa: E402
from beerus_portable.app import MainWindow  # noqa: E402
from beerus_portable.windows_inventory import (  # noqa: E402
    OWN_DRIVE_MESSAGE,
    SYSTEM_DRIVE_MESSAGE,
    PortableDriveContext,
)
from conftest import (  # noqa: E402
    make_boot_drive,
    make_drive,
    make_unidentifiable,
)

MIB = 1024 * 1024
SIZE = 8 * MIB


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
            raise AssertionError(
                "timed out waiting for page; current page text: "
                + window.stack.currentWidget().objectName()
            )


@pytest.fixture()
def device_file(tmp_path):
    path = tmp_path / "fake-drive.img"
    path.write_bytes(b"\xaa" * SIZE)
    return path


@pytest.fixture()
def documents_dir(tmp_path):
    return tmp_path / "certificates"


def make_window(drives, executor, documents_dir, context=None, verifier=None):
    authority = GateAuthority()
    engine = WipeEngine(
        authority,
        executor,
        enumerator=lambda: list(drives),
        verifier=verifier,
    )
    return MainWindow(
        engine,
        authority,
        documents_dir=documents_dir,
        context_provider=(lambda: context) if context is not None else None,
    )


def verified_window(device_file, documents_dir, drives=None):
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
    )


def select_first_drive(window):
    window.operator_edit.setText("Skyler Test")
    window.drive_list.setCurrentRow(0)
    assert window.start_button.isEnabled()


def walk_gates(window, serial):
    window.start_button.click()
    assert window.stack.currentWidget() is window.page_identity
    window.serial_edit.setText(serial)
    window.continue_button.click()
    assert window.stack.currentWidget() is window.page_scope
    window.ack_button.click()
    assert window.stack.currentWidget() is window.page_final
    window.phrase_edit.setText(f"ERASE {serial}")
    window.erase_button.click()


def test_full_flow_wipes_verifies_and_certifies(
    qapp, device_file, documents_dir
):
    window = verified_window(device_file, documents_dir)
    drive = make_drive()
    assert window.drive_list.count() == 1
    select_first_drive(window)
    walk_gates(window, drive.serial)
    wait_for_page(window, window.page_result)

    # The data itself was destroyed: every byte overwritten.
    assert device_file.read_bytes() == b"\x00" * SIZE
    assert window.verdict_label.text() == "VERIFICATION: PASSED"
    assert "full drive surface" in window.verification_view.toPlainText()
    assert "Certificate of destruction saved" in window.cert_label.text()

    documents = list(documents_dir.glob("*.html"))
    assert len(documents) == 1
    html = documents[0].read_text(encoding="utf-8")
    assert "Certificate of Destruction" in html
    assert drive.serial in html
    assert "Skyler Test" in html


def test_blocked_drives_are_shown_greyed_with_reasons(
    qapp, device_file, documents_dir
):
    target = make_drive(device_path=str(device_file))
    system = make_drive(
        device_path="\\\\.\\PhysicalDrive0",
        raw_name="PhysicalDrive0",
        model="Samsung SSD 860 EVO 500GB",
        serial="S3Z8NB0K123456B",
        interface="SATA",
        drive_class=DriveClass.SATA_SSD,
        is_boot=True,
    )
    own = make_drive(
        device_path="\\\\.\\PhysicalDrive1",
        raw_name="PhysicalDrive1",
        model="SanDisk Ultra USB",
        serial="4C530001230987",
        interface="USB",
        drive_class=DriveClass.USB,
        is_boot=True,
    )
    mystery = make_unidentifiable(device_path="\\\\.\\PhysicalDrive3")
    context = PortableDriveContext(
        system_fingerprints=frozenset({system.fingerprint}),
        own_fingerprints=frozenset({own.fingerprint}),
    )
    window = make_window(
        [system, own, mystery, target],
        FileExecutor(str(device_file)),
        documents_dir,
        context=context,
    )
    texts = [
        window.drive_list.item(i).text() for i in range(4)
    ]
    assert SYSTEM_DRIVE_MESSAGE in texts[0]
    assert OWN_DRIVE_MESSAGE in texts[1]
    assert "could not identify" in texts[2]
    for i in range(3):
        item = window.drive_list.item(i)
        assert not (item.flags() & Qt.ItemFlag.ItemIsEnabled)
    assert window.drive_list.item(3).flags() & Qt.ItemFlag.ItemIsEnabled


def test_boot_drive_uses_engine_wording_without_portable_context(
    qapp, documents_dir
):
    window = make_window(
        [make_boot_drive()], FileExecutor("/nonexistent"), documents_dir
    )
    item = window.drive_list.item(0)
    assert "booted from" in item.text()
    assert not (item.flags() & Qt.ItemFlag.ItemIsEnabled)


def test_wrong_serial_cancels_and_wipes_nothing(
    qapp, device_file, documents_dir
):
    window = verified_window(device_file, documents_dir)
    select_first_drive(window)
    window.start_button.click()
    window.serial_edit.setText("NOT-THE-SERIAL")
    window.continue_button.click()
    assert window.stack.currentWidget() is window.page_message
    assert "does not match" in window.message_label.text()
    assert device_file.read_bytes() == b"\xaa" * SIZE


def test_wrong_final_phrase_cancels_and_wipes_nothing(
    qapp, device_file, documents_dir
):
    window = verified_window(device_file, documents_dir)
    drive = make_drive()
    select_first_drive(window)
    window.start_button.click()
    window.serial_edit.setText(drive.serial)
    window.continue_button.click()
    window.ack_button.click()
    window.phrase_edit.setText("ERASE THE WRONG THING")
    window.erase_button.click()
    assert window.stack.currentWidget() is window.page_message
    assert "exact confirmation phrase" in window.message_label.text()
    assert device_file.read_bytes() == b"\xaa" * SIZE


def test_shipped_disarmed_state_refuses_through_the_window(
    qapp, device_file, documents_dir
):
    # Exactly the wiring the launcher ships: RealExecutor in its
    # default disarmed construction. The full gate flow completes
    # — and the wipe is refused, in plain language, with the
    # target untouched.
    drive = make_drive(device_path=str(device_file))
    window = make_window([drive], RealExecutor(), documents_dir)
    select_first_drive(window)
    walk_gates(window, drive.serial)
    wait_for_page(window, window.page_message)
    assert "not armed" in window.message_label.text()
    assert device_file.read_bytes() == b"\xaa" * SIZE


def test_start_needs_an_operator_name(qapp, device_file, documents_dir):
    window = verified_window(device_file, documents_dir)
    window.drive_list.setCurrentRow(0)
    assert not window.start_button.isEnabled()
    window.operator_edit.setText("Skyler Test")
    assert window.start_button.isEnabled()


# ---------------------------------------------------------------------
# Files & folders mode
# ---------------------------------------------------------------------

from beerus import filewipe  # noqa: E402
from beerus.filewipe import (  # noqa: E402
    FileGateAuthority,
    FileWipeEngine,
    FreeSpaceEngine,
)


def files_window(documents_dir, **kwargs):
    drive = make_drive()
    engine = WipeEngine(
        GateAuthority(),
        FileExecutor("/nonexistent"),
        enumerator=lambda: [drive],
    )
    return MainWindow(engine, GateAuthority(),
                      documents_dir=documents_dir, **kwargs)


def walk_file_gates(window, phrase):
    assert window.stack.currentWidget() is window.page_scope
    window.ack_button.click()
    assert window.stack.currentWidget() is window.page_final
    window.phrase_edit.setText(phrase)
    window.erase_button.click()


def test_files_mode_full_flow(qapp, tmp_path, documents_dir):
    target = tmp_path / "tax-records.txt"
    target.write_bytes(b"secret" * 100)
    window = files_window(documents_dir)
    window.operator_edit.setText("Skyler Test")
    window.mode_files_radio.setChecked(True)
    assert window.panel_drive.isHidden()
    assert not window.panel_files.isHidden()
    window._add_path(str(target))
    assert window.start_files_button.isEnabled()
    window.start_files_button.click()
    assert "tax-records.txt" in window.scope_view.toPlainText()
    assert "1 file(s)" in window.scope_view.toPlainText()
    walk_file_gates(window, "ERASE 1 FILE")
    wait_for_page(window, window.page_result)
    assert not target.exists()
    assert window.verdict_label.text() == "FILE WIPE COMPLETE"
    reports = list(documents_dir.glob("beerus-file-wipe-report-*.html"))
    assert len(reports) == 1
    html = reports[0].read_text(encoding="utf-8")
    assert "File Wipe Report" in html
    assert "tax-records.txt" in html
    assert "not a certificate of destruction" in html


def test_files_mode_wrong_phrase_wipes_nothing(qapp, tmp_path, documents_dir):
    target = tmp_path / "keep.txt"
    target.write_bytes(b"precious" * 50)
    window = files_window(documents_dir)
    window.operator_edit.setText("Skyler Test")
    window.mode_files_radio.setChecked(True)
    window._add_path(str(target))
    window.start_files_button.click()
    window.ack_button.click()
    window.phrase_edit.setText("ERASE EVERYTHING")
    window.erase_button.click()
    assert window.stack.currentWidget() is window.page_message
    assert target.read_bytes() == b"precious" * 50


def test_files_mode_protected_location_refused_at_scope(
    qapp, tmp_path, documents_dir
):
    protected = tmp_path / "protected"
    protected.mkdir()
    target = protected / "os-file.dll"
    target.write_bytes(b"system" * 10)
    window = files_window(
        documents_dir, protected_roots=(str(protected),)
    )
    window.operator_edit.setText("Skyler Test")
    window.mode_files_radio.setChecked(True)
    window._add_path(str(target))
    window.start_files_button.click()
    assert window.stack.currentWidget() is window.page_message
    assert "protected Windows folder" in window.message_label.text()
    assert target.read_bytes() == b"system" * 10


def test_files_mode_locked_file_reported_in_result(
    qapp, tmp_path, documents_dir
):
    target = tmp_path / "locked.txt"
    target.write_bytes(b"in use" * 40)

    def seam(path, flags, original_open):
        raise PermissionError("simulated Windows lock")

    authority = FileGateAuthority()
    window = files_window(
        documents_dir,
        file_authority=authority,
        file_engine=FileWipeEngine(authority, open_seam=seam),
    )
    window.operator_edit.setText("Skyler Test")
    window.mode_files_radio.setChecked(True)
    window._add_path(str(target))
    window.start_files_button.click()
    walk_file_gates(window, "ERASE 1 FILE")
    wait_for_page(window, window.page_result)
    assert "NOTHING WAS WIPED" in window.verdict_label.text()
    assert "FAILED" in window.verification_view.toPlainText()
    assert "locked" in window.verification_view.toPlainText()
    assert target.read_bytes() == b"in use" * 40
    reports = list(documents_dir.glob("beerus-file-wipe-report-*.html"))
    assert len(reports) == 1


# ---------------------------------------------------------------------
# Free-space mode
# ---------------------------------------------------------------------


def test_free_space_mode_full_flow(qapp, tmp_path, documents_dir, monkeypatch):
    live = tmp_path / "keep-me.txt"
    live.write_bytes(b"important live data" * 100)
    probe = lambda p: 24 * MIB  # noqa: E731
    real_build = filewipe.build_free_space_scope
    monkeypatch.setattr(
        filewipe,
        "build_free_space_scope",
        lambda root: real_build(
            root, free_bytes_probe=probe, reserve_bytes=MIB
        ),
    )
    authority = FileGateAuthority()
    window = files_window(
        documents_dir,
        file_authority=authority,
        free_space_engine=FreeSpaceEngine(
            authority, free_bytes_probe=probe, chunk_bytes=MIB
        ),
    )
    window.operator_edit.setText("Skyler Test")
    window.mode_free_radio.setChecked(True)
    assert not window.panel_free.isHidden()
    window.volume_combo.setCurrentText(str(tmp_path))
    assert window.start_free_button.isEnabled()
    window.start_free_button.click()
    assert window.stack.currentWidget() is window.page_scope
    assert "safety margin" in window.scope_view.toPlainText()
    walk_file_gates(window, "WIPE FREE SPACE")
    wait_for_page(window, window.page_result)
    assert window.verdict_label.text() == "FREE-SPACE WIPE COMPLETE"
    assert live.read_bytes() == b"important live data" * 100
    assert not (tmp_path / "Beerus free-space wipe").exists()
    reports = list(documents_dir.glob("beerus-free-space-report-*.html"))
    assert len(reports) == 1
