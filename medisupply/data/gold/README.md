# Phase 9 human labeling

`candidates.jsonl` contains 400 real FDA records selected for review. It is not
gold data: every baseline prediction is only a suggestion.

Start or resume labeling from the project root:

```bash
/opt/anaconda3/envs/medisupply/bin/python scripts/label_gold_dataset.py --annotator "your-name"
```

Useful commands:

```bash
# Review only 20 records in this session
/opt/anaconda3/envs/medisupply/bin/python scripts/label_gold_dataset.py --annotator "your-name" --limit 20

# Enable one-key approval for the strictly eligible confident records
/opt/anaconda3/envs/medisupply/bin/python scripts/label_gold_dataset.py --annotator "your-name" --fast-mode

# Check progress without opening a prompt
/opt/anaconda3/envs/medisupply/bin/python scripts/label_gold_dataset.py --status

# After all 400 records have been reviewed, create the splits and report
/opt/anaconda3/envs/medisupply/bin/python scripts/label_gold_dataset.py --finalize
```

Each accepted or corrected label is validated against `phase9_v1` and saved
immediately to `labeled.jsonl`, so the process is safe to stop and resume.
Enter `?` at the category prompt to show the locked category definitions.
`--finalize` refuses to run until every candidate has a human label. It then
creates `train.jsonl`, `validation.jsonl`, and `test.jsonl` plus the Phase 9
quality report.

Each JSONL row wraps one validated `DisruptionEvent` in an object that also
contains the source record ID, raw FDA text, baseline suggestion, annotator,
timestamp, and any human disagreement note.

## Fast mode safeguards

Fast mode displays eligible FDA text and its baseline suggestion together on
one compact line. Press `a` to approve immediately, `f` to open the full
reviewer, `s` to skip, or `q` to quit.

A record is eligible only when all of these are true:

- its selection reason is exactly `baseline_confident`;
- its numeric confidence is exactly `1.000`;
- it has a confident keyword match; and
- it has no category collision.

Low-confidence, collision-boundary, targeted-capacity-boundary, and unknown
control records always use the full multi-field reviewer even when
`--fast-mode` is enabled.
