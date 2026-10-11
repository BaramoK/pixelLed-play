"""A small brick-breaker (breakout) demo rendered on the LED panel.

:class:`Breakout` holds the whole game state and is pure (no BLE, no I/O), so
it can be unit tested without hardware - exactly like
:mod:`pixelled_lib.library.render` and :mod:`pixelled_lib.library.control`.
:func:`run_breakout` wires it to a real panel:
everything (field + HUD) is displayed on the LED matrix, and a *minimal*
tkinter window is used only to capture keyboard input.

Levels are endless: clearing a wall builds a harder one (a taller wall - one
more row every five levels - plus a denser one and a 10 % faster ball) and
awards an extra life every five levels - but only while the player has fewer
than five lives, so the reward tops the player back up instead of growing
without bound, and the game only ends when the player runs out of lives.

Destroyed bricks can also drop a bonus: half a brick (2 px wide, 1 px tall)
falls at half the ball's speed - slower, so the player has time to fetch it -
and, caught by the paddle, either grants an extra life, sends a second ball,
widens the bar by one pixel per side or, for the red malus, narrows it.
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

# Paddle width bounds (px): the ``grow`` and ``shrink`` pickups resize the bar by
# one pixel per side (two in total), within these limits. They never override the
# width the panel implies, so a bar already wider than the ceiling keeps its
# width (only a malus narrows it) - see :attr:`Breakout.paddle_width`.
PADDLE_MIN_WIDTH = 6
PADDLE_MAX_WIDTH = 12

# Pickups: a destroyed brick can drop half a brick (:data:`BONUS_WIDTH` x
# :data:`BONUS_HEIGHT` px) that falls at half the ball's speed
# (:data:`_BONUS_SPEED_FACTOR`). Catching it applies its effect; the red one is
# a malus, so it is worth dodging.
BONUS_WIDTH = BRICK_SIZE
BONUS_HEIGHT = BRICK_SIZE // 2
BONUS_LIFE = "life"
BONUS_BALL = "ball"
BONUS_GROW = "grow"
BONUS_SHRINK = "shrink"
BONUS_KINDS: tuple[str, ...] = (BONUS_LIFE, BONUS_BALL, BONUS_GROW, BONUS_SHRINK)
BONUS_COLORS: dict[str, Color] = {
    BONUS_LIFE: (0, 255, 0),  # green: one extra life
    BONUS_BALL: (0, 128, 255),  # blue: one more ball
    BONUS_GROW: (255, 255, 255),  # white: one pixel wider on each side
    BONUS_SHRINK: (255, 0, 0),  # red: one pixel narrower on each side (malus)
}

HUD_SCORE_COLOR: Color = (0, 200, 0)
HUD_LIVES_COLOR: Color = (255, 255, 255)
HUD_LEVEL_COLOR: Color = (255, 255, 0)
HUD_OVER_COLOR: Color = (255, 0, 0)

# Paddle bounces allowed without destroying a brick before the ball is nudged
# onto a fresh trajectory (keeps a single brick from becoming unreachable).
_DRY_BOUNCE_LIMIT = 5

# Endless levels: each cleared wall starts a new, harder one (a taller, denser
# wall and a faster ball) and rewards the player with an extra life every
# _LEVELS_PER_LIFE levels - but only while below _MAX_LEVEL_LIVES, so the
# reward tops the player back up instead of growing without bound. There is no
# "win" state - only running out of lives ends the game.
_DENSITY_STEP = 0.1  # wall density added every _LEVELS_PER_STEP levels
_SPEED_GROWTH = 1.1  # ball speed factor applied every _LEVELS_PER_STEP levels
_LEVELS_PER_STEP = 5  # rows, density and speed all step up every this many levels
_MAX_SPEED = 3.0  # hard cap on the ball speed, whatever the level
_LEVELS_PER_LIFE = 5  # one extra life every this many levels (5, 10, 15, ...)
_MAX_LEVEL_LIVES = 5  # the level-up reward never pushes lives past this
_START_ROWS = 3  # brick rows at level 1 (one more every _LEVELS_PER_STEP levels)
_BONUS_DROP_CHANCE = 0.15  # default probability for a brick to drop a pickup
_BONUS_SPEED_FACTOR = 0.5  # pickups fall at half the ball speed (px/tick)
_MAX_BALLS = 4  # hard cap on the number of balls in play


def _clamp(value: int, low: int, high: int) -> int:
    """Return ``value`` constrained to ``[low, high]``."""
    return max(low, min(high, value))


class _Ball:
    """One ball: a single pixel with its own position and velocity.

    The game holds a list of them (a ``ball`` pickup adds a second one); the
    :attr:`Breakout.ball_x` / :attr:`Breakout.ball_y` / :attr:`Breakout.vx` /
    :attr:`Breakout.vy` properties address the first ball, which is all a
    single-ball game ever has.
    """

    __slots__ = ("x", "y", "vx", "vy")

    def __init__(self, x: int, y: int, vx: int = 0, vy: int = 0) -> None:
        self.x = int(x)
        self.y = int(y)
        self.vx = int(vx)
        self.vy = int(vy)


class _Bonus:
    """A falling pickup: half a brick (:data:`BONUS_WIDTH` x :data:`BONUS_HEIGHT`).

    It falls at half the ball's speed (:data:`_BONUS_SPEED_FACTOR`) and, being
    one row of the brick grid, drops one pixel at a time
    (:meth:`Breakout._fall_bonuses`) until the paddle catches it or it falls off
    the bottom.
    """

    __slots__ = ("x", "y", "kind")

    def __init__(self, x: int, y: int, kind: str) -> None:
        self.x = int(x)
        self.y = int(y)
        self.kind = kind


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
    awards an extra life every :data:`_LEVELS_PER_LIFE` levels - but only while
    the player sits below :data:`_MAX_LEVEL_LIVES` lives, so the reward tops the
    player back up instead of growing without bound. There is no "win" state -
    the game only ends when the player runs out of lives.

    Destroyed bricks can drop a pickup (one brick in ``bonus_chance``): half a
    brick that falls at half the ball's speed and, caught by the paddle, either
    grants an extra life, sends a second ball, widens the bar by one pixel per
    side or - the red malus - narrows it.

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
    bonus_chance:
        Probability for a destroyed brick to drop a pickup (0..1,
        :data:`_BONUS_DROP_CHANCE` by default); ``0`` disables pickups.
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
        bonus_chance: float = _BONUS_DROP_CHANCE,
    ):
        self.width = int(width)
        self.height = int(height)
        self.initial_lives = max(1, int(lives))
        self.density = min(1.0, max(0.0, float(density)))
        self.speed = max(1.0, float(speed))
        self.initial_level = max(1, int(level))
        self.hud = bool(hud)
        self.bonus_chance = min(1.0, max(0.0, float(bonus_chance)))
        self.reset(seed=seed)

    # -- geometry ---------------------------------------------------------
    @property
    def hud_rows(self) -> int:
        """Number of rows reserved for the HUD (0 when disabled or too small)."""
        return 1 if (self.hud and self.height >= 10) else 0

    @property
    def base_paddle_width(self) -> int:
        """Paddle width implied by the panel, before any pickup resized it."""
        return max(3, self.width // 4)

    @property
    def paddle_width(self) -> int:
        """Paddle width in pixels: the base width, +/-2 px per pickup caught.

        The ``grow`` and ``shrink`` pickups widen or narrow the bar by one pixel
        per side, so the width moves in steps of two, within
        :data:`PADDLE_MIN_WIDTH` and :data:`PADDLE_MAX_WIDTH`. Those bounds never
        override the panel-derived width: a bar already wider than the ceiling
        keeps its width (only a malus narrows it), and one narrower than the
        floor keeps its width too (only a bonus widens it).
        """
        return self.base_paddle_width + 2 * _clamp(
            self.paddle_grow, *self._paddle_grow_bounds()
        )

    def _paddle_grow_bounds(self) -> tuple[int, int]:
        """Smallest and largest :attr:`paddle_grow` keeping the width in bounds."""
        base = self.base_paddle_width
        return (
            -((base - min(base, PADDLE_MIN_WIDTH)) // 2),
            (max(base, PADDLE_MAX_WIDTH) - base) // 2,
        )

    # The balls in play live in a list (a ``ball`` pickup adds one); these four
    # properties address the first ball, which is all a single-ball game has.
    @property
    def ball_x(self) -> int:
        """X of the first ball in play."""
        return self.balls[0].x

    @ball_x.setter
    def ball_x(self, value: int) -> None:
        self.balls[0].x = int(value)

    @property
    def ball_y(self) -> int:
        """Y of the first ball in play."""
        return self.balls[0].y

    @ball_y.setter
    def ball_y(self, value: int) -> None:
        self.balls[0].y = int(value)

    @property
    def vx(self) -> int:
        """Horizontal velocity of the first ball in play."""
        return self.balls[0].vx

    @vx.setter
    def vx(self, value: int) -> None:
        self.balls[0].vx = int(value)

    @property
    def vy(self) -> int:
        """Vertical velocity of the first ball in play."""
        return self.balls[0].vy

    @vy.setter
    def vy(self, value: int) -> None:
        self.balls[0].vy = int(value)

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
    def _ball_on_paddle(self) -> _Ball:
        """Return a fresh ball resting on the middle of the paddle."""
        return _Ball(self.paddle_x + self.paddle_width // 2, self.paddle_y - 1)

    def reset(self, seed: Optional[int] = None) -> None:
        """Start a new game, rebuilding the (random) wall.

        The paddle is back to its base width (:attr:`paddle_grow` is zeroed, so
        a pickup that resized it does not survive a replay) and no pickup is
        left falling.
        """
        self._rng = random.Random(seed)
        self.level = self.initial_level
        # Starting at level N must feel like a run that just reached N, so the
        # extra lives awarded at levels 5, 10, 15, ... are already in the bag -
        # but that reward stops once the player sits at _MAX_LEVEL_LIVES, and an
        # explicit starting count above it (``--lives``) is never reduced.
        self.lives = self.initial_lives + min(
            self.level // _LEVELS_PER_LIFE,
            max(0, _MAX_LEVEL_LIVES - self.initial_lives),
        )
        self.score = 0
        self.level_score = 0
        self.state = self.READY
        self.paddle_grow = 0
        self.paddle_x = (self.width - self.paddle_width) // 2
        self.paddle_vx = 0
        self._dry_bounces = 0
        self._move_carry = 0.0
        self._bonus_carry = 0.0
        self.balls = [self._ball_on_paddle()]
        self.bonuses: list[_Bonus] = []
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
        # Pickups fall at half that speed - slower, so the player has time to
        # fetch them - with an accumulator of their own. They are stepped first,
        # so one caught on the very tick the ball is lost is still applied
        # before the life is spent.
        self._bonus_carry += self.level_speed * _BONUS_SPEED_FACTOR
        bonus_steps = int(self._bonus_carry)
        self._bonus_carry -= bonus_steps
        for _ in range(bonus_steps):
            self._fall_bonuses()
        for _ in range(steps):
            self._move_balls()
            if self.state != self.RUNNING:
                break

    def _move_balls(self) -> None:
        """Move every ball in play by one pixel."""
        for ball in list(self.balls):
            self._unit_step(ball)
            if self.state != self.RUNNING:
                return

    def _unit_step(self, ball: Optional[_Ball] = None) -> None:
        """Move one ball one pixel, resolving walls, bricks and the paddle.

        ``ball`` defaults to the first ball in play, the only one a single-ball
        game has; each ball of a multi-ball game is moved on its own, so the
        extra ball simply travels independently.
        """
        if ball is None:
            ball = self.balls[0]
        nx = ball.x + ball.vx
        ny = ball.y + ball.vy

        # Side walls.
        if nx < 0:
            nx, ball.vx = 0, 1
        elif nx >= self.width:
            nx, ball.vx = self.width - 1, -1

        # Top wall: the ball bounces just below the HUD row.
        if ny < self.hud_rows:
            ny, ball.vy = self.hud_rows, 1

        # Bricks: test each axis independently so corners reflect correctly.
        hit_x = self._brick_at(nx, ball.y)
        hit_y = self._brick_at(ball.x, ny)
        hit_diag = None if (hit_x or hit_y) else self._brick_at(nx, ny)
        if hit_x and hit_y:
            self._hit_brick(*hit_x)
            self._hit_brick(*hit_y)
            ball.vx, ball.vy = -ball.vx, -ball.vy
            nx, ny = ball.x, ball.y
        elif hit_x:
            self._hit_brick(*hit_x)
            ball.vx = -ball.vx
            nx = ball.x
        elif hit_y:
            self._hit_brick(*hit_y)
            ball.vy = -ball.vy
            ny = ball.y
        elif hit_diag:
            # Diagonal move straight into a brick corner: bounce back.
            self._hit_brick(*hit_diag)
            ball.vx, ball.vy = -ball.vx, -ball.vy
            nx, ny = ball.x, ball.y

        ball.x, ball.y = nx, ny

        # Paddle: bounce and deflect according to the impact point.
        if (
            ball.vy > 0
            and ball.y >= self.paddle_y
            and self.paddle_x <= ball.x < self.paddle_x + self.paddle_width
        ):
            ball.y = self.paddle_y - 1
            ball.vy = -1
            center = self.paddle_x + self.paddle_width / 2.0
            offset = ball.x - center
            if offset < 0:
                ball.vx = -1
            elif offset > 0:
                ball.vx = 1
            elif self.paddle_vx:
                ball.vx = self.paddle_vx  # spin imparted by a moving paddle
            else:
                # Dead-centre with a still paddle: pick a direction at random so
                # the ball cannot get trapped in a periodic 45-degree orbit.
                ball.vx = self._rng.choice((-1, 1))

            # Anti-stall: after several paddle bounces without destroying a
            # brick, nudge the ball onto a fresh trajectory. The 1px sideways
            # shift is what actually escapes the 45-degree lattice the ball
            # otherwise stays confined to (a pure direction change cannot).
            self._dry_bounces += 1
            if self._dry_bounces >= _DRY_BOUNCE_LIMIT:
                self._dry_bounces = 0
                ball.x = _clamp(ball.x + self._rng.choice((-1, 1)), 0, self.width - 1)
                ball.vx = self._rng.choice((-1, 1))

        # Lost ball: a life is only spent when the last one is gone.
        if ball.y >= self.height:
            self._drop_ball(ball)
            return

        if not self.bricks:
            self.advance_level()

    def _hit_brick(self, x: int, y: int) -> None:
        """Remove the brick at ``(x, y)``, update the score and maybe drop a pickup."""
        self.bricks.discard((x, y))
        self.brick_colors.pop((x, y), None)
        self.score += 1
        self.level_score += 1
        self._dry_bounces = 0
        if self._rng.random() < self.bonus_chance:
            self.bonuses.append(_Bonus(x, y, self._rng.choice(BONUS_KINDS)))

    # -- pickups ----------------------------------------------------------
    def _fall_bonuses(self) -> None:
        """Drop every pickup by one pixel, catching or discarding it.

        Pickups fall at half the ball's speed (:data:`_BONUS_SPEED_FACTOR`), one
        pixel at a time (:meth:`step`), so even the 3 px/tick cap cannot make one
        tunnel through the paddle: a pickup at 1.5 px/tick still lands on the bar
        row, and the catch test is a threshold. A pickup reaching the paddle row
        inside the bar is caught (:meth:`_apply_bonus`); one falling past the
        bottom row is simply lost.
        """
        kept: list[_Bonus] = []
        for bonus in self.bonuses:
            bonus.y += 1
            if bonus.y >= self.paddle_y and self._catches(bonus):
                self._apply_bonus(bonus.kind)
            elif bonus.y < self.height:
                kept.append(bonus)
        self.bonuses = kept

    def _catches(self, bonus: _Bonus) -> bool:
        """True when the pickup overlaps the paddle horizontally."""
        return not (
            bonus.x + BONUS_WIDTH - 1 < self.paddle_x
            or bonus.x > self.paddle_x + self.paddle_width - 1
        )

    def _apply_bonus(self, kind: str) -> None:
        """Apply a caught pickup: a life, a second ball or a resized bar."""
        if kind == BONUS_LIFE:
            self.lives += 1
        elif kind == BONUS_BALL:
            self._add_ball()
        elif kind == BONUS_GROW:
            self._resize_paddle(1)
        elif kind == BONUS_SHRINK:
            self._resize_paddle(-1)

    def _add_ball(self) -> None:
        """Send an extra ball from the paddle (up to :data:`_MAX_BALLS`).

        It leaves the paddle going up, with the opposite horizontal velocity to
        the first ball, so the two balls diverge instead of travelling as one.
        """
        if len(self.balls) >= _MAX_BALLS:
            return
        ball = self._ball_on_paddle()
        ball.vy = -1
        ball.vx = -self.vx if self.vx else self._rng.choice((-1, 1))
        self.balls.append(ball)

    def _resize_paddle(self, steps: int) -> None:
        """Widen (``steps > 0``) or narrow (``steps < 0``) the bar.

        The width moves by one pixel per side (two in total) and stops at the
        paddle bounds; the bar keeps its centre and never leaves the field.
        """
        width = self.paddle_width
        self.paddle_grow = _clamp(self.paddle_grow + steps, *self._paddle_grow_bounds())
        new_width = self.paddle_width
        if new_width == width:
            return
        center = self.paddle_x + width // 2
        self.paddle_x = _clamp(
            center - new_width // 2, 0, max(0, self.width - new_width)
        )

    def _drop_ball(self, ball: _Ball) -> None:
        """Remove a ball that left the field, spending a life if it was the last."""
        if ball in self.balls:
            self.balls.remove(ball)
        if not self.balls:
            self._lose_ball()

    def _lose_ball(self) -> None:
        """Consume a life, resetting to ``READY`` or ending the game.

        A fresh ball is put back on the paddle and the falling pickups are
        cleared; the paddle keeps whatever width it had.
        """
        self.lives -= 1
        self.balls = [self._ball_on_paddle()]
        self._move_carry = 0.0
        self._bonus_carry = 0.0
        self.bonuses = []
        if self.lives <= 0:
            self.lives = 0
            self.state = self.GAME_OVER
        else:
            self.state = self.READY

    def advance_level(self) -> None:
        """Start the next (harder) level once the current wall is cleared.

        Score and lives carry over, and reaching a multiple of
        :data:`_LEVELS_PER_LIFE` rewards the player with an extra life - but only
        while they sit below :data:`_MAX_LEVEL_LIVES`, so the reward tops the
        player back up instead of growing without bound. Only the ball is put
        back on the (re-centred) paddle, ready to be launched again, and the
        falling pickups are cleared. The paddle keeps the width it had (only
        :meth:`reset` restores the base width). Levels are endless, so this never
        ends the game - running out of lives does.
        """
        self.level += 1
        self.level_score = 0
        if self.level % _LEVELS_PER_LIFE == 0 and self.lives < _MAX_LEVEL_LIVES:
            self.lives += 1
        self.paddle_x = (self.width - self.paddle_width) // 2
        self.paddle_vx = 0
        self._dry_bounces = 0
        self._move_carry = 0.0
        self._bonus_carry = 0.0
        self.balls = [self._ball_on_paddle()]
        self.bonuses = []
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
        # Pickups: half a brick each, in the colour of their effect.
        for bonus in self.bonuses:
            draw.rectangle(
                [
                    bonus.x,
                    bonus.y,
                    bonus.x + BONUS_WIDTH - 1,
                    bonus.y + BONUS_HEIGHT - 1,
                ],
                fill=BONUS_COLORS[bonus.kind],
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
        for ball in self.balls:
            if 0 <= ball.x < self.width and 0 <= ball.y < self.height:
                draw.point((ball.x, ball.y), fill=BALL_COLOR)
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
    bonus_chance: float = _BONUS_DROP_CHANCE,
    panel: Optional[Panel] = None,
) -> None:
    """Play the brick-breaker demo on the panel.

    Everything (field + HUD) is drawn on the LED matrix. A *minimal* tkinter
    window is opened only to capture the keyboard, which gives a smooth,
    continuously moving paddle.

    Levels are endless: clearing a wall builds a taller, denser and faster one
    and rewards the player with an extra life every five levels - but only while
    below five lives, so the reward tops the player back up instead of growing
    without bound - and the run only ends when the player runs out of lives.

    Destroyed bricks can drop a pickup the paddle may catch: an extra life, a
    second ball, a wider bar - or, for the red malus, a narrower one.

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
        bonus_chance: Probability for a destroyed brick to drop a pickup (0..1,
            0.15 by default); 0 disables pickups.
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
            bonus_chance=bonus_chance,
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
                f"balls={len(game.balls)}  "
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
