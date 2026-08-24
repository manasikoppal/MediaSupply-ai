"""Claude teacher-labeling queue, prompts, validation, usage, and evaluation."""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from .baseline_classifier import STAGES
from .create_gold_sample import _source_id
from .gold_dataset import GoldCandidate, GoldLabelRecord, atomic_write_jsonl
from .phase12_dataset import incident_identity, reason_text_id, shortage_incident_id
from .schema import (
    PRIMARY_CAUSE_TAXONOMY_VERSION,
    DisruptionEvent,
    PrimaryCause,
)
from .taxonomy_guidance import CATEGORY_GUIDANCE

DEFAULT_MODEL = "claude-sonnet-5"
PROMPT_VERSION = "phase10_teacher_v1_1_temperature_boundary"
COMPATIBLE_PILOT_PROMPT_VERSIONS = frozenset({"phase10_teacher_v1", PROMPT_VERSION})
MISSING_REASON_SENTINEL = "[FDA reason missing]"
DEFAULT_PILOT_SIZE = 50
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"

MODEL_PRICING_USD_PER_MTOK = {
    "claude-sonnet-5": {
        "input": 2.0,
        "output": 10.0,
        "cache_creation": 2.5,
        "cache_read": 0.2,
    }
}

FEW_SHOT_CANDIDATE_IDS = (
    "recall:D-044-2013",  # genuine potency + labeling dual cause
    "recall:D-0538-2025",  # shipping delay causes temperature excursion
    "recall:D-1330-2020",  # regulatory + sterility collision
    "recall:D-0013-2025",  # inactive-ingredient label mismatch
    "recall:D-0616-2021",  # temperature abuse is a quality/stability problem
    "recall:D-227-2013",  # non-sterility outranks misleading-label framing
)

EDGE_FAMILY_PATTERNS = {
    "temperature_abuse": re.compile(
        r"temperature abuse|temperature excursion", re.IGNORECASE
    ),
    "defective_delivery_system": re.compile(
        r"defective delivery system", re.IGNORECASE
    ),
    "subpotent_phrasing": re.compile(r"\bsubpotent\b|\bsub-potent\b", re.IGNORECASE),
    "tablet_imprint": re.compile(
        r"tablet imprint|incorrect imprint|missing imprint", re.IGNORECASE
    ),
    "undeclared_excipients": re.compile(
        r"undeclared excipient|incorrect excipient", re.IGNORECASE
    ),
    "shipping_quality_collision": re.compile(
        r"(?:shipping|transit) delay.*temperature|temperature.*(?:shipping|transit) delay",
        re.IGNORECASE,
    ),
    "dual_cause_and": re.compile(r"\b(?:and|additionally|also)\b", re.IGNORECASE),
}


class TeacherInputRecord(BaseModel):
    """One incident/text unit awaiting policy or Claude labeling."""

    model_config = ConfigDict(extra="forbid")

    teacher_id: str
    snapshot: str
    dataset_scope: Literal["corpus", "gold_evaluation"]
    source: Literal["shortage", "recall"]
    source_record_ids: list[str] = Field(min_length=1)
    incident_id: str
    reason_text_id: str
    raw_text: str
    source_raw_text: str | None
    source_context: dict[str, Any]
    requires_model_call: bool


class TeacherUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    estimated_cost_usd: float = 0.0


class TeacherLabelRecord(BaseModel):
    """A validated teacher or deterministic unknown-policy annotation."""

    model_config = ConfigDict(extra="forbid")

    teacher_id: str
    taxonomy_version: str
    snapshot: str
    dataset_scope: Literal["corpus", "gold_evaluation"]
    source: Literal["shortage", "recall"]
    source_record_ids: list[str]
    incident_id: str
    reason_text_id: str
    raw_text: str
    source_raw_text: str | None
    source_context: dict[str, Any]
    event: DisruptionEvent
    annotator: Literal["teacher"] = "teacher"
    label_method: Literal["claude", "policy_unknown"]
    model: str | None
    prompt_version: str
    labeled_at: str
    validation_status: Literal["passed"] = "passed"
    request_id: str | None = None
    usage: TeacherUsage | None = None


class TeacherFailureRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    teacher_id: str
    dataset_scope: Literal["corpus", "gold_evaluation"]
    model: str
    prompt_version: str
    failed_at: str
    error_type: Literal["api_error", "validation_error"]
    error: str
    raw_output: str | None = None
    request_id: str | None = None
    usage: TeacherUsage | None = None


class TeacherValidationError(ValueError):
    pass


def _context(source: str, row: dict[str, Any]) -> dict[str, Any]:
    if source == "recall":
        fields = (
            "event_id",
            "classification",
            "status",
            "recalling_firm",
            "recall_initiation_date",
            "product_description",
        )
    else:
        fields = (
            "package_ndc",
            "generic_name",
            "company_name",
            "status",
            "availability",
            "related_info",
            "initial_posting_date",
        )
    return {field: row.get(field) for field in fields}


def _raw_text(source: str, row: dict[str, Any]) -> str | None:
    field = "reason_for_recall" if source == "recall" else "shortage_reason"
    value = row.get(field)
    if value is None or not str(value).strip():
        return None
    return str(value).strip()


def _teacher_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}:{digest}"


def _aggregate_recall_context(rows: list[dict[str, Any]]) -> dict[str, Any]:
    representative = min(rows, key=lambda row: str(row.get("recall_number") or ""))
    return {
        "event_id": str(representative.get("event_id") or ""),
        "classifications": dict(
            Counter(str(row.get("classification")) for row in rows)
        ),
        "statuses": dict(Counter(str(row.get("status")) for row in rows)),
        "recalling_firm": representative.get("recalling_firm"),
        "recall_initiation_date": representative.get("recall_initiation_date"),
        "product_examples": [
            row.get("product_description")
            for row in sorted(
                rows, key=lambda row: str(row.get("recall_number") or "")
            )[:3]
        ],
        "filing_count": len(rows),
    }


def build_corpus_queue(
    recall_rows: list[dict[str, Any]],
    shortage_rows: list[dict[str, Any]],
    gold_candidates: list[GoldCandidate],
    *,
    snapshot: str,
) -> list[TeacherInputRecord]:
    """Build non-gold incident-deduplicated recall and shortage units."""
    gold_candidate_ids = {candidate.candidate_id for candidate in gold_candidates}
    gold_recall_pairs = set()
    for candidate in gold_candidates:
        if candidate.source != "recall":
            continue
        incident_id, _ = incident_identity(candidate)
        gold_recall_pairs.add((incident_id, reason_text_id(candidate.raw_text)))

    recall_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in recall_rows:
        raw_text = _raw_text("recall", row)
        if raw_text is None:
            continue
        event_value = str(row.get("event_id") or row.get("recall_number") or "missing")
        incident_id = f"recall_event:{event_value}"
        text_id = reason_text_id(raw_text)
        recall_groups[(incident_id, text_id)].append(row)

    queue = []
    for (incident_id, text_id), rows in sorted(recall_groups.items()):
        if (incident_id, text_id) in gold_recall_pairs:
            continue
        raw_text = _raw_text("recall", rows[0])
        if raw_text is None:
            raise AssertionError("Recall reason disappeared while grouping")
        source_ids = sorted(str(row.get("recall_number")) for row in rows)
        queue.append(
            TeacherInputRecord(
                teacher_id=_teacher_id("recall", incident_id, text_id),
                snapshot=snapshot,
                dataset_scope="corpus",
                source="recall",
                source_record_ids=source_ids,
                incident_id=incident_id,
                reason_text_id=text_id,
                raw_text=raw_text,
                source_raw_text=raw_text,
                source_context=_aggregate_recall_context(rows),
                requires_model_call=True,
            )
        )

    for row in shortage_rows:
        source_id = _source_id("shortage", row)
        candidate_id = f"shortage:{source_id}"
        if candidate_id in gold_candidate_ids:
            continue
        raw_source_text = _raw_text("shortage", row)
        raw_text = raw_source_text or MISSING_REASON_SENTINEL
        is_unknown = raw_source_text is None or raw_source_text.casefold() == "other"
        context = _context("shortage", row)
        incident_id, _ = shortage_incident_id(
            context,
            fallback_source_record_id=source_id,
        )
        queue.append(
            TeacherInputRecord(
                teacher_id=f"shortage:{source_id}",
                snapshot=snapshot,
                dataset_scope="corpus",
                source="shortage",
                source_record_ids=[source_id],
                incident_id=incident_id,
                reason_text_id=reason_text_id(raw_text),
                raw_text=raw_text,
                source_raw_text=raw_source_text,
                source_context=context,
                requires_model_call=not is_unknown,
            )
        )

    if len({row.teacher_id for row in queue}) != len(queue):
        raise ValueError("Teacher queue contains duplicate IDs")
    return sorted(queue, key=lambda row: row.teacher_id)


def build_gold_evaluation_queue(
    candidates: list[GoldCandidate],
) -> list[TeacherInputRecord]:
    """Create one blind evaluation unit per Phase 9 candidate."""
    queue = []
    for candidate in sorted(candidates, key=lambda row: row.sequence):
        incident_id, _ = incident_identity(candidate)
        source_raw_text = candidate.raw_text
        raw_text = source_raw_text or MISSING_REASON_SENTINEL
        policy_unknown = candidate.source == "shortage" and (
            source_raw_text is None or source_raw_text.casefold() == "other"
        )
        queue.append(
            TeacherInputRecord(
                teacher_id=f"gold:{candidate.candidate_id}",
                snapshot=candidate.snapshot,
                dataset_scope="gold_evaluation",
                source=candidate.source,
                source_record_ids=[candidate.source_record_id],
                incident_id=incident_id,
                reason_text_id=reason_text_id(raw_text),
                raw_text=raw_text,
                source_raw_text=source_raw_text,
                source_context=candidate.source_context,
                requires_model_call=not policy_unknown,
            )
        )
    return queue


def auto_unknown_label(unit: TeacherInputRecord) -> TeacherLabelRecord:
    if unit.requires_model_call:
        raise ValueError("auto_unknown_label received a model-call unit")
    reason_marker = (
        "fda_reason_missing" if unit.source_raw_text is None else "fda_reason_other"
    )
    availability = str(unit.source_context.get("availability") or "").casefold()
    severity = "high" if availability == "unavailable" else "medium"
    evidence = (
        MISSING_REASON_SENTINEL if unit.source_raw_text is None else unit.raw_text
    )
    event = DisruptionEvent(
        primary_cause="unknown",
        secondary_causes=[reason_marker],
        supply_chain_stage="unknown",
        severity=severity,
        evidence=[evidence],
    )
    return TeacherLabelRecord(
        teacher_id=unit.teacher_id,
        taxonomy_version=PRIMARY_CAUSE_TAXONOMY_VERSION,
        snapshot=unit.snapshot,
        dataset_scope=unit.dataset_scope,
        source=unit.source,
        source_record_ids=unit.source_record_ids,
        incident_id=unit.incident_id,
        reason_text_id=unit.reason_text_id,
        raw_text=unit.raw_text,
        source_raw_text=unit.source_raw_text,
        source_context=unit.source_context,
        event=event,
        label_method="policy_unknown",
        model=None,
        prompt_version=PROMPT_VERSION,
        labeled_at=datetime.now().astimezone().isoformat(),
    )


def build_few_shot_examples(
    candidates: list[GoldCandidate], labels: list[GoldLabelRecord]
) -> list[dict[str, Any]]:
    candidate_index = {candidate.candidate_id: candidate for candidate in candidates}
    label_index = {label.candidate_id: label for label in labels}
    examples = []
    for candidate_id in FEW_SHOT_CANDIDATE_IDS:
        candidate = candidate_index.get(candidate_id)
        label = label_index.get(candidate_id)
        if candidate is None or label is None:
            continue
        incident_id, _ = incident_identity(candidate)
        event = label.event.model_copy(update={"evidence": [candidate.raw_text]})
        examples.append(
            {
                "candidate_id": candidate_id,
                "incident_id": incident_id,
                "reason_text_id": reason_text_id(candidate.raw_text),
                "source": candidate.source,
                "raw_text": candidate.raw_text,
                "source_context": candidate.source_context,
                "event": event.model_dump(),
            }
        )
    return examples


def few_shots_for_target(
    few_shots: list[dict[str, Any]], unit: TeacherInputRecord
) -> list[dict[str, Any]]:
    """Prevent target-label leakage during blind gold evaluation."""
    if unit.dataset_scope != "gold_evaluation":
        return few_shots
    return [
        example
        for example in few_shots
        if example["incident_id"] != unit.incident_id
        and example["reason_text_id"] != unit.reason_text_id
    ]


def event_json_schema() -> dict[str, Any]:
    causes = list(get_args(PrimaryCause))
    return {
        "type": "object",
        "properties": {
            "primary_cause": {"type": "string", "enum": causes},
            "secondary_causes": {"type": "array", "items": {"type": "string"}},
            "supply_chain_stage": {"type": "string"},
            "severity": {"type": "string", "enum": ["low", "medium", "high"]},
            "evidence": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "primary_cause",
            "secondary_causes",
            "supply_chain_stage",
            "severity",
            "evidence",
        ],
        "additionalProperties": False,
    }


def system_prompt() -> str:
    definitions = "\n".join(
        f"- {category}: {definition}"
        for category, definition in CATEGORY_GUIDANCE.items()
    )
    stages = "\n".join(f"- {category}: {stage}" for category, stage in STAGES.items())
    return f"""You are the Phase 10 teacher annotator for FDA drug disruptions.

Use exactly this locked phase9_v1 taxonomy and these exact definitions:
{definitions}

Default supply-chain stages by primary cause:
{stages}

Decision policy:
1. Select the most specific supported causal category, not merely the first phrase.
2. Temperature-boundary rule: when "temperature abuse", "temperature excursion", improper storage temperature, or resulting heat/cold product damage is the direct FDA finding, use manufacturing_quality_problem as primary. This remains true when the text says it happened "during shipping", "during transit", or "during distribution"; those phrases identify location, not a shipping delay.
3. Use shipping_delay as primary only when the text explicitly names a delay or logistics interruption—such as "shipping delay", "transit delay", "delayed shipment", freight delay, or late delivery—as causing the temperature excursion. Then include manufacturing_quality_problem as a secondary cause. Mere occurrence during shipping/transit is not enough.
4. When one cause initiates another consequence outside that temperature boundary, use the initiating cause as primary.
5. For genuine independent dual causes, choose the cause most directly affecting product safety or therapeutic performance as primary and preserve the other category in secondary_causes.
6. Use adverse_event_signal only when harm is reported and no more specific root cause is established.
7. Use recall only for a stated recall reason that genuinely falls outside the causal taxonomy. Never use recall as a no-match fallback.
8. Use unknown only when FDA provides no causal information or only a placeholder.
9. For recall inputs whose primary cause is not recall, include recall_event in secondary_causes.
10. Severity follows the Phase 9 reviewer default: recall Class I=high, Class II=medium, Class III=low; shortage unavailable=high, limited availability=medium, resolved=low, otherwise medium.
11. Evidence must contain one or more non-empty, verbatim, case-sensitive substrings copied from RAW_TEXT. Never quote context fields, paraphrase, or invent evidence.
12. Return only the schema-constrained JSON object."""


def user_prompt(unit: TeacherInputRecord, few_shots: list[dict[str, Any]]) -> str:
    rendered_examples = []
    for index, example in enumerate(few_shots, 1):
        rendered_examples.append(
            f"EXAMPLE {index}\n"
            f"SOURCE: {example['source']}\n"
            f"CONTEXT: {json.dumps(example['source_context'], ensure_ascii=False, sort_keys=True)}\n"
            f"RAW_TEXT: {example['raw_text']}\n"
            f"OUTPUT: {json.dumps(example['event'], ensure_ascii=False, sort_keys=True)}"
        )
    examples_block = "\n\n".join(rendered_examples) or "[No non-leaking examples]"
    return f"""Use the examples for boundary-handling style. They are not the target.

{examples_block}

TARGET
SOURCE: {unit.source}
CONTEXT: {json.dumps(unit.source_context, ensure_ascii=False, sort_keys=True)}
RAW_TEXT: {unit.raw_text}

Classify TARGET only. Every evidence item must be copied exactly from TARGET RAW_TEXT."""


def validate_teacher_output(output_text: str, raw_text: str) -> DisruptionEvent:
    try:
        payload = json.loads(output_text)
    except json.JSONDecodeError as error:
        raise TeacherValidationError(f"invalid JSON: {error.msg}") from error
    try:
        event = DisruptionEvent.model_validate(payload)
    except Exception as error:
        raise TeacherValidationError(f"schema validation failed: {error}") from error
    for evidence in event.evidence:
        if evidence not in raw_text:
            raise TeacherValidationError(
                f"evidence is not an exact raw_text substring: {evidence!r}"
            )
    return event


def usage_cost(usage: dict[str, Any], model: str) -> TeacherUsage:
    pricing = MODEL_PRICING_USD_PER_MTOK.get(model)
    if pricing is None:
        raise ValueError(f"No pinned pricing configured for model {model!r}")
    values = {
        "input_tokens": int(usage.get("input_tokens", 0)),
        "output_tokens": int(usage.get("output_tokens", 0)),
        "cache_creation_input_tokens": int(usage.get("cache_creation_input_tokens", 0)),
        "cache_read_input_tokens": int(usage.get("cache_read_input_tokens", 0)),
    }
    cost = (
        values["input_tokens"] * pricing["input"]
        + values["output_tokens"] * pricing["output"]
        + values["cache_creation_input_tokens"] * pricing["cache_creation"]
        + values["cache_read_input_tokens"] * pricing["cache_read"]
    ) / 1_000_000
    return TeacherUsage(**values, estimated_cost_usd=cost)


class AnthropicMessagesClient:
    """Minimal dependency-free Claude Messages API client."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        timeout_seconds: int = 120,
        max_retries: int = 4,
    ) -> None:
        if not api_key:
            raise ValueError("Anthropic API key is empty")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    def label(
        self, unit: TeacherInputRecord, few_shots: list[dict[str, Any]]
    ) -> tuple[str, str | None, TeacherUsage]:
        body = {
            "model": self.model,
            "max_tokens": 1200,
            "system": [
                {
                    "type": "text",
                    "text": system_prompt(),
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": user_prompt(unit, few_shots)}],
            "output_config": {
                "effort": "medium",
                "format": {"type": "json_schema", "schema": event_json_schema()},
            },
        }
        request = urllib.request.Request(
            ANTHROPIC_MESSAGES_URL,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": ANTHROPIC_API_VERSION,
            },
            method="POST",
        )
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout_seconds
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if payload.get("stop_reason") == "refusal":
                    raise TeacherValidationError("Claude refused the classification")
                text_blocks = [
                    block.get("text", "")
                    for block in payload.get("content", [])
                    if block.get("type") == "text"
                ]
                if not text_blocks:
                    raise TeacherValidationError(
                        "Claude returned no text content block"
                    )
                usage = usage_cost(payload.get("usage", {}), self.model)
                return "".join(text_blocks), payload.get("id"), usage
            except urllib.error.HTTPError as error:
                retryable = error.code == 429 or 500 <= error.code < 600
                details = error.read().decode("utf-8", errors="replace")[:1000]
                if not retryable or attempt >= self.max_retries:
                    raise RuntimeError(
                        f"Anthropic API HTTP {error.code}: {details}"
                    ) from error
            except urllib.error.URLError as error:
                if attempt >= self.max_retries:
                    raise RuntimeError(
                        f"Anthropic API network error: {error}"
                    ) from error
            time.sleep(min(2**attempt, 30))
        raise AssertionError("retry loop exited unexpectedly")


def label_with_claude(
    client: AnthropicMessagesClient,
    unit: TeacherInputRecord,
    few_shots: list[dict[str, Any]],
) -> TeacherLabelRecord:
    output_text, request_id, usage = client.label(
        unit, few_shots_for_target(few_shots, unit)
    )
    event = validate_teacher_output(output_text, unit.raw_text)
    return teacher_record_from_event(
        unit,
        event,
        model=client.model,
        request_id=request_id,
        usage=usage,
    )


def teacher_record_from_event(
    unit: TeacherInputRecord,
    event: DisruptionEvent,
    *,
    model: str,
    request_id: str | None,
    usage: TeacherUsage,
) -> TeacherLabelRecord:
    return TeacherLabelRecord(
        teacher_id=unit.teacher_id,
        taxonomy_version=PRIMARY_CAUSE_TAXONOMY_VERSION,
        snapshot=unit.snapshot,
        dataset_scope=unit.dataset_scope,
        source=unit.source,
        source_record_ids=unit.source_record_ids,
        incident_id=unit.incident_id,
        reason_text_id=unit.reason_text_id,
        raw_text=unit.raw_text,
        source_raw_text=unit.source_raw_text,
        source_context=unit.source_context,
        event=event,
        label_method="claude",
        model=model,
        prompt_version=PROMPT_VERSION,
        labeled_at=datetime.now().astimezone().isoformat(),
        request_id=request_id,
        usage=usage,
    )


def select_pilot_units(
    queue: list[TeacherInputRecord],
    *,
    size: int = DEFAULT_PILOT_SIZE,
    seed: int = 202610,
) -> list[TeacherInputRecord]:
    """Choose a deterministic, source-aware pilot with extra boundary vocabulary."""
    eligible = [row for row in queue if row.requires_model_call]
    if size < 1 or size > len(eligible):
        raise ValueError("pilot size must be between 1 and the model-call queue size")
    edge_pattern = re.compile(
        r"temperature|delivery system|subpotent|imprint|excipient|shipping|transit|mislab",
        re.IGNORECASE,
    )
    rng = random.Random(seed)
    recalls = [row for row in eligible if row.source == "recall"]
    shortages = [row for row in eligible if row.source == "shortage"]
    rng.shuffle(recalls)
    rng.shuffle(shortages)
    edge = [row for row in recalls if edge_pattern.search(row.raw_text)]
    regular = [row for row in recalls if row not in edge]
    shortage_target = min(len(shortages), max(1, round(size * 0.20)))
    edge_target = min(len(edge), max(1, round(size * 0.30)))
    chosen = shortages[:shortage_target] + edge[:edge_target]
    selected_ids = {row.teacher_id for row in chosen}
    remainder = [
        row
        for row in regular + edge[edge_target:] + shortages[shortage_target:]
        if row.teacher_id not in selected_ids
    ]
    chosen.extend(remainder[: size - len(chosen)])
    return chosen


def aggregate_usage(records: list[TeacherLabelRecord]) -> dict[str, Any]:
    usage_records = [record.usage for record in records if record.usage is not None]
    return {
        "model_calls": len(usage_records),
        "input_tokens": sum(row.input_tokens for row in usage_records),
        "output_tokens": sum(row.output_tokens for row in usage_records),
        "cache_creation_input_tokens": sum(
            row.cache_creation_input_tokens for row in usage_records
        ),
        "cache_read_input_tokens": sum(
            row.cache_read_input_tokens for row in usage_records
        ),
        "estimated_cost_usd": sum(row.estimated_cost_usd for row in usage_records),
    }


def pilot_cost_estimate(
    pilot_labels: list[TeacherLabelRecord],
    *,
    pilot_failures: list[TeacherFailureRecord],
    corpus_model_calls: int,
    gold_model_calls: int,
) -> dict[str, Any]:
    usage = aggregate_usage(pilot_labels)
    failure_usage = [failure.usage for failure in pilot_failures if failure.usage]
    attempted = usage["model_calls"] + len(failure_usage)
    pass_rate = usage["model_calls"] / attempted if attempted else 0.0
    failed_cost = sum(row.estimated_cost_usd for row in failure_usage)
    all_attempt_cost = usage["estimated_cost_usd"] + failed_cost
    average_cost = all_attempt_cost / attempted if attempted else 0.0
    expected_attempt_multiplier = 1.0 / pass_rate if pass_rate else 1.0
    total_calls = corpus_model_calls + gold_model_calls
    projected_total = average_cost * total_calls * expected_attempt_multiplier
    remaining_calls = max(0, total_calls - usage["model_calls"])
    projected_remaining = average_cost * remaining_calls * expected_attempt_multiplier
    return {
        "pilot_attempted": attempted,
        "pilot_passed": usage["model_calls"],
        "pilot_failed_validation": len(failure_usage),
        "validation_pass_rate": pass_rate,
        "usage": {
            **usage,
            "failed_call_cost_usd": failed_cost,
            "all_attempt_cost_usd": all_attempt_cost,
        },
        "average_attempt_cost_usd": average_cost,
        "corpus_model_calls": corpus_model_calls,
        "gold_evaluation_model_calls": gold_model_calls,
        "projected_total_model_calls": total_calls,
        "projected_total_cost_usd": projected_total,
        "projected_additional_cost_after_pilot_usd": projected_remaining,
    }


def gold_evaluation_metrics(
    candidates: list[GoldCandidate],
    human_labels: list[GoldLabelRecord],
    predictions: list[TeacherLabelRecord],
) -> dict[str, Any]:
    candidate_index = {candidate.candidate_id: candidate for candidate in candidates}
    human_index = {label.candidate_id: label for label in human_labels}
    prediction_index = {
        prediction.teacher_id.removeprefix("gold:"): prediction
        for prediction in predictions
        if prediction.dataset_scope == "gold_evaluation"
    }
    compared = sorted(set(human_index) & set(prediction_index))
    baseline_agreed = sum(
        human_index[key].baseline_primary_cause == human_index[key].event.primary_cause
        for key in compared
    )
    teacher_agreed = sum(
        prediction_index[key].event.primary_cause
        == human_index[key].event.primary_cause
        for key in compared
    )
    confusions = Counter(
        (
            human_index[key].event.primary_cause,
            prediction_index[key].event.primary_cause,
        )
        for key in compared
        if prediction_index[key].event.primary_cause
        != human_index[key].event.primary_cause
    )
    disagreement_examples = [
        {
            "candidate_id": key,
            "human_category": human_index[key].event.primary_cause,
            "teacher_category": prediction_index[key].event.primary_cause,
            "raw_text": candidate_index[key].raw_text,
        }
        for key in compared
        if prediction_index[key].event.primary_cause
        != human_index[key].event.primary_cause
    ]
    by_category: dict[str, Counter[str]] = defaultdict(Counter)
    for key in compared:
        human_category = human_index[key].event.primary_cause
        by_category[human_category]["total"] += 1
        by_category[human_category]["teacher_agreed"] += (
            prediction_index[key].event.primary_cause == human_category
        )
        by_category[human_category]["baseline_agreed"] += (
            human_index[key].baseline_primary_cause == human_category
        )

    def subset_metrics(keys: list[str]) -> dict[str, Any]:
        available = [
            key for key in keys if key in prediction_index and key in human_index
        ]
        agreed = sum(
            prediction_index[key].event.primary_cause
            == human_index[key].event.primary_cause
            for key in available
        )
        return {
            "records": len(available),
            "agreed": agreed,
            "percentage": 100 * agreed / len(available) if available else None,
        }

    fallback_keys = [
        key
        for key, candidate in candidate_index.items()
        if candidate.baseline.fallback and candidate.baseline.confidence_score == 0.0
    ]
    collision_keys = [
        key
        for key, candidate in candidate_index.items()
        if candidate.baseline.collision
    ]
    edge_families = {}
    for family, pattern in EDGE_FAMILY_PATTERNS.items():
        keys = [
            key
            for key, candidate in candidate_index.items()
            if pattern.search(candidate.raw_text or "")
        ]
        edge_families[family] = subset_metrics(keys)

    return {
        "gold_records": len(human_labels),
        "teacher_predictions": len(prediction_index),
        "compared": len(compared),
        "teacher_agreement": {
            "agreed": teacher_agreed,
            "percentage": 100 * teacher_agreed / len(compared) if compared else None,
        },
        "baseline_agreement": {
            "agreed": baseline_agreed,
            "percentage": 100 * baseline_agreed / len(compared) if compared else None,
        },
        "by_human_category": {
            category: {
                "records": counts["total"],
                "teacher_agreed": counts["teacher_agreed"],
                "teacher_percentage": 100 * counts["teacher_agreed"] / counts["total"],
                "baseline_agreed": counts["baseline_agreed"],
                "baseline_percentage": 100
                * counts["baseline_agreed"]
                / counts["total"],
            }
            for category, counts in sorted(by_category.items())
        },
        "confusion_pairs": [
            {"human_category": pair[0], "teacher_category": pair[1], "records": count}
            for pair, count in confusions.most_common()
        ],
        "disagreements": disagreement_examples,
        "zero_match_fallback": subset_metrics(fallback_keys),
        "collision_boundary": subset_metrics(collision_keys),
        "edge_families": edge_families,
        "manufacturing_capacity_predictions": sum(
            prediction.event.primary_cause == "manufacturing_capacity"
            for prediction in prediction_index.values()
        ),
    }


def render_teacher_report(metrics: dict[str, Any]) -> str:
    queue = metrics["queue"]
    evaluation = metrics["gold_evaluation"]
    evaluation_run = metrics["gold_evaluation_run"]
    remaining_corpus = metrics["remaining_corpus"]
    current_corpus_run = metrics["current_corpus_run"]
    phase_cost = metrics["phase_cost"]
    phase_budget = metrics.get("phase_budget")
    phase_finalization = metrics.get("phase_finalization")
    shortage_completion = metrics.get("shortage_completion")
    pilot = metrics.get("pilot")
    validation = metrics["validation"]
    budget_status_lines = []
    if phase_budget:
        usable_remaining = max(
            0.0,
            phase_budget["effective_measured_ceiling_usd"]
            - phase_cost["measured_total_usd"],
        )
        budget_status_lines = [
            f"Configured total-phase ceiling: **${phase_budget['total_phase_ceiling_usd']:.2f}**; safety reserve: **${phase_budget['safety_margin_usd']:.2f}**; measurable stop point: **${phase_budget['effective_measured_ceiling_usd']:.2f}**; usable measured headroom: **${usable_remaining:.4f}**.",
            "",
        ]
    lines = [
        "# Phase 10 Teacher Labeling Report",
        "",
        f"Generated: {metrics['generated_at']}",
        "",
        f"Snapshot: `{metrics['snapshot']}`",
        "",
        f"Teacher model: `{metrics['model']}`",
        "",
        f"Current corpus prompt version: `{metrics['prompt_version']}`",
        "",
        "## Status",
        "",
        f"Anthropic API credential detected: **{'yes' if metrics['api_key_available'] else 'no'}**.",
        "",
        f"Corpus queue: **{queue['total']:,}** incident-aware units; **{queue['model_calls']:,}** require Claude and **{queue['policy_unknown']:,}** are deterministic unknown-policy labels.",
        "",
        f"Teacher corpus labels written: **{metrics['corpus_labels']:,}/{queue['total']:,}**.",
        "",
        f"Final Phase 10 corpus composition: **{metrics['corpus_label_methods'].get('claude', 0):,} model-labeled** and **{metrics['corpus_label_methods'].get('policy_unknown', 0):,} deterministic unknown-policy** records, plus the separate **400-record human gold** evaluation set.",
        "",
        f"Refined-prompt corpus outputs: **{current_corpus_run['validated_model_outputs']:,}** at **${current_corpus_run['estimated_cost_usd']:.4f}** measured API cost; **{remaining_corpus['model_calls']:,}** model calls remain.",
        "",
        f"Total measured Phase 10 spend: **${phase_cost['measured_total_usd']:.4f}** (pilot + gold evaluation + corpus + paid rejected outputs).",
        "",
        (
            "Shortage-only completion is finished: every non-gold shortage with a real FDA reason now has a validated teacher label. Recall completion remains intentionally out of scope."
            if phase_finalization
            and phase_finalization.get("status")
            == "shortages_complete_recalls_partial"
            else (
                "Shortage-only completion is blocked and remains incomplete. No recall calls were attempted. "
                + str(shortage_completion.get("fatal_error") or "See the shortage completion audit for the failure.")
            )
            if phase_finalization
            and phase_finalization.get("status") == "shortage_completion_incomplete"
            else
            "Phase 10 is finalized as an intentionally partial corpus because of the approved cost limit. "
            "No additional teacher calls are planned; the remaining queue can be resumed later if needed."
            if phase_finalization
            and phase_finalization.get("status") == "finalized_partial"
            else "The full run is cost-gated. It requires a completed pilot plus explicit approval and a persisted total-phase ceiling."
        ),
        "",
        *budget_status_lines,
        *(
            [
                f"Shortage-only extension: **{shortage_completion['validated_this_run']:,}** new validated labels at **${shortage_completion['measured_incremental_cost_usd']:.4f}** measured incremental cost; **{shortage_completion['remaining_shortage_calls']:,}** real-reason shortage calls remain.",
                "",
            ]
            if shortage_completion
            else []
        ),
        "## Queue construction",
        "",
        f"- Recall queue: **{queue['by_source']['recall']:,}** incident/text units representing **{queue['recall_filings_represented']:,}** raw filings after gold-pair exclusion.",
        f"- Shortage queue: **{queue['by_source']['shortage']:,}** records; **{queue['model_calls_by_source']['shortage']:,}** require Claude and **{queue['policy_unknown']:,}** use the unknown policy.",
        "- Recalls are one unit per unique `(FDA event_id, normalized reason_text_id)` pair, excluding every pair represented in Phase 9 gold.",
        "- Shortage records represented in Phase 9 gold are excluded by their deterministic source record ID.",
        "- Populated non-`Other` shortage reasons require Claude. Missing/`Other` reasons receive `unknown` without a model call.",
        "- Teacher annotations are stored separately and always carry `annotator: teacher`; human gold files are immutable.",
        "",
        "## Prompt and hallucination controls",
        "",
        "The prompt contains the exact 13 category definitions used by the Phase 9 reviewer plus gold-derived boundary examples. During blind gold evaluation, any few-shot example sharing the target incident or normalized exact text is removed.",
        "",
        "Temperature boundary refinement: a temperature excursion or temperature abuse stated as the direct finding remains `manufacturing_quality_problem`, including when it occurred during transit. `shipping_delay` now requires an explicitly named delay or logistics interruption that caused the excursion.",
        "",
        "Claude is constrained to the `DisruptionEvent` JSON schema. The local validator then independently parses JSON, validates the Pydantic schema, and requires every evidence item to be a non-empty, case-sensitive substring of target `raw_text`.",
        "",
        f"Validated model outputs: **{validation['passed']:,}/{validation['responses']:,} ({validation['pass_percentage']:.2f}%)**.",
        "",
        f"Rejected hallucination/schema outputs: **{validation['failed']:,}**; API/network failures without a response: **{validation['api_errors']:,}**.",
        "",
        "## Pilot and cost gate",
        "",
    ]
    if pilot:
        usage = pilot["usage"]
        lines.extend(
            [
                f"Pilot validation: **{pilot['pilot_passed']:,}/{pilot['pilot_attempted']:,} ({100 * pilot['validation_pass_rate']:.2f}%)**.",
                "",
                f"Pilot tokens: input {usage['input_tokens']:,}, output {usage['output_tokens']:,}, cache-write {usage['cache_creation_input_tokens']:,}, cache-read {usage['cache_read_input_tokens']:,}.",
                "",
                f"Pilot API cost: **${usage['all_attempt_cost_usd']:.4f}**.",
                "",
                f"Projected complete corpus + 400-record gold evaluation cost: **${pilot['projected_total_cost_usd']:.2f}**; projected additional cost after pilot: **${pilot['projected_additional_cost_after_pilot_usd']:.2f}**.",
                "",
                f"With the pilot and gold evaluation now saved, the remaining non-gold corpus is **{remaining_corpus['model_calls']:,}** model calls, projected at **${remaining_corpus['projected_cost_usd']:.2f}** from the pilot average.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "**Pilot not run.** Actual token usage and total cost cannot be estimated until `ANTHROPIC_API_KEY` is available and at least 50 model responses complete.",
                "",
            ]
        )
    lines.extend(
        [
            "Pricing is pinned to Claude Sonnet 5 at $2/input MTok, $10/output MTok, $2.50/cache-write MTok, and $0.20/cache-read MTok. Confirm current pricing before a delayed full run: https://platform.claude.com/docs/en/about-claude/pricing",
            "",
            "## Gold evaluation: teacher vs rule baseline",
            "",
            f"Human gold records: **{evaluation['gold_records']:,}**; blind teacher predictions available: **{evaluation['teacher_predictions']:,}**; compared: **{evaluation['compared']:,}**.",
            "",
            f"Gold evaluation prompt provenance: **{', '.join(f'`{version}` ({count:,})' for version, count in sorted(metrics['gold_prompt_versions'].items())) or 'none'}**. The temperature-boundary refinement was made after this benchmark and the gold predictions were not overwritten.",
            "",
            f"Gold model validation: **{evaluation_run['validated_model_outputs']:,}/{evaluation_run['model_required']:,}** outputs passed; **{evaluation_run['validation_failures']:,}** validation failures. The audit log contains **{evaluation_run['api_errors']:,}** transient network errors (**{evaluation_run['local_sandbox_dns_errors']:,}** from the initial local sandbox and **{evaluation_run['unresolved_api_errors']:,}** unresolved); all requested predictions are now present. Measured validated-response cost: **${evaluation_run['estimated_cost_usd']:.4f}**.",
            "",
        ]
    )
    if evaluation["compared"]:
        teacher = evaluation["teacher_agreement"]
        baseline = evaluation["baseline_agreement"]
        lines.extend(
            [
                f"Teacher agreement: **{teacher['agreed']:,}/{evaluation['compared']:,} ({teacher['percentage']:.2f}%)**.",
                "",
                f"Frozen Phase 8 baseline agreement on the same compared records: **{baseline['agreed']:,}/{evaluation['compared']:,} ({baseline['percentage']:.2f}%)**.",
                "",
                "| Human category | Records | Teacher agreement | Baseline agreement |",
                "|---|---:|---:|---:|",
            ]
        )
        for category, values in evaluation["by_human_category"].items():
            lines.append(
                f"| `{category}` | {values['records']:,} | {values['teacher_agreed']:,}/{values['records']:,} ({values['teacher_percentage']:.2f}%) | {values['baseline_agreed']:,}/{values['records']:,} ({values['baseline_percentage']:.2f}%) |"
            )
        lines.extend(
            [
                "",
                "### Teacher disagreement patterns",
                "",
                "| Human category | Teacher category | Records |",
                "|---|---|---:|",
            ]
        )
        for row in evaluation["confusion_pairs"]:
            lines.append(
                f"| `{row['human_category']}` | `{row['teacher_category']}` | {row['records']:,} |"
            )
        lines.extend(
            [
                "",
                "### Known difficult families",
                "",
                "| Family | Records | Teacher agreement |",
                "|---|---:|---:|",
            ]
        )
        difficult = {
            "zero_match_fallback": evaluation["zero_match_fallback"],
            "collision_boundary": evaluation["collision_boundary"],
            **evaluation["edge_families"],
        }
        for family, values in difficult.items():
            percentage = (
                f"{values['percentage']:.2f}%"
                if values["percentage"] is not None
                else "n/a"
            )
            lines.append(
                f"| `{family}` | {values['records']:,} | {values['agreed']:,}/{values['records']:,} ({percentage}) |"
            )
    else:
        lines.extend(
            [
                "Teacher accuracy is pending the cost-approved full gold evaluation. The frozen rule baseline's completed-gold agreement is reported below for reference only.",
                "",
            ]
        )
    baseline_full = metrics["baseline_full_gold"]
    lines.extend(
        [
            f"Frozen Phase 8 baseline on all 400 gold records: **{baseline_full['agreed']:,}/400 ({baseline_full['percentage']:.2f}%)**.",
            "",
            f"Teacher `manufacturing_capacity` predictions so far: **{metrics['manufacturing_capacity_predictions']:,}**. Human gold contains **{metrics['human_manufacturing_capacity']:,}** such labels; zero confirms that Phase 9 found no supported examples.",
            "",
            "## Reproduction",
            "",
            "```bash",
            "# Safe, no API calls",
            "/opt/anaconda3/envs/medisupply/bin/python scripts/run_teacher_labeling.py --mode prepare",
            "",
            "# Requires ANTHROPIC_API_KEY; exactly 50 pilot model responses",
            "/opt/anaconda3/envs/medisupply/bin/python scripts/run_teacher_labeling.py --mode pilot --pilot-size 50",
            "",
            "# Blind gold evaluation only; does not label the non-gold corpus",
            "/opt/anaconda3/envs/medisupply/bin/python scripts/run_teacher_labeling.py --mode gold-eval --max-cost-usd 4",
            "",
            "# Total-phase ceiling includes pilot, gold evaluation, corpus, and paid rejects",
            "/opt/anaconda3/envs/medisupply/bin/python scripts/run_teacher_labeling.py --mode full --approve-full-run --total-phase-ceiling-usd 19 --phase-cost-safety-margin-usd 0.25",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def append_jsonl(path: Path, record: BaseModel | dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(record, BaseModel):
        payload = record.model_dump_json()
    else:
        payload = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(descriptor, (payload + "\n").encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_queue(path: Path, records: list[BaseModel]) -> None:
    atomic_write_jsonl(path, records)
