"""Mode lookup by name, so a stored game can be reopened with the same rules and config."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from mafia.engine.mode import Mode
from mafia.modes.mystery.rules import MysteryConfig, MysteryMode
from mafia.modes.original.config import OriginalConfig
from mafia.modes.original.rules import OriginalMode

ModeFactory = Callable[[Mapping[str, Any]], Mode[Any]]


def _original(config: Mapping[str, Any]) -> Mode[Any]:
    return OriginalMode(OriginalConfig.model_validate(dict(config)))


def _mystery(config: Mapping[str, Any]) -> Mode[Any]:
    return MysteryMode(MysteryConfig.model_validate(dict(config)))


_FACTORIES: dict[str, ModeFactory] = {
    OriginalMode.name: _original,
    MysteryMode.name: _mystery,
}


def mode_names() -> list[str]:
    return sorted(_FACTORIES)


def build_mode(name: str, config: Mapping[str, Any]) -> Mode[Any]:
    try:
        factory = _FACTORIES[name]
    except KeyError:
        raise ValueError(f"unknown mode {name!r}; available: {mode_names()}") from None
    return factory(config)


def default_config(name: str) -> dict[str, Any]:
    if name == OriginalMode.name:
        return OriginalConfig().model_dump(mode="json")
    raise ValueError(f"unknown mode {name!r}")
