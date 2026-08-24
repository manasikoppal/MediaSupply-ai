# Phase 11 SLM Distillation Data Report

Generated: 2026-08-23T16:55:06.450572-04:00

## Dataset frozen for Phase 11

Combined annotations: **3,829** — **2,090 teacher**, **1,339 deterministic unknown**, and **400 human gold**.

All records retain `annotation_origin`; raw snapshots, teacher outputs, and human gold labels are unchanged.

The full 400-record human gold set is reserved as `gold_evaluation` and is never eligible for training.

## Leakage audit

Phase 11 uses the Phase 12 connected-component rule: examples sharing either FDA/surrogate `incident_id` or normalized `reason_text_id`, including transitive links, cannot cross data boundaries.

This leaves **1,651** leakage-safe non-gold examples and quarantines **1,778** non-gold examples connected to held-out gold.

Gold/training group overlap: **0**. Cross-training-split group overlap: **0**.

| Artifact | Records | Connected groups | Effective weight |
|---|---:|---:|---:|
| `train` | 1,139 | 1,040 | 1040.00 |
| `validation` | 250 | 206 | 223.00 |
| `test` | 262 | 207 | 223.00 |
| `gold_evaluation` | 400 | 214 | 400.00 |
| `excluded` | 1,778 | 32 | 1778.00 |

## Class balance and training-readiness warning

Without leakage quarantine, deterministic `unknown` would be **39.05%** of the non-gold pool and risks teaching a broad default at the expense of real causal classes.

Under the strict holdout policy, the eligible pool covers only **3/13 categories**; its majority class is **82.01%** of records.

| Eligible category | Records |
|---|---:|
| `labeling_packaging_error` | 193 |
| `manufacturing_quality_problem` | 1,354 |
| `regulatory_noncompliance` | 104 |

This pool is valid for a leakage-safe experiment, but it is not sufficient to claim broad 13-category supervised coverage. Fine-tuning should not begin without explicitly accepting that limitation or changing the data policy.

## Model and compute recommendation

Recommended checkpoint: `Qwen/Qwen2.5-1.5B-Instruct` with 4-bit QLoRA through MLX-LM. Its 1.54B size is a practical middle ground for this 16 GB Apple-silicon machine, its instruction tuning is preferable to a raw pretrained checkpoint for schema-constrained JSON, and its Apache-2.0 license is straightforward. The 0.5B variant should be faster but has less capacity for subtle causal boundaries; a 3B model raises memory, latency, and overfitting risk without fixing missing-category supervision.

Audited host: MacBook Air (Apple M4, 10 CPU cores, 16 GB unified memory). Local 4-bit LoRA is sufficient; cloud GPU access is optional, not required. Full-precision fine-tuning is not recommended on this machine.

Estimated local wall time, before benchmarking: about **45–120 minutes per LoRA run**, plus **20–45 minutes** for base and tuned evaluation over 400 gold examples. Allow **2–4 hours end-to-end** for model download, one training run, validation, generation, and reporting; two or three controlled hyperparameter runs may take **4–8 hours** on this fanless laptop.

The current `medisupply` environment does not yet contain MLX-LM or the Hugging Face training stack. No package or model download has been performed.

An optional 24 GB L4 cloud instance is currently listed at roughly $0.49/hour, so a conservative one-run budget is about **$1–$3**, including setup/idle time but excluding persistent storage. Verify live pricing before launch.

References: https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct, https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/LORA.md, and https://www.runpod.io/pricing

## Recommended experiment after approval

1. Evaluate the untouched base model on all 400 gold records.
2. LoRA-tune Qwen2.5-1.5B-Instruct on `train.jsonl`, select only by `validation.jsonl`, and run one final test against all 400 gold records.
3. Validate JSON schema and verbatim evidence, and report primary-cause accuracy overall/per category alongside baseline 79.25% and teacher 95.50%.
4. Measure local p50/p95 latency, throughput, peak memory, model/adapter size, and schema/evidence validation rates.

The `test.jsonl` artifact is an auxiliary teacher-label holdout. The 400-record `gold_evaluation.jsonl` remains the only human-ground-truth final evaluation set.
