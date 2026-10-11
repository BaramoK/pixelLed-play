"""Unit tests for the brick-breaker game logic (no BLE / no device required).

``Panel.__new__`` is used to bypass the constructor (which would open a real
Bluetooth connection) and inject a fake client that records the payloads, so
the whole "game -> frame -> panel" chain can be checked without hardware.
"""

import io

import pytest
from PIL import Image

from pixelled_play.games.breakout import (
    BALL_COLOR,
    BONUS_BALL,
    BONUS_COLORS,
    BONUS_GROW,
    BONUS_HEIGHT,
    BONUS_KINDS,
    BONUS_LIFE,
    BONUS_SHRINK,
    BONUS_WIDTH,
    BRICK_PITCH,
    BRICK_SIZE,
    HUD_LEVEL_COLOR,
    HUD_LIVES_COLOR,
    HUD_OVER_COLOR,
    HUD_SCORE_COLOR,
    PADDLE_COLOR,
    PADDLE_EDGE_COLOR,
    PADDLE_MAX_WIDTH,
    PADDLE_MIN_WIDTH,
    Breakout,
    _Bonus,
    _LEVELS_PER_LIFE,
    _LEVELS_PER_STEP,
    _MAX_BALLS,
    _MAX_LEVEL_LIVES,
    _MAX_SPEED,
    _START_ROWS,
)
from pixelled_lib.library.panel import Panel


class FakeClient:
    """Records calls to ``send_image_hex`` instead of talking to hardware."""

    def __init__(self):
        self.calls = []

    def send_image_hex(self, hex_string, extension, *args, **kwargs):
        self.calls.append((hex_string, extension))


def _make_panel(width=32, height=32):
    panel = Panel.__new__(Panel)  # bypass __init__ (no BLE)
    panel._client = FakeClient()
    panel.width = width
    panel.height = height
    panel.led_type = "RGB"
    panel.device_info = None
    return panel


def _decode(hex_string):
    return Image.open(io.BytesIO(bytes.fromhex(hex_string))).convert("RGB")


def _lowest_free_brick(game):
    """Return the origin of a brick with nothing directly below it."""
    candidates = [
        b for b in game.bricks if (b[0], b[1] + BRICK_PITCH) not in game.bricks
    ]
    return max(candidates or list(game.bricks), key=lambda b: b[1])


def _hit_brick_from_below(game, bx, by):
    """Destroy the brick at ``(bx, by)`` with the ball coming straight up."""
    game.ball_x, game.ball_y = bx, by + BRICK_SIZE
    game.vx, game.vy = 0, -1
    game.step()


def _pickup_above_the_paddle(game, kind):
    """Put a pickup one row above the paddle, where it falls onto the bar."""
    bonus = _Bonus(game.paddle_x, game.paddle_y - 1, kind)
    game.bonuses.append(bonus)
    return bonus


def _catch_pickup(game, kind, ticks=4):
    """Drop a pickup above the paddle and step until the bar catches it.

    Pickups fall at half the ball's speed, so the one-row drop takes a couple of
    ticks (two at the level 1 speed); ``ticks`` bounds the loop, so a pickup that
    is never caught fails the test instead of hanging it.
    """
    bonus = _pickup_above_the_paddle(game, kind)
    for _ in range(ticks):
        game.step()
        if bonus not in game.bonuses:
            break
    return bonus


def test_initial_state():
    game = Breakout(32, 32, seed=1)
    assert game.lives == 3
    assert game.score == 0
    assert game.state == Breakout.READY
    assert game.paddle_x == (32 - game.paddle_width) // 2
    assert game.ball_y == game.paddle_y - 1
    assert game.total_bricks > 0


def test_bricks_stay_within_the_field():
    game = Breakout(32, 16, seed=2)
    for x, y in game.bricks:
        assert 0 <= x
        assert x + BRICK_SIZE - 1 < game.width
        assert game.hud_rows <= y
        assert y + BRICK_SIZE - 1 < game.paddle_y


def test_brick_at_maps_every_pixel_of_a_brick_to_its_origin():
    game = Breakout(32, 32, seed=1)
    bx, by = min(game.bricks)
    for dy in range(BRICK_SIZE):
        for dx in range(BRICK_SIZE):
            assert game._brick_at(bx + dx, by + dy) == (bx, by)
    game.clear_bricks()
    assert game._brick_at(bx, by) is None
    assert game._brick_at(0, 0) is None
    assert game._brick_at(-1, 0) is None


def test_paddle_is_two_rows_tall():
    game = Breakout(32, 32, seed=1)
    assert game.paddle_height == 2
    assert game.paddle_y == game.height - game.paddle_height
    assert game.paddle_bottom == game.height - 1
    img = game.render()
    # Every pixel of both rows belongs to the bar: white, or a red-orange tip.
    for x in range(game.paddle_x, game.paddle_x + game.paddle_width):
        assert img.getpixel((x, game.paddle_y)) in (PADDLE_COLOR, PADDLE_EDGE_COLOR)
        assert img.getpixel((x, game.paddle_bottom)) in (
            PADDLE_COLOR,
            PADDLE_EDGE_COLOR,
        )
    assert img.getpixel((game.paddle_x, game.paddle_y - 1)) == (0, 0, 0)


def test_paddle_tips_are_red_orange():
    """The two end columns of the bar are red-orange, the middle stays white."""
    for width, height in ((16, 16), (32, 32), (64, 32)):
        game = Breakout(width, height, seed=1)
        assert game.paddle_width > 2  # otherwise no white middle would be left
        img = game.render()
        left = game.paddle_x
        right = game.paddle_x + game.paddle_width - 1
        center = (left + right) // 2
        for y in (game.paddle_y, game.paddle_bottom):
            assert img.getpixel((left, y)) == PADDLE_EDGE_COLOR
            assert img.getpixel((right, y)) == PADDLE_EDGE_COLOR
            assert img.getpixel((center, y)) == PADDLE_COLOR
        # The tips are painted over the bar only: nothing bleeds outside of it.
        assert img.getpixel((left - 1, game.paddle_y)) == (0, 0, 0)
        assert img.getpixel((right + 1, game.paddle_y)) == (0, 0, 0)


def test_bricks_are_two_by_two_pixels():
    game = Breakout(32, 32, seed=1)
    img = game.render()
    bx, by = min(game.bricks)
    color = game.brick_colors[(bx, by)]
    for dy in range(BRICK_SIZE):
        for dx in range(BRICK_SIZE):
            assert img.getpixel((bx + dx, by + dy)) == color
    # Bricks are laid out edge to edge: a missing brick is a black 2x2 hole.
    holes = [
        (bx + BRICK_PITCH, by)
        for bx, by in game.bricks
        if (bx + BRICK_PITCH, by) not in game.bricks
        and bx + BRICK_PITCH + BRICK_SIZE - 1 < game.width
    ]
    assert holes
    hx, hy = holes[0]
    for dy in range(BRICK_SIZE):
        for dx in range(BRICK_SIZE):
            assert img.getpixel((hx + dx, hy + dy)) == (0, 0, 0)


def test_wall_stays_clear_of_the_paddle():
    for width, height in ((16, 16), (32, 16), (32, 32), (64, 32)):
        game = Breakout(width, height, seed=3)
        assert game.bricks
        lowest = max(y for _, y in game.bricks)
        assert lowest + BRICK_SIZE - 1 < game.paddle_y


def test_wall_grid_is_aligned_on_the_brick_pitch():
    assert BRICK_PITCH == BRICK_SIZE
    game = Breakout(32, 32, seed=5)
    for x, y in game.bricks:
        assert x % BRICK_PITCH == 0
        assert y % BRICK_PITCH == 0
        assert y >= game.hud_rows


def test_layout_is_deterministic_for_a_seed():
    a = Breakout(32, 32, seed=42)
    b = Breakout(32, 32, seed=42)
    assert a.bricks == b.bricks
    assert a.total_bricks == b.total_bricks


def test_different_seeds_give_different_walls():
    a = Breakout(32, 32, seed=1)
    b = Breakout(32, 32, seed=2)
    assert a.bricks != b.bricks


def test_paddle_is_clamped_to_the_field():
    game = Breakout(32, 32, seed=1)
    game.move_paddle(-1000)
    assert game.paddle_x == 0
    game.move_paddle(1000)
    assert game.paddle_x == game.width - game.paddle_width


def test_ball_follows_the_paddle_before_launch():
    game = Breakout(32, 32, seed=1)
    game.move_paddle(-2)
    assert game.ball_x == game.paddle_x + game.paddle_width // 2


def test_launch_then_step_moves_the_ball_up():
    game = Breakout(32, 32, seed=1)
    y0 = game.ball_y
    game.launch()
    assert game.state == Breakout.RUNNING
    game.step()
    assert game.ball_y == y0 - 1


def test_ball_bounces_off_the_left_wall():
    game = Breakout(32, 32, seed=1)
    game.launch()
    game.ball_x, game.ball_y = 0, 20
    game.vx, game.vy = -1, -1
    game.step()
    assert game.vx == 1
    assert game.ball_x == 0


def test_ball_bounces_off_the_right_wall():
    game = Breakout(32, 32, seed=1)
    game.launch()
    game.ball_x, game.ball_y = 31, 20
    game.vx, game.vy = 1, -1
    game.step()
    assert game.vx == -1
    assert game.ball_x == 31


def test_ball_bounces_below_the_hud():
    game = Breakout(32, 32, seed=1)
    game.launch()
    game.ball_x, game.ball_y = 1, game.hud_rows
    game.vx, game.vy = 1, -1
    game.step()
    assert game.vy == 1
    assert game.ball_y == game.hud_rows


def test_ball_destroys_a_brick_and_scores():
    game = Breakout(32, 32, seed=1)
    game.launch()
    # A brick with nothing directly below it, so the ball can approach from the
    # bottom without touching another brick first.
    candidates = [
        b for b in game.bricks if (b[0], b[1] + BRICK_PITCH) not in game.bricks
    ]
    bx, by = max(candidates, key=lambda b: b[1])
    game.ball_x, game.ball_y = bx, by + BRICK_SIZE
    game.vx, game.vy = 0, -1
    game.step()
    assert (bx, by) not in game.bricks
    assert game.score == 1
    assert game.vy == 1


def test_ball_bounces_off_the_paddle():
    game = Breakout(32, 32, seed=1)
    game.launch()
    game.ball_x = game.paddle_x + game.paddle_width // 2
    game.ball_y = game.paddle_y - 1
    game.vx, game.vy = 1, 1
    game.step()
    assert game.vy == -1
    assert game.ball_y == game.paddle_y - 1


def test_paddle_side_deflects_the_ball():
    game = Breakout(32, 32, seed=1)
    game.launch()
    game.move_paddle(0)
    game.ball_x = game.paddle_x + 1  # left of centre
    game.ball_y = game.paddle_y - 1
    game.vx, game.vy = 1, 1
    game.step()
    assert game.vy == -1
    assert game.vx == -1


def test_moving_paddle_spins_a_centred_ball():
    game = Breakout(32, 32, seed=1)
    game.launch()
    game.move_paddle(1)  # the paddle is moving right
    # Aim so that after this tick's horizontal move the ball lands exactly on
    # the paddle centre, which is where "spin" applies.
    center = game.paddle_x + game.paddle_width // 2
    game.vx, game.vy = -1, 1
    game.ball_x, game.ball_y = center - game.vx, game.paddle_y - 1
    game.step()
    assert game.vy == -1
    assert game.vx == 1


def test_anti_stall_nudge_fires_after_dry_bounces():
    game = Breakout(32, 32, seed=1)
    game.launch()

    def hit_paddle():
        game.ball_x = game.paddle_x + game.paddle_width // 2
        game.ball_y = game.paddle_y - 1
        game.vx, game.vy = 1, 1
        game.step()

    for _ in range(4):
        hit_paddle()
    assert game._dry_bounces == 4  # counting, no nudge yet
    hit_paddle()
    assert game._dry_bounces == 0  # the nudge fired and reset the counter


def test_diagonal_move_into_a_brick_destroys_it():
    """Regression: a diagonal move must not tunnel straight into a brick."""
    game = Breakout(32, 32, seed=1)
    game.launch()
    # Two isolated bricks: the ball comes in diagonally at the bottom-left
    # corner of one of them, so only the "destination cell itself" check can
    # stop it. The second brick keeps the wall from being cleared (which would
    # start the next level and rebuild a fresh wall).
    game.clear_bricks()
    game.bricks.add((10, 10))
    game.brick_colors[(10, 10)] = (255, 0, 0)
    game.bricks.add((20, 10))
    game.brick_colors[(20, 10)] = (0, 255, 0)
    game.ball_x, game.ball_y = 9, 12
    game.vx, game.vy = 1, -1
    game.step()
    assert (10, 10) not in game.bricks
    assert (game.ball_x, game.ball_y) != (10, 10)
    assert game.vx == -1 and game.vy == 1
    assert game.level == 1
    assert game.level_score == 1


def test_tracking_paddle_clears_the_wall_and_levels_up():
    """A paddle that follows the ball must be able to clear a whole wall."""
    # Pickups are disabled: a caught extra life (or a narrower bar) would make
    # the score and lives assertions below depend on the drop luck.
    game = Breakout(32, 32, seed=1, bonus_chance=0.0)
    first_wall = game.total_bricks
    game.launch()
    ticks = 0
    while (
        game.level == 1
        and game.state != Breakout.GAME_OVER
        and ticks < 60000
    ):
        target = game.ball_x - game.paddle_width // 2
        if game.paddle_x < target:
            game.move_paddle(1)
        elif game.paddle_x > target:
            game.move_paddle(-1)
        else:
            game.move_paddle(0)
        if game.state == Breakout.READY:
            game.launch()
        game.step()
        assert game._brick_at(game.ball_x, game.ball_y) is None
        assert game.hud_rows <= game.ball_y <= game.height
        ticks += 1
    assert game.level == 2
    assert game.state == Breakout.READY
    assert game.score == first_wall
    assert game.lives == 3  # no extra life before the first 5 levels are cleared


def test_lost_ball_decrements_lives_and_resets():
    game = Breakout(32, 32, seed=1)
    game.launch()
    game.ball_x, game.ball_y = 1, game.height - 1
    game.vx, game.vy = 1, 1
    game.step()
    assert game.lives == 2
    assert game.state == Breakout.READY
    assert game.ball_y == game.paddle_y - 1


def test_game_over_after_three_lost_balls():
    game = Breakout(32, 32, seed=1)
    for _ in range(3):
        game.launch()
        game.ball_x, game.ball_y = 1, game.height - 1
        game.vx, game.vy = 1, 1
        game.step()
    assert game.lives == 0
    assert game.state == Breakout.GAME_OVER
    game.step()  # no-op once the game is over
    assert game.state == Breakout.GAME_OVER


def test_clearing_the_wall_advances_to_the_next_level():
    game = Breakout(32, 32, seed=1)
    game.launch()
    game.clear_bricks()
    game.ball_x, game.ball_y = 16, 20
    game.vx, game.vy = 1, 1
    game.step()
    assert game.level == 2
    assert game.state == Breakout.READY
    assert game.bricks  # a fresh wall was built for the new level
    assert game.level_score == 0


def test_advance_level_keeps_the_score_and_does_not_award_a_life_yet():
    game = Breakout(32, 32, seed=1, lives=3)
    game.score = 7
    game.launch()
    game.clear_bricks()
    game.ball_x, game.ball_y = 16, 20
    game.vx, game.vy = 1, 1
    game.step()
    assert game.level == 2
    assert game.lives == 3  # the first extra life only comes at level 5
    assert game.score == 7  # the running total is untouched
    assert game.level_score == 0  # but the per-level bar resets


def test_a_life_is_awarded_every_five_cleared_levels():
    game = Breakout(32, 32, seed=1, lives=3)
    milestones = {_LEVELS_PER_LIFE, 2 * _LEVELS_PER_LIFE}
    for level in range(2, 2 * _LEVELS_PER_LIFE + 2):
        game.launch()
        game.clear_bricks()
        game.ball_x, game.ball_y = 16, 20
        game.vx, game.vy = 1, 1
        game.step()
        assert game.level == level
        # exactly one extra life at each milestone, none in between
        assert game.lives == 3 + sum(1 for m in milestones if m <= level)


def test_starting_level_can_be_chosen():
    game = Breakout(32, 32, seed=1, level=3)
    assert game.level == 3
    assert game.initial_level == 3


def test_starting_level_comes_with_the_lives_it_would_have_earned():
    """``--level 10`` must start with the extra lives levels 5 and 10 award."""
    for level, expected in ((1, 3), (4, 3), (5, 4), (9, 4), (10, 5), (15, 5)):
        game = Breakout(32, 32, seed=1, lives=3, level=level)
        assert game.lives == expected
        # the reward tops the player back up to _MAX_LEVEL_LIVES, never past it
        assert game.lives == min(_MAX_LEVEL_LIVES, 3 + level // _LEVELS_PER_LIFE)


def test_level_reward_stops_at_the_lives_cap():
    """Once at the cap, clearing further milestones never adds a life."""
    game = Breakout(32, 32, seed=1, lives=_MAX_LEVEL_LIVES)
    for _ in range(3 * _LEVELS_PER_LIFE):  # cross levels 5, 10 and 15
        game.launch()
        game.clear_bricks()
        game.ball_x, game.ball_y = 16, 20
        game.vx, game.vy = 1, 1
        game.step()
    assert game.level == 1 + 3 * _LEVELS_PER_LIFE
    assert game.lives == _MAX_LEVEL_LIVES  # already at the cap: no reward


def test_level_reward_tops_up_towards_the_lives_cap():
    """A player below the cap is topped up to it, then stops earning lives."""
    game = Breakout(32, 32, seed=1, lives=_MAX_LEVEL_LIVES - 1)
    for _ in range(_LEVELS_PER_LIFE - 1):  # level 1 -> level 5
        game.launch()
        game.clear_bricks()
        game.ball_x, game.ball_y = 16, 20
        game.vx, game.vy = 1, 1
        game.step()
    assert game.level == _LEVELS_PER_LIFE
    assert game.lives == _MAX_LEVEL_LIVES  # topped up: 4 -> 5
    for _ in range(_LEVELS_PER_LIFE):  # level 5 -> level 10
        game.launch()
        game.clear_bricks()
        game.ball_x, game.ball_y = 16, 20
        game.vx, game.vy = 1, 1
        game.step()
    assert game.level == 2 * _LEVELS_PER_LIFE
    assert game.lives == _MAX_LEVEL_LIVES  # already at the cap: no reward


def test_density_starts_at_the_default_and_gains_a_tenth_every_five_levels():
    def density(level, **kwargs):
        return Breakout(32, 32, seed=1, level=level, **kwargs).level_density

    assert density(1) == pytest.approx(0.5)  # the base density is the level 1 one
    assert density(_LEVELS_PER_STEP) == pytest.approx(0.5)  # nothing before level 6
    assert density(_LEVELS_PER_STEP + 1) == pytest.approx(0.6)
    assert density(2 * _LEVELS_PER_STEP + 1) == pytest.approx(0.7)
    assert density(26) == 1.0  # a full wall, capped
    assert density(1000) == 1.0
    assert density(_LEVELS_PER_STEP + 1, density=0.5) == pytest.approx(0.6)


def test_speed_grows_by_a_tenth_every_five_levels_and_is_capped():
    def speed(level, **kwargs):
        return Breakout(32, 32, seed=1, level=level, **kwargs).level_speed

    assert speed(1) == 1.0
    assert speed(_LEVELS_PER_STEP) == 1.0  # nothing before level 6
    assert speed(_LEVELS_PER_STEP + 1) == pytest.approx(1.1)
    assert speed(2 * _LEVELS_PER_STEP + 1) == pytest.approx(1.21)  # compounding
    # 11 steps of +10 % still fit under the cap; the 12th does not.
    assert speed(56) == pytest.approx(1.1**11)
    assert 1.1**12 > _MAX_SPEED
    assert speed(61) == _MAX_SPEED  # the cap kicks in around level 61
    assert speed(1000) == _MAX_SPEED
    assert speed(_LEVELS_PER_STEP + 1, speed=2.0) == pytest.approx(2.2)


def test_wall_gains_a_row_every_five_levels_up_to_the_panel_height():
    def rows(level):
        return Breakout(32, 32, seed=1, level=level).brick_rows

    assert rows(1) == _START_ROWS == 3
    assert rows(_LEVELS_PER_STEP) == 3  # nothing before level 6
    assert rows(_LEVELS_PER_STEP + 1) == 4
    assert rows(2 * _LEVELS_PER_STEP + 1) == 5
    assert rows(3 * _LEVELS_PER_STEP + 1) == 6
    assert rows(4 * _LEVELS_PER_STEP + 1) == 7
    assert rows(5 * _LEVELS_PER_STEP + 1) == 8  # a 32x32 panel fits eight rows
    assert rows(1000) == 8  # never taller than the panel


def test_wall_rows_are_capped_by_the_panel_size():
    # A 16x16 panel only has room for four brick rows, whatever the level.
    assert Breakout(16, 16, seed=1, level=1).brick_rows == _START_ROWS
    assert Breakout(16, 16, seed=1, level=1000).brick_rows == 4


def test_fractional_speed_is_averaged_over_ticks():
    """A 1.1 px/tick ball covers 11 px in 10 ticks (2 px on the tenth)."""
    game = Breakout(32, 32, seed=1, level=_LEVELS_PER_STEP + 1)  # 1.1 px/tick
    game.clear_bricks()
    # A single far-away brick keeps the level from ending (an empty wall would
    # advance to the next level and rebuild).
    game.bricks.add((0, 0))
    game.brick_colors[(0, 0)] = (255, 0, 0)
    game.launch()
    game.ball_x, game.ball_y = 8, 25
    game.vx, game.vy = 0, -1  # straight up: no side wall bounce to confuse us
    for _ in range(10):
        game.step()
    assert game.ball_y == 25 - 11
    assert game.level == _LEVELS_PER_STEP + 1  # no level-up mid-measurement
    assert game.state == Breakout.RUNNING


def test_reset_returns_to_the_starting_level():
    game = Breakout(32, 32, seed=1, level=5)
    assert game.lives == 4  # the starting level already earned one extra life
    game.launch()
    game.clear_bricks()
    game.ball_x, game.ball_y = 16, 20
    game.vx, game.vy = 1, 1
    game.step()
    assert game.level == 6
    game.launch()
    game.ball_x, game.ball_y = 1, game.height - 1  # lose the ball
    game.vx, game.vy = 1, 1
    game.step()
    assert game.lives == 3
    game.reset(seed=1)
    assert game.level == 5
    assert game.score == 0
    assert game.level_score == 0
    assert game.lives == 4  # the starting level's extra life is granted again


def test_render_returns_rgb_at_panel_size():
    game = Breakout(32, 16, seed=1)
    img = game.render()
    assert img.mode == "RGB"
    assert img.size == (32, 16)


def test_render_draws_paddle_and_ball_on_a_black_background():
    game = Breakout(32, 32, seed=1)
    game.launch()
    game.ball_x, game.ball_y = 5, 20
    img = game.render()
    assert img.getpixel((5, 20)) == BALL_COLOR
    center = game.paddle_x + game.paddle_width // 2
    assert img.getpixel((center, game.paddle_y)) == PADDLE_COLOR
    assert img.getpixel((game.paddle_x, game.paddle_y)) == PADDLE_EDGE_COLOR
    assert img.getpixel((31, 31)) == (0, 0, 0)


def test_hud_shows_remaining_lives_on_the_right():
    game = Breakout(32, 32, seed=1, lives=3)
    img = game.render()
    for i in range(3):
        assert img.getpixel((31 - i, 0)) == HUD_LIVES_COLOR


def test_hud_score_bar_grows_with_the_level_progress():
    game = Breakout(32, 32, seed=1)
    game.level_score = 0
    empty = sum(
        1
        for x in range(game.width)
        if game.render().getpixel((x, 0)) == HUD_SCORE_COLOR
    )
    game.level_score = game.total_bricks
    full = sum(
        1
        for x in range(game.width)
        if game.render().getpixel((x, 0)) == HUD_SCORE_COLOR
    )
    assert empty == 0
    assert full > 0


def test_hud_is_red_on_game_over():
    game = Breakout(32, 32, seed=1)
    game.state = Breakout.GAME_OVER
    img = game.render()
    assert all(img.getpixel((x, 0)) == HUD_OVER_COLOR for x in range(game.width))


def test_hud_shows_one_pixel_per_level():
    game = Breakout(32, 32, seed=1)
    game.level = 1
    one = sum(
        1
        for x in range(game.width)
        if game.render().getpixel((x, 0)) == HUD_LEVEL_COLOR
    )
    game.level = 3
    three = sum(
        1
        for x in range(game.width)
        if game.render().getpixel((x, 0)) == HUD_LEVEL_COLOR
    )
    assert one == 1
    assert three == 3


def test_no_hud_leaves_the_top_row_to_the_field():
    game = Breakout(32, 32, seed=1, hud=False)
    assert game.hud_rows == 0
    img = game.render()
    # The lives pixels are the only white on the top row (the paddle and the
    # ball live at the bottom of the field), so their absence proves the HUD
    # was not drawn.
    assert all(img.getpixel((x, 0)) != HUD_LIVES_COLOR for x in range(game.width))
    # The freed row is simply the first row of the wall grid.
    full = Breakout(32, 32, seed=1, hud=False, density=1.0)
    assert min(y for _, y in full.bricks) == 0


def test_breakout_frame_is_pushed_to_the_panel():
    panel = _make_panel(32, 16)
    game = Breakout(panel.width, panel.height, seed=1)
    game.launch()
    game.ball_x, game.ball_y = 5, 10
    panel.send_frame(game.render())

    assert len(panel._client.calls) == 1
    hex_string, extension = panel._client.calls[0]
    assert extension == ".png"
    decoded = _decode(hex_string)
    assert decoded.size == (32, 16)
    center = game.paddle_x + game.paddle_width // 2
    assert decoded.getpixel((center, game.paddle_y)) == PADDLE_COLOR
    assert decoded.getpixel((game.paddle_x, game.paddle_y)) == PADDLE_EDGE_COLOR
    assert decoded.getpixel((5, 10)) == BALL_COLOR

def test_a_destroyed_brick_drops_a_pickup_at_its_origin():
    """The pickup is half a brick, left where the top-left of the brick was."""
    game = Breakout(32, 32, seed=1, bonus_chance=1.0)
    game.clear_bricks()
    for origin, color in (((10, 6), (255, 0, 0)), ((20, 6), (0, 255, 0))):
        game.bricks.add(origin)
        game.brick_colors[origin] = color
    game.launch()
    _hit_brick_from_below(game, 10, 6)
    assert (10, 6) not in game.bricks
    assert len(game.bonuses) == 1
    bonus = game.bonuses[0]
    assert (bonus.x, bonus.y) == (10, 6)  # the brick's own top-left corner
    assert bonus.kind in BONUS_KINDS


def test_a_pickup_is_half_a_brick():
    assert BONUS_WIDTH == BRICK_SIZE
    assert BONUS_HEIGHT == BRICK_SIZE // 2 == 1
    assert set(BONUS_COLORS) == set(BONUS_KINDS)


def test_no_pickup_is_dropped_when_the_drop_chance_is_zero():
    game = Breakout(32, 32, seed=1, bonus_chance=0.0)
    game.launch()
    for _ in range(5):
        _hit_brick_from_below(game, *_lowest_free_brick(game))
    assert game.score >= 5
    assert game.bonuses == []


def test_every_destroyed_brick_drops_a_pickup_when_the_chance_is_one():
    game = Breakout(32, 32, seed=1, bonus_chance=1.0)
    game.clear_bricks()
    column = [(10, 2 + BRICK_PITCH * i) for i in range(5)]  # five stacked bricks
    # A sixth, far-away brick keeps the wall from being cleared (which would
    # start the next level and wipe the falling pickups).
    for origin in column + [(20, 2)]:
        game.bricks.add(origin)
        game.brick_colors[origin] = (255, 0, 0)
    game.launch()
    for origin in reversed(column):  # bottom brick first, so each hit is clean
        _hit_brick_from_below(game, *origin)
        assert len(game.bonuses) == game.score  # exactly one pickup per brick
    assert game.score == len(column)
    assert game.level == 1


def test_the_drop_chance_is_clamped_to_a_probability():
    assert Breakout(32, 32, seed=1).bonus_chance == pytest.approx(0.15)
    assert Breakout(32, 32, seed=1, bonus_chance=-1).bonus_chance == 0.0
    assert Breakout(32, 32, seed=1, bonus_chance=2).bonus_chance == 1.0


def test_the_pickup_kind_is_reproducible_with_a_seed():
    def run(seed):
        game = Breakout(32, 32, seed=seed, bonus_chance=1.0)
        game.launch()
        for _ in range(8):
            _hit_brick_from_below(game, *_lowest_free_brick(game))
        return [bonus.kind for bonus in game.bonuses], game.score

    kinds, score = run(7)
    assert kinds == run(7)[0]  # same seed: same wall, same drops, same kinds
    assert len(kinds) == score >= 8
    assert all(kind in BONUS_KINDS for kind in kinds)


def test_a_pickup_falls_at_half_the_ball_speed():
    """At 1 px/tick the ball covers 4 px in 4 ticks, the pickup only 2."""
    game = Breakout(32, 32, seed=1, bonus_chance=0.0)
    game.launch()
    game.ball_x, game.ball_y = 1, 20  # well below the wall, above the paddle
    game.vx, game.vy = 1, -1
    bonus = _Bonus(20, 5, BONUS_LIFE)
    game.bonuses.append(bonus)
    for _ in range(4):
        game.step()
    assert game.ball_y == 20 - 4
    assert bonus.y == 5 + 2  # half the ball's 4 px
    assert game.bonuses == [bonus]


def test_a_pickup_falls_at_half_the_speed_when_it_is_fractional():
    """At 1.1 px/tick the ball covers 11 px in 10 ticks, the pickup 5."""
    game = Breakout(32, 32, seed=1, level=_LEVELS_PER_STEP + 1, bonus_chance=0.0)
    game.clear_bricks()
    game.bricks.add((0, 0))  # keep the wall from being cleared mid-measurement
    game.brick_colors[(0, 0)] = (255, 0, 0)
    game.launch()
    game.ball_x, game.ball_y = 8, 25
    game.vx, game.vy = 0, -1
    bonus = _Bonus(20, 20, BONUS_LIFE)  # outside the bar (12..19), so not caught
    game.bonuses.append(bonus)
    for _ in range(10):
        game.step()
    assert game.ball_y == 25 - 11
    assert bonus.y == 20 + 5  # 0.55 px/tick: 5 whole pixels, half the ball's 11


def test_a_fast_pickup_cannot_tunnel_through_the_paddle():
    """At the 3 px/tick cap the pickup still lands on the bar (1.5 px/tick)."""
    game = Breakout(32, 32, seed=1, level=61, bonus_chance=0.0)  # 3 px/tick
    game.launch()
    game.lives = 3
    bonus = _Bonus(game.paddle_x + 1, game.paddle_y - 2, BONUS_LIFE)
    game.bonuses.append(bonus)
    game.step()  # the ball covers three pixels, the pickup only one
    assert (bonus.y, game.lives) == (game.paddle_y - 1, 3)
    game.step()  # ...then two, still landing on the bar row rather than past it
    assert game.lives == 4
    assert game.bonuses == []


def test_a_missed_pickup_is_discarded_at_the_bottom():
    game = Breakout(32, 32, seed=1, bonus_chance=0.0)
    game.launch()
    game.ball_x, game.ball_y = 1, 20
    game.vx, game.vy = 1, -1
    # Against the right edge of the field: the bar (12..19) never reaches it.
    game.bonuses.append(_Bonus(game.width - 1, game.paddle_y - 2, BONUS_SHRINK))
    for _ in range(8):  # 4 px at half the ball's speed
        game.step()
    assert game.bonuses == []
    assert game.paddle_width == game.base_paddle_width  # the malus never applied


def test_a_pickup_falls_at_half_the_level_speed_over_a_long_fall():
    """30 px in 20 ticks at the 3 px/tick cap: half the ball's speed, exactly."""
    game = Breakout(32, 32, seed=1, level=61, bonus_chance=0.0)  # 3 px/tick
    game.launch()
    bonus = _Bonus(0, 2, BONUS_LIFE)  # left edge: the bar (12..19) misses it
    game.bonuses.append(bonus)
    for _ in range(20):
        game.ball_x, game.ball_y = 16, 20  # park the ball, so no life is lost
        game.vx, game.vy = 0, -1
        game.step()
    assert game.level_speed == _MAX_SPEED == 3.0
    assert bonus.y == 2 + 30  # 1.5 px/tick, i.e. exactly half
    assert game.bonuses == []  # and it is gone: discarded past the bottom row


def test_pickups_do_not_move_while_the_game_is_ready():
    game = Breakout(32, 32, seed=1, bonus_chance=0.0)
    assert game.state == Breakout.READY
    bonus = _Bonus(4, 10, BONUS_LIFE)
    game.bonuses.append(bonus)
    game.step()
    assert bonus.y == 10
    assert game.bonuses == [bonus]


def test_catching_a_life_pickup_adds_a_life():
    game = Breakout(32, 32, seed=1, lives=3, bonus_chance=0.0)
    game.launch()
    _catch_pickup(game, BONUS_LIFE)
    assert game.lives == 4
    assert game.bonuses == []  # a caught pickup is consumed


def test_catching_a_ball_pickup_sends_a_second_ball():
    game = Breakout(32, 32, seed=1, bonus_chance=0.0)
    game.launch()
    game.ball_x, game.ball_y = 5, 20
    game.vx, game.vy = 1, -1
    _catch_pickup(game, BONUS_BALL)
    assert len(game.balls) == 2
    first, extra = game.balls
    assert extra is not first
    assert extra.vy == -1  # it leaves the paddle going up
    assert (first.vx, extra.vx) == (1, -1)  # sent the other way: they diverge
    x0, x1 = first.x, extra.x
    game.step()
    assert first.x == x0 + 1  # ...and then travel on their own
    assert extra.x == x1 - 1


def test_the_number_of_balls_is_capped():
    game = Breakout(32, 32, seed=1, bonus_chance=0.0)
    game.launch()
    for _ in range(_MAX_BALLS + 2):
        _catch_pickup(game, BONUS_BALL)
    assert len(game.balls) == _MAX_BALLS


def test_losing_one_of_two_balls_does_not_cost_a_life():
    game = Breakout(32, 32, seed=1, bonus_chance=0.0)
    game.launch()
    _catch_pickup(game, BONUS_BALL)
    assert len(game.balls) == 2
    first, extra = game.balls
    first.x, first.y, first.vx, first.vy = 1, game.height - 1, 1, 1  # dropped
    extra.x, extra.y, extra.vx, extra.vy = 5, 20, 1, -1
    game.step()
    assert game.lives == 3  # only the last ball costs a life
    assert len(game.balls) == 1
    assert game.state == Breakout.RUNNING
    assert (game.ball_x, game.ball_y) == (6, 19)  # the game follows the survivor


def test_the_game_ends_when_the_last_ball_is_lost():
    game = Breakout(32, 32, seed=1, lives=1, bonus_chance=0.0)
    game.launch()
    _catch_pickup(game, BONUS_BALL)
    assert len(game.balls) == 2
    for ball in game.balls:
        ball.x, ball.y, ball.vx, ball.vy = 1, game.height - 1, 1, 1
    game.step()
    assert game.lives == 0
    assert game.state == Breakout.GAME_OVER


def test_catching_a_grow_pickup_widens_the_paddle_by_one_pixel_per_side():
    game = Breakout(32, 32, seed=1, bonus_chance=0.0)
    game.launch()
    width = game.paddle_width
    center = game.paddle_x + width // 2
    _catch_pickup(game, BONUS_GROW)
    assert game.paddle_width == width + 2  # one pixel on each side
    assert game.paddle_x == center - game.paddle_width // 2  # centre kept
    assert game.paddle_x + game.paddle_width <= game.width


def test_a_grow_pickup_keeps_the_paddle_inside_the_field():
    game = Breakout(32, 32, seed=1, bonus_chance=0.0)
    game.launch()
    game.move_paddle(-1000)  # the bar is against the left edge
    width = game.paddle_width
    _catch_pickup(game, BONUS_GROW)
    assert game.paddle_width == width + 2
    assert game.paddle_x == 0
    assert game.paddle_x + game.paddle_width <= game.width


def test_the_paddle_width_stops_at_its_bounds():
    game = Breakout(32, 32, seed=1, bonus_chance=0.0)
    assert game.paddle_width == game.base_paddle_width == 8  # 32 // 4
    game.launch()
    for kind, expected in (
        (BONUS_GROW, 10),
        (BONUS_GROW, PADDLE_MAX_WIDTH),
        (BONUS_GROW, PADDLE_MAX_WIDTH),  # capped
        (BONUS_SHRINK, PADDLE_MAX_WIDTH - 2),
        (BONUS_SHRINK, 8),  # back to the width the panel implies
        (BONUS_SHRINK, PADDLE_MIN_WIDTH),  # floored
    ):
        _catch_pickup(game, kind)
        assert game.paddle_width == expected


def test_the_paddle_bounds_never_override_the_panel_width():
    """A bar wider than the ceiling keeps its width; a narrower one keeps too."""
    wide = Breakout(64, 32, seed=1, bonus_chance=0.0)  # 64 // 4 = 16 px
    assert wide.paddle_width == 16 > PADDLE_MAX_WIDTH
    wide.launch()
    _catch_pickup(wide, BONUS_GROW)
    assert wide.paddle_width == 16  # already above the ceiling: no widening
    _catch_pickup(wide, BONUS_SHRINK)
    assert wide.paddle_width == 14  # but a malus still narrows it

    small = Breakout(16, 16, seed=1)  # 16 // 4 = 4 px
    assert small.base_paddle_width == 4 < PADDLE_MIN_WIDTH
    assert small.paddle_width == 4


def test_the_paddle_width_survives_a_level_change_but_not_a_replay():
    game = Breakout(32, 32, seed=1, bonus_chance=0.0)
    game.launch()
    _catch_pickup(game, BONUS_GROW)
    assert game.paddle_width == game.base_paddle_width + 2
    game.clear_bricks()
    game.ball_x, game.ball_y = 16, 20
    game.vx, game.vy = 1, 1
    game.step()
    assert game.level == 2
    assert game.paddle_width == game.base_paddle_width + 2  # kept across levels
    game.reset(seed=1)
    assert game.paddle_width == game.base_paddle_width  # a replay starts from scratch


def test_pickups_are_cleared_when_a_life_is_lost():
    game = Breakout(32, 32, seed=1, bonus_chance=0.0)
    game.launch()
    game.bonuses.append(_Bonus(4, 10, BONUS_LIFE))
    game.ball_x, game.ball_y = 1, game.height - 1
    game.vx, game.vy = 1, 1
    game.step()
    assert game.lives == 2
    assert game.bonuses == []


def test_pickups_are_cleared_when_the_level_advances():
    game = Breakout(32, 32, seed=1, bonus_chance=0.0)
    game.launch()
    game.bonuses.append(_Bonus(4, 10, BONUS_LIFE))
    game.clear_bricks()
    game.ball_x, game.ball_y = 16, 20
    game.vx, game.vy = 1, 1
    game.step()
    assert game.level == 2
    assert game.bonuses == []


def test_render_draws_pickups_with_their_kind_colour():
    game = Breakout(32, 32, seed=1, bonus_chance=0.0)
    for i, kind in enumerate(BONUS_KINDS):
        game.bonuses.append(_Bonus(4, 10 + 4 * i, kind))
    img = game.render()
    for bonus in game.bonuses:
        color = BONUS_COLORS[bonus.kind]
        for dx in range(BONUS_WIDTH):
            assert img.getpixel((bonus.x + dx, bonus.y)) == color
        # Half a brick: exactly one row tall, and nothing bleeds around it.
        assert img.getpixel((bonus.x, bonus.y + 1)) == (0, 0, 0)
        assert img.getpixel((bonus.x - 1, bonus.y)) == (0, 0, 0)


