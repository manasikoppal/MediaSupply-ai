# Phase 13 Intelligence Engine Report

Generated: 2026-08-23T23:04:10.418262-04:00

Graph snapshot: `2026-08-21_17` (risk observation date `2026-08-21`)

## Scope and data confidence

This is a deterministic calculation layer. It makes no AI/API calls. The Phase 10 teacher output is read only as an already-structured root-cause input; raw FDA data, the knowledge graph, and labels are unchanged.

- Current FDA shortage records: **1,177**.
- Current shortages linked to an NDC product and scored: **1,107** (94.05%).
- Current shortages excluded because Phase 6 had no product link: **69**.
- Linked current shortages not scored because a product lacked an active-ingredient edge: **1**.
- Scored shortages with an available structured cause state: **1,021** (92.23%). Missing labels use the documented `unknown` uncertainty treatment.
- Cause methods among scored records: **156** Claude labels, **865** saved deterministic unknown-policy labels, and **0** newly derived FDA-no-reason labels.
- Current scored shortages reserved for held-out human evaluation: **86**. Their labels remain hidden from scoring and display as `unknown (reserved for evaluation)`.
- Deterministic FDA-no-reason shortages: **865**; new shortages awaiting optional teacher review: **0**. Neither path makes an AI/API call.
- Recall-to-product linkage is **19.49%** overall and **100.00%** when FDA supplies harmonized identifiers.

A positive recall overlap backed by an explicit/harmonized NDC is labeled high-confidence. A negative result is labeled limited-confidence because older unlinked recalls can hide a real overlap.

## Explainable 0–100 weighting

| Component | Max | Rule |
|---|---:|---|
| Few available manufacturers | 20 | 0 → 20; 1 → 18; 2 → 15; 3–4 → 10; 5–9 → 4; 10+ → 0 |
| Current shortage duration | 30 | <30d → 3; 30–89d → 8; 90–179d → 15; 180–364d → 22; 365+d → 30 |
| Same-ingredient recall overlap | 30 | 30 when a different manufacturer's product with the same active-ingredient set has an Ongoing linked recall; otherwise 0 |
| Available strict proxy equivalents | 10 | 0 → 10; 1 → 7; 2–3 → 3; 4+ → 0 |
| Manufacturing-related root cause | 10 | Teacher cause in active/inactive ingredient shortage, manufacturing quality, or capacity → 10; known non-manufacturing → 0; unknown/unlabeled → 3 |

Risk tiers are high (65–100), elevated (45–64), moderate (25–44), and low (0–24). For combination products, concentration uses the least-diversified active ingredient. “Available” means a finished NDC product with neither a linked Current shortage nor a linked Ongoing recall. Proxy equivalence is the Phase 6 same-ingredient-set + strength + dosage-form + route grouping; it is not an FDA Orange Book therapeutic-equivalence rating.

## Required validation: lisdexamfetamine `00054-0370`

Result: **67/100 (high)**.

| Component | Observation | Points |
|---|---|---:|
| Available manufacturers | 9 | 4/20 |
| Ongoing duration | 1,134 days | 30/30 |
| Recall overlap | True (high linkage confidence) | 30/30 |
| Available strict proxy equivalents | 6 of 18 candidates | 0/10 |
| Root cause | `unknown` (reserved for human evaluation; teacher label intentionally excluded) | 3/10 |

The overlap traversal found **13** same-ingredient products from another manufacturer under **8** active linked recall(s). **12** of those recalled products are also in Current shortage. Among the **18** strict 20 mg capsule proxy alternatives, **12** are unavailable and **6** remain available; this reproduces the Phase 6 “most alternatives also in shortage” high-risk pattern without overstating it.

The human gold set contains this record, so it was intentionally excluded from Phase 10 corpus labels. Per the requested input policy, Phase 13 does not leak that human label into the teacher layer: the root cause is treated as unknown, yet the case still scores high from the observable supply constraints.

## Highest-risk current shortages

Ranked among graph-linked current shortage records. For a more useful sanity-check list, rows with the same generic name, manufacturer, and initial posting date are shown once; the Packages column reports how many package-level FDA records the row represents. The machine-readable output retains every package record.

| Rank | Score | Drug / example package NDC | Manufacturer | Packages | Days | Mfrs | Recall | Available TE | Cause |
|---:|---:|---|---|---:|---:|---:|---|---:|---|
| 1 | **88** | Penicillin G Benzathine Injection / `84383011001` | Laboratorios Atral | 1 | 1,213 | 2 | Yes (high) | 0 | `unknown (no reason provided by FDA)` |
| 2 | **83** | Dobutamine Hydrochloride Injection / `70436020380` | Hainan Poly Pharm. Co., Ltd. | 1 | 3,210 | 3 | Yes (high) | 0 | `unknown (no reason provided by FDA)` |
| 3 | **83** | Dobutamine Hydrochloride Injection / `00338107702` | Baxter Healthcare | 3 | 3,210 | 3 | Yes (high) | 0 | `unknown (no reason provided by FDA)` |
| 4 | **83** | Methylphenidate Film, Extended Release / `00574243065` | Noven Pharmaceuticals, Inc. | 4 | 539 | 4 | Yes (high) | 0 | `unknown (no reason provided by FDA)` |
| 5 | **81** | Methylprednisolone Acetate Injection / `25021082105` | Sagent Pharmaceuticals | 3 | 1,710 | 6 | Yes (high) | 1 | `active_ingredient_shortage` |
| 6 | **77** | Methylprednisolone Acetate Injection / `00009027401` | Pfizer Inc. | 13 | 1,710 | 6 | Yes (high) | 0 | `unknown (no reason provided by FDA)` |
| 7 | **74** | Lisdexamfetamine Dimesylate Capsule / `00054037225` | Hikma Pharmaceuticals USA, Inc. | 7 | 1,134 | 9 | Yes (high) | 6 | `active_ingredient_shortage` |
| 8 | **74** | Lisdexamfetamine Dimesylate Capsule / `64850055001` | Elite Laboratories, Inc. | 7 | 1,134 | 9 | Yes (high) | 4 | `active_ingredient_shortage` |
| 9 | **74** | Lisdexamfetamine Dimesylate Capsule / `43547060510` | Solco Healthcare US, LLC | 7 | 1,134 | 9 | Yes (high) | 6 | `active_ingredient_shortage` |
| 10 | **74** | Lisdexamfetamine Dimesylate Capsule / `42858016501` | Rhodes Pharmaceuticals L.P. | 6 | 1,134 | 9 | Yes (high) | 6 | `active_ingredient_shortage` |
| 11 | **73** | Atropine Sulfate Injection / `16729051243` | Accord Healthcare Inc. | 1 | 5,346 | 51 | Yes (high) | 3 | `manufacturing_quality_problem` |
| 12 | **73** | Lidocaine Hydrochloride Injection / `63323048457` | Fresenius Kabi USA, LLC | 20 | 5,294 | 34 | Yes (high) | 0 | `unknown (reserved for evaluation)` |
| 13 | **73** | Lidocaine Hydrochloride Injection / `00409477601` | Hospira, Inc., a Pfizer Company | 16 | 5,294 | 245 | Yes (high) | 0 | `unknown (no reason provided by FDA)` |
| 14 | **73** | Lidocaine Hydrochloride Injection / `00338040903` | Baxter Healthcare | 2 | 3,222 | 245 | Yes (high) | 0 | `unknown (no reason provided by FDA)` |
| 15 | **73** | Morphine Sulfate Injection / `66794016202` | Piramal Critical Care Inc. | 2 | 3,216 | 21 | Yes (high) | 0 | `unknown (no reason provided by FDA)` |

## Interpretation limits

- Scores describe the stated snapshot and do not forecast probability or patient harm.
- A linked Current shortage is conservatively treated as making that NDC product unavailable; FDA's finer availability text is not standardized enough for inventory arithmetic.
- Recall overlap requires the same active-ingredient set (strength may differ) so combination products do not match on only one component; available alternatives use the stricter strength/form/route proxy group.
- Unlinked recalls can create false-negative overlap results, so negative overlap confidence is explicitly limited.
- `unknown` adds 3 uncertainty points. It is not interpreted as a manufacturing cause and does not receive the full 10 points.

## Reproduction

```bash
/opt/anaconda3/envs/medisupply/bin/python scripts/run_intelligence_engine.py --top 15
```

Machine-readable output: `reports/intelligence_engine.json`.
