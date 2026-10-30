# Codex 지원 설계 (초안)

상태: 설계 초안 (2026-10-30). 구현 전이며, §4의 미검증 항목은 실측이 필요하다.
관련: [design.md §11](../design.md), D10, D11.

## 1. 왜 Claude 어댑터를 그대로 옮길 수 없나

Claude Code에서는 격리를 다음 장치로 강제한다([harness-contract.md](../harness-contract.md)).

| 장치 | Claude Code | Codex (공식 문서, 2026-10-30 확인) |
|---|---|---|
| 도구 호출 훅에서 서브에이전트 식별 | `agent_type` 있음 | PreToolUse/PostToolUse 입력에 **명시 없음**. SubagentStart/Stop에만 `agent_type` |
| 서브에이전트 도구 제한 | `tools`에 MCP 도구만 지정 | MCP 서버는 부모 것을 상속하며 줄일 수 없음. 셸은 `features.shell_tool=false`로 끔 |
| 모델이 보는 도구 결과 교체 | `updatedToolOutput` (키 구성 유지 필요) | 전용 필드 없음. `decision: "block"` + `reason`으로 결과를 피드백 문구로 대체 |
| 입력 재작성 | `updatedInput` | `updatedInput` 있음 |

좌석 사칭 방지(서버가 받는 `seat`을 훅이 덮어쓰기)는 도구 호출이 어느 서브에이전트에서 왔는지를 훅이 알아야 성립한다. Codex에서는 이게 보장되지 않는다.

## 2. 제안: 프로세스 경계로 신원을 강제하는 헤드리스 러너

**좌석을 채팅 세션의 서브에이전트로 띄우지 않는다.** 엔진이 좌석 차례마다 별도 `codex exec` 프로세스를 직접 실행한다.

```
Codex 채팅(메인) ──MCP(공개 도구만)──▶ 엔진 워커 ──spawn──▶ codex exec (seat-3 전용)
                                           │                    └─ MCP 서버 인스턴스 (env: MAFIA_SEAT=3, 일회용 토큰)
                                           └─ 결과는 DB로만 들어옴. 메인은 advance로 진행만 봄
```

- **신원:** 좌석 프로세스에 붙는 MCP 서버를 그 프로세스 전용으로 띄운다. 좌석 번호와 일회용 토큰은 환경 변수로 넘긴다. 모델은 환경 변수를 바꿀 수 없으므로 사칭이 구조적으로 불가능하다. 토큰은 요청 하나에만 유효하고 제출하면 소멸한다.
- **도구 제한:** 좌석 프로세스 설정에서 셸을 끄고(`features.shell_tool=false`), MCP 서버는 좌석 전용 서버 하나만 둔다. 작업 디렉토리는 빈 임시 디렉토리로 하고, sandbox는 읽기 전용이나 그보다 강한 수준으로 둔다.
- **결과 봉인:** 좌석의 출력은 메인 세션을 지나가지 않는다. 메인은 `advance`의 공개 표시만 본다. Claude 어댑터의 함정 1·2(백그라운드 알림, 출력 교체 무시)가 애초에 생기지 않는다.
- **메인 세션:** Codex 채팅에는 MCP 공개 도구(`new_game`, `advance`, `status`, `prepare_mystery`)와 드라이버 프롬프트(`AGENTS.md`)만 둔다. 사람 입력의 원문 기록은 Codex의 UserPromptSubmit 훅으로 처리한다. 이 경로는 좌석 식별이 필요 없어 그대로 옮겨진다.

## 3. 코드 구조

| 위치 | 내용 |
|---|---|
| `runners/base.py` | `SeatRunner.run(request) -> RunOutcome` 포트 |
| `runners/claude_subagent.py` | 현재 방식 (채팅이 Agent로 깨움, 훅 강제) |
| `runners/codex_exec.py` | 위 §2의 헤드리스 러너 |
| `hosts/codex/` | `AGENTS.md`, `.codex/config.toml`, 훅(`hooks.json`) 생성기 |
| 엔진 워커 | 좌석 차례를 프로세스로 돌리는 백그라운드 워커. `advance(wait)`는 그 사이 생긴 이벤트만 돌려준다(MCP 호출 시간 제한 대응) |

엔진·모드·저장소·뷰·프롬프트 본문은 그대로 공유한다. 프롬프트는 이미 호스트 중립 md다(D12).

## 4. 구현 전에 실측할 것

1. `codex exec`의 비대화형 옵션: 모델 지정, 셸 끄기, MCP 설정 파일 지정과 고정, 시스템 지시 주입, 종료 코드
2. `features.shell_tool=false`일 때 파일 읽기 경로가 정말 없는지
3. 좌석 한 차례의 지연과 토큰 오버헤드. 좌석 8명의 병렬 실행 한도
4. Codex 채팅(IDE 확장)에서 프로젝트 MCP와 `hooks.json` 승인 절차
5. UserPromptSubmit에서 `decision: "block"` + `reason`이 사람에게 보이는지 (`:role` 등 정보 명령)

## 5. 이 방식의 대가

- 채팅에서 서브에이전트 실행 행이 보이지 않는다. 대신 VSCode 에이전트 맵으로 엿볼 수 있던 스포일러 경로도 사라진다.
- 엔진 워커라는 실행 단위가 하나 늘어난다. 장애 시 재시작과 중복 실행 방지가 필요하다.
- Claude 쪽도 같은 러너로 바꿀 수 있다(`claude -p`). 그렇게 하면 두 호스트의 격리 방식이 통일된다. 다만 처음 정한 "Claude 서브에이전트" 방식을 바꾸는 일이므로, 사용자 결정 사항으로 남긴다.
