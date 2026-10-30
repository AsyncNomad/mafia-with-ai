"""AI persona pool (config/personas.toml)."""

from __future__ import annotations

import random
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mafia import paths


class Persona(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=8)
    age: int = Field(ge=10, le=99)
    occupation: str
    personality: str
    speech_style: str
    trust: Literal["낮음", "보통", "높음"]
    deception: Literal["서툼", "보통", "능숙"]
    talkativeness: Literal["과묵", "보통", "수다"]
    quirks: tuple[str, ...] = ()
    sample_lines: tuple[str, ...] = ()


class PersonaPool(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    persona: tuple[Persona, ...]

    @model_validator(mode="after")
    def _unique_names(self) -> PersonaPool:
        names = [p.name for p in self.persona]
        if len(names) != len(set(names)):
            raise ValueError("persona names must be unique")
        initials = [n[0] for n in names]
        if len(initials) != len(set(initials)):
            raise ValueError("persona names must not share a first syllable")
        return self


def personas_path() -> Path:
    return paths.config_dir() / "personas.toml"


def load_personas(path: Path | None = None) -> tuple[Persona, ...]:
    with (path or personas_path()).open("rb") as f:
        return PersonaPool.model_validate(tomllib.load(f)).persona


def pick(
    pool: tuple[Persona, ...], count: int, rng: random.Random, *, avoid: str = ""
) -> list[Persona]:
    """`count` distinct personas, skipping any whose name collides with `avoid` (the human)."""
    candidates = [p for p in pool if p.name != avoid and (not avoid or p.name[0] != avoid[0])]
    if len(candidates) < count:
        raise ValueError(f"persona pool has {len(candidates)} usable entries, need {count}")
    return rng.sample(candidates, count)
