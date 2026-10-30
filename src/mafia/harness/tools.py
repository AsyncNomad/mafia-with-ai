"""Game tool families, by host-neutral name (no MCP prefix).

The family a tool belongs to decides which callers may use it. Adding a tool to the MCP
server without registering it here makes it unusable by everyone, which is the safe default.
"""

from __future__ import annotations

from collections.abc import Mapping

MCP_SERVER_NAME = "mafia"

# Callable by the main session. Must never return information beyond the human's own view.
PUBLIC_TOOLS: frozenset[str] = frozenset(
    {
        "new_game",
        "advance",
        "status",
        "ping",  # M0 spike only
        "spike_arm",  # M0 spike only
    }
)

# Callable by seat agents. The harness overwrites the `seat` argument with the caller's seat.
SEAT_TOOLS: frozenset[str] = frozenset(
    {
        "my_turn",
        "submit_speech",
        "submit_target",
        "submit_approval",
    }
)

# Callable only by the named agent.
AGENT_TOOLS: Mapping[str, frozenset[str]] = {
    "mystery-architect": frozenset(
        {"mystery_brief", "mystery_revision_packet", "mystery_submit_section", "mystery_check"}
    ),
    "mystery-auditor": frozenset({"mystery_review_packet", "mystery_submit_audit"}),
    "mystery-solver": frozenset({"mystery_solver_packet", "mystery_submit_solution"}),
    "mystery-narrator": frozenset({"narration_packet", "submit_narration"}),
}

KNOWN_AGENTS: frozenset[str] = frozenset(AGENT_TOOLS)

SEAT_ARGUMENT = "seat"
