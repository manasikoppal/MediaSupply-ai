from __future__ import annotations

import json

import pytest

from src.models.gold_dataset import GoldCandidate
from src.models.taxonomy_guidance import CATEGORY_GUIDANCE
from src.models.teacher_labeling import (
    TeacherInputRecord,
    TeacherValidationError,
    auto_unknown_label,
    build_corpus_queue,
    few_shots_for_target,
    system_prompt,
    validate_teacher_output,
)


def _candidate(**updates) -> GoldCandidate:
    payload = {
        "candidate_id": "recall:R-1",
        "sequence": 1,
        "taxonomy_version": "phase9_v1",
        "snapshot": "2026-08-21_17",
        "sampling_stratum": "recall",
        "selection_reason": "baseline_low_confidence",
        "source": "recall",
        "source_record_id": "R-1",
        "text_field": "reason_for_recall",
        "raw_text": "Exact reason",
        "source_context": {"event_id": "E-1"},
        "baseline": {
            "primary_cause": "recall",
            "confidence": "low",
            "confidence_score": 0.0,
            "confident_keyword_match": False,
            "fallback": True,
            "collision": False,
            "matched_categories": [],
            "matched_rules": {},
            "event": {
                "primary_cause": "recall",
                "secondary_causes": [],
                "supply_chain_stage": "recall",
                "severity": "medium",
                "evidence": ["Exact reason"],
            },
        },
    }
    payload.update(updates)
    return GoldCandidate.model_validate(payload)


def test_prompt_contains_locked_human_reviewer_definitions() -> None:
    prompt = system_prompt()
    for category, definition in CATEGORY_GUIDANCE.items():
        assert f"- {category}: {definition}" in prompt


def test_prompt_distinguishes_temperature_finding_from_explicit_delay() -> None:
    prompt = system_prompt()
    assert '"during shipping"' in prompt
    assert "identify location, not a shipping delay" in prompt
    assert (
        "Use shipping_delay as primary only when the text explicitly names a delay"
        in prompt
    )
    assert "Mere occurrence during shipping/transit is not enough" in prompt


def test_validator_enforces_exact_evidence_substrings() -> None:
    raw_text = "Product was recalled for a temperature excursion during transit."
    valid = {
        "primary_cause": "shipping_delay",
        "secondary_causes": ["manufacturing_quality_problem", "recall_event"],
        "supply_chain_stage": "distribution",
        "severity": "medium",
        "evidence": ["temperature excursion during transit"],
    }
    assert (
        validate_teacher_output(json.dumps(valid), raw_text).primary_cause
        == "shipping_delay"
    )

    invalid = {**valid, "evidence": ["temperature was outside specification"]}
    with pytest.raises(TeacherValidationError, match="exact raw_text substring"):
        validate_teacher_output(json.dumps(invalid), raw_text)


def test_validator_rejects_invalid_schema_values_and_json() -> None:
    with pytest.raises(TeacherValidationError, match="invalid JSON"):
        validate_teacher_output("not-json", "not-json")
    payload = {
        "primary_cause": "not_a_category",
        "secondary_causes": [],
        "supply_chain_stage": "unknown",
        "severity": "critical",
        "evidence": ["reason"],
    }
    with pytest.raises(TeacherValidationError, match="schema validation failed"):
        validate_teacher_output(json.dumps(payload), "reason")


def test_queue_deduplicates_recall_event_text_pairs_and_excludes_gold() -> None:
    recalls = [
        {
            "recall_number": "R-1",
            "event_id": "E-1",
            "reason_for_recall": "Exact reason",
        },
        {
            "recall_number": "R-2",
            "event_id": "E-1",
            "reason_for_recall": "Exact reason",
        },
        {
            "recall_number": "R-3",
            "event_id": "E-2",
            "reason_for_recall": "Another reason",
        },
        {
            "recall_number": "R-4",
            "event_id": "E-2",
            "reason_for_recall": " Another   reason ",
        },
    ]
    shortages = [
        {
            "package_ndc": "123",
            "company_name": "Maker",
            "generic_name": "Drug",
            "initial_posting_date": "20260101",
            "shortage_reason": None,
        }
    ]
    queue = build_corpus_queue(
        recalls,
        shortages,
        [_candidate()],
        snapshot="2026-08-21_17",
    )
    recall_units = [row for row in queue if row.source == "recall"]
    assert len(recall_units) == 1
    assert recall_units[0].incident_id == "recall_event:E-2"
    assert recall_units[0].source_record_ids == ["R-3", "R-4"]
    assert recall_units[0].source_context["filing_count"] == 2

    shortage_unit = next(row for row in queue if row.source == "shortage")
    assert shortage_unit.requires_model_call is False
    label = auto_unknown_label(shortage_unit)
    assert label.annotator == "teacher"
    assert label.label_method == "policy_unknown"
    assert label.event.primary_cause == "unknown"


def test_gold_evaluation_removes_incident_or_text_leaking_fewshots() -> None:
    unit = TeacherInputRecord(
        teacher_id="gold:recall:R-1",
        snapshot="2026-08-21_17",
        dataset_scope="gold_evaluation",
        source="recall",
        source_record_ids=["R-1"],
        incident_id="recall_event:E-1",
        reason_text_id="reason:one",
        raw_text="Target",
        source_raw_text="Target",
        source_context={},
        requires_model_call=True,
    )
    examples = [
        {"incident_id": "recall_event:E-1", "reason_text_id": "reason:x"},
        {"incident_id": "recall_event:E-2", "reason_text_id": "reason:one"},
        {"incident_id": "recall_event:E-3", "reason_text_id": "reason:three"},
    ]
    assert few_shots_for_target(examples, unit) == [examples[2]]
