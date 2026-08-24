"""Canonical event models used by downstream disruption analysis."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Locked after the Phase 8 baseline audit; revise only with a versioned taxonomy change.
PRIMARY_CAUSE_TAXONOMY_VERSION = "phase9_v1"

PrimaryCause = Literal[
    "active_ingredient_shortage",
    "inactive_ingredient_shortage",
    "manufacturing_quality_problem",
    "manufacturing_capacity",
    "regulatory_delay",
    "shipping_delay",
    "demand_increase",
    "product_discontinuation",
    "labeling_packaging_error",
    "regulatory_noncompliance",
    "adverse_event_signal",
    "recall",
    "unknown",
]

Severity = Literal["low", "medium", "high"]


class DisruptionEvent(BaseModel):
    """A normalized, evidence-backed drug supply disruption event.

    ``primary_cause`` describes the underlying disruption mechanism when the
    FDA text supports one. ``recall`` is reserved for recall events whose root
    cause is unclear or falls outside the locked Phase 9 taxonomy.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    primary_cause: PrimaryCause
    secondary_causes: list[str] = Field(default_factory=list)
    supply_chain_stage: str = Field(min_length=1)
    severity: Severity
    evidence: list[str] = Field(min_length=1)

    @field_validator("secondary_causes", "evidence")
    @classmethod
    def reject_blank_list_items(cls, values: list[str]) -> list[str]:
        """Strip list values and reject evidence/cause labels with no text."""
        stripped = [value.strip() for value in values]
        if any(not value for value in stripped):
            raise ValueError("list items must contain non-whitespace text")
        return stripped
