"""One table: a game, its AI seats and (optionally) one human seat.

This is the single entry point used by the MCP server (driver and seat tools) and by the hooks
(`HarnessState`). Every method opens no secrets beyond what its caller may see:
- driver methods render only the human's own view (or public events when nobody is human),
- seat methods render only that seat's engine context.
"""

from __future__ import annotations

import json
import random
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from mafia import paths
from mafia.app.human_input import (
    HELP_TEXT,
    Command,
    parse,
    resolve_label,
    resolve_seat,
    split_show,
)
from mafia.app.prep import PrepService, Stage, StartGame
from mafia.engine.core_events import ContextDelivered, GameEnded, SeatInfo
from mafia.engine.events import Event
from mafia.engine.requests import Action, ActionRejected, Answer, SeatRequest
from mafia.engine.runtime import Clock, SystemClock, rng_for
from mafia.engine.visibility import Viewer, can_see
from mafia.harness.identity import CallerKind, alias_names, classify
from mafia.harness.ports import HumanInputKind, HumanInputOutcome
from mafia.harness.tools import KNOWN_AGENTS
from mafia.modes.mystery.rules import cast_seats
from mafia.modes.mystery.scenario import Scenario
from mafia.personas import Persona, load_personas, pick
from mafia.service import GameService, Loaded
from mafia.store.db import connect
from mafia.store.events import EventStore
from mafia.store.harness import HarnessStore
from mafia.store.scenarios import ScenarioStore
from mafia.views.chat_md import render_events, render_human_request, render_role_card
from mafia.views.narration import name_lookup, phase_label
from mafia.views.postmortem import render_postmortem
from mafia.views.seat_text import render_seat_context

SEAT_COUNT = 8
# A background run with no stop event after this long is presumed dead (host closed mid-run).
RUN_STALE_AFTER_SECONDS = 30 * 60
_ALIAS_RNG = random.SystemRandom()  # unpredictable on purpose: aliases must not encode seats
PUBLIC_VIEWER = "public"
_LOG_LINES = 25


class NoActiveGame(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class Next:
    # wait: agents are still running in the background; end the turn and resume on notice
    type: Literal["wake", "human", "ended", "wait"]
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
    preparation: PrepService | None = None

    @classmethod
    def open_default(cls) -> TableApp:
        conn = connect(paths.game_db_path())
        clock = SystemClock()
        return cls(
            GameService(EventStore(conn), clock),
            HarnessStore(conn),
            clock,
            preparation=PrepService(ScenarioStore(conn), clock),
        )

    def prep(self) -> PrepService:
        if self.preparation is None:
            raise LookupError("이 테이블에는 시나리오 준비 기능이 없습니다.")
        return self.preparation

    def _new_mystery(
        self, *, human_name: str, watch: bool, seed: str | None, scenario_id: str | None = None
    ) -> AdvanceResult:
        store = self.prep().store
        record = store.get(scenario_id) if scenario_id else store.next_unplayed()
        if record is None or record.scenario is None:
            started = self.prepare_mystery("", StartGame(human_name=human_name, watch=watch))
            note = "라이브러리에 준비된 시나리오가 없어 새로 만듭니다. 시나리오가 완성되면 바로 게임이 시작됩니다."
            return AdvanceResult(f"{note}\n\n{started.display}", started.next)
        scenario = Scenario.model_validate(record.scenario)
        seed = seed or secrets.token_hex(16)
        cast = cast_seats(seed, [c.id for c in scenario.characters])
        human_seat = None if watch else rng_for(seed, "table").randint(1, SEAT_COUNT)
        seats = [
            SeatInfo(seat=s, name=scenario.character(cid).name) for s, cid in sorted(cast.items())
        ]
        self.service.new_game(
            mode="mystery",
            seats=seats,
            human_seats=[] if human_seat is None else [human_seat],
            seed=seed,
            config={"scenario": record.scenario},
        )
        store.mark_played(record.scenario_id)
        return self.advance()

    # --- murder-mystery preparation -----------------------------------------------------
    def prepare_mystery(
        self, request: str = "", start_game: StartGame | None = None
    ) -> AdvanceResult:
        self.prep().start(request, start_game)
        preparing = self._advance_preparation()
        assert preparing is not None
        return preparing

    def _advance_preparation(self) -> AdvanceResult | None:
        if self.preparation is None or self.preparation.active() is None:
            return None
        live = {a: self._running(a) for a in KNOWN_AGENTS}
        progress = self.preparation.advance(live)
        if progress.waiting:
            return AdvanceResult(progress.display, Next("wait"))
        if progress.agents:
            return AdvanceResult(
                progress.display, Next("wake", progress.agents, parallel=len(progress.agents) > 1)
            )
        if progress.stage is Stage.VERIFIED and progress.start_game is not None:
            game = self._new_mystery(
                human_name=progress.start_game.human_name,
                watch=progress.start_game.watch,
                seed=None,
                scenario_id=progress.scenario_id,
            )
            return AdvanceResult(f"{progress.display}\n\n{game.display}", game.next)
        tail = ""
        if progress.stage is Stage.VERIFIED:
            tail = "\n\n시나리오가 라이브러리에 저장되었습니다. `/mafia new mystery`로 시작하세요."
        return AdvanceResult(progress.display + tail, Next("ended"))

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
        if mode == "mystery":
            return self._new_mystery(human_name=human_name, watch=watch, seed=seed)
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

    def _game_in_progress(self) -> bool:
        game_id = self.service.store.active_game()
        return game_id is not None and not self.service.load(game_id).state.core.ended

    def advance(self) -> AdvanceResult:
        # A game in progress always comes first; preparation runs only between games.
        if not self._game_in_progress():
            preparing = self._advance_preparation()
            if preparing is not None:
                return preparing
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
                public = [
                    f"seat-{r.seat}"
                    for r in sorted(core.open_requests.values(), key=lambda r: r.seat)
                    if not r.covert
                ]
                hidden = [
                    alias
                    for alias, (_seat, done) in sorted(self._sync_aliases(game_id, loaded).items())
                    if not done
                ]
                agents = tuple(a for a in public + hidden if self._running(a) == 0)
                # No agents to wake: every pending seat is already running in the background.
                nxt = Next("wake", agents, parallel=len(agents) > 1) if agents else Next("wait")
        return AdvanceResult("\n\n".join(p for p in parts if p).strip(), nxt)

    def status(self) -> str:
        loaded = self.service.load(self.game_id())
        core = loaded.state.core
        names = name_lookup(core.seats)
        alive = getattr(loaded.state.mode, "alive", frozenset(core.seat_numbers))
        return (
            f"{core.day}일차 · {phase_label(core.mode, core.phase)}"
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
        reveal: Sequence[str] | None = None,
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
                reveal=tuple(reveal or ()),
                private_note=private_note,
                suspicion={int(k): int(v) for k, v in suspicion.items()} if suspicion else None,
            )
            self.service.submit(game_id, seat, request.request_id, action)
        except (ActionRejected, ValueError) as exc:
            return f"거부됨: {exc} 다시 제출하세요."
        return "접수되었습니다. 최종 응답으로 '제출 완료'라고만 쓰고 끝내세요."

    # --- HarnessState (hooks) -----------------------------------------------------------
    def _running(self, agent_type: str) -> int:
        return self.harness.running(agent_type, self.clock.now(), RUN_STALE_AFTER_SECONDS)

    # --- covert turns: anonymous aliases ------------------------------------------------
    def _aliases(self, game_id: str) -> dict[str, tuple[int, bool]]:
        return {alias: (seat, done) for alias, seat, _batch, done in self.harness.aliases(game_id)}

    def _sync_aliases(self, game_id: str, loaded: Loaded) -> dict[str, tuple[int, bool]]:
        """While covert requests are open, every living AI seat gets a random alias, so neither
        the driver nor the UI can tell who acts (or how many act) at night."""
        core = loaded.state.core
        covert = sorted(
            r.request_id
            for r in core.open_requests.values()
            if r.covert and r.seat not in core.human_seats
        )
        batch = ",".join(covert)
        current = self.harness.aliases(game_id)
        if current and all(b == batch for _a, _s, b, _d in current):
            return self._aliases(game_id)
        if not covert:
            if current:
                self.harness.replace_aliases(game_id, "", {})
            return {}
        alive: frozenset[int] = getattr(loaded.state.mode, "alive", frozenset(core.seat_numbers))
        seats = sorted(s for s in alive if s not in core.human_seats)
        names = _ALIAS_RNG.sample(list(alias_names()), len(seats))
        self.harness.replace_aliases(game_id, batch, dict(zip(names, seats, strict=True)))
        return self._aliases(game_id)

    def seat_of(self, agent_type: str) -> int | None:
        caller = classify(agent_type, KNOWN_AGENTS)
        if caller.kind is CallerKind.SEAT:
            return caller.seat
        if caller.kind is CallerKind.ALIAS:
            try:
                entry = self._aliases(self.game_id()).get(agent_type)
            except NoActiveGame:
                return None
            return None if entry is None else entry[0]
        return None

    def _wanted_runs(self, agent_type: str) -> int:
        caller = classify(agent_type, KNOWN_AGENTS)
        if caller.kind is CallerKind.AGENT:
            return 0 if self.preparation is None else self.preparation.wanted_runs(agent_type)
        try:
            game_id = self.game_id()
        except NoActiveGame:
            return 0
        if caller.kind is CallerKind.ALIAS:
            entry = self._aliases(game_id).get(agent_type)
            return 0 if entry is None or entry[1] else 1  # dummies are woken once too
        if caller.kind is not CallerKind.SEAT or caller.seat is None:
            return 0
        loaded = self.service.load(game_id)
        if caller.seat in loaded.state.core.human_seats:
            return 0
        request = self._request_for(loaded, caller.seat)
        # A covert request is never reachable by seat name: probing seat-N must reveal nothing.
        return int(request is not None and not request.covert)

    def needs_submission(self, agent_type: str) -> bool:
        caller = classify(agent_type, KNOWN_AGENTS)
        if caller.kind is CallerKind.ALIAS:
            seat = self.seat_of(agent_type)
            if seat is None:
                return False
            request = self._request_for(self.service.load(self.game_id()), seat)
            return request is not None and request.covert
        return self._wanted_runs(agent_type) > 0

    def is_awaiting(self, agent_type: str) -> bool:
        # Parallel spawns of one type (solvers) may start before all spawn checks ran, so the
        # rule is "fewer runs in progress than the stage can use", not "none in progress".
        return self._running(agent_type) < self._wanted_runs(agent_type)

    def record_agent_started(self, agent_id: str, agent_type: str) -> None:
        self.harness.agent_started(
            agent_id or f"anon-{agent_type}-{self.clock.now()}", agent_type, self.clock.now()
        )

    def record_agent_finished(self, agent_id: str, agent_type: str) -> None:
        if agent_id:
            self.harness.agent_stopped(agent_id, self.clock.now())
        try:
            game_id = self.game_id()
        except NoActiveGame:
            return
        seat = self.seat_of(agent_type)
        if classify(agent_type, KNOWN_AGENTS).kind is CallerKind.ALIAS:
            self.harness.mark_alias_done(game_id, agent_type)
        if seat is None:
            return
        request = self._request_for(self.service.load(game_id), seat)
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
            case Command.ASK:
                return HumanInputOutcome(HumanInputKind.PASSTHROUGH)
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
                    # Answered by the engine, never by the driver: nothing to leak, nothing to argue.
                    return _info(_not_your_turn(core.phase))
                return self._submit_human(game_id, human, request, Action(speech=parsed.argument))
            case _:
                pass

        if request is None:
            return _rejected("지금은 당신의 차례가 아닙니다.")
        match parsed.command:
            case Command.PASS:
                action = Action()
            case Command.SHOW:
                if request.answer is not Answer.SPEECH:
                    return _rejected("카드는 발언 차례에만 공개할 수 있습니다.")
                cards, words = split_show(parsed.argument)
                if not cards:
                    return _rejected(
                        "공개할 카드 id를 적어 주세요. 예: `:show k12 이 카드를 보세요`"
                    )
                action = Action(speech=words or "이 카드를 보세요.", reveal=cards)
            case Command.VOTE if request.option_labels:
                target = resolve_label(parsed.argument, request.options, request.option_labels)
                if target is None:
                    return _rejected(f"'{parsed.argument}'에 해당하는 선택지를 찾지 못했습니다.")
                action = Action(target=target)
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
        title = (
            "🕯 머더 미스터리를 시작합니다"
            if core.mode == "mystery"
            else "🔪 마피아 게임을 시작합니다"
        )
        lines = [f"## {title}"]
        if human is None:
            lines.append("관전 모드입니다. AI 8명이 플레이합니다.")
        else:
            brief = loaded.engine.mode.seat_brief(loaded.state.mode, core, human)
            role = "역을 맡았습니다" if core.mode == "mystery" else "좌석입니다"
            lines.append(f"당신은 **{name_lookup(core.seats)(human)}** {role}.")
            lines.append(render_role_card(brief, core.seats))
            lines.append("명령어는 `:help`")
        return "\n\n".join(lines)


_TOOL_FOR: Mapping[Answer, str] = {
    Answer.SPEECH: "submit_speech",
    Answer.TARGET: "submit_target",
    Answer.APPROVAL: "submit_approval",
}


_NIGHT_PHASES = frozenset({"mafia_chat", "night_actions"})


def _not_your_turn(phase: str) -> str:
    if phase in _NIGHT_PHASES:
        return "🌙 밤입니다. 지금은 아무도 말할 수 없습니다. 밤이 지나면 무슨 일이 있었는지 알려 드립니다."
    return "지금은 당신의 발언 차례가 아닙니다. 차례가 오면 알려 드립니다. 진행자에게 물을 땐 `:ask 질문`"


def _info(message: str) -> HumanInputOutcome:
    return HumanInputOutcome(HumanInputKind.INFO, message)


def _rejected(message: str) -> HumanInputOutcome:
    return HumanInputOutcome(HumanInputKind.REJECTED, message)
