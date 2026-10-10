"""Unit tests for the brick-breaker game logic (no BLE / no device required).

``Panel.__new__`` is used to bypass the constructor (which would open a real
Bluetooth connection) and inject a fake client that records the payloads, so
the whole "game -> frame -> panel" chain can be checked without hardware.
"""

import io

from PIL import Image

from pixelled_play.games.breakout import (
    BALL_COLOR,
    BRICK_PITCH,
    BRICK_SIZE,
    HUD_LIVES_COLOR,
    HUD_OVER_COLOR,
    HUD_SCORE_COLOR,
    HUD_WIN_COLOR,
    PADDLE_COLOR,
    Breakout,
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
    # A single isolated brick: the ball comes in diagonally at its bottom-left
    # corner, so only the "destination cell itself" check can stop it.
    game.clear_bricks()
    game.bricks.add((10, 10))
    game.brick_colors[(10, 10)] = (255, 0, 0)
    game.ball_x, game.ball_y = 9, 12
    game.vx, game.vy = 1, -1
    game.step()
    assert (10, 10) not in game.bricks
    assert (game.ball_x, game.ball_y) != (10, 10)
    assert game.vx == -1 and game.vy == 1


def test_tracking_paddle_clears_the_whole_wall():
    """A paddle that follows the ball must be able to finish the level."""
    game = Breakout(32, 32, seed=1)
    game.launch()
    ticks = 0
    while game.state in (Breakout.RUNNING, Breakout.READY) and ticks < 60000:
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
    assert game.state == Breakout.WON
    assert game.score == game.total_bricks


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


def test_win_when_all_bricks_are_cleared():
    game = Breakout(32, 32, seed=1)
    game.launch()
    game.clear_bricks()
    game.ball_x, game.ball_y = 16, 20
    game.vx, game.vy = 1, 1
    game.step()
    assert game.state == Breakout.WON


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


def test_hud_score_bar_grows_with_score():
    game = Breakout(32, 32, seed=1)
    game.score = 0
    empty = sum(
        1
        for x in range(game.width)
        if game.render().getpixel((x, 0)) == HUD_SCORE_COLOR
    )
    game.score = game.total_bricks
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


def test_hud_is_green_when_won():
    game = Breakout(32, 32, seed=1)
    game.state = Breakout.WON
    img = game.render()
    assert all(img.getpixel((x, 0)) == HUD_WIN_COLOR for x in range(game.width))


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

