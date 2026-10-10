"""A small Tetris demo rendered on the LED panel.

:class:`Tetris` holds the whole game state and is pure (no BLE, no I/O), so it
can be unit tested without hardware - exactly like
:mod:`pixelled_play.games.breakout`.
:func:`run_tetris` wires it to a real panel: the field and the HUD (next piece,
score, level) are drawn on the LED matrix, and a *minimal* tkinter window is
used only to capture keyboard input.

Geometry: a block is always 2x2 px (:data:`BLOCK_W` x :data:`BLOCK_H`) - the
game is never scaled, whatever the panel size - and a piece is made of 4 such
blocks, so it fits in a 3x2 block volume at most (``I`` is the usual 4x1
exception). The field is :data:`FIELD_COLS` blocks wide and as many rows as the
panel height allows, with a :data:`HUD_W` px band on its right. On a 32x32 panel
that fills the matrix exactly: a 10x16 block field (20x32 px) plus a 12 px HUD.

The game starts by itself: the opening piece is spawned in ``READY`` and the
first :meth:`Tetris.step` makes it fall, so no key press is needed to play.
"""

from __future__ import annotations

import random
from typing import Optional

from PIL import Image, ImageDraw

from pixelled_lib.library.keys import KeyRepeater
from pixelled_lib.library.panel import Panel, use_panel
from pixelled_lib.library.sender import FrameSender

__all__ = ["Tetris", "run_tetris"]

Color = tuple[int, int, int]

# A block is always 2x2 px (:data:`BLOCK_W` x :data:`BLOCK_H`), whatever the
# panel size - the game is never scaled up or down.
BLOCK_W = 2
BLOCK_H = 2

FIELD_COLS = 10  # classic Tetris width
MIN_FIELD_COLS = 4  # an I piece is 4 blocks wide
MIN_FIELD_ROWS = 4
HUD_W = 12  # width of the HUD band, in px (6 blocks at 2x2 px)
PREVIEW_CELLS = 4  # size of the "next piece" preview box, in blocks

_SQUARE = ((0, 0), (1, 0), (0, 1), (1, 1))

# The 7 tetrominoes, each with its 4 rotations pre-computed as ``(dx, dy)``
# offsets inside a box whose top-left corner is ``(0, 0)`` - no matrix maths at
# run time, which keeps collisions and rendering trivial.
TETROMINOES: dict[str, tuple[tuple[tuple[int, int], ...], ...]] = {
    "I": (
        ((0, 1), (1, 1), (2, 1), (3, 1)),
        ((2, 0), (2, 1), (2, 2), (2, 3)),
        ((0, 2), (1, 2), (2, 2), (3, 2)),
        ((1, 0), (1, 1), (1, 2), (1, 3)),
    ),
    "O": (_SQUARE,) * 4,
    "T": (
        ((1, 0), (0, 1), (1, 1), (2, 1)),
        ((1, 0), (1, 1), (2, 1), (1, 2)),
        ((0, 1), (1, 1), (2, 1), (1, 2)),
        ((1, 0), (0, 1), (1, 1), (1, 2)),
    ),
    "S": (
        ((1, 0), (2, 0), (0, 1), (1, 1)),
        ((1, 0), (1, 1), (2, 1), (2, 2)),
        ((1, 1), (2, 1), (0, 2), (1, 2)),
        ((0, 0), (0, 1), (1, 1), (1, 2)),
    ),
    "Z": (
        ((0, 0), (1, 0), (1, 1), (2, 1)),
        ((2, 0), (1, 1), (2, 1), (1, 2)),
        ((0, 1), (1, 1), (1, 2), (2, 2)),
        ((1, 0), (0, 1), (1, 1), (0, 2)),
    ),
    "J": (
        ((0, 0), (0, 1), (1, 1), (2, 1)),
        ((1, 0), (2, 0), (1, 1), (1, 2)),
        ((0, 1), (1, 1), (2, 1), (2, 2)),
        ((1, 0), (1, 1), (0, 2), (1, 2)),
    ),
    "L": (
        ((2, 0), (0, 1), (1, 1), (2, 1)),
        ((1, 0), (1, 1), (1, 2), (2, 2)),
        ((0, 1), (1, 1), (2, 1), (0, 2)),
        ((0, 0), (1, 0), (1, 1), (1, 2)),
    ),
}

PIECE_COLORS: dict[str, Color] = {
    "I": (0, 200, 255),
    "O": (255, 220, 0),
    "T": (180, 0, 255),
    "S": (0, 220, 0),
    "Z": (255, 0, 0),
    "J": (0, 80, 255),
    "L": (255, 140, 0),
}

HUD_DIVIDER_COLOR: Color = (60, 60, 60)
HUD_SCORE_COLOR: Color = (0, 200, 0)
HUD_LEVEL_COLOR: Color = (255, 255, 0)
HUD_OVER_COLOR: Color = (255, 0, 0)
HUD_PAUSE_COLOR: Color = (255, 255, 255)

# Scoring (classic values) and gravity.
_LINE_SCORES = (0, 100, 300, 500, 800)
_LINES_PER_LEVEL = 10
_SOFT_DROP_POINTS = 1
_HARD_DROP_POINTS = 2
_SCORE_PER_BAR_PIXEL = 100  # the score bar grows by 1 px per 100 points

_BASE_DROP_TICKS = 10  # gravity period at level 1, in ticks (1 row/s at 10 fps)
_DROP_ACCEL = 0.85  # gravity period multiplier per level

# Wall kicks tried in order when a rotation would collide (classic-ish; the
# downward kick lets a piece rotate when it is sitting at the very top).
_KICKS = ((0, 0), (-1, 0), (1, 0), (-2, 0), (2, 0), (0, -1), (0, 1))


def _fit_geometry(width: int, height: int) -> tuple[int, int, int, int]:
    """Return ``(cell_w, cell_h, cols, rows)`` for a panel of that size.

    Blocks are always :data:`BLOCK_W` x :data:`BLOCK_H` px (2x2), so the field is
    :data:`FIELD_COLS` blocks wide and as many rows as the panel height allows;
    a panel too narrow for 10 columns simply gets fewer of them (never less than
    :data:`MIN_FIELD_COLS`, so an ``I`` piece always fits). Only when the panel
    is too small even for that do the blocks shrink to 1x1 px.
    """
    for cell_w, cell_h in ((BLOCK_W, BLOCK_H), (1, 1)):
        cols = min(FIELD_COLS, width // cell_w)
        rows = height // cell_h
        if cols >= MIN_FIELD_COLS and rows >= MIN_FIELD_ROWS:
            return cell_w, cell_h, cols, rows
    # Absurdly small panel: one block per pixel, whatever fits.
    return 1, 1, max(1, min(FIELD_COLS, width)), max(1, height)


class Tetris:
    """A single-player Tetris game.

    The whole game state (field, falling piece, score, level) lives here and is
    turned into an image by :meth:`render`; nothing in this class touches the
    panel.

    Parameters
    ----------
    width, height:
        Panel size in pixels.
    level:
        Starting level; gravity gets faster as the level rises.
    seed:
        Optional RNG seed. With a seed the piece sequence is reproducible.
    hud:
        When true the right part of the panel is used as a HUD band (next piece
        preview, score bar, level bar). The field itself is identical either way.
    """

    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    GAME_OVER = "game_over"

    def __init__(
        self,
        width: int,
        height: int,
        level: int = 1,
        seed: Optional[int] = None,
        hud: bool = True,
    ) -> None:
        self.width = int(width)
        self.height = int(height)
        self.hud = bool(hud)
        self.initial_level = max(1, int(level))
        self.cell_w, self.cell_h, self.cols, self.rows = _fit_geometry(
            self.width, self.height
        )
        field_w = self.cols * self.cell_w
        # The HUD band is a fixed width band; when the panel is too narrow to
        # hold it next to the field, it is dropped and the field is centred.
        self.hud_w = HUD_W if (self.hud and self.width - field_w >= HUD_W) else 0
        self.field_x = (self.width - field_w - self.hud_w) // 2
        self.field_y = (self.height - self.rows * self.cell_h) // 2
        self.hud_x = self.field_x + field_w
        self.reset(seed=seed)

    # -- geometry ----------------------------------------------------------

    @property
    def field_width(self) -> int:
        """Width of the playfield in pixels."""
        return self.cols * self.cell_w

    @property
    def field_height(self) -> int:
        """Height of the playfield in pixels."""
        return self.rows * self.cell_h

    @property
    def hud_content_x(self) -> int:
        """X of the first HUD pixel after the separator column."""
        return self.hud_x + 1

    @property
    def hud_content_w(self) -> int:
        """Width available for the HUD content, separator excluded."""
        return max(0, self.hud_w - 1)

    @property
    def drop_ticks(self) -> int:
        """Ticks between two gravity steps, at the current level."""
        return max(1, int(round(_BASE_DROP_TICKS * _DROP_ACCEL ** (self.level - 1))))

    # -- game state --------------------------------------------------------

    def reset(self, seed: Optional[int] = None) -> None:
        """Start a new game: empty field, score 0, level back to the start.

        The opening piece is already on the board in ``READY``; the first
        :meth:`step` promotes the game to ``RUNNING`` and it falls on its own.
        """
        self._rng = random.Random(seed)
        self._bag: list[str] = []
        self.grid: list[list[Optional[Color]]] = [
            [None] * self.cols for _ in range(self.rows)
        ]
        self.score = 0
        self.lines = 0
        self.level = self.initial_level
        self.state = self.READY
        self.piece: Optional[str] = None
        self.piece_x = 0
        self.piece_y = 0
        self.rotation = 0
        self._drop_counter = 0
        self.next_piece = self._next_piece()
        self.spawn()

    def start(self) -> None:
        """Start falling; no-op unless the game is waiting in ``READY``.

        The game also starts by itself on the first :meth:`step`, so no key
        press is needed to make the opening piece fall.
        """
        if self.state == self.READY:
            self.state = self.RUNNING

    def toggle_pause(self) -> None:
        """Pause a running game, or resume a paused one."""
        if self.state == self.RUNNING:
            self.state = self.PAUSED
        elif self.state == self.PAUSED:
            self.state = self.RUNNING

    # -- pieces ------------------------------------------------------------

    def piece_cells(self) -> list[tuple[int, int]]:
        """Absolute ``(x, y)`` blocks of the falling piece, row 0 being the top."""
        if self.piece is None:
            return []
        return [
            (self.piece_x + dx, self.piece_y + dy)
            for dx, dy in TETROMINOES[self.piece][self.rotation]
        ]

    def _next_piece(self) -> str:
        """Pop the next piece from a shuffled 7-bag (seed reproducible)."""
        if not self._bag:
            self._bag = list(TETROMINOES)
            self._rng.shuffle(self._bag)
        return self._bag.pop()

    def spawn(self) -> bool:
        """Bring the queued piece into play.

        The piece appears centred and flush with the top of the field. Returns
        False (and sets :attr:`state` to ``GAME_OVER``) when there is no room
        left for it.
        """
        self.piece = self.next_piece
        self.next_piece = self._next_piece()
        self.rotation = 0
        self._drop_counter = 0
        cells = TETROMINOES[self.piece][0]
        box_w = max(dx for dx, _ in cells) + 1
        self.piece_x = (self.cols - box_w) // 2
        self.piece_y = -min(dy for _, dy in cells)
        if self._collides(self.piece_cells()):
            self.state = self.GAME_OVER
            return False
        return True

    def _collides(self, cells: list[tuple[int, int]]) -> bool:
        """True when any of ``cells`` is outside the field or already filled."""
        for x, y in cells:
            if x < 0 or x >= self.cols or y < 0 or y >= self.rows:
                return True
            if self.grid[y][x] is not None:
                return True
        return False

    # -- player actions ----------------------------------------------------

    def move(self, dx: int, dy: int = 0) -> bool:
        """Shift the falling piece by ``(dx, dy)`` blocks; returns success."""
        if self.piece is None or self.state in (self.PAUSED, self.GAME_OVER):
            return False
        cells = [(x + dx, y + dy) for x, y in self.piece_cells()]
        if self._collides(cells):
            return False
        self.piece_x += dx
        self.piece_y += dy
        return True

    def soft_drop(self) -> bool:
        """Move the piece down one block, scoring one point per block."""
        if self.state not in (self.READY, self.RUNNING):
            return False
        if not self.move(0, 1):
            return False
        self.score += _SOFT_DROP_POINTS
        self._drop_counter = 0
        return True

    def rotate(self, cw: bool = True) -> bool:
        """Rotate the piece, trying the wall kicks of :data:`_KICKS` in order."""
        if self.piece is None or self.state in (self.PAUSED, self.GAME_OVER):
            return False
        rotation = (self.rotation + (1 if cw else -1)) % len(TETROMINOES[self.piece])
        offsets = [
            (self.piece_x + dx, self.piece_y + dy)
            for dx, dy in TETROMINOES[self.piece][rotation]
        ]
        for kick_x, kick_y in _KICKS:
            cells = [(x + kick_x, y + kick_y) for x, y in offsets]
            if not self._collides(cells):
                self.rotation = rotation
                self.piece_x += kick_x
                self.piece_y += kick_y
                return True
        return False

    def hard_drop(self) -> int:
        """Drop the piece to the bottom, lock it and score the distance."""
        if self.piece is None or self.state not in (self.READY, self.RUNNING):
            return 0
        distance = 0
        while self.move(0, 1):
            distance += 1
        self.score += distance * _HARD_DROP_POINTS
        self._lock()
        return distance

    def step(self) -> None:
        """Advance the simulation by one tick: gravity, then locking.

        A freshly reset game is ``READY``: the first tick promotes it to
        ``RUNNING`` so the opening piece starts falling by itself (the counter
        is then reset by that first tick, giving it one gravity period of
        grace before it moves).
        """
        if self.state == self.READY:
            self.start()
        if self.state != self.RUNNING:
            return
        self._drop_counter += 1
        if self._drop_counter < self.drop_ticks:
            return
        self._drop_counter = 0
        if not self.move(0, 1):
            self._lock()

    # -- locking and scoring -----------------------------------------------

    def _lock(self) -> None:
        """Write the falling piece into the field, clear lines, spawn the next."""
        if self.piece is None:
            return
        color = PIECE_COLORS[self.piece]
        for x, y in self.piece_cells():
            if 0 <= x < self.cols and 0 <= y < self.rows:
                self.grid[y][x] = color
        self._clear_lines()
        self.spawn()

    def _clear_lines(self) -> int:
        """Remove the full rows, shift the rest down and update the score."""
        remaining = [row for row in self.grid if any(cell is None for cell in row)]
        cleared = self.rows - len(remaining)
        if cleared:
            self.grid = [[None] * self.cols for _ in range(cleared)] + remaining
            self.lines += cleared
            self.score += _LINE_SCORES[min(cleared, len(_LINE_SCORES) - 1)] * self.level
            self.level = self.initial_level + self.lines // _LINES_PER_LEVEL
        return cleared

    # -- rendering ---------------------------------------------------------

    def render(self) -> Image.Image:
        """Return the current frame as an ``RGB`` image of the panel size."""
        img = Image.new("RGB", (self.width, self.height), (0, 0, 0))
        draw = ImageDraw.Draw(img)
        for y, row in enumerate(self.grid):
            for x, color in enumerate(row):
                if color is not None:
                    self._draw_block(draw, x, y, color)
        if self.piece is not None:
            color = PIECE_COLORS[self.piece]
            for x, y in self.piece_cells():
                self._draw_block(draw, x, y, color)
        if self.hud_w > 0:
            self._draw_hud(draw)
        return img

    def _draw_block(
        self, draw: ImageDraw.ImageDraw, x: int, y: int, color: Color
    ) -> None:
        """Fill the ``cell_w`` x ``cell_h`` px block at field cell ``(x, y)``."""
        left = self.field_x + x * self.cell_w
        top = self.field_y + y * self.cell_h
        draw.rectangle(
            [left, top, left + self.cell_w - 1, top + self.cell_h - 1], fill=color
        )

    def _draw_hud(self, draw: ImageDraw.ImageDraw) -> None:
        """Draw the right band: separator, next piece preview, score and level."""
        top = self.field_y
        bottom = self.field_y + self.field_height - 1
        divider = HUD_PAUSE_COLOR if self.state == self.PAUSED else HUD_DIVIDER_COLOR
        draw.line([self.hud_x, top, self.hud_x, bottom], fill=divider)
        if self.state == self.GAME_OVER:
            draw.rectangle(
                [self.hud_x + 1, top, self.hud_x + self.hud_w - 1, bottom],
                fill=HUD_OVER_COLOR,
            )
            return
        cell_w, cell_h = self._preview_cell()
        self._draw_preview(draw, cell_w, cell_h)
        self._draw_bars(draw, top + PREVIEW_CELLS * cell_h + 2, bottom)

    def _preview_cell(self) -> tuple[int, int]:
        """Block size of the next-piece preview, kept inside the HUD band."""
        cell_w = max(1, min(self.cell_w, self.hud_content_w // PREVIEW_CELLS))
        half = (self.field_height // 2) // PREVIEW_CELLS
        cell_h = max(1, min(self.cell_h, half))
        return cell_w, cell_h

    def _draw_preview(
        self, draw: ImageDraw.ImageDraw, cell_w: int, cell_h: int
    ) -> None:
        """Draw the queued piece centred in a :data:`PREVIEW_CELLS` box."""
        cells = TETROMINOES[self.next_piece][0]
        dxs = [dx for dx, _ in cells]
        dys = [dy for _, dy in cells]
        width = max(dxs) - min(dxs) + 1
        height = max(dys) - min(dys) + 1
        box_w = PREVIEW_CELLS * cell_w
        box_h = PREVIEW_CELLS * cell_h
        origin_x = self.hud_content_x + (self.hud_content_w - box_w) // 2
        origin_y = self.field_y + max(0, (box_h - height * cell_h) // 2)
        origin_x += max(0, (box_w - width * cell_w) // 2)
        color = PIECE_COLORS[self.next_piece]
        for dx, dy in cells:
            left = origin_x + (dx - min(dxs)) * cell_w
            top = origin_y + (dy - min(dys)) * cell_h
            draw.rectangle(
                [left, top, left + cell_w - 1, top + cell_h - 1], fill=color
            )

    def _draw_bars(self, draw: ImageDraw.ImageDraw, top: int, bottom: int) -> None:
        """Draw the score bar (green) and the level bar (yellow) side by side."""
        height = bottom - top + 1
        if height <= 0:
            return
        bar_w = max(1, min(self.cell_w, self.hud_content_w // 4))
        score_px = min(height, self.score // _SCORE_PER_BAR_PIXEL)
        for i in range(score_px):
            draw.rectangle(
                [
                    self.hud_content_x,
                    bottom - i,
                    self.hud_content_x + bar_w - 1,
                    bottom - i,
                ],
                fill=HUD_SCORE_COLOR,
            )
        level_x = self.hud_content_x + bar_w + 1
        if level_x + bar_w - 1 >= self.hud_x + self.hud_w:
            return
        for i in range(min(height, self.level)):
            draw.rectangle(
                [level_x, bottom - i, level_x + bar_w - 1, bottom - i],
                fill=HUD_LEVEL_COLOR,
            )


def run_tetris(
    address: str,
    brightness: Optional[int] = None,
    fps: float = 10.0,
    level: int = 1,
    seed: Optional[int] = None,
    hud: bool = True,
    panel: Optional[Panel] = None,
) -> None:
    """Play the Tetris demo on the panel.

    Everything (field + HUD) is drawn on the LED matrix. A *minimal* tkinter
    window is opened only to capture the keyboard.

    Args:
        address: Bluetooth MAC address of the panel.
        brightness: Optional brightness (0-100) applied on connect.
        fps: Target game/send rate (frames per second).
        level: Starting level (gravity speeds up as the level rises).
        seed: Optional RNG seed for a reproducible piece sequence.
        hud: Draw the on-panel HUD (next piece, score bar, level bar).
        panel: Optional already connected panel to use instead of connecting;
            a borrowed panel is left open (see :func:`pixelled_lib.library.panel.use_panel`).
    """
    import tkinter as tk

    with use_panel(address, brightness, panel) as panel:
        print(
            f"Connected to {panel.width}x{panel.height} panel "
            f"(LED type {panel.led_type})"
        )
        print("Playing tetris - all output is on the panel.")

        game = Tetris(
            panel.width, panel.height, level=level, seed=seed, hud=hud
        )
        sender = FrameSender(panel, fps)
        sender.start()

        interval = int(1000 / fps) if fps > 0 else 100
        repeater = KeyRepeater()
        alive = True

        root = tk.Tk()
        root.title("pixelled_play tetris - keyboard controller")
        label = tk.Label(root, anchor="w", justify="left", font=("Consolas", 9))
        label.pack(padx=10, pady=10)

        def status_text() -> str:
            return (
                f"score={game.score}  lines={game.lines}  level={game.level}  "
                f"next={game.next_piece}  state={game.state}\n"
                "left/right or A/D: move    up/W or X: rotate    Z: rotate ccw\n"
                "down/S: soft drop    space: hard drop    "
                "p: pause    r: replay    q/Esc: quit"
            )

        def quit_app() -> None:
            nonlocal alive
            alive = False
            root.destroy()

        def on_press(event) -> None:
            key = event.keysym.lower()
            if not repeater.press(key):
                # The OS re-sends KeyPress while a key is held; tick() already
                # repeats the held keys, so acting here would double the move.
                return
            if key in ("left", "a"):
                game.move(-1)
            elif key in ("right", "d"):
                game.move(1)
            elif key in ("down", "s"):
                game.soft_drop()
            elif key in ("up", "w", "x"):
                game.rotate(True)
            elif key == "z":
                game.rotate(False)
            elif key == "space":
                if game.state == Tetris.GAME_OVER:
                    game.reset(seed=seed)
                else:
                    # The game starts by itself, so space is always a hard drop.
                    game.hard_drop()
            elif key == "p":
                game.toggle_pause()
            elif key == "r":
                game.reset(seed=seed)
            elif key in ("q", "escape"):
                quit_app()

        def on_release(event) -> None:
            repeater.release(event.keysym.lower())

        def on_focus_out(_event) -> None:
            # No KeyRelease is delivered once we lose the focus: without this
            # the last key would stay held (and keep repeating) forever.
            repeater.clear()

        def tick() -> None:
            if not alive:
                return
            for key in repeater.due():
                if key in ("left", "a"):
                    game.move(-1)
                elif key in ("right", "d"):
                    game.move(1)
                elif key in ("down", "s"):
                    game.soft_drop()
            game.step()
            label.config(text=status_text())
            sender.submit(game.render())
            root.after(interval, tick)

        root.bind("<KeyPress>", on_press)
        root.bind("<KeyRelease>", on_release)
        root.bind("<FocusOut>", on_focus_out)
        root.protocol("WM_DELETE_WINDOW", quit_app)
        root.focus_force()
        label.config(text=status_text())
        sender.submit(game.render())
        root.after(interval, tick)
        try:
            root.mainloop()
        finally:
            sender.stop()
            sender.join(timeout=1.0)

        print(f"Sent {sender.sent} frames.")

