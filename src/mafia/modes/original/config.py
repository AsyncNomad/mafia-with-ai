from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mafia.modes.original.roles import Role

SEAT_COUNT = 8


class OriginalConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    roles: dict[Role, int] = Field(
        default_factory=lambda: {Role.MAFIA: 2, Role.POLICE: 1, Role.DOCTOR: 1, Role.CITIZEN: 4}
    )
    discussion_rounds: int = Field(default=2, ge=1, le=5)
    mafia_chat_rounds: int = Field(default=1, ge=0, le=3)
    doctor_self_protect: bool = True
    doctor_repeat_protect: bool = False
    reveal_role_on_death: bool = False
    max_days: int = Field(default=10, ge=2, le=30)
    # Classic table flow: the game opens at night, someone may die, then day breaks.
    start_with_night: bool = True

    @model_validator(mode="after")
    def _check_roles(self) -> OriginalConfig:
        if sum(self.roles.values()) != SEAT_COUNT:
            raise ValueError(
                f"role counts must sum to {SEAT_COUNT}, got {sum(self.roles.values())}"
            )
        if any(n < 0 for n in self.roles.values()):
            raise ValueError("role counts must be non-negative")
        mafia = self.roles.get(Role.MAFIA, 0)
        if not 1 <= mafia < SEAT_COUNT - mafia:
            raise ValueError("there must be at least one mafia and fewer mafia than others")
        if self.roles.get(Role.POLICE, 0) > 1 or self.roles.get(Role.DOCTOR, 0) > 1:
            raise ValueError("at most one police and one doctor are supported")
        return self
