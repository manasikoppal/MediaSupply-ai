import unittest

from pydantic import ValidationError

from src.models.event_examples import HAND_MAPPED_EXAMPLES, validated_hand_mapped_events
from src.models.schema import DisruptionEvent


class DisruptionEventTest(unittest.TestCase):
    def test_all_hand_mapped_examples_validate(self) -> None:
        events = validated_hand_mapped_events()
        self.assertEqual(len(events), len(HAND_MAPPED_EXAMPLES))
        self.assertEqual(len(events), 16)

    def test_secondary_causes_default_is_not_shared(self) -> None:
        first = DisruptionEvent(
            primary_cause="unknown",
            supply_chain_stage="unknown",
            severity="low",
            evidence=["FDA shortage_reason not supplied"],
        )
        second = DisruptionEvent(
            primary_cause="unknown",
            supply_chain_stage="unknown",
            severity="low",
            evidence=["FDA shortage_reason not supplied"],
        )
        first.secondary_causes.append("example")
        self.assertEqual(second.secondary_causes, [])

    def test_invalid_cause_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            DisruptionEvent(
                primary_cause="labeling_error",
                supply_chain_stage="packaging_labeling",
                severity="low",
                evidence=["Example"],
            )

    def test_revised_taxonomy_categories_are_accepted(self) -> None:
        for cause in (
            "labeling_packaging_error",
            "regulatory_noncompliance",
            "adverse_event_signal",
        ):
            with self.subTest(cause=cause):
                event = DisruptionEvent(
                    primary_cause=cause,
                    supply_chain_stage="test_stage",
                    severity="medium",
                    evidence=["FDA example"],
                )
                self.assertEqual(event.primary_cause, cause)

    def test_evidence_is_required_and_must_not_be_blank(self) -> None:
        for evidence in ([], ["   "]):
            with self.subTest(evidence=evidence), self.assertRaises(ValidationError):
                DisruptionEvent(
                    primary_cause="unknown",
                    supply_chain_stage="unknown",
                    severity="low",
                    evidence=evidence,
                )

    def test_extra_fields_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            DisruptionEvent(
                primary_cause="unknown",
                supply_chain_stage="unknown",
                severity="low",
                evidence=["Example"],
                classifier_confidence=0.5,
            )


if __name__ == "__main__":
    unittest.main()
