# mafia-with-ai 설계

사람 1명과 AI 7명이 VSCode의 Claude Code 채팅창에서 하는 8인 추리 게임.
AI 플레이어는 Claude Code 서브에이전트이며, 서로의 정보를 절대 공유하지 않고 각자 독립된 인격으로 플레이한다.
격리는 프롬프트에 대한 신뢰가 아니라 **하네스(훅·권한·MCP)로 강제**한다.

- 모드별 규칙: [modes/original.md](modes/original.md), [modes/murder-mystery.md](modes/murder-mystery.md)

---

## 1. 확정 사항

| 항목 | 결정 |
|---|---|
| 인원 | **8인 고정** (사람 1 + AI 7). AI만 8명인 관전 모드도 같은 구조 |
| 플레이 화면 | VSCode Claude Code 확장 채팅 |
| god 로그 | VSCode 통합 터미널에서 `mafia tail --god` |
| 모드 | **오리지널 모드**(마피아·경찰·의사·시민), **머더 미스터리 모드**(사전에 생성·검증한 시나리오로 진행) |
| 모델 호출 | Claude Code가 담당 (Anthropic API 키 불필요, 구독 사용량 소모) |

## 2. 핵심 원칙

1. **사회자는 코드다.** 진실(역할, 생존, 밤 행동, 시나리오의 진상)은 엔진과 SQLite에만 있다. 어떤 LLM 세션도 자기 몫 이상을 받지 않는다.
2. **기억은 엔진이 가진다.** 좌석 서브에이전트는 매 차례 새로 뜨고, `my_turn`으로 자기 정체·인격·본 것·지난 메모를 받아 간다. 무엇을 보여 줬는지는 엔진이 전부 통제하고 기록한다.
3. **진실을 아는 자는 말하지 않고, 말하는 자는 진실을 모른다.** 진상을 아는 에이전트(시나리오 설계자·감수자)의 출력은 엔진으로만 간다. 화면에 말을 내보내는 에이전트(메인 세션·내레이터)는 공개 정보만 받는다.
4. **격리는 강제하고 검증한다.** 훅이 신원을 주입하고 호출을 차단하며 결과를 봉인한다. `mafia audit`가 사후에 기계적으로 확인한다.

## 3. 아키텍처

| Claude Code 기능 | 게임에서 맡는 일 |
|---|---|
| 메인 채팅 세션 | 진행 드라이버이자 화면. 엔진이 지시한 대로 서브에이전트를 깨우고, 엔진이 만든 사람 시점 마크다운을 그대로 출력한다. 비밀을 모른다 |
| 서브에이전트 `seat-1`~`seat-8` | 플레이어. 차례마다 새로 떠서 행동 하나를 제출하고 끝난다 |
| 서브에이전트 `mystery-*` | 머더 미스터리 시나리오 설계·감수·블라인드 풀이·내레이션 |
| MCP 서버 `mafia` (stdio) | 엔진 창구. 호출 주체별(메인 / 좌석 / mystery-*)로 도구 집합을 분리한다 |
| SQLite (WAL) | 진실의 원천. MCP 서버, 훅 프로세스, CLI가 함께 쓴다 |
| 훅 | 격리 강제: 신원 주입, 호출 차단, 결과 봉인, 사람 입력 원문 기록 |
| 스킬 `/mafia` | 메인 세션의 진행 프로토콜 (`new original`, `new mystery`, `continue`, `status`) |
| 통합 터미널 | `mafia tail --god`(전체), `--view seat-N`(특정 좌석 시점) |

### 한 차례의 흐름

```
사람 ──입력──▶ [UserPromptSubmit 훅] ─원문 그대로─▶ SQLite
메인 ──mcp__mafia__advance()──▶ 엔진
     ◀── {public_log_md, next: wake [3,5] parallel | human | game_over}
next = wake:
  메인 ──Agent(seat-3)──▶ [PreToolUse/Agent: 대기 중인 좌석인지 확인, 프롬프트를 고정 문구로 교체]
     seat-3 ─my_turn()──▶ [PreToolUse/mcp: agent_type=seat-3 → seat=3 강제 주입]
            ◀── 이 좌석에게만 보이는 컨텍스트
     seat-3 ─submit_*()──▶ 엔진 검증·기록
     [SubagentStop: 제출 안 했으면 block]
  ◀── [PostToolUse/Agent: 결과를 "seat-3 제출 완료"로 봉인]
메인 ──advance() 반복 … 사람 차례가 오면 출력하고 대기
```

투표·밤 행동·조사 선택은 병렬(한 메시지에 Agent 여러 개), 토론은 순차로 진행한다.

## 4. 정보 격리

| # | 막는 것 | 방법 |
|---|---|---|
| 1 | 좌석 사칭 | PreToolUse(`mcp__mafia__.*`)가 `agent_type`에서 좌석 번호를 꺼내 `updatedInput`으로 `seat` 인자를 덮어씀 |
| 2 | 메인의 비밀 도구 호출 | 같은 훅: `agent_type` 없음 = 메인 → 공개 도구만 허용 |
| 3 | 도구 패밀리 침범 | 같은 훅: `seat-*`는 좌석 도구만, `mystery-architect`는 설계 도구만 쓰는 식으로 agent_type과 도구를 대응시킴 |
| 4 | 메인이 서브에이전트에게 질문 끼워 넣기 | PreToolUse(`Agent`): 엔진이 지금 기다리는 에이전트가 아니면 deny하고, 프롬프트는 고정 문구로 교체. `SendMessage`는 deny |
| 5 | 서브에이전트 최종 응답으로 누출 | 생성 시 `run_in_background: false`로 동기 강제 → PostToolUse(`Agent`)가 `updatedToolOutput`으로 봉인(키 구성 유지). 백그라운드 결과 알림(`<task-notification>`)은 UserPromptSubmit에서 차단. SubagentStop은 제출 여부 확인 ([harness-contract.md](harness-contract.md) 함정 1·2) |
| 6 | 파일·로그 엿보기 | 서브에이전트 `tools`는 MCP 도구만 (Read/Bash/Agent 없음). `memory` 필드는 쓰지 않음(쓰면 Read/Write가 자동으로 켜짐) |
| 7 | 메인의 god 데이터 접근 | `play/` 권한에서 Read·Bash·Write·Edit·Glob·Grep·Web 계열 deny. `runs/`는 `play/` 바깥 |
| 8 | 사람 발언 왜곡 | UserPromptSubmit 훅이 원문을 직접 기록. 메인은 전달하지 않음 |
| 9 | 사회자 사칭 | 엔진이 발언을 `[이름] "…"`로 감싸고 시스템 표지 흉내를 무력화 |
| 10 | 사후 검증 | `mafia audit`: 전달된 컨텍스트(deliveries 테이블)를 가시성 규칙과 대조. 서브에이전트 전사본에서 허용 외 도구 호출이 없는지 검사 |

**막지 못하는 것:** 사람이 VSCode 에이전트 맵에서 서브에이전트를 클릭하면 내부(`my_turn` 결과)가 보인다. `~/.claude/projects/.../subagents/*.jsonl`에도 남는다. 즉 AI 사이의 격리는 강제되지만, 사람이 일부러 엿보는 것은 god 로그와 같은 자율 영역이다.

## 5. 워크스페이스와 디렉토리

개발용 루트와 플레이용 `play/`를 분리한다. 개발 세션에는 Bash가 필요하고, 플레이 세션은 전부 잠가야 하기 때문이다. 플레이할 때는 VSCode에서 `play/` 폴더를 따로 연다.

```
mafia-with-ai/
├── pyproject.toml
├── CLAUDE.md                     # 개발 규칙(격리 불변식). play/ 세션에도 상위 CLAUDE.md로 로드되므로 짧게
├── docs/
│   ├── design.md                 # 이 문서
│   ├── harness-contract.md       # (M0) 훅·권한이 보장하는 것, 실측 결과
│   └── modes/
│       ├── original.md
│       └── murder-mystery.md
├── config/
│   ├── modes/original.toml       # 역할 구성, 라운드 수, 좌석별 모델·effort
│   ├── modes/mystery.toml        # 파이프라인 기준값, 반복 한도, 솔버 구성
│   ├── mystery/brief_pools.toml  # 배경·톤·반전 유형·금지 클리셰 풀
│   └── personas/*.yaml           # 오리지널 모드 인격 풀 (게임마다 랜덤 배정, 역할과 독립)
├── src/mafia/
│   ├── engine/                   # 모드 공통: 페이즈 머신, 좌석 요청(pending), 투표, 토론 순서
│   ├── modes/
│   │   ├── base.py               # Mode 프로토콜: setup / phases / 가시성 / 좌석 컨텍스트 / 승패
│   │   ├── original/             # rules.py, roles/ (mafia, police, doctor, citizen)
│   │   └── mystery/              # rules.py, scenario.py(스키마), validate.py, brief.py, pipeline.py, scoring.py
│   ├── store/                    # schema.sql, repo.py — games, events, seats, pending_requests, deliveries
│   ├── views/                    # visibility.py, seat_context.py, chat_md.py(사람 시점), term.py(god 로그)
│   ├── mcp_server/               # server.py, public_tools.py, seat_tools.py, mystery_tools.py
│   ├── runners/                  # SeatRunner 포트: AI 한 차례를 "어떻게" 실행하나 (§11)
│   │   ├── base.py               #   run(request) → 제출 완료 여부. 엔진은 이것만 앎
│   │   ├── claude_subagent.py    #   채팅 세션이 Agent로 띄움 (기본, 훅 강제)
│   │   └── fake.py               #   테스트용
│   ├── hosts/claude_code/        # 호스트 어댑터: 훅 구현 + agents/settings 템플릿 생성
│   │   ├── hooks/                #   guard_mcp, guard_spawn, guard_all, seal_agent, enforce_submit, capture_human
│   │   └── templates/            #   seat.md.j2, settings.json.j2, SKILL.md.j2
│   ├── setup/                    # config → play/ 워크스페이스 생성(gen), 환경 진단(doctor)
│   └── cli.py                    # typer: setup / tail / replay / audit / scenarios / reset
│   ├── prompts/agents/           # 에이전트 프롬프트 정본 (호스트 중립 md, D12)
├── play/                         # ★ 테이블: VSCode로 이 폴더를 연다. 전부 `mafia setup` 생성물, gitignore
│   ├── CLAUDE.md                 # "너는 진행 드라이버다"
│   ├── .mcp.json
│   └── .claude/
│       ├── settings.json         # 권한 잠금 + 훅 등록
│       ├── skills/mafia/SKILL.md
│       └── agents/               # seat-1…8, mystery-architect/auditor/solver/narrator
├── runs/                         # gitignore, play/ 바깥
│   ├── games/<id>/game.db
│   └── scenarios/<id>/           # 검증 통과한 시나리오 라이브러리
└── tests/
    ├── unit/                     # 엔진·가시성·훅 로직·시나리오 검증기 (네트워크 없음)
    ├── sim/                      # FakeSeat 드라이버로 수백 판 자동 진행 + audit
    └── contract/                 # 하네스 계약 테스트 (실제 Claude Code로 수동 실행)
```

## 6. 기술 스택

| 영역 | 선택 |
|---|---|
| 언어 | Python 3.12, `uv` |
| MCP 서버 | 공식 Python `mcp` SDK (stdio) |
| 저장소 | `sqlite3` (WAL) |
| 스키마 | `pydantic` v2. 제출 도구 입력 스키마와 시나리오 스키마를 겸함 |
| god 로그 / CLI | `rich`, `typer` |
| 테스트 | `pytest`. FakeSeat 드라이버로 네트워크 없이 엔진·격리를 대량 시뮬레이션 |

## 7. 개발 단계

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| 상태 (2026-10-30) | M0·M1 완료, M2 완료(CLI 헤드리스 E2E 통과, VSCode 수동 확인 대기), M3 완료(`tail`, 복기, `replay`, `games`), M4 구현 완료(실전 E2E 대기) | |
| M0 하네스 스파이크 | 빈 엔진, 훅 6개, 좌석 2개 | 아래 §9의 미검증 가정을 VSCode에서 실측하고 `docs/harness-contract.md`에 기록 |
| M1 오리지널 엔진 | 규칙, 가시성, SQLite, FakeSeat 시뮬레이션 | 수백 판 자동 진행, audit 100% 통과 |
| M2 오리지널 연결 | MCP 도구, `/mafia`, 좌석 생성기 | 사람 1 + AI 7로 VSCode에서 한 판 완주 |
| M3 관전·기록 | `tail --god`, 복기 화면, replay, audit | |
| M4 머더 미스터리 | 시나리오 스키마·검증기 → 파이프라인 → 모드 규칙 | 검증 통과 시나리오 3편 이상, 한 판 완주 |
| M5 확장 | 역할 추가, 발언권 입찰, 내레이터 고도화 | |

## 8. 결정 기록

| # | 결정 | 기각한 대안과 이유 |
|---|---|---|
| D1 | 사회자는 결정론적 코드 | LLM 사회자: 모든 비밀을 알고 프롬프트를 직접 써서 누출을 막을 장치가 없다 |
| D2 | VSCode 채팅에서 플레이 + god 로그는 통합 터미널 | 순수 터미널 앱(Rich): 처음 안이었으나 사용자가 채팅 형식을 원함. 터미널 CLI: WSL 한글 IME 문제, 마크다운 렌더링이 약함 |
| D3 | 플레이어 = Claude Code 서브에이전트 + 훅 강제 | Messages API 직접 호출: 격리는 가장 강하지만 채팅 하네스가 아님. Agent SDK: 기본 도구를 하나하나 꺼야 함. M0 실패 시 대안은 엔진이 `claude -p`로 좌석을 직접 띄우는 것 |
| D4 | 좌석은 상태 없음, 기억은 엔진이 가짐 | `SendMessage`로 서브에이전트 재개: 메인이 AI에게 말을 걸 통로가 생기고, 전달 내용을 감사할 수 없음 |
| D5 | `play/` 워크스페이스 분리 | 한 폴더: 개발용 Bash와 플레이용 잠금이 공존할 수 없음 |
| D6 | 8인 고정 | 가변 인원: 시나리오(인물 8명, 카드 수) 설계와 검증이 인원에 묶여 복잡도가 폭증 |
| D7 | 머더 미스터리 시나리오는 사전 생성 + 다단계 검증 + 라이브러리 | 게임 직전 즉석 생성: 수십 분 걸리고 품질 보증이 없음 |
| D8 | 사람 입력 명령은 `:` 접두어 | `/`는 Claude Code 명령, `@`는 파일 멘션과 충돌 |
| D9 | 시나리오 품질은 프롬프트 수사가 아니라 구조로 보증 (스키마 → 코드 검증 → 적대적 감수 → 블라인드 풀이 → 수정 루프) | "세계 최고 수준의 기획자" 같은 역할 문장에 기대기: 현재 모델에서는 효과가 작음. 맥락·이유·검증이 품질을 만든다 |
| D10 | AI 실행 방식을 SeatRunner 포트로 분리. 기본은 `claude_subagent` | 훅 기반 강제를 엔진에 직접 묶기: Codex 등 다른 호스트로 옮길 수 없게 됨 (§11) |
| D11 | 지원 범위: Claude Code 우선, 다음 단계로 Codex. 로컬 LLM과 기타 모델 API는 범위 밖 (§11 향후 확장) | 처음부터 모델 무관 엔진 주도 구조(ModelProvider)로 가기: 검토 결과 격리·이식성은 더 좋지만, 사용자가 서브에이전트 기반 원안을 택함 (2026-10-30) |
| D12 | 에이전트 프롬프트 정본은 호스트 중립 md(`src/mafia/prompts/`). 호스트별 파일(`play/.claude/agents/*.md`의 frontmatter 등)은 `mafia setup`이 생성 | 호스트별 파일을 직접 관리: 호스트가 늘면 프롬프트가 갈라지고, 기계별 절대 경로가 리포에 들어감 |
| D13 | 오리지널 모드의 4개 역할은 테이블(`roles.py`)로 고정. 역할 플러그인(밤 행동 우선순위·해석 훅)은 5번째 역할을 넣을 때 도입 | 지금 플러그인 프레임워크 도입: 쓰임새가 하나뿐인 추상화는 형태를 잘못 잡기 쉬움. 모드 자체는 이미 `Mode` 프로토콜로 교체 가능 |
| D14 | 엔진은 이벤트 소싱 + 순수 함수. 난수는 `rng_for(seed, 용도, …)`로 결정적 | 상태 스냅샷 저장: 재생·감사·크래시 복구가 어려움. 전역 `random`: 재생 시 결과가 달라짐 |
| D15 | 좌석에게 보여 준 컨텍스트를 `ContextDelivered`(신의 시점) 이벤트로 기록하고, `audit`가 당시 상태로 재생해 검사 | 전달 내용을 로그 파일에만 남기기: 게임 상태와 어긋날 수 있고, 시점별 가시성을 재현할 수 없음 |

## 9. 하네스 가정 검증 (M0)

결과와 발견한 함정은 [harness-contract.md](harness-contract.md)에 있다. 2026-10-30, Claude Code 2.1.312 헤드리스 기준.

| # | 가정 | 결과 |
|---|---|---|
| 1 | Agent 결과에 `updatedToolOutput`을 적용하면 메인이 보는 내용이 바뀐다 | **조건부 확인.** 동기 실행일 때만 가능하고(기본은 백그라운드), 키 구성을 원본과 똑같이 유지해야 함. 다르면 경고 없이 무시됨 |
| 2 | 메인 세션의 훅 입력에는 `agent_type`이 없다 | 확인 |
| 3 | 프로젝트 훅, `.mcp.json`, `.claude/agents`가 VSCode에서도 똑같이 동작한다 | **미확인.** CLI 헤드리스로만 확인. VSCode 절차는 harness-contract.md §3 |
| 4 | MCP 도구만 가진 서브에이전트에는 Read/Bash/Agent가 없다 | 확인 (도구 자체가 노출되지 않음) |
| 5 | Agent의 `prompt`를 `updatedInput`으로 교체할 수 있다 | 확인 |
| 6 | 도구 이름이 `mcp__mafia__<tool>` 형식이다 | 확인 |

Claude Code를 업데이트하면 `scripts/contract-headless.sh`로 다시 확인한다.

## 10. 품질 기준 (상용 수준)

### 10.1 계층과 의존 방향

```
modes / engine (순수 로직, I/O 없음)
        ▲
store (SQLite, 마이그레이션)   views (가시성, 컨텍스트 조립, 렌더링)
        ▲
adapters: mcp_server · hosts/<host> · runners · cli
```

- 의존은 위쪽(안쪽)으로만 흐른다. `engine`과 `modes`는 `store`·`mcp`·`rich`를 import하지 않는다. `import-linter` 계약으로 CI에서 강제한다.
- 시간·난수·ID 생성은 주입한다(`Clock`, `Rng`, `IdGen`). 엔진 안에서 `datetime.now()`를 쓰지 않는다. WSL 벽시계가 뒤로 가는 문제도 있으므로 경과 시간은 단조 시계로 잰다.

### 10.2 확장 지점 (모두 프로토콜 + 레지스트리)

| 확장 | 방법 |
|---|---|
| 새 모드 | `modes/base.Mode` 구현 + 레지스트리 등록. 엔진 코드 수정 없음 |
| 새 역할 (오리지널) | `Role` 구현 파일 하나 |
| 새 AI 실행 방식 / 호스트 | `runners/base.SeatRunner`, `hosts/<name>/` |
| 새 시나리오 배경·반전 | `config/mystery/brief_pools.toml` |
| 새 인격 | `config/personas/*.yaml` |

서드파티 확장은 Python entry points(`mafia.modes`, `mafia.roles`, `mafia.runners`)로 받는다.

### 10.3 신뢰성
- **이벤트 소싱.** 게임 상태는 이벤트 로그를 접어서(fold) 만든다. 어느 시점에 죽어도 `/mafia continue`로 재개할 수 있다.
- **멱등 제출.** 좌석 요청마다 `request_id`가 있다. 같은 요청에 대한 중복 제출은 무시하고, 만료된 요청에 대한 제출은 거부한다.
- **타임아웃과 대체 행동.** 좌석 요청에 타임아웃·재시도 한도를 둔다. 초과하면 규칙상 안전한 대체 행동(패스, 무작위 유효 투표)을 하고 경고 이벤트를 남긴다.
- **훅은 fail-closed.** 훅 내부 예외는 모두 잡아서 명시적 `deny`로 응답한다. 비정상 종료가 "허용"으로 해석되는 경로를 두지 않는다.
- **스키마 버전.** DB(`schema_version` + 순차 마이그레이션), 이벤트, 시나리오 JSON 모두 버전 필드를 갖는다. 저장된 게임과 시나리오 라이브러리는 업그레이드 후에도 열린다.
- **설정 검증.** 시작 시 config 전체를 pydantic으로 검증하고, 틀린 키나 값은 파일:줄 수준의 메시지로 알린다.

### 10.4 검증 체계

| 층 | 대상 | 도구 |
|---|---|---|
| 정적 | 타입, 린트, 계층 계약 | `pyright --strict`(core), `ruff`, `import-linter` |
| 단위 | 규칙, 투표·밤 해석, 가시성, 시나리오 검증기, 훅 로직(JSON 픽스처 입력) | `pytest` |
| 속성 기반 | 불변식: 좌석 컨텍스트에 보이면 안 되는 이벤트가 없음, 승패 판정 일관성, 재생(fold) 결정성 | `hypothesis` |
| 시뮬레이션 | FakeSeat로 수천 판 자동 진행 + audit | `tests/sim` |
| 스냅샷 | 채팅 마크다운, god 로그 렌더링 | golden 파일 |
| 시나리오 검증기 | 정상 픽스처 1편 + 결함을 하나씩 주입한 변형 수십 개 → 각 결함을 정확히 잡는지 | `tests/unit/mystery` |
| 하네스 계약 | §9 가정 + 격리 장치 10개 | `tests/contract` 체크리스트 (실제 Claude Code, 수동) |

- 품질 게이트는 `uv run task check` 한 번에 돌린다(ruff, pyright, import-linter, pytest + 커버리지). core 커버리지 하한은 90%.
- 단위·속성·시뮬레이션 테스트는 네트워크를 쓰지 않는다. 실제 모델 호출은 하네스 계약 테스트와 실제 플레이에서만 한다.

### 10.5 운영성
- 엔진, MCP 서버, 훅 모두 구조화 로그(JSON 한 줄)를 `runs/logs/`에 남긴다. `game_id`, `request_id`, `agent_type`를 상관 키로 쓴다.
- `mafia doctor`: uv, MCP 등록, 훅 신뢰 여부, 에이전트 생성물 최신 여부, DB 쓰기 권한, 바이너리 경로를 진단하고 해결 방법을 출력한다.
- 생성물(`play/.claude/agents/seat-*.md` 등)에는 생성기 버전과 config 해시를 기록한다. `doctor`가 낡은 생성물을 감지한다.

## 11. 호스트 이식성 (Codex 등)

**결론: 지금 설계는 Codex에서 그대로는 돌아가지 않는다.** 엔진·MCP 서버·SQLite·CLI·프롬프트 본문은 이식 가능하지만, 격리를 강제하는 층이 Claude Code 훅의 의미론에 묶여 있다.

2026-10-30 기준 Codex 공식 문서(learn.chatgpt.com/docs/hooks, /docs/agent-configuration/subagents)와 비교한 결과:

| 우리가 쓰는 장치 | Claude Code | Codex |
|---|---|---|
| 도구 호출 훅에서 서브에이전트 식별 (`agent_type`) | 있음 | PreToolUse/PostToolUse 입력에 **명시 없음** (SubagentStart/Stop에만 있음) |
| PreToolUse 입력 재작성 | `updatedInput` | `updatedInput` 있음 |
| 모델이 보는 도구 결과 교체 | `updatedToolOutput` | 전용 필드 없음. `decision: "block"` + `reason`으로 결과를 피드백 문구로 대체 |
| 서브에이전트 도구 제한 | `tools`로 MCP 도구만 허용 | MCP 서버는 부모 것을 상속하고 줄일 수 없음. 셸은 `features.shell_tool=false`로 끌 수 있음 |
| 서브에이전트 정의 | `.claude/agents/*.md` (YAML frontmatter) | `.codex/agents/*.toml` (`developer_instructions`) |
| 훅 설정 | `.claude/settings.json` | `.codex/hooks.json` (feature flag, `/hooks`에서 승인 필요) |

좌석 사칭 방지(§4 #1~3)는 "도구 호출이 어느 서브에이전트에서 왔는가"를 훅이 알아야 성립하는데, Codex에서는 이게 문서상 보장되지 않는다.

### 설계 대응: SeatRunner 포트
"AI 한 차례를 어떻게 실행하는가"를 엔진에서 분리한다(`runners/`).

| 러너 | 방식 | 신원 강제 | 이식성 |
|---|---|---|---|
| `claude_subagent` (기본) | 채팅 세션이 Agent로 서브에이전트를 띄움 | 훅 (`agent_type`) | Claude Code 전용 |
| `headless` (확장) | 엔진이 좌석마다 `claude -p` 또는 `codex exec` 프로세스를 직접 띄우고, 그 프로세스 전용 MCP 서버 인스턴스를 `MAFIA_SEAT`·토큰 환경 변수와 함께 붙임 | **프로세스 경계.** 모델이 환경 변수를 바꿀 수 없음 | 호스트 무관. Claude 좌석과 Codex 좌석을 한 판에 섞을 수도 있음 |

`headless`에서는 채팅 호스트가 공개 도구(`advance` 등)만 부르면 된다. 따라서 메인 채팅도 MCP를 지원하는 어떤 에이전트든 될 수 있다. 대가는 다음과 같다.
- AI 차례가 MCP 도구 호출 안에서 오래 돌기 때문에, 엔진 워커를 백그라운드로 두고 `advance(wait)`로 폴링하는 구조가 필요하다.
- 각 CLI의 비대화형 모드에서 도구 제한·MCP 설정 고정 옵션을 따로 검증해야 한다.

### 향후 확장 후보 (현재 범위 밖, D11)
로컬 LLM(Ollama, llama.cpp, vLLM 등)과 기타 모델 API는 고려하지 않는다. 나중에 추가한다면 엔진이 좌석 컨텍스트로 모델을 직접 호출하는 `ModelProvider` 러너가 자연스러운 경로다. 2026-10-30에 검토한 쟁점:

- 등급별 프롬프트 오버레이
- 구조화 출력 제약(문법/JSON 모드)과 재시도
- 좌석별 기억 압축 (소형 모델은 실질 8K 컨텍스트)
- 역할 누설 후검사
- 모델별 평가 하네스

시나리오 생성은 강한 모델이 필요하다. 다만 완성된 시나리오 JSON은 모델과 무관한 데이터라 공유할 수 있다.

### 참고: superpowers가 어디서나 붙는 이유
superpowers는 **지시문(스킬 마크다운)**이라서, 호스트별 도구 이름 대응표(`references/codex-tools.md` 등)만 두면 옮겨진다. 강제력은 없다. 이 프로젝트의 격리는 **강제**가 핵심이라 호스트의 훅 의미론에 의존하고, 그래서 이식에 어댑터가 필요하다. 지시문 층(에이전트 프롬프트 본문, `/mafia` 진행 프로토콜)은 superpowers처럼 공유하고, 강제 층만 호스트별로 둔다.
