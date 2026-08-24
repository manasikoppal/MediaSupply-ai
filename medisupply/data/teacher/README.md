# Phase 10 teacher labels

This directory is separate from immutable Phase 9 human gold data.

- `queue.jsonl`: non-gold labeling units. Recall rows are grouped by FDA `event_id` plus normalized exact `reason_text_id`; shortage rows remain record-level.
- `auto_unknown.jsonl`: deterministic labels for non-gold shortages whose FDA reason is missing or `Other`.
- `pilot.jsonl`: validated Claude labels from the 50–100 record cost pilot.
- `pilot_rejected.jsonl`: pilot API or validation failures, including invalid raw output when available.
- `pilot_summary.json`: actual token usage, validation rate, and projected full-run cost.
- `labeled.jsonl`: final non-gold teacher corpus. Every row has `annotator: "teacher"`; `label_method` distinguishes Claude from the unknown policy.
- `gold_predictions.jsonl`: blind teacher predictions used only to evaluate against the 400 human labels.
- `gold_rejected.jsonl`: invalid or failed calls from the isolated blind gold evaluation.
- `rejected.jsonl`: full-run API and validation failures.
- `phase_budget.json`: persisted total-phase ceiling, safety reserve, and measured starting spend for the latest approved run.

Safe queue preparation makes no API calls:

```bash
/opt/anaconda3/envs/medisupply/bin/python scripts/run_teacher_labeling.py --mode prepare
```

Set `ANTHROPIC_API_KEY` in the invoking shell, then run the pilot:

```bash
/opt/anaconda3/envs/medisupply/bin/python scripts/run_teacher_labeling.py --mode pilot --pilot-size 50
```

Review `pilot_summary.json` and `reports/teacher_labeling_quality.md`. The full run has two independent gates: a completed 50+ response pilot and an explicit approved dollar ceiling.

Run the blind gold evaluation independently of the non-gold corpus:

```bash
/opt/anaconda3/envs/medisupply/bin/python scripts/run_teacher_labeling.py \
  --mode gold-eval \
  --max-cost-usd 4
```

```bash
/opt/anaconda3/envs/medisupply/bin/python scripts/run_teacher_labeling.py \
  --mode full \
  --approve-full-run \
  --total-phase-ceiling-usd 19 \
  --phase-cost-safety-margin-usd 0.25
```

The full-run gate counts measured usage from the pilot, blind gold evaluation, corpus labels, and paid rejected outputs exactly once. The safety margin is held below the requested total to cover interrupted calls whose usage was not returned locally.

The pipeline is resumable by `teacher_id`. Never copy `gold_predictions.jsonl` into the human gold set.
