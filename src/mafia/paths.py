"""Filesystem locations shared by the engine, MCP server, hooks and CLI.

Every process (Claude Code spawns the MCP server and each hook separately) must agree on
these paths without sharing memory, so they are derived from the installed package location
and can be overridden through environment variables.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def runs_dir() -> Path:
    """Directory holding game databases and logs. Never inside the play workspace."""
    return Path(os.environ.get("MAFIA_RUNS_DIR", REPO_ROOT / "runs"))


def play_dir() -> Path:
    """The generated workspace the player opens in the IDE."""
    return Path(os.environ.get("MAFIA_PLAY_DIR", REPO_ROOT / "play"))


def logs_dir() -> Path:
    return runs_dir() / "logs"


def prompts_dir() -> Path:
    return Path(__file__).resolve().parent / "prompts"
