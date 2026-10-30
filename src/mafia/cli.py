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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mafia")
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="generate the play workspace")
    setup.add_argument("--spike", action="store_true", help="M0 harness spike workspace")
    setup.add_argument("--mode", choices=("enforce", "observe"), default="enforce")
    setup.set_defaults(func=_setup)

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
