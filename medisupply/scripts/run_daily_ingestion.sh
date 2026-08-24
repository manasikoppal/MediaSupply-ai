#!/bin/zsh

PROJECT_ROOT="/Users/manasikoppal/Documents/New project/medisupply"
PYTHON_BIN="${MEDISUPPLY_PYTHON:-/opt/anaconda3/envs/medisupply/bin/python}"
LOG_DIR="$PROJECT_ROOT/logs"
PIPELINE_LOG="$LOG_DIR/daily_pipeline.log"

mkdir -p "$LOG_DIR"

timestamp() {
  date '+%Y-%m-%d %H:%M:%S %z'
}

pipeline_log() {
  print -r -- "[$(timestamp)] $1" >>"$PIPELINE_LOG"
}

run_stage() {
  local stage_name="$1"
  local stage_log="$2"
  shift 2

  pipeline_log "START: $stage_name"
  {
    print -r -- "[$(timestamp)] START: $stage_name"
    "$@"
    local stage_status=$?
    if (( stage_status != 0 )); then
      print -r -- "[$(timestamp)] FAILURE: $stage_name exited with status $stage_status"
    else
      print -r -- "[$(timestamp)] SUCCESS: $stage_name completed"
    fi
    return "$stage_status"
  } >>"$LOG_DIR/$stage_log" 2>&1
}

if ! cd "$PROJECT_ROOT"; then
  pipeline_log "FAILURE: Could not cd to project root: $PROJECT_ROOT"
  exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  pipeline_log "FAILURE: Conda Python is not executable: $PYTHON_BIN"
  exit 1
fi

pipeline_log "Starting daily free pipeline"

run_stage "FDA ingestion" "ingestion.log" \
  "$PYTHON_BIN" src/ingestion/run_ingestion.py || exit_status=$?
if (( ${exit_status:-0} != 0 )); then
  pipeline_log "PIPELINE STOPPED after FDA ingestion failure; downstream data was not promoted"
  exit "$exit_status"
fi

run_stage "Cleaning and entity resolution" "cleaning.log" \
  "$PYTHON_BIN" src/cleaning/run_cleaning.py || exit_status=$?
if (( ${exit_status:-0} != 0 )); then
  pipeline_log "PIPELINE STOPPED after cleaning/entity-resolution failure; downstream data was not promoted"
  exit "$exit_status"
fi

run_stage "Knowledge graph rebuild" "knowledge_graph.log" \
  "$PYTHON_BIN" src/graph/build_graph.py || exit_status=$?
if (( ${exit_status:-0} != 0 )); then
  pipeline_log "PIPELINE STOPPED after knowledge-graph failure; dashboard data was not promoted"
  exit "$exit_status"
fi

run_stage "Intelligence engine recalculation" "intelligence_engine.log" \
  "$PYTHON_BIN" scripts/run_intelligence_engine.py --top 15 || exit_status=$?
if (( ${exit_status:-0} != 0 )); then
  pipeline_log "PIPELINE STOPPED after intelligence-engine failure; dashboard data was not promoted"
  exit "$exit_status"
fi

run_stage "Dashboard data validation and promotion" "dashboard_refresh.log" \
  "$PYTHON_BIN" scripts/refresh_dashboard_data.py || exit_status=$?
if (( ${exit_status:-0} != 0 )); then
  pipeline_log "PIPELINE STOPPED: dashboard validation failed; the previous dashboard artifact remains active"
  exit "$exit_status"
fi

pipeline_log "SUCCESS: Daily free pipeline completed; dashboard data promoted"
