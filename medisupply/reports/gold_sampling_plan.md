# Phase 9 Gold Sampling Plan

Taxonomy: `phase9_v1`

Candidates: **400**

Sources: 105 shortages and 295 recalls.

Low-confidence or collision-boundary candidates: 154.

These are review candidates, not gold labels. `sampling_stratum` records why a record was selected; only the interactive human decision becomes `event.primary_cause` in `labeled.jsonl`.

## Sampling strata

| Target stratum | Candidates | Baseline predicts same category |
|---|---:|---:|
| `active_ingredient_shortage` | 20 | 20 |
| `inactive_ingredient_shortage` | 4 | 4 |
| `manufacturing_quality_problem` | 70 | 70 |
| `manufacturing_capacity` | 15 | 1 |
| `regulatory_delay` | 3 | 3 |
| `shipping_delay` | 25 | 25 |
| `demand_increase` | 25 | 25 |
| `product_discontinuation` | 25 | 25 |
| `labeling_packaging_error` | 50 | 50 |
| `regulatory_noncompliance` | 45 | 45 |
| `adverse_event_signal` | 40 | 40 |
| `recall` | 70 | 70 |
| `unknown` | 8 | 8 |

## Important source constraints

- FDA exposes only eight distinct populated shortage-reason phrases in this snapshot. Several shortage strata therefore contain different real records with identical reason text.
- Only three direct `regulatory_delay` records and four direct `inactive_ingredient_shortage` records exist; all are included.
- Only one allowed reason field directly matches `manufacturing_capacity`. The 15-record capacity stratum includes that record plus targeted production/facility boundary cases for human review. It does not pre-assign those cases to capacity.
- The eight `unknown` controls are the explicit exception to the non-null/non-`Other` shortage rule: four FDA `Other` records and four records with a missing shortage reason.
- Splits are created only after all 400 records have independent human labels.

## Baseline suggestions in the queue

| Baseline prediction | Candidates |
|---|---:|
| `manufacturing_quality_problem` | 81 |
| `recall` | 70 |
| `labeling_packaging_error` | 51 |
| `regulatory_noncompliance` | 45 |
| `adverse_event_signal` | 40 |
| `shipping_delay` | 27 |
| `product_discontinuation` | 25 |
| `demand_increase` | 25 |
| `active_ingredient_shortage` | 20 |
| `unknown` | 8 |
| `inactive_ingredient_shortage` | 4 |
| `regulatory_delay` | 3 |
| `manufacturing_capacity` | 1 |
