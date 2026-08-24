# FDA Data Quality Report

Generated: 2026-08-21T18:32:18.442896-04:00

Snapshot: `2026-08-21_17`

## Shortage → NDC join quality

**Join success: 89.13% (1,451/1,628 shortage records).**

| Match result | Records | Percentage |
|---|---:|---:|
| `package_ndc` | 1,403 | 86.18% |
| `product_ndc` | 48 | 2.95% |
| `unmatched` | 177 | 10.87% |

Package-level matches are preferred. Product-level fallback is used only when the package code is absent from the current NDC Directory. Ambiguous candidates are not counted as successful joins.

### Join rate by shortage status

| Status | Matched | Total | Success rate |
|---|---:|---:|---:|
| Current | 1,108 | 1,177 | 94.14% |
| Resolved | 10 | 10 | 100.00% |
| To Be Discontinued | 333 | 441 | 75.51% |

## Shortage context fields

- `shortage_reason` remains null for all 1,205 records where FDA did not supply it.
- `operational_context` captures `related_info` for 263/754 Current records missing a reason.
- `discontinuation_context` captures `related_info` for 439/441 To Be Discontinued records missing a reason.
- `availability` is not used to infer or backfill a shortage reason.

## Missing fields

| Source | Field | Missing | Percentage |
|---|---|---:|---:|
| shortages | `package_ndc` | 0 | 0.00% |
| shortages | `generic_name` | 0 | 0.00% |
| shortages | `company_name` | 0 | 0.00% |
| shortages | `status` | 0 | 0.00% |
| shortages | `shortage_reason` | 1,205 | 74.02% |
| shortages | `availability` | 451 | 27.70% |
| ndc | `product_ndc` | 0 | 0.00% |
| ndc | `generic_name` | 3 | <0.01% |
| ndc | `brand_name` | 21,718 | 15.83% |
| ndc | `labeler_name` | 0 | 0.00% |
| ndc | `active_ingredients` | 2,460 | 1.79% |
| ndc | `packaging` | 425 | 0.31% |
| recalls | `recall_number` | 1 | <0.01% |
| recalls | `recalling_firm` | 0 | 0.00% |
| recalls | `product_description` | 0 | 0.00% |
| recalls | `reason_for_recall` | 0 | 0.00% |
| recalls | `classification` | 0 | 0.00% |
| drugsfda | `application_number` | 0 | 0.00% |
| drugsfda | `sponsor_name` | 0 | 0.00% |
| drugsfda | `products` | 346 | 1.18% |
| drugsfda | `submissions` | 2,705 | 9.24% |

## Unique entities

| Entity | Raw count | Normalized count |
|---|---:|---:|
| NDC manufacturers | 9,753 | 7,237 |
| Shortage manufacturers | 133 | 131 |
| NDC generic names | 24,576 | 18,687 |
| NDC brand names | 43,345 | 39,677 |
| Active ingredients | 7,706 | 7,694 |

## NDC normalization

- Unique valid package NDCs: 255,231
- Invalid NDC Directory package values: 2
- Invalid shortage package values: 0
- Canonical format is 5-4 for products (9 digits) and 5-4-2 for packages (11 digits).
- Unhyphenated 10-digit inputs are rejected as ambiguous rather than padded heuristically.

## Unmatched shortage examples

| Package NDC | Generic name | Manufacturer | Status |
|---|---|---|---|
| 13668-355-01 | Nebivolol Hydrochloride Tablet | Torrent Pharmaceuticals Limited | To Be Discontinued |
| 69516-010-30 | Obeticholic Acid Tablet | Intercept Pharmaceuticals | To Be Discontinued |
| 71288-728-32 | Bupivacaine Hydrochloride Injection | Kindos Pharmaceuticals Co. Ltd. | To Be Discontinued |
| 0069-4380-71 | Prazosin Hydrochloride Capsule | Pfizer Inc. | To Be Discontinued |
| 0071-0527-23 | Quinapril Hydrochloride Tablet | Pfizer Inc. | To Be Discontinued |
| 13668-354-90 | Nebivolol Hydrochloride Tablet | Torrent Pharmaceuticals Limited | To Be Discontinued |
| 69516-005-30 | Obeticholic Acid Tablet | Intercept Pharmaceuticals | To Be Discontinued |
| 0480-5795-08 | Cetrorelix Acetate Injection | Teva Pharmaceuticals USA, Inc. | To Be Discontinued |
| 55566-2500-0 | Desmopressin Acetate Spray | Ferring | Current |
| 63323-671-50 | Dexmedetomidine Hydrochloride Injection | Fresenius Kabi USA, LLC | Current |
