"""Helpers that leave the LED panel in a clean state when a session ends.

The matrix keeps displaying the last frame it received, so a clean BLE
disconnect - which :class:`pixelled_lib.library.panel.Panel` already performs
through its context manager - is *not* enough: the panel would stay frozen on
the last game/menu frame. Pushing an all-black frame just before the connection
is closed turns the display off cleanly instead.
"""

from __future__ import annotations

from PIL import Image

from pixelled_lib.library.panel import Panel

Color = tuple[int, int, int]

# The "screen off" colour: an all-black frame blanks the matrix.
BLANK_COLOR: Color = (0, 0, 0)

__all__ = ["BLANK_COLOR", "blank_panel"]


def blank_panel(panel: Panel) -> None:
    """Send an all-black frame so the panel stops showing the last frame.

    The panel keeps the last frame it received, so this is what makes a clean
    exit actually clear the display (disconnecting alone does not).

    Best effort: this runs on a shutdown path where the panel may already be
    gone, so a send failure is swallowed instead of masking the real exit
    reason.
    """
    try:
        panel.send_frame(Image.new("RGB", panel.size, BLANK_COLOR))
    except Exception:
        pass
