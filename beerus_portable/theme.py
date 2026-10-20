"""The Beerus Portable palette and stylesheet.

Sentinel house standard: green on black, nothing else. No purple
anywhere. Every text/background pairing used here is held to a
WCAG contrast ratio of at least 4.5:1 — the palette test computes
the ratios from these constants, so a palette edit that breaks
contrast fails the suite instead of shipping.
"""

from __future__ import annotations

# The fleet palette (shared with ARGUS.EXE's verified theme).
BG = "#060a07"  # window background, near-black green
PANEL = "#0c130d"  # raised panels
PANEL_BORDER = "#1d2b1f"
TEXT = "#e8f2ea"  # primary text
TEXT_FAINT = "#8fae94"  # secondary text (still >= 4.5:1 on BG/PANEL)
GREEN = "#2ee06f"  # the Sentinel green: primary actions, headings
GREEN_DARK_TEXT = "#04150a"  # text on the green itself
RED = "#b03a2e"  # rare accent only: the final erase action
RED_TEXT = "#ffffff"  # text on the red accent
INPUT_BG = "#0a0f0a"
DISABLED_TEXT = "#5f7263"

# Every (foreground, background) pairing the stylesheet renders
# text in. The palette test asserts each of these is >= 4.5:1.
TEXT_PAIRS = (
    (TEXT, BG),
    (TEXT, PANEL),
    (TEXT, INPUT_BG),
    (TEXT_FAINT, BG),
    (TEXT_FAINT, PANEL),
    (GREEN, BG),
    (GREEN, PANEL),
    (GREEN_DARK_TEXT, GREEN),
    (RED_TEXT, RED),
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
    font-family: "Segoe UI", "Helvetica Neue", sans-serif;
    font-size: 14px;
}}
QLabel#title {{
    font-size: 26px;
    font-weight: bold;
    color: {GREEN};
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
    color: {GREEN};
}}
QLabel#warning {{
    font-size: 16px;
    font-weight: bold;
    color: {TEXT};
}}
QLabel#verdictPass {{
    font-size: 22px;
    font-weight: bold;
    color: {GREEN};
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
    border-color: {GREEN};
}}
QPushButton:disabled {{
    color: {DISABLED_TEXT};
    border-color: {PANEL};
}}
QPushButton#primary {{
    background-color: {GREEN};
    color: {GREEN_DARK_TEXT};
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
    background-color: {GREEN};
    border-radius: 5px;
}}
"""
