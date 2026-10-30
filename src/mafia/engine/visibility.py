"""Who may see an event. Every event carries exactly one `Visibility`; there is no default."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Scope(StrEnum):
    PUBLIC = "public"  # everyone at the table
    TEAM = "team"  # members of one team (e.g. mafia night chat)
    SEATS = "seats"  # the listed seats only (role cards, investigation results, own actions)
    GOD = "god"  # nobody at the table; logs, god view and audit only


class Visibility(BaseModel):
    model_config = ConfigDict(frozen=True)

    scope: Scope
    team: str | None = None
    seats: tuple[int, ...] = ()

    @classmethod
    def public(cls) -> Visibility:
        return cls(scope=Scope.PUBLIC)

    @classmethod
    def to_team(cls, team: str) -> Visibility:
        return cls(scope=Scope.TEAM, team=team)

    @classmethod
    def to_seats(cls, *seats: int) -> Visibility:
        if not seats:
            raise ValueError("a seat-scoped event needs at least one seat")
        return cls(scope=Scope.SEATS, seats=tuple(sorted(set(seats))))

    @classmethod
    def god(cls) -> Visibility:
        return cls(scope=Scope.GOD)


@dataclass(frozen=True, slots=True)
class Viewer:
    """A point of view. Seats get their team memberships from the mode."""

    seat: int | None = None
    teams: frozenset[str] = frozenset()
    is_god: bool = False

    @classmethod
    def god(cls) -> Viewer:
        return cls(is_god=True)


def can_see(viewer: Viewer, visibility: Visibility) -> bool:
    if viewer.is_god:
        return True
    match visibility.scope:
        case Scope.PUBLIC:
            return True
        case Scope.TEAM:
            return visibility.team is not None and visibility.team in viewer.teams
        case Scope.SEATS:
            return viewer.seat is not None and viewer.seat in visibility.seats
        case Scope.GOD:
            return False
