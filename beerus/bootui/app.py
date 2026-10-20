"""The Beerus boot-environment window.

One window over the live session's own flows — the graphical
twin of the boot console (``beerus.live``), not a second
interface over the engine. Every step goes through the same
objects the console drives: ``engine.assess_all()`` for the
drive list, ``GateAuthority.begin_session`` with the plan from
``beerus.methods.select_plan``, the gate session's strict order
(confirm the serial, acknowledge the full scope, type the exact
final phrase), the wipe on a worker thread, and
``beerus.certificate`` for the job document.

This module contains no arming code and constructs no executor
(see the inheritance law in ``beerus.bootui``): ``main`` asks
``beerus.live.build_live_engine`` — the one arming site — for
the session's ``(engine, authority, documents_dir)``, and a
refusal from that builder is shown as a designed refusal window
with exit code 2, exactly as the console prints it and exits 2.

Every failure state is a designed page: a refused drive list, a
gate refusal, a session that expired, a wipe that stopped early
(reported as NOT sanitized, in the console's words), a document
that could not be issued or saved. Nothing here shows a
traceback, and nothing invents progress: the running page
renders the engine's events as they arrive, and a percentage
appears only when an event actually carries one.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject, QPropertyAnimation, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsOpacityEffect,
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
from beerus.errors import BeerusError, RefusalError
from beerus.live import build_live_engine
from beerus.methods import select_plan
from beerus.verify import VerificationVerdict

from .theme import STYLESHEET

EXIT_OK = 0
EXIT_REFUSAL = 2
EXIT_NO_GRAPHICAL = 76

_VERDICT_WORDS = {
    VerificationVerdict.PASS: "PASSED",
    VerificationVerdict.FAIL: "FAILED",
    VerificationVerdict.NOT_VERIFIED: "NOT VERIFIED",
}

_NO_DRIVES_NOTE = (
    "No drive on this machine can be wiped right now. Connect a "
    "drive to destroy, then list the drives again — or quit and "
    "shut the machine down."
)

_NOT_SANITIZED = (
    "Treat this drive as NOT sanitized: its data may be partly "
    "or fully intact. Run the wipe again and let verification "
    "complete before relying on it."
)


def _error_text(error: BeerusError) -> str:
    """The plain-language text an engine error carries for users."""
    return getattr(error, "user_message", None) or str(error)


def human_size(size_bytes: int) -> str:
    """Render a byte count in a plain, human-readable form.

    This formatter lives inside the boot UI package on purpose:
    the live image stages ONLY the ``beerus`` package (see
    ``image/stage_image.py``), so nothing here may import a
    helper from outside it — an import the repo checkout can
    resolve but the image cannot would kill the graphical
    session at import time, on every machine. The format is
    the one the drive list has always shown (the same rendering
    the portable app uses), pinned by test:
    ``8.00 MB (8,388,608 bytes)``.
    """
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


def _percent_text(percent: float) -> str:
    """Render an event's real percentage, without inventing digits."""
    return f"{percent:g}%"


class WipeWorker(QObject):
    """Runs one engine wipe off the window thread.

    Mirror of the portable app's worker, with the failure kinds
    kept apart the way the console keeps them apart: a BeerusError
    carries its own plain-language message, an OSError means the
    wipe stopped early (the drive is NOT sanitized), and anything
    else is a stop Beerus could not read. The window renders what
    each kind means; the worker never merges them.
    """

    event = Signal(object)
    succeeded = Signal(object)
    failed_beerus = Signal(str)
    failed_os = Signal(str)
    failed_unknown = Signal()

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
            self.failed_beerus.emit(_error_text(error))
            return
        except OSError as error:
            self.failed_os.emit(
                error.strerror or "the system refused the operation"
            )
            return
        except Exception:
            self.failed_unknown.emit()
            return
        self.succeeded.emit(result)


class BootWindow(QMainWindow):
    """The boot UI window, wired to one live session."""

    def __init__(self, engine, authority, documents_dir, parent=None):
        super().__init__(parent)
        self._engine = engine
        self._authority = authority
        self._documents_dir = Path(documents_dir)
        self._documents_dir_text = str(documents_dir)

        self._assessments: list = []
        self._drive = None
        self._plan = None
        self._session = None
        self._thread: QThread | None = None
        self._worker = None
        self._wiping = False

        # The page fade is purely visual: the target page is set
        # immediately and synchronously, and the fade never
        # delays or blocks a transition. Tests and the
        # screenshot tool may turn it off (a settled render must
        # not depend on an animation finishing).
        self.fade_enabled = True
        self._fade_animation = None
        self._fade_effect = None
        self._fade_target = None

        self.setWindowTitle("Beerus — Live Boot")
        self.setMinimumSize(860, 640)
        self.setStyleSheet(STYLESHEET)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self._build_welcome_page()
        self._build_drives_page()
        self._build_identity_page()
        self._build_scope_page()
        self._build_final_page()
        self._build_running_page()
        self._build_result_page()
        self._build_message_page()
        for page in (
            self.page_welcome,
            self.page_drives,
            self.page_identity,
            self.page_scope,
            self.page_final,
            self.page_running,
            self.page_result,
            self.page_message,
        ):
            self.stack.addWidget(page)
        self.stack.setCurrentWidget(self.page_welcome)
        self.stack.currentChanged.connect(self._fade_page)

    # ------------------------------------------------------------------
    # Animation: a short fade on page changes, visual only
    # ------------------------------------------------------------------

    def _fade_page(self, index):
        # Settle any fade still running: its page goes straight
        # to full opacity with no effect left on it.
        if self._fade_animation is not None:
            self._fade_animation.stop()
            self._settle_fade()
        if not self.fade_enabled:
            return
        page = self.stack.widget(index)
        if page is None:
            return
        effect = QGraphicsOpacityEffect(page)
        effect.setOpacity(0.0)
        page.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(150)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.finished.connect(self._settle_fade)
        self._fade_animation = animation
        self._fade_effect = effect
        self._fade_target = page
        animation.start()

    def _settle_fade(self):
        if self._fade_target is not None:
            if self._fade_effect is not None:
                self._fade_effect.setOpacity(1.0)
            self._fade_target.setGraphicsEffect(None)
        self._fade_animation = None
        self._fade_effect = None
        self._fade_target = None

    def _show(self, page):
        self.stack.setCurrentWidget(page)

    # ------------------------------------------------------------------
    # Page 1: welcome
    # ------------------------------------------------------------------

    def _build_welcome_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("BEERUS — Live Boot")
        title.setObjectName("brand")
        layout.addWidget(title)
        subtitle = QLabel("Secure drive destruction — Sentinel Safeguard LLC")
        subtitle.setObjectName("subtitle")
        layout.addWidget(subtitle)
        protection = QLabel(
            "This machine booted from the Live Boot key. The "
            "key itself can never be wiped, and no drive is "
            "touched until you choose one and complete the "
            "confirmation steps."
        )
        protection.setWordWrap(True)
        layout.addWidget(protection)
        # This page can only render inside a running graphical
        # session, so the line is true by construction.
        environment = QLabel("Graphical session")
        environment.setObjectName("subtitle")
        layout.addWidget(environment)
        documents = QLabel(
            "Job documents from this session are kept in memory "
            "only, at:\n"
            f"  {self._documents_dir_text}\n"
            "Copy each document somewhere safe before this "
            "machine is shut down — when it turns off, they are "
            "gone."
        )
        documents.setWordWrap(True)
        layout.addWidget(documents)
        operator_label = QLabel(
            "Your name — it goes on the job documents:"
        )
        layout.addWidget(operator_label)
        self.operator_edit = QLineEdit()
        self.operator_edit.setPlaceholderText("Operator name")
        self.operator_edit.textChanged.connect(self._update_begin_enabled)
        layout.addWidget(self.operator_edit)
        buttons = QHBoxLayout()
        quit_button = QPushButton("Quit")
        quit_button.clicked.connect(self.close)
        buttons.addWidget(quit_button)
        buttons.addStretch(1)
        self.begin_button = QPushButton("Begin")
        self.begin_button.setObjectName("primary")
        self.begin_button.setEnabled(False)
        self.begin_button.clicked.connect(self._show_drives)
        buttons.addWidget(self.begin_button)
        layout.addLayout(buttons)
        layout.addStretch(1)
        self.page_welcome = page

    def _update_begin_enabled(self):
        self.begin_button.setEnabled(bool(self.operator_edit.text().strip()))

    # ------------------------------------------------------------------
    # Page 2: the drive list
    # ------------------------------------------------------------------

    def _build_drives_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel("Choose the drive to destroy")
        heading.setObjectName("title")
        layout.addWidget(heading)
        self.drive_list = QListWidget()
        self.drive_list.setWordWrap(True)
        self.drive_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.drive_list.itemSelectionChanged.connect(
            self._update_start_enabled
        )
        layout.addWidget(self.drive_list, stretch=1)
        self.no_eligible_note = QLabel(_NO_DRIVES_NOTE)
        self.no_eligible_note.setObjectName("subtitle")
        self.no_eligible_note.setWordWrap(True)
        self.no_eligible_note.setVisible(False)
        layout.addWidget(self.no_eligible_note)
        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh the list")
        self.refresh_button.clicked.connect(self._show_drives)
        buttons.addWidget(self.refresh_button)
        quit_button = QPushButton("Quit")
        quit_button.clicked.connect(self.close)
        buttons.addWidget(quit_button)
        buttons.addStretch(1)
        self.start_button = QPushButton("Start the confirmation")
        self.start_button.setObjectName("primary")
        self.start_button.setEnabled(False)
        self.start_button.clicked.connect(self._begin_gates)
        buttons.addWidget(self.start_button)
        layout.addLayout(buttons)
        self.page_drives = page

    def _show_drives(self):
        """(Re-)read the drives and show the list, honestly."""
        if self.stack.currentWidget() is self.page_welcome:
            # The Begin step is gated on an operator name, and
            # the gate is re-checked HERE, in the handler — not
            # only in the Begin button's enabled state. A signal
            # emitted programmatically bypasses enablement, and a
            # name cleared after typing must not let the flow
            # advance: a wipe run with no operator could never
            # have its job document issued.
            if not self.operator_edit.text().strip():
                return
        # (Re-)entering the drive list abandons any gate session
        # in progress: the confirmation starts over from the
        # list. The text console's flow is linear and holds no
        # such state to resume; this window must not invent a
        # resumable one against a now-stale assessment.
        self._session = None
        self._drive = None
        self._plan = None
        try:
            assessments = self._engine.assess_all()
        except BeerusError as error:
            self._show_message(
                f"{_error_text(error)}\n"
                "Beerus cannot continue without a drive list.",
                try_again=True,
            )
            return
        except Exception:
            self._show_message(
                "Beerus could not read the list of drives on this "
                "machine.\n"
                "Beerus cannot continue without a drive list.",
                try_again=True,
            )
            return
        self._assessments = list(assessments)
        self.drive_list.clear()
        for index, assessment in enumerate(self._assessments):
            drive = assessment.drive
            text = (
                f"{drive.model or 'Unidentified drive'} — "
                f"serial {drive.serial or 'not readable'} — "
                f"{human_size(drive.size_bytes)}"
            )
            if not assessment.eligible:
                text += (
                    f"\nThis drive cannot be wiped: {assessment.reason}"
                )
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, index)
            if not assessment.eligible:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.drive_list.addItem(item)
        self.no_eligible_note.setVisible(
            not any(a.eligible for a in self._assessments)
        )
        self._update_start_enabled()
        self._show(self.page_drives)

    def _update_start_enabled(self):
        # The Start button's own state must reflect the greyed
        # assessment, not merely the presence of a selection: an
        # ineligible (disabled) row can be landed on
        # programmatically even though a mouse user can't pick
        # it, and the button must stay off for it (the portable's
        # N3 lesson). The gate still refuses independently.
        item = self.drive_list.currentItem()
        selectable = (
            item is not None
            and bool(item.flags() & Qt.ItemFlag.ItemIsEnabled)
            and item.data(Qt.ItemDataRole.UserRole) is not None
        )
        self.start_button.setEnabled(selectable)

    def _selected_assessment(self):
        item = self.drive_list.currentItem()
        if item is None:
            return None
        index = item.data(Qt.ItemDataRole.UserRole)
        if index is None or not 0 <= index < len(self._assessments):
            return None
        return self._assessments[index]

    # ------------------------------------------------------------------
    # Pages 3-5: the gates (identity, scope, final)
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
        self.serial_edit.returnPressed.connect(self._confirm_identity)
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
            "I have read this — everything on this drive will be destroyed"
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
        self.phrase_edit.returnPressed.connect(self._final_confirmation)
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
        # The operator gate is re-checked here too (see
        # _show_drives): a name cleared after Begin must not
        # let a confirmation start that could never be
        # documented.
        if not self.operator_edit.text().strip():
            return
        assessment = self._selected_assessment()
        if assessment is None or not assessment.eligible:
            return
        drive = assessment.drive
        try:
            plan = select_plan(drive)
            session = self._authority.begin_session(drive, plan)
        except BeerusError as error:
            self._show_message(_error_text(error))
            return
        except Exception:
            self._show_message(
                "Beerus could not start the confirmation for that "
                "drive. Nothing was wiped."
            )
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
        self._show(self.page_identity)

    def _confirm_identity(self):
        if self._session is None:
            return
        try:
            self._session.confirm_identity(self.serial_edit.text())
        except BeerusError as error:
            self._session = None
            self._show_message(_error_text(error))
            return
        except Exception:
            self._session = None
            self._show_message(
                "The confirmation could not be completed. "
                "Nothing was wiped."
            )
            return
        self.scope_view.setPlainText(self._session.scope.text)
        self._show(self.page_scope)

    def _acknowledge_scope(self):
        if self._session is None:
            return
        try:
            self._session.acknowledge_scope()
        except BeerusError as error:
            self._session = None
            self._show_message(_error_text(error))
            return
        except Exception:
            self._session = None
            self._show_message(
                "The confirmation could not be completed. "
                "Nothing was wiped."
            )
            return
        self.phrase_label.setText(
            "Type this exactly, including the serial number:\n"
            f"ERASE {self._drive.serial}"
        )
        self.phrase_edit.clear()
        self._show(self.page_final)

    def _final_confirmation(self):
        if self._session is None:
            return
        try:
            token = self._session.final_confirmation(self.phrase_edit.text())
        except BeerusError as error:
            self._session = None
            self._show_message(_error_text(error))
            return
        except Exception:
            self._session = None
            self._show_message(
                "The confirmation could not be completed. "
                "Nothing was wiped."
            )
            return
        self._session = None
        self._start_wipe(token)

    def _cancel_gates(self):
        self._session = None
        self._drive = None
        self._plan = None
        self._show_drives()

    # ------------------------------------------------------------------
    # Page 6: the wipe running
    # ------------------------------------------------------------------

    def _build_running_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel("Destroying the data")
        heading.setObjectName("title")
        layout.addWidget(heading)
        self.running_status = QLabel("")
        self.running_status.setObjectName("warning")
        self.running_status.setWordWrap(True)
        layout.addWidget(self.running_status)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # busy until a real event says more
        self.progress_bar.setFormat("")
        layout.addWidget(self.progress_bar)
        note = QLabel("The wipe is running. Do not turn the machine off.")
        note.setObjectName("subtitle")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        self.page_running = page

    def _start_wipe(self, token):
        self.running_status.setText("Starting the wipe.")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat("")
        self._wiping = True
        self._show(self.page_running)

        self._thread = QThread(self)
        self._worker = WipeWorker(self._engine, self._drive, token)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.event.connect(self._on_progress)
        self._worker.succeeded.connect(self._on_wipe_succeeded)
        self._worker.failed_beerus.connect(self._on_wipe_failed_beerus)
        self._worker.failed_os.connect(self._on_wipe_failed_os)
        self._worker.failed_unknown.connect(self._on_wipe_failed_unknown)
        self._worker.succeeded.connect(self._thread.quit)
        self._worker.failed_beerus.connect(self._thread.quit)
        self._worker.failed_os.connect(self._thread.quit)
        self._worker.failed_unknown.connect(self._thread.quit)
        self._thread.start()

    def _on_progress(self, event):
        if event.message:
            self.running_status.setText(event.message)
        percent = event.percent
        if percent is not None:
            # The event carried a real percentage: show exactly
            # that, in the bar's value and its format together.
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(int(percent))
            self.progress_bar.setFormat(_percent_text(percent))
        else:
            # No real total to measure against (a firmware erase
            # reports none): busy bar, and no numeric percentage
            # anywhere on the page.
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setFormat("")

    def _on_wipe_failed_beerus(self, message):
        self._wiping = False
        self._show_message(message)

    def _on_wipe_failed_os(self, reason):
        self._wiping = False
        self._show_unfinished(
            f"The wipe stopped before it finished: {reason}."
        )

    def _on_wipe_failed_unknown(self):
        self._wiping = False
        self._show_unfinished(
            "The wipe stopped before it finished, for a reason "
            "Beerus could not read."
        )

    def _show_unfinished(self, reason_line):
        self.result_method.setText(reason_line)
        self._set_verdict_text(_NOT_SANITIZED, passed=False)
        self.document_label.setText("")
        self._show(self.page_result)

    # ------------------------------------------------------------------
    # Page 7: the result + the document
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
        self.verdict_label.setWordWrap(True)
        layout.addWidget(self.verdict_label)
        self.document_label = QLabel("")
        self.document_label.setWordWrap(True)
        layout.addWidget(self.document_label)
        buttons = QHBoxLayout()
        self.back_to_drives_button = QPushButton("Back to the drive list")
        self.back_to_drives_button.setObjectName("primary")
        self.back_to_drives_button.clicked.connect(self._back_to_drives)
        buttons.addWidget(self.back_to_drives_button)
        buttons.addStretch(1)
        quit_button = QPushButton("Quit")
        quit_button.clicked.connect(self.close)
        buttons.addWidget(quit_button)
        layout.addLayout(buttons)
        layout.addStretch(1)
        self.page_result = page

    def _set_verdict_text(self, text, *, passed):
        self.verdict_label.setText(text)
        self.verdict_label.setObjectName(
            "verdictPass" if passed else "verdictBad"
        )
        self.verdict_label.style().unpolish(self.verdict_label)
        self.verdict_label.style().polish(self.verdict_label)

    def _on_wipe_succeeded(self, result):
        self._wiping = False
        self.result_method.setText(f"Wipe finished: {result.method_label}.")
        verification = result.verification
        if verification is None:
            self._set_verdict_text(
                "No document was issued: without verification, "
                "there is nothing Beerus can certify.",
                passed=False,
            )
            self.document_label.setText("")
            self._show(self.page_result)
            return
        self._set_verdict_text(
            "Verification: "
            f"{_VERDICT_WORDS.get(verification.verdict, 'NOT VERIFIED')} — "
            f"{verification.detail}",
            passed=verification.verdict is VerificationVerdict.PASS,
        )
        self.document_label.setText(self._document_text(result))
        self._show(self.page_result)

    def _document_text(self, result) -> str:
        """Issue this run's document; return its designed wording.

        Mirrors the console exactly: the document can only be
        issued in the same run as its wipe, a BeerusError is an
        issue failure, and an OSError is a save failure — the
        wipe and its verification stand either way.
        """
        try:
            record = build_job_record(
                self._drive, result, self.operator_edit.text()
            )
            document = issue_document(record)
            path = Path(write_document(document, self._documents_dir))
        except BeerusError as error:
            return (
                "The wipe finished, but its document could not "
                f"be issued: {_error_text(error)}"
            )
        except OSError as error:
            reason = error.strerror or "the system refused the operation"
            return (
                "The wipe finished, and the verification above "
                "stands — but its document could not be saved: "
                f"{reason}. A document can only be issued in the "
                "same run as its wipe, so this run's document "
                "cannot be recreated. Check the documents location "
                f"({self._documents_dir_text}) before the next job."
            )
        except Exception:
            return (
                "The wipe finished, and the verification above "
                "stands — but its document could not be issued, "
                "for a reason Beerus could not read. A document "
                "can only be issued in the same run as its wipe, "
                "so this run's document cannot be recreated."
            )
        if document.kind == "certificate":
            headline = f"Certificate of destruction saved: {path.name}"
        else:
            headline = (
                "This wipe did not earn a certificate. A failure "
                f"report was saved instead: {path.name}"
            )
        return (
            f"{headline}\n"
            f"Saved in: {path.parent}\n"
            "Copy it somewhere safe before this machine is shut "
            "down — it is stored in memory only."
        )

    def _back_to_drives(self):
        self._drive = None
        self._plan = None
        self._session = None
        self._show_drives()

    # ------------------------------------------------------------------
    # Page 8: a designed message (refusals and failures end here)
    # ------------------------------------------------------------------

    def _build_message_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel("Beerus stopped this step")
        heading.setObjectName("title")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        self.message_label = QLabel("")
        self.message_label.setObjectName("warning")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)
        buttons = QHBoxLayout()
        self.try_again_button = QPushButton("Try again")
        self.try_again_button.clicked.connect(self._show_drives)
        buttons.addWidget(self.try_again_button)
        self.message_back_button = QPushButton("Back to the drive list")
        self.message_back_button.setObjectName("primary")
        self.message_back_button.clicked.connect(self._back_to_drives)
        buttons.addWidget(self.message_back_button)
        buttons.addStretch(1)
        quit_button = QPushButton("Quit")
        quit_button.clicked.connect(self.close)
        buttons.addWidget(quit_button)
        layout.addLayout(buttons)
        layout.addStretch(1)
        self.page_message = page

    def _show_message(self, message, *, try_again=False):
        self.message_label.setText(message)
        self.try_again_button.setVisible(try_again)
        self._show(self.page_message)

    # ------------------------------------------------------------------
    # Window behavior
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        # A wipe in flight is never interrupted by closing the
        # window — the drive's state depends on it finishing.
        if self._wiping:
            event.ignore()
            return
        super().closeEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.stack.currentWidget() in (
            self.page_identity,
            self.page_scope,
            self.page_final,
        ):
            self._cancel_gates()
            event.accept()
            return
        super().keyPressEvent(event)


class RefusalWindow(QMainWindow):
    """The designed refusal: the builder said no, in its own words."""

    def __init__(self, message, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Beerus — Live Boot")
        self.setMinimumSize(640, 320)
        self.setStyleSheet(STYLESHEET)
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("BEERUS — Live Boot")
        title.setObjectName("brand")
        layout.addWidget(title)
        self.message_label = QLabel(message)
        self.message_label.setObjectName("warning")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.quit_button = QPushButton("Quit")
        self.quit_button.setObjectName("primary")
        self.quit_button.clicked.connect(self.close)
        buttons.addWidget(self.quit_button)
        layout.addLayout(buttons)
        layout.addStretch(1)
        self.setCentralWidget(page)


def _default_app_factory(args):
    return QApplication.instance() or QApplication(args)


def main(argv=None, *, engine_factory=None, app_factory=None) -> int:
    """Boot-UI entry point. Returns the process exit code.

    Exit codes: 0 for a session that ran (whatever the operator
    did in it), 2 for a refusal to start (the builder's designed
    refusal, or any other failure while the live session is
    being built — shown in a refusal window, never a traceback),
    and 76 when no graphical session could be started at all —
    the launcher's signal to fall back to the text console.

    Test seams: ``engine_factory`` replaces
    ``beerus.live.build_live_engine`` (the one arming site) and
    is called with no arguments; ``app_factory`` replaces
    ``QApplication`` and is called with the argument list. A fake
    app whose ``exec()`` returns 0 drives a full successful run
    without a display.
    """
    if app_factory is None:
        app_factory = _default_app_factory
    args = list(sys.argv) if argv is None else list(argv)
    try:
        app = app_factory(args)
    except Exception:
        print(
            "The graphical session could not start on this machine.",
            file=sys.stderr,
        )
        return EXIT_NO_GRAPHICAL
    if engine_factory is None:
        engine_factory = build_live_engine
    try:
        engine, authority, documents_dir = engine_factory()
    except RefusalError as exc:
        refusal_message = exc.user_message
    except BeerusError as exc:
        # A designed Beerus error carries its own plain-language
        # message; it is shown as it stands.
        refusal_message = _error_text(exc)
    except Exception as exc:
        # In the boot context there is no traceback channel: any
        # other failure while building the live session lands on
        # the designed refusal window too, with an honest plain
        # line and the error's own text — never a traceback.
        refusal_message = (
            "Beerus could not start on this machine, so nothing "
            "will be wiped."
        )
        if str(exc):
            refusal_message += f"\n{exc}"
    else:
        refusal_message = None
    if refusal_message is not None:
        window = RefusalWindow(refusal_message)
        window.show()
        app.exec()
        return EXIT_REFUSAL
    window = BootWindow(engine, authority, documents_dir)
    window.show()
    code = app.exec()
    return EXIT_OK if code == 0 else int(code)


if __name__ == "__main__":
    raise SystemExit(main())
