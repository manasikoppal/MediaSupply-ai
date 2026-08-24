# Phase 10 Shortage-Only Completion Impact

Generated: 2026-08-23

## Completion and cost

- Real-reason shortage records requiring Claude: **183**.
- Previously labeled: **10**.
- Resumed and validated in this run: **173/173**.
- Remaining real-reason shortage calls: **0**.
- Validation/API failures in the successful run: **0**.
- Projected incremental cost: **$1.2591**.
- Measured incremental cost: **$1.2091**, below the **$2.00** hard ceiling.
- Total measured Phase 10 spend after the extension: **$19.9434**.
- Recall calls were not attempted; **3,098 recall incident/text units** remain intentionally unlabeled.

## Phase 13 before and after

| Metric | Before | After | Change |
|---|---:|---:|---:|
| Scored current shortages with Phase 10 teacher/policy label | 875 | 1,021 | +146 |
| Root-cause label coverage | 79.04% | 92.23% | +13.19 percentage points |
| Claude-derived labels among scored current shortages | 10 | 156 | +146 |
| Current scored shortages with `unknown` cause | 1,097 | 951 | -146 |
| `active_ingredient_shortage` | 0 | 37 | +37 |
| `demand_increase` | 4 | 76 | +72 |
| `manufacturing_quality_problem` | 2 | 14 | +12 |
| `product_discontinuation` | 4 | 25 | +21 |
| `shipping_delay` | 0 | 4 | +4 |

The remaining 951 `unknown` scores are mostly legitimate deterministic unknown-policy records where FDA supplied no real reason, plus human-gold exclusions and shortages outside the Phase 10 non-gold corpus. They were not silently inferred.

## Dashboard ranking impact

The dashboard still exposes **294 grouped shortage signals** representing **1,107 package records**.

| Grouped tier | Before | After | Change |
|---|---:|---:|---:|
| High | 88 | 92 | +4 |
| Elevated | 118 | 114 | -4 |
| Moderate | 88 | 88 | 0 |

Notable top-list changes:

- Sagent methylprednisolone acetate moved from **74 to 81** after receiving `active_ingredient_shortage`, and now ranks fifth in the Phase 13 report.
- Four lisdexamfetamine manufacturer groups now score **74** with `active_ingredient_shortage` and appear in the report's top ten.
- Accord atropine sulfate now appears at **73** with `manufacturing_quality_problem`.
- The exact `00054-0370` validation record remains `unknown` at 67 because it belongs to the held-out human gold set and was correctly excluded from Phase 10 teacher labeling. The grouped Hikma dashboard row uses another representative package from the same grouped shortage incident and now carries the teacher-derived active-ingredient cause.

The dashboard continues to label every score as a research signal requiring human review. Recall-confidence behavior is unchanged because this extension labeled shortages only.

