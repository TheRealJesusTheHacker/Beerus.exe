"""The Beerus Portable window.

One window, one job, in the only order the engine allows:

    pick a drive -> prove it is the right drive (type its serial)
    -> read the full scope and acknowledge it -> type the exact
    final phrase -> the wipe runs -> verification reads the
    surface back -> the certificate (or failure report) is issued.

Every step is driven by the engine's own objects — the gate
session from ``beerus.gates``, the plan from
``beerus.methods.select_plan``, the wipe through
``beerus.engine.WipeEngine``, the verdict from
``beerus.verify``, the document from ``beerus.certificate``.
This window holds no wipe logic and no second gate: it presents
what the engine decides, and where the engine refuses, the
refusal is the screen.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from beerus.certificate import build_job_record, issue_document, write_document
from beerus.engine import assess_target
from beerus.errors import BeerusError
from beerus.methods import select_plan
from beerus.verify import VerificationVerdict

from .theme import STYLESHEET
from .windows_inventory import portable_assessment


def human_size(size_bytes: int) -> str:
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
    return f"{value:.2f} {units[unit_index]} ({size_bytes:,} bytes)"


def _error_text(error: BeerusError) -> str:
    """The plain-language text an engine error carries for users."""
    return getattr(error, "user_message", None) or str(error)


class WipeWorker(QObject):
    """Runs one engine wipe off the window thread.

    The engine reports real byte counters as events; they are
    forwarded untouched — the window renders them, it never
    invents progress of its own.
    """

    event = Signal(object)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, engine, target, token):
        super().__init__()
        self._engine = engine
        self._target = target
        self._token = token

    def run(self):
        try:
            result = self._engine.wipe(
                self._target, self._token, emit=self.event.emit
            )
        except BeerusError as error:
            self.failed.emit(_error_text(error))
            return
        except Exception:
            self.failed.emit(
                "Something went wrong while wiping. Beerus cannot "
                "say what state the drive is in — treat its data as "
                "not destroyed, and run verification before trusting "
                "this drive again."
            )
            return
        self.succeeded.emit(result)


class MainWindow(QMainWindow):
    """The Beerus Portable window, wired to one engine."""

    def __init__(
        self,
        engine,
        authority,
        *,
        documents_dir,
        context_provider=None,
        parent=None,
    ):
        super().__init__(parent)
        self._engine = engine
        self._authority = authority
        self._documents_dir = Path(documents_dir)
        self._context_provider = context_provider

        self._drive = None
        self._plan = None
        self._session = None
        self._thread: QThread | None = None
        self._worker: WipeWorker | None = None
        self._document_path: Path | None = None

        self.setWindowTitle("Beerus Portable — Sentinel Safeguard LLC")
        self.setMinimumSize(780, 640)
        self.setStyleSheet(STYLESHEET)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self._build_drives_page()
        self._build_identity_page()
        self._build_scope_page()
        self._build_final_page()
        self._build_running_page()
        self._build_result_page()
        self._build_message_page()
        self.stack.addWidget(self.page_drives)
        self.stack.addWidget(self.page_identity)
        self.stack.addWidget(self.page_scope)
        self.stack.addWidget(self.page_final)
        self.stack.addWidget(self.page_running)
        self.stack.addWidget(self.page_result)
        self.stack.addWidget(self.page_message)

        self.refresh_drives()

    # ------------------------------------------------------------------
    # Page 1: the drive list
    # ------------------------------------------------------------------

    def _build_drives_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("BEERUS PORTABLE")
        title.setObjectName("title")
        layout.addWidget(title)
        subtitle = QLabel(
            "Secure drive destruction, on this machine. Pick a "
            "drive, prove it is the right one, and Beerus destroys "
            "its data to the NIST 800-88 standard — then reads the "
            "surface back and shows you exactly what it checked."
        )
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        operator_label = QLabel(
            "Your name — it goes on the certificate of destruction:"
        )
        layout.addWidget(operator_label)
        self.operator_edit = QLineEdit()
        self.operator_edit.setPlaceholderText("Operator name")
        self.operator_edit.textChanged.connect(self._update_start_enabled)
        layout.addWidget(self.operator_edit)

        self.drive_list = QListWidget()
        self.drive_list.itemSelectionChanged.connect(
            self._update_start_enabled
        )
        layout.addWidget(self.drive_list, stretch=1)

        note = QLabel(
            "Drives Beerus Portable will not wipe are greyed, with "
            "the reason shown. Nothing is hidden from this list."
        )
        note.setObjectName("subtitle")
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("Read the drives again")
        self.refresh_button.clicked.connect(self.refresh_drives)
        buttons.addWidget(self.refresh_button)
        buttons.addStretch(1)
        self.start_button = QPushButton("Start — this is the drive")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self._begin_gates)
        buttons.addWidget(self.start_button)
        layout.addLayout(buttons)
        self.page_drives = page

    def refresh_drives(self):
        """Re-read the drives and rebuild the list, honestly."""
        self.drive_list.clear()
        try:
            drives = self._engine.list_drives()
        except BeerusError as error:
            self._add_note_item(_error_text(error))
            self._update_start_enabled()
            return
        except Exception:
            self._add_note_item(
                "Beerus Portable could not read the list of drives "
                "on this machine."
            )
            self._update_start_enabled()
            return
        context = (
            self._context_provider()
            if self._context_provider is not None
            else None
        )
        if not drives:
            self._add_note_item(
                "No drives were found on this machine. Connect the "
                "drive to destroy, then read the drives again."
            )
        for drive in drives:
            assessment = portable_assessment(drive, context)
            if assessment.eligible:
                status = "Ready to wipe"
            else:
                status = assessment.reason or "Cannot be wiped"
            item = QListWidgetItem(
                f"{drive.model} — serial {drive.serial}\n"
                f"{human_size(drive.size_bytes)} · "
                f"{drive.interface or 'unknown connection'} · {status}"
            )
            item.setData(Qt.ItemDataRole.UserRole, drive)
            item.setData(Qt.ItemDataRole.UserRole + 1, assessment)
            if not assessment.eligible:
                item.setFlags(
                    item.flags() & ~Qt.ItemFlag.ItemIsEnabled
                )
            self.drive_list.addItem(item)
        self._update_start_enabled()

    def _add_note_item(self, text):
        item = QListWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        self.drive_list.addItem(item)

    def selected_drive(self):
        item = self.drive_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _update_start_enabled(self):
        drive = self.selected_drive()
        operator = self.operator_edit.text().strip()
        self.start_button.setEnabled(drive is not None and bool(operator))

    # ------------------------------------------------------------------
    # The gates (pages 2-4)
    # ------------------------------------------------------------------

    def _build_identity_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel("Prove this is the right drive")
        heading.setObjectName("title")
        layout.addWidget(heading)
        warning = QLabel(
            "Everything on this drive is about to be destroyed "
            "permanently. Check the drive in front of you against "
            "these details — model and serial number are printed "
            "on the drive's own label."
        )
        warning.setObjectName("warning")
        warning.setWordWrap(True)
        layout.addWidget(warning)
        self.identity_model = QLabel("")
        self.identity_model.setObjectName("identity")
        layout.addWidget(self.identity_model)
        self.identity_serial = QLabel("")
        self.identity_serial.setObjectName("serial")
        layout.addWidget(self.identity_serial)
        self.identity_facts = QLabel("")
        self.identity_facts.setWordWrap(True)
        layout.addWidget(self.identity_facts)
        prompt = QLabel(
            "Type the drive's serial number exactly as shown, "
            "to prove this is the drive you mean to destroy:"
        )
        prompt.setWordWrap(True)
        layout.addWidget(prompt)
        self.serial_edit = QLineEdit()
        layout.addWidget(self.serial_edit)
        expiry = QLabel(
            "For safety, this confirmation expires after a few "
            "minutes. If it expires, nothing has been wiped — "
            "just start again."
        )
        expiry.setObjectName("subtitle")
        layout.addWidget(expiry)
        buttons = QHBoxLayout()
        cancel = QPushButton("Cancel — back to the drive list")
        cancel.clicked.connect(self._cancel_gates)
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        self.continue_button = QPushButton("Continue")
        self.continue_button.setObjectName("primary")
        self.continue_button.clicked.connect(self._confirm_identity)
        buttons.addWidget(self.continue_button)
        layout.addLayout(buttons)
        layout.addStretch(1)
        self.page_identity = page

    def _build_scope_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel("What will happen — in full")
        heading.setObjectName("title")
        layout.addWidget(heading)
        self.scope_view = QPlainTextEdit()
        self.scope_view.setReadOnly(True)
        layout.addWidget(self.scope_view, stretch=1)
        buttons = QHBoxLayout()
        cancel = QPushButton("Cancel — back to the drive list")
        cancel.clicked.connect(self._cancel_gates)
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        self.ack_button = QPushButton(
            "I have read this — everything on this drive "
            "will be destroyed"
        )
        self.ack_button.setObjectName("primary")
        self.ack_button.clicked.connect(self._acknowledge_scope)
        buttons.addWidget(self.ack_button)
        layout.addLayout(buttons)
        self.page_scope = page

    def _build_final_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel("The last step")
        heading.setObjectName("title")
        layout.addWidget(heading)
        warning = QLabel(
            "There is no step after this one. When you press the "
            "erase button, the wipe starts, and the data on this "
            "drive is gone for good."
        )
        warning.setObjectName("warning")
        warning.setWordWrap(True)
        layout.addWidget(warning)
        self.phrase_label = QLabel("")
        self.phrase_label.setWordWrap(True)
        layout.addWidget(self.phrase_label)
        self.phrase_edit = QLineEdit()
        layout.addWidget(self.phrase_edit)
        buttons = QHBoxLayout()
        cancel = QPushButton("Cancel — back to the drive list")
        cancel.clicked.connect(self._cancel_gates)
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        self.erase_button = QPushButton("Erase this drive — permanently")
        self.erase_button.setObjectName("danger")
        self.erase_button.clicked.connect(self._final_confirmation)
        buttons.addWidget(self.erase_button)
        layout.addLayout(buttons)
        layout.addStretch(1)
        self.page_final = page

    def _begin_gates(self):
        drive = self.selected_drive()
        if drive is None:
            return
        try:
            plan = select_plan(drive)
            session = self._authority.begin_session(drive, plan)
        except BeerusError as error:
            self._show_message(_error_text(error))
            return
        self._drive = drive
        self._plan = plan
        self._session = session
        self.identity_model.setText(drive.model)
        self.identity_serial.setText(f"Serial: {drive.serial}")
        self.identity_facts.setText(
            f"Size: {human_size(drive.size_bytes)} · "
            f"Connection: {drive.interface or 'unknown'} · "
            f"Method: {plan.method_label} "
            f"(NIST 800-88 {plan.nist_category})"
        )
        self.serial_edit.clear()
        self.stack.setCurrentWidget(self.page_identity)

    def _confirm_identity(self):
        try:
            self._session.confirm_identity(self.serial_edit.text())
        except BeerusError as error:
            self._session = None
            self._show_message(_error_text(error))
            return
        self.scope_view.setPlainText(self._session.scope.text)
        self.stack.setCurrentWidget(self.page_scope)

    def _acknowledge_scope(self):
        try:
            self._session.acknowledge_scope()
        except BeerusError as error:
            self._session = None
            self._show_message(_error_text(error))
            return
        self.phrase_label.setText(
            "Type this exactly, including the serial number:\n"
            f"ERASE {self._drive.serial}"
        )
        self.phrase_edit.clear()
        self.stack.setCurrentWidget(self.page_final)

    def _final_confirmation(self):
        try:
            token = self._session.final_confirmation(self.phrase_edit.text())
        except BeerusError as error:
            self._session = None
            self._show_message(_error_text(error))
            return
        self._session = None
        self._start_wipe(token)

    def _cancel_gates(self):
        self._session = None
        self._drive = None
        self._plan = None
        self.refresh_drives()
        self.stack.setCurrentWidget(self.page_drives)

    # ------------------------------------------------------------------
    # Page 5: the wipe running
    # ------------------------------------------------------------------

    def _build_running_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel("Destroying the data")
        heading.setObjectName("title")
        layout.addWidget(heading)
        self.running_facts = QLabel("")
        self.running_facts.setObjectName("warning")
        self.running_facts.setWordWrap(True)
        layout.addWidget(self.running_facts)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # busy until real counters arrive
        layout.addWidget(self.progress_bar)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view, stretch=1)
        note = QLabel(
            "Do not switch the machine off or unplug the drive "
            "while this runs. When the wipe finishes, Beerus "
            "reads the surface back and verifies the result "
            "before any certificate is issued."
        )
        note.setObjectName("subtitle")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.page_running = page

    def _start_wipe(self, token):
        self.running_facts.setText(
            f"{self._drive.model} — serial {self._drive.serial}\n"
            f"Method: {self._plan.method_label}"
        )
        self.log_view.clear()
        self.progress_bar.setRange(0, 0)
        self.stack.setCurrentWidget(self.page_running)

        self._thread = QThread(self)
        self._worker = WipeWorker(self._engine, self._drive, token)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.event.connect(self._on_progress)
        self._worker.succeeded.connect(self._on_wipe_succeeded)
        self._worker.failed.connect(self._on_wipe_failed)
        self._worker.succeeded.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.start()

    def _on_progress(self, event):
        if event.message:
            self.log_view.appendPlainText(event.message)
        if event.bytes_total and event.bytes_total > 0:
            self.progress_bar.setRange(0, int(event.bytes_total))
            self.progress_bar.setValue(int(event.bytes_done))
        else:
            self.progress_bar.setRange(0, 0)

    def _on_wipe_failed(self, message):
        self._show_message(message)

    # ------------------------------------------------------------------
    # Page 6: the result + the document
    # ------------------------------------------------------------------

    def _build_result_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel("The result")
        heading.setObjectName("title")
        layout.addWidget(heading)
        self.result_method = QLabel("")
        self.result_method.setObjectName("warning")
        self.result_method.setWordWrap(True)
        layout.addWidget(self.result_method)
        self.verdict_label = QLabel("")
        layout.addWidget(self.verdict_label)
        self.verification_view = QPlainTextEdit()
        self.verification_view.setReadOnly(True)
        layout.addWidget(self.verification_view, stretch=1)
        self.cert_label = QLabel("")
        self.cert_label.setWordWrap(True)
        layout.addWidget(self.cert_label)
        buttons = QHBoxLayout()
        self.open_doc_button = QPushButton("Open the document")
        self.open_doc_button.clicked.connect(self._open_document)
        buttons.addWidget(self.open_doc_button)
        buttons.addStretch(1)
        self.again_button = QPushButton("Wipe another drive")
        self.again_button.setObjectName("primary")
        self.again_button.clicked.connect(self._back_to_drives)
        buttons.addWidget(self.again_button)
        close_button = QPushButton("Close Beerus Portable")
        close_button.clicked.connect(self.close)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        self.page_result = page

    def _verification_text(self, verification) -> str:
        """What verification checked, stated exactly as it happened."""
        if verification is None:
            return (
                "This run did not include verification, so Beerus "
                "cannot say the data is gone. No certificate can be "
                "issued for an unverified wipe."
            )
        lines = []
        if verification.coverage == "full":
            lines.append(
                "What was checked: the full drive surface — "
                f"{verification.bytes_checked:,} of "
                f"{verification.bytes_total:,} bytes were read "
                "back and checked."
            )
        elif verification.coverage == "sampled":
            lines.append(
                "What was checked: a sample of the drive surface — "
                f"{verification.bytes_checked:,} of "
                f"{verification.bytes_total:,} bytes in "
                f"{verification.regions_checked} regions were read "
                "back and checked, and the drive's own erase "
                "status was checked where the drive reports one. "
                "The rest of the surface was not read back; that "
                "is the honest limit of checking an erase the "
                "drive's firmware performed."
            )
        else:
            lines.append(
                "What was checked: nothing — the drive could not "
                "be read back, so no part of the surface was "
                "checked."
            )
        if verification.remnants_found:
            lines.append(
                "Surviving data was found: "
                f"{verification.remnants_found} remnant(s), first "
                "at byte offset(s) "
                + ", ".join(
                    str(o) for o in verification.first_remnant_offsets
                )
                + "."
            )
        firmware_lines = {
            "confirmed": (
                "The drive's firmware confirms its erase finished."
            ),
            "in_progress": (
                "The drive reports its erase is still in progress."
            ),
            "unreadable": (
                "The drive's erase status could not be read."
            ),
            "not_available": (
                "This drive does not report an erase status."
            ),
        }
        firmware_line = firmware_lines.get(verification.firmware_status)
        if firmware_line:
            lines.append(firmware_line)
        if verification.detail:
            lines.append(verification.detail)
        return "\n".join(lines)

    def _on_wipe_succeeded(self, result):
        self.result_method.setText(
            f"{self._drive.model} — serial {self._drive.serial}\n"
            f"Method that ran: {result.method_label} "
            f"(NIST 800-88 {result.nist_category})"
        )
        verification = result.verification
        if verification is None:
            verdict_word = "NOT VERIFIED"
            self.verdict_label.setObjectName("verdictBad")
        elif verification.verdict is VerificationVerdict.PASS:
            verdict_word = "VERIFICATION: PASSED"
            self.verdict_label.setObjectName("verdictPass")
        elif verification.verdict is VerificationVerdict.FAIL:
            verdict_word = "VERIFICATION: FAILED"
            self.verdict_label.setObjectName("verdictBad")
        else:
            verdict_word = "NOT VERIFIED"
            self.verdict_label.setObjectName("verdictBad")
        self.verdict_label.setText(verdict_word)
        self.verdict_label.style().unpolish(self.verdict_label)
        self.verdict_label.style().polish(self.verdict_label)
        self.verification_view.setPlainText(
            self._verification_text(verification)
        )

        self._document_path = None
        self.open_doc_button.setEnabled(False)
        if verification is None:
            self.cert_label.setText(
                "No document was issued: without verification, "
                "there is nothing Beerus can certify."
            )
        else:
            try:
                record = build_job_record(
                    self._drive,
                    result,
                    self.operator_edit.text(),
                )
                document = issue_document(record)
                self._documents_dir.mkdir(parents=True, exist_ok=True)
                path = write_document(document, self._documents_dir)
                self._document_path = Path(path)
                if document.kind == "certificate":
                    self.cert_label.setText(
                        "Certificate of destruction saved: "
                        f"{self._document_path.name}\n"
                        f"Saved in: {self._document_path.parent}"
                    )
                else:
                    self.cert_label.setText(
                        "This wipe did not earn a certificate. A "
                        "failure report was saved instead: "
                        f"{self._document_path.name}\n"
                        f"Saved in: {self._document_path.parent}"
                    )
                self.open_doc_button.setEnabled(True)
            except BeerusError as error:
                self.cert_label.setText(_error_text(error))
            except OSError:
                self.cert_label.setText(
                    "The document could not be saved. The wipe and "
                    "its verification above are still the record of "
                    "this job — note them down before closing."
                )
        self.stack.setCurrentWidget(self.page_result)

    def _open_document(self):
        if self._document_path is not None:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self._document_path))
            )

    def _back_to_drives(self):
        self._drive = None
        self._plan = None
        self.refresh_drives()
        self.stack.setCurrentWidget(self.page_drives)

    # ------------------------------------------------------------------
    # Page 7: a designed message (refusals and failures end here)
    # ------------------------------------------------------------------

    def _build_message_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel("Beerus stopped — nothing was wiped")
        heading.setObjectName("title")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        self.message_label = QLabel("")
        self.message_label.setObjectName("warning")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)
        self.back_button = QPushButton("Back to the drive list")
        self.back_button.setObjectName("primary")
        self.back_button.clicked.connect(self._back_to_drives)
        layout.addWidget(self.back_button)
        layout.addStretch(1)
        self.page_message = page

    def _show_message(self, message):
        self.message_label.setText(message)
        self.stack.setCurrentWidget(self.page_message)


def build_window(engine, authority, *, documents_dir, context_provider=None):
    """Assemble the portable window over a wired engine."""
    return MainWindow(
        engine,
        authority,
        documents_dir=documents_dir,
        context_provider=context_provider,
    )
