# mafia-with-ai

사람 1명 + AI 7명이 Claude Code 채팅창에서 하는 8인 추리 게임(오리지널 마피아 / 머더 미스터리).
AI 플레이어는 서로 정보를 공유하지 않는 독립 에이전트이고, 격리는 하네스(훅·권한·MCP)와 엔진으로 강제합니다.

- 설계: [docs/design.md](docs/design.md)
- 모드: [docs/modes/](docs/modes/)

## 개발

```bash
uv sync
uv run pytest
```
