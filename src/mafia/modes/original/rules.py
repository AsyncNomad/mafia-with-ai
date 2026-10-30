"""Original mode rules as an engine `Mode`.

Transition discipline: when a phase changes, `progress` emits events only. Requests for the new
phase are opened by the *next* `progress` call, computed from the state that already reflects
those events (e.g. an execution). This makes "a dead seat got a request" structurally impossible.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from mafia.engine.core_events import ActionSubmitted, GameEnded, PhaseChanged, RequestOpened
from mafia.engine.events import Event, NewEvent, emit
from mafia.engine.mode import CoreState, Progress
from mafia.engine.requests import Action, Answer, RequestSpec, SeatRequest
from mafia.engine.runtime import rng_for
from mafia.engine.visibility import Viewer, Visibility
from mafia.modes.original.config import OriginalConfig
from mafia.modes.original.events import (
    ConfirmationRevealed,
    DiscussionRoundStarted,
    InvestigationResult,
    MafiaChat,
    MafiaChatRoundStarted,
    MafiaTargetChosen,
    NightResolved,
    NominationRevealed,
    PeacefulNight,
    PlayerDied,
    RoleAssigned,
    RolesRevealed,
    Speech,
)
from mafia.modes.original.roles import Role, Team

MAFIA_TEAM = Team.MAFIA.value


class Phase(StrEnum):
    SETUP = "setup"
    DISCUSSION = "discussion"
    NOMINATION = "nomination"
    DEFENSE = "defense"
    CONFIRMATION = "confirmation"
    MAFIA_CHAT = "mafia_chat"
    NIGHT_ACTIONS = "night_actions"
    ENDED = "ended"


class Kind(StrEnum):
    DISCUSSION = "discussion"
    NOMINATION_VOTE = "nomination_vote"
    DEFENSE = "defense"
    CONFIRMATION_VOTE = "confirmation_vote"
    MAFIA_CHAT = "mafia_chat"
    MAFIA_KILL = "mafia_kill"
    INVESTIGATE = "investigate"
    PROTECT = "protect"


def _empty() -> Mapping[Any, Any]:
    return MappingProxyType({})


def _with(mapping: Mapping[Any, Any], key: Any, value: Any) -> Mapping[Any, Any]:
    return MappingProxyType({**mapping, key: value})


@dataclass(frozen=True, slots=True)
class OriginalState:
    roles: Mapping[int, Role] = field(default_factory=_empty)
    alive: frozenset[int] = frozenset()
    phase: Phase = Phase.SETUP
    day: int = 0
    # sequential phases (discussion, mafia chat)
    round: int = 0
    order: tuple[int, ...] = ()
    done: frozenset[int] = frozenset()
    # parallel phases: whether this phase's request batch has been opened
    opened: bool = False
    nomination_votes: Mapping[int, int | None] = field(default_factory=_empty)
    nominee: int | None = None
    confirmation_votes: Mapping[int, bool | None] = field(default_factory=_empty)
    kill_votes: Mapping[int, int | None] = field(default_factory=_empty)
    protect: int | None = None
    investigate: int | None = None
    last_protected: int | None = None

    def alive_with(self, role: Role) -> list[int]:
        return sorted(s for s in self.alive if self.roles.get(s) is role)

    def alive_mafia(self) -> list[int]:
        return self.alive_with(Role.MAFIA)


def winner_of(roles: Mapping[int, Role], alive: frozenset[int]) -> Team | None:
    mafia = sum(1 for s in alive if roles[s] is Role.MAFIA)
    others = len(alive) - mafia
    if mafia == 0:
        return Team.CITIZENS
    if mafia >= others:
        return Team.MAFIA
    return None


class OriginalMode:
    name = "original"

    def __init__(self, config: OriginalConfig | None = None) -> None:
        self.config = config or OriginalConfig()

    # --- folding ------------------------------------------------------------------------
    def initial_state(self) -> OriginalState:
        return OriginalState()

    def reduce(self, state: OriginalState, core: CoreState, event: Event) -> OriginalState:
        match event.kind:
            case RoleAssigned.KIND:
                b = event.body_as(RoleAssigned)
                return replace(
                    state, roles=_with(state.roles, b.seat, b.role), alive=state.alive | {b.seat}
                )
            case PhaseChanged.KIND:
                return self._enter_phase(state, event.body_as(PhaseChanged))
            case DiscussionRoundStarted.KIND:
                b = event.body_as(DiscussionRoundStarted)
                return replace(state, round=b.round, order=b.order, done=frozenset())
            case MafiaChatRoundStarted.KIND:
                b = event.body_as(MafiaChatRoundStarted)
                order = tuple(state.alive_mafia())
                return replace(state, round=b.round, order=order, done=frozenset())
            case RequestOpened.KIND:
                return replace(state, opened=True)
            case ActionSubmitted.KIND:
                return self._record_action(state, event.body_as(ActionSubmitted))
            case NominationRevealed.KIND:
                return replace(state, nominee=event.body_as(NominationRevealed).nominee)
            case PlayerDied.KIND:
                return replace(state, alive=state.alive - {event.body_as(PlayerDied).seat})
            case NightResolved.KIND:
                return replace(state, last_protected=event.body_as(NightResolved).protected)
            case GameEnded.KIND:
                return replace(state, phase=Phase.ENDED)
            case _:
                return state

    @staticmethod
    def _enter_phase(state: OriginalState, b: PhaseChanged) -> OriginalState:
        phase = Phase(b.phase)
        state = replace(
            state, phase=phase, day=b.day, opened=False, round=0, order=(), done=frozenset()
        )
        match phase:
            case Phase.NOMINATION:
                return replace(state, nomination_votes=_empty(), nominee=None)
            case Phase.CONFIRMATION:
                return replace(state, confirmation_votes=_empty())
            case Phase.NIGHT_ACTIONS:
                return replace(state, kill_votes=_empty(), protect=None, investigate=None)
            case _:
                return state

    @staticmethod
    def _record_action(state: OriginalState, b: ActionSubmitted) -> OriginalState:
        a = b.action
        match Kind(b.kind):
            case Kind.DISCUSSION | Kind.DEFENSE | Kind.MAFIA_CHAT:
                return replace(state, done=state.done | {b.seat})
            case Kind.NOMINATION_VOTE:
                return replace(
                    state, nomination_votes=_with(state.nomination_votes, b.seat, a.target)
                )
            case Kind.CONFIRMATION_VOTE:
                return replace(
                    state, confirmation_votes=_with(state.confirmation_votes, b.seat, a.approve)
                )
            case Kind.MAFIA_KILL:
                return replace(state, kill_votes=_with(state.kill_votes, b.seat, a.target))
            case Kind.INVESTIGATE:
                return replace(state, investigate=a.target)
            case Kind.PROTECT:
                return replace(state, protect=a.target)

    # --- setup and progress -------------------------------------------------------------
    def setup(self, state: OriginalState, core: CoreState) -> Sequence[NewEvent]:
        deck = [role for role, count in self.config.roles.items() for _ in range(count)]
        if len(deck) != len(core.seats):
            raise ValueError(f"{len(deck)} roles for {len(core.seats)} seats")
        rng_for(core.seed, "roles").shuffle(deck)
        assignment = dict(zip(core.seat_numbers, deck, strict=True))
        mafia = tuple(s for s, r in assignment.items() if r is Role.MAFIA)
        return [
            emit(
                RoleAssigned(
                    seat=seat,
                    role=role,
                    teammates=tuple(m for m in mafia if m != seat) if role is Role.MAFIA else (),
                ),
                Visibility.to_seats(seat),
            )
            for seat, role in assignment.items()
        ]

    def progress(self, state: OriginalState, core: CoreState) -> Progress:
        match state.phase:
            case Phase.SETUP:
                if self.config.start_with_night:
                    return Progress(events=self._night_start(state, day=1))
                return Progress(events=self._day_start(state, core, day=1))
            case Phase.DISCUSSION:
                return self._discussion(state, core)
            case Phase.NOMINATION:
                if not state.opened:
                    return self._open_or_resolve(
                        state, core, self._nomination_requests(state, core)
                    )
                return self._resolve_nomination(state, core)
            case Phase.DEFENSE:
                if not state.opened and state.nominee is not None:
                    return Progress(requests=[self._defense_request(state, core, state.nominee)])
                return Progress(events=[_phase(state.day, Phase.CONFIRMATION)])
            case Phase.CONFIRMATION:
                if not state.opened:
                    return self._open_or_resolve(
                        state, core, self._confirmation_requests(state, core)
                    )
                return self._resolve_confirmation(state, core)
            case Phase.MAFIA_CHAT:
                return self._mafia_chat(state, core)
            case Phase.NIGHT_ACTIONS:
                if not state.opened:
                    return self._open_or_resolve(state, core, self._night_requests(state, core))
                return self._resolve_night(state, core)
            case Phase.ENDED:
                raise RuntimeError("progress called after the game ended")

    def _open_or_resolve(
        self, state: OriginalState, core: CoreState, requests: list[RequestSpec]
    ) -> Progress:
        if requests:
            return Progress(requests=requests)
        # Nobody is eligible: resolve as if everyone abstained.
        return self.progress(replace(state, opened=True), core)

    def _day_start(self, state: OriginalState, core: CoreState, day: int) -> list[NewEvent]:
        return [_phase(day, Phase.DISCUSSION), self._round_start(state, core, day, 1)]

    def _round_start(
        self, state: OriginalState, core: CoreState, day: int, round_: int
    ) -> NewEvent:
        order = sorted(state.alive)
        rng_for(core.seed, "discussion_order", day, round_).shuffle(order)
        return emit(
            DiscussionRoundStarted(day=day, round=round_, order=tuple(order)), Visibility.public()
        )

    def _night_start(self, state: OriginalState, day: int) -> list[NewEvent]:
        if self.config.mafia_chat_rounds > 0 and len(state.alive_mafia()) >= 2:
            return [
                _phase(day, Phase.MAFIA_CHAT),
                emit(MafiaChatRoundStarted(day=day, round=1), Visibility.to_team(MAFIA_TEAM)),
            ]
        return [_phase(day, Phase.NIGHT_ACTIONS)]

    def _end(self, state: OriginalState, winner: Team | None, reason: str) -> list[NewEvent]:
        return [
            emit(RolesRevealed(roles=dict(state.roles), winner=winner), Visibility.public()),
            emit(
                GameEnded(winner=winner.value if winner else "draw", reason=reason),
                Visibility.public(),
            ),
        ]

    # --- day ----------------------------------------------------------------------------
    def _discussion(self, state: OriginalState, core: CoreState) -> Progress:
        nxt = next((s for s in state.order if s in state.alive and s not in state.done), None)
        if nxt is not None:
            return Progress(
                requests=[
                    RequestSpec(
                        seat=nxt,
                        kind=Kind.DISCUSSION,
                        answer=Answer.SPEECH,
                        instruction=(
                            f"{state.day}일차 낮 토론 {state.round}/{self.config.discussion_rounds}라운드, "
                            "당신의 발언 차례입니다. 1~3문장으로 말하세요. 패스하려면 speech를 비워 두세요."
                        ),
                        allow_pass=True,
                    )
                ]
            )
        if state.round < self.config.discussion_rounds:
            return Progress(events=[self._round_start(state, core, state.day, state.round + 1)])
        return Progress(events=[_phase(state.day, Phase.NOMINATION)])

    def _nomination_requests(self, state: OriginalState, core: CoreState) -> list[RequestSpec]:
        return [
            RequestSpec(
                seat=seat,
                kind=Kind.NOMINATION_VOTE,
                answer=Answer.TARGET,
                instruction=(
                    f"{state.day}일차 지목 투표입니다. 처형 후보로 지목할 사람을 고르세요. "
                    "투표는 모두 동시에 공개됩니다. 기권하려면 target을 비워 두세요."
                ),
                options=tuple(s for s in sorted(state.alive) if s != seat),
                allow_pass=True,
            )
            for seat in sorted(state.alive)
        ]

    def _resolve_nomination(self, state: OriginalState, core: CoreState) -> Progress:
        votes = {s: state.nomination_votes.get(s) for s in sorted(state.alive)}
        tally = Counter(t for t in votes.values() if t is not None)
        nominee: int | None = None
        if tally:
            top = max(tally.values())
            leaders = [s for s, n in tally.items() if n == top]
            nominee = leaders[0] if len(leaders) == 1 else None
        events = [emit(NominationRevealed(votes=votes, nominee=nominee), Visibility.public())]
        if nominee is not None:
            events.append(_phase(state.day, Phase.DEFENSE))
        else:
            events += self._night_start(state, state.day)
        return Progress(events=events)

    def _defense_request(self, state: OriginalState, core: CoreState, nominee: int) -> RequestSpec:
        return RequestSpec(
            seat=nominee,
            kind=Kind.DEFENSE,
            answer=Answer.SPEECH,
            instruction="당신이 처형 후보로 지목되었습니다. 최후 변론을 하세요(1~4문장).",
            allow_pass=True,
        )

    def _confirmation_requests(self, state: OriginalState, core: CoreState) -> list[RequestSpec]:
        if state.nominee is None:
            return []
        name = core.name_of(state.nominee)
        return [
            RequestSpec(
                seat=seat,
                kind=Kind.CONFIRMATION_VOTE,
                answer=Answer.APPROVAL,
                instruction=(
                    f"{name}({state.nominee}번)을 처형할지 찬반 투표입니다. "
                    "찬성은 approve=true, 반대는 false입니다. 기권하려면 비워 두세요."
                ),
                allow_pass=True,
            )
            for seat in sorted(state.alive)
            if seat != state.nominee
        ]

    def _resolve_confirmation(self, state: OriginalState, core: CoreState) -> Progress:
        voters = [s for s in sorted(state.alive) if s != state.nominee]
        votes = {s: state.confirmation_votes.get(s) for s in voters}
        yes = sum(1 for v in votes.values() if v is True)
        no = sum(1 for v in votes.values() if v is False)
        approved = state.nominee is not None and yes > no
        events = [emit(ConfirmationRevealed(votes=votes, approved=approved), Visibility.public())]
        if not approved or state.nominee is None:
            return Progress(events=events + self._night_start(state, state.day))
        events.append(self._death(state, state.nominee, "execution"))
        alive_after = state.alive - {state.nominee}
        winner = winner_of(state.roles, alive_after)
        if winner is not None:
            return Progress(events=events + self._end(state, winner, "execution"))
        return Progress(
            events=events + self._night_start(replace(state, alive=alive_after), state.day)
        )

    def _death(self, state: OriginalState, seat: int, cause: str) -> NewEvent:
        role = state.roles[seat] if self.config.reveal_role_on_death else None
        return emit(PlayerDied(seat=seat, cause=cause, role=role), Visibility.public())

    # --- night --------------------------------------------------------------------------
    def _mafia_chat(self, state: OriginalState, core: CoreState) -> Progress:
        nxt = next((s for s in state.order if s in state.alive and s not in state.done), None)
        if nxt is not None:
            return Progress(
                requests=[
                    RequestSpec(
                        seat=nxt,
                        kind=Kind.MAFIA_CHAT,
                        answer=Answer.SPEECH,
                        instruction=(
                            f"{state.day}일차 밤, 마피아끼리만 보는 대화입니다. "
                            "오늘 밤 누구를 노릴지 동료와 상의하세요(1~3문장)."
                        ),
                        allow_pass=True,
                    )
                ]
            )
        if state.round < self.config.mafia_chat_rounds:
            return Progress(
                events=[
                    emit(
                        MafiaChatRoundStarted(day=state.day, round=state.round + 1),
                        Visibility.to_team(MAFIA_TEAM),
                    )
                ]
            )
        return Progress(events=[_phase(state.day, Phase.NIGHT_ACTIONS)])

    def _night_requests(self, state: OriginalState, core: CoreState) -> list[RequestSpec]:
        mafia = set(state.alive_mafia())
        requests = [
            RequestSpec(
                seat=seat,
                kind=Kind.MAFIA_KILL,
                answer=Answer.TARGET,
                instruction="오늘 밤 처치할 사람을 고르세요. 마피아들의 표가 가장 많은 사람이 대상이 됩니다.",
                options=tuple(s for s in sorted(state.alive) if s not in mafia),
                allow_pass=True,
            )
            for seat in sorted(mafia)
        ]
        for police in state.alive_with(Role.POLICE):
            requests.append(
                RequestSpec(
                    seat=police,
                    kind=Kind.INVESTIGATE,
                    answer=Answer.TARGET,
                    instruction="오늘 밤 조사할 사람을 고르세요. 마피아인지 아닌지 당신에게만 알려 드립니다.",
                    options=tuple(s for s in sorted(state.alive) if s != police),
                    allow_pass=True,
                )
            )
        for doctor in state.alive_with(Role.DOCTOR):
            options = [
                s for s in sorted(state.alive) if self.config.doctor_self_protect or s != doctor
            ]
            if not self.config.doctor_repeat_protect and state.last_protected in options:
                options.remove(state.last_protected)
            requests.append(
                RequestSpec(
                    seat=doctor,
                    kind=Kind.PROTECT,
                    answer=Answer.TARGET,
                    instruction="오늘 밤 마피아로부터 지킬 사람을 고르세요.",
                    options=tuple(options),
                    allow_pass=True,
                )
            )
        return requests

    def _resolve_night(self, state: OriginalState, core: CoreState) -> Progress:
        votes = {s: state.kill_votes.get(s) for s in state.alive_mafia()}
        tally = Counter(t for t in votes.values() if t is not None)
        target: int | None = None
        if tally:
            top = max(tally.values())
            leaders = sorted(s for s, n in tally.items() if n == top)
            target = rng_for(core.seed, "mafia_tie", state.day).choice(leaders)
        killed = target if target is not None and target != state.protect else None

        events: list[NewEvent] = [
            emit(MafiaTargetChosen(target=target, votes=votes), Visibility.to_team(MAFIA_TEAM))
        ]
        for police in state.alive_with(Role.POLICE):
            if state.investigate is not None:
                events.append(
                    emit(
                        InvestigationResult(
                            target=state.investigate,
                            is_mafia=state.roles[state.investigate] is Role.MAFIA,
                        ),
                        Visibility.to_seats(police),
                    )
                )
        events.append(
            emit(
                NightResolved(
                    day=state.day,
                    mafia_target=target,
                    protected=state.protect,
                    investigated=state.investigate,
                    killed=killed,
                ),
                Visibility.god(),
            )
        )

        day = state.day + 1
        events.append(_phase(day, Phase.DISCUSSION))
        alive_after = state.alive
        if killed is not None:
            events.append(self._death(state, killed, "night"))
            alive_after = state.alive - {killed}
        else:
            events.append(emit(PeacefulNight(day=day), Visibility.public()))

        winner = winner_of(state.roles, alive_after)
        if winner is not None:
            return Progress(events=events + self._end(state, winner, "night"))
        if day > self.config.max_days:
            return Progress(events=events + self._end(state, None, "max_days"))
        events.append(self._round_start(replace(state, alive=alive_after), core, day, 1))
        return Progress(events=events)

    # --- seat-facing --------------------------------------------------------------------
    def validate(
        self, state: OriginalState, core: CoreState, request: SeatRequest, action: Action
    ) -> None:
        # Options are fixed when the request opens and nothing changes them before it closes.
        return None

    def on_action(
        self, state: OriginalState, core: CoreState, request: SeatRequest, action: Action
    ) -> Sequence[NewEvent]:
        match Kind(request.kind):
            case Kind.DISCUSSION | Kind.DEFENSE:
                return [
                    emit(
                        Speech(seat=request.seat, text=action.speech, context=request.kind),
                        Visibility.public(),
                    )
                ]
            case Kind.MAFIA_CHAT:
                return [
                    emit(
                        MafiaChat(seat=request.seat, text=action.speech),
                        Visibility.to_team(MAFIA_TEAM),
                    )
                ]
            case _:
                return []

    def fallback(self, state: OriginalState, core: CoreState, request: SeatRequest) -> Action:
        return Action()  # every request allows passing / abstaining

    def viewer(self, state: OriginalState, core: CoreState, seat: int) -> Viewer:
        teams: frozenset[str] = (
            frozenset({MAFIA_TEAM}) if state.roles.get(seat) is Role.MAFIA else frozenset()
        )
        return Viewer(seat=seat, teams=teams)

    def seat_brief(self, state: OriginalState, core: CoreState, seat: int) -> Mapping[str, Any]:
        role = state.roles[seat]
        teammates = (
            [
                {"seat": m, "name": core.name_of(m)}
                for m in sorted(state.roles)
                if m != seat and state.roles[m] is Role.MAFIA
            ]
            if role is Role.MAFIA
            else []
        )
        return {
            "seat": seat,
            "name": core.name_of(seat),
            "role": role.label,
            "role_description": role.description,
            "team": role.team.value,
            "mafia_teammates": teammates,
            "alive": seat in state.alive,
            "alive_seats": sorted(state.alive),
            "day": state.day,
            "phase": state.phase.value,
        }


def _phase(day: int, phase: Phase) -> NewEvent:
    return emit(PhaseChanged(day=day, phase=phase.value), Visibility.public())
