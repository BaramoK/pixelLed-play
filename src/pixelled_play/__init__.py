"""pixelled_play - LED matrix games running on iPixel Color panels.

The games live in :mod:`pixelled_play.games` and reuse the display layer of
:mod:`pixelled_lib.library` (renderers, bitmap font, sprite and the launcher
engine). :mod:`pixelled_play.launcher` is the on-panel menu that starts them and
:mod:`pixelled_play.cli` exposes everything on the command line.
"""

from .games.breakout import Breakout, run_breakout
from .games.tetris import Tetris, run_tetris
from .launcher import build_games_groups, run_launcher

__version__ = "0.1.0"

__all__ = [
    "Breakout",
    "Tetris",
    "build_games_groups",
    "run_breakout",
    "run_launcher",
    "run_tetris",
    "__version__",
]
