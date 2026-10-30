"""Injected time and deterministic randomness."""

from __future__ import annotations

import hashlib
import random
import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float:
        """Wall-clock seconds, used only to timestamp events for humans. Never for logic."""
        ...


class SystemClock:
    def now(self) -> float:
        return time.time()


class FixedClock:
    def __init__(self, start: float = 0.0, step: float = 1.0) -> None:
        self._t = start
        self._step = step

    def now(self) -> float:
        self._t += self._step
        return self._t


def rng_for(seed: str, *scope: object) -> random.Random:
    """A random generator determined only by the game seed and the decision's scope.

    Folding the same log always reproduces the same choices, whichever process folds it.
    """
    material = "\x1f".join([seed, *map(str, scope)]).encode()
    return random.Random(int.from_bytes(hashlib.sha256(material).digest()[:8], "big"))
