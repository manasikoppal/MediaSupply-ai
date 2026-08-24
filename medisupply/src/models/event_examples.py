"""Human-reviewed Phase 7 mappings from real FDA records.

These examples are fixtures for validating the event schema and reviewing the
taxonomy. They are not classifier rules or an automatically labeled dataset.
"""

from __future__ import annotations

from typing import Any

from .schema import DisruptionEvent


HAND_MAPPED_EXAMPLES: tuple[dict[str, Any], ...] = (
    {
        "source": "shortages",
        "source_id": "43547-606-10",
        "source_text": "Shortage of an active ingredient",
        "event": {
            "primary_cause": "active_ingredient_shortage",
            "secondary_causes": [],
            "supply_chain_stage": "raw_material_sourcing",
            "severity": "medium",
            "evidence": [
                "FDA shortage_reason: Shortage of an active ingredient",
                "FDA status: Current",
                "FDA availability: Limited Availability",
            ],
        },
    },
    {
        "source": "shortages",
        "source_id": "0054-3298-63",
        "source_text": "Shortage of an inactive ingredient component",
        "event": {
            "primary_cause": "inactive_ingredient_shortage",
            "secondary_causes": [],
            "supply_chain_stage": "raw_material_sourcing",
            "severity": "medium",
            "evidence": [
                "FDA shortage_reason: Shortage of an inactive ingredient component",
                "FDA status: Current",
                "FDA availability: Limited Availability",
            ],
        },
    },
    {
        "source": "shortages",
        "source_id": "0338-3993-01",
        "source_text": "Requirements related to complying with good manufacturing practices",
        "event": {
            "primary_cause": "manufacturing_quality_problem",
            "secondary_causes": ["good_manufacturing_practice_compliance"],
            "supply_chain_stage": "manufacturing",
            "severity": "high",
            "evidence": [
                "FDA shortage_reason: Requirements related to complying with good manufacturing practices",
                "FDA status: Current",
                "FDA availability: Unavailable",
            ],
        },
    },
    {
        "source": "shortages",
        "source_id": "63323-464-31",
        "source_text": "Demand increase for the drug",
        "event": {
            "primary_cause": "demand_increase",
            "secondary_causes": [],
            "supply_chain_stage": "demand_planning",
            "severity": "high",
            "evidence": [
                "FDA shortage_reason: Demand increase for the drug",
                "FDA status: Current",
                "FDA availability: Unavailable",
            ],
        },
    },
    {
        "source": "shortages",
        "source_id": "65219-065-05",
        "source_text": "Delay in shipping of the drug",
        "event": {
            "primary_cause": "shipping_delay",
            "secondary_causes": [],
            "supply_chain_stage": "distribution",
            "severity": "high",
            "evidence": [
                "FDA shortage_reason: Delay in shipping of the drug",
                "FDA status: Current",
                "FDA availability: Unavailable",
            ],
        },
    },
    {
        "source": "shortages",
        "source_id": "0378-4430-01",
        "source_text": "Regulatory delay",
        "event": {
            "primary_cause": "regulatory_delay",
            "secondary_causes": ["contract_manufacturing_delay"],
            "supply_chain_stage": "regulatory_review",
            "severity": "high",
            "evidence": [
                "FDA shortage_reason: Regulatory delay",
                "FDA related_info: Anticipated availability: Q4 2026; Delay is due to manufacturing delays at the contract manufacturing facility.",
                "FDA availability: Unavailable",
            ],
        },
    },
    {
        "source": "shortages",
        "source_id": "0574-4022-35",
        "source_text": "Discontinuation of the manufacture of the drug",
        "event": {
            "primary_cause": "product_discontinuation",
            "secondary_causes": [],
            "supply_chain_stage": "product_lifecycle",
            "severity": "high",
            "evidence": [
                "FDA shortage_reason: Discontinuation of the manufacture of the drug",
                "FDA status: Current",
                "FDA availability: Unavailable",
            ],
        },
    },
    {
        "source": "shortages",
        "source_id": "0409-0152-24",
        "source_text": "Other",
        "event": {
            "primary_cause": "unknown",
            "secondary_causes": ["fda_reason_other"],
            "supply_chain_stage": "unknown",
            "severity": "medium",
            "evidence": [
                "FDA shortage_reason: Other",
                "FDA status: Current",
                "FDA availability: Limited Availability",
            ],
        },
    },
    {
        "source": "recalls",
        "source_id": "D-0115-2026",
        "source_text": "Lack of Assurance of Sterility",
        "event": {
            "primary_cause": "manufacturing_quality_problem",
            "secondary_causes": ["sterility_assurance_failure", "recall_event"],
            "supply_chain_stage": "manufacturing",
            "severity": "medium",
            "evidence": [
                "FDA reason_for_recall: Lack of Assurance of Sterility",
                "FDA classification: Class II",
                "FDA status: Ongoing",
            ],
        },
    },
    {
        "source": "recalls",
        "source_id": "D-0276-2024",
        "source_text": "Failed Dissolution Specifications",
        "event": {
            "primary_cause": "manufacturing_quality_problem",
            "secondary_causes": ["failed_dissolution_specification", "recall_event"],
            "supply_chain_stage": "manufacturing",
            "severity": "medium",
            "evidence": [
                "FDA reason_for_recall: Failed Dissolution Specifications",
                "FDA classification: Class II",
                "FDA status: Terminated",
            ],
        },
    },
    {
        "source": "recalls",
        "source_id": "D-707-2015",
        "source_text": "Penicillin Cross Contamination: All lots of all products repackaged and distributed between 01/05/12 and 02/12/15 are being recalled because they were repackaged in a facility with penicillin products without adequate separation which could introduce the potential for cross contamination with penicillin.",
        "event": {
            "primary_cause": "manufacturing_quality_problem",
            "secondary_causes": ["cross_contamination", "recall_event"],
            "supply_chain_stage": "manufacturing",
            "severity": "medium",
            "evidence": [
                "FDA reason_for_recall: Penicillin Cross Contamination: All lots of all products repackaged and distributed between 01/05/12 and 02/12/15 are being recalled because they were repackaged in a facility with penicillin products without adequate separation which could introduce the potential for cross contamination with penicillin.",
                "FDA classification: Class II",
                "FDA status: Terminated",
            ],
        },
    },
    {
        "source": "recalls",
        "source_id": "D-0429-2021",
        "source_text": "CGMP Deviations: Intermittent exposure to temperature excursion during storage.",
        "event": {
            "primary_cause": "recall",
            "secondary_causes": [
                "temperature_storage_excursion",
                "cgmp_deviation",
            ],
            "supply_chain_stage": "storage",
            "severity": "medium",
            "evidence": [
                "FDA reason_for_recall: CGMP Deviations: Intermittent exposure to temperature excursion during storage.",
                "FDA classification: Class II",
                "FDA status: Terminated",
            ],
        },
    },
    {
        "source": "recalls",
        "source_id": "D-0930-2017",
        "source_text": "Labeling: Incorrect or Missing Lot and/or Exp Date",
        "event": {
            "primary_cause": "labeling_packaging_error",
            "secondary_causes": ["labeling_error", "recall_event"],
            "supply_chain_stage": "packaging_labeling",
            "severity": "low",
            "evidence": [
                "FDA reason_for_recall: Labeling: Incorrect or Missing Lot and/or Exp Date",
                "FDA classification: Class III",
                "FDA status: Terminated",
            ],
        },
    },
    {
        "source": "recalls",
        "source_id": "D-0887-2016",
        "source_text": "Marketed Without An Approved NDA/ANDA",
        "event": {
            "primary_cause": "regulatory_noncompliance",
            "secondary_causes": ["unapproved_marketing", "recall_event"],
            "supply_chain_stage": "regulatory_compliance",
            "severity": "high",
            "evidence": [
                "FDA reason_for_recall: Marketed Without An Approved NDA/ANDA",
                "FDA classification: Class I",
                "FDA status: Terminated",
            ],
        },
    },
    {
        "source": "recalls",
        "source_id": "D-241-2014",
        "source_text": "The firm received seven reports of adverse reactions in the form of skin abscesses potentially linked to compounded preservative-free methylprednisolone 80mg/ml 10 ml vials.",
        "event": {
            "primary_cause": "adverse_event_signal",
            "secondary_causes": ["recall_event"],
            "supply_chain_stage": "post_market_surveillance",
            "severity": "medium",
            "evidence": [
                "FDA reason_for_recall: The firm received seven reports of adverse reactions in the form of skin abscesses potentially linked to compounded preservative-free methylprednisolone 80mg/ml 10 ml vials.",
                "FDA classification: Class II",
                "FDA status: Terminated",
            ],
        },
    },
    {
        "source": "recalls",
        "source_id": "D-0818-2023",
        "source_text": "CGMP Deviations: Firm went out of business and could no longer continue stability studies.",
        "event": {
            "primary_cause": "manufacturing_quality_problem",
            "secondary_causes": [
                "stability_monitoring_failure",
                "business_closure",
                "recall_event",
            ],
            "supply_chain_stage": "manufacturing_quality",
            "severity": "medium",
            "evidence": [
                "FDA reason_for_recall: CGMP Deviations: Firm went out of business and could no longer continue stability studies.",
                "FDA classification: Class II",
                "FDA status: Ongoing",
            ],
        },
    },
)


def validated_hand_mapped_events() -> list[DisruptionEvent]:
    """Validate every human-reviewed event payload against the Phase 7 model."""
    return [DisruptionEvent.model_validate(example["event"]) for example in HAND_MAPPED_EXAMPLES]
