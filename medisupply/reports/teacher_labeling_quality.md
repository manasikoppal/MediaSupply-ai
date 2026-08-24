# Phase 10 Teacher Labeling Report

Generated: 2026-08-23T22:17:09.703677-04:00

Snapshot: `2026-08-21_17`

Teacher model: `claude-sonnet-5`

Current corpus prompt version: `phase10_teacher_v1_1_temperature_boundary`

## Status

Anthropic API credential detected: **yes**.

Corpus queue: **6,700** incident-aware units; **5,361** require Claude and **1,339** are deterministic unknown-policy labels.

Teacher corpus labels written: **3,602/6,700**.

Final Phase 10 corpus composition: **2,263 model-labeled** and **1,339 deterministic unknown-policy** records, plus the separate **400-record human gold** evaluation set.

Refined-prompt corpus outputs: **2,213** at **$16.6417** measured API cost; **3,098** model calls remain.

Total measured Phase 10 spend: **$19.9434** (pilot + gold evaluation + corpus + paid rejected outputs).

Shortage-only completion is finished: every non-gold shortage with a real FDA reason now has a validated teacher label. Recall completion remains intentionally out of scope.

Configured total-phase ceiling: **$20.73**; safety reserve: **$0.00**; measurable stop point: **$20.73**; usable measured headroom: **$0.7909**.

Shortage-only extension: **173** new validated labels at **$1.2091** measured incremental cost; **0** real-reason shortage calls remain.

## Queue construction

- Recall queue: **5,178** incident/text units representing **15,364** raw filings after gold-pair exclusion.
- Shortage queue: **1,522** records; **183** require Claude and **1,339** use the unknown policy.
- Recalls are one unit per unique `(FDA event_id, normalized reason_text_id)` pair, excluding every pair represented in Phase 9 gold.
- Shortage records represented in Phase 9 gold are excluded by their deterministic source record ID.
- Populated non-`Other` shortage reasons require Claude. Missing/`Other` reasons receive `unknown` without a model call.
- Teacher annotations are stored separately and always carry `annotator: teacher`; human gold files are immutable.

## Prompt and hallucination controls

The prompt contains the exact 13 category definitions used by the Phase 9 reviewer plus gold-derived boundary examples. During blind gold evaluation, any few-shot example sharing the target incident or normalized exact text is removed.

Temperature boundary refinement: a temperature excursion or temperature abuse stated as the direct finding remains `manufacturing_quality_problem`, including when it occurred during transit. `shipping_delay` now requires an explicitly named delay or logistics interruption that caused the excursion.

Claude is constrained to the `DisruptionEvent` JSON schema. The local validator then independently parses JSON, validates the Pydantic schema, and requires every evidence item to be a non-empty, case-sensitive substring of target `raw_text`.

Validated model outputs: **2,655/2,662 (99.74%)**.

Rejected hallucination/schema outputs: **7**; API/network failures without a response: **184**.

## Pilot and cost gate

Pilot validation: **50/50 (100.00%)**.

Pilot tokens: input 148,670, output 5,654, cache-write 1,752, cache-read 85,848.

Pilot API cost: **$0.3754**.

Projected complete corpus + 400-record gold evaluation cost: **$43.20**; projected additional cost after pilot: **$42.82**.

With the pilot and gold evaluation now saved, the remaining non-gold corpus is **3,098** model calls, projected at **$23.26** from the pilot average.

Pricing is pinned to Claude Sonnet 5 at $2/input MTok, $10/output MTok, $2.50/cache-write MTok, and $0.20/cache-read MTok. Confirm current pricing before a delayed full run: https://platform.claude.com/docs/en/about-claude/pricing

## Gold evaluation: teacher vs rule baseline

Human gold records: **400**; blind teacher predictions available: **400**; compared: **400**.

Gold evaluation prompt provenance: **`phase10_teacher_v1` (392)**. The temperature-boundary refinement was made after this benchmark and the gold predictions were not overwritten.

Gold model validation: **392/392** outputs passed; **0** validation failures. The audit log contains **11** transient network errors (**10** from the initial local sandbox and **0** unresolved); all requested predictions are now present. Measured validated-response cost: **$2.8626**.

Teacher agreement: **382/400 (95.50%)**.

Frozen Phase 8 baseline agreement on the same compared records: **317/400 (79.25%)**.

| Human category | Records | Teacher agreement | Baseline agreement |
|---|---:|---:|---:|
| `active_ingredient_shortage` | 20 | 20/20 (100.00%) | 20/20 (100.00%) |
| `adverse_event_signal` | 40 | 40/40 (100.00%) | 40/40 (100.00%) |
| `demand_increase` | 25 | 25/25 (100.00%) | 25/25 (100.00%) |
| `inactive_ingredient_shortage` | 4 | 4/4 (100.00%) | 4/4 (100.00%) |
| `labeling_packaging_error` | 54 | 50/54 (92.59%) | 40/54 (74.07%) |
| `manufacturing_quality_problem` | 147 | 138/147 (93.88%) | 81/147 (55.10%) |
| `product_discontinuation` | 25 | 25/25 (100.00%) | 25/25 (100.00%) |
| `regulatory_delay` | 3 | 3/3 (100.00%) | 3/3 (100.00%) |
| `regulatory_noncompliance` | 46 | 42/46 (91.30%) | 44/46 (95.65%) |
| `shipping_delay` | 27 | 27/27 (100.00%) | 27/27 (100.00%) |
| `unknown` | 9 | 8/9 (88.89%) | 8/9 (88.89%) |

### Teacher disagreement patterns

| Human category | Teacher category | Records |
|---|---|---:|
| `manufacturing_quality_problem` | `shipping_delay` | 6 |
| `labeling_packaging_error` | `manufacturing_quality_problem` | 4 |
| `manufacturing_quality_problem` | `labeling_packaging_error` | 3 |
| `regulatory_noncompliance` | `labeling_packaging_error` | 2 |
| `regulatory_noncompliance` | `manufacturing_quality_problem` | 2 |
| `unknown` | `manufacturing_quality_problem` | 1 |

### Known difficult families

| Family | Records | Teacher agreement |
|---|---:|---:|
| `zero_match_fallback` | 78 | 65/78 (83.33%) |
| `collision_boundary` | 52 | 47/52 (90.38%) |
| `temperature_abuse` | 24 | 17/24 (70.83%) |
| `defective_delivery_system` | 23 | 22/23 (95.65%) |
| `subpotent_phrasing` | 11 | 11/11 (100.00%) |
| `tablet_imprint` | 0 | 0/0 (n/a) |
| `undeclared_excipients` | 5 | 3/5 (60.00%) |
| `shipping_quality_collision` | 5 | 5/5 (100.00%) |
| `dual_cause_and` | 70 | 65/70 (92.86%) |
Frozen Phase 8 baseline on all 400 gold records: **317/400 (79.25%)**.

Teacher `manufacturing_capacity` predictions so far: **0**. Human gold contains **0** such labels; zero confirms that Phase 9 found no supported examples.

## Reproduction

```bash
# Safe, no API calls
/opt/anaconda3/envs/medisupply/bin/python scripts/run_teacher_labeling.py --mode prepare

# Requires ANTHROPIC_API_KEY; exactly 50 pilot model responses
/opt/anaconda3/envs/medisupply/bin/python scripts/run_teacher_labeling.py --mode pilot --pilot-size 50

# Blind gold evaluation only; does not label the non-gold corpus
/opt/anaconda3/envs/medisupply/bin/python scripts/run_teacher_labeling.py --mode gold-eval --max-cost-usd 4

# Total-phase ceiling includes pilot, gold evaluation, corpus, and paid rejects
/opt/anaconda3/envs/medisupply/bin/python scripts/run_teacher_labeling.py --mode full --approve-full-run --total-phase-ceiling-usd 19 --phase-cost-safety-margin-usd 0.25
```
