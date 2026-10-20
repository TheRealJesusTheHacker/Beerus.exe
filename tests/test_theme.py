"""Palette tests for Beerus Portable.

Beerus wears its OWN palette — deep violet-black, Beerus
violet, destruction gold — by the owner's namesake exception
for this tool (2026-10-20: purple for Beerus "because it's
appropriate"). Since v0.6.1 the portable carries the same
canonical identity as the boot UI. The disciplines pinned
here:

- the exact constants are literal-pinned, so a silent palette
  drift fails the suite;
- every text pairing the stylesheet renders clears the 4.5:1
  contrast floor (violet-on-black is proved, not assumed);
- this palette AGREES with the boot UI's, constant for
  constant — one tool, one identity, so the two faces of
  Beerus cannot drift apart in dress;
- the violet is present ON PURPOSE (a future sweep draining
  Beerus back to fleet colors fails loudly here), and the
  fleet green is gone;
- the danger red is the fleet's exact pairing, unchanged.
"""

from __future__ import annotations

from beerus.bootui import theme as bootui_theme
from beerus_portable import theme

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


def _palette(module):
    return {
        name: getattr(module, name)
        for name in dir(module)
        if name.isupper()
        and isinstance(getattr(module, name), str)
        and getattr(module, name).startswith("#")
    }


def test_the_palette_constants_are_pinned():
    assert _palette(theme) == _PINNED


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


def test_this_palette_is_the_boot_uis():
    # One tool, one identity (v0.6.1): the portable app and the
    # boot UI pin the same canonical namesake set. If either
    # module's constants move, this test — and its mirror in
    # the boot UI's palette tests — fails until both agree
    # again.
    assert _palette(theme) == _palette(bootui_theme)
    assert theme.TEXT_PAIRS == bootui_theme.TEXT_PAIRS


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


def test_the_fleet_green_is_gone():
    # The portable wore the fleet green/black until v0.6.0.
    # Beerus's own identity replaced it: the fleet green and
    # the old near-black green background appear nowhere in
    # the palette or the rendered stylesheet.
    palette = _palette(theme)
    assert "GREEN" not in palette
    assert "#2ee06f" not in palette.values()
    assert "#2ee06f" not in theme.STYLESHEET
    assert "#060a07" not in palette.values()
