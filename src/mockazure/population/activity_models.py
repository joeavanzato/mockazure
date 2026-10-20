"""Finite activity inputs, stored inside the existing encrypted run snapshot."""

from pydantic import Field

from mockazure.population.types import Input


class ActivityBatch(Input):
    recipe_step: str = Field(min_length=1, max_length=128)
    action_count: int = Field(ge=1, le=100000, strict=True)
    pacing_ms: int = Field(default=0, ge=0, le=60000, strict=True)
