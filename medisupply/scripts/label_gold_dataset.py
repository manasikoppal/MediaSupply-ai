#!/usr/bin/env python3
"""Resumable interactive review for the Phase 9 gold dataset."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import termios
import tty
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from src.models.baseline_classifier import CATEGORY_PRIORITY, STAGES
from src.models.gold_dataset import (
    GoldCandidate,
    GoldLabelRecord,
    atomic_write_jsonl,
    gold_metrics,
    load_jsonl,
    render_gold_report,
    stratified_split,
)
from src.models.taxonomy_guidance import CATEGORY_GUIDANCE

DEFAULT_CANDIDATES = REPOSITORY_ROOT / "data" / "gold" / "candidates.jsonl"
DEFAULT_LABELS = REPOSITORY_ROOT / "data" / "gold" / "labeled.jsonl"
DEFAULT_REPORT_JSON = REPOSITORY_ROOT / "reports" / "gold_dataset_quality.json"
DEFAULT_REPORT_MD = REPOSITORY_ROOT / "reports" / "gold_dataset_quality.md"

def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, path)
    except BaseException:
        if temp_path:
            temp_path.unlink(missing_ok=True)
        raise


def _load_state(
    candidates_path: Path, labels_path: Path
) -> tuple[list[GoldCandidate], list[GoldLabelRecord]]:
    candidates = load_jsonl(candidates_path, GoldCandidate)
    if not candidates:
        raise RuntimeError(f"No candidates found in {candidates_path}")
    labels = load_jsonl(labels_path, GoldLabelRecord)
    candidate_index = {candidate.candidate_id: candidate for candidate in candidates}
    if len(candidate_index) != len(candidates):
        raise ValueError("Candidate queue contains duplicate candidate IDs")
    seen_labels = set()
    for label in labels:
        if label.candidate_id in seen_labels:
            raise ValueError(f"Duplicate label for {label.candidate_id}")
        seen_labels.add(label.candidate_id)
        candidate = candidate_index.get(label.candidate_id)
        if candidate is None:
            raise ValueError(f"Label has no candidate: {label.candidate_id}")
        if (
            label.source != candidate.source
            or label.source_record_id != candidate.source_record_id
            or label.raw_text != candidate.raw_text
        ):
            raise ValueError(f"Label provenance differs from candidate: {label.candidate_id}")
    return sorted(candidates, key=lambda row: row.sequence), labels


def _print_status(
    candidates: list[GoldCandidate], labels: list[GoldLabelRecord]
) -> None:
    labeled_ids = {label.candidate_id for label in labels}
    agreements = sum(
        label.baseline_primary_cause == label.event.primary_cause for label in labels
    )
    print(f"Progress: {len(labels):,}/{len(candidates):,} labeled")
    print(f"Remaining: {len(candidates) - len(labeled_ids):,}")
    if labels:
        print(f"Agreement so far: {agreements / len(labels):.2%}")
        print("Final labels so far:")
        for category, count in Counter(
            label.event.primary_cause for label in labels
        ).most_common():
            print(f"  {category}: {count}")


def _display_candidate(candidate: GoldCandidate, labeled: int, total: int) -> None:
    baseline = candidate.baseline
    print("\n" + "=" * 88)
    print(
        f"Candidate {candidate.sequence}/400 | review progress {labeled}/{total} | "
        f"{candidate.source}:{candidate.source_record_id}"
    )
    print(
        f"Sampling stratum: {candidate.sampling_stratum} "
        f"({candidate.selection_reason})"
    )
    print(f"\nFDA {candidate.text_field}:")
    print(candidate.raw_text or "[FDA did not supply a shortage_reason]")
    print("\nSource context:")
    for key, value in candidate.source_context.items():
        if value not in (None, "", [], {}):
            compact = " ".join(str(value).split())
            print(f"  {key}: {compact[:500]}")
    print("\nBaseline suggestion:")
    print(f"  primary_cause: {baseline.primary_cause}")
    print(
        f"  confidence: {baseline.confidence} ({baseline.confidence_score:.3f}); "
        f"fallback={baseline.fallback}; collision={baseline.collision}"
    )
    matches = ", ".join(baseline.matched_categories) or "none"
    print(f"  all matched categories: {matches}")
    print(f"  stage: {baseline.event.supply_chain_stage}")
    print(f"  severity: {baseline.event.severity}")


def _fast_mode_eligible(candidate: GoldCandidate) -> bool:
    """Return true only for the exact, high-confidence safe fast-mode stratum."""
    baseline = candidate.baseline
    return (
        candidate.selection_reason == "baseline_confident"
        and baseline.confidence == "high"
        and baseline.confidence_score == 1.0
        and baseline.confident_keyword_match
        and not baseline.collision
    )


def _group_key(candidate: GoldCandidate) -> tuple[str, str, str | None, str]:
    """Identify candidates with exactly the same FDA text and baseline cause."""
    return (
        candidate.source,
        candidate.text_field,
        candidate.raw_text,
        candidate.baseline.primary_cause,
    )


def _build_review_units(
    pending: list[GoldCandidate], *, grouped_mode: bool
) -> list[list[GoldCandidate]]:
    """Group exact-text duplicates only when every member is fast-mode eligible."""
    if not grouped_mode:
        return [[candidate] for candidate in pending]

    eligible_groups: dict[tuple[str, str, str | None, str], list[GoldCandidate]] = (
        defaultdict(list)
    )
    for candidate in pending:
        if _fast_mode_eligible(candidate):
            eligible_groups[_group_key(candidate)].append(candidate)

    units: list[list[GoldCandidate]] = []
    assigned: set[str] = set()
    for candidate in pending:
        if candidate.candidate_id in assigned:
            continue
        if _fast_mode_eligible(candidate):
            group = eligible_groups[_group_key(candidate)]
            units.append(group)
            assigned.update(member.candidate_id for member in group)
        else:
            units.append([candidate])
            assigned.add(candidate.candidate_id)
    return units


def _read_single_key(prompt: str) -> str:
    """Read one key immediately on a TTY, with a line-input fallback."""
    if not sys.stdin.isatty():
        return input(prompt).strip().lower()[:1]
    print(prompt, end="", flush=True)
    descriptor = sys.stdin.fileno()
    previous = termios.tcgetattr(descriptor)
    try:
        tty.setraw(descriptor)
        key = sys.stdin.read(1)
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, previous)
    if key == "\x03":
        raise KeyboardInterrupt
    print(key if key not in {"\r", "\n"} else "")
    return key.lower()


def _fast_choice(candidate: GoldCandidate, labeled: int, total: int) -> str:
    text = " ".join(
        (candidate.raw_text or "[FDA did not supply a shortage_reason]").split()
    )
    print(
        f"[{candidate.sequence}/400 | {labeled}/{total} labeled] "
        f"{candidate.source}:{candidate.source_record_id} | {text} "
        f"=> {candidate.baseline.primary_cause} (1.000, no collision)"
    )
    while True:
        choice = _read_single_key("[a]pprove  [f]ull review  [s]kip  [q]uit: ")
        if choice in {"", "a", "\r", "\n"}:
            return "__accept__"
        if choice == "f":
            return "__full__"
        if choice in {"s", "q"}:
            return choice
        print("Press a, f, s, or q.")


def _group_choice(candidates: list[GoldCandidate], labeled: int, total: int) -> str:
    """Show one approval prompt for a safe exact-text duplicate group."""
    exemplar = candidates[0]
    text = " ".join(
        (exemplar.raw_text or "[FDA did not supply a shortage_reason]").split()
    )
    severities = Counter(candidate.baseline.event.severity for candidate in candidates)
    severity_summary = ", ".join(
        f"{severity}={count}" for severity, count in sorted(severities.items())
    )
    context_lines = []
    for field in ("classification", "status", "availability"):
        values = Counter(
            str(candidate.source_context[field])
            for candidate in candidates
            if candidate.source_context.get(field) not in (None, "", [], {})
        )
        if values:
            rendered = ", ".join(
                f"{value}={count}" for value, count in values.most_common()
            )
            context_lines.append(f"  {field}: {rendered}")

    print("\n" + "=" * 88)
    print(
        f"Exact-text group: {len(candidates)} records | "
        f"review progress {labeled}/{total}"
    )
    print(f"FDA {exemplar.text_field}: {text}")
    print(f"Baseline suggestion: {exemplar.baseline.primary_cause} (1.000, no collision)")
    print(f"Record-specific severities preserved: {severity_summary}")
    if context_lines:
        print("Context across records:")
        print("\n".join(context_lines))
    record_ids = ", ".join(candidate.source_record_id for candidate in candidates[:8])
    if len(candidates) > 8:
        record_ids += f", … (+{len(candidates) - 8} more)"
    print(f"Records: {record_ids}")

    while True:
        choice = _read_single_key(
            "[a]pprove all  [f]ull individual review  [s]kip group  [q]uit: "
        )
        if choice in {"", "a", "\r", "\n"}:
            return "__accept_group__"
        if choice == "f":
            return "__individual__"
        if choice in {"s", "q"}:
            return choice
        print("Press a, f, s, or q.")


def _choose_category(default: str) -> str | None:
    print("\nPress Enter to accept the full baseline event, or choose a primary cause:")
    for index, category in enumerate(CATEGORY_PRIORITY, 1):
        marker = " (baseline)" if category == default else ""
        print(f"  {index:2d}. {category}{marker}")
    print("  e. edit stage/secondary causes while keeping the baseline primary cause")
    print("  ?. show concise category guidance")
    print("  s. skip for this session")
    print("  q. save and quit")
    while True:
        choice = input("Choice: ").strip().lower()
        if choice == "":
            return "__accept__"
        if choice == "?":
            print("")
            for category in CATEGORY_PRIORITY:
                print(f"  {category}: {CATEGORY_GUIDANCE[category]}")
            print("")
            continue
        if choice in {"e", "s", "q"}:
            return choice
        if choice.isdigit() and 1 <= int(choice) <= len(CATEGORY_PRIORITY):
            return CATEGORY_PRIORITY[int(choice) - 1]
        print("Enter a listed number, Enter, e, ?, s, or q.")


def _prompt_event(candidate: GoldCandidate, primary_cause: str):
    baseline_event = candidate.baseline.event
    default_stage = (
        baseline_event.supply_chain_stage
        if primary_cause == baseline_event.primary_cause
        else STAGES[primary_cause]
    )
    stage = input(f"Supply-chain stage [{default_stage}]: ").strip() or default_stage

    severity_values = ("low", "medium", "high")
    default_severity = baseline_event.severity
    while True:
        severity = (
            input(f"Severity low/medium/high [{default_severity}]: ").strip().lower()
            or default_severity
        )
        if severity in severity_values:
            break
        print("Severity must be low, medium, or high.")

    if primary_cause == baseline_event.primary_cause:
        default_secondary = list(baseline_event.secondary_causes)
    else:
        default_secondary = (
            ["recall_event"]
            if candidate.source == "recall" and primary_cause != "recall"
            else []
        )
    rendered_default = ", ".join(default_secondary)
    secondary_input = input(
        f"Secondary causes, comma-separated [{rendered_default or 'none'}]: "
    ).strip()
    secondary = (
        [value.strip() for value in secondary_input.split(",") if value.strip()]
        if secondary_input
        else default_secondary
    )
    additional_evidence = input("Optional additional evidence (Enter for none): ").strip()
    evidence = list(baseline_event.evidence)
    if additional_evidence:
        evidence.append(additional_evidence)

    payload = baseline_event.model_dump()
    payload.update(
        {
            "primary_cause": primary_cause,
            "secondary_causes": secondary,
            "supply_chain_stage": stage,
            "severity": severity,
            "evidence": evidence,
        }
    )
    return baseline_event.__class__.model_validate(payload)


def _make_label(
    candidate: GoldCandidate, *, annotator: str, choice: str
) -> GoldLabelRecord:
    if choice == "__accept__":
        event = candidate.baseline.event.model_copy(deep=True)
    else:
        primary_cause = (
            candidate.baseline.primary_cause if choice == "e" else choice
        )
        event = _prompt_event(candidate, primary_cause)

    disagreement_note = None
    if event.primary_cause != candidate.baseline.primary_cause:
        while not disagreement_note:
            disagreement_note = input(
                "Why does the baseline label need correction? (required): "
            ).strip()

    return GoldLabelRecord(
        candidate_id=candidate.candidate_id,
        taxonomy_version=candidate.taxonomy_version,
        snapshot=candidate.snapshot,
        sampling_stratum=candidate.sampling_stratum,
        source=candidate.source,
        source_record_id=candidate.source_record_id,
        text_field=candidate.text_field,
        raw_text=candidate.raw_text,
        baseline_primary_cause=candidate.baseline.primary_cause,
        baseline_confidence=candidate.baseline.confidence,
        baseline_collision=candidate.baseline.collision,
        event=event,
        annotator=annotator,
        labeled_at=datetime.now().astimezone().isoformat(),
        disagreement_note=disagreement_note,
    )


def run_interactive(
    candidates: list[GoldCandidate],
    labels: list[GoldLabelRecord],
    *,
    labels_path: Path,
    annotator: str,
    limit: int | None,
    fast_mode: bool,
    grouped_mode: bool,
) -> None:
    labeled_ids = {label.candidate_id for label in labels}
    pending = [
        candidate for candidate in candidates if candidate.candidate_id not in labeled_ids
    ]
    review_units = _build_review_units(pending, grouped_mode=grouped_mode)
    sequence_by_id = {
        candidate.candidate_id: candidate.sequence for candidate in candidates
    }
    reviewed_this_run = 0
    quit_requested = False
    for original_unit in review_units:
        if limit is not None and reviewed_this_run >= limit:
            break

        unit = original_unit
        if limit is not None:
            unit = unit[: limit - reviewed_this_run]

        if grouped_mode and len(unit) > 1:
            group_choice = _group_choice(unit, len(labels), len(candidates))
            if group_choice == "q":
                break
            if group_choice == "s":
                continue
            if group_choice == "__accept_group__":
                new_labels = [
                    _make_label(candidate, annotator=annotator, choice="__accept__")
                    for candidate in unit
                ]
                labels.extend(new_labels)
                labels.sort(key=lambda row: sequence_by_id[row.candidate_id])
                atomic_write_jsonl(labels_path, labels)
                reviewed_this_run += len(new_labels)
                print(
                    f"Saved {len(new_labels)} exact-text records -> "
                    f"{new_labels[0].event.primary_cause}"
                )
                continue

        for candidate in unit:
            if limit is not None and reviewed_this_run >= limit:
                break
            force_full_review = grouped_mode and len(unit) > 1
            if (
                not force_full_review
                and (fast_mode or grouped_mode)
                and _fast_mode_eligible(candidate)
            ):
                choice = _fast_choice(candidate, len(labels), len(candidates))
                if choice == "__full__":
                    _display_candidate(candidate, len(labels), len(candidates))
                    choice = _choose_category(candidate.baseline.primary_cause)
            else:
                _display_candidate(candidate, len(labels), len(candidates))
                choice = _choose_category(candidate.baseline.primary_cause)
            if choice == "q":
                quit_requested = True
                break
            if choice == "s":
                continue
            label = _make_label(candidate, annotator=annotator, choice=choice)
            labels.append(label)
            labels.sort(key=lambda row: sequence_by_id[row.candidate_id])
            atomic_write_jsonl(labels_path, labels)
            reviewed_this_run += 1
            print(f"Saved {label.candidate_id} -> {label.event.primary_cause}")
        if quit_requested:
            break
    print("")
    _print_status(candidates, labels)
    print(f"Labels: {labels_path}")


def finalize(
    candidates: list[GoldCandidate],
    labels: list[GoldLabelRecord],
    *,
    output_dir: Path,
    report_json: Path,
    report_md: Path,
) -> None:
    candidate_ids = {candidate.candidate_id for candidate in candidates}
    label_ids = {label.candidate_id for label in labels}
    missing = candidate_ids - label_ids
    if missing:
        raise RuntimeError(
            f"Cannot finalize: {len(missing)} of {len(candidates)} candidates remain unlabeled"
        )
    if label_ids - candidate_ids:
        raise RuntimeError("Labels contain records outside the candidate queue")

    splits = stratified_split(labels)
    output_dir.mkdir(parents=True, exist_ok=True)
    for split, records in splits.items():
        atomic_write_jsonl(output_dir / f"{split}.jsonl", records)
    metrics = gold_metrics(labels, splits)
    _atomic_text(report_json, json.dumps(metrics, indent=2, ensure_ascii=False) + "\n")
    _atomic_text(report_md, render_gold_report(metrics))
    print("Gold dataset finalized and validated.")
    for split, records in splits.items():
        print(f"  {split}: {len(records):,}")
    print(f"Report: {report_md}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--annotator", default="human")
    parser.add_argument("--limit", type=int, help="Maximum records to review this run")
    parser.add_argument(
        "--fast-mode",
        action="store_true",
        help="One-key approval only for exact 1.000-confidence, non-collision baseline_confident records",
    )
    parser.add_argument(
        "--grouped-mode",
        action="store_true",
        help="Review identical FDA text once when every grouped record is fast-mode eligible; all other records retain full review",
    )
    parser.add_argument("--status", action="store_true", help="Show progress without prompting")
    parser.add_argument("--finalize", action="store_true", help="Create splits and final report")
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_REPORT_MD)
    args = parser.parse_args()

    candidates, labels = _load_state(args.candidates, args.labels)
    if args.status:
        _print_status(candidates, labels)
        return
    if args.finalize:
        finalize(
            candidates,
            labels,
            output_dir=args.labels.parent,
            report_json=args.report_json,
            report_md=args.report_md,
        )
        return
    run_interactive(
        candidates,
        labels,
        labels_path=args.labels,
        annotator=args.annotator,
        limit=args.limit,
        fast_mode=args.fast_mode,
        grouped_mode=args.grouped_mode,
    )


if __name__ == "__main__":
    main()
