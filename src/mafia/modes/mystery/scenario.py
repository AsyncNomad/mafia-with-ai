"""Scenario schema: the contract between the scenario architect and the game.

Visibility of each part (docs/modes/murder-mystery.md §4):
P = public, S = the owning character's sheet, G = god only (until the game ends).
Ids are short and typed by prefix so that cross references are easy to validate:
characters `c1`..`c8`, locations `l1`..`l6`, clues `k01`.., facts `f1`...
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1

CharacterId = Annotated[str, Field(pattern=r"^c[1-8]$")]
LocationId = Annotated[str, Field(pattern=r"^l[1-9]$")]
ClueId = Annotated[str, Field(pattern=r"^k[0-9]{2}$")]
FactId = Annotated[str, Field(pattern=r"^f[0-9]{1,2}$")]
Text = Annotated[str, Field(min_length=1)]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- public world ---------------------------------------------------------------------------
class Concept(_Model):
    title: Text  # P
    logline: Text  # P
    tone: Text
    era: Text
    setting: Text
    twist_type: Text
    content_notes: tuple[str, ...] = ()


class Location(_Model):
    id: LocationId
    name: Text
    description: Text  # P


class World(_Model):
    setting_description: Text  # P
    locations: tuple[Location, ...]


class Victim(_Model):
    name: Text  # P
    public_profile: Text  # P
    true_background: Text  # G


# --- truth (G) ------------------------------------------------------------------------------
class TimelineEntry(_Model):
    slot: Text
    character_id: CharacterId
    location_id: LocationId
    action: Text
    witnessed_by: tuple[CharacterId, ...] = ()


class Deduction(_Model):
    step: int = Field(ge=1)
    claim: Text
    evidence: tuple[str, ...] = Field(min_length=1)  # clue or fact ids


class Truth(_Model):
    culprit_id: CharacterId
    motive: Text
    means: Text
    opportunity: Text
    summary: Text
    time_slots: tuple[str, ...] = Field(min_length=6, max_length=12)
    timeline: tuple[TimelineEntry, ...]
    key_deductions: tuple[Deduction, ...] = Field(min_length=3)


class Fact(_Model):
    id: FactId
    statement: Text
    decisive: bool = False  # needed to convict the culprit
    proven_by: tuple[ClueId, ...] = Field(min_length=1)
    excludes: CharacterId | None = None  # this fact rules out that (innocent) character


# --- characters -----------------------------------------------------------------------------
class Secret(_Model):
    what: Text
    why_hide: Text
    exposed_by: tuple[ClueId, ...] = Field(min_length=1)


class NightEntry(_Model):
    slot: Text
    location_id: LocationId
    action: Text


class Knowledge(_Model):
    what: Text
    slot: str | None = None
    fact_id: FactId | None = None


class Lie(_Model):
    about: Text
    false_version: Text
    why: Text
    broken_by: tuple[ClueId, ...] = Field(min_length=1)


class HoldClue(_Model):
    type: Literal["hold_clue"] = "hold_clue"
    clue: ClueId


class ClueNotRevealed(_Model):
    type: Literal["clue_not_revealed"] = "clue_not_revealed"
    clue: ClueId


class ClueRevealed(_Model):
    type: Literal["clue_revealed"] = "clue_revealed"
    clue: ClueId


class AccusedIs(_Model):
    type: Literal["accused_is"] = "accused_is"
    character: CharacterId


class AccusedIsNot(_Model):
    type: Literal["accused_is_not"] = "accused_is_not"
    character: CharacterId


class CulpritCaught(_Model):
    type: Literal["culprit_caught"] = "culprit_caught"


class CulpritEscaped(_Model):
    type: Literal["culprit_escaped"] = "culprit_escaped"


class VotedFor(_Model):
    type: Literal["voted_for"] = "voted_for"
    character: CharacterId


Predicate = Annotated[
    HoldClue
    | ClueNotRevealed
    | ClueRevealed
    | AccusedIs
    | AccusedIsNot
    | CulpritCaught
    | CulpritEscaped
    | VotedFor,
    Field(discriminator="type"),
]


class Goal(_Model):
    description: Text
    predicate: Predicate
    points: int = Field(ge=1, le=3)


class PlayGuide(_Model):
    temperament: Text
    speech_style: Text
    sample_lines: tuple[str, ...] = Field(min_length=2, max_length=3)


class CulpritKit(_Model):
    cover_story: Text
    dangerous_clues: tuple[ClueId, ...] = Field(min_length=1)
    deflection: tuple[str, ...] = Field(min_length=1)


class Sheet(_Model):
    background: Text
    relation_to_victim_truth: Text
    secret: Secret
    motive: Text
    night: tuple[NightEntry, ...]
    knowledge: tuple[Knowledge, ...] = ()
    lies: tuple[Lie, ...] = ()
    say_freely: tuple[str, ...] = ()
    hide: tuple[str, ...] = ()
    relationships: dict[CharacterId, str] = Field(default_factory=dict)
    goals: tuple[Goal, ...] = Field(min_length=1, max_length=2)
    play_guide: PlayGuide
    culprit_kit: CulpritKit | None = None


class Character(_Model):
    id: CharacterId
    name: Text
    age: int = Field(ge=5, le=110)
    occupation: Text
    public_profile: Text  # P
    public_relation_to_victim: Text  # P
    sheet: Sheet  # S


# --- clues ----------------------------------------------------------------------------------
class Clue(_Model):
    id: ClueId
    title: Text
    text: Text  # observable facts only; interpretation is the players' job
    kind: Text  # e.g. "물건", "문서", "증언", "기록"
    round: Literal[0, 1, 2]  # 0 = starting hand
    location_id: LocationId | None = None  # rounds 1-2
    starting_holder: CharacterId | None = None  # round 0
    unlock_condition: ClueId | None = None  # enters the pool once this clue is revealed
    supports: tuple[FactId, ...] = ()
    points_to: tuple[CharacterId, ...] = ()  # characters this card casts suspicion on
    is_red_herring: bool = False
    red_herring_resolution: tuple[ClueId, ...] = ()


# --- narration ------------------------------------------------------------------------------
class Narration(_Model):
    prologue: Text  # P
    character_intros: dict[CharacterId, str]  # P
    round_intros: tuple[str, ...] = Field(min_length=2, max_length=2)  # P, before each round
    midgame_twist: Text  # P
    accusation_intro: Text  # P
    ending_caught: Text  # P at the end
    ending_escaped: Text  # P at the end
    truth_reveal: Text  # P after the game
    epilogues: dict[CharacterId, str]  # P after the game


class Scenario(_Model):
    schema_version: Literal[1] = SCHEMA_VERSION
    concept: Concept
    world: World
    victim: Victim
    truth: Truth
    facts: tuple[Fact, ...]
    characters: tuple[Character, ...]
    clues: tuple[Clue, ...]
    narration: Narration

    def character(self, character_id: str) -> Character:
        return next(c for c in self.characters if c.id == character_id)

    def clue(self, clue_id: str) -> Clue:
        return next(k for k in self.clues if k.id == clue_id)
