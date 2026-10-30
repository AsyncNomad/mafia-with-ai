from __future__ import annotations

import pytest

from mafia.engine.visibility import Viewer, Visibility, can_see

SEAT3 = Viewer(seat=3)
MAFIA3 = Viewer(seat=3, teams=frozenset({"mafia"}))
GOD = Viewer.god()


@pytest.mark.parametrize(
    ("viewer", "visibility", "expected"),
    [
        (SEAT3, Visibility.public(), True),
        (SEAT3, Visibility.god(), False),
        (MAFIA3, Visibility.god(), False),
        (GOD, Visibility.god(), True),
        (SEAT3, Visibility.to_team("mafia"), False),
        (MAFIA3, Visibility.to_team("mafia"), True),
        (MAFIA3, Visibility.to_team("other"), False),
        (SEAT3, Visibility.to_seats(3), True),
        (SEAT3, Visibility.to_seats(1, 2), False),
        (MAFIA3, Visibility.to_seats(1, 2, 3), True),
        (GOD, Visibility.to_seats(1), True),
        (Viewer(), Visibility.to_seats(1), False),
    ],
)
def test_can_see(viewer: Viewer, visibility: Visibility, expected: bool) -> None:
    assert can_see(viewer, visibility) is expected


def test_seat_scope_requires_seats() -> None:
    with pytest.raises(ValueError):
        Visibility.to_seats()


def test_seat_scope_is_normalised() -> None:
    assert Visibility.to_seats(3, 1, 3).seats == (1, 3)
