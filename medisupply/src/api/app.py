"""FastAPI application presenting Phase 13 intelligence outputs."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections import Counter
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.intelligence.engine import IntelligenceEngine

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT = REPOSITORY_ROOT / "data" / "dashboard" / "current.json"
STATIC_DIR = Path(__file__).resolve().parent / "static"
RISK_ORDER = {"high": 0, "elevated": 1, "moderate": 2, "low": 3}
DISCLAIMER = (
    "Supply-chain research signal only. Requires human review. "
    "Not medical or clinical advice."
)


def _group_key(record: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(record.get("generic_name") or "").casefold(),
        str(record.get("manufacturer") or "").casefold(),
        str(record.get("initial_posting_date") or ""),
    )


class IntelligenceStore:
    """Load, group, search, and expand the immutable Phase 13 artifact."""

    def __init__(self, report_path: str | Path = DEFAULT_REPORT) -> None:
        self.report_path = Path(report_path)
        if not self.report_path.exists():
            raise FileNotFoundError(
                f"Phase 13 output is missing: {self.report_path}. "
                "Run scripts/run_intelligence_engine.py first."
            )
        self.payload = json.loads(self.report_path.read_text())
        database_override = os.environ.get("MEDISUPPLY_KNOWLEDGE_GRAPH")
        database_value = database_override or self.payload.get("database")
        self.database = Path(str(database_value)) if database_override else (
            REPOSITORY_ROOT / str(database_value) if database_value else None
        )
        if self.database is None or not self.database.is_file():
            raise FileNotFoundError(
                f"Dashboard knowledge graph is missing: {self.database}"
            )
        database_uri = f"file:{self.database.resolve()}?mode=ro"
        with sqlite3.connect(database_uri, uri=True) as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key = 'snapshot'"
            ).fetchone()
        database_snapshot = str(row[0]) if row else None
        if database_snapshot != self.payload.get("snapshot"):
            raise ValueError(
                "Dashboard artifact and knowledge graph snapshots do not match: "
                f"{self.payload.get('snapshot')} != {database_snapshot}"
            )
        records = self.payload.get("all_scored_current_shortages")
        if not isinstance(records, list):
            raise TypeError("Phase 13 output has no scored shortage list")
        self.records = records
        self.groups = self._group_records(records)
        self._add_drug_relationship_counts(self.groups)
        self.manufacturers = sorted(
            {
                str(item["manufacturer"])
                for item in self.groups
                if item.get("manufacturer")
            },
            key=str.casefold,
        )
        self._detail_cache: dict[int, dict[str, Any]] = {}
        self.group_by_shortage_id: dict[int, dict[str, Any]] = {}
        for group in self.groups:
            for shortage_id in group["shortage_ids"]:
                self.group_by_shortage_id[int(shortage_id)] = group

    @staticmethod
    def _group_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: dict[tuple[str, str, str], dict[str, Any]] = {}
        for record in sorted(
            records,
            key=lambda item: (
                -int(item.get("score", 0)),
                int(item.get("shortage_id", 0)),
            ),
        ):
            key = _group_key(record)
            if key not in groups:
                representative = dict(record)
                representative.update(
                    {
                        "representative_shortage_id": int(record["shortage_id"]),
                        "shortage_ids": [],
                        "package_ndcs": [],
                        "package_count": 0,
                        "evaluation_reserved_count": 0,
                        "fda_no_reason_count": 0,
                    }
                )
                groups[key] = representative
            group = groups[key]
            group["shortage_ids"].append(int(record["shortage_id"]))
            package_ndc = record.get("package_ndc")
            if package_ndc and package_ndc not in group["package_ndcs"]:
                group["package_ndcs"].append(package_ndc)
            group["package_count"] += 1
            group["evaluation_reserved_count"] += int(
                bool(record.get("reserved_for_evaluation"))
            )
            group["fda_no_reason_count"] += int(
                record.get("unknown_reason") == "fda_reason_not_provided"
            )

        results = []
        for group in groups.values():
            # This application deliberately treats every computed score as a
            # research signal requiring review, even when its data links are strong.
            group["requires_human_review"] = True
            group["detail_url"] = f"/drug/{group['representative_shortage_id']}"
            results.append(group)
        return sorted(
            results,
            key=lambda item: (
                RISK_ORDER.get(str(item.get("risk_tier")), 99),
                -int(item.get("score", 0)),
                str(item.get("generic_name") or "").casefold(),
            ),
        )

    @staticmethod
    def _add_drug_relationship_counts(groups: list[dict[str, Any]]) -> None:
        by_drug: dict[str, list[dict[str, Any]]] = {}
        for group in groups:
            name = str(group.get("generic_name") or "").casefold()
            by_drug.setdefault(name, []).append(group)
        for related in by_drug.values():
            manufacturers = {
                str(item.get("manufacturer"))
                for item in related
                if item.get("manufacturer")
            }
            package_count = sum(int(item["package_count"]) for item in related)
            for item in related:
                item["related_manufacturer_count"] = len(manufacturers)
                item["related_signal_count"] = len(related)
                item["related_package_count"] = package_count

    def search(
        self,
        query: str = "",
        tier: str | None = None,
        manufacturer: str | None = None,
    ) -> list[dict[str, Any]]:
        needle = query.strip().casefold()
        results = self.groups
        if tier:
            results = [item for item in results if item.get("risk_tier") == tier]
        if manufacturer:
            selected = manufacturer.strip().casefold()
            results = [
                item
                for item in results
                if str(item.get("manufacturer") or "").casefold() == selected
            ]
        if needle:
            results = [
                item
                for item in results
                if needle in str(item.get("generic_name") or "").casefold()
                or needle in str(item.get("manufacturer") or "").casefold()
            ]
        return results

    def detail(self, shortage_id: int) -> dict[str, Any]:
        group = self.group_by_shortage_id.get(shortage_id)
        if group is None:
            raise KeyError(shortage_id)
        representative_id = int(group["representative_shortage_id"])
        if representative_id in self._detail_cache:
            return self._detail_cache[representative_id]
        with IntelligenceEngine(self.database) as engine:
            score = engine.supply_fragility_score(representative_id)
        cause_component = score["components"]["manufacturing_root_cause"]
        precomputed_cause = str(group.get("primary_cause") or "unknown")
        precomputed_unknown_reason = group.get("unknown_reason")
        precomputed_available = bool(group.get("teacher_label_available"))
        precomputed_reserved = bool(group.get("reserved_for_evaluation"))
        cause_component.update(
            {
                "points": int(
                    (group.get("component_points") or {}).get(
                        "manufacturing_root_cause", cause_component["points"]
                    )
                ),
                "observed": precomputed_cause,
                "teacher_label_available": precomputed_available,
                "label_method": group.get("label_method")
                or "precomputed_dashboard_artifact",
                "reserved_for_evaluation": precomputed_reserved,
                "unknown_reason": precomputed_unknown_reason,
            }
        )
        score["root_cause"] = {
            "primary_cause": precomputed_cause,
            "label_method": cause_component["label_method"],
            "teacher_id": None,
            "source_record_id": None,
            "available": precomputed_available,
            "reserved_for_evaluation": precomputed_reserved,
            "unknown_reason": precomputed_unknown_reason,
        }
        score["score"] = int(group["score"])
        score["risk_tier"] = str(group["risk_tier"])
        warnings = []
        root_cause = score["root_cause"]
        recall_confidence = score["recall_overlap"]["linkage_confidence"]
        unknown_reason = root_cause["unknown_reason"]
        if unknown_reason == "reserved_for_evaluation":
            warnings.append(
                "Root cause is unknown because this record is intentionally reserved for held-out human evaluation; its human label is not used in scoring."
            )
        elif unknown_reason == "fda_reason_not_provided":
            warnings.append(
                "Root cause is unknown because FDA provided no usable shortage reason (the field was missing or FDA supplied its 'Other' placeholder); Phase 9 policy does not infer a cause."
            )
        elif unknown_reason == "needs_teacher_labeling":
            warnings.append(
                "FDA supplied cause text, but this new record has not been reviewed by the optional teacher-labeling phase; it remains unknown without making a paid API call."
            )
        elif root_cause["primary_cause"] == "unknown":
            warnings.append("Root cause remains unclassified in the available inputs.")
        if (
            not root_cause["available"]
            and not root_cause["reserved_for_evaluation"]
            and unknown_reason != "needs_teacher_labeling"
        ):
            warnings.append(
                "No Phase 10 teacher label is available for this shortage; the score uses the documented unknown-cause prior."
            )
        if group["evaluation_reserved_count"] and not root_cause["reserved_for_evaluation"]:
            count = int(group["evaluation_reserved_count"])
            warnings.append(
                f"{count} grouped package record{'s are' if count != 1 else ' is'} "
                "reserved for held-out human evaluation; those human labels are not used in this representative score."
            )
        if (
            group["fda_no_reason_count"]
            and unknown_reason != "fda_reason_not_provided"
        ):
            count = int(group["fda_no_reason_count"])
            warnings.append(
                f"FDA provided no usable shortage reason for {count} grouped package "
                f"record{'s' if count != 1 else ''}; no cause was inferred for those records."
            )
        if recall_confidence["level"] == "limited":
            warnings.append(
                "Recall overlap is limited-confidence because most historical recall records lack product identifiers."
            )
        detail = {
            "group": group,
            "score": score,
            "warnings": warnings,
            "requires_human_review": True,
            "disclaimer": DISCLAIMER,
        }
        if len(self._detail_cache) >= 256:
            self._detail_cache.pop(next(iter(self._detail_cache)))
        self._detail_cache[representative_id] = detail
        return detail

    def metadata(self) -> dict[str, Any]:
        tier_counts = Counter(str(item.get("risk_tier")) for item in self.groups)
        review_count = sum(bool(item["requires_human_review"]) for item in self.groups)
        return {
            "generated_at": self.payload.get("generated_at"),
            "snapshot": self.payload.get("snapshot"),
            "as_of": self.payload.get("as_of"),
            "group_count": len(self.groups),
            "package_record_count": len(self.records),
            "tier_counts": dict(tier_counts),
            "manufacturers": self.manufacturers,
            "requires_human_review_count": review_count,
            "evaluation_reserved_package_records": sum(
                int(item.get("evaluation_reserved_count", 0)) for item in self.groups
            ),
            "fda_no_reason_package_records": sum(
                int(item.get("fda_no_reason_count", 0)) for item in self.groups
            ),
            "coverage": self.payload.get("coverage"),
            "recall_linkage": self.payload.get("recall_linkage"),
            "disclaimer": DISCLAIMER,
        }


class ReloadingIntelligenceStore:
    """Adopt a newly promoted dashboard artifact on the next request."""

    def __init__(self, report_path: str | Path) -> None:
        self.report_path = Path(report_path)
        self._lock = threading.Lock()
        self._signature: tuple[int, int] | None = None
        self._store: IntelligenceStore | None = None

    def current(self) -> IntelligenceStore:
        stat = self.report_path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        if self._store is not None and signature == self._signature:
            return self._store
        with self._lock:
            stat = self.report_path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            if self._store is None or signature != self._signature:
                candidate = IntelligenceStore(self.report_path)
                self._store = candidate
                self._signature = signature
        if self._store is None:  # pragma: no cover - defensive type narrowing
            raise RuntimeError("Dashboard intelligence store failed to initialize")
        return self._store


def create_app(report_path: str | Path | None = None) -> FastAPI:
    selected_report = report_path or os.environ.get(
        "MEDISUPPLY_INTELLIGENCE_REPORT", str(DEFAULT_REPORT)
    )
    stores = ReloadingIntelligenceStore(selected_report)
    application = FastAPI(
        title="MediSupply Intelligence Dashboard",
        version="phase14_v1",
        description=DISCLAIMER,
        docs_url="/api/docs",
        redoc_url=None,
    )
    application.state.store = stores
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @application.get("/api/health")
    def health() -> dict[str, Any]:
        store = stores.current()
        return {
            "status": "ok",
            "snapshot": store.payload.get("snapshot"),
            "groups": len(store.groups),
        }

    @application.get("/api/meta")
    def metadata() -> dict[str, Any]:
        return stores.current().metadata()

    @application.get("/api/shortages")
    def shortages(
        q: str = Query(default="", max_length=120),
        tier: str | None = Query(default=None),
        manufacturer: str | None = Query(default=None, max_length=180),
    ) -> dict[str, Any]:
        if tier is not None and tier not in RISK_ORDER:
            raise HTTPException(status_code=422, detail="Unknown risk tier")
        records = stores.current().search(q, tier, manufacturer)
        return {
            "count": len(records),
            "records": records,
            "disclaimer": DISCLAIMER,
        }

    @application.get("/api/shortages/{shortage_id}")
    def shortage_detail(shortage_id: int) -> dict[str, Any]:
        try:
            return stores.current().detail(shortage_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Shortage not found") from exc

    @application.get("/drug/{shortage_id}", include_in_schema=False)
    def detail_page(shortage_id: int) -> FileResponse:
        store = stores.current()
        if shortage_id not in store.group_by_shortage_id:
            raise HTTPException(status_code=404, detail="Shortage not found")
        return FileResponse(STATIC_DIR / "index.html")

    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return application


app = create_app()
