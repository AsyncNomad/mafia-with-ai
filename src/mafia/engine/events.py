"""Event envelope and typed event bodies.

An `Event` is what is stored: an envelope (sequence number, time, visibility) around a body
serialised as `kind` + `data`. Bodies are pydantic models that register themselves by `KIND`,
so modes add their own event types without touching this module.
"""

from __future__ import annotations

from typing import Any, ClassVar, TypeVar

from pydantic import BaseModel, ConfigDict

from mafia.engine.visibility import Visibility

_REGISTRY: dict[str, type[Body]] = {}

B = TypeVar("B", bound="Body")


class Body(BaseModel):
    """Base class of every event body. Subclasses set a unique `KIND`."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    KIND: ClassVar[str] = ""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        kind = cls.__dict__.get("KIND", "")
        if not kind:
            return  # abstract intermediate class
        if kind in _REGISTRY and _REGISTRY[kind] is not cls:
            raise TypeError(f"duplicate event kind {kind!r}")
        _REGISTRY[kind] = cls


def body_type(kind: str) -> type[Body]:
    try:
        return _REGISTRY[kind]
    except KeyError:
        raise ValueError(f"unknown event kind {kind!r}") from None


class Event(BaseModel):
    """A stored event. `seq` starts at 1 and has no gaps within a game."""

    model_config = ConfigDict(frozen=True)

    seq: int
    at: float
    kind: str
    visibility: Visibility
    data: dict[str, Any]

    def body(self) -> Body:
        return body_type(self.kind).model_validate(self.data)

    def body_as(self, cls: type[B]) -> B:
        if self.kind != cls.KIND:
            raise TypeError(f"event {self.seq} is {self.kind!r}, not {cls.KIND!r}")
        return cls.model_validate(self.data)


class NewEvent(BaseModel):
    """An event produced by the engine or a mode, before it is numbered and timestamped."""

    model_config = ConfigDict(frozen=True)

    body: Body
    visibility: Visibility


def emit(body: Body, visibility: Visibility) -> NewEvent:
    return NewEvent(body=body, visibility=visibility)
