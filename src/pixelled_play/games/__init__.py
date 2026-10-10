"""Games rendered entirely on the LED panel.

Each game keeps its whole state in a pure object (:class:`Breakout`,
:class:`Tetris`) and a ``run_*`` function wires it to a real panel with a
minimal keyboard-capture window.
"""

from .breakout import Breakout, run_breakout
from .tetris import Tetris, run_tetris

__all__ = ["Breakout", "Tetris", "run_breakout", "run_tetris"]
