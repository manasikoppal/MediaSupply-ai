# Daily Free Pipeline

The existing macOS cron wrapper now runs the complete free deterministic pipeline in order:

1. FDA ingestion
2. Cleaning and entity resolution
3. Knowledge graph rebuild
4. Intelligence engine recalculation
5. Dashboard artifact validation and atomic promotion

The wrapper is fail-fast. If a stage exits unsuccessfully, later stages do not run and the previous validated dashboard artifact remains active. A running dashboard notices an atomically promoted artifact on its next request, so it does not require a restart.

Phase 10 teacher labeling is intentionally absent. New shortages with a missing/`Other` FDA reason receive deterministic `unknown (no reason provided by FDA)`. New shortages with real cause text but no teacher annotation receive `unknown (needs teacher labeling)`. New recalls still enter the graph and recall-overlap calculations, but no paid cause classification is requested.

## Logs

- `logs/daily_pipeline.log`: concise stage-level outcome and failure location
- `logs/ingestion.log`
- `logs/cleaning.log`
- `logs/knowledge_graph.log`
- `logs/intelligence_engine.log`
- `logs/dashboard_refresh.log`

Every file is append-only and each run/stage boundary is timestamped.

## Cron

The existing cron entry remains the same because the wrapper path is unchanged:

```cron
0 8 * * * /Users/manasikoppal/Documents/New\ project/medisupply/scripts/run_daily_ingestion.sh
```

Verify registration with `crontab -l`. After a run, start with `logs/daily_pipeline.log`; a successful run ends with `SUCCESS: Daily free pipeline completed; dashboard data promoted`.
