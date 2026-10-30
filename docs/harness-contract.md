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

## 3. 남은 검증

- [ ] **VSCode 확장에서 같은 계약을 확인하기.** 헤드리스와 같은 바이너리지만 UI 경로는 다를 수 있습니다. 절차:
  1. `uv run mafia spike-reset && uv run mafia setup --spike --mode enforce`
  2. VSCode에서 `play/` 폴더를 새 창으로 열고, 워크스페이스 신뢰와 MCP 서버 `mafia` 사용을 승인합니다.
  3. Claude Code 채팅에 [tests/contract/spike_prompt.md](../tests/contract/spike_prompt.md) 내용을 붙여 넣습니다.
  4. 응답의 마지막 메시지를 `runs/spike/vscode-main.txt`로 저장한 뒤 `uv run mafia spike-report --main-output runs/spike/vscode-main.txt`를 실행합니다.
- [ ] **Codex 단계에서 같은 계약을 정의하기.** 훅 의미론이 달라서(§11) 헤드리스 러너로 대응합니다.

## 4. 알려진 한계 (설계상 수용)

- 사람이 VSCode 에이전트 맵에서 서브에이전트를 직접 열면 내부(`my_turn` 결과)가 보입니다. 서브에이전트 전사본(`~/.claude/projects/.../subagents/`)에도 남습니다. AI 사이의 격리와는 무관한 사람 쪽 스포일러 문제입니다.
- SubagentStop 차단은 1회만 합니다. 두 번째에도 제출하지 않으면 종료를 허용하고, 엔진이 대체 행동을 적용합니다. 무한 루프보다 낫다고 판단했습니다.
