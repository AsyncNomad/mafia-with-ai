from __future__ import annotations

import sys

from mafia import paths
from mafia.hosts.claude_code.hooks.dispatch import HookContext, read_mode, run
from mafia.hosts.claude_code.spike.state import SpikeState


def main() -> int:
    ctx = HookContext(
        mode=read_mode(paths.runs_dir() / "harness_mode"),
        # M0: the spike state stands in for the engine. Replaced by the game store in M1.
        state_factory=SpikeState.open_default,
        log_path=paths.logs_dir() / "hooks.jsonl",
    )
    sys.stdout.write(run(sys.stdin.read(), ctx))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
