"""Prompt formatting and evaluation helpers for the Phase 11 feasibility run."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from typing import Any, get_args

from pydantic import ValidationError

from .phase11_dataset import Phase11Example
from .schema import DisruptionEvent, PrimaryCause
from .teacher_labeling import system_prompt

TRAINED_CATEGORIES = frozenset(
    {
        "manufacturing_quality_problem",
        "labeling_packaging_error",
        "regulatory_noncompliance",
    }
)
MODEL_ID = "Qwen/Qwen2.5-1.5B-Instruct"
EXPERIMENT_LABEL = "phase11_feasibility_3_of_13"
_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def distillation_system_prompt() -> str:
    return system_prompt().replace(
        "You are the Phase 10 teacher annotator for FDA drug disruptions.",
        "Classify FDA drug disruptions into the exact DisruptionEvent JSON schema.",
    )


def compact_context(example: Phase11Example) -> dict[str, Any]:
    if example.source == "recall":
        keys = ("classification", "classifications", "status", "statuses")
    else:
        keys = ("status", "availability")
    return {
        key: example.source_context[key]
        for key in keys
        if key in example.source_context
    }


def input_messages(example: Phase11Example) -> list[dict[str, str]]:
    context = json.dumps(compact_context(example), ensure_ascii=False, sort_keys=True)
    return [
        {"role": "system", "content": distillation_system_prompt()},
        {
            "role": "user",
            "content": (
                f"SOURCE: {example.source}\n"
                f"CONTEXT: {context}\n"
                f"RAW_TEXT: {example.raw_text}\n\n"
                "Return only the JSON object. Every evidence item must be an exact, "
                "case-sensitive substring of RAW_TEXT."
            ),
        },
    ]


def training_messages(example: Phase11Example) -> list[dict[str, str]]:
    return [
        *input_messages(example),
        {
            "role": "assistant",
            "content": example.event.model_dump_json(),
        },
    ]


def prompt_hash() -> str:
    return hashlib.sha256(distillation_system_prompt().encode()).hexdigest()


def validate_prediction(
    raw_output: str, raw_text: str
) -> tuple[DisruptionEvent | None, str | None]:
    cleaned = _JSON_FENCE.sub("", raw_output.strip()).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as error:
        return None, f"invalid_json: {error.msg}"
    try:
        event = DisruptionEvent.model_validate(payload)
    except ValidationError as error:
        return None, f"schema_error: {error.errors()[0]['msg']}"
    invalid_evidence = [value for value in event.evidence if value not in raw_text]
    if invalid_evidence:
        return None, "evidence_not_exact_substring"
    return event, None


def evaluation_metrics(predictions: list[dict[str, Any]]) -> dict[str, Any]:
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in predictions:
        by_category[row["human_primary_cause"]].append(row)

    category_rows = {}
    for category in get_args(PrimaryCause):
        rows = by_category.get(category, [])
        if not rows:
            category_rows[category] = {
                "records": 0,
                "correct": 0,
                "accuracy_percentage": None,
                "valid_outputs": 0,
                "support_outcome": (
                    "trained_category_no_gold_examples"
                    if category in TRAINED_CATEGORIES
                    else "unsupported_category_no_gold_examples"
                ),
                "predicted_categories": {},
            }
            continue
        correct = sum(row["primary_cause_correct"] for row in rows)
        valid = sum(row["validation_error"] is None for row in rows)
        category_rows[category] = {
            "records": len(rows),
            "correct": correct,
            "accuracy_percentage": 100 * correct / len(rows),
            "valid_outputs": valid,
            "support_outcome": (
                "trained_category"
                if category in TRAINED_CATEGORIES
                else "unsupported_category"
            ),
            "predicted_categories": dict(
                Counter(
                    row["predicted_primary_cause"] or "invalid_output" for row in rows
                )
            ),
        }
    total = len(predictions)
    correct = sum(row["primary_cause_correct"] for row in predictions)
    supported = [
        row for row in predictions if row["human_primary_cause"] in TRAINED_CATEGORIES
    ]
    unsupported = [
        row
        for row in predictions
        if row["human_primary_cause"] not in TRAINED_CATEGORIES
    ]

    def subset(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "records": len(rows),
            "correct": sum(row["primary_cause_correct"] for row in rows),
            "accuracy_percentage": (
                100 * sum(row["primary_cause_correct"] for row in rows) / len(rows)
                if rows
                else 0.0
            ),
        }

    return {
        "records": total,
        "correct": correct,
        "accuracy_percentage": 100 * correct / total if total else 0.0,
        "valid_json_schema_evidence": sum(
            row["validation_error"] is None for row in predictions
        ),
        "supported_categories": subset(supported),
        "unsupported_categories": subset(unsupported),
        "by_category": category_rows,
        "outcome_types": dict(Counter(row["outcome_type"] for row in predictions)),
    }
