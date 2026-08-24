# Phase 14 Production Application

## Outcome

The Phase 14 dashboard is a read-only FastAPI application backed by the Phase 13 intelligence artifact and deterministic intelligence engine. It does not make AI calls or modify snapshots, labels, or the knowledge graph.

The application provides:

- A fragility-ranked shortage list grouped by generic drug name, manufacturer, and initial posting date rather than individual package NDC rows.
- Search by drug or manufacturer, an exact manufacturer dropdown, and risk-tier filtering; all three filters combine.
- Related manufacturer signals render under a shared drug heading with manufacturer and package-count badges, avoiding duplicate-looking drug rows without merging distinct manufacturer risks.
- Shareable detail routes with all five Phase 13 scoring components.
- Tier-colored list scores and a 0–100 detail-page fragility gauge.
- Low-prominence data-freshness metadata in the footer, with an accessible tooltip, rather than in the primary header.
- Always-visible risk tier and recall-linkage confidence.
- Explicit human-review, research-signal, and non-clinical-advice framing.
- Specific warnings for unknown root causes, absent Phase 10 labels, evaluation-reserved human-gold records, and limited-confidence negative recall results.

Human-gold causes remain hidden from Phase 13 scoring. A held-out record is displayed as `unknown (reserved for evaluation)` with an explanatory tooltip instead of being presented as an ordinary missing label. A Phase 10 deterministic `policy_unknown` record is displayed separately as `unknown (no reason provided by FDA)`, indicating that `shortage_reason` was missing or FDA supplied its non-causal `Other` placeholder. Grouped rows show provenance counts when their package records have mixed cause states.

## Data flow

The list view reads the atomically promoted `data/dashboard/current.json` artifact. Drug details reuse `IntelligenceEngine.supply_fragility_score()` against the knowledge-graph database recorded in that same artifact and cache up to 256 read-only results in memory. A running server checks the promoted artifact on each request and reloads it when it changes.

Grouping follows the Phase 13 report: generic name + manufacturer + initial posting date. All package NDCs and shortage IDs remain attached to each grouped row, and the detail page identifies its representative package.

## Routes

| Route | Purpose |
|---|---|
| `/` | Ranked and grouped shortage list |
| `/drug/{shortage_id}` | Drug shortage detail page |
| `/api/shortages` | Searchable/filterable grouped records |
| `/api/shortages/{shortage_id}` | Complete deterministic detail payload |
| `/api/meta` | Snapshot, coverage, and tier counts |
| `/api/health` | Read-only health check |
| `/api/docs` | FastAPI API documentation |

## Run locally

```bash
/opt/anaconda3/envs/medisupply/bin/python -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000
```

Then open `http://127.0.0.1:8000`.

If the Phase 13 artifact moves, set `MEDISUPPLY_INTELLIGENCE_REPORT` to its path before starting the application.

The daily pipeline regenerates Phase 13 output, validates it, and atomically promotes it with `scripts/refresh_dashboard_data.py`. If validation fails, the previously promoted dashboard data remains active.

## Current snapshot

- Phase 13 snapshot: `2026-08-21_17`.
- Grouped shortage signals exposed by the application: 294.
- Package-level scored records represented: 1,107.
- Grouped risk tiers after shortage-label completion: 92 high, 114 elevated, and 88 moderate.
- Phase 10 teacher/policy root-cause coverage among scored current shortages: 1,021/1,107 (92.23%).
- Human-gold current shortages reserved from teacher labeling: 86. These remain `unknown` for scoring but are visibly identified as evaluation-reserved in the list and detail views.
- Current shortages assigned deterministic `policy_unknown` because FDA supplied no usable reason: 865. These are visibly identified as FDA-no-reason records rather than generic uncertainty.
- Overall recall linkage: 19.49%; identifier-present linkage: 100%.

These limitations are part of the UI, not relegated to this report. A limited-confidence negative recall result is not presented as proof that no overlap exists.

## Validation

- Root page, health endpoint, grouped list, combined search/tier/manufacturer filters, evaluation-reserved provenance, lisdexamfetamine detail, error responses, and shareable detail routes are covered by automated tests.
- Python lint and JavaScript syntax checks pass.
- The complete repository test suite passes.
- A live local-server check returned HTTP 200 for the root page, health endpoint, and lisdexamfetamine search.
