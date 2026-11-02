# Claude Code 하네스 계약 (M0 실측)

이 문서는 격리 보장이 기대는 Claude Code 동작을 **실측으로** 확인한 기록입니다. 일부 동작은 공식 문서에 없거나 문서와 다르게 동작합니다. 그래서 Claude Code를 업데이트하면 `scripts/contract-headless.sh`를 다시 돌려야 합니다.

- 측정일: 2026-10-30
- Claude Code: 2.1.312 (VSCode 확장 번들 바이너리, `claude -p` 헤드리스)
- 메인 모델: sonnet / 좌석: haiku
- 시나리오: [tests/contract/spike_prompt.md](../tests/contract/spike_prompt.md)
  - 공개 도구 호출
  - 좌석 2개 병렬 생성 (seat-2는 레드팀: 사칭, 파일 접근, 제출 회피를 시도)
  - 대기 중이 아닌 에이전트 생성 시도
  - 메인의 Read/Bash 시도
  - 결과 원문 인용 요구

## 1. 결과

| 계약 | 판정 | 근거 |
|---|---|---|
| C1 메인 세션의 훅 입력에는 `agent_type`이 없다 | PASS | Agent 호출 3건 모두 없음 |
| C2 MCP 도구 이름은 `mcp__mafia__<tool>` | PASS | |
| C3 서브에이전트 안의 도구 호출에는 `agent_type`(+`agent_id`)이 있다 | PASS | `seat-1`, `seat-2` |
| C4 좌석 사칭 시 `seat` 인자를 덮어쓴다 (`updatedInput`) | PASS | seat-2가 `seat=1`로 호출 → 서버는 2번으로 받음 |
| C5 좌석에게 Read/Bash/공개 도구가 없다 | PASS | `tools` 제한으로 도구 자체가 노출되지 않음 |
| C6 서브에이전트 프롬프트가 고정 문구로 바뀐다 | PASS | 서브에이전트 전사본의 첫 메시지 = `WAKE_PROMPT` |
| C7 서브에이전트 결과가 메인에 봉인되어 보인다 | PASS | 메인이 받은 것은 `seat-N: 제출 완료`뿐 (§2의 함정 1, 2 해결 후) |
| C8 대기 중이 아닌 에이전트(`general-purpose`)는 생성이 거부된다 | PASS | 실제 시작 0건 |
| C9 메인의 Read/Bash 차단 | N/A (더 강하게 충족) | 권한 `deny`가 도구 목록에서 아예 제거. 훅까지 오지 않음 |
| C10 제출 없이 끝내려 하면 SubagentStop이 1회 막고, 이후 제출한다 | PASS | 차단 1회 → 제출 |
| C11 사람 입력이 UserPromptSubmit에서 원문 그대로 기록된다 | PASS | |
| C12 훅 처리 시간 | PASS | 핸들러 최대 4ms. 프로세스 기동 포함 중앙값 31ms, 최대 34ms |
| C13 훅 내부 오류 없음 | PASS | |

관찰 모드(모든 호출 허용)에서는 같은 시나리오에서 사칭이 성공했고, 비밀 코드가 메인에 그대로 노출됐습니다. 즉 위 PASS는 모두 훅이 만들어 낸 결과입니다.

## 2. 실측으로 발견한 함정 (문서에 없거나 문서와 다름)

### 함정 1. Agent는 기본이 백그라운드이고, 결과는 PostToolUse를 거치지 않는다
- `run_in_background`를 지정하지 않으면 Agent는 비동기로 실행됩니다. PostToolUse는 `{"status": "async_launched", ...}`만 받습니다.
- 실제 결과는 나중에 `<task-notification>…<result>…</result>` 형태의 **사용자 프롬프트**로 메인에 들어옵니다. 이 프롬프트는 UserPromptSubmit 훅을 거칩니다.
- **대응 (두 겹):**
  1. 생성 훅이 `updatedInput`에 `run_in_background: false`를 **명시**해 동기로 강제합니다. 필드를 지우면 기본값(백그라운드)이 적용되므로 반드시 명시해야 합니다.
  2. UserPromptSubmit 훅은 `<task-notification>`으로 시작하는 프롬프트를 `decision: "block"` + `suppressOriginalPrompt: true`로 차단합니다. 이런 프롬프트는 사람 입력으로도 기록하지 않습니다.

### 함정 2. `updatedToolOutput`은 원래 응답과 키 구성이 다르면 경고 없이 무시된다
- 처음 구현은 봉인 시 응답에서 `prompt` 키를 지웠는데, Claude Code가 교체를 적용하지 않고 원본을 그대로 메인에 넘겼습니다. 오류나 로그는 없었습니다.
- **대응:** 봉인은 원본의 키를 모두 유지하고 값만 바꿉니다(`content`를 봉인 문구로, 텍스트 필드도 봉인 문구로). 단위 테스트가 `sealed.keys() == response.keys()`를 고정합니다.

### 함정 3. `suppressOriginalPrompt`의 의미
- 바이너리 스키마 설명: *"When decision is "block", omit the original prompt from the block message"*.
- 프롬프트를 바꿔 끼우는 기능이 아닙니다. **차단할 때 원문을 메시지에서 빼는 옵션**입니다.

### 함정 4. 권한 `deny`는 도구를 목록에서 제거한다
- `permissions.deny`에 넣은 내장 도구는 모델에게 아예 보이지 않습니다. 훅의 PreToolUse 차단보다 앞단에서 막히므로 C9는 훅 로그에 나타나지 않습니다.
- 이 설정은 세션 전체(서브에이전트 포함)에 적용됩니다.

### 참고: Claude Code가 보내는 실제 필드
- **PreToolUse (메인):** `cwd, effort, hook_event_name, permission_mode, prompt_id, session_id, tool_input, tool_name, tool_use_id, transcript_path`
- **PreToolUse (서브에이전트):** 위 필드 + `agent_id, agent_type, mcp_server`
- **SubagentStop:** `agent_id, agent_transcript_path, agent_type, background_tasks, cwd, effort, last_assistant_message, permission_mode, prompt_id, session_crons, session_id, stop_hook_active, transcript_path`
- **UserPromptSubmit:** `cwd, hook_event_name, permission_mode, prompt, prompt_id, session_id, transcript_path`. 알림인지 구분하는 필드는 **없습니다**. 그래서 프롬프트 본문의 `<task-notification>` 접두어로 판별합니다.
- **Agent의 동기 응답(`tool_response`):** `status, prompt, agentId, agentType, toolsUsed, harnessNoteCount, harnessTailCount, harnessSectionHash, content[], resolvedModel, totalDurationMs, totalTokens, totalToolUseCount, usage, toolStats, canContinueAgent`

## 3. 실제 게임 E2E (M2)

2026-10-30, `claude -p "/mafia watch"` 헤드리스. 조건은 AI 8명 관전 게임이고, 메인과 좌석 모두 sonnet, 좌석 effort는 medium입니다.

| 항목 | 결과 |
|---|---|
| 진행 | 메인 146턴, 약 9분 44초 만에 게임 정상 종료 (마피아 승). API 요금 환산 약 $3.6 |
| 이벤트 | 384개, `audit` 위반 **0건** |
| 좌석 행동 | 92개. 대체 행동 0건, 92건에 메모, 91건에 의심도 |
| 훅 | 836회 호출, 내부 오류 0건, 최대 94ms |
| 차단 | 메인·좌석의 허용 외 도구 시도 0건. 백그라운드 알림 0건(동기 강제가 동작함). 제출 전 종료 시도 5회는 차단 후 모두 제출 |
| 봉인 | Agent 결과 87건 모두 `status: completed`로 받아 봉인 |

재현하려면 `uv run mafia setup` 후 `play/`에서 `claude -p "/mafia watch"`를 실행합니다.

## 4. VSCode 확장에서 발견한 차이 (2026-10-30, 사용자 첫 플레이)

### 함정 5. VSCode의 Agent는 항상 백그라운드로 실행된다
- 생성 훅이 `updatedInput`에 `run_in_background: false`를 넣어도 `tool_response.status`가 `async_launched`로 돌아왔다. CLI 헤드리스에서는 지켜졌다.
- VSCode의 Agent 도구 스키마에는 이 인자가 아예 없는 것으로 보인다.
- 격리는 유지됐다. 결과가 `<task-notification>`으로 들어왔고, UserPromptSubmit에서 차단됐다(함정 1의 2차 방어선).
- 하지만 진행이 꼬였다. 진행자가 에이전트가 끝나기 전에 `advance`를 부르고, 결과 알림을 막으니 진행자가 다시 깨어날 방법도 없었다.
- **대응 (호스트와 무관하게 동작):**
  1. SubagentStart/SubagentStop 훅이 `agent_id`별 실행 중 여부를 `agent_live`에 기록한다. 30분이 지나면 죽은 실행으로 본다.
  2. 실행 중인 에이전트는 다시 깨우지 않는다. 생성 허용 기준은 "실행 중 수 < 단계가 쓸 수 있는 수"다. 솔버는 남은 작업 수만큼 병렬 생성을 허용한다.
  3. 기다릴 에이전트가 모두 실행 중이면 `advance`가 `next.type = "wait"`을 돌려준다. 진행자는 도구를 부르지 않고 턴을 끝낸다. 준비 파이프라인도 그 단계의 에이전트가 실행 중이면 판정을 미루고 깨우기 횟수를 쓰지 않는다.
  4. 완료 알림은 `<result>`가 정확히 "제출 완료"(또는 빈 값)일 때만 통과시키고, 진행자에게 `advance`를 부르라는 맥락을 붙인다. 그 밖의 결과는 계속 차단한다.
  5. 제출을 마친 에이전트가 다른 말로 끝내려 하면 SubagentStop이 한 번 막는다("최종 응답은 '제출 완료' 한 마디만").

### 함정 6. MCP SDK는 동기 도구를 스레드 풀에서 실행한다
- `mcp` 2.x의 `MCPServer`는 동기 함수를 `anyio.to_thread.run_sync`로 돌린다. 서버가 캐시해 둔 sqlite3 연결이 다른 작업 스레드에서 쓰이는 순간 `ProgrammingError`가 난다. 그 뒤로는 모든 도구가 0초 만에 실패했다.
- SDK가 예외를 "Error executing tool"로만 바꿔서 원인이 보이지 않았다.
- 헤드리스 E2E에서는 우연히 같은 스레드만 걸려 드러나지 않았다.
- **대응:** 작업 스레드마다 `TableApp`(연결)을 하나씩 둔다. 예상하지 못한 예외는 트레이스백을 `runs/logs/mcp-server.log`에 남기고, 읽을 수 있는 "엔진 오류" 메시지로 돌려준다.
- **회귀 테스트** (`tests/unit/test_mcp_server.py`):
  - 연결을 만든 스레드가 살아 있는 동안 다른 스레드에서 호출하는 경우. 순차 스레드는 스레드 ID가 재사용돼 재현되지 않는다.
  - 모든 도구 본문이 가드를 거치는지 검사

### 함정 7. VSCode의 백그라운드 서브에이전트는 `SubagentHandback`으로 끝나야 한다
- 이 도구(`{"message": …}`)로 보고하지 않고 끝내면 Claude Code가 스스로 종료를 막고 보고를 요구한다. 결국 끝나도 알림 `<result>`에는 "보고 없이 끝났다"는 문구가 들어간다.
- 우리 훅이 이 도구를 막았던 탓에 알림이 봉인 조건을 통과하지 못했고, 진행이 멈췄다.
- 동시에 이 도구는 누출 통로였다. 실제로 한 좌석이 보고 메시지에 "(4번, 마피아)"라고 쓰려 했고, 훅이 막아서 누출되지 않았다.
- **대응:** 제출을 마친 좌석·에이전트에게만 허용하고, `message`는 항상 "제출 완료"로 덮어쓴다(`updatedInput`).

### 함정 8. MCP SDK는 `str` 반환을 JSON으로 감싼다
- `mcp` 2.x는 반환 타입을 보고 구조화 출력(`{"result": "…"}`)을 자동으로 만든다. 진행자는 `\n`이 이스케이프된 JSON 문자열을 받았고, 그대로 옮겨 적는 대신 요약했다. 그 결과 사람은 다른 플레이어의 발언을 보지 못했다.
- **대응:** 모든 도구에 `structured_output=False`를 지정해 평문 `TextContent`로 돌려준다. 드라이버 프롬프트에도 "사람은 도구 결과를 볼 수 없고, 당신이 쓴 글만 본다"를 명시한다.

### 함정 9. UserPromptSubmit에는 사람이 친 글만 오지 않는다
- VSCode에서는 백그라운드 서브에이전트의 보고가 `<agent-message from="…">[Subagent hand-back] … The report follows:\n  (두 칸 들여쓴 보고)</agent-message>` 형태의 프롬프트로도 들어왔다. `<task-notification>`과는 다른 형식이다.
- 우리 훅이 이 보고를 사람 입력으로 처리했다. 마침 사람의 발언 차례여서, 좌석의 보고("제출 완료")가 **사람의 발언으로 제출**됐다. 사람이 겪기에는 "내 차례가 씹히고 넘어간다"로 보였다.
- 사람이 친 글 앞에도 `<ide_opened_file>…</ide_opened_file>` 같은 IDE 정보 블록이 붙어 온다.
- **대응:** `hooks/prompts.py`가 프롬프트의 겉봉투를 먼저 분류한다.
  - `<task-notification>`과 `<agent-message>`는 절대 사람 입력이 아니다.
  - 봉인 조건(보고 본문이 정확히 "제출 완료"이고, 안내문 위에는 도구 목록 메모만 있음)을 만족하면 진행자를 깨운다. 그 밖에는 차단한다.
  - 사람 입력은 IDE 정보 블록을 떼어 낸 뒤 해석한다.
  - 테스트 입력은 실제 기록에 찍힌 문자열을 그대로 쓴다(`tests/unit/test_hook_prompts.py`).

### 함정 10. 진행자는 표시 내용을 요약하려 든다
- 평문으로 바꾼 뒤에도 진행자가 "하람의 반응을 작성했어요: …"처럼 요약하고, 원문은 접힌 도구 결과에만 남았다.
- **대응:** 메인이 표시 도구(`advance`, `new_game`, `prepare_mystery`)의 결과를 받을 때마다 PostToolUse 훅이 "지금 `<display>`를 그대로 출력하라"는 `additionalContext`를 붙인다. 프롬프트 맨 앞에 적어 둔 지시보다 결과를 받은 순간의 지시가 잘 지켜진다는 판단이다. 드라이버 지침에도 예시를 넣었다.
- 이 지시도 지켜지지 않았다. 진행자는 결과를 받자마자 다음 좌석을 깨우고, 그 뒤에 "○○이 발언을 고르고 있습니다"라는 상태 안내만 썼다.
- **최종 대응 (강제):** 진행자가 다음 행동(Agent, `advance`, `new_game`, `prepare_mystery`)을 하려 하거나 턴을 끝내려 할(Stop) 때, 훅이 진행자의 전사본을 읽는다(`hooks/echo.py`).
  - 직전 `<display>`의 모든 줄이 그 뒤 진행자의 답변에 들어 있는지 확인한다. 공백과 마크다운 기호는 무시하고 각 줄의 앞 30자를 비교한다.
  - 빠진 줄이 있으면 행동을 거부하거나 턴 종료를 막고, 빠진 줄을 알려 준다.
  - 실제 VSCode 전사본으로 재생해 보니, 요약하고 넘어갔던 행동 29건을 모두 잡았고 제대로 출력한 1건만 허용했다.
  - 화면 표시를 보장하는 장치이지 보안 경계가 아니다. 그래서 전사본을 읽지 못하면 통과시킨다(fail-open).

### 함정 11. 깨우는 이름 자체가 역할을 누설한다
- 첫 실제 플레이에서 시민인 사람이 밤에 "마피아 누구야?"라고 치자 진행자가 마피아를 짚어 말했다.
- 진행자는 비밀 정보를 받지 않았다. 그런데 밤에 `advance`가 "`seat-2`, `seat-6`을 깨워라"라고 알려 주었고, VSCode의 에이전트 표시도 같은 이름을 보여 줬다. 밤에 깨어나는 좌석 = 마피아·경찰·의사이므로 **이름 목록이 곧 역할표**였다. 진행자는 그걸 근거로 답했고, 사람도 화면만 보고 알 수 있었다.
- **대응 1: 비밀 요청(`RequestSpec.covert`).** 밤 행동(마피아 처치, 경찰 조사, 의사 보호)과 마피아 대화 요청은 `covert=True`다. 좌석 이름(`seat-N`)으로는 비밀 요청에 닿을 수 없다. `is_awaiting("seat-N")`, `needs_submission("seat-N")`이 거짓이라 스폰도 막히고, 무엇을 물어도 차이가 드러나지 않는다.
- **대응 2: 익명 별칭 `agent-1`~`agent-8`.** 비밀 요청이 열려 있는 동안 **살아 있는 AI 전원**에게 별칭을 무작위(`random.SystemRandom`, 시드와 무관)로 새로 배정하고 전원을 깨운다(`TableApp._sync_aliases`, 테이블 `aliases`).
  - 할 일이 없는 좌석(더미)도 똑같이 한 번 깨어나 "지금은 차례가 아닙니다"를 받고 끝난다. 그래서 깨우는 수도 역할과 무관하다.
  - 별칭 대응은 엔진 DB에만 있다. 훅이 `agent-N`의 호출을 받으면 `seat_of`로 좌석을 찾아 그 좌석으로 주입하고(`dispatch._resolve`), 대응이 없으면 거부한다.
  - 요청 묶음(비밀 요청 id 집합)이 바뀔 때마다 새로 섞는다. 같은 별칭이 다음 밤에 같은 사람이라는 보장이 없다.
- **대응 3: 마피아 대화를 숨김.** 마피아 대화 단계 진입(`PhaseChanged`)은 마피아 팀에게만 보이고, 단계 이름도 "밤"으로만 표시한다. 마피아 대화는 라운드마다 마피아 전원이 동시에 말한다(순서가 누설되지 않게).
- **대응 4: 차례 밖의 말은 엔진이 답한다.** 사람이 발언 차례가 아닐 때 친 글은 UserPromptSubmit 훅이 진행자를 거치지 않고 정해진 문구로 답한다. 밤이면 "🌙 밤입니다. 지금은 아무도 말할 수 없습니다…", 낮이면 "지금은 당신의 발언 차례가 아닙니다…". 진행자 모델이 아예 호출되지 않으니 탈옥 문구가 먹힐 곳이 없다. 진행자에게 따로 묻고 싶으면 `:ask`를 쓴다. 진행자는 애초에 비밀을 모르고, 드라이버 지침의 "비밀 보호" 절이 추측까지 금한다.
- **남는 틈 (수용):** 더미는 할 일이 없어 빨리 끝난다. 에이전트 맵에서 끝나는 시점을 지켜보면 "늦게 끝난 별칭이 행동한 좌석"이라는 짐작은 가능하다. 다만 별칭이 매번 다시 섞이므로 그 별칭이 누구인지는 여전히 모른다.

### 진행 방식: 알림에 의존하지 않는 롱폴링
- 완료 알림의 형식은 호스트와 모드마다 다르고 문서화되어 있지 않다. 그래서 진행을 알림에 걸지 않는다.
- `advance`는 에이전트가 실행 중이면 서버에서 최대 40초 동안 1초 간격으로 다시 확인하고, 그사이 생긴 화면을 모아 돌려준다. 그래도 `wait`이면 진행자가 `advance`를 다시 부른다.
- 알림은 봉인 조건을 만족할 때만 통과시키고, 그 밖에는 짧은 문구로 차단한다.

## 5. 남은 검증

- [ ] **VSCode 확장에서 같은 계약을 확인하기.** 헤드리스와 같은 바이너리지만 UI 경로는 다를 수 있습니다. 절차:
  1. `uv run mafia spike-reset && uv run mafia setup --spike --mode enforce`
  2. VSCode에서 `play/` 폴더를 새 창으로 열고, 워크스페이스 신뢰와 MCP 서버 `mafia` 사용을 승인합니다.
  3. Claude Code 채팅에 [tests/contract/spike_prompt.md](../tests/contract/spike_prompt.md) 내용을 붙여 넣습니다.
  4. 응답의 마지막 메시지를 `runs/spike/vscode-main.txt`로 저장한 뒤 `uv run mafia spike-report --main-output runs/spike/vscode-main.txt`를 실행합니다.
- [ ] **Codex 단계에서 같은 계약을 정의하기.** 훅 의미론이 달라서(§11) 헤드리스 러너로 대응합니다.

## 6. 알려진 한계 (설계상 수용)

- 사람이 VSCode 에이전트 맵에서 서브에이전트를 직접 열면 내부(`my_turn` 결과)가 보입니다. 서브에이전트 전사본(`~/.claude/projects/.../subagents/`)에도 남습니다. AI 사이의 격리와는 무관한 사람 쪽 스포일러 문제입니다.
- SubagentStop 차단은 1회만 합니다. 두 번째에도 제출하지 않으면 종료를 허용하고, 엔진이 대체 행동을 적용합니다. 무한 루프보다 낫다고 판단했습니다.
