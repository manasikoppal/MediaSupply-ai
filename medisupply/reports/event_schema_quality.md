# Phase 7 Event Schema Review

Generated: 2026-08-21  
Taxonomy revised after Phase 8: 2026-08-21  
Source snapshot: `data/snapshots/2026-08-21_17`  
Records reviewed: 1,628 shortages and 17,876 recalls

The locked Phase 9 taxonomy has 13 primary causes. Phase 8 added
`labeling_packaging_error`, `regulatory_noncompliance`, and
`adverse_event_signal`; FDA shortage reason “Other” remains `unknown`.

## Recommendation for shortages with no reason

Retain the 1,205 reason-missing shortage records as disruption events with
`primary_cause="unknown"`, but exclude them from supervised cause-classifier
training and evaluation in Phase 9.

This gives the records two distinct uses:

- **Event tracking and inference:** keep them, because the FDA has still
  reported a real shortage. Evidence may state that `shortage_reason` was not
  supplied and may quote `operational_context` or `discontinuation_context`
  with its field provenance.
- **Cause-label training:** exclude them from positive cause labels. Neither
  context field should be treated as a cause or used to backfill
  `shortage_reason`. They can later form a separate abstention/unknown test set.

Excluding these records everywhere would discard 74.02% of shortage coverage
and bias the corpus toward events FDA chose to explain. Treating their context
as a gold cause would create unsupported labels. No inclusion or training rule
has been implemented in this phase; this is a recommendation for review.

## Schema decisions

The Pydantic model is in `src/models/schema.py`. It uses the 13 locked
primary causes, requires a nonblank supply-chain stage and at least one evidence
item, rejects extra fields, and gives each instance its own empty
`secondary_causes` list.

For recall records, `primary_cause` means the underlying mechanism when the FDA
text supports one. For example, lack of sterility is a
`manufacturing_quality_problem`, while `recall_event` is retained as a
secondary cause. The primary value `recall` is the fallback when the record
establishes a recall but its reason is outside the current causal taxonomy.
Making every recall's primary cause `recall` would erase the main source of
cause training signal.

The hand mappings use FDA recall class as a review aid for severity (`Class I`
high, `Class II` medium, `Class III` low). Shortage examples use current
availability as a review aid (`Unavailable` high and `Limited Availability`
medium). These are example annotations, not production labeling rules.

## Hand-mapped shortage examples

All source text below appears in the snapshot and all event payloads validate
against the Pydantic model.

| NDC | FDA `shortage_reason` | Primary cause | Stage | Severity |
|---|---|---|---|---|
| `43547-606-10` | Shortage of an active ingredient | `active_ingredient_shortage` | `raw_material_sourcing` | medium |
| `0054-3298-63` | Shortage of an inactive ingredient component | `inactive_ingredient_shortage` | `raw_material_sourcing` | medium |
| `0338-3993-01` | Requirements related to complying with good manufacturing practices | `manufacturing_quality_problem` | `manufacturing` | high |
| `63323-464-31` | Demand increase for the drug | `demand_increase` | `demand_planning` | high |
| `65219-065-05` | Delay in shipping of the drug | `shipping_delay` | `distribution` | high |
| `0378-4430-01` | Regulatory delay | `regulatory_delay` | `regulatory_review` | high |
| `0574-4022-35` | Discontinuation of the manufacture of the drug | `product_discontinuation` | `product_lifecycle` | high |
| `0409-0152-24` | Other | `unknown` | `unknown` | medium |

The regulatory-delay example also says in `related_info` that a contract
manufacturer is delayed. The FDA's explicit reason remains primary; the extra
detail is represented as `contract_manufacturing_delay` in
`secondary_causes`, not as a replacement label.

## Hand-mapped recall examples

| Recall | FDA `reason_for_recall` (abridged) | Primary cause | Secondary cause or gap | Stage | Severity |
|---|---|---|---|---|---|
| `D-0115-2026` | Lack of Assurance of Sterility | `manufacturing_quality_problem` | `sterility_assurance_failure` | `manufacturing` | medium |
| `D-0276-2024` | Failed Dissolution Specifications | `manufacturing_quality_problem` | `failed_dissolution_specification` | `manufacturing` | medium |
| `D-707-2015` | Penicillin Cross Contamination | `manufacturing_quality_problem` | `cross_contamination` | `manufacturing` | medium |
| `D-0429-2021` | Temperature excursion during storage | `recall` | `temperature_storage_excursion` | `storage` | medium |
| `D-0930-2017` | Incorrect or missing lot/expiration date | `labeling_packaging_error` | `labeling_error` | `packaging_labeling` | low |
| `D-0887-2016` | Marketed Without An Approved NDA/ANDA | `regulatory_noncompliance` | `unapproved_marketing` | `regulatory_compliance` | high |
| `D-241-2014` | Reports of adverse reactions/skin abscesses | `adverse_event_signal` | `recall_event` | `post_market_surveillance` | medium |
| `D-0818-2023` | Firm closed and could not continue stability studies | `manufacturing_quality_problem` | `stability_monitoring_failure`, `business_closure` | `manufacturing_quality` | medium |

The complete, verbatim evidence strings and event payloads are kept in
`src/models/event_examples.py`.

## Exact shortage category distribution

Only 423 shortages have a populated reason. FDA uses eight distinct reason
phrases in this snapshot, so these mappings are exact rather than inferred.

| Primary cause | Records | Share of 423 reason-populated records |
|---|---:|---:|
| `unknown` (FDA reason is “Other”) | 142 | 33.57% |
| `demand_increase` | 102 | 24.11% |
| `product_discontinuation` | 71 | 16.78% |
| `active_ingredient_shortage` | 57 | 13.48% |
| `shipping_delay` | 24 | 5.67% |
| `manufacturing_quality_problem` | 20 | 4.73% |
| `inactive_ingredient_shortage` | 4 | 0.95% |
| `regulatory_delay` | 3 | 0.71% |
| `manufacturing_capacity` | 0 | 0.00% |
| `labeling_packaging_error` | 0 | 0.00% |
| `regulatory_noncompliance` | 0 | 0.00% |
| `adverse_event_signal` | 0 | 0.00% |
| `recall` | 0 | 0.00% |

If all 1,205 missing reasons are retained as unknown events, the full shortage
event distribution becomes:

| Primary cause | Records | Share of all 1,628 shortages |
|---|---:|---:|
| `unknown` | 1,347 | 82.74% |
| `demand_increase` | 102 | 6.27% |
| `product_discontinuation` | 71 | 4.36% |
| `active_ingredient_shortage` | 57 | 3.50% |
| `shipping_delay` | 24 | 1.47% |
| `manufacturing_quality_problem` | 20 | 1.23% |
| `inactive_ingredient_shortage` | 4 | 0.25% |
| `regulatory_delay` | 3 | 0.18% |
| `manufacturing_capacity` | 0 | 0.00% |
| `labeling_packaging_error` | 0 | 0.00% |
| `regulatory_noncompliance` | 0 | 0.00% |
| `adverse_event_signal` | 0 | 0.00% |
| `recall` | 0 | 0.00% |

This is why the unknown-only records should not dominate classifier training.

## Revised recall distribution

The reproducible Phase 8 baseline supersedes the earlier one-time keyword
sensitivity estimate. With the 13-category taxonomy, 340/17,876 recalls (1.90%)
receive no confident keyword match, while 15,233 are provisionally mapped to
`manufacturing_quality_problem`. See `reports/baseline_classifier_quality.md`
for category counts, collision examples, and the baseline limitations.

## Phase 8 taxonomy disposition

The following keyword-family counts overlap and are only scope indicators, not
labels:

| Reviewed concept | Recall records mentioning it | Locked disposition |
|---|---:|---|
| Labeling or mispackaging | 1,819 | Adopted as `labeling_packaging_error`. |
| Unapproved or misbranded product | 688 | Adopted as `regulatory_noncompliance`, distinct from `regulatory_delay`. |
| Storage/temperature excursion | 503 | Storage failure is not necessarily a `shipping_delay` and can occur before distribution. |
| Adverse-event signal without established mechanism | 304 | Adopted as `adverse_event_signal`. |
| Counterfeit or tampering | 13 | Deliberate product-integrity events have no clean causal category. |

Storage/temperature excursion and counterfeit/tampering remain outside the
locked taxonomy because their uncovered volumes were much smaller after the
revised baseline. They continue to fall back to `recall` or another supported
cause when the text provides one.

## Balance implications for Phase 9

- Stratify the gold sample by exact shortage phrase and by recall reason family;
  random sampling will be overwhelmed by manufacturing-quality recalls.
- Keep `unknown`, missing-reason, and out-of-taxonomy records in separate review
  strata rather than letting them become a single catch-all training majority.
- Preserve the locked meaning of `recall` as an abstention/event-type fallback,
  not an underlying causal label.
- Do not report model performance only as overall accuracy. Per-class recall and
  macro-averaged metrics will be necessary for the rare categories.
