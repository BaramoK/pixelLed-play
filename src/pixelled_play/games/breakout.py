"""A small brick-breaker (breakout) demo rendered on the LED panel.

:class:`Breakout` holds the whole game state and is pure (no BLE, no I/O), so
it can be unit tested without hardware - exactly like
:mod:`pixelled_lib.library.render` and :mod:`pixelled_lib.library.control`.
:func:`run_breakout` wires it to a real panel:
everything (field + HUD) is displayed on the LED matrix, and a *minimal*
tkinter window is used only to capture keyboard input.

Levels are endless: clearing a wall builds a harder one (a taller wall - one
more row every five levels - plus a denser one and a 10 % faster ball) and
awards an extra life every five levels, so the game only ends when the player
runs out of lives.
"""

from __future__ import annotations

import random
from typing import Optional

from PIL import Image, ImageDraw

from pixelled_lib.library.panel import Panel, use_panel
from pixelled_lib.library.sender import FrameSender

Color = tuple[int, int, int]

# Row colours for the brick wall (classic breakout look).
BRICK_PALETTE: tuple[Color, ...] = (
    (255, 0, 0),
    (255, 128, 0),
    (255, 255, 0),
    (0, 200, 0),
    (0, 200, 200),
    (0, 128, 255),
    (200, 0, 255),
)

# Bricks are 2x2 px blocks laid out edge to edge (no gap), so the wall reads as
# a solid mass with holes - the classic breakout look. The ball stays 1 px and
# the paddle is a 2-row bar with red-orange tips (:data:`PADDLE_EDGE_COLOR`).
BRICK_SIZE = 2
BRICK_PITCH = BRICK_SIZE

PADDLE_HEIGHT = 2

PADDLE_COLOR: Color = (255, 255, 255)
# The paddle's two end columns are red-orange (the hue halfway between the
# wall's red and orange rows), so the bar reads as a paddle with visible tips
# instead of a plain white block.
PADDLE_EDGE_COLOR: Color = (255, 64, 0)
BALL_COLOR: Color = (255, 255, 255)

HUD_SCORE_COLOR: Color = (0, 200, 0)
HUD_LIVES_COLOR: Color = (255, 255, 255)
HUD_LEVEL_COLOR: Color = (255, 255, 0)
HUD_OVER_COLOR: Color = (255, 0, 0)

# Paddle bounces allowed without destroying a brick before the ball is nudged
# onto a fresh trajectory (keeps a single brick from becoming unreachable).
_DRY_BOUNCE_LIMIT = 5

# Endless levels: each cleared wall starts a new, harder one (a taller, denser
# wall and a faster ball) and rewards the player with an extra life every
# _LEVELS_PER_LIFE levels, so a good run lasts. There is no "win" state - only
# running out of lives ends the game.
_DENSITY_STEP = 0.1  # wall density added every _LEVELS_PER_STEP levels
_SPEED_GROWTH = 1.1  # ball speed factor applied every _LEVELS_PER_STEP levels
_LEVELS_PER_STEP = 5  # rows, density and speed all step up every this many levels
_MAX_SPEED = 3.0  # hard cap on the ball speed, whatever the level
_LEVELS_PER_LIFE = 5  # one extra life every this many levels (5, 10, 15, ...)
_START_ROWS = 3  # brick rows at level 1 (one more every _LEVELS_PER_STEP levels)


def _clamp(value: int, low: int, high: int) -> int:
    """Return ``value`` constrained to ``[low, high]``."""
    return max(low, min(high, value))


class Breakout:
    """An endless brick breaker game.

    The whole game state (wall, paddle, ball, lives, score, level) lives here
    and is rendered by :meth:`render`. Nothing in this class touches the panel.

    Geometry: the paddle is a 2-row bar sitting on the last row, the wall is
    built from 2x2 px bricks laid out edge to edge (:data:`BRICK_PITCH` grid)
    and the ball is a single pixel. The paddle is white (:data:`PADDLE_COLOR`)
    with a red-orange column at each end (:data:`PADDLE_EDGE_COLOR`).

    Levels are endless: clearing a wall calls :meth:`advance_level`, which
    builds a taller, denser and slightly faster wall, keeps score and lives, and
    awards an extra life every :data:`_LEVELS_PER_LIFE` levels. There is no
    "win" state - the game only ends when the player runs out of lives.

    Parameters
    ----------
    width, height:
        Field size in pixels (the panel size).
    lives:
        Number of balls available at level 1 (a higher starting level adds the
        extra lives it would have earned on the way).
    seed:
        Optional RNG seed; when given the random wall is reproducible.
    density:
        Probability for a wall cell to contain a brick (0..1) at level 1
        (``0.5`` by default); it gains :data:`_DENSITY_STEP` every
        :data:`_LEVELS_PER_STEP` levels, up to a full wall.
    speed:
        Pixels travelled per tick at level 1 (``>= 1``, ``1`` by default); it
        grows by 10 % every :data:`_LEVELS_PER_STEP` levels, capped at
        :data:`_MAX_SPEED`. Fractional values are averaged over ticks (see
        :meth:`step`).
    level:
        Starting level (default 1); higher levels start with a taller and
        denser wall, a faster ball and the extra lives earned up to that level.
    hud:
        When true, the top row is reserved for the on-panel HUD
        (score bar on the left, level then remaining lives on the right).
    """

    READY = "ready"
    RUNNING = "running"
    GAME_OVER = "game_over"

    def __init__(
        self,
        width: int,
        height: int,
        lives: int = 3,
        seed: Optional[int] = None,
        density: float = 0.5,
        speed: float = 1.0,
        level: int = 1,
        hud: bool = True,
    ):
        self.width = int(width)
        self.height = int(height)
        self.initial_lives = max(1, int(lives))
        self.density = min(1.0, max(0.0, float(density)))
        self.speed = max(1.0, float(speed))
        self.initial_level = max(1, int(level))
        self.hud = bool(hud)
        self.reset(seed=seed)

    # -- geometry ---------------------------------------------------------
    @property
    def hud_rows(self) -> int:
        """Number of rows reserved for the HUD (0 when disabled or too small)."""
        return 1 if (self.hud and self.height >= 10) else 0

    @property
    def paddle_width(self) -> int:
        """Paddle width in pixels."""
        return max(3, self.width // 4)

    @property
    def paddle_height(self) -> int:
        """Paddle height in pixels (a thick bar, unlike the 1 px ball)."""
        return min(PADDLE_HEIGHT, max(1, self.height))

    @property
    def paddle_y(self) -> int:
        """Top row of the paddle (its bottom row is the last row of the field)."""
        return self.height - self.paddle_height

    @property
    def paddle_bottom(self) -> int:
        """Bottom row of the paddle (the last row of the field)."""
        return self.paddle_y + self.paddle_height - 1

    @property
    def level_density(self) -> float:
        """Wall density at the current level (rises with the level, capped at 1).

        It starts at :attr:`density` on level 1 and gains :data:`_DENSITY_STEP`
        every :data:`_LEVELS_PER_STEP` levels, until the wall is full.
        """
        steps = (self.level - 1) // _LEVELS_PER_STEP
        return min(1.0, self.density + _DENSITY_STEP * steps)

    @property
    def level_speed(self) -> float:
        """Ball speed in pixels per tick at the current level (capped).

        It starts at :attr:`speed` on level 1 and grows by 10 % every
        :data:`_LEVELS_PER_STEP` levels, up to :data:`_MAX_SPEED`. The value is
        fractional on purpose - :meth:`step` averages it over ticks, so 1.1
        px/tick means 2 px on one tick out of ten.
        """
        steps = (self.level - 1) // _LEVELS_PER_STEP
        return min(_MAX_SPEED, self.speed * _SPEED_GROWTH**steps)

    @property
    def level_rows(self) -> int:
        """Brick rows wanted at the current level (before the panel-size cap).

        It starts at :data:`_START_ROWS` on level 1 and gains one row every
        :data:`_LEVELS_PER_STEP` levels; :meth:`_build_wall` caps it at what the
        panel can actually hold, so a small panel simply builds fewer rows.
        """
        return _START_ROWS + (self.level - 1) // _LEVELS_PER_STEP

    def _brick_at(self, x: int, y: int) -> Optional[tuple[int, int]]:
        """Return the origin of the brick covering ``(x, y)``, or ``None``.

        Bricks are :data:`BRICK_SIZE` squares on a :data:`BRICK_PITCH` grid
        aligned on the origin, so the lookup is a plain modulo - it works for
        any pixel of the field, brick or not.
        """
        if x < 0 or y < 0:
            return None
        origin = (x - x % BRICK_PITCH, y - y % BRICK_PITCH)
        return origin if origin in self.bricks else None

    # -- lifecycle --------------------------------------------------------
    def reset(self, seed: Optional[int] = None) -> None:
        """Start a new game, rebuilding the (random) wall."""
        self._rng = random.Random(seed)
        self.level = self.initial_level
        # Starting at level N must feel like a run that just reached N, so the
        # extra lives awarded at levels 5, 10, 15, ... are already in the bag.
        self.lives = self.initial_lives + self.level // _LEVELS_PER_LIFE
        self.score = 0
        self.level_score = 0
        self.state = self.READY
        self.paddle_x = (self.width - self.paddle_width) // 2
        self.paddle_vx = 0
        self._dry_bounces = 0
        self._move_carry = 0.0
        self.ball_x = self.paddle_x + self.paddle_width // 2
        self.ball_y = self.paddle_y - 1
        self.vx = 0
        self.vy = 0
        self._build_wall()

    def move_paddle(self, dx: int) -> None:
        """Move the paddle by ``dx`` pixels (clamped to the field).

        ``dx`` also records the paddle direction, which is used as "spin" when
        the ball hits the paddle dead-centre. Call it every tick (with ``dx=0``
        when the player is idle) so the direction stays up to date.
        """
        dx = int(dx)
        self.paddle_vx = 0 if dx == 0 else (1 if dx > 0 else -1)
        self.paddle_x = _clamp(
            self.paddle_x + dx, 0, max(0, self.width - self.paddle_width)
        )
        if self.state == self.READY:
            self.ball_x = self.paddle_x + self.paddle_width // 2
            self.ball_y = self.paddle_y - 1

    def launch(self) -> None:
        """Release the ball from the paddle (no-op unless in ``READY``)."""
        if self.state != self.READY:
            return
        self.vy = -1
        self.vx = self._rng.choice((-1, 1))
        self.state = self.RUNNING

    # -- simulation -------------------------------------------------------
    def step(self) -> None:
        """Advance the simulation by one tick."""
        if self.state == self.GAME_OVER:
            return
        if self.state == self.READY:
            # The ball rides on the paddle until it is launched.
            self.ball_x = self.paddle_x + self.paddle_width // 2
            self.ball_y = self.paddle_y - 1
            return
        # The speed is fractional (1.1, 1.21, ... px/tick), so accumulate the
        # distance wanted for this tick and move a whole number of pixels,
        # carrying the rest over to the next tick: the ball then averages
        # level_speed px/tick without any sub-pixel geometry.
        self._move_carry += self.level_speed
        steps = int(self._move_carry)
        self._move_carry -= steps
        for _ in range(steps):
            self._unit_step()
            if self.state != self.RUNNING:
                break

    def _unit_step(self) -> None:
        """Move the ball one pixel, resolving walls, bricks and the paddle."""
        nx = self.ball_x + self.vx
        ny = self.ball_y + self.vy

        # Side walls.
        if nx < 0:
            nx, self.vx = 0, 1
        elif nx >= self.width:
            nx, self.vx = self.width - 1, -1

        # Top wall: the ball bounces just below the HUD row.
        if ny < self.hud_rows:
            ny, self.vy = self.hud_rows, 1

        # Bricks: test each axis independently so corners reflect correctly.
        hit_x = self._brick_at(nx, self.ball_y)
        hit_y = self._brick_at(self.ball_x, ny)
        hit_diag = None if (hit_x or hit_y) else self._brick_at(nx, ny)
        if hit_x and hit_y:
            self._hit_brick(*hit_x)
            self._hit_brick(*hit_y)
            self.vx, self.vy = -self.vx, -self.vy
            nx, ny = self.ball_x, self.ball_y
        elif hit_x:
            self._hit_brick(*hit_x)
            self.vx = -self.vx
            nx = self.ball_x
        elif hit_y:
            self._hit_brick(*hit_y)
            self.vy = -self.vy
            ny = self.ball_y
        elif hit_diag:
            # Diagonal move straight into a brick corner: bounce back.
            self._hit_brick(*hit_diag)
            self.vx, self.vy = -self.vx, -self.vy
            nx, ny = self.ball_x, self.ball_y

        self.ball_x, self.ball_y = nx, ny

        # Paddle: bounce and deflect according to the impact point.
        if (
            self.vy > 0
            and self.ball_y >= self.paddle_y
            and self.paddle_x <= self.ball_x < self.paddle_x + self.paddle_width
        ):
            self.ball_y = self.paddle_y - 1
            self.vy = -1
            center = self.paddle_x + self.paddle_width / 2.0
            offset = self.ball_x - center
            if offset < 0:
                self.vx = -1
            elif offset > 0:
                self.vx = 1
            elif self.paddle_vx:
                self.vx = self.paddle_vx  # spin imparted by a moving paddle
            else:
                # Dead-centre with a still paddle: pick a direction at random so
                # the ball cannot get trapped in a periodic 45-degree orbit.
                self.vx = self._rng.choice((-1, 1))

            # Anti-stall: after several paddle bounces without destroying a
            # brick, nudge the ball onto a fresh trajectory. The 1px sideways
            # shift is what actually escapes the 45-degree lattice the ball
            # otherwise stays confined to (a pure direction change cannot).
            self._dry_bounces += 1
            if self._dry_bounces >= _DRY_BOUNCE_LIMIT:
                self._dry_bounces = 0
                self.ball_x = _clamp(
                    self.ball_x + self._rng.choice((-1, 1)), 0, self.width - 1
                )
                self.vx = self._rng.choice((-1, 1))

        # Lost ball.
        if self.ball_y >= self.height:
            self._lose_ball()
            return

        if not self.bricks:
            self.advance_level()

    def _hit_brick(self, x: int, y: int) -> None:
        """Remove the brick at ``(x, y)`` and update the score."""
        self.bricks.discard((x, y))
        self.brick_colors.pop((x, y), None)
        self.score += 1
        self.level_score += 1
        self._dry_bounces = 0

    def _lose_ball(self) -> None:
        """Consume a ball, resetting to ``READY`` or ending the game."""
        self.lives -= 1
        self.vx = 0
        self.vy = 0
        self._move_carry = 0.0
        self.ball_x = self.paddle_x + self.paddle_width // 2
        self.ball_y = self.paddle_y - 1
        if self.lives <= 0:
            self.lives = 0
            self.state = self.GAME_OVER
        else:
            self.state = self.READY

    def advance_level(self) -> None:
        """Start the next (harder) level once the current wall is cleared.

        Score and lives carry over, and reaching a multiple of
        :data:`_LEVELS_PER_LIFE` rewards the player with an extra life; only the
        ball is put back on the (re-centred) paddle, ready to be launched again.
        Levels are endless, so this never ends the game - running out of lives
        does.
        """
        self.level += 1
        self.level_score = 0
        if self.level % _LEVELS_PER_LIFE == 0:
            self.lives += 1
        self.paddle_x = (self.width - self.paddle_width) // 2
        self.paddle_vx = 0
        self._dry_bounces = 0
        self._move_carry = 0.0
        self.vx = 0
        self.vy = 0
        self.ball_x = self.paddle_x + self.paddle_width // 2
        self.ball_y = self.paddle_y - 1
        self.state = self.READY
        self._build_wall()

    def _build_wall(self) -> None:
        """Build the random brick wall for the current field size and level.

        The wall grows with the level: it holds :attr:`level_rows` rows (one
        more every :data:`_LEVELS_PER_STEP` levels, capped at what the panel can
        hold) and its cell fill probability is :attr:`level_density`, so it also
        gets denser as the level rises. The grid starts on a multiple of
        :data:`BRICK_PITCH` (that is what makes :meth:`_brick_at` a plain
        modulo) and never comes closer than two rows above the paddle, so there
        is always room to play.
        """
        top = -(-self.hud_rows // BRICK_PITCH) * BRICK_PITCH
        usable = self.paddle_y - 2 - top
        fits = max(1, min(self.height // (2 * BRICK_PITCH), usable // BRICK_PITCH))
        rows = max(1, min(self.level_rows, fits))
        cols = max(1, self.width // BRICK_PITCH)
        self.brick_rows = rows
        self.brick_cols = cols
        self.bricks = set()  # set of (x, y) brick origins still standing
        self.brick_colors = {}  # (x, y) origin -> Color
        for row in range(rows):
            y = top + row * BRICK_PITCH
            color = BRICK_PALETTE[row % len(BRICK_PALETTE)]
            for col in range(cols):
                x = col * BRICK_PITCH
                if self._rng.random() < self.level_density:
                    self.bricks.add((x, y))
                    self.brick_colors[(x, y)] = color
        if not self.bricks:  # guarantee at least one brick
            x = (cols // 2) * BRICK_PITCH
            self.bricks.add((x, top))
            self.brick_colors[(x, top)] = BRICK_PALETTE[0]
        self.total_bricks = len(self.bricks)

    def clear_bricks(self) -> None:
        """Remove every brick (mainly useful for tests).

        Clearing the wall while the ball is moving makes the next
        :meth:`_unit_step` see an empty wall and start the next level, exactly
        as if the last brick had just been destroyed.
        """
        self.bricks.clear()
        self.brick_colors.clear()

    def render(self) -> Image.Image:
        """Return the current frame as an ``RGB`` image of the field size."""
        img = Image.new("RGB", (self.width, self.height), (0, 0, 0))
        draw = ImageDraw.Draw(img)
        for x, y in self.bricks:
            draw.rectangle(
                [x, y, x + BRICK_SIZE - 1, y + BRICK_SIZE - 1],
                fill=self.brick_colors[(x, y)],
            )
        draw.rectangle(
            [
                self.paddle_x,
                self.paddle_y,
                self.paddle_x + self.paddle_width - 1,
                self.paddle_bottom,
            ],
            fill=PADDLE_COLOR,
        )
        # Red-orange tips: the first and last column of the bar, over its whole
        # height (both rows), painted over the white bar drawn above.
        for x in (self.paddle_x, self.paddle_x + self.paddle_width - 1):
            draw.rectangle(
                [x, self.paddle_y, x, self.paddle_bottom],
                fill=PADDLE_EDGE_COLOR,
            )
        if 0 <= self.ball_x < self.width and 0 <= self.ball_y < self.height:
            draw.point((self.ball_x, self.ball_y), fill=BALL_COLOR)
        if self.hud_rows:
            self._draw_hud(draw)
        return img

    def _draw_hud(self, draw: ImageDraw.ImageDraw) -> None:
        """Draw the top-row HUD: score bar (left), level then lives (right)."""
        y = 0
        if self.state == self.GAME_OVER:
            draw.line([0, y, self.width - 1, y], fill=HUD_OVER_COLOR)
            return
        # Lives: one white pixel each, packed against the right edge.
        lives = min(self.lives, self.width)
        for i in range(lives):
            draw.point((self.width - 1 - i, y), fill=HUD_LIVES_COLOR)
        # Level: one yellow pixel per level, just left of the lives.
        level_w = min(self.level, max(0, self.width - lives - 1))
        for i in range(level_w):
            draw.point((self.width - lives - 1 - i, y), fill=HUD_LEVEL_COLOR)
        # Score bar: green, one pixel per fraction of the current wall cleared.
        span = max(0, self.width - lives - level_w - 1)
        filled = 0
        if self.total_bricks and span:
            filled = int(round(self.level_score / self.total_bricks * span))
        for i in range(min(filled, span)):
            draw.point((i, y), fill=HUD_SCORE_COLOR)


def run_breakout(
    address: str,
    brightness: Optional[int] = None,
    fps: float = 15.0,
    seed: Optional[int] = None,
    density: float = 0.5,
    lives: int = 3,
    speed: float = 1.0,
    level: int = 1,
    hud: bool = True,
    panel: Optional[Panel] = None,
) -> None:
    """Play the brick-breaker demo on the panel.

    Everything (field + HUD) is drawn on the LED matrix. A *minimal* tkinter
    window is opened only to capture the keyboard, which gives a smooth,
    continuously moving paddle.

    Levels are endless: clearing a wall builds a taller, denser and faster one
    and rewards the player with an extra life every five levels, so the run
    only ends when the player runs out of lives.

    Args:
        address: Bluetooth MAC address of the panel.
        brightness: Optional brightness (0-100) applied on connect.
        fps: Target game/send rate (frames per second).
        seed: Optional RNG seed for a reproducible wall.
        density: Probability for a wall cell to contain a brick (0..1) at level 1
            (0.5 by default); it gains 0.1 every five levels, up to a full wall.
        lives: Number of balls available at level 1 (higher starting levels add
            the extra lives they would have earned).
        speed: Pixels travelled per tick at level 1 (1 by default); it grows by
            10 % every five levels, capped at 3.
        level: Starting level (default 1); the wall already has the extra rows
            (one every five levels) and the extra lives earned up to it.
        hud: Draw the on-panel HUD (score bar + level + lives).
        panel: Optional already connected panel to use instead of connecting;
            a borrowed panel is left open (see :func:`pixelled_lib.library.panel.use_panel`).
    """
    from pixelled_lib.library.window import Window

    with use_panel(address, brightness, panel) as panel:
        print(
            f"Connected to {panel.width}x{panel.height} panel "
            f"(LED type {panel.led_type})"
        )
        print("Playing breakout - all output is on the panel.")

        game = Breakout(
            panel.width,
            panel.height,
            lives=lives,
            seed=seed,
            density=density,
            speed=speed,
            level=level,
            hud=hud,
        )
        sender = FrameSender(panel, fps)
        sender.start()

        interval = int(1000 / fps) if fps > 0 else 100
        pressed = set()

        window = Window("pixelled_play breakout - keyboard controller")
        label = window.label

        def status_text() -> str:
            return (
                f"level={game.level}  lives={game.lives}  "
                f"bricks={game.level_score}/{game.total_bricks}  "
                f"score={game.score}  state={game.state}\n"
                "left/right or A/D: paddle    space: launch    "
                "r: replay    q/Esc: quit"
            )

        def quit_app() -> None:
            window.close()

        def on_press(event) -> None:
            key = event.keysym.lower()
            if key in ("left", "a", "right", "d"):
                pressed.add(key)
            elif key == "space":
                game.launch()
            elif key == "r":
                game.reset(seed=seed)
            elif key in ("q", "escape"):
                quit_app()

        def on_release(event) -> None:
            pressed.discard(event.keysym.lower())

        def tick() -> None:
            if window.closed:
                return
            dx = (1 if pressed & {"right", "d"} else 0) - (
                1 if pressed & {"left", "a"} else 0
            )
            game.move_paddle(dx)
            game.step()
            label.config(text=status_text())
            sender.submit(game.render())
            window.schedule(interval, tick)

        window.bind("<KeyPress>", on_press)
        window.bind("<KeyRelease>", on_release)
        window.on_close(quit_app)
        label.config(text=status_text())
        sender.submit(game.render())
        window.schedule(interval, tick)
        try:
            window.mainloop()
        finally:
            sender.stop()
            sender.join(timeout=1.0)
            window.close()

        print(f"Sent {sender.sent} frames.")
