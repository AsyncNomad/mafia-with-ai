from __future__ import annotations

import sys

from mafia import paths
from mafia.harness.ports import HarnessState
from mafia.hosts.claude_code.hooks.dispatch import HookContext, read_mode, run


def _state() -> HarnessState:
    """The game behind the hooks: the real table, or the M0 spike stand-in."""
    try:
        backend = (paths.runs_dir() / "harness_backend").read_text(encoding="utf-8").strip()
    except OSError:
        backend = "game"
    if backend == "spike":
        from mafia.hosts.claude_code.spike.state import SpikeState

        return SpikeState.open_default()
    from mafia.app.table import TableApp

    return TableApp.open_default()


def main() -> int:
    ctx = HookContext(
        mode=read_mode(paths.runs_dir() / "harness_mode"),
        state_factory=_state,
        log_path=paths.logs_dir() / "hooks.jsonl",
    )
    sys.stdout.write(run(sys.stdin.read(), ctx))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
