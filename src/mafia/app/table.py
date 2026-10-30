"""One table: a game, its AI seats and (optionally) one human seat.

This is the single entry point used by the MCP server (driver and seat tools) and by the hooks
(`HarnessState`). Every method opens no secrets beyond what its caller may see:
- driver methods render only the human's own view (or public events when nobody is human),
- seat methods render only that seat's engine context.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from mafia import paths
from mafia.app.human_input import HELP_TEXT, Command, parse, resolve_seat
from mafia.engine.core_events import ContextDelivered, GameEnded, SeatInfo
from mafia.engine.events import Event
from mafia.engine.requests import Action, ActionRejected, Answer, SeatRequest
from mafia.engine.runtime import Clock, SystemClock, rng_for
from mafia.engine.visibility import Viewer, can_see
from mafia.harness.identity import CallerKind, classify
from mafia.harness.ports import HumanInputKind, HumanInputOutcome
from mafia.harness.tools import KNOWN_AGENTS
from mafia.personas import Persona, load_personas, pick
from mafia.service import GameService, Loaded
from mafia.store.db import connect
from mafia.store.events import EventStore
from mafia.store.harness import HarnessStore
from mafia.views.chat_md import render_events, render_human_request, render_role_card
from mafia.views.narration import PHASE_LABELS, name_lookup
from mafia.views.postmortem import render_postmortem
from mafia.views.seat_text import render_seat_context

SEAT_COUNT = 8
PUBLIC_VIEWER = "public"
_LOG_LINES = 25


class NoActiveGame(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class Next:
    type: Literal["wake", "human", "ended"]
    agents: tuple[str, ...] = ()
    parallel: bool = False


@dataclass(frozen=True, slots=True)
class AdvanceResult:
    display: str
    next: Next

    def to_text(self) -> str:
        payload = {
            "type": self.next.type,
            "agents": list(self.next.agents),
            "parallel": self.next.parallel,
        }
        return f"<display>\n{self.display}\n</display>\n<next>{json.dumps(payload)}</next>"


@dataclass
class TableApp:
    service: GameService
    harness: HarnessStore
    clock: Clock = field(default_factory=SystemClock)
    personas: tuple[Persona, ...] | None = None

    @classmethod
    def open_default(cls) -> TableApp:
        conn = connect(paths.game_db_path())
        clock = SystemClock()
        return cls(GameService(EventStore(conn), clock), HarnessStore(conn), clock)

    # --- driver (main session) ----------------------------------------------------------
    def new_game(
        self,
        *,
        mode: str = "original",
        human_name: str = "나",
        watch: bool = False,
        seed: str | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> AdvanceResult:
        seed = seed or secrets.token_hex(16)
        rng = rng_for(seed, "table")
        human_seat = None if watch else rng.randint(1, SEAT_COUNT)
        pool = self.personas or load_personas()
        ai_seats = [s for s in range(1, SEAT_COUNT + 1) if s != human_seat]
        chosen = pick(pool, len(ai_seats), rng, avoid="" if watch else human_name)
        persona_of = dict(zip(ai_seats, chosen, strict=True))
        seats = [
            SeatInfo(seat=s, name=human_name if s == human_seat else persona_of[s].name)
            for s in range(1, SEAT_COUNT + 1)
        ]
        self.service.new_game(
            mode=mode,
            seats=seats,
            human_seats=[] if human_seat is None else [human_seat],
            seed=seed,
            config=config,
            profiles={s: p.model_dump(mode="json") for s, p in persona_of.items()},
        )
        return self.advance()

    def advance(self) -> AdvanceResult:
        game_id = self.game_id()
        self.service.advance(game_id)
        self._expire_abandoned(game_id)
        loaded = self.service.load(game_id)
        core = loaded.state.core
        human = self._human_seat(loaded)

        viewer_key = f"seat-{human}" if human is not None else PUBLIC_VIEWER
        cursor = self.harness.cursor(game_id, viewer_key)
        seen = [e for e in self._visible_to(loaded, human) if e.seq > cursor]
        parts: list[str] = []
        if cursor == 0:
            parts.append(self._intro(loaded, human))
        rendered = render_events(seen, core.seats, core.mode)
        if rendered:
            parts.append(rendered)
        self.harness.set_cursor(game_id, viewer_key, core.last_seq)

        if core.ended:
            nxt = Next("ended")
            if any(e.kind == GameEnded.KIND for e in seen):
                review = render_postmortem(loaded.log, core.seats, core.mode)
                if review:
                    parts.append(review)
            parts.append("**게임이 끝났습니다.** 새 게임은 `/mafia new`")
        else:
            human_request = self._request_for(loaded, human)
            if human_request is not None:
                parts.append(render_human_request(human_request, core.seats))
                nxt = Next("human")
            else:
                agents = tuple(
                    f"seat-{r.seat}"
                    for r in sorted(core.open_requests.values(), key=lambda r: r.seat)
                )
                nxt = Next("wake", agents, parallel=len(agents) > 1)
        return AdvanceResult("\n\n".join(p for p in parts if p).strip(), nxt)

    def status(self) -> str:
        loaded = self.service.load(self.game_id())
        core = loaded.state.core
        names = name_lookup(core.seats)
        alive = getattr(loaded.state.mode, "alive", frozenset(core.seat_numbers))
        return (
            f"{core.day}일차 · {PHASE_LABELS.get(core.phase, core.phase)}"
            f"{' · 종료' if core.ended else ''}\n"
            f"생존자: {', '.join(names(s) for s in sorted(alive))}"
        )

    # --- seats (AI subagents) -----------------------------------------------------------
    def seat_turn(self, seat: int) -> str:
        game_id = self.game_id()
        loaded = self.service.load(game_id)
        if seat in loaded.state.core.human_seats:
            return "이 좌석은 사람 플레이어의 좌석입니다."
        ctx = self.service.context(game_id, seat)
        return render_seat_context(ctx, loaded.state.core.mode)

    def seat_submit(
        self,
        seat: int,
        answer: Answer,
        *,
        speech: str | None = None,
        target: int | None = None,
        approve: bool | None = None,
        private_note: str | None = None,
        suspicion: Mapping[str, int] | Mapping[int, int] | None = None,
    ) -> str:
        game_id = self.game_id()
        loaded = self.service.load(game_id)
        if seat in loaded.state.core.human_seats:
            return "거부됨: 이 좌석은 사람 플레이어의 좌석입니다."
        request = self._request_for(loaded, seat)
        if request is None:
            return "지금은 당신의 차례가 아닙니다. 아무것도 제출하지 말고 끝내세요."
        if request.answer is not answer:
            return f"거부됨: 지금 요청에는 {_TOOL_FOR[request.answer]}를 써야 합니다."
        try:
            action = Action(
                speech=speech,
                target=target,
                approve=approve,
                private_note=private_note,
                suspicion={int(k): int(v) for k, v in suspicion.items()} if suspicion else None,
            )
            self.service.submit(game_id, seat, request.request_id, action)
        except (ActionRejected, ValueError) as exc:
            return f"거부됨: {exc} 다시 제출하세요."
        return "접수되었습니다. 최종 응답으로 '제출 완료'라고만 쓰고 끝내세요."

    # --- HarnessState (hooks) -----------------------------------------------------------
    def is_awaiting(self, agent_type: str) -> bool:
        caller = classify(agent_type, KNOWN_AGENTS)
        if caller.kind is not CallerKind.SEAT or caller.seat is None:
            return False  # mystery agents arrive with the murder-mystery mode (M4)
        try:
            loaded = self.service.load(self.game_id())
        except NoActiveGame:
            return False
        if caller.seat in loaded.state.core.human_seats:
            return False
        return self._request_for(loaded, caller.seat) is not None

    def record_agent_finished(self, agent_type: str) -> None:
        caller = classify(agent_type, KNOWN_AGENTS)
        if caller.kind is not CallerKind.SEAT or caller.seat is None:
            return
        try:
            game_id = self.game_id()
        except NoActiveGame:
            return
        request = self._request_for(self.service.load(game_id), caller.seat)
        if request is not None:
            self.harness.record_agent_finished(
                game_id, request.request_id, agent_type, self.clock.now()
            )

    def handle_human_input(self, text: str) -> HumanInputOutcome:
        parsed = parse(text)
        if parsed is None:
            return HumanInputOutcome(HumanInputKind.PASSTHROUGH)
        try:
            game_id = self.game_id()
        except NoActiveGame:
            return HumanInputOutcome(HumanInputKind.PASSTHROUGH)
        loaded = self.service.load(game_id)
        human = self._human_seat(loaded)
        if human is None or loaded.state.core.ended:
            return HumanInputOutcome(HumanInputKind.PASSTHROUGH)
        core = loaded.state.core
        request = self._request_for(loaded, human)

        match parsed.command:
            case Command.HELP:
                return _info(HELP_TEXT)
            case Command.WHO:
                return _info(self.status())
            case Command.ROLE:
                brief = self.service.load(game_id).engine.mode.seat_brief(
                    loaded.state.mode, core, human
                )
                return _info(render_role_card(brief, core.seats))
            case Command.LOG:
                lines = render_events(self._visible_to(loaded, human), core.seats, core.mode)
                return _info("\n".join(lines.splitlines()[-_LOG_LINES:]))
            case Command.UNKNOWN:
                return _rejected("알 수 없는 명령입니다. `:help`로 명령을 확인하세요.")
            case Command.SPEECH:
                if request is None or request.answer is not Answer.SPEECH:
                    return HumanInputOutcome(HumanInputKind.PASSTHROUGH)
                return self._submit_human(game_id, human, request, Action(speech=parsed.argument))
            case _:
                pass

        if request is None:
            return _rejected("지금은 당신의 차례가 아닙니다.")
        match parsed.command:
            case Command.PASS:
                action = Action()
            case Command.VOTE:
                if request.answer is not Answer.TARGET:
                    return _rejected("지금은 대상을 고르는 차례가 아닙니다.")
                target = resolve_seat(parsed.argument, core.seats)
                if target is None:
                    return _rejected(f"'{parsed.argument}'에 해당하는 사람을 찾지 못했습니다.")
                action = Action(target=target)
            case Command.YES | Command.NO:
                if request.answer is not Answer.APPROVAL:
                    return _rejected("지금은 찬반 투표 차례가 아닙니다.")
                action = Action(approve=parsed.command is Command.YES)
            case _:
                return _rejected("알 수 없는 명령입니다. `:help`로 명령을 확인하세요.")
        return self._submit_human(game_id, human, request, action)

    # --- internals ----------------------------------------------------------------------
    def _submit_human(
        self, game_id: str, seat: int, request: SeatRequest, action: Action
    ) -> HumanInputOutcome:
        try:
            self.service.submit(game_id, seat, request.request_id, action)
        except ActionRejected as exc:
            return _rejected(str(exc))
        return HumanInputOutcome(
            HumanInputKind.SUBMITTED,
            "사람 플레이어가 자기 차례 행동을 이미 게임 엔진에 제출했습니다(원문 그대로 처리됨). "
            "이 메시지에 답하거나 내용을 해석·전달하지 말고, 곧바로 mcp__mafia__advance를 호출해 진행하세요.",
        )

    def _expire_abandoned(self, game_id: str) -> None:
        """AI requests whose subagent already finished without submitting get the fallback."""
        for _ in range(SEAT_COUNT * 4):
            loaded = self.service.load(game_id)
            finished = self.harness.finished_requests(game_id)
            stale = [
                r
                for r in loaded.state.core.open_requests.values()
                if r.request_id in finished and r.seat not in loaded.state.core.human_seats
            ]
            if not stale:
                return
            for request in stale:
                self.service.expire(game_id, request.request_id)

    def game_id(self) -> str:
        """The active game. Raises `NoActiveGame` before the first `new_game`."""
        game_id = self.service.store.active_game()
        if game_id is None:
            raise NoActiveGame("진행 중인 게임이 없습니다. /mafia new 로 시작하세요.")
        return game_id

    @staticmethod
    def _human_seat(loaded: Loaded) -> int | None:
        humans = sorted(loaded.state.core.human_seats)
        return humans[0] if humans else None

    @staticmethod
    def _request_for(loaded: Loaded, seat: int | None) -> SeatRequest | None:
        if seat is None:
            return None
        return next((r for r in loaded.state.core.open_requests.values() if r.seat == seat), None)

    @staticmethod
    def _visible_to(loaded: Loaded, seat: int | None) -> Sequence[Event]:
        if seat is None:
            viewer = Viewer()  # public only
        else:
            viewer = loaded.engine.mode.viewer(loaded.state.mode, loaded.state.core, seat)
        return [
            e
            for e in loaded.log
            if e.kind != ContextDelivered.KIND and can_see(viewer, e.visibility)
        ]

    def _intro(self, loaded: Loaded, human: int | None) -> str:
        core = loaded.state.core
        lines = ["## 🔪 마피아 게임을 시작합니다"]
        if human is None:
            lines.append("관전 모드입니다. AI 8명이 플레이합니다.")
        else:
            brief = loaded.engine.mode.seat_brief(loaded.state.mode, core, human)
            lines.append(f"당신은 **{name_lookup(core.seats)(human)}** 좌석입니다.")
            lines.append(render_role_card(brief, core.seats))
            lines.append("명령어는 `:help`")
        return "\n\n".join(lines)


_TOOL_FOR: Mapping[Answer, str] = {
    Answer.SPEECH: "submit_speech",
    Answer.TARGET: "submit_target",
    Answer.APPROVAL: "submit_approval",
}


def _info(message: str) -> HumanInputOutcome:
    return HumanInputOutcome(HumanInputKind.INFO, message)


def _rejected(message: str) -> HumanInputOutcome:
    return HumanInputOutcome(HumanInputKind.REJECTED, message)
