"""Command line interface for pixelled_play."""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from . import __version__
from .games.breakout import run_breakout
from .games.tetris import run_tetris
from .launcher import run_launcher


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-a",
        "--address",
        required=True,
        help="Bluetooth MAC address of the panel (e.g. 30:E1:AF:BD:5F:D0).",
    )
    parser.add_argument(
        "-b",
        "--brightness",
        type=int,
        default=None,
        help="Brightness 0-100 applied on connect.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pixelled-play",
        description=(
            "Games rendered entirely on an iPixel Color LED matrix panel "
            "(built on pypixelcolor, display layer from pixelled-lib)."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"pixelled-play {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_breakout = sub.add_parser(
        "breakout", help="Play a brick-breaker demo entirely on the panel."
    )
    _add_common(p_breakout)
    p_breakout.add_argument(
        "--fps", type=float, default=15.0, help="Game/send rate in fps (default 15)."
    )
    p_breakout.add_argument(
        "--level",
        type=int,
        default=1,
        help="Starting level (default 1); also grants the lives earned so far.",
    )
    p_breakout.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Ball speed in pixels per tick at level 1 (default 1); x1.1 every "
        "five levels.",
    )
    p_breakout.add_argument(
        "--lives",
        type=int,
        default=3,
        help="Number of balls at level 1 (default 3).",
    )
    p_breakout.add_argument(
        "--density",
        type=float,
        default=0.5,
        help="Probability for a wall cell to hold a brick at level 1 (default "
        "0.5); +0.1 every five levels.",
    )
    p_breakout.add_argument(
        "--seed", type=int, default=None, help="RNG seed for a reproducible wall."
    )
    p_breakout.add_argument(
        "--no-hud",
        dest="hud",
        action="store_false",
        help="Do not reserve the top row for the on-panel HUD.",
    )

    p_tetris = sub.add_parser(
        "tetris", help="Play a Tetris demo entirely on the panel."
    )
    _add_common(p_tetris)
    p_tetris.add_argument(
        "--fps", type=float, default=10.0, help="Game/send rate in fps (default 10)."
    )
    p_tetris.add_argument(
        "--level", type=int, default=1, help="Starting level (default 1)."
    )
    p_tetris.add_argument(
        "--seed", type=int, default=None, help="RNG seed for a reproducible run."
    )
    p_tetris.add_argument(
        "--no-hud",
        dest="hud",
        action="store_false",
        help="Do not reserve the right band for the on-panel HUD.",
    )

    p_menu = sub.add_parser(
        "menu",
        help="Show a launcher on the panel and run the selected game.",
    )
    _add_common(p_menu)
    p_menu.add_argument(
        "--fps", type=float, default=10.0, help="Menu refresh rate (default 10)."
    )
    p_menu.add_argument(
        "--seed", type=int, default=None, help="RNG seed for the games."
    )
    p_menu.add_argument(
        "--auto",
        type=float,
        default=2.0,
        help="Seconds per item while auto-scrolling (0 = off, default 2).",
    )

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "breakout":
        run_breakout(
            args.address,
            brightness=args.brightness,
            fps=args.fps,
            seed=args.seed,
            density=args.density,
            lives=args.lives,
            speed=args.speed,
            level=args.level,
            hud=args.hud,
        )
        return 0

    if args.command == "tetris":
        run_tetris(
            args.address,
            brightness=args.brightness,
            fps=args.fps,
            level=args.level,
            seed=args.seed,
            hud=args.hud,
        )
        return 0

    if args.command == "menu":
        run_launcher(
            args.address,
            brightness=args.brightness,
            fps=args.fps,
            seed=args.seed,
            auto=args.auto,
        )
        return 0

    parser.error("Unknown command")
    return 2


if __name__ == "__main__":
    sys.exit(main())
