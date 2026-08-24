import json
from pathlib import Path

import pytest

from scripts.refresh_dashboard_data import promote_dashboard_data

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_promotion_validates_then_writes_atomically(tmp_path: Path) -> None:
    destination = tmp_path / "dashboard" / "current.json"
    snapshot, record_count = promote_dashboard_data(
        REPOSITORY_ROOT / "reports" / "intelligence_engine.json",
        destination,
    )
    payload = json.loads(destination.read_text())
    assert payload["snapshot"] == snapshot
    assert len(payload["all_scored_current_shortages"]) == record_count
    assert record_count > 0


def test_invalid_dashboard_artifact_does_not_replace_active_data(
    tmp_path: Path,
) -> None:
    source_payload = json.loads(
        (REPOSITORY_ROOT / "reports" / "intelligence_engine.json").read_text()
    )
    unknown = next(
        item
        for item in source_payload["all_scored_current_shortages"]
        if item["primary_cause"] == "unknown"
    )
    unknown["unknown_reason"] = None
    source = tmp_path / "invalid.json"
    source.write_text(json.dumps(source_payload))
    destination = tmp_path / "current.json"
    destination.write_text("previous validated dashboard data")

    with pytest.raises(ValueError, match="provenance"):
        promote_dashboard_data(source, destination)
    assert destination.read_text() == "previous validated dashboard data"


def test_daily_wrapper_is_fail_fast_free_pipeline() -> None:
    wrapper = (REPOSITORY_ROOT / "scripts" / "run_daily_ingestion.sh").read_text()
    ordered_commands = [
        "src/ingestion/run_ingestion.py",
        "src/cleaning/run_cleaning.py",
        "src/graph/build_graph.py",
        "scripts/run_intelligence_engine.py",
        "scripts/refresh_dashboard_data.py",
    ]
    positions = [wrapper.index(command) for command in ordered_commands]
    assert positions == sorted(positions)
    assert "run_teacher_labeling.py" not in wrapper
    assert "PIPELINE STOPPED" in wrapper
    for log_name in (
        "ingestion.log",
        "cleaning.log",
        "knowledge_graph.log",
        "intelligence_engine.log",
        "dashboard_refresh.log",
    ):
        assert log_name in wrapper
