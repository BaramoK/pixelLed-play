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
    BRICK_PITCH,
    BRICK_SIZE,
    HUD_LEVEL_COLOR,
    HUD_LIVES_COLOR,
    HUD_OVER_COLOR,
    HUD_SCORE_COLOR,
    PADDLE_COLOR,
    Breakout,
    _LEVELS_PER_LIFE,
    _LEVELS_PER_STEP,
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
    for x in range(game.paddle_x, game.paddle_x + game.paddle_width):
        assert img.getpixel((x, game.paddle_y)) == PADDLE_COLOR
        assert img.getpixel((x, game.paddle_bottom)) == PADDLE_COLOR
    assert img.getpixel((game.paddle_x, game.paddle_y - 1)) == (0, 0, 0)


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
    game = Breakout(32, 32, seed=1)
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
    for level, expected in ((1, 3), (4, 3), (5, 4), (9, 4), (10, 5), (15, 6)):
        game = Breakout(32, 32, seed=1, lives=3, level=level)
        assert game.lives == expected
        assert game.lives == 3 + level // _LEVELS_PER_LIFE


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
    assert img.getpixel((game.paddle_x, game.paddle_y)) == PADDLE_COLOR
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
    assert decoded.getpixel((game.paddle_x, game.paddle_y)) == PADDLE_COLOR
    assert decoded.getpixel((5, 10)) == BALL_COLOR

