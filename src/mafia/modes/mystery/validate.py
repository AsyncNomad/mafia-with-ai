"""Static scenario validation (docs/modes/murder-mystery.md §3.3).

Everything a program can check is checked here, so the auditor and the blind solvers spend
their effort on what only a reader can judge. Each issue has a stable `code` (tests and the
architect's revision loop rely on it) and a `path` pointing at the offending field.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from mafia.modes.mystery.scenario import (
    AccusedIs,
    AccusedIsNot,
    ClueNotRevealed,
    ClueRevealed,
    HoldClue,
    Scenario,
    VotedFor,
)

CHARACTER_COUNT = 8
LOCATION_COUNT = 6
CLUES_PER_LOCATION_PER_ROUND = 2
MIN_RED_HERRING_TARGETS = 3

# (min, max) characters for long texts; outside the range is a warning, not an error.
TEXT_LENGTHS = {
    "narration.prologue": (300, 2500),
    "truth.summary": (400, 2000),
    "narration.truth_reveal": (800, 4000),
}


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class Issue:
    severity: Severity
    code: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.code} @ {self.path}: {self.message}"


def errors(issues: Iterable[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity is Severity.ERROR]


class _Collector:
    def __init__(self) -> None:
        self.issues: list[Issue] = []

    def error(self, code: str, path: str, message: str) -> None:
        self.issues.append(Issue(Severity.ERROR, code, path, message))

    def warn(self, code: str, path: str, message: str) -> None:
        self.issues.append(Issue(Severity.WARNING, code, path, message))


def validate(s: Scenario) -> list[Issue]:
    c = _Collector()
    char_ids = {ch.id for ch in s.characters}
    loc_ids = {loc.id for loc in s.world.locations}
    clue_ids = {k.id for k in s.clues}
    fact_ids = {f.id for f in s.facts}

    _counts_and_ids(s, c)
    _references(s, c, char_ids, loc_ids, clue_ids, fact_ids)
    _culprit(s, c, char_ids)
    _timeline(s, c, char_ids, loc_ids)
    _sheets_match_truth(s, c)
    _deduction_graph(s, c, clue_ids, fact_ids)
    _red_herrings(s, c)
    _unlocks(s, c)
    _narration(s, c, char_ids)
    _texts(s, c)
    return c.issues


def _dupes(values: Iterable[str]) -> list[str]:
    return [v for v, n in Counter(values).items() if n > 1]


def _counts_and_ids(s: Scenario, c: _Collector) -> None:
    if len(s.characters) != CHARACTER_COUNT:
        c.error(
            "character_count",
            "characters",
            f"{CHARACTER_COUNT}명이어야 합니다 ({len(s.characters)}명)",
        )
    if len(s.world.locations) != LOCATION_COUNT:
        c.error("location_count", "world.locations", f"장소는 {LOCATION_COUNT}곳이어야 합니다")
    for label, ids in (
        ("characters", [ch.id for ch in s.characters]),
        ("world.locations", [loc.id for loc in s.world.locations]),
        ("clues", [k.id for k in s.clues]),
        ("facts", [f.id for f in s.facts]),
    ):
        for dup in _dupes(ids):
            c.error("duplicate_id", label, f"id {dup}가 중복됩니다")
    for dup in _dupes(ch.name for ch in s.characters):
        c.error("duplicate_name", "characters", f"이름 {dup}가 중복됩니다")
    initials = Counter(ch.name[0] for ch in s.characters)
    for initial, n in initials.items():
        if n > 1:
            c.warn("similar_names", "characters", f"이름 첫 글자 '{initial}'가 {n}명에게 겹칩니다")

    per_slot: Counter[tuple[str, int]] = Counter()
    for k in s.clues:
        path = f"clues[{k.id}]"
        if k.round == 0:
            if k.starting_holder is None or k.location_id is not None:
                c.error("clue_placement", path, "시작 카드는 starting_holder만 가져야 합니다")
        else:
            if k.location_id is None or k.starting_holder is not None:
                c.error("clue_placement", path, "조사 카드는 location_id만 가져야 합니다")
            elif k.unlock_condition is None:
                per_slot[(k.location_id, k.round)] += 1
    for loc in s.world.locations:
        for rnd in (1, 2):
            n = per_slot[(loc.id, rnd)]
            if n != CLUES_PER_LOCATION_PER_ROUND:
                c.error(
                    "clues_per_location",
                    f"clues@{loc.id}/r{rnd}",
                    f"{loc.name} {rnd}라운드 기본 카드는 {CLUES_PER_LOCATION_PER_ROUND}장이어야 합니다 ({n}장)",
                )
    holders = Counter(k.starting_holder for k in s.clues if k.round == 0)
    for ch in s.characters:
        if holders[ch.id] != 1:
            c.error(
                "starting_hand",
                f"characters[{ch.id}]",
                f"시작 카드는 1장이어야 합니다 ({holders[ch.id]}장)",
            )


def _references(
    s: Scenario,
    c: _Collector,
    char_ids: set[str],
    loc_ids: set[str],
    clue_ids: set[str],
    fact_ids: set[str],
) -> None:
    def need(kind: str, ids: set[str], value: str | None, path: str) -> None:
        if value is not None and value not in ids:
            c.error("dangling_reference", path, f"존재하지 않는 {kind} {value}")

    for k in s.clues:
        p = f"clues[{k.id}]"
        need("장소", loc_ids, k.location_id, f"{p}.location_id")
        need("인물", char_ids, k.starting_holder, f"{p}.starting_holder")
        need("카드", clue_ids, k.unlock_condition, f"{p}.unlock_condition")
        for f in k.supports:
            need("사실", fact_ids, f, f"{p}.supports")
        for ch in k.points_to:
            need("인물", char_ids, ch, f"{p}.points_to")
        for r in k.red_herring_resolution:
            need("카드", clue_ids, r, f"{p}.red_herring_resolution")
    for f in s.facts:
        for k in f.proven_by:
            need("카드", clue_ids, k, f"facts[{f.id}].proven_by")
        need("인물", char_ids, f.excludes, f"facts[{f.id}].excludes")
    for ch in s.characters:
        p = f"characters[{ch.id}].sheet"
        sh = ch.sheet
        for k in sh.secret.exposed_by:
            need("카드", clue_ids, k, f"{p}.secret.exposed_by")
        for i, lie in enumerate(sh.lies):
            for k in lie.broken_by:
                need("카드", clue_ids, k, f"{p}.lies[{i}].broken_by")
        for i, kn in enumerate(sh.knowledge):
            need("사실", fact_ids, kn.fact_id, f"{p}.knowledge[{i}].fact_id")
        for other in sh.relationships:
            need("인물", char_ids, other, f"{p}.relationships")
            if other == ch.id:
                c.error(
                    "self_relationship", f"{p}.relationships", "자기 자신과의 관계는 쓸 수 없습니다"
                )
        for i, goal in enumerate(sh.goals):
            pr = goal.predicate
            gp = f"{p}.goals[{i}].predicate"
            if isinstance(pr, HoldClue | ClueRevealed | ClueNotRevealed):
                need("카드", clue_ids, pr.clue, gp)
            elif isinstance(pr, AccusedIs | AccusedIsNot | VotedFor):
                need("인물", char_ids, pr.character, gp)
        if sh.culprit_kit:
            for k in sh.culprit_kit.dangerous_clues:
                need("카드", clue_ids, k, f"{p}.culprit_kit.dangerous_clues")
    for d in s.truth.key_deductions:
        for ref in d.evidence:
            if ref not in clue_ids and ref not in fact_ids:
                c.error(
                    "dangling_reference",
                    f"truth.key_deductions[{d.step}]",
                    f"존재하지 않는 근거 {ref}",
                )


def _culprit(s: Scenario, c: _Collector, char_ids: set[str]) -> None:
    if s.truth.culprit_id not in char_ids:
        c.error("culprit_unknown", "truth.culprit_id", "범인이 등장인물 중에 없습니다")
        return
    for ch in s.characters:
        has_kit = ch.sheet.culprit_kit is not None
        if ch.id == s.truth.culprit_id and not has_kit:
            c.error(
                "culprit_kit",
                f"characters[{ch.id}].sheet.culprit_kit",
                "범인 시트에는 culprit_kit이 필요합니다",
            )
        if ch.id != s.truth.culprit_id and has_kit:
            c.error(
                "culprit_kit",
                f"characters[{ch.id}].sheet.culprit_kit",
                "범인이 아닌 인물에게 culprit_kit이 있습니다",
            )


def _timeline(s: Scenario, c: _Collector, char_ids: set[str], loc_ids: set[str]) -> None:
    slots = s.truth.time_slots
    for dup in _dupes(slots):
        c.error("duplicate_slot", "truth.time_slots", f"슬롯 {dup}가 중복됩니다")
    cells: dict[tuple[str, str], list[str]] = defaultdict(list)
    for e in s.truth.timeline:
        if e.slot not in slots:
            c.error("unknown_slot", "truth.timeline", f"정의되지 않은 슬롯 {e.slot}")
        if e.location_id not in loc_ids:
            c.error("dangling_reference", "truth.timeline", f"존재하지 않는 장소 {e.location_id}")
        cells[(e.slot, e.character_id)].append(e.location_id)
    for slot in slots:
        for ch in sorted(char_ids):
            got = cells.get((slot, ch), [])
            if len(got) != 1:
                c.error(
                    "timeline_gap" if not got else "timeline_conflict",
                    f"truth.timeline[{slot}][{ch}]",
                    "모든 슬롯마다 모든 인물의 위치가 정확히 하나 있어야 합니다",
                )
    where = {key: locs[0] for key, locs in cells.items() if len(locs) == 1}
    for e in s.truth.timeline:
        for w in e.witnessed_by:
            if where.get((e.slot, w)) != e.location_id:
                c.error(
                    "impossible_witness",
                    f"truth.timeline[{e.slot}][{e.character_id}]",
                    f"목격자 {w}는 그 시각 같은 장소에 없었습니다",
                )


def _sheets_match_truth(s: Scenario, c: _Collector) -> None:
    truth = {(e.slot, e.character_id): e.location_id for e in s.truth.timeline}
    for ch in s.characters:
        night = {n.slot: n.location_id for n in ch.sheet.night}
        for slot in s.truth.time_slots:
            path = f"characters[{ch.id}].sheet.night[{slot}]"
            if slot not in night:
                c.error("sheet_gap", path, "시트의 밤 행적에 이 슬롯이 없습니다")
            elif (slot, ch.id) in truth and night[slot] != truth[(slot, ch.id)]:
                c.error(
                    "sheet_contradicts_truth",
                    path,
                    "시트의 실제 행적이 진짜 시간표와 다릅니다 (남에게 할 거짓말은 lies에 쓰세요)",
                )


def _deduction_graph(s: Scenario, c: _Collector, clue_ids: set[str], fact_ids: set[str]) -> None:
    source = {k.id: (k.location_id or f"hand:{k.starting_holder}") for k in s.clues}
    decisive = [f for f in s.facts if f.decisive]
    if not decisive:
        c.error("no_decisive_fact", "facts", "범인 특정에 필요한 결정적 사실(decisive)이 없습니다")
    for f in decisive:
        sources = {source[k] for k in f.proven_by if k in source}
        if len(sources) < 2:
            c.error(
                "fragile_decisive_fact",
                f"facts[{f.id}]",
                "결정적 사실은 서로 다른 장소·소지자의 카드 2장 이상으로 증명돼야 합니다",
            )
        if f.excludes is not None:
            c.error(
                "decisive_excludes", f"facts[{f.id}]", "결정적 사실에는 excludes를 쓰지 않습니다"
            )
    innocents = [ch.id for ch in s.characters if ch.id != s.truth.culprit_id]
    excluded = {f.excludes for f in s.facts if f.excludes}
    for ch in innocents:
        if ch not in excluded:
            c.error(
                "not_excluded", f"characters[{ch}]", "이 무고한 인물을 배제하는 사실이 없습니다"
            )
    if s.truth.culprit_id in excluded:
        c.error("culprit_excluded", "facts", "범인을 배제하는 사실이 있습니다")
    cited = {ref for d in s.truth.key_deductions for ref in d.evidence}
    for f in decisive:
        if f.id not in cited and not set(f.proven_by) & cited:
            c.warn(
                "decisive_unused", f"facts[{f.id}]", "정답 풀이가 이 결정적 사실을 쓰지 않습니다"
            )
    steps = [d.step for d in s.truth.key_deductions]
    if steps != list(range(1, len(steps) + 1)):
        c.error("deduction_steps", "truth.key_deductions", "step은 1부터 순서대로여야 합니다")
    _ = clue_ids, fact_ids


def _red_herrings(s: Scenario, c: _Collector) -> None:
    innocents = {ch.id for ch in s.characters} - {s.truth.culprit_id}
    targets: set[str] = set()
    for k in s.clues:
        if not k.is_red_herring:
            continue
        if not k.red_herring_resolution:
            c.error(
                "unresolved_red_herring", f"clues[{k.id}]", "붉은 청어에는 해명 카드가 필요합니다"
            )
        if not k.points_to:
            c.error(
                "red_herring_target",
                f"clues[{k.id}]",
                "붉은 청어가 누구를 가리키는지 points_to에 쓰세요",
            )
        if s.truth.culprit_id in k.points_to:
            c.error("red_herring_target", f"clues[{k.id}]", "붉은 청어는 범인을 가리키면 안 됩니다")
        targets |= set(k.points_to) & innocents
    if len(targets) < MIN_RED_HERRING_TARGETS:
        c.error(
            "too_few_red_herrings",
            "clues",
            f"붉은 청어가 가리키는 무고한 인물이 {MIN_RED_HERRING_TARGETS}명 이상이어야 합니다 ({len(targets)}명)",
        )


def _unlocks(s: Scenario, c: _Collector) -> None:
    by_id = {k.id: k for k in s.clues}
    for k in s.clues:
        seen = {k.id}
        cur = k
        while cur.unlock_condition is not None:
            if cur.unlock_condition in seen:
                c.error("unlock_cycle", f"clues[{k.id}].unlock_condition", "잠금 조건이 순환합니다")
                break
            seen.add(cur.unlock_condition)
            nxt = by_id.get(cur.unlock_condition)
            if nxt is None:
                break
            cur = nxt
        prereq = by_id.get(k.unlock_condition) if k.unlock_condition else None
        if prereq is not None and prereq.round > k.round:
            c.error(
                "unlock_order",
                f"clues[{k.id}].unlock_condition",
                "잠금 해제 조건 카드가 이 카드보다 늦은 라운드에 나옵니다",
            )


def _narration(s: Scenario, c: _Collector, char_ids: set[str]) -> None:
    for field_name, mapping in (
        ("character_intros", s.narration.character_intros),
        ("epilogues", s.narration.epilogues),
    ):
        missing = char_ids - set(mapping)
        if missing:
            c.error("narration_missing", f"narration.{field_name}", f"빠진 인물: {sorted(missing)}")
    culprit = next((ch for ch in s.characters if ch.id == s.truth.culprit_id), None)
    if culprit is not None:
        public_texts = {
            "narration.prologue": s.narration.prologue,
            "narration.midgame_twist": s.narration.midgame_twist,
            "narration.accusation_intro": s.narration.accusation_intro,
            **{f"narration.round_intros[{i}]": t for i, t in enumerate(s.narration.round_intros)},
        }
        for path, text in public_texts.items():
            if "범인" in text and culprit.name in text:
                c.warn("possible_leak", path, "공개 텍스트에 범인의 이름과 '범인'이 함께 나옵니다")


def _texts(s: Scenario, c: _Collector) -> None:
    values = {
        "narration.prologue": s.narration.prologue,
        "truth.summary": s.truth.summary,
        "narration.truth_reveal": s.narration.truth_reveal,
    }
    for path, (lo, hi) in TEXT_LENGTHS.items():
        n = len(values[path])
        if not lo <= n <= hi:
            c.warn("text_length", path, f"{lo}~{hi}자를 권장합니다 ({n}자)")
