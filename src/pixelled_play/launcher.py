"""The on-panel launcher for the games.

The launcher engine lives in :mod:`pixelled_lib.library.menu`; this module only
describes *which* games this project ships (breakout, tetris) and wires them to
the engine, exactly like ``pixelled_lib`` does for its own debug tools.
"""

from __future__ import annotations

from typing import Optional

from pixelled_lib.library.menu import AUTO_DELAY_S, MenuGroup, MenuItem, run_menu
from pixelled_lib.library.panel import Panel

from .display import blank_panel
from .games.breakout import run_breakout
from .games.tetris import run_tetris

# Per-game settings used when the launcher starts a game.
_BREAKOUT_FPS = 15.0
_TETRIS_FPS = 10.0


def build_games_groups(
    address: str,
    seed: Optional[int] = None,
) -> list[MenuGroup]:
    """Build the games launcher groups; each action borrows the panel it is given."""
    games = [
        MenuItem(
            "BRKOUT",
            "play the brick-breaker demo",
            lambda panel: run_breakout(
                address, fps=_BREAKOUT_FPS, seed=seed, panel=panel
            ),
        ),
        MenuItem(
            "TETRIS",
            "play the Tetris demo",
            lambda panel: run_tetris(address, fps=_TETRIS_FPS, seed=seed, panel=panel),
        ),
    ]
    return [MenuGroup("GAMES", "games", games)]


def run_launcher(
    address: str,
    brightness: Optional[int] = None,
    fps: float = 10.0,
    seed: Optional[int] = None,
    auto: float = AUTO_DELAY_S,
) -> None:
    """Show the games launcher on the panel and run the selected game.

    Args:
        address: Bluetooth MAC address of the panel.
        brightness: Optional brightness (0-100) applied on connect.
        fps: Menu refresh rate (frames per second).
        seed: Optional RNG seed passed on to the games.
        auto: Seconds between two automatic moves (``0`` starts in keyboard
            mode; ``a`` still toggles auto-scrolling on).
    """
    with Panel(address, brightness=brightness) as panel:
        try:
            run_menu(
                panel,
                build_games_groups(address, seed=seed),
                fps=fps,
                auto=auto,
                title="pixelled play launcher",
            )
        finally:
            # The panel keeps the last frame it got, so clear it before the
            # connection is closed - otherwise it stays frozen on the last
            # menu frame (a clean disconnect alone does not blank it).
            blank_panel(panel)
