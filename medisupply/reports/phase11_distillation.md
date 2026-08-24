# Phase 11 SLM Distillation Feasibility Report

Generated: 2026-08-23T21:33:49.798420-04:00

## Scope and conclusion

**Trained on a leakage-safe subset covering 3 of 13 categories due to data availability, not a complete distilled classifier.**

This experiment measures whether a small local model can learn the output contract and the three supported causal categories. Results for every other category are explicitly marked `unsupported_category`; they are not presented as ordinary classification mistakes from a fully supervised model.

## Same-gold-set comparison

| Model | Gold records | Primary-cause accuracy |
|---|---:|---:|
| Phase 8 rule baseline | 400 | 79.25% |
| Claude teacher | 400 | 95.50% |
| Qwen2.5-1.5B zero-shot | 400 | 0.00% |
| Phase 11 Qwen2.5-1.5B QLoRA | 400 | 67.50% |

The small model is **11.75 percentage points below** the rule baseline and **28.00 points below** the teacher on the same gold set. It is not a production replacement.

## Gold accuracy by category

Supported-category aggregate: **223/247 (90.28%)**.

Explicit unsupported-category aggregate: **47/153 (30.72%)**. Any correct predictions here are zero-shot carryover, not evidence of supervised category coverage.

| Human category | Outcome type | Records | Correct | Accuracy | Predictions |
|---|---|---:|---:|---:|---|
| `active_ingredient_shortage` | `unsupported_category` | 20 | 0 | 0.00% | invalid_output: 13, manufacturing_quality_problem: 7 |
| `inactive_ingredient_shortage` | `unsupported_category` | 4 | 1 | 25.00% | inactive_ingredient_shortage: 1, invalid_output: 3 |
| `manufacturing_quality_problem` | `trained_category` | 147 | 146 | 99.32% | invalid_output: 1, manufacturing_quality_problem: 146 |
| `manufacturing_capacity` | `unsupported_category_no_gold_examples` | 0 | 0 | N/A | none |
| `regulatory_delay` | `unsupported_category` | 3 | 3 | 100.00% | regulatory_delay: 3 |
| `shipping_delay` | `unsupported_category` | 27 | 18 | 66.67% | invalid_output: 4, manufacturing_quality_problem: 5, shipping_delay: 18 |
| `demand_increase` | `unsupported_category` | 25 | 25 | 100.00% | demand_increase: 25 |
| `product_discontinuation` | `unsupported_category` | 25 | 0 | 0.00% | invalid_output: 1, manufacturing_capacity: 24 |
| `labeling_packaging_error` | `trained_category` | 54 | 38 | 70.37% | invalid_output: 4, labeling_packaging_error: 38, manufacturing_quality_problem: 12 |
| `regulatory_noncompliance` | `trained_category` | 46 | 39 | 84.78% | invalid_output: 3, labeling_packaging_error: 1, manufacturing_quality_problem: 3, regulatory_noncompliance: 39 |
| `adverse_event_signal` | `unsupported_category` | 40 | 0 | 0.00% | manufacturing_quality_problem: 40 |
| `recall` | `unsupported_category_no_gold_examples` | 0 | 0 | N/A | none |
| `unknown` | `unsupported_category` | 9 | 0 | 0.00% | manufacturing_quality_problem: 7, regulatory_noncompliance: 2 |

Strict JSON/schema/evidence validation: **371/400**.

## Latency and footprint

Local tuned inference latency: mean **3.51s**, p50 **3.34s**, p95 **4.69s** per record.

Generation throughput: **45.63 tokens/s**; measured peak MLX memory: **1.78 GB**.

Quantized base model: **839.3 MiB**; LoRA adapter/checkpoints: **4.8 MiB**.

Local inference has no marginal API charge after deployment and does not require network access. Phase 10 teacher calls averaged roughly $0.007–$0.008 per validated record; teacher latency was not instrumented, so no unsupported latency comparison is claimed.

## Training run

Timed local MLX-LM execution: **49.6 minutes**; status: **`completed_early_stopping`**.

Selected checkpoint: iteration **250**. Validation loss moved from **1.087** to **0.035**. The longer configured run was intentionally stopped after the validated adapter was saved.

Reproduction configuration: `configs/phase11_qwen_lora_selected.yaml`.

The training input capped repeated exact reason text to one representative, then repeated only minority-class rows inside training. Validation, auxiliary test, and gold evaluation were not oversampled.

## What a balanced production version needs

- More incident-independent teacher or human labels for the ten unsupported categories. Exact duplicate filings do not add independent supervision.
- A practical next target is at least 75–100 distinct incidents and distinct reason texts per category before splitting, with additional examples for boundary-heavy categories such as shipping delay versus temperature-related quality problems.
- Preserve a human-only final test set. New teacher labels should be grouped by `incident_id` and `reason_text_id` before sampling so train/validation/test remain leakage-free.
- Add genuine `unknown` examples whose holdout policy does not connect every missing-reason sentinel into the gold component—either by collecting a separate policy-training corpus or by approving a versioned exception for non-semantic missing-text sentinels.
- Retrain with class-aware sampling and early stopping only after each category has independent train and validation support. Repeat the identical 400-gold evaluation for an apples-to-apples comparison.
