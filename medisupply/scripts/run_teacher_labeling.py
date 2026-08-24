#!/usr/bin/env python3
"""Prepare, pilot, and cost-gate Phase 10 Claude teacher labeling."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from src.models.baseline_classifier import _latest_snapshot, _load_records
from src.models.gold_dataset import GoldCandidate, GoldLabelRecord, load_jsonl
from src.models.phase12_dataset import atomic_write_text
from src.models.teacher_labeling import (
    COMPATIBLE_PILOT_PROMPT_VERSIONS,
    DEFAULT_MODEL,
    DEFAULT_PILOT_SIZE,
    PROMPT_VERSION,
    AnthropicMessagesClient,
    TeacherFailureRecord,
    TeacherInputRecord,
    TeacherLabelRecord,
    TeacherValidationError,
    append_jsonl,
    auto_unknown_label,
    build_corpus_queue,
    build_few_shot_examples,
    build_gold_evaluation_queue,
    few_shots_for_target,
    gold_evaluation_metrics,
    pilot_cost_estimate,
    render_teacher_report,
    select_pilot_units,
    teacher_record_from_event,
    validate_teacher_output,
    write_queue,
)

DEFAULT_CANDIDATES = REPOSITORY_ROOT / "data" / "gold" / "candidates.jsonl"
DEFAULT_GOLD_LABELS = REPOSITORY_ROOT / "data" / "gold" / "labeled.jsonl"
DEFAULT_OUTPUT_DIR = REPOSITORY_ROOT / "data" / "teacher"
DEFAULT_REPORT_JSON = REPOSITORY_ROOT / "reports" / "teacher_labeling_quality.json"
DEFAULT_REPORT_MD = REPOSITORY_ROOT / "reports" / "teacher_labeling_quality.md"


class CostLimitReached(RuntimeError):
    """Raised before a call that could exceed the approved run budget."""


def _paths(output_dir: Path) -> dict[str, Path]:
    return {
        "queue": output_dir / "queue.jsonl",
        "auto_unknown": output_dir / "auto_unknown.jsonl",
        "pilot": output_dir / "pilot.jsonl",
        "pilot_rejected": output_dir / "pilot_rejected.jsonl",
        "pilot_summary": output_dir / "pilot_summary.json",
        "labeled": output_dir / "labeled.jsonl",
        "gold_predictions": output_dir / "gold_predictions.jsonl",
        "gold_rejected": output_dir / "gold_rejected.jsonl",
        "rejected": output_dir / "rejected.jsonl",
        "budget": output_dir / "phase_budget.json",
        "finalization": output_dir / "phase10_finalization.json",
        "shortage_completion": output_dir / "shortage_completion.json",
    }


def _assert_complete_gold(
    candidates: list[GoldCandidate], labels: list[GoldLabelRecord]
) -> None:
    candidate_ids = {row.candidate_id for row in candidates}
    label_ids = {row.candidate_id for row in labels}
    if len(candidate_ids) != 400 or label_ids != candidate_ids:
        raise RuntimeError(
            "Phase 10 requires the completed 400-record Phase 9 gold set; "
            f"found {len(labels)}/{len(candidates)} labels"
        )


def _prepare(
    *,
    snapshot: Path,
    candidates_path: Path,
    labels_path: Path,
    output_dir: Path,
) -> tuple[
    list[GoldCandidate],
    list[GoldLabelRecord],
    list[TeacherInputRecord],
    list[TeacherInputRecord],
    list[TeacherLabelRecord],
]:
    candidates = load_jsonl(candidates_path, GoldCandidate)
    labels = load_jsonl(labels_path, GoldLabelRecord)
    _assert_complete_gold(candidates, labels)
    recalls = _load_records(snapshot, "recalls")
    shortages = _load_records(snapshot, "shortages")
    corpus_queue = build_corpus_queue(
        recalls,
        shortages,
        candidates,
        snapshot=snapshot.name,
    )
    gold_queue = build_gold_evaluation_queue(candidates)
    auto_labels = [
        auto_unknown_label(row) for row in corpus_queue if not row.requires_model_call
    ]
    paths = _paths(output_dir)
    write_queue(paths["queue"], corpus_queue)
    write_queue(paths["auto_unknown"], auto_labels)
    current_ids = {row.teacher_id for row in corpus_queue}
    existing_labels = [
        row
        for row in load_jsonl(paths["labeled"], TeacherLabelRecord)
        if row.teacher_id in current_ids
    ]
    merged_labels = {row.teacher_id: row for row in [*auto_labels, *existing_labels]}
    write_queue(
        paths["labeled"],
        sorted(merged_labels.values(), key=lambda row: row.teacher_id),
    )
    return candidates, labels, corpus_queue, gold_queue, auto_labels


def _load_pilot_summary(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _usage_total(records: list[TeacherLabelRecord | TeacherFailureRecord]) -> float:
    return sum(row.usage.estimated_cost_usd for row in records if row.usage is not None)


def _measured_phase_cost(paths: dict[str, Path]) -> dict[str, float]:
    """Count every measured paid response once across all Phase 10 stages."""
    pilot = load_jsonl(paths["pilot"], TeacherLabelRecord)
    pilot_ids = {row.teacher_id for row in pilot}
    gold = load_jsonl(paths["gold_predictions"], TeacherLabelRecord)
    corpus = [
        row
        for row in load_jsonl(paths["labeled"], TeacherLabelRecord)
        if row.teacher_id not in pilot_ids
    ]
    pilot_failures = load_jsonl(paths["pilot_rejected"], TeacherFailureRecord)
    gold_failures = load_jsonl(paths["gold_rejected"], TeacherFailureRecord)
    corpus_failures = load_jsonl(paths["rejected"], TeacherFailureRecord)
    breakdown = {
        "pilot_validated_usd": _usage_total(pilot),
        "gold_validated_usd": _usage_total(gold),
        "corpus_validated_usd": _usage_total(corpus),
        "pilot_rejected_usd": _usage_total(pilot_failures),
        "gold_rejected_usd": _usage_total(gold_failures),
        "corpus_rejected_usd": _usage_total(corpus_failures),
    }
    breakdown["measured_total_usd"] = sum(breakdown.values())
    return breakdown


def _failure(
    unit: TeacherInputRecord,
    *,
    model: str,
    error_type: str,
    error: Exception,
    raw_output: str | None = None,
    request_id: str | None = None,
    usage=None,
) -> TeacherFailureRecord:
    return TeacherFailureRecord(
        teacher_id=unit.teacher_id,
        dataset_scope=unit.dataset_scope,
        model=model,
        prompt_version=PROMPT_VERSION,
        failed_at=datetime.now().astimezone().isoformat(),
        error_type=error_type,
        error=str(error),
        raw_output=raw_output,
        request_id=request_id,
        usage=usage,
    )


def _call_one(
    *,
    client: AnthropicMessagesClient,
    unit: TeacherInputRecord,
    few_shots: list[dict[str, Any]],
) -> tuple[TeacherLabelRecord | None, TeacherFailureRecord | None]:
    raw_output = None
    request_id = None
    usage = None
    try:
        raw_output, request_id, usage = client.label(
            unit, few_shots_for_target(few_shots, unit)
        )
        event = validate_teacher_output(raw_output, unit.raw_text)
        return (
            teacher_record_from_event(
                unit,
                event,
                model=client.model,
                request_id=request_id,
                usage=usage,
            ),
            None,
        )
    except TeacherValidationError as error:
        return None, _failure(
            unit,
            model=client.model,
            error_type="validation_error",
            error=error,
            raw_output=raw_output,
            request_id=request_id,
            usage=usage,
        )
    except (OSError, RuntimeError, ValueError) as error:
        return None, _failure(
            unit,
            model=client.model,
            error_type="api_error",
            error=error,
        )


def _run_units(
    *,
    client: AnthropicMessagesClient,
    units: list[TeacherInputRecord],
    few_shots: list[dict[str, Any]],
    labels_path: Path,
    failures_path: Path,
    existing_labels: list[TeacherLabelRecord],
    cost_limit_usd: float | None = None,
    already_spent_usd: float = 0.0,
    budget_existing_prompt_versions: set[str] | None = None,
    next_call_reserve_usd: float = 0.0,
    max_workers: int = 1,
) -> tuple[list[TeacherLabelRecord], list[TeacherFailureRecord]]:
    if max_workers < 1:
        raise ValueError("max_workers must be positive")
    labels = list(existing_labels)
    labeled_ids = {row.teacher_id for row in labels}
    failures: list[TeacherFailureRecord] = []
    spent = already_spent_usd
    spent += sum(
        row.usage.estimated_cost_usd
        for row in labels
        if row.usage is not None
        and (
            budget_existing_prompt_versions is None
            or row.prompt_version in budget_existing_prompt_versions
        )
    )
    pending = [row for row in units if row.teacher_id not in labeled_ids]

    def save_result(
        label: TeacherLabelRecord | None,
        failure: TeacherFailureRecord | None,
    ) -> None:
        nonlocal spent
        if label is not None:
            append_jsonl(labels_path, label)
            labels.append(label)
            labeled_ids.add(label.teacher_id)
            spent += label.usage.estimated_cost_usd if label.usage else 0.0
        elif failure is not None:
            append_jsonl(failures_path, failure)
            failures.append(failure)
            spent += failure.usage.estimated_cost_usd if failure.usage else 0.0

    completed = 0
    next_report = 25
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while completed < len(pending):
            batch_size = min(max_workers, len(pending) - completed)
            if cost_limit_usd is not None and next_call_reserve_usd:
                affordable = int(
                    max(0.0, cost_limit_usd - spent) // next_call_reserve_usd
                )
                batch_size = min(batch_size, affordable)
            if batch_size < 1:
                raise CostLimitReached(
                    f"Approved API cost limit ${cost_limit_usd:.2f} reached before "
                    f"{len(pending) - completed:,} remaining calls"
                )
            batch = pending[completed : completed + batch_size]
            futures = {
                executor.submit(
                    _call_one,
                    client=client,
                    unit=unit,
                    few_shots=few_shots,
                ): unit
                for unit in batch
            }
            for future in as_completed(futures):
                label, failure = future.result()
                save_result(label, failure)
            authentication_failure = next(
                (
                    failure
                    for failure in failures[-batch_size:]
                    if "HTTP 401" in failure.error
                    or "authentication_error" in failure.error
                ),
                None,
            )
            if authentication_failure is not None:
                raise RuntimeError(
                    "Anthropic authentication failed; stopping before submitting "
                    "additional batches. Update ANTHROPIC_API_KEY and resume."
                )
            completed += batch_size
            if completed >= next_report or completed == len(pending):
                print(
                    f"Processed {completed:,}/{len(pending):,} pending calls; "
                    f"validated {len(labels):,}; estimated API cost ${spent:.4f}",
                    flush=True,
                )
                while next_report <= completed:
                    next_report += 25
    return labels, failures


def _pilot(
    *,
    client: AnthropicMessagesClient,
    pilot_size: int,
    corpus_queue: list[TeacherInputRecord],
    gold_queue: list[TeacherInputRecord],
    few_shots: list[dict[str, Any]],
    paths: dict[str, Path],
) -> dict[str, Any]:
    if not 50 <= pilot_size <= 100:
        raise ValueError("--pilot-size must be between 50 and 100")
    units = select_pilot_units(corpus_queue, size=pilot_size)
    pilot_ids = {row.teacher_id for row in units}
    existing = [
        row
        for row in load_jsonl(paths["pilot"], TeacherLabelRecord)
        if row.teacher_id in pilot_ids
    ]
    labels, new_failures = _run_units(
        client=client,
        units=units,
        few_shots=few_shots,
        labels_path=paths["pilot"],
        failures_path=paths["pilot_rejected"],
        existing_labels=existing,
    )
    failures = load_jsonl(paths["pilot_rejected"], TeacherFailureRecord)
    relevant_failures = [row for row in failures if row.teacher_id in pilot_ids]
    summary = pilot_cost_estimate(
        labels,
        pilot_failures=relevant_failures,
        corpus_model_calls=sum(row.requires_model_call for row in corpus_queue),
        gold_model_calls=sum(row.requires_model_call for row in gold_queue),
    )
    summary.update(
        {
            "generated_at": datetime.now().astimezone().isoformat(),
            "model": client.model,
            "prompt_version": PROMPT_VERSION,
            "pilot_size": pilot_size,
            "api_errors": sum(
                row.error_type == "api_error" for row in relevant_failures
            ),
            "new_failures_this_run": len(new_failures),
        }
    )
    atomic_write_text(
        paths["pilot_summary"],
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
    )
    return summary


def _merge_corpus_labels(
    paths: dict[str, Path], auto_labels: list[TeacherLabelRecord]
) -> list[TeacherLabelRecord]:
    existing = load_jsonl(paths["labeled"], TeacherLabelRecord)
    pilot = load_jsonl(paths["pilot"], TeacherLabelRecord)
    merged = {row.teacher_id: row for row in [*auto_labels, *pilot, *existing]}
    records = sorted(merged.values(), key=lambda row: row.teacher_id)
    write_queue(paths["labeled"], records)
    return records


def _complete_shortages(
    *,
    client: AnthropicMessagesClient,
    approved: bool,
    max_cost_usd: float | None,
    corpus_queue: list[TeacherInputRecord],
    auto_labels: list[TeacherLabelRecord],
    few_shots: list[dict[str, Any]],
    paths: dict[str, Path],
) -> None:
    """Label only real-reason shortage units under an incremental cost gate."""
    if not approved or max_cost_usd is None:
        raise RuntimeError(
            "Shortage completion requires --approve-shortage-run and --max-cost-usd"
        )
    if max_cost_usd >= 5:
        raise RuntimeError("Shortage-only ceiling must stay below $5")

    existing = _merge_corpus_labels(paths, auto_labels)
    existing_ids = {row.teacher_id for row in existing}
    units = [
        row
        for row in corpus_queue
        if row.source == "shortage" and row.requires_model_call
    ]
    pending_at_start = [row for row in units if row.teacher_id not in existing_ids]
    phase_cost_at_start = _measured_phase_cost(paths)["measured_total_usd"]
    failures_at_start = len(
        load_jsonl(paths["rejected"], TeacherFailureRecord)
    )
    observed_costs = [
        row.usage.estimated_cost_usd
        for row in existing
        if row.source == "shortage"
        and row.label_method == "claude"
        and row.usage is not None
    ]
    average_cost = (
        sum(observed_costs) / len(observed_costs) if observed_costs else 0.0
    )
    projected_cost = len(pending_at_start) * average_cost * 1.02
    print(
        f"Shortage-only queue: {len(units):,} model-required; "
        f"{len(pending_at_start):,} pending; projected incremental cost "
        f"${projected_cost:.2f}; hard ceiling ${max_cost_usd:.2f}",
        flush=True,
    )

    stopped_by_cost_gate = False
    fatal_error: str | None = None
    try:
        _run_units(
            client=client,
            units=units,
            few_shots=few_shots,
            labels_path=paths["labeled"],
            failures_path=paths["rejected"],
            existing_labels=existing,
            cost_limit_usd=max_cost_usd,
            budget_existing_prompt_versions=set(),
            next_call_reserve_usd=0.02,
            max_workers=8,
        )
    except CostLimitReached as error:
        stopped_by_cost_gate = True
        print(f"Cost gate stopped shortage-only run: {error}", flush=True)
    except RuntimeError as error:
        fatal_error = str(error)
        print(f"Shortage-only run stopped: {error}", flush=True)

    final_labels = load_jsonl(paths["labeled"], TeacherLabelRecord)
    final_ids = {row.teacher_id for row in final_labels}
    remaining_shortages = [row for row in units if row.teacher_id not in final_ids]
    new_label_ids = final_ids - existing_ids
    incremental_label_cost = sum(
        row.usage.estimated_cost_usd
        for row in final_labels
        if row.teacher_id in new_label_ids and row.usage is not None
    )
    all_failures = load_jsonl(paths["rejected"], TeacherFailureRecord)
    new_failures = all_failures[failures_at_start:]
    incremental_failure_cost = _usage_total(new_failures)
    incremental_cost = incremental_label_cost + incremental_failure_cost
    phase_cost = _measured_phase_cost(paths)
    remaining_by_source = Counter(
        row.source
        for row in corpus_queue
        if row.requires_model_call and row.teacher_id not in final_ids
    )
    completion = {
        "status": (
            "complete" if not remaining_shortages else "incomplete"
        ),
        "completed_at": datetime.now().astimezone().isoformat(),
        "model": client.model,
        "prompt_version": PROMPT_VERSION,
        "required_shortage_calls": len(units),
        "pending_at_start": len(pending_at_start),
        "validated_this_run": len(new_label_ids),
        "remaining_shortage_calls": len(remaining_shortages),
        "validation_or_api_failures_this_run": len(new_failures),
        "projected_incremental_cost_usd": projected_cost,
        "incremental_cost_ceiling_usd": max_cost_usd,
        "measured_incremental_cost_usd": incremental_cost,
        "measured_phase_cost_at_start_usd": phase_cost_at_start,
        "measured_phase_cost_after_usd": phase_cost["measured_total_usd"],
        "stopped_by_cost_gate": stopped_by_cost_gate,
        "fatal_error": fatal_error,
    }
    atomic_write_text(
        paths["shortage_completion"],
        json.dumps(completion, indent=2) + "\n",
    )
    budget = {
        "updated_at": datetime.now().astimezone().isoformat(),
        "mode": "shortage_only_extension",
        "total_phase_ceiling_usd": phase_cost_at_start + max_cost_usd,
        "safety_margin_usd": 0.0,
        "effective_measured_ceiling_usd": phase_cost_at_start + max_cost_usd,
        "measured_start_usd": phase_cost_at_start,
        "measured_headroom_at_start_usd": max_cost_usd,
        "per_call_reserve_usd": 0.02,
    }
    atomic_write_text(paths["budget"], json.dumps(budget, indent=2) + "\n")
    finalization = {
        "status": (
            "shortages_complete_recalls_partial"
            if not remaining_shortages
            else "shortage_completion_incomplete"
        ),
        "finalized_at": datetime.now().astimezone().isoformat(),
        "final_total_phase_spend_usd": phase_cost["measured_total_usd"],
        "model_labeled_records": sum(
            row.label_method == "claude" for row in final_labels
        ),
        "deterministic_unknown_records": sum(
            row.label_method == "policy_unknown" for row in final_labels
        ),
        "human_gold_records": 400,
        "remaining_model_calls": sum(remaining_by_source.values()),
        "remaining_model_calls_by_source": dict(remaining_by_source),
        "reason": (
            "All non-gold shortage records with real FDA reasons are labeled; "
            "recall completion remains intentionally out of scope."
            if not remaining_shortages
            else "Shortage-only completion did not finish; no recall calls were attempted."
        ),
        "resume_supported": True,
    }
    atomic_write_text(
        paths["finalization"], json.dumps(finalization, indent=2) + "\n"
    )
    print(
        f"Shortage-only completion: {len(new_label_ids):,} new validated labels; "
        f"{len(remaining_shortages):,} shortage calls remain; "
        f"measured incremental cost ${incremental_cost:.4f}",
        flush=True,
    )


def _full(
    *,
    client: AnthropicMessagesClient,
    approved: bool,
    total_phase_ceiling_usd: float | None,
    safety_margin_usd: float,
    corpus_queue: list[TeacherInputRecord],
    gold_queue: list[TeacherInputRecord],
    auto_labels: list[TeacherLabelRecord],
    few_shots: list[dict[str, Any]],
    paths: dict[str, Path],
) -> None:
    summary = _load_pilot_summary(paths["pilot_summary"])
    if not approved or total_phase_ceiling_usd is None:
        raise RuntimeError(
            "Full labeling requires --approve-full-run and --total-phase-ceiling-usd"
        )
    if safety_margin_usd < 0 or safety_margin_usd >= total_phase_ceiling_usd:
        raise RuntimeError("Phase cost safety margin is invalid")
    if not summary or summary.get("pilot_attempted", 0) < 50:
        raise RuntimeError("A completed 50+ response pilot is required first")
    if summary.get("model") != client.model:
        raise RuntimeError("Pilot model does not match this full run")
    if summary.get("prompt_version") not in COMPATIBLE_PILOT_PROMPT_VERSIONS:
        raise RuntimeError("Pilot prompt is not compatible with this full run")

    phase_cost = _measured_phase_cost(paths)
    measured_start = phase_cost["measured_total_usd"]
    effective_ceiling = total_phase_ceiling_usd - safety_margin_usd
    budget = {
        "updated_at": datetime.now().astimezone().isoformat(),
        "total_phase_ceiling_usd": total_phase_ceiling_usd,
        "safety_margin_usd": safety_margin_usd,
        "effective_measured_ceiling_usd": effective_ceiling,
        "measured_start_usd": measured_start,
        "measured_headroom_at_start_usd": max(0.0, effective_ceiling - measured_start),
    }
    atomic_write_text(paths["budget"], json.dumps(budget, indent=2) + "\n")
    if measured_start >= effective_ceiling:
        print(
            f"Total-phase cost gate already reached: ${measured_start:.4f} "
            f"measured against ${effective_ceiling:.2f} effective ceiling",
            flush=True,
        )
        return
    print(
        f"Total-phase budget: ${measured_start:.4f} measured; "
        f"${effective_ceiling - measured_start:.4f} usable headroom; "
        f"${safety_margin_usd:.2f} reserved below ${total_phase_ceiling_usd:.2f}",
        flush=True,
    )

    corpus_labels = _merge_corpus_labels(paths, auto_labels)
    corpus_units = [row for row in corpus_queue if row.requires_model_call]
    try:
        corpus_labels, _ = _run_units(
            client=client,
            units=corpus_units,
            few_shots=few_shots,
            labels_path=paths["labeled"],
            failures_path=paths["rejected"],
            existing_labels=corpus_labels,
            cost_limit_usd=effective_ceiling,
            already_spent_usd=measured_start,
            budget_existing_prompt_versions=set(),
            next_call_reserve_usd=0.02,
            max_workers=8,
        )
    except CostLimitReached as error:
        print(f"Cost gate stopped corpus run: {error}", flush=True)
        return

    gold_existing = load_jsonl(paths["gold_predictions"], TeacherLabelRecord)
    gold_map = {row.teacher_id: row for row in gold_existing}
    for unit in gold_queue:
        if not unit.requires_model_call:
            gold_map.setdefault(unit.teacher_id, auto_unknown_label(unit))
    gold_predictions = sorted(gold_map.values(), key=lambda row: row.teacher_id)
    write_queue(paths["gold_predictions"], gold_predictions)
    if len(gold_predictions) != len(gold_queue):
        raise RuntimeError(
            "Gold evaluation is incomplete; run --mode gold-eval separately. "
            "Full mode will not mix corpus and evaluation calls."
        )


def _gold_evaluation(
    *,
    client: AnthropicMessagesClient,
    max_cost_usd: float,
    gold_queue: list[TeacherInputRecord],
    few_shots: list[dict[str, Any]],
    paths: dict[str, Path],
) -> None:
    """Run only the blind gold evaluation, never the non-gold corpus."""
    summary = _load_pilot_summary(paths["pilot_summary"])
    if not summary or summary.get("pilot_attempted", 0) < 50:
        raise RuntimeError("A completed 50+ response pilot is required first")
    if (
        summary.get("model") != client.model
        or summary.get("prompt_version") != PROMPT_VERSION
    ):
        raise RuntimeError("Pilot model/prompt does not match this gold evaluation")

    existing = load_jsonl(paths["gold_predictions"], TeacherLabelRecord)
    prediction_map = {row.teacher_id: row for row in existing}
    for unit in gold_queue:
        if not unit.requires_model_call:
            prediction_map.setdefault(unit.teacher_id, auto_unknown_label(unit))
    predictions = sorted(prediction_map.values(), key=lambda row: row.teacher_id)
    write_queue(paths["gold_predictions"], predictions)

    model_units = [row for row in gold_queue if row.requires_model_call]
    _run_units(
        client=client,
        units=model_units,
        few_shots=few_shots,
        labels_path=paths["gold_predictions"],
        failures_path=paths["gold_rejected"],
        existing_labels=predictions,
        cost_limit_usd=max_cost_usd,
    )


def _report(
    *,
    snapshot: Path,
    model: str,
    candidates: list[GoldCandidate],
    human_labels: list[GoldLabelRecord],
    corpus_queue: list[TeacherInputRecord],
    paths: dict[str, Path],
    report_json: Path,
    report_md: Path,
) -> dict[str, Any]:
    corpus_labels = load_jsonl(paths["labeled"], TeacherLabelRecord)
    gold_predictions = load_jsonl(paths["gold_predictions"], TeacherLabelRecord)
    pilot_labels = load_jsonl(paths["pilot"], TeacherLabelRecord)
    pilot_failures = load_jsonl(paths["pilot_rejected"], TeacherFailureRecord)
    full_failures = load_jsonl(paths["rejected"], TeacherFailureRecord)
    gold_failures = load_jsonl(paths["gold_rejected"], TeacherFailureRecord)
    all_model_labels = [
        row for row in [*pilot_labels, *corpus_labels, *gold_predictions] if row.usage
    ]
    # Corpus pilot records are later copied into labeled.jsonl; count each API response once.
    unique_passes = {row.teacher_id: row for row in all_model_labels}
    validation_failures = [
        row
        for row in [*pilot_failures, *full_failures, *gold_failures]
        if row.error_type == "validation_error"
    ]
    api_errors = [
        row
        for row in [*pilot_failures, *full_failures, *gold_failures]
        if row.error_type == "api_error"
    ]
    responses = len(unique_passes) + len(validation_failures)
    evaluation = gold_evaluation_metrics(candidates, human_labels, gold_predictions)
    gold_model_outputs = [row for row in gold_predictions if row.usage is not None]
    gold_validation_failures = [
        row for row in gold_failures if row.error_type == "validation_error"
    ]
    gold_api_failures = [row for row in gold_failures if row.error_type == "api_error"]
    predicted_gold_ids = {row.teacher_id for row in gold_predictions}
    gold_responses = len(gold_model_outputs) + len(gold_validation_failures)
    baseline_agreed = sum(
        row.baseline_primary_cause == row.event.primary_cause for row in human_labels
    )
    capacity_predictions = sum(
        row.event.primary_cause == "manufacturing_capacity"
        for row in [*corpus_labels, *gold_predictions]
    )
    corpus_model_ids = {
        row.teacher_id for row in corpus_queue if row.requires_model_call
    }
    completed_corpus_model_ids = {
        row.teacher_id
        for row in [*pilot_labels, *corpus_labels]
        if row.usage is not None and row.teacher_id in corpus_model_ids
    }
    pilot_summary = _load_pilot_summary(paths["pilot_summary"])
    remaining_corpus_calls = len(corpus_model_ids - completed_corpus_model_ids)
    projected_remaining_corpus_cost = (
        remaining_corpus_calls
        * float(pilot_summary["average_attempt_cost_usd"])
        / float(pilot_summary["validation_pass_rate"])
        if pilot_summary and pilot_summary["validation_pass_rate"]
        else None
    )
    current_prompt_corpus_outputs = [
        row
        for row in corpus_labels
        if row.usage is not None and row.prompt_version == PROMPT_VERSION
    ]
    phase_cost = _measured_phase_cost(paths)
    phase_budget = _load_pilot_summary(paths["budget"])
    phase_finalization = _load_pilot_summary(paths["finalization"])
    shortage_completion = _load_pilot_summary(paths["shortage_completion"])
    corpus_model_records = {
        row.teacher_id: row
        for row in [*pilot_labels, *corpus_labels]
        if row.usage is not None
    }
    metrics = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "snapshot": snapshot.name,
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "corpus_prompt_versions": dict(
            Counter(row.prompt_version for row in corpus_model_records.values())
        ),
        "gold_prompt_versions": dict(
            Counter(
                row.prompt_version for row in gold_predictions if row.usage is not None
            )
        ),
        "api_key_available": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "queue": {
            "total": len(corpus_queue),
            "model_calls": sum(row.requires_model_call for row in corpus_queue),
            "policy_unknown": sum(not row.requires_model_call for row in corpus_queue),
            "by_source": dict(Counter(row.source for row in corpus_queue)),
            "model_calls_by_source": dict(
                Counter(row.source for row in corpus_queue if row.requires_model_call)
            ),
            "recall_filings_represented": sum(
                int(row.source_context["filing_count"])
                for row in corpus_queue
                if row.source == "recall"
            ),
        },
        "corpus_labels": len({row.teacher_id for row in corpus_labels}),
        "corpus_label_methods": dict(
            Counter(row.label_method for row in corpus_labels)
        ),
        "validation": {
            "responses": responses,
            "passed": len(unique_passes),
            "failed": len(validation_failures),
            "pass_percentage": 100 * len(unique_passes) / responses
            if responses
            else 0.0,
            "api_errors": len(api_errors),
        },
        "pilot": pilot_summary,
        "remaining_corpus": {
            "model_calls": remaining_corpus_calls,
            "projected_cost_usd": projected_remaining_corpus_cost,
        },
        "current_corpus_run": {
            "prompt_version": PROMPT_VERSION,
            "validated_model_outputs": len(current_prompt_corpus_outputs),
            "estimated_cost_usd": sum(
                row.usage.estimated_cost_usd for row in current_prompt_corpus_outputs
            ),
        },
        "phase_cost": phase_cost,
        "phase_budget": phase_budget,
        "phase_finalization": phase_finalization,
        "shortage_completion": shortage_completion,
        "gold_evaluation": evaluation,
        "gold_evaluation_run": {
            "model_required": 392,
            "policy_unknown": 8,
            "validated_model_outputs": len(gold_model_outputs),
            "validation_failures": len(gold_validation_failures),
            "api_errors": len(gold_api_failures),
            "recovered_api_errors": sum(
                row.teacher_id in predicted_gold_ids for row in gold_api_failures
            ),
            "unresolved_api_errors": sum(
                row.teacher_id not in predicted_gold_ids for row in gold_api_failures
            ),
            "local_sandbox_dns_errors": sum(
                "nodename nor servname" in row.error for row in gold_api_failures
            ),
            "validation_pass_percentage": 100 * len(gold_model_outputs) / gold_responses
            if gold_responses
            else 0.0,
            "input_tokens": sum(row.usage.input_tokens for row in gold_model_outputs),
            "output_tokens": sum(row.usage.output_tokens for row in gold_model_outputs),
            "cache_creation_input_tokens": sum(
                row.usage.cache_creation_input_tokens for row in gold_model_outputs
            ),
            "cache_read_input_tokens": sum(
                row.usage.cache_read_input_tokens for row in gold_model_outputs
            ),
            "estimated_cost_usd": sum(
                row.usage.estimated_cost_usd for row in gold_model_outputs
            ),
        },
        "baseline_full_gold": {
            "agreed": baseline_agreed,
            "percentage": 100 * baseline_agreed / len(human_labels),
        },
        "manufacturing_capacity_predictions": capacity_predictions,
        "human_manufacturing_capacity": sum(
            row.event.primary_cause == "manufacturing_capacity" for row in human_labels
        ),
    }
    atomic_write_text(
        report_json, json.dumps(metrics, indent=2, ensure_ascii=False) + "\n"
    )
    atomic_write_text(report_md, render_teacher_report(metrics))
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("prepare", "pilot", "gold-eval", "full", "shortages", "report"),
        default="prepare",
    )
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--gold-labels", type=Path, default=DEFAULT_GOLD_LABELS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_REPORT_MD)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--pilot-size", type=int, default=DEFAULT_PILOT_SIZE)
    parser.add_argument("--approve-full-run", action="store_true")
    parser.add_argument("--approve-shortage-run", action="store_true")
    parser.add_argument("--max-cost-usd", type=float)
    parser.add_argument("--total-phase-ceiling-usd", type=float)
    parser.add_argument("--phase-cost-safety-margin-usd", type=float, default=0.25)
    args = parser.parse_args()

    if args.max_cost_usd is not None and args.max_cost_usd <= 0:
        parser.error("--max-cost-usd must be positive")
    if args.total_phase_ceiling_usd is not None and args.total_phase_ceiling_usd <= 0:
        parser.error("--total-phase-ceiling-usd must be positive")
    if args.phase_cost_safety_margin_usd < 0:
        parser.error("--phase-cost-safety-margin-usd cannot be negative")
    snapshot = args.snapshot or _latest_snapshot()
    paths = _paths(args.output_dir)
    candidates, labels, corpus_queue, gold_queue, auto_labels = _prepare(
        snapshot=snapshot,
        candidates_path=args.candidates,
        labels_path=args.gold_labels,
        output_dir=args.output_dir,
    )
    few_shots = build_few_shot_examples(candidates, labels)

    if args.mode in {"pilot", "gold-eval", "full", "shortages"}:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Export it in the invoking shell; "
                "do not store it in source control."
            )
        client = AnthropicMessagesClient(api_key=api_key, model=args.model)
        if args.mode == "pilot":
            summary = _pilot(
                client=client,
                pilot_size=args.pilot_size,
                corpus_queue=corpus_queue,
                gold_queue=gold_queue,
                few_shots=few_shots,
                paths=paths,
            )
            print(
                f"Pilot responses: {summary['pilot_attempted']}/{summary['pilot_size']}; "
                f"API cost ${summary['usage']['all_attempt_cost_usd']:.4f}; "
                f"projected total ${summary['projected_total_cost_usd']:.2f}"
            )
        elif args.mode == "gold-eval":
            if args.max_cost_usd is None:
                raise RuntimeError("Gold evaluation requires --max-cost-usd")
            _gold_evaluation(
                client=client,
                max_cost_usd=args.max_cost_usd,
                gold_queue=gold_queue,
                few_shots=few_shots,
                paths=paths,
            )
        elif args.mode == "full":
            _full(
                client=client,
                approved=args.approve_full_run,
                total_phase_ceiling_usd=args.total_phase_ceiling_usd,
                safety_margin_usd=args.phase_cost_safety_margin_usd,
                corpus_queue=corpus_queue,
                gold_queue=gold_queue,
                auto_labels=auto_labels,
                few_shots=few_shots,
                paths=paths,
            )
        else:
            _complete_shortages(
                client=client,
                approved=args.approve_shortage_run,
                max_cost_usd=args.max_cost_usd,
                corpus_queue=corpus_queue,
                auto_labels=auto_labels,
                few_shots=few_shots,
                paths=paths,
            )

    metrics = _report(
        snapshot=snapshot,
        model=args.model,
        candidates=candidates,
        human_labels=labels,
        corpus_queue=corpus_queue,
        paths=paths,
        report_json=args.report_json,
        report_md=args.report_md,
    )
    print(f"Teacher queue: {paths['queue']} ({metrics['queue']['total']:,} units)")
    print(f"Report: {args.report_md}")
    if args.mode == "prepare" and not metrics["api_key_available"]:
        print("Pilot is ready but not run: ANTHROPIC_API_KEY is not set.")


if __name__ == "__main__":
    main()
