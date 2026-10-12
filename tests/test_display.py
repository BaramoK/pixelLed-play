"""Unit tests for the exit helpers (no BLE / no device required).

``Panel.__new__`` is used to bypass the constructor (which would open a real
Bluetooth connection) and inject a fake client that records the payloads, so
:func:`blank_panel` can be checked without hardware.
"""

import io

from PIL import Image

from pixelled_lib.library.panel import Panel
from pixelled_play.display import BLANK_COLOR, blank_panel


class FakeClient:
    """Records calls to ``send_image_hex`` instead of talking to hardware."""

    def __init__(self):
        self.calls = []

    def send_image_hex(self, hex_string, extension, *args, **kwargs):
        self.calls.append((hex_string, extension))


class FailingClient:
    """Fails every send, like a panel that is already gone."""

    def send_image_hex(self, *args, **kwargs):
        raise RuntimeError("panel is gone")


def _make_panel(width=32, height=32, client=None):
    panel = Panel.__new__(Panel)  # bypass __init__ (no BLE)
    panel._client = client if client is not None else FakeClient()
    panel.width = width
    panel.height = height
    panel.led_type = "RGB"
    panel.device_info = None
    return panel


def _decode(hex_string):
    return Image.open(io.BytesIO(bytes.fromhex(hex_string))).convert("RGB")


def _pixels(image):
    for y in range(image.height):
        for x in range(image.width):
            yield image.getpixel((x, y))


def test_blank_panel_sends_one_all_black_frame():
    panel = _make_panel(width=32, height=32)
    blank_panel(panel)

    assert len(panel._client.calls) == 1
    hex_string, extension = panel._client.calls[0]
    assert extension == ".png"
    image = _decode(hex_string)
    assert image.size == (32, 32)
    assert all(pixel == BLANK_COLOR for pixel in _pixels(image))


def test_blank_panel_matches_the_panel_size():
    panel = _make_panel(width=64, height=64)
    blank_panel(panel)

    image = _decode(panel._client.calls[0][0])
    assert image.size == (64, 64)
    assert all(pixel == BLANK_COLOR for pixel in _pixels(image))


def test_blank_panel_is_best_effort():
    # A send failure on the shutdown path must not raise.
    panel = _make_panel(client=FailingClient())
    blank_panel(panel)
