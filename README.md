# pixelled-play

Games rendered entirely on **iPixel Color** LED matrix panels, built on
[`pixelled-lib`](https://github.com/BaramoK/pixelLed-lib) and
[`pypixelcolor`](https://pypi.org/project/pypixelcolor).

- **Breakout** — a small brick-breaker demo: the whole game (field + HUD) is
  drawn on the panel, while a minimal window only captures the keyboard. The
  wall is built from 2x2 px bricks packed edge to edge, the paddle is a 2 px bar
  with red-orange tips and the ball is a single pixel. Levels are endless:
  clearing a wall builds a taller, denser, slightly faster one and awards an
  extra life every five levels.
- **Tetris** — a small Tetris demo, also played entirely on the panel: a 10
  block wide field of fixed 2x2 px blocks with a 12 px HUD band on its right
  (next piece preview, score bar, level bar). 7-bag randomizer, wall kicks,
  soft/hard drop, pause and restart, all from the keyboard.
- **Launcher** — a menu drawn on the panel that starts either game and shares a
  single BLE connection with it. It scrolls by itself, so the panel is useful
  without a keyboard.

The display layer (panel, renderers, bitmap font, sprite, keyboard helper and
the launcher engine) comes from
[`pixelled-lib`](https://github.com/BaramoK/pixelLed-lib); this project only adds
the games.

## Installation

`pixelled-play` depends on `pixelled-lib` and is not published on PyPI yet:

```bash
git clone https://github.com/BaramoK/pixelLed-play.git
cd pixelLed-play
python -m venv .venv
.venv/Scripts/python -m pip install -e .    # Windows
# .venv/bin/python -m pip install -e .      # Linux / macOS
```

`pixelled-play` is a console script, created **inside** that environment: the
shell only finds it once the environment is active (or once its `Scripts`
directory is on `PATH`).

```bash
.venv/Scripts/Activate.ps1                  # PowerShell (Windows)
.venv/Scripts/activate                      # cmd (Windows)
source .venv/bin/activate                   # Linux / macOS

pixelled-play menu -a 30:E1:AF:BD:5F:D0
```

Without activating it, call the script (or the module) by path instead:

```bash
.venv/Scripts/pixelled-play.exe menu -a 30:E1:AF:BD:5F:D0
.venv/Scripts/python -m pixelled_play menu -a 30:E1:AF:BD:5F:D0
```

For local development against a checkout of the library, install that one first:

```bash
pip install -e ../pixelLed-lib
pip install -e . --no-deps
```

## Usage

```bash
# Play a brick-breaker demo entirely on the panel (q to quit)
pixelled-play breakout -a 30:E1:AF:BD:5F:D0 --fps 15 --level 1 --lives 3 --seed 42

# Play a Tetris demo entirely on the panel (q to quit)
pixelled-play tetris -a 30:E1:AF:BD:5F:D0 --fps 10 --level 1 --seed 42

# Show a launcher on the panel and run the selected game
pixelled-play menu -a 30:E1:AF:BD:5F:D0
```

Or as a library:

```python
from pixelled_lib import Panel

from pixelled_play.games.tetris import Tetris

with Panel("30:E1:AF:BD:5F:D0", brightness=60) as panel:
    game = Tetris(panel.width, panel.height, seed=42)
    game.start()
    game.step()
    panel.send_frame(game.render())
```

## Notes and limitations

- `pixelled-play breakout` draws everything on the panel; its (minimal) window
  only captures the keyboard: arrows/`A`-`D` move the paddle, space launches,
  `r` replays a new random wall and `q`/Esc quits. The HUD lives on the panel's
  top row (green score bar for the current level on the left, then a yellow
  level bar, then the remaining lives on the right); disable it with `--no-hud`.
  Bricks are 2x2 px blocks packed edge to edge (no gap), so the wall looks like
  the classic solid mass: `--density` is the probability for a 2x2 cell to be
  filled **at level 1** (default 0.5); raise it (e.g. `--density 0.8`) for a
  denser wall. The paddle is a white 2 px bar whose two end columns are
  red-orange, so it reads as a paddle and not a plain white block; the ball is a
  single white pixel. Levels are endless: clearing every brick builds a taller wall
  (3 rows at level 1, +1 row every five levels, up to what the panel holds) that
  is also denser (density +0.1 every five levels, up to a full wall) and speeds
  the ball up (x1.1, i.e. +10 %, every five levels, capped at 3 px/tick), and
  rewards the player with **one extra life every five levels**; the score keeps
  accumulating, so only running out of lives ends the game. `--level` starts at
  a higher level and grants the extra rows and lives it would have earned on the
  way (`--lives` is the count at level 1).
- `pixelled-play tetris` also draws everything on the panel; its (minimal)
  window only captures the keyboard: arrows/`A`-`D` move the piece, up/`W`/`X`
  rotate clockwise, `Z` rotates counter-clockwise, down/`S` soft drops, space
  hard drops, `p` pauses, `r` replays a new game and `q`/Esc quits. A tap moves
  or drops the piece by exactly one block; hold a key to keep repeating it. The
  first piece starts falling on its own (1 row per second at the default 10 fps),
  no key press is needed to begin. The field is 10 blocks wide, every block is
  exactly 2x2 px and a piece is made of 4 such blocks (so it spans 3x2 blocks at
  most), so the same game looks the same on any panel: the block size is never
  scaled (a panel narrower than 20 px simply gets fewer columns). The 12 px band
  on the right holds the next piece preview, a green score bar (1 px per 100
  points) and a yellow level bar (1 px per level); on a 32x32 panel field + band
  fill the matrix exactly (a 20x32 px field plus the band). Disable it with
  `--no-hud`. `--level` starts at a higher speed, `--seed` makes the piece
  sequence reproducible.
- `pixelled-play menu` connects **once** and then lends that connection to the
  game it starts, so replaying costs no reconnection. It lists the games
  (`GAMES`: Breakout, Tetris) and scrolls by itself (`--auto 2.0` = seconds per
  item, `0` starts in keyboard mode); the first arrow key takes over and
  auto-scrolling resumes after a few idle seconds. Keys: up/down or `W`/`S`
  move, `1`-`9` jump straight to an item, Enter/Space launches, `a` toggles
  auto-scrolling, `Esc`/Left/`b` returns from the group to the root and `q`
  quits (a launched game goes back to its group when it exits).
- Both games open a `tkinter` window to capture the keyboard, so they need a
  graphical display on the workstation running them. That window comes from
  `pixelled_lib.library.window` and is shared with the launcher: a whole session
  runs on a single Tk root, which is what keeps going from the menu to a game
  (and back) from breaking the Tk interpreter.
- Frame rate is limited by BLE throughput and the per-frame ACK; expect a few
  frames per second up to roughly 10-15 fps depending on panel size and content.

## Tests

```bash
pytest
```

The breakout and tetris tests run without any hardware.

## License

MIT. This is a third-party project, not affiliated with iPixel Color.
