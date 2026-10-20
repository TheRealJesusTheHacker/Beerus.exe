"""Palette tests for Beerus Portable.

Two brand laws, enforced by the suite: every text/background
pairing the stylesheet renders meets the fleet's 4.5:1 contrast
floor, and no palette color is a purple — green and black, with
red only as the rare danger accent.
"""

from __future__ import annotations

import colorsys

from beerus_portable import theme


def test_every_text_pair_meets_the_contrast_floor():
    for foreground, background in theme.TEXT_PAIRS:
        ratio = theme.contrast_ratio(foreground, background)
        assert ratio >= 4.5, (
            f"{foreground} on {background} is {ratio:.2f}:1 — "
            "below the 4.5:1 floor"
        )


def test_no_purple_anywhere_in_the_palette():
    palette = {
        name: getattr(theme, name)
        for name in dir(theme)
        if name.isupper() and isinstance(getattr(theme, name), str)
        and getattr(theme, name).startswith("#")
    }
    assert palette, "palette constants not found"
    for name, hex_color in palette.items():
        h = hex_color.lstrip("#")
        r, g, b = (int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
        hue, _lightness, saturation = colorsys.rgb_to_hls(r, g, b)
        hue_degrees = hue * 360.0
        if saturation <= 0.15:
            continue  # near-neutral: no hue to object to
        in_green = 80 <= hue_degrees <= 175
        in_red = hue_degrees <= 20 or hue_degrees >= 345
        assert in_green or in_red, (
            f"{name}={hex_color} has hue {hue_degrees:.0f}° — "
            "outside the green/black/red brand"
        )
