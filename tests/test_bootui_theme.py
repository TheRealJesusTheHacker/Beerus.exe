"""Palette tests for the Beerus boot UI.

The boot UI wears Beerus's OWN palette — deep violet-black,
Beerus violet, destruction gold — by the owner's namesake
exception for this tool (2026-10-20: purple for Beerus
"because it's appropriate"). Three disciplines are pinned
here:

- every text pairing the stylesheet renders clears the 4.5:1
  contrast floor (violet-on-black is proved, not assumed);
- the exact constants are literal-pinned, so a silent palette
  drift fails the suite;
- the violet is present ON PURPOSE: the primary constant is
  purple-dominant by design, asserted here so a future sweep
  draining it back to fleet colors fails loudly instead of
  silently "fixing" Beerus.
"""

from __future__ import annotations

from beerus.bootui import theme
from beerus_portable import theme as portable_theme

_PINNED = {
    "BG": "#0a0614",
    "PANEL": "#150d24",
    "PANEL_BORDER": "#2e2150",
    "TEXT": "#f2edfa",
    "TEXT_FAINT": "#a99cc9",
    "VIOLET": "#a478f7",
    "VIOLET_DARK_TEXT": "#12081f",
    "GOLD": "#e0aa3e",
    "ENERGY": "#d0a6ff",
    "INPUT_BG": "#0e0819",
    "DISABLED_TEXT": "#948ab5",
    "RED": "#b03a2e",
    "RED_TEXT": "#ffffff",
}


def _palette():
    return {
        name: getattr(theme, name)
        for name in dir(theme)
        if name.isupper()
        and isinstance(getattr(theme, name), str)
        and getattr(theme, name).startswith("#")
    }


def test_the_palette_constants_are_pinned():
    assert _palette() == _PINNED


def test_every_text_pair_meets_the_contrast_floor():
    for foreground, background in theme.TEXT_PAIRS:
        ratio = theme.contrast_ratio(foreground, background)
        assert ratio >= 4.5, (
            f"{foreground} on {background} is {ratio:.2f}:1 — "
            "below the 4.5:1 floor"
        )


def test_the_energy_highlight_meets_the_contrast_floor():
    # ENERGY is the canonical set's hot highlight; its pairings
    # (on BG, on PANEL) are proven here even though the current
    # stylesheet spends it sparingly — the whole canonical set
    # is held to the floor, not just the pairs in TEXT_PAIRS.
    for background in (theme.BG, theme.PANEL):
        ratio = theme.contrast_ratio(theme.ENERGY, background)
        assert ratio >= 4.5, (
            f"ENERGY on {background} is {ratio:.2f}:1 — "
            "below the 4.5:1 floor"
        )


def test_the_violet_primary_is_purple_dominant_by_design():
    # The presence/intent guard: Beerus's primary IS purple —
    # blue and red both well above green, the violet signature.
    # If a palette sweep ever drains this back toward the fleet
    # green, this test is the loud failure that stops it.
    h = theme.VIOLET.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    assert b > g + 40 and r > g + 20, (
        f"VIOLET={theme.VIOLET} (r={r}, g={g}, b={b}) is not "
        "the purple-dominant Beerus violet the owner approved"
    )


def test_this_palette_is_the_portables():
    # One tool, one identity: until v0.6.0 the portable kept
    # the fleet palette and this test pinned the difference.
    # In v0.6.1 the portable joined the boot UI's canonical
    # namesake set (the owner's exception for Beerus,
    # 2026-10-20), and the pin now runs the other way — the
    # two faces of Beerus agree, constant for constant.
    assert theme.VIOLET == portable_theme.VIOLET
    assert theme.BG == portable_theme.BG
    assert theme.TEXT == portable_theme.TEXT
    assert _palette() == {
        name: getattr(portable_theme, name)
        for name in dir(portable_theme)
        if name.isupper()
        and isinstance(getattr(portable_theme, name), str)
        and getattr(portable_theme, name).startswith("#")
    }


def test_font_stack_names_the_live_images_font():
    # The live image carries DejaVu Sans (fonts-dejavu-core in
    # the image definition); the boot UI's stack names it
    # between the Windows faces and the generic fallback.
    assert '"DejaVu Sans"' in theme.STYLESHEET
