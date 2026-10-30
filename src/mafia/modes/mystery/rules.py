"""Murder-mystery mode rules (docs/modes/murder-mystery.md §2) as an engine `Mode`.

Same transition discipline as the original mode: phase changes emit events only, and the next
`progress` call opens requests from the updated state.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mafia.engine.core_events import ActionSubmitted, GameEnded, PhaseChanged, RequestOpened
from mafia.engine.events import Event, NewEvent, emit
from mafia.engine.mode import CoreState, Progress
from mafia.engine.requests import Action, ActionRejected, Answer, RequestSpec, SeatRequest
from mafia.engine.runtime import rng_for
from mafia.engine.visibility import Viewer, Visibility
from mafia.modes.mystery.events import (
    AccusationRevealed,
    CardReceived,
    CardRevealed,
    CharacterAssigned,
    EndingRevealed,
    GoalResult,
    Investigated,
    Narrated,
    SpeakingRoundStarted,
    Speech,
)
from mafia.modes.mystery.scenario import (
    AccusedIs,
    AccusedIsNot,
    Clue,
    ClueNotRevealed,
    ClueRevealed,
    CulpritCaught,
    CulpritEscaped,
    HoldClue,
    Scenario,
    VotedFor,
)

INNOCENTS = "innocents"
CULPRIT = "culprit"


class Phase(StrEnum):
    SETUP = "setup"
    STATEMENT = "statement"
    INVESTIGATION = "investigation"
    DISCUSSION = "discussion"
    FINAL = "final"
    ACCUSATION = "accusation"
    RUNOFF = "runoff"
    ENDED = "ended"


class Kind(StrEnum):
    STATEMENT = "statement"
    INVESTIGATE = "investigate"
    DISCUSSION = "discussion"
    FINAL = "final_statement"
    ACCUSE = "accuse"


SPEECH_KINDS = frozenset({Kind.STATEMENT, Kind.DISCUSSION, Kind.FINAL})


class MysteryConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario: Scenario
    discussion_rounds: int = Field(default=2, ge=1, le=4)


def cast_seats(seed: str, character_ids: Sequence[str]) -> dict[int, str]:
    """Seat -> character. Shared by the table (to name seats) and the mode (secret assignment)."""
    ids = sorted(character_ids)
    rng_for(seed, "mystery_cast").shuffle(ids)
    return {seat: cid for seat, cid in enumerate(ids, start=1)}


def _empty() -> Mapping[Any, Any]:
    return MappingProxyType({})


def _with(mapping: Mapping[Any, Any], key: Any, value: Any) -> Mapping[Any, Any]:
    return MappingProxyType({**mapping, key: value})


@dataclass(frozen=True, slots=True)
class MysteryState:
    character_of: Mapping[int, str] = field(default_factory=_empty)
    phase: Phase = Phase.SETUP
    investigation: int = 0
    round: int = 0
    order: tuple[int, ...] = ()
    done: frozenset[int] = frozenset()
    opened: bool = False
    picks: Mapping[int, int | None] = field(default_factory=_empty)
    hands: Mapping[int, tuple[str, ...]] = field(default_factory=_empty)
    found: frozenset[str] = frozenset()
    revealed: frozenset[str] = frozenset()
    votes: Mapping[int, int | None] = field(default_factory=_empty)
    runoff: tuple[int, ...] = ()
    last_ballot: Mapping[int, int | None] = field(default_factory=_empty)


class MysteryMode:
    name = "mystery"

    def __init__(self, config: MysteryConfig) -> None:
        self.config = config
        self.s = config.scenario
        self._locations = tuple(self.s.world.locations)

    # --- folding ------------------------------------------------------------------------
    def initial_state(self) -> MysteryState:
        return MysteryState()

    def reduce(self, state: MysteryState, core: CoreState, event: Event) -> MysteryState:
        match event.kind:
            case CharacterAssigned.KIND:
                b = event.body_as(CharacterAssigned)
                return replace(
                    state, character_of=_with(state.character_of, b.seat, b.character_id)
                )
            case CardReceived.KIND:
                b = event.body_as(CardReceived)
                hand = (*state.hands.get(b.seat, ()), b.clue_id)
                return replace(
                    state, hands=_with(state.hands, b.seat, hand), found=state.found | {b.clue_id}
                )
            case CardRevealed.KIND:
                return replace(
                    state, revealed=state.revealed | {event.body_as(CardRevealed).clue_id}
                )
            case PhaseChanged.KIND:
                b = event.body_as(PhaseChanged)
                phase = Phase(b.phase)
                state = replace(
                    state, phase=phase, opened=False, round=0, order=(), done=frozenset()
                )
                if phase is Phase.INVESTIGATION:
                    return replace(state, investigation=b.day, picks=_empty())
                if phase in (Phase.ACCUSATION, Phase.RUNOFF):
                    return replace(state, votes=_empty())
                return state
            case SpeakingRoundStarted.KIND:
                b = event.body_as(SpeakingRoundStarted)
                return replace(state, round=b.round, order=b.order, done=frozenset())
            case RequestOpened.KIND:
                return replace(state, opened=True)
            case ActionSubmitted.KIND:
                b = event.body_as(ActionSubmitted)
                match Kind(b.kind):
                    case Kind.STATEMENT | Kind.DISCUSSION | Kind.FINAL:
                        return replace(state, done=state.done | {b.seat})
                    case Kind.INVESTIGATE:
                        return replace(state, picks=_with(state.picks, b.seat, b.action.target))
                    case Kind.ACCUSE:
                        return replace(
                            state,
                            votes=_with(state.votes, b.seat, b.action.target),
                            last_ballot=_with(state.last_ballot, b.seat, b.action.target),
                        )
            case AccusationRevealed.KIND:
                return replace(state, runoff=event.body_as(AccusationRevealed).runoff_between)
            case GameEnded.KIND:
                return replace(state, phase=Phase.ENDED)
            case _:
                pass
        return state

    # --- setup and progress -------------------------------------------------------------
    def setup(self, state: MysteryState, core: CoreState) -> Sequence[NewEvent]:
        cast = cast_seats(core.seed, [c.id for c in self.s.characters])
        if sorted(cast) != sorted(core.seat_numbers):
            raise ValueError("the table must have exactly one seat per scenario character")
        events: list[NewEvent] = []
        for seat, cid in sorted(cast.items()):
            events.append(
                emit(CharacterAssigned(seat=seat, character_id=cid), Visibility.to_seats(seat))
            )
            for clue in self.s.clues:
                if clue.round == 0 and clue.starting_holder == cid:
                    events.append(self._card(seat, clue))
        return events

    def progress(self, state: MysteryState, core: CoreState) -> Progress:
        seats = sorted(core.seat_numbers)
        match state.phase:
            case Phase.SETUP:
                return Progress(
                    events=[
                        self._narrate("prologue", self._prologue()),
                        _phase(1, Phase.STATEMENT),
                        self._round(core, Phase.STATEMENT, 1, shuffle=False),
                    ]
                )
            case Phase.STATEMENT | Phase.DISCUSSION | Phase.FINAL:
                return self._speaking(state, core, seats)
            case Phase.INVESTIGATION:
                if not state.opened:
                    return Progress(requests=[self._investigate_request(state, s) for s in seats])
                return self._resolve_investigation(state, core)
            case Phase.ACCUSATION | Phase.RUNOFF:
                if not state.opened:
                    return Progress(requests=[self._accuse_request(state, core, s) for s in seats])
                return self._resolve_accusation(state, core)
            case Phase.ENDED:
                raise RuntimeError("progress called after the game ended")

    def _speaking(self, state: MysteryState, core: CoreState, seats: list[int]) -> Progress:
        nxt = next((s for s in state.order if s not in state.done), None)
        kind = {
            Phase.STATEMENT: Kind.STATEMENT,
            Phase.DISCUSSION: Kind.DISCUSSION,
            Phase.FINAL: Kind.FINAL,
        }[state.phase]
        if nxt is not None:
            return Progress(requests=[self._speech_request(state, nxt, kind)])
        if state.phase is Phase.DISCUSSION and state.round < self.config.discussion_rounds:
            return Progress(
                events=[
                    self._round(core, Phase.DISCUSSION, state.round + 1, day=state.investigation)
                ]
            )
        match state.phase:
            case Phase.STATEMENT:
                return Progress(events=self._investigation_start(1))
            case Phase.DISCUSSION if state.investigation == 1:
                return Progress(
                    events=[
                        self._narrate("midgame_twist", self.s.narration.midgame_twist),
                        *self._investigation_start(2),
                    ]
                )
            case Phase.DISCUSSION:
                return Progress(
                    events=[_phase(2, Phase.FINAL), self._round(core, Phase.FINAL, 1, day=2)]
                )
            case _:
                return Progress(
                    events=[
                        self._narrate("accusation_intro", self.s.narration.accusation_intro),
                        _phase(2, Phase.ACCUSATION),
                    ]
                )

    def _investigation_start(self, n: int) -> list[NewEvent]:
        return [
            _phase(n, Phase.INVESTIGATION),
            self._narrate(f"round_intro_{n}", self.s.narration.round_intros[n - 1]),
        ]

    def _round(
        self, core: CoreState, phase: Phase, round_: int, *, day: int = 1, shuffle: bool = True
    ) -> NewEvent:
        order = sorted(core.seat_numbers)
        if shuffle:
            rng_for(core.seed, "mystery_order", phase.value, day, round_).shuffle(order)
        return emit(
            SpeakingRoundStarted(stage=phase.value, round=round_, order=tuple(order)),
            Visibility.public(),
        )

    def _speech_request(self, state: MysteryState, seat: int, kind: Kind) -> RequestSpec:
        text = {
            Kind.STATEMENT: "첫 진술 차례입니다. 자기소개와 그날 밤 당신의 행적을 말하세요(2~4문장).",
            Kind.DISCUSSION: f"{state.investigation}차 조사 후 토론 {state.round}/{self.config.discussion_rounds}라운드, 당신의 발언 차례입니다(1~3문장).",
            Kind.FINAL: "최종 변론입니다. 누가 범인인지, 혹은 왜 당신이 범인이 아닌지 말하세요(2~4문장).",
        }[kind]
        return RequestSpec(
            seat=seat,
            kind=kind,
            answer=Answer.SPEECH,
            instruction=text
            + " 손에 든 카드를 공개하려면 reveal에 카드 id를 넣으세요(최대 3장). 패스하려면 speech를 비워 두세요.",
            allow_pass=True,
        )

    def _investigate_request(self, state: MysteryState, seat: int) -> RequestSpec:
        n = state.investigation
        return RequestSpec(
            seat=seat,
            kind=Kind.INVESTIGATE,
            answer=Answer.TARGET,
            instruction=(
                f"{n}차 조사입니다. 조사할 장소를 고르세요. 그곳의 단서 카드 1장을 당신만 받습니다. "
                "누가 어디를 조사했는지는 모두에게 공개됩니다. 고른 곳에 남은 카드가 없으면 다른 곳으로 옮겨 갑니다."
            ),
            options=tuple(range(1, len(self._locations) + 1)),
            option_labels=tuple(loc.name for loc in self._locations),
            allow_pass=True,
        )

    def _pool(self, state: MysteryState, location_id: str, found: set[str]) -> list[Clue]:
        return [
            k
            for k in self.s.clues
            if k.location_id == location_id
            and 1 <= k.round <= state.investigation
            and k.id not in found
            and (k.unlock_condition is None or k.unlock_condition in state.revealed)
        ]

    def _resolve_investigation(self, state: MysteryState, core: CoreState) -> Progress:
        rng = rng_for(core.seed, "mystery_investigation", state.investigation)
        found = set(state.found)
        order = sorted(core.seat_numbers)
        rng.shuffle(order)
        visits: dict[int, str] = {}
        empty: list[int] = []
        cards: list[NewEvent] = []
        for seat in order:
            pick = state.picks.get(seat)
            index = pick if pick is not None else rng.randint(1, len(self._locations))
            location = self._locations[index - 1]
            pool = self._pool(state, location.id, found)
            if not pool:
                alternatives = [loc for loc in self._locations if self._pool(state, loc.id, found)]
                if not alternatives:
                    visits[seat] = location.name
                    empty.append(seat)
                    continue
                location = rng.choice(alternatives)
                pool = self._pool(state, location.id, found)
            clue = rng.choice(sorted(pool, key=lambda k: k.id))
            found.add(clue.id)
            visits[seat] = location.name
            cards.append(self._card(seat, clue))
        events = [
            emit(
                Investigated(
                    investigation=state.investigation,
                    visits=dict(sorted(visits.items())),
                    empty_handed=tuple(sorted(empty)),
                ),
                Visibility.public(),
            ),
            *cards,
            _phase(state.investigation, Phase.DISCUSSION),
            self._round(core, Phase.DISCUSSION, 1, day=state.investigation),
        ]
        return Progress(events=events)

    def _accuse_request(self, state: MysteryState, core: CoreState, seat: int) -> RequestSpec:
        if state.phase is Phase.RUNOFF:
            options = tuple(s for s in state.runoff if s != seat)
            text = "결선 투표입니다. 동률인 후보 중 범인이라고 생각하는 사람을 고르세요."
        else:
            options = tuple(s for s in sorted(core.seat_numbers) if s != seat)
            text = "범인 지목 투표입니다. 범인이라고 생각하는 사람을 고르세요. 투표는 동시에 공개됩니다."
        return RequestSpec(
            seat=seat,
            kind=Kind.ACCUSE,
            answer=Answer.TARGET,
            instruction=text + " 기권하려면 target을 비워 두세요.",
            options=options,
            allow_pass=True,
        )

    def _resolve_accusation(self, state: MysteryState, core: CoreState) -> Progress:
        votes = {s: state.votes.get(s) for s in sorted(core.seat_numbers)}
        tally = Counter(t for t in votes.values() if t is not None)
        accused: int | None = None
        leaders: list[int] = []
        if tally:
            top = max(tally.values())
            leaders = sorted(s for s, n in tally.items() if n == top)
            accused = leaders[0] if len(leaders) == 1 else None
        if accused is None and len(leaders) > 1 and state.phase is Phase.ACCUSATION:
            return Progress(
                events=[
                    emit(
                        AccusationRevealed(
                            votes=votes, accused=None, runoff_between=tuple(leaders)
                        ),
                        Visibility.public(),
                    ),
                    _phase(2, Phase.RUNOFF),
                ]
            )
        return Progress(
            events=[
                emit(AccusationRevealed(votes=votes, accused=accused), Visibility.public()),
                *self._ending(state, accused),
            ]
        )

    def _ending(self, state: MysteryState, accused: int | None) -> list[NewEvent]:
        seat_of = {cid: seat for seat, cid in state.character_of.items()}
        culprit_seat = seat_of[self.s.truth.culprit_id]
        caught = accused == culprit_seat
        accused_char = state.character_of.get(accused) if accused is not None else None
        goals: dict[int, tuple[GoalResult, ...]] = {}
        for seat, cid in sorted(state.character_of.items()):
            results: list[GoalResult] = []
            for goal in self.s.character(cid).sheet.goals:
                p = goal.predicate
                ballot = state.last_ballot.get(seat)
                match p:
                    case HoldClue():
                        ok = p.clue in state.hands.get(seat, ())
                    case ClueNotRevealed():
                        ok = p.clue not in state.revealed
                    case ClueRevealed():
                        ok = p.clue in state.revealed
                    case AccusedIs():
                        ok = accused_char == p.character
                    case AccusedIsNot():
                        ok = accused_char != p.character
                    case CulpritCaught():
                        ok = caught
                    case CulpritEscaped():
                        ok = not caught
                    case VotedFor():
                        ok = ballot is not None and state.character_of.get(ballot) == p.character
                results.append(
                    GoalResult(
                        description=goal.description, achieved=ok, points=goal.points if ok else 0
                    )
                )
            goals[seat] = tuple(results)
        names = {seat: self.s.character(cid).name for seat, cid in state.character_of.items()}
        ending = EndingRevealed(
            caught=caught,
            culprit_seat=culprit_seat,
            ending=self.s.narration.ending_caught if caught else self.s.narration.ending_escaped,
            truth=self.s.narration.truth_reveal,
            characters=names,
            epilogues={
                seat: self.s.narration.epilogues.get(cid, "")
                for seat, cid in state.character_of.items()
            },
            goals=goals,
        )
        return [
            emit(ending, Visibility.public()),
            emit(
                GameEnded(winner=INNOCENTS if caught else CULPRIT, reason="accusation"),
                Visibility.public(),
            ),
        ]

    # --- helpers ------------------------------------------------------------------------
    def _card(self, seat: int, clue: Clue) -> NewEvent:
        return emit(
            CardReceived(
                seat=seat,
                clue_id=clue.id,
                title=clue.title,
                text=clue.text,
                where=self._where(clue),
            ),
            Visibility.to_seats(seat),
        )

    def _where(self, clue: Clue) -> str:
        if clue.round == 0:
            return "시작 카드"
        loc = next(
            (x.name for x in self._locations if x.id == clue.location_id), clue.location_id or ""
        )
        return f"{loc} · {clue.round}라운드"

    def _narrate(self, beat: str, text: str) -> NewEvent:
        return emit(Narrated(beat=beat, text=text), Visibility.public())

    def _prologue(self) -> str:
        s = self.s
        intros = "\n".join(
            f"- **{c.name}** ({c.age}세, {c.occupation}): {s.narration.character_intros.get(c.id, c.public_profile)}"
            for c in s.characters
        )
        places = ", ".join(loc.name for loc in s.world.locations)
        return (
            f"# {s.concept.title}\n*{s.concept.logline}*\n\n{s.narration.prologue}\n\n"
            f"**피해자** {s.victim.name}: {s.victim.public_profile}\n\n**등장인물**\n{intros}\n\n**조사할 수 있는 곳** {places}"
        )

    # --- seat-facing --------------------------------------------------------------------
    def validate(
        self, state: MysteryState, core: CoreState, request: SeatRequest, action: Action
    ) -> None:
        if not action.reveal:
            return
        if Kind(request.kind) not in SPEECH_KINDS:
            raise ActionRejected("카드는 발언할 때만 공개할 수 있습니다.")
        hand = state.hands.get(request.seat, ())
        if len(set(action.reveal)) != len(action.reveal):
            raise ActionRejected("같은 카드를 두 번 공개할 수 없습니다.")
        for clue_id in action.reveal:
            if clue_id not in hand:
                raise ActionRejected(
                    f"{clue_id}는 당신 손에 없는 카드입니다. 손에 든 카드: {list(hand)}"
                )
            if clue_id in state.revealed:
                raise ActionRejected(f"{clue_id}는 이미 공개된 카드입니다.")

    def on_action(
        self, state: MysteryState, core: CoreState, request: SeatRequest, action: Action
    ) -> Sequence[NewEvent]:
        if Kind(request.kind) not in SPEECH_KINDS:
            return []
        events = [
            emit(
                Speech(seat=request.seat, text=action.speech, stage=request.kind),
                Visibility.public(),
            )
        ]
        for clue_id in action.reveal:
            clue = self.s.clue(clue_id)
            events.append(
                emit(
                    CardRevealed(
                        seat=request.seat, clue_id=clue.id, title=clue.title, text=clue.text
                    ),
                    Visibility.public(),
                )
            )
        return events

    def fallback(self, state: MysteryState, core: CoreState, request: SeatRequest) -> Action:
        return Action()  # pass; an investigation with no pick goes to a random location

    def viewer(self, state: MysteryState, core: CoreState, seat: int) -> Viewer:
        return Viewer(seat=seat)

    def seat_brief(self, state: MysteryState, core: CoreState, seat: int) -> Mapping[str, Any]:
        cid = state.character_of.get(seat)
        character = self.s.character(cid) if cid else None
        hand = [
            {
                "id": k,
                "title": self.s.clue(k).title,
                "text": self.s.clue(k).text,
                "where": self._where(self.s.clue(k)),
                "revealed": k in state.revealed,
            }
            for k in state.hands.get(seat, ())
        ]
        names = {
            self.s.character(c).id: f"{self.s.character(c).name}({s}번)"
            for s, c in state.character_of.items()
        }
        return {
            "mode": self.name,
            "seat": seat,
            "character": None if character is None else character.model_dump(mode="json"),
            "names_by_character": names,
            "hand": hand,
            "locations": [loc.name for loc in self._locations],
            "location_names": {loc.id: loc.name for loc in self._locations},
            "phase": state.phase.value,
            "investigation": state.investigation,
            "title": self.s.concept.title,
        }


def _phase(day: int, phase: Phase) -> NewEvent:
    return emit(PhaseChanged(day=day, phase=phase.value), Visibility.public())
