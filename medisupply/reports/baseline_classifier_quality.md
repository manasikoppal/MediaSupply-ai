# Phase 8 Rule-Based Baseline Report

Generated: 2026-08-21T19:06:04.159448-04:00

Snapshot: `2026-08-21_17`

## Headline

**2.48% (454/18,299) of reason-bearing records received no confident keyword match.**

A confident match requires at least one narrow, category-specific rule in the selected category. A weak generic term can still select a lowest-confidence category; no match falls back to `unknown` for shortages and `recall` for recalls. Collisions are retained for audit even though deterministic priority selects one primary cause.

## Locked taxonomy revision

The Phase 9 taxonomy `phase9_v1` has 13 primary causes. This revision adds `labeling_packaging_error`, `regulatory_noncompliance`, and `adverse_event_signal`. Their motivating no-confident volumes in the previous baseline were 1,466, 588, and 301 recall records respectively. FDA shortage reason `Other` remains `unknown`; it is not promoted into a causal category.

## Coverage by source

| Source | Records | No confident match | Pure fallback | Low confidence | Multi-category collision |
|---|---:|---:|---:|---:|---:|
| shortages | 423 | 142 (33.57%) | 142 (33.57%) | 142 (33.57%) | 0 (0.00%) |
| recalls | 17,876 | 312 (1.75%) | 276 (1.54%) | 459 (2.57%) | 157 (0.88%) |

## Predicted category distribution

| Category | Shortages | Recalls |
|---|---:|---:|
| `inactive_ingredient_shortage` | 4 | 0 |
| `active_ingredient_shortage` | 57 | 0 |
| `regulatory_delay` | 3 | 0 |
| `regulatory_noncompliance` | 0 | 665 |
| `labeling_packaging_error` | 0 | 1,405 |
| `shipping_delay` | 24 | 7 |
| `demand_increase` | 102 | 0 |
| `product_discontinuation` | 71 | 0 |
| `manufacturing_capacity` | 0 | 1 |
| `manufacturing_quality_problem` | 20 | 15,224 |
| `adverse_event_signal` | 0 | 298 |
| `recall` | 0 | 276 |
| `unknown` | 142 | 0 |

## Resolved discontinuation/quality collision

The previous rules produced 91 collisions from only two unique FDA narratives. Both were keyword overreach rather than true product-discontinuation labels: the text described ending stability work, not ending the drug product.

| Records | FDA text | Revised primary cause | Why |
|---:|---|---|---|
| 86 | CGMP Deviations: Firm went out of business and could no longer continue stability studies. | `manufacturing_quality_problem` | Business closure is context; the stated recall problem is loss of required stability oversight. |
| 5 | CGMP Deviations; the firm discontinued required stability testing for products on the market still within expiry | `manufacturing_quality_problem` | The firm discontinued testing, not the drug product or its manufacture. |

## Most common rule collisions

| Matched categories | Records | Common real text |
|---|---:|---|
| `regulatory_noncompliance` + `manufacturing_quality_problem` | 86 | CGMP Deviations: These products have been found to be misbranded as unapproved new drugs (35) |
| `regulatory_noncompliance` + `labeling_packaging_error` | 39 | Marketed Without An Approved NDA/ANDA: Products marked as dietary supplements have labeling that bears drug/disease cla… (14) |
| `labeling_packaging_error` + `manufacturing_quality_problem` | 30 | Labeling: Not Elsewhere Classified: This product is misbranded because the product is not sterile and the labeling is m… (6) |
| `shipping_delay` + `manufacturing_quality_problem` | 7 | CGMP Deviations; potential temperature excursions due to transit delays (3) |
| `manufacturing_quality_problem` + `adverse_event_signal` | 3 | Microbial Contamination of Sterile Products: Product associated with reports of adverse events indicative of infusion r… (1) |
| `labeling_packaging_error` + `adverse_event_signal` | 3 | Labeling; Incorrect or Missing Package Insert: The package insert provided with the product does not include all requir… (2) |
| `manufacturing_capacity` + `manufacturing_quality_problem` | 1 | Lack of assurance of sterility; equipment failure led to potential breach in asceptic process. (1) |

## Shortage proxy-reference comparison

The eight standardized FDA shortage phrases provide a useful proxy reference: **100.00% (423/423) agreement**. This is coverage, not independent validation—the rules were designed around these known phrases.

| Reference category | Baseline category | Records |
|---|---|---:|
| `active_ingredient_shortage` | `active_ingredient_shortage` | 57 |
| `demand_increase` | `demand_increase` | 102 |
| `inactive_ingredient_shortage` | `inactive_ingredient_shortage` | 4 |
| `manufacturing_quality_problem` | `manufacturing_quality_problem` | 20 |
| `product_discontinuation` | `product_discontinuation` | 71 |
| `regulatory_delay` | `regulatory_delay` | 3 |
| `shipping_delay` | `shipping_delay` | 24 |
| `unknown` | `unknown` | 142 |

## Phase 7 recall hand-sample comparison

The baseline agrees with **7/8** previously hand-reviewed recall examples. Eight examples are too few for an accuracy estimate; disagreements expose rule/taxonomy behavior.

| Recall | Hand mapping | Baseline | Confidence | All matched categories |
|---|---|---|---|---|
| `D-0115-2026` | `manufacturing_quality_problem` | `manufacturing_quality_problem` | high | `manufacturing_quality_problem` |
| `D-0276-2024` | `manufacturing_quality_problem` | `manufacturing_quality_problem` | high | `manufacturing_quality_problem` |
| `D-707-2015` | `manufacturing_quality_problem` | `manufacturing_quality_problem` | high | `manufacturing_quality_problem` |
| `D-0429-2021` | `recall` | `manufacturing_quality_problem` | high | `manufacturing_quality_problem` |
| `D-0930-2017` | `labeling_packaging_error` | `labeling_packaging_error` | high | `labeling_packaging_error` |
| `D-0887-2016` | `regulatory_noncompliance` | `regulatory_noncompliance` | high | `regulatory_noncompliance` |
| `D-241-2014` | `adverse_event_signal` | `adverse_event_signal` | high | `adverse_event_signal` |
| `D-0818-2023` | `manufacturing_quality_problem` | `manufacturing_quality_problem` | high | `manufacturing_quality_problem` |

## Taxonomy-gap signals in recall text

These keyword families overlap and are diagnostic counts, not labels.

| Missing concept candidate | All recalls | No confident current-category match |
|---|---:|---:|
| `storage_temperature_excursion` | 503 | 61 |
| `counterfeit_or_tampering` | 13 | 0 |

## Common no-confident-match text

| Source | Records | FDA reason text |
|---|---:|---|
| shortages | 142 | Other |
| recalls | 15 | This recall is being initiated following FDA's recommendation based on certain observations noted during an August 15, 2025, inspection of the manufacturing fa… |
| recalls | 13 | Temperature Abuse: product samples were stored at temperatures below 32* F which is not in accordance with storage requirements that could cause a lack of effi… |
| recalls | 8 | Temperature Abuse: Product exposed to temperature outside specified limits. |
| recalls | 7 | Defective Delivery System: There is a potential for some tablets to be missing the laser drilling which might affect drug release. |
| recalls | 6 | Incorrect/Undeclared Excipients: Specific drug products were compounded with an incorrect solvent. |
| recalls | 5 | Temperature Abuse; various products were not stored at Controlled Room Temperature as per USP guidelines during shipping |
| recalls | 4 | Defective Delivery System: There is a remote potential that cartons of product could be co-packaged with an oral dosing syringe without dose markings. |
| recalls | 4 | Incorrect/Undeclared Excipient: There is a potential an incorrect grade of excipient was used during manufacturing. |
| recalls | 4 | Defective Delivery System: potential risk of rubber stopper particles clogging the needle and leading to underdosing |
| recalls | 4 | Defective Delivery System: Some Lupron Depot Kits may contain a syringe with a potentially defective LuproLoc needle stick protection device. |

## Failure-pattern summary

- The shortage baseline is nearly a lookup table because FDA currently uses only eight reason phrases; its apparent agreement is not evidence of generalization.
- Recall text mixes root cause, observed defect, regulatory action, lifecycle state, and harm in the same field. Multi-category matches are therefore meaningful ambiguity, not merely regex noise.
- Common unmatched quality phrases show vocabulary failure rather than taxonomy failure; a learned model should recognize paraphrases without continually expanding a brittle synonym list.
- Tightening discontinuation to explicit drug/product/manufacturing cessation eliminated the former 91-record discontinuation/quality collision without changing the quality interpretation of those records.
- `shipping_delay` + `manufacturing_quality_problem` commonly describes cold-chain transit delays and possible temperature damage, again mixing initiating cause with resulting product risk.
- The locked taxonomy keeps `recall` as an explicit event-type/out-of-taxonomy fallback, while the other values are causal categories. Phase 9 gold labels should preserve that abstention semantics.
- Storage-temperature excursions and counterfeit/tampering remain the clearest unresolved taxonomy candidates. The three high-volume Phase 8 gaps are now first-class categories.
- A Phase 9 gold set should be stratified across predicted categories, collisions, pure fallbacks, weak-only matches, and the candidate gap families. Overall accuracy alone would hide failure on rare categories.

## Reproduction

```bash
/opt/anaconda3/envs/medisupply/bin/python src/models/baseline_classifier.py
```
