"""The persona pool is the table's cast: 20s-30s peers chatting, not a panel giving speeches."""

from __future__ import annotations

import pytest

from mafia.personas import Persona, load_personas

# Registers that made the AI seats sound like elders or officials (observed in play).
STIFF = ("습니다", "십시오", "바랍니다", "양반", "허허", "구먼", "하오")


@pytest.mark.parametrize("persona", load_personas(), ids=lambda p: p.name)
def test_every_persona_is_in_their_twenties_or_thirties(persona: Persona) -> None:
    assert 20 <= persona.age <= 39


@pytest.mark.parametrize("persona", load_personas(), ids=lambda p: p.name)
def test_sample_lines_use_the_chat_register(persona: Persona) -> None:
    assert persona.sample_lines
    for line in persona.sample_lines:
        assert not any(marker in line for marker in STIFF), line
    assert "하십시오체" not in persona.speech_style and "속담" not in persona.speech_style


def test_pool_still_covers_a_table() -> None:
    assert len(load_personas()) >= 12
