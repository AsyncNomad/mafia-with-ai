마피아 게임을 시작하거나 이어 갑니다. 인자: `$ARGUMENTS`

- `new [이름]`: 새 게임을 시작합니다. 사람 플레이어 이름은 기본값 "나"입니다. `mcp__mafia__new_game`을 `human_name`과 함께 호출하세요.
- `watch`: AI 8명끼리 하는 관전 게임입니다. `mcp__mafia__new_game`을 `watch=true`로 호출하세요.
- `new mystery [이름]` / `watch mystery`: 머더 미스터리 모드입니다. 위와 같되 `mode="mystery"`를 함께 넘기세요. 준비된 시나리오가 없으면 먼저 시나리오를 만들고(수십 분) 끝나는 대로 게임이 시작됩니다.
- `continue` 또는 인자 없음: 진행 중인 게임을 이어 갑니다. `mcp__mafia__advance`를 호출하세요.
- `status`: `mcp__mafia__status` 결과를 보여 주세요.
- `prepare [요청]`: 머더 미스터리 시나리오를 준비합니다. `mcp__mafia__prepare_mystery`를 `request`(배경이나 분위기에 대한 요청, 없으면 빈 문자열)와 함께 호출하세요. 준비에는 수십 분이 걸릴 수 있습니다.

그다음은 이 워크스페이스 CLAUDE.md의 **진행 루프**를 그대로 따르세요. `<display>`는 수정 없이 출력하고, `<next>`에 따라 좌석을 깨우거나 사람의 입력을 기다립니다.
