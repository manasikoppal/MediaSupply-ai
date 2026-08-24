"""Locked Phase 9 taxonomy definitions used by human and teacher labeling."""

from __future__ import annotations

from .schema import PrimaryCause

CATEGORY_GUIDANCE: dict[PrimaryCause, str] = {
    "active_ingredient_shortage": "Explicit shortage/unavailability of the API or active ingredient.",
    "inactive_ingredient_shortage": "Explicit shortage of an excipient or inactive component.",
    "manufacturing_quality_problem": "Sterility, contamination, potency, stability, specification, process, or product-integrity failure.",
    "manufacturing_capacity": "Insufficient production throughput, equipment, facility, or manufacturing capacity without a quality defect as the primary issue.",
    "regulatory_delay": "A regulatory review or approval delay that postpones supply.",
    "regulatory_noncompliance": "Unapproved, misbranded, unregistered, or otherwise noncompliant marketing/manufacture.",
    "labeling_packaging_error": "Incorrect/missing labels, inserts, dates, packaging, or wrong-product presentation.",
    "shipping_delay": "Transport, freight, transit, logistics, or distribution delay.",
    "demand_increase": "Demand growth or surge is the stated initiating cause.",
    "product_discontinuation": "Explicit cessation of the drug product or its manufacture—not merely stopped testing.",
    "adverse_event_signal": "Reported patient harm/safety signal when the text does not establish a more specific root cause.",
    "recall": "Recall event whose stated reason falls outside the locked causal taxonomy.",
    "unknown": "FDA supplied no causal reason or only a placeholder such as 'Other'.",
}
