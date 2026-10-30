"""`mafia` command line."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from mafia import paths


def _setup(args: argparse.Namespace) -> int:
    from mafia.hosts.claude_code.workspace import write_workspace

    written = write_workspace(paths.play_dir(), spike=args.spike)
    mode_file = paths.runs_dir() / "harness_mode"
    mode_file.parent.mkdir(parents=True, exist_ok=True)
    mode_file.write_text(args.mode + "\n", encoding="utf-8")
    backend = "spike" if args.spike else "game"
    (paths.runs_dir() / "harness_backend").write_text(backend + "\n", encoding="utf-8")
    for path in written:
        print(f"wrote {path}")
    print(f"harness mode: {args.mode} ({mode_file})")
    return 0


def _spike_report(args: argparse.Namespace) -> int:
    from mafia.hosts.claude_code.spike.report import main as report_main

    return report_main(args.main_output)


def _spike_reset(_: argparse.Namespace) -> int:
    from mafia.hosts.claude_code.spike.state import SpikeState

    SpikeState.open_default().reset()
    log = paths.logs_dir() / "hooks.jsonl"
    log.unlink(missing_ok=True)
    print("spike state and hook log cleared")
    return 0


def _tail(args: argparse.Namespace) -> int:
    from mafia.tail import View, run

    view = View(god=args.god, seat=args.seat)
    return run(view, game_id=args.game, follow=not args.once)


def _replay(args: argparse.Namespace) -> int:
    from mafia.tail import View, replay

    return replay(View(god=args.god, seat=args.seat), game_id=args.game, speed=args.speed)


def _scenarios(args: argparse.Namespace) -> int:
    from mafia.tail import list_scenarios

    return list_scenarios(spoil=args.spoil)


def _games(_: argparse.Namespace) -> int:
    from mafia.tail import list_games

    return list_games()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mafia")
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="generate the play workspace")
    setup.add_argument("--spike", action="store_true", help="M0 harness spike workspace")
    setup.add_argument("--mode", choices=("enforce", "observe"), default="enforce")
    setup.set_defaults(func=_setup)

    tail = sub.add_parser("tail", help="follow the game log in this terminal")
    who = tail.add_mutually_exclusive_group()
    who.add_argument("--god", action="store_true", help="everything, including secrets (spoilers!)")
    who.add_argument("--seat", type=int, help="what this seat can see")
    tail.add_argument("--game", default=None, help="game id (default: the active game)")
    tail.add_argument("--once", action="store_true", help="print what exists and exit")
    tail.set_defaults(func=_tail)

    rep = sub.add_parser("replay", help="replay a stored game, paced for reading")
    rep_who = rep.add_mutually_exclusive_group()
    rep_who.add_argument("--god", action="store_true", help="everything, including secrets")
    rep_who.add_argument("--seat", type=int, help="what this seat could see")
    rep.add_argument("--game", default=None, help="game id (default: the active game)")
    rep.add_argument("--speed", type=float, default=1.0, help="2 = twice as fast, 0 = instant")
    rep.set_defaults(func=_replay)

    scen = sub.add_parser("scenarios", help="list murder-mystery scenarios in the library")
    scen.add_argument("--spoil", action="store_true", help="also print each scenario's solution")
    scen.set_defaults(func=_scenarios)

    games = sub.add_parser("games", help="list stored games")
    games.set_defaults(func=_games)

    report = sub.add_parser("spike-report", help="evaluate M0 harness contracts")
    report.add_argument(
        "--main-output", default=None, help="file with the main session's final output"
    )
    report.set_defaults(func=_spike_report)

    reset = sub.add_parser("spike-reset", help="clear M0 spike state and hook log")
    reset.set_defaults(func=_spike_reset)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
