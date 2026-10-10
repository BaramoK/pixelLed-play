"""Unit tests for the Tetris game logic (no BLE / no device required).

``Panel.__new__`` is used to bypass the constructor (which would open a real
Bluetooth connection) and inject a fake client that records the payloads, so
the whole "game -> frame -> panel" chain can be checked without hardware.
"""

import io

from PIL import Image

from pixelled_lib.library.keys import KeyRepeater
from pixelled_lib.library.panel import Panel
from pixelled_play.games.tetris import (
    BLOCK_H,
    BLOCK_W,
    FIELD_COLS,
    HUD_DIVIDER_COLOR,
    HUD_LEVEL_COLOR,
    HUD_OVER_COLOR,
    HUD_PAUSE_COLOR,
    HUD_SCORE_COLOR,
    HUD_W,
    PIECE_COLORS,
    PREVIEW_CELLS,
    TETROMINOES,
    Tetris,
)

DUMMY = (100, 100, 100)  # stands for "already locked" cells in the tests


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


def _colors(img):
    """Set of colors present in ``img`` (``getdata`` is deprecated in Pillow)."""
    return {color for _, color in img.getcolors(maxcolors=1 << 16)}


def _empty_grid(game):
    """Wipe the field and the falling piece, for hand-built board tests."""
    game.grid = [[None] * game.cols for _ in range(game.rows)]
    game.piece = None
    return game


def _check_invariants(game):
    """Assert what a real game can never break, whatever the input."""
    assert len(game.grid) == game.rows
    for row in game.grid:
        assert len(row) == game.cols
        assert any(cell is None for cell in row)  # full rows are always cleared
        for cell in row:
            assert cell is None or cell in PIECE_COLORS.values()
    if game.state != Tetris.GAME_OVER:
        assert game.piece in PIECE_COLORS
        assert not game._collides(game.piece_cells())


def _played_pieces(game, count=14):
    """Play ``count`` pieces on a self-emptying board and return their names."""
    pieces = []
    for _ in range(count):
        pieces.append(game.piece)
        game.hard_drop()
        game.grid = [[None] * game.cols for _ in range(game.rows)]
    return pieces


# -- geometry -------------------------------------------------------------


def test_geometry_fills_a_32x32_panel_exactly():
    game = Tetris(32, 32)
    assert (game.cell_w, game.cell_h) == (BLOCK_W, BLOCK_H) == (2, 2)
    assert (game.cols, game.rows) == (FIELD_COLS, 16)
    assert (game.field_width, game.field_height) == (20, 32)
    assert (game.field_x, game.field_y) == (0, 0)
    assert (game.hud_x, game.hud_w) == (20, 12)
    # Field + HUD band covers the whole matrix, without wasting a pixel.
    assert game.field_x + game.field_width + game.hud_w == game.width
    assert game.field_y + game.field_height == game.height


def test_geometry_never_overflows_the_panel():
    sizes = (
        (6, 6),
        (8, 8),
        (16, 16),
        (32, 6),
        (32, 16),
        (16, 32),
        (32, 32),
        (64, 32),
        (64, 64),
    )
    for width, height in sizes:
        game = Tetris(width, height)
        assert game.cell_w >= 1 and game.cell_h >= 1
        assert 1 <= game.cols <= FIELD_COLS
        assert game.rows >= 1
        assert game.field_x >= 0 and game.field_y >= 0
        assert game.field_x + game.field_width + game.hud_w <= width
        assert game.field_y + game.field_height <= height
        assert game.hud_w in (0, HUD_W)
        assert len(game.grid) == game.rows
        assert all(len(row) == game.cols for row in game.grid)


def test_blocks_are_never_scaled():
    # A block is 2x2 px whatever the panel size - never stretched, never shrunk.
    for width, height in ((16, 16), (32, 16), (32, 32), (64, 32), (64, 64)):
        game = Tetris(width, height)
        assert (game.cell_w, game.cell_h) == (BLOCK_W, BLOCK_H) == (2, 2)
    # And the field stays the classic 10 blocks (20 px) wide whenever it fits.
    for width, height in ((32, 16), (32, 32), (64, 32), (64, 64)):
        game = Tetris(width, height)
        assert game.cols == FIELD_COLS
        assert game.field_width == BLOCK_W * FIELD_COLS


def test_a_narrow_panel_keeps_four_columns_and_drops_the_hud():
    game = Tetris(16, 16)  # 8 blocks of 2x2 px is all that fits
    assert (game.cell_w, game.cell_h) == (BLOCK_W, BLOCK_H)
    assert (game.cols, game.rows) == (8, 8)
    assert game.hud_w == 0
    assert game.field_x + game.field_width == 16  # still no wasted pixel
    narrow = Tetris(20, 20)  # 10 columns fit, but nothing is left for a band
    assert (narrow.cols, narrow.hud_w) == (FIELD_COLS, 0)


def test_absurdly_small_panels_fall_back_to_one_pixel_blocks():
    game = Tetris(6, 6)
    assert (game.cell_w, game.cell_h) == (1, 1)
    assert (game.cols, game.rows) == (6, 6)
    assert game.field_x + game.field_width <= 6


def test_no_hud_leaves_the_field_centred_and_black():
    game = _empty_grid(Tetris(32, 32, hud=False))
    assert game.hud_w == 0
    assert (game.cell_w, game.cell_h) == (BLOCK_W, BLOCK_H)
    assert (game.cols, game.rows) == (FIELD_COLS, 16)
    assert game.field_x == (32 - game.field_width) // 2
    # Nothing at all is drawn: no divider, no preview, no bars.
    assert _colors(game.render()) == {(0, 0, 0)}


def test_hud_is_dropped_when_the_panel_is_too_small_for_a_band():
    game = _empty_grid(Tetris(8, 8))
    assert game.hud_w == 0
    assert game.field_x + game.field_width <= 8
    assert _colors(game.render()) == {(0, 0, 0)}


# -- pieces ---------------------------------------------------------------


def test_seven_bag_deals_every_piece_once():
    game = Tetris(32, 32, seed=1)
    played = _played_pieces(game)
    for start in (0, 7):
        assert sorted(played[start : start + 7]) == sorted(TETROMINOES)


def test_piece_sequence_is_deterministic_for_a_seed():
    same_a = _played_pieces(Tetris(32, 32, seed=42))
    same_b = _played_pieces(Tetris(32, 32, seed=42))
    other = _played_pieces(Tetris(32, 32, seed=7))
    assert same_a == same_b
    assert same_a != other


def test_spawn_is_centred_and_inside_the_field():
    game = Tetris(32, 32, seed=1)
    cells = game.piece_cells()
    assert len(cells) == 4
    assert all(0 <= x < game.cols and 0 <= y < game.rows for x, y in cells)
    assert not game._collides(cells)
    assert min(y for _, y in cells) == 0
    box_w = max(dx for dx, _ in TETROMINOES[game.piece][0]) + 1
    assert game.piece_x == (game.cols - box_w) // 2
    assert game.rotation == 0


def test_every_piece_has_four_rotations_of_four_blocks():
    for rotations in TETROMINOES.values():
        assert len(rotations) == 4
        for cells in rotations:
            assert len(cells) == 4
            assert len(set(cells)) == 4


def test_every_piece_fits_in_a_three_by_two_block_volume():
    """Each piece is 4 blocks and spans at most 3x2 blocks (``I`` is 4x1)."""
    for name, rotations in TETROMINOES.items():
        for cells in rotations:
            assert len(cells) == 4
            box_w = max(dx for dx, _ in cells) - min(dx for dx, _ in cells) + 1
            box_h = max(dy for _, dy in cells) - min(dy for _, dy in cells) + 1
            if name == "I":
                assert sorted((box_w, box_h)) == [1, 4]
            else:
                assert sorted((box_w, box_h)) in ([2, 2], [2, 3])


# -- movement -------------------------------------------------------------


def test_move_stops_at_the_walls():
    game = Tetris(32, 32, seed=1)
    while game.move(-1):
        pass
    assert min(x for x, _ in game.piece_cells()) == 0
    assert not game.move(-1)
    while game.move(1):
        pass
    assert max(x for x, _ in game.piece_cells()) == game.cols - 1
    assert not game.move(1)


def test_move_is_blocked_by_the_stack():
    game = Tetris(32, 32, seed=1)
    game.piece = "O"
    game.rotation = 0
    game.piece_x = 0
    game.piece_y = 2
    assert game.piece_cells() == [(0, 2), (1, 2), (0, 3), (1, 3)]
    game.grid[2][2] = DUMMY
    assert not game.move(1)
    assert game.piece_x == 0


def test_soft_drop_moves_down_and_scores():
    game = Tetris(32, 32, seed=1)
    y = game.piece_y
    assert game.soft_drop()
    assert game.piece_y == y + 1
    assert game.score == 1


def test_gravity_drops_one_block_per_drop_ticks():
    game = Tetris(32, 32, seed=1)
    game.start()
    y = game.piece_y
    for _ in range(game.drop_ticks - 1):
        game.step()
    assert game.piece_y == y
    game.step()
    assert game.piece_y == y + 1
    assert game.score == 0  # gravity itself is free


def test_the_game_starts_by_itself_on_the_first_tick():
    game = Tetris(32, 32, seed=1)
    assert game.state == Tetris.READY  # no key press has been seen yet
    game.step()
    assert game.state == Tetris.RUNNING


def test_the_first_piece_falls_without_any_key_press():
    game = Tetris(32, 32, seed=1)
    y = game.piece_y
    for _ in range(game.drop_ticks - 1):
        game.step()  # no game.start(), no key: the opening piece falls on its own
    assert game.piece_y == y
    game.step()
    assert game.piece_y == y + 1
    assert game.state == Tetris.RUNNING


def test_the_opening_piece_reaches_the_floor_by_gravity_alone():
    game = Tetris(32, 32, seed=1)
    piece = game.piece
    for _ in range(game.drop_ticks * (game.rows + 2)):
        game.step()
    assert game.state == Tetris.RUNNING
    assert game.piece != piece  # it locked and the next piece is falling
    assert sum(cell is not None for row in game.grid for cell in row) == 4
    assert any(cell is not None for cell in game.grid[-1])  # it landed on the floor
    _check_invariants(game)


def test_hard_drop_scores_two_per_block():
    game = Tetris(32, 32, seed=1)
    distance = game.rows - 1 - max(y for _, y in game.piece_cells())
    assert game.hard_drop() == distance
    assert game.score == 2 * distance


def test_piece_locks_when_it_cannot_fall():
    game = Tetris(32, 32, seed=1)
    game.start()
    piece = game.piece
    while game.move(0, 1):
        pass
    cells = game.piece_cells()
    for _ in range(game.drop_ticks):
        game.step()
    assert game.piece != piece
    assert all(game.grid[y][x] == PIECE_COLORS[piece] for x, y in cells)
    _check_invariants(game)


def test_rotation_returns_to_the_start_after_four_turns():
    for name in sorted(TETROMINOES):
        game = Tetris(32, 32, seed=1)
        game.piece = name
        game.rotation = 0
        game.piece_x = 3
        game.piece_y = 3
        start = sorted(game.piece_cells())
        for _ in range(4):
            assert game.rotate()
        assert game.rotation == 0
        assert (game.piece_x, game.piece_y) == (3, 3)
        assert sorted(game.piece_cells()) == start


def test_rotate_ccw_is_the_reverse_of_cw():
    game = Tetris(32, 32, seed=1)
    game.piece = "T"
    game.piece_x, game.piece_y, game.rotation = 3, 3, 0
    assert game.rotate(False)
    assert game.rotation == 3
    assert game.rotate(True)
    assert game.rotation == 0


def test_rotation_kicks_away_from_the_right_wall():
    game = Tetris(32, 32, seed=1)
    game.piece = "I"
    game.rotation = 1
    game.piece_x = game.cols - 3  # the vertical I hugs the right wall
    game.piece_y = 4
    assert max(x for x, _ in game.piece_cells()) == game.cols - 1
    assert game.rotate()
    assert game.rotation == 2
    assert game.piece_x == game.cols - 4
    assert all(0 <= x < game.cols for x, _ in game.piece_cells())


def test_rotation_works_at_the_top_of_the_field():
    game = Tetris(32, 32, seed=1)
    game.piece = "I"
    game.rotation = 0
    game.piece_x = 3
    game.piece_y = -1  # where the horizontal I spawns
    assert game.piece_cells() == [(3, 0), (4, 0), (5, 0), (6, 0)]
    assert game.rotate()
    assert game.rotation == 1
    assert game.piece_y == 0  # the downward kick kept every cell on the field
    assert all(y >= 0 for _, y in game.piece_cells())


# -- locking, clearing and scoring ----------------------------------------


def test_completed_row_is_cleared_and_scores():
    game = Tetris(32, 32, seed=1)
    row = game.rows - 1
    for x in range(game.cols):
        game.grid[row][x] = DUMMY
    game.grid[row][game.cols - 2] = None
    game.grid[row][game.cols - 1] = None
    marker = PIECE_COLORS["T"]
    game.grid[row - 1][5] = marker
    game.piece = "O"
    game.rotation = 0
    game.piece_x = game.cols - 2
    game.piece_y = row - 1
    assert not game._collides(game.piece_cells())
    game.hard_drop()
    assert game.lines == 1
    assert game.score == 100  # one line at level 1
    assert game.grid[row][5] == marker  # everything above fell one row
    assert game.grid[row - 1][5] is None
    assert game.grid[row][game.cols - 1] == PIECE_COLORS["O"]
    _check_invariants(game)


def test_four_rows_at_once_score_800():
    game = Tetris(32, 32, seed=1)
    for row in range(game.rows - 4, game.rows):
        for x in range(1, game.cols):
            game.grid[row][x] = DUMMY
    game.piece = "I"
    game.rotation = 1  # vertical I, two columns left of the box origin
    game.piece_x = -2
    game.piece_y = game.rows - 4
    assert not game._collides(game.piece_cells())
    game.hard_drop()
    assert game.lines == 4
    assert game.score == 800
    assert all(cell is None for row in game.grid for cell in row)
    _check_invariants(game)


def test_level_rises_every_ten_lines_and_scores_at_the_current_level():
    game = Tetris(32, 32, level=3)
    assert game.level == 3
    assert game.drop_ticks < Tetris(32, 32, level=1).drop_ticks
    assert game.drop_ticks == Tetris(32, 32, level=3).drop_ticks
    game.lines = 9
    game.grid[game.rows - 1] = [DUMMY] * game.cols
    assert game._clear_lines() == 1
    assert game.lines == 10
    assert game.level == 4
    assert game.score == 100 * 3  # scored with the level in force at the time
    assert game.drop_ticks == Tetris(32, 32, level=4).drop_ticks


def test_no_row_is_ever_left_full():
    game = Tetris(32, 32, seed=1)
    for x in range(game.cols):
        game.grid[game.rows - 1][x] = DUMMY
    game.grid[game.rows - 1][0] = None
    game.piece = "I"
    game.rotation = 1
    game.piece_x = -2
    game.piece_y = 0
    game.hard_drop()
    assert game.lines == 1
    _check_invariants(game)


# -- states ---------------------------------------------------------------


def test_game_over_when_the_piece_cannot_spawn():
    game = Tetris(32, 32, seed=1)
    for row in range(4):
        for x in range(game.cols):
            game.grid[row][x] = DUMMY
    assert not game.spawn()
    assert game.state == Tetris.GAME_OVER
    before = [row[:] for row in game.grid]
    game.step()
    assert game.hard_drop() == 0
    assert not game.move(1)
    assert not game.rotate()
    assert game.grid == before


def test_pause_freezes_the_simulation():
    game = Tetris(32, 32, seed=1)
    game.start()
    assert game.state == Tetris.RUNNING
    game.toggle_pause()
    assert game.state == Tetris.PAUSED
    y = game.piece_y
    for _ in range(50):
        game.step()
    assert game.piece_y == y
    assert not game.move(1)
    assert not game.rotate()
    assert not game.soft_drop()
    assert game.score == 0
    game.toggle_pause()
    assert game.state == Tetris.RUNNING
    for _ in range(game.drop_ticks):
        game.step()
    assert game.piece_y == y + 1


def test_pause_is_a_no_op_outside_a_running_game():
    game = Tetris(32, 32, seed=1)
    game.toggle_pause()
    assert game.state == Tetris.READY
    game.state = Tetris.GAME_OVER
    game.toggle_pause()
    assert game.state == Tetris.GAME_OVER


def test_reset_starts_a_fresh_game():
    game = Tetris(32, 32, seed=1)
    game.start()
    game.hard_drop()
    game.score = 123
    game.lines = 5
    game.level = 4
    game.state = Tetris.GAME_OVER
    game.reset(seed=1)
    assert game.score == 0
    assert game.lines == 0
    assert game.level == 1
    assert game.state == Tetris.READY
    assert all(cell is None for row in game.grid for cell in row)
    assert game.piece in PIECE_COLORS
    assert game.piece == Tetris(32, 32, seed=1).piece  # same seed, same opening


# -- rendering ------------------------------------------------------------


def test_render_returns_rgb_at_panel_size():
    for width, height in ((32, 32), (16, 16), (64, 64)):
        img = Tetris(width, height, seed=1).render()
        assert img.size == (width, height)
        assert img.mode == "RGB"


def test_render_draws_a_locked_block_at_the_right_place():
    game = _empty_grid(Tetris(32, 32, seed=1))
    game.grid[4][7] = PIECE_COLORS["T"]
    img = game.render()
    left = game.field_x + 7 * game.cell_w
    top = game.field_y + 4 * game.cell_h
    for y in range(top, top + game.cell_h):
        for x in range(left, left + game.cell_w):
            assert img.getpixel((x, y)) == PIECE_COLORS["T"]
    assert img.getpixel((left - 1, top)) != PIECE_COLORS["T"]
    assert img.getpixel((left, top - 1)) != PIECE_COLORS["T"]


def test_a_rendered_block_is_a_two_by_two_pixel_square():
    game = _empty_grid(Tetris(32, 32, seed=1))
    row = game.rows - 2  # keep one row of margin below the block
    game.grid[row][0] = PIECE_COLORS["T"]
    img = game.render()
    left = game.field_x
    top = game.field_y + row * game.cell_h
    block = [
        img.getpixel((left + dx, top + dy))
        for dy in range(BLOCK_H)
        for dx in range(BLOCK_W)
    ]
    assert block == [PIECE_COLORS["T"]] * 4  # exactly 2x2 px of colour
    assert img.getpixel((left + BLOCK_W, top)) == (0, 0, 0)
    assert img.getpixel((left, top + BLOCK_H)) == (0, 0, 0)


def test_a_rendered_piece_is_four_blocks_of_two_by_two_pixels():
    game = _empty_grid(Tetris(32, 32, seed=1))
    game.piece = "T"
    game.rotation = 0
    game.piece_x, game.piece_y = 2, 5
    img = game.render()
    for x, y in game.piece_cells():
        left = game.field_x + x * BLOCK_W
        top = game.field_y + y * BLOCK_H
        for dy in range(BLOCK_H):
            for dx in range(BLOCK_W):
                assert img.getpixel((left + dx, top + dy)) == PIECE_COLORS["T"]
    painted = sum(
        1
        for py in range(game.height)
        for px in range(game.width)
        if img.getpixel((px, py)) == PIECE_COLORS["T"]
    )
    assert painted == 4 * BLOCK_W * BLOCK_H  # 4 blocks, 4 px each


def test_render_draws_the_falling_piece():
    game = _empty_grid(Tetris(32, 32, seed=1))
    game.piece = "L"
    game.rotation = 0
    game.piece_x = 2
    game.piece_y = 5
    img = game.render()
    for x, y in game.piece_cells():
        left = game.field_x + x * game.cell_w
        top = game.field_y + y * game.cell_h
        assert img.getpixel((left, top)) == PIECE_COLORS["L"]


def test_render_keeps_everything_inside_the_field_and_the_hud():
    for width, height in ((32, 32), (16, 16), (64, 64), (16, 32), (64, 32)):
        game = _empty_grid(Tetris(width, height, seed=3))
        game.grid = [[PIECE_COLORS["O"]] * game.cols for _ in range(game.rows)]
        img = game.render()
        for y in range(game.height):
            for x in range(game.width):
                if img.getpixel((x, y)) == (0, 0, 0):
                    continue
                in_field = (
                    game.field_x <= x < game.field_x + game.field_width
                    and game.field_y <= y < game.field_y + game.field_height
                )
                in_hud = (
                    game.hud_w > 0
                    and game.hud_x <= x < game.hud_x + game.hud_w
                    and game.field_y <= y < game.field_y + game.field_height
                )
                assert in_field or in_hud


def test_render_hud_shows_the_next_piece_preview():
    game = _empty_grid(Tetris(32, 32, seed=5))
    _, cell_h = game._preview_cell()
    expected = PIECE_COLORS[game.next_piece]
    img = game.render()
    band = [
        img.getpixel((x, y))
        for y in range(game.field_y, game.field_y + PREVIEW_CELLS * cell_h)
        for x in range(game.hud_content_x, game.width)
    ]
    assert band.count(expected) > 0
    for pixel in band:  # only the queued piece is drawn up there
        assert pixel in ((0, 0, 0), expected)


def test_render_hud_bars_grow_with_score_and_level():
    game = _empty_grid(Tetris(32, 32, seed=1, level=4))
    game.score = 350
    img = game.render()
    bottom = game.field_y + game.field_height - 1
    x = game.hud_content_x
    green = sum(
        1 for y in range(game.height) if img.getpixel((x, y)) == HUD_SCORE_COLOR
    )
    assert green == 3  # one pixel per 100 points
    assert img.getpixel((x, bottom)) == HUD_SCORE_COLOR
    bar_w = max(1, min(game.cell_w, game.hud_content_w // 4))
    yellow = sum(
        1
        for y in range(game.height)
        if img.getpixel((x + bar_w + 1, y)) == HUD_LEVEL_COLOR
    )
    assert yellow == 4  # one pixel per level


def test_render_hud_divider_separates_field_and_band():
    game = _empty_grid(Tetris(32, 32, seed=1))
    img = game.render()
    for y in range(game.field_y, game.field_y + game.field_height):
        assert img.getpixel((game.hud_x, y)) == HUD_DIVIDER_COLOR


def test_render_hud_is_red_on_game_over():
    game = _empty_grid(Tetris(32, 32, seed=1))
    game.state = Tetris.GAME_OVER
    img = game.render()
    for y in range(game.field_y, game.field_y + game.field_height):
        for x in range(game.hud_x + 1, game.hud_x + game.hud_w):
            assert img.getpixel((x, y)) == HUD_OVER_COLOR


def test_render_hud_divider_is_white_when_paused():
    game = _empty_grid(Tetris(32, 32, seed=1))
    game.state = Tetris.PAUSED
    img = game.render()
    for y in range(game.field_y, game.field_y + game.field_height):
        assert img.getpixel((game.hud_x, y)) == HUD_PAUSE_COLOR


def test_no_hud_draws_no_hud_colors():
    game = _empty_grid(Tetris(32, 32, seed=1, hud=False))
    game.score = 500
    img = game.render()
    for color in (HUD_DIVIDER_COLOR, HUD_LEVEL_COLOR, HUD_OVER_COLOR, HUD_PAUSE_COLOR):
        assert color not in _colors(img)


# -- auto-play ------------------------------------------------------------


def test_auto_play_keeps_the_invariants_until_game_over():
    for seed in (1, 7, 42):
        game = Tetris(32, 32, seed=seed)
        game.start()
        pieces = 0
        while game.state != Tetris.GAME_OVER and pieces < 400:
            while game.move(-1):  # stack everything against the left wall
                pass
            game.hard_drop()
            pieces += 1
            _check_invariants(game)
        assert game.state == Tetris.GAME_OVER
        assert pieces < 400
        assert game.score > 0
        assert all(len(row) == game.cols for row in game.grid)


def test_auto_play_is_reproducible_with_a_seed():
    def play(seed):
        game = Tetris(32, 32, seed=seed)
        game.start()
        while game.state != Tetris.GAME_OVER:
            while game.move(-1):
                pass
            game.hard_drop()
        return game.score, game.lines, [row[:] for row in game.grid]

    assert play(3) == play(3)


# -- keyboard repeat ------------------------------------------------------


def test_one_key_press_moves_the_piece_by_exactly_one_block():
    game = Tetris(32, 32, seed=1)
    repeater = KeyRepeater()
    x = game.piece_x
    if repeater.press("right"):  # <KeyPress>, as run_tetris does
        game.move(1)
    repeater.release("right")  # a human tap, released before the next frame
    for _ in range(30):  # three seconds of frames
        assert repeater.due() == []
    assert game.piece_x == x + 1  # one press, one block: never two


def test_a_tap_released_after_the_next_frame_moves_exactly_one_block():
    game = Tetris(32, 32, seed=1)
    repeater = KeyRepeater()
    x = game.piece_x
    if repeater.press("right"):  # <KeyPress>
        game.move(1)
    for _key in repeater.due():  # first frame: the key is still down
        game.move(1)
    repeater.release("right")  # a human tap ends right after that frame
    for _ in range(40):  # four more seconds
        for _key in repeater.due():
            game.move(1)
    assert game.piece_x == x + 1  # it must not move twice


def test_holding_a_key_walks_the_piece_to_the_wall():
    game = Tetris(32, 32, seed=1)
    repeater = KeyRepeater()
    if repeater.press("left"):
        game.move(-1)
    for _ in range(200):  # hold long enough to cross the whole field
        for key in repeater.due():
            if key == "left":
                game.move(-1)
    assert game.piece_x == 0
    assert game.state == Tetris.READY  # no tick was played in between


# -- panel integration ----------------------------------------------------


def test_tetris_frame_is_pushed_to_the_panel():
    panel = _make_panel(32, 32)
    game = _empty_grid(Tetris(panel.width, panel.height, seed=1))
    game.grid[game.rows - 1][0] = PIECE_COLORS["Z"]
    panel.send_frame(game.render())

    assert len(panel._client.calls) == 1
    hex_string, extension = panel._client.calls[0]
    assert extension == ".png"
    decoded = _decode(hex_string)
    assert decoded.size == (32, 32)
    bottom = game.field_y + game.field_height - 1
    assert decoded.getpixel((game.field_x, bottom)) == PIECE_COLORS["Z"]
