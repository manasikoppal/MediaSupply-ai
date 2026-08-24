from src.models.gold_dataset import GoldCandidate, GoldLabelRecord
from src.models.phase11_dataset import build_phase11_pool, phase11_metrics
from src.models.phase12_dataset import reason_text_id
from src.models.schema import DisruptionEvent, PrimaryCause
from src.models.teacher_labeling import TeacherLabelRecord


def _event(
    cause: PrimaryCause = "manufacturing_quality_problem",
) -> DisruptionEvent:
    return DisruptionEvent(
        primary_cause=cause,
        secondary_causes=[],
        supply_chain_stage="manufacturing",
        severity="medium",
        evidence=["reason"],
    )


def _teacher(
    teacher_id: str, incident_id: str, text_id: str, *, method: str = "claude"
) -> TeacherLabelRecord:
    return TeacherLabelRecord.model_construct(
        teacher_id=teacher_id,
        taxonomy_version="phase9_v1",
        snapshot="snapshot",
        dataset_scope="corpus",
        source="recall",
        source_record_ids=[teacher_id],
        incident_id=incident_id,
        reason_text_id=text_id,
        raw_text="reason",
        source_raw_text="reason" if method == "claude" else None,
        source_context={},
        event=_event(
            "unknown" if method == "policy_unknown" else "manufacturing_quality_problem"
        ),
        annotator="teacher",
        label_method=method,
        model="teacher" if method == "claude" else None,
        prompt_version="v1",
        labeled_at="now",
        validation_status="passed",
        request_id=None,
        usage=None,
    )


def test_gold_connected_components_are_quarantined() -> None:
    candidate = GoldCandidate.model_construct(
        candidate_id="gold-1",
        source="recall",
        source_record_id="gold-record",
        source_context={"event_id": "gold-event"},
    )
    gold = GoldLabelRecord.model_construct(
        candidate_id="gold-1",
        taxonomy_version="phase9_v1",
        snapshot="snapshot",
        source="recall",
        source_record_id="gold-record",
        raw_text="shared reason",
        event=_event(),
    )
    # First teacher row shares the gold text. The second is linked transitively by
    # incident, so both must be excluded even though the second text is different.
    shared_text_id = reason_text_id("shared reason")
    teacher = [
        _teacher("teacher-1", "recall_event:other", shared_text_id),
        _teacher("teacher-2", "recall_event:other", "reason:different"),
        _teacher("teacher-3", "recall_event:safe", "reason:safe"),
    ]

    pool, splits = build_phase11_pool(teacher, [candidate], [gold])

    assert {row.candidate_id for row in splits["excluded"]} == {
        "teacher-1",
        "teacher-2",
    }
    assert sum(len(splits[name]) for name in ("train", "validation", "test")) == 1
    metrics = phase11_metrics(pool, splits)
    assert metrics["leakage_checks"]["gold_train_group_overlap"] == 0
    assert metrics["leakage_checks"]["cross_training_split_group_overlap"] == 0


def test_origin_tags_distinguish_teacher_and_deterministic() -> None:
    candidate = GoldCandidate.model_construct(
        candidate_id="gold-1",
        source="recall",
        source_record_id="gold-record",
        source_context={"event_id": "gold-event"},
    )
    gold = GoldLabelRecord.model_construct(
        candidate_id="gold-1",
        taxonomy_version="phase9_v1",
        snapshot="snapshot",
        source="recall",
        source_record_id="gold-record",
        raw_text="gold reason",
        event=_event(),
    )
    pool, _ = build_phase11_pool(
        [
            _teacher("teacher", "recall_event:t", "reason:t"),
            _teacher(
                "deterministic",
                "recall_event:d",
                "reason:d",
                method="policy_unknown",
            ),
        ],
        [candidate],
        [gold],
    )

    origins = {row.candidate_id: row.annotation_origin for row in pool}
    assert origins == {
        "gold-1": "gold",
        "teacher": "teacher",
        "deterministic": "deterministic",
    }
