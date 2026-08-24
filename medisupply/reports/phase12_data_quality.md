# Phase 12 Incident-Aware Modeling Data Report

Generated: 2026-08-23T13:48:00.657769-04:00

Snapshot: `2026-08-21_17`

## Headline

Exact duplicate recall reasons affect **14,619/17,876 (81.78%)** raw recall filings.

Phase 12 therefore uses incident-aware identifiers, one-total-vote text weights, and leakage-connected split groups. Raw snapshots, the knowledge graph, and Phase 9 labels remain immutable inputs.

## Gold readiness

Human labels available: **400/400**; remaining: **0**.

Final Phase 12 split artifacts: **materialized**.

## Identity and weighting policy

- Recall `incident_id` is FDA `event_id`; `recall_number` remains product-level provenance.
- Shortages use a documented surrogate derived from generic name, manufacturer, and initial posting date because FDA supplies no equivalent event ID.
- `reason_text_id` hashes Unicode-normalized, case-folded, whitespace-collapsed FDA reason text.
- `split_group_id` is the connected component formed by shared `incident_id` or `reason_text_id`; a component can appear in only one split.
- Default row weight is `1 / (filings in incident+text × independent incidents with text)`, so each exact text contributes total weight 1.
- Trainers without sample-weight support may use the deterministic fallback: one filing per incident and at most 5 incidents per exact text.

FDA documents that an event may contain multiple recalled products and that a recall number identifies a specific classified recalled product: https://www.fda.gov/safety/enforcement-reports/enforcement-report-information-and-definitions

## Full recall duplication audit

Unique exact reason texts: **4,430**.

Unique normalized reason texts: **4,320**.

Unique FDA event IDs: **4,643**; missing event IDs: **0**.

One row per FDA event and normalized exact text would yield **5,418** modeling units.

The five-event fallback cap would retain **4,781** normalized event-text rows before gold-label filtering.

| Minimum repetitions | Exact-text groups | Records | Raw share |
|---:|---:|---:|---:|
| 2 | 1,173 | 14,619 | 81.78% |
| 5 | 389 | 12,626 | 70.63% |
| 10 | 217 | 11,558 | 64.66% |
| 20 | 117 | 10,225 | 57.20% |
| 35 | 76 | 9,147 | 51.17% |
| 50 | 53 | 8,204 | 45.89% |
| 100 | 22 | 6,107 | 34.16% |

### Largest exact-text groups

| Records | Independent events | FDA reason |
|---:|---:|---|
| 1,902 | 116 | Lack of Assurance of Sterility |
| 465 | 1 | Microbial contamination |
| 463 | 1 | Penicillin Cross Contamination: All lots of all products repackaged and distributed between 01/05/12 and 02/12/15 are being recalled because they were repackaged in a facility wit… |
| 405 | 14 | Lack of sterility assurance. |
| 398 | 2 | Lack of Assurance of Sterility; FDA inspection identified GMP violations potentially impacting product quality and sterility |
| 298 | 1 | The firm received seven reports of adverse reactions in the form of skin abscesses potentially linked to compounded preservative-free methylprednisolone 80mg/ml 10 ml vials. |
| 287 | 68 | CGMP Deviations |
| 198 | 1 | CGMP Deviations: Intermittent exposure to temperature excursion during storage. |
| 168 | 3 | Lack of Assurance of Sterility: FDA inspection findings resulted in concerns regarding quality control processes |
| 168 | 8 | Lack of assurance of sterility |

## Candidate queue independent support

Support is insufficient when either independent incidents or unique normalized texts is below three—the minimum needed to place independent evidence in train, validation, and test.

| Baseline stratum | Records | Incidents | Unique texts | Support |
|---|---:|---:|---:|---|
| `active_ingredient_shortage` | 20 | 13 | 1 | `insufficient_independent_examples` |
| `adverse_event_signal` | 40 | 1 | 1 | `insufficient_independent_examples` |
| `demand_increase` | 25 | 23 | 1 | `insufficient_independent_examples` |
| `inactive_ingredient_shortage` | 4 | 2 | 1 | `insufficient_independent_examples` |
| `labeling_packaging_error` | 51 | 32 | 49 | `sufficient` |
| `manufacturing_capacity` | 1 | 1 | 1 | `insufficient_independent_examples` |
| `manufacturing_quality_problem` | 81 | 78 | 79 | `sufficient` |
| `product_discontinuation` | 25 | 14 | 1 | `insufficient_independent_examples` |
| `recall` | 70 | 62 | 64 | `sufficient` |
| `regulatory_delay` | 3 | 2 | 1 | `insufficient_independent_examples` |
| `regulatory_noncompliance` | 45 | 39 | 43 | `sufficient` |
| `shipping_delay` | 27 | 17 | 4 | `sufficient` |
| `unknown` | 8 | 8 | 2 | `insufficient_independent_examples` |

## Adverse-event signal limitation

**`adverse_event_signal`: insufficient_independent_examples**.

The queue contains 40 records but only 1 independent incident and 1 unique reason text. Phase 12 must not report these as independent examples. Keep the taxonomy value, but report supervised validation/test accuracy as unavailable until additional independent incidents exist.

## Current human-labeled support

These counts are provisional until labeling reaches 400/400.

| Final human category | Records | Incidents | Unique texts | Support |
|---|---:|---:|---:|---|
| `active_ingredient_shortage` | 20 | 13 | 1 | `insufficient_independent_examples` |
| `adverse_event_signal` | 40 | 1 | 1 | `insufficient_independent_examples` |
| `demand_increase` | 25 | 23 | 1 | `insufficient_independent_examples` |
| `inactive_ingredient_shortage` | 4 | 2 | 1 | `insufficient_independent_examples` |
| `labeling_packaging_error` | 54 | 37 | 54 | `sufficient` |
| `manufacturing_quality_problem` | 147 | 133 | 137 | `sufficient` |
| `product_discontinuation` | 25 | 14 | 1 | `insufficient_independent_examples` |
| `regulatory_delay` | 3 | 2 | 1 | `insufficient_independent_examples` |
| `regulatory_noncompliance` | 46 | 40 | 44 | `sufficient` |
| `shipping_delay` | 27 | 17 | 4 | `sufficient` |
| `unknown` | 9 | 9 | 3 | `sufficient` |

## Materialized grouped splits

| Split | Records | Effective weight | Leakage groups |
|---|---:|---:|---:|
| train | 317 | 173.000 | 170 |
| validation | 38 | 37.000 | 26 |
| test | 45 | 38.000 | 26 |

## Reproduction

```bash
/opt/anaconda3/envs/medisupply/bin/python scripts/build_phase12_dataset.py
```

Use `--weighting-mode cap --max-events-per-text 5` only when the Phase 12 trainer cannot consume `sample_weight`.
