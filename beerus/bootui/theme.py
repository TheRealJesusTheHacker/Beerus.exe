"""The Beerus boot UI palette and stylesheet — BEERUS'S OWN.

This is not the fleet's shared green/black. The boot UI wears
its namesake's identity: Beerus, God of Destruction — deep
violet-black, Beerus violet, and destruction gold. The palette
is the owner's own call, a namesake exception he made for this
tool — purple for Beerus, in his words, "because it's
appropriate" (2026-10-20) — recorded here so no future sweep
or editor "corrects" Beerus back to fleet colors. It
deliberately differs from ``beerus_portable.theme`` (the
shipped portable app keeps the fleet palette; this one does
not, and a test pins the difference).

Violet-on-black is worked for, not assumed: every
text/background pairing used here is held to a WCAG contrast
ratio of at least 4.5:1, computed from these constants by the
palette test, so a palette edit that breaks contrast fails the
suite instead of shipping. Red appears only as the erase
action's danger accent, in the exact pairing the portable app
already proved — and no highlight in this palette sits close
enough to that red to be mistaken for it.

The constants below are the canonical namesake set for Beerus
from the fleet palette program (2026-10-20) — the same values
the program proved on its renders (worst pairing 5.89:1). They
are pinned literally by the palette test; do not retune them
locally.
"""

from __future__ import annotations

BG = "#0a0614"  # deep violet-black — the void, tinted with his color
PANEL = "#150d24"  # raised panels
PANEL_BORDER = "#2e2150"  # deep violet
TEXT = "#f2edfa"  # hot lavender-white
TEXT_FAINT = "#a99cc9"  # muted lavender-grey (>= 4.5:1 on BG/PANEL)
VIOLET = "#a478f7"  # Beerus violet: primary actions, headings, progress
VIOLET_DARK_TEXT = "#12081f"  # text on the violet itself
GOLD = "#e0aa3e"  # destruction gold: the second signature (titles, serial, PASS)
# The canonical hot highlight (10.09:1 on BG / 9.49:1 on PANEL,
# computed by the palette test): the lightest violet in the set,
# held for emphasis elements. Pinned and proven with the rest of
# the set so the full canonical palette lives in this one module.
ENERGY = "#d0a6ff"
INPUT_BG = "#0e0819"
# Disabled controls are exempt from WCAG contrast rules, but the
# house bar is a readable render: this still clears 4.5:1.
DISABLED_TEXT = "#948ab5"  # lavender-grey
RED = "#b03a2e"  # danger only: the final erase action (unchanged pairing)
RED_TEXT = "#ffffff"  # text on the red accent

# Every (foreground, background) pairing the stylesheet renders
# text in. The palette test asserts each of these is >= 4.5:1.
TEXT_PAIRS = (
    (TEXT, BG),
    (TEXT, PANEL),
    (TEXT, INPUT_BG),
    (TEXT, PANEL_BORDER),  # the selected drive row: violet wash, TEXT on it
    (TEXT_FAINT, BG),
    (TEXT_FAINT, PANEL),
    (VIOLET, BG),
    (VIOLET, PANEL),
    (VIOLET_DARK_TEXT, VIOLET),
    (GOLD, BG),
    (GOLD, PANEL),
    (RED_TEXT, RED),
    (DISABLED_TEXT, PANEL),
    (DISABLED_TEXT, BG),
)


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG contrast ratio between two #rrggbb colors."""

    def luminance(hex_color: str) -> float:
        h = hex_color.lstrip("#")
        channels = [int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4)]

        def linear(c: float) -> float:
            return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

        r, g, b = (linear(c) for c in channels)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    l1 = luminance(foreground)
    l2 = luminance(background)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


STYLESHEET = f"""
QMainWindow, QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: "Segoe UI", "DejaVu Sans", "Helvetica Neue", sans-serif;
    font-size: 14px;
}}
QLabel#brand {{
    font-size: 26px;
    font-weight: bold;
    color: {GOLD};
}}
QLabel#title {{
    font-size: 26px;
    font-weight: bold;
    color: {VIOLET};
}}
QLabel#subtitle {{
    color: {TEXT_FAINT};
    font-size: 14px;
}}
QLabel#identity {{
    font-size: 22px;
    font-weight: bold;
    color: {TEXT};
}}
QLabel#serial {{
    font-size: 20px;
    font-weight: bold;
    color: {GOLD};
}}
QLabel#warning {{
    font-size: 16px;
    font-weight: bold;
    color: {TEXT};
}}
QLabel#verdictPass {{
    font-size: 22px;
    font-weight: bold;
    color: {GOLD};
}}
QLabel#verdictBad {{
    font-size: 22px;
    font-weight: bold;
    color: {TEXT};
}}
QFrame#panel {{
    background-color: {PANEL};
    border: 1px solid {PANEL_BORDER};
    border-radius: 8px;
}}
QListWidget {{
    background-color: {PANEL};
    border: 1px solid {PANEL_BORDER};
    border-radius: 8px;
    padding: 6px;
    outline: 0;
}}
QListWidget::item {{
    padding: 10px;
    border-radius: 6px;
    color: {TEXT};
}}
QListWidget::item:selected {{
    background-color: {PANEL_BORDER};
    color: {TEXT};
}}
QListWidget::item:disabled {{
    color: {TEXT_FAINT};
}}
QLineEdit {{
    background-color: {INPUT_BG};
    border: 1px solid {PANEL_BORDER};
    border-radius: 6px;
    padding: 8px;
    color: {TEXT};
    selection-background-color: {PANEL_BORDER};
}}
QPlainTextEdit {{
    background-color: {PANEL};
    border: 1px solid {PANEL_BORDER};
    border-radius: 8px;
    padding: 8px;
    color: {TEXT};
}}
QPushButton {{
    background-color: {PANEL};
    border: 1px solid {PANEL_BORDER};
    border-radius: 6px;
    padding: 10px 18px;
    color: {TEXT};
}}
QPushButton:hover {{
    border-color: {GOLD};
}}
QPushButton:disabled {{
    color: {DISABLED_TEXT};
    border-color: {PANEL};
}}
QPushButton#primary {{
    background-color: {VIOLET};
    color: {VIOLET_DARK_TEXT};
    font-weight: bold;
    border: none;
}}
QPushButton#primary:disabled {{
    background-color: {PANEL};
    color: {DISABLED_TEXT};
}}
QPushButton#danger {{
    background-color: {RED};
    color: {RED_TEXT};
    font-weight: bold;
    border: none;
}}
QProgressBar {{
    background-color: {PANEL};
    border: 1px solid {PANEL_BORDER};
    border-radius: 6px;
    height: 22px;
    text-align: center;
    color: {TEXT};
}}
QProgressBar::chunk {{
    background-color: {VIOLET};
    border-radius: 5px;
}}
QRadioButton {{
    color: {TEXT};
    spacing: 8px;
    padding: 6px 4px;
}}
QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 8px;
    border: 2px solid {TEXT_FAINT};
    background-color: {INPUT_BG};
}}
QRadioButton::indicator:checked {{
    border-color: {VIOLET};
    background-color: {VIOLET};
}}
QComboBox {{
    background-color: {INPUT_BG};
    border: 1px solid {PANEL_BORDER};
    border-radius: 6px;
    padding: 8px;
    color: {TEXT};
}}
QComboBox QAbstractItemView {{
    background-color: {PANEL};
    color: {TEXT};
    selection-background-color: {PANEL_BORDER};
}}
"""
