import unittest

from src.models.gold_dataset import BaselineSuggestion, GoldCandidate, GoldLabelRecord
from src.models.phase12_dataset import (
    build_phase12_examples,
    cap_examples,
    category_support,
    grouped_stratified_split,
    incident_identity,
    normalize_reason_text,
    reason_text_id,
)
from src.models.schema import PRIMARY_CAUSE_TAXONOMY_VERSION, DisruptionEvent


class Phase12DatasetTest(unittest.TestCase):
    @staticmethod
    def candidate(
        index: int,
        *,
        event_id: str,
        text: str,
        category: str = "manufacturing_quality_problem",
    ) -> GoldCandidate:
        event = DisruptionEvent(
            primary_cause=category,
            secondary_causes=["recall_event"],
            supply_chain_stage="manufacturing",
            severity="medium",
            evidence=[f"FDA reason_for_recall: {text}"],
        )
        return GoldCandidate(
            candidate_id=f"recall:R-{index}",
            sequence=index,
            taxonomy_version=PRIMARY_CAUSE_TAXONOMY_VERSION,
            snapshot="2026-08-21_17",
            sampling_stratum=category,
            selection_reason="baseline_confident",
            source="recall",
            source_record_id=f"R-{index}",
            text_field="reason_for_recall",
            raw_text=text,
            source_context={"event_id": event_id, "classification": "Class II"},
            baseline=BaselineSuggestion(
                primary_cause=category,
                confidence="high",
                confidence_score=1.0,
                confident_keyword_match=True,
                fallback=False,
                collision=False,
                matched_categories=[category],
                matched_rules={category: ["test_rule"]},
                event=event,
            ),
        )

    @staticmethod
    def label(candidate: GoldCandidate) -> GoldLabelRecord:
        return GoldLabelRecord(
            candidate_id=candidate.candidate_id,
            taxonomy_version=candidate.taxonomy_version,
            snapshot=candidate.snapshot,
            sampling_stratum=candidate.sampling_stratum,
            source=candidate.source,
            source_record_id=candidate.source_record_id,
            text_field=candidate.text_field,
            raw_text=candidate.raw_text,
            baseline_primary_cause=candidate.baseline.primary_cause,
            baseline_confidence=candidate.baseline.confidence,
            baseline_collision=candidate.baseline.collision,
            event=candidate.baseline.event,
            annotator="tester",
            labeled_at="2026-08-23T12:00:00-04:00",
        )

    def test_reason_identity_normalizes_case_unicode_and_whitespace(self) -> None:
        first = " Lack  of\nAssurance of Sterility "
        second = "lack of assurance of sterility"
        self.assertEqual(normalize_reason_text(first), second)
        self.assertEqual(reason_text_id(first), reason_text_id(second))

    def test_recall_incident_identity_uses_event_id(self) -> None:
        first = self.candidate(1, event_id="65479", text="Reason one")
        second = self.candidate(2, event_id="65479", text="Reason two")
        self.assertEqual(incident_identity(first), incident_identity(second))
        self.assertEqual(
            incident_identity(first), ("recall_event:65479", "fda_event_id")
        )

    def test_weights_give_each_exact_text_one_total_vote(self) -> None:
        candidates = [
            self.candidate(1, event_id="A", text="Repeated reason"),
            self.candidate(2, event_id="A", text="Repeated reason"),
            self.candidate(3, event_id="B", text="Repeated reason"),
            self.candidate(4, event_id="C", text="Distinct reason"),
        ]
        examples = build_phase12_examples(
            candidates, [self.label(candidate) for candidate in candidates]
        )
        repeated = [row for row in examples if row.raw_text == "Repeated reason"]
        distinct = [row for row in examples if row.raw_text == "Distinct reason"]

        self.assertEqual([row.sample_weight for row in repeated], [0.25, 0.25, 0.5])
        self.assertAlmostEqual(sum(row.sample_weight for row in repeated), 1.0)
        self.assertEqual(distinct[0].sample_weight, 1.0)

    def test_leakage_groups_are_transitive_across_incident_and_text(self) -> None:
        candidates = [
            self.candidate(1, event_id="A", text="Shared text"),
            self.candidate(2, event_id="A", text="Second text"),
            self.candidate(3, event_id="B", text="Second text"),
            self.candidate(4, event_id="C", text="Independent text"),
        ]
        examples = build_phase12_examples(
            candidates, [self.label(candidate) for candidate in candidates]
        )
        self.assertEqual(len({row.split_group_id for row in examples[:3]}), 1)
        self.assertNotEqual(examples[0].split_group_id, examples[3].split_group_id)

    def test_grouped_split_never_crosses_a_leakage_group(self) -> None:
        candidates = []
        for index in range(1, 16):
            candidates.append(
                self.candidate(
                    index,
                    event_id=f"E-{index}",
                    text=f"Reason {index}",
                    category=(
                        "labeling_packaging_error"
                        if index % 2
                        else "manufacturing_quality_problem"
                    ),
                )
            )
        examples = build_phase12_examples(
            candidates, [self.label(candidate) for candidate in candidates]
        )
        first = grouped_stratified_split(examples)
        second = grouped_stratified_split(examples)

        location = {}
        for split, rows in first.items():
            for row in rows:
                previous = location.setdefault(row.split_group_id, split)
                self.assertEqual(previous, split)
        self.assertEqual(
            {
                split: [row.candidate_id for row in rows]
                for split, rows in first.items()
            },
            {
                split: [row.candidate_id for row in rows]
                for split, rows in second.items()
            },
        )
        self.assertEqual(sum(map(len, first.values())), len(examples))

    def test_cap_keeps_one_filing_per_incident_and_five_events_per_text(self) -> None:
        candidates = []
        index = 1
        for event_index in range(7):
            for _ in range(2):
                candidates.append(
                    self.candidate(index, event_id=f"E-{event_index}", text="Same")
                )
                index += 1
        examples = build_phase12_examples(
            candidates, [self.label(candidate) for candidate in candidates]
        )

        capped = cap_examples(examples, max_events_per_text=5)

        self.assertEqual(len(capped), 5)
        self.assertEqual(len({row.incident_id for row in capped}), 5)
        self.assertTrue(all(row.sample_weight == 1.0 for row in capped))
        self.assertTrue(all(row.weighting_method == "cap" for row in capped))

    def test_support_flags_one_incident_category_as_insufficient(self) -> None:
        support = category_support(
            [
                ("adverse_event_signal", "event:1", "text:1"),
                ("adverse_event_signal", "event:1", "text:1"),
            ]
        )
        self.assertEqual(
            support["adverse_event_signal"]["support_status"],
            "insufficient_independent_examples",
        )

    def test_duplicate_gold_label_ids_are_rejected(self) -> None:
        candidate = self.candidate(1, event_id="A", text="Reason")
        label = self.label(candidate)
        with self.assertRaisesRegex(ValueError, "duplicate candidate IDs"):
            build_phase12_examples([candidate], [label, label])


if __name__ == "__main__":
    unittest.main()
