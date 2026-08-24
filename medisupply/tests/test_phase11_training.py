import json

from src.models.phase11_training import evaluation_metrics, validate_prediction


def test_validate_prediction_enforces_verbatim_evidence() -> None:
    payload = {
        "primary_cause": "manufacturing_quality_problem",
        "secondary_causes": [],
        "supply_chain_stage": "manufacturing",
        "severity": "medium",
        "evidence": ["exact finding"],
    }
    event, error = validate_prediction(json.dumps(payload), "FDA exact finding here")
    assert error is None
    assert event is not None

    payload["evidence"] = ["paraphrased finding"]
    event, error = validate_prediction(json.dumps(payload), "FDA exact finding here")
    assert event is None
    assert error == "evidence_not_exact_substring"


def test_unsupported_category_is_explicit_even_when_prediction_is_correct() -> None:
    rows = [
        {
            "human_primary_cause": "shipping_delay",
            "predicted_primary_cause": "shipping_delay",
            "primary_cause_correct": True,
            "validation_error": None,
            "outcome_type": "unsupported_category",
        }
    ]
    metrics = evaluation_metrics(rows)
    assert metrics["by_category"]["shipping_delay"]["support_outcome"] == (
        "unsupported_category"
    )
    assert metrics["unsupported_categories"]["accuracy_percentage"] == 100.0
