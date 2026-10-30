"""Event bodies of the original mode. Visibility is decided where they are emitted (rules.py)."""

from __future__ import annotations

from typing import ClassVar

from mafia.engine.events import Body
from mafia.modes.original.roles import Role, Team


class RoleAssigned(Body):
    """Seat-only role card. Mafia also learn their teammates."""

    KIND: ClassVar[str] = "original.role_assigned"
    seat: int
    role: Role
    teammates: tuple[int, ...] = ()


class DiscussionRoundStarted(Body):
    KIND: ClassVar[str] = "original.discussion_round_started"
    day: int
    round: int
    order: tuple[int, ...]


class Speech(Body):
    """Public speech. `text=None` means the seat passed."""

    KIND: ClassVar[str] = "original.speech"
    seat: int
    text: str | None
    context: str  # "discussion" | "defense"


class MafiaChatRoundStarted(Body):
    KIND: ClassVar[str] = "original.mafia_chat_round_started"
    day: int
    round: int


class MafiaChat(Body):
    KIND: ClassVar[str] = "original.mafia_chat"
    seat: int
    text: str | None


class NominationRevealed(Body):
    """Simultaneous reveal of the nomination vote. `None` vote = abstained."""

    KIND: ClassVar[str] = "original.nomination_revealed"
    votes: dict[int, int | None]
    nominee: int | None


class ConfirmationRevealed(Body):
    KIND: ClassVar[str] = "original.confirmation_revealed"
    votes: dict[int, bool | None]
    approved: bool


class PlayerDied(Body):
    """Public death notice. `role` is filled only when the table reveals roles on death."""

    KIND: ClassVar[str] = "original.player_died"
    seat: int
    cause: str  # "execution" | "night"
    role: Role | None = None


class PeacefulNight(Body):
    KIND: ClassVar[str] = "original.peaceful_night"
    day: int


class MafiaTargetChosen(Body):
    """Mafia-only: the team's final decision for tonight."""

    KIND: ClassVar[str] = "original.mafia_target_chosen"
    target: int | None
    votes: dict[int, int | None]


class InvestigationResult(Body):
    """Police-only."""

    KIND: ClassVar[str] = "original.investigation_result"
    target: int
    is_mafia: bool


class NightResolved(Body):
    """God-only record of what really happened at night."""

    KIND: ClassVar[str] = "original.night_resolved"
    day: int
    mafia_target: int | None
    protected: int | None
    investigated: int | None
    killed: int | None


class RolesRevealed(Body):
    KIND: ClassVar[str] = "original.roles_revealed"
    roles: dict[int, Role]
    winner: Team | None
