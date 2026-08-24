import unittest

from scripts.label_gold_dataset import (
    _build_review_units,
    _fast_mode_eligible,
    _make_label,
)
from src.models.gold_dataset import BaselineSuggestion, GoldCandidate
from src.models.schema import PRIMARY_CAUSE_TAXONOMY_VERSION, DisruptionEvent


class FastModeEligibilityTest(unittest.TestCase):
    @staticmethod
    def candidate() -> GoldCandidate:
        event = DisruptionEvent(
            primary_cause="manufacturing_quality_problem",
            secondary_causes=["recall_event"],
            supply_chain_stage="manufacturing",
            severity="medium",
            evidence=["FDA reason_for_recall: Lack of Assurance of Sterility"],
        )
        return GoldCandidate(
            candidate_id="recall:R-1",
            sequence=1,
            taxonomy_version=PRIMARY_CAUSE_TAXONOMY_VERSION,
            snapshot="2026-08-21_17",
            sampling_stratum="manufacturing_quality_problem",
            selection_reason="baseline_confident",
            source="recall",
            source_record_id="R-1",
            text_field="reason_for_recall",
            raw_text="Lack of Assurance of Sterility",
            source_context={},
            baseline=BaselineSuggestion(
                primary_cause="manufacturing_quality_problem",
                confidence="high",
                confidence_score=1.0,
                confident_keyword_match=True,
                fallback=False,
                collision=False,
                matched_categories=["manufacturing_quality_problem"],
                matched_rules={"manufacturing_quality_problem": ["sterility"]},
                event=event,
            ),
        )

    def test_only_exact_confident_non_collision_candidate_is_eligible(self) -> None:
        candidate = self.candidate()
        self.assertTrue(_fast_mode_eligible(candidate))

        cases = (
            candidate.model_copy(update={"selection_reason": "baseline_low_confidence"}),
            candidate.model_copy(update={"selection_reason": "baseline_collision_boundary"}),
            candidate.model_copy(
                update={"selection_reason": "targeted_capacity_boundary_review"}
            ),
            candidate.model_copy(
                update={
                    "baseline": candidate.baseline.model_copy(
                        update={"confidence_score": 0.999}
                    )
                }
            ),
            candidate.model_copy(
                update={
                    "baseline": candidate.baseline.model_copy(update={"collision": True})
                }
            ),
        )
        for ineligible in cases:
            with self.subTest(reason=ineligible.selection_reason):
                self.assertFalse(_fast_mode_eligible(ineligible))

    def test_grouped_mode_only_groups_safe_exact_text_duplicates(self) -> None:
        first = self.candidate()
        second = first.model_copy(
            deep=True,
            update={
                "candidate_id": "recall:R-2",
                "sequence": 2,
                "source_record_id": "R-2",
            },
        )
        unsafe = first.model_copy(
            deep=True,
            update={
                "candidate_id": "recall:R-3",
                "sequence": 3,
                "source_record_id": "R-3",
                "selection_reason": "baseline_low_confidence",
            },
        )
        unsafe_duplicate = unsafe.model_copy(
            deep=True,
            update={
                "candidate_id": "recall:R-4",
                "sequence": 4,
                "source_record_id": "R-4",
            },
        )

        units = _build_review_units(
            [first, second, unsafe, unsafe_duplicate], grouped_mode=True
        )

        self.assertEqual(
            [[candidate.candidate_id for candidate in unit] for unit in units],
            [["recall:R-1", "recall:R-2"], ["recall:R-3"], ["recall:R-4"]],
        )

    def test_group_approval_preserves_each_candidates_event(self) -> None:
        first = self.candidate()
        second = first.model_copy(
            deep=True,
            update={
                "candidate_id": "recall:R-2",
                "sequence": 2,
                "source_record_id": "R-2",
                "baseline": first.baseline.model_copy(
                    deep=True,
                    update={
                        "event": first.baseline.event.model_copy(
                            deep=True,
                            update={"severity": "high"},
                        )
                    },
                ),
            },
        )

        labels = [
            _make_label(candidate, annotator="tester", choice="__accept__")
            for candidate in (first, second)
        ]

        self.assertEqual(
            [label.candidate_id for label in labels], ["recall:R-1", "recall:R-2"]
        )
        self.assertEqual([label.event.severity for label in labels], ["medium", "high"])

    def test_grouping_can_be_disabled(self) -> None:
        first = self.candidate()
        second = first.model_copy(
            deep=True,
            update={
                "candidate_id": "recall:R-2",
                "sequence": 2,
                "source_record_id": "R-2",
            },
        )

        units = _build_review_units([first, second], grouped_mode=False)

        self.assertEqual([len(unit) for unit in units], [1, 1])


if __name__ == "__main__":
    unittest.main()
