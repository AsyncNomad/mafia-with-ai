from __future__ import annotations

import json
from pathlib import Path

from mafia.agents import MYSTERY_AGENTS
from mafia.harness.tools import AGENT_TOOLS, SEAT_TOOLS
from mafia.hosts.claude_code.workspace import GENERATED_MARKER, write_workspace


def _frontmatter(text: str) -> dict[str, str]:
    lines = text.split("---", 2)[1].strip().splitlines()
    pairs = (line.split(":", 1) for line in lines if ":" in line and not line.startswith("#"))
    return {k.strip(): v.strip() for k, v in pairs}


def test_generates_all_agents_with_scoped_tools(tmp_path: Path) -> None:
    write_workspace(tmp_path, spike=True, python=Path("/py/bin/python"))
    agents = tmp_path / ".claude" / "agents"
    for spec in MYSTERY_AGENTS:
        fm = _frontmatter((agents / f"{spec.name}.md").read_text(encoding="utf-8"))
        tools = {t.strip() for t in fm["tools"].split(",")}
        assert tools == {f"mcp__mafia__{t}" for t in AGENT_TOOLS[spec.name]}
        assert fm["omitClaudeMd"] == "true"
    for seat in (1, 2):
        fm = _frontmatter((agents / f"seat-{seat}.md").read_text(encoding="utf-8"))
        assert {t.strip() for t in fm["tools"].split(",")} == {
            f"mcp__mafia__{t}" for t in SEAT_TOOLS
        }


def test_agent_bodies_come_from_neutral_prompts(tmp_path: Path) -> None:
    write_workspace(tmp_path, spike=True, python=Path("/py/bin/python"))
    for spec in MYSTERY_AGENTS:
        text = (tmp_path / ".claude" / "agents" / f"{spec.name}.md").read_text(encoding="utf-8")
        assert spec.prompt().strip() in text


def test_settings_lock_main_and_register_hooks(tmp_path: Path) -> None:
    write_workspace(tmp_path, spike=True, python=Path("/py/bin/python"))
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert {"Bash", "Read", "Write", "Edit"} <= set(settings["permissions"]["deny"])
    for event in ("PreToolUse", "PostToolUse", "SubagentStop", "UserPromptSubmit"):
        command = settings["hooks"][event][0]["hooks"][0]["command"]
        assert command == '"/py/bin/python" -m mafia.hosts.claude_code.hooks'


def test_stale_generated_agents_removed_but_user_files_kept(tmp_path: Path) -> None:
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "seat-7.md").write_text(f"---\n# {GENERATED_MARKER}\n---\nold", encoding="utf-8")
    (agents / "mine.md").write_text("---\nname: mine\n---\nhand written", encoding="utf-8")
    write_workspace(tmp_path, spike=True, python=Path("/py/bin/python"))
    assert not (agents / "seat-7.md").exists()
    assert (agents / "mine.md").exists()


def test_game_workspace_has_eight_seats_skill_and_driver(tmp_path: Path) -> None:
    write_workspace(tmp_path, spike=False, python=Path("/py/bin/python"))
    agents = tmp_path / ".claude" / "agents"
    seats = sorted(p.stem for p in agents.glob("seat-*.md"))
    assert seats == [f"seat-{i}" for i in range(1, 9)]
    seat_text = (agents / "seat-5.md").read_text(encoding="utf-8")
    assert "my_turn" in seat_text and "제출 완료" in seat_text
    assert _frontmatter(seat_text)["omitClaudeMd"] == "true"
    skill = (tmp_path / ".claude" / "skills" / "mafia" / "SKILL.md").read_text(encoding="utf-8")
    assert _frontmatter(skill)["name"] == "mafia"
    assert "진행 루프" in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    mcp = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["mafia"]["args"] == ["-m", "mafia.mcp_server.server"]


def test_switching_back_to_spike_removes_game_only_files(tmp_path: Path) -> None:
    write_workspace(tmp_path, spike=False, python=Path("/py/bin/python"))
    write_workspace(tmp_path, spike=True, python=Path("/py/bin/python"))
    agents = tmp_path / ".claude" / "agents"
    assert sorted(p.stem for p in agents.glob("seat-*.md")) == ["seat-1", "seat-2"]
    assert not (tmp_path / ".claude" / "skills" / "mafia" / "SKILL.md").exists()


def test_aliases_are_seats_without_a_number(tmp_path: Path) -> None:
    write_workspace(tmp_path, spike=False, python=Path("/py/bin/python"))
    agents = tmp_path / ".claude" / "agents"
    assert sorted(p.stem for p in agents.glob("agent-*.md")) == [f"agent-{i}" for i in range(1, 9)]
    seat = _frontmatter((agents / "seat-2.md").read_text(encoding="utf-8"))
    alias_text = (agents / "agent-2.md").read_text(encoding="utf-8")
    alias = _frontmatter(alias_text)
    assert alias["tools"] == seat["tools"] and alias["model"] == seat["model"]
    assert "my_turn" in alias_text and "제출 완료" in alias_text
    assert not any(ch.isdigit() for ch in alias["description"])
