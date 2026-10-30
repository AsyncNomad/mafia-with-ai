"""Roles and teams of the original mode.

M1 keeps the four classic roles in a table. A role plug-in mechanism (night-action priority,
custom resolution) is deferred until a fifth role is added; see docs/design.md D13.
"""

from __future__ import annotations

from enum import StrEnum


class Team(StrEnum):
    MAFIA = "mafia"
    CITIZENS = "citizens"


class Role(StrEnum):
    MAFIA = "mafia"
    POLICE = "police"
    DOCTOR = "doctor"
    CITIZEN = "citizen"

    @property
    def team(self) -> Team:
        return Team.MAFIA if self is Role.MAFIA else Team.CITIZENS

    @property
    def label(self) -> str:
        return _LABELS[self]

    @property
    def description(self) -> str:
        return _DESCRIPTIONS[self]


_LABELS = {
    Role.MAFIA: "마피아",
    Role.POLICE: "경찰",
    Role.DOCTOR: "의사",
    Role.CITIZEN: "시민",
}

_DESCRIPTIONS = {
    Role.MAFIA: (
        "당신은 마피아입니다. 밤마다 동료 마피아와 상의해 시민 한 명을 처치합니다. "
        "낮에는 정체를 숨기고 시민인 척하세요. 마피아 수가 나머지 생존자 수 이상이 되면 승리합니다."
    ),
    Role.POLICE: (
        "당신은 경찰입니다. 밤마다 한 명을 조사해 마피아인지 알아냅니다. "
        "결과는 당신만 압니다. 언제 어떻게 밝힐지 신중히 정하세요. 마피아를 모두 처형하면 시민 팀이 승리합니다."
    ),
    Role.DOCTOR: (
        "당신은 의사입니다. 밤마다 한 명을 골라 마피아의 공격에서 지킵니다. "
        "마피아를 모두 처형하면 시민 팀이 승리합니다."
    ),
    Role.CITIZEN: (
        "당신은 시민입니다. 특별한 능력은 없지만 토론과 투표로 마피아를 찾아내야 합니다. "
        "마피아를 모두 처형하면 시민 팀이 승리합니다."
    ),
}
