import unittest

from pydantic import ValidationError

from src.models.create_gold_sample import TARGETS
from src.models.gold_dataset import GoldLabelRecord, gold_metrics, stratified_split
from src.models.schema import DisruptionEvent, PRIMARY_CAUSE_TAXONOMY_VERSION


class GoldDatasetTest(unittest.TestCase):
    @staticmethod
    def label(index: int, category: str, baseline: str | None = None) -> GoldLabelRecord:
        return GoldLabelRecord(
            candidate_id=f"candidate-{index}",
            taxonomy_version=PRIMARY_CAUSE_TAXONOMY_VERSION,
            snapshot="2026-08-21_17",
            sampling_stratum=category,
            source="recall",
            source_record_id=f"R-{index}",
            text_field="reason_for_recall",
            raw_text=f"Reason {index}",
            baseline_primary_cause=baseline or category,
            baseline_confidence="high",
            baseline_collision=False,
            event=DisruptionEvent(
                primary_cause=category,
                supply_chain_stage="test_stage",
                severity="medium",
                evidence=[f"FDA reason_for_recall: Reason {index}"],
            ),
            annotator="tester",
            labeled_at="2026-08-21T12:00:00-04:00",
        )

    def test_split_is_exact_and_covers_every_sufficient_category(self) -> None:
        records = []
        index = 0
        for category, count in TARGETS.items():
            for _ in range(count):
                records.append(self.label(index, category))
                index += 1

        splits = stratified_split(records)

        self.assertEqual(len(splits["train"]), 280)
        self.assertEqual(len(splits["validation"]), 60)
        self.assertEqual(len(splits["test"]), 60)
        for category, count in TARGETS.items():
            if count >= 3:
                for split in splits.values():
                    self.assertIn(category, {row.event.primary_cause for row in split})

    def test_metrics_measure_agreement_by_human_final_category(self) -> None:
        records = [
            self.label(1, "recall"),
            self.label(2, "recall", baseline="manufacturing_quality_problem"),
            self.label(3, "unknown"),
        ]
        splits = {"train": records, "validation": [], "test": []}

        metrics = gold_metrics(records, splits)

        self.assertEqual(metrics["baseline_agreement"]["agreed"], 2)
        recall = metrics["baseline_agreement"]["by_final_category"]["recall"]
        self.assertEqual(recall["agreed"], 1)
        self.assertEqual(recall["total"], 2)
        self.assertEqual(len(metrics["disagreements"]), 1)

    def test_rejects_wrong_taxonomy_version(self) -> None:
        payload = self.label(1, "recall").model_dump()
        payload["taxonomy_version"] = "phase8"
        with self.assertRaises(ValidationError):
            GoldLabelRecord.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
