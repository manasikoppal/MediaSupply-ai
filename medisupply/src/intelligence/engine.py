"""Explainable, deterministic risk calculations over the Phase 6 graph.

No model is called here. Phase 10 labels are only read as already-structured
root-cause inputs. All availability and overlap statements describe the graph
snapshot, not a live FDA query.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Self

from src.cleaning.normalizers import normalize_drug_name, normalize_ndc

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MANUFACTURING_CAUSES = frozenset(
    {
        "active_ingredient_shortage",
        "inactive_ingredient_shortage",
        "manufacturing_quality_problem",
        "manufacturing_capacity",
    }
)

# The five components sum to 100. Unknown cause receives a small uncertainty
# prior instead of being treated as evidence that manufacturing risk is absent.
FRAGILITY_WEIGHTS = {
    "manufacturer_concentration": 20,
    "shortage_duration": 30,
    "recall_overlap": 30,
    "alternative_availability": 10,
    "manufacturing_root_cause": 10,
}


def _latest_database() -> Path:
    databases = sorted(
        (REPOSITORY_ROOT / "data" / "processed").glob(
            "*/knowledge_graph.sqlite"
        )
    )
    if not databases:
        raise FileNotFoundError("No processed knowledge_graph.sqlite found")
    return databases[-1]


def _load_results(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text())
    if isinstance(payload, dict):
        payload = payload.get("results", payload.get("records", []))
    if not isinstance(payload, list):
        raise TypeError(f"Expected a JSON record list in {path}")
    return payload


def _stable_hash(parts: Iterable[Any], length: int = 10) -> str:
    material = "\x1f".join(str(part or "") for part in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:length]


def _shortage_source_id(record: dict[str, Any]) -> str:
    """Reproduce Phase 9/10's stable shortage source identifier."""
    suffix = _stable_hash(
        (
            record.get("package_ndc"),
            record.get("initial_posting_date"),
            record.get("presentation"),
            record.get("company_name"),
        )
    )
    return f"{record.get('package_ndc', 'missing')}:{suffix}"


def _parse_fda_date(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%m/%d/%Y", "%Y%m%d", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).date()
        except ValueError:
            pass
    return None


def _risk_tier(score: int) -> str:
    if score >= 65:
        return "high"
    if score >= 45:
        return "elevated"
    if score >= 25:
        return "moderate"
    return "low"


class IntelligenceEngine:
    """Reusable read-only connection and caches for Phase 13 calculations."""

    def __init__(
        self,
        database: str | Path | None = None,
        *,
        teacher_labels: str | Path | None = None,
        gold_labels: str | Path | None = None,
        shortages_enriched: str | Path | None = None,
        as_of: date | str | None = None,
    ) -> None:
        self.database = Path(database) if database else _latest_database()
        uri = f"file:{self.database.resolve()}?mode=ro"
        self.connection = sqlite3.connect(uri, uri=True)
        self.connection.row_factory = sqlite3.Row
        self.snapshot = self._metadata("snapshot") or self.database.parent.name
        self.generated_at = self._metadata("generated_at")
        self.equivalence_method = self._metadata("equivalence_method")
        self.as_of = self._resolve_as_of(as_of)
        self.teacher_labels_path = Path(teacher_labels) if teacher_labels else (
            REPOSITORY_ROOT / "data" / "teacher" / "labeled.jsonl"
        )
        self.gold_labels_path = Path(gold_labels) if gold_labels else (
            REPOSITORY_ROOT / "data" / "gold" / "labeled.jsonl"
        )
        self.shortages_enriched_path = (
            Path(shortages_enriched)
            if shortages_enriched
            else self.database.parent / "shortages_enriched.json"
        )
        self._cause_by_shortage_id: dict[int, dict[str, Any]] | None = None
        self._evaluation_reserved_shortage_ids: set[int] = set()
        self._fda_no_reason_shortage_ids: set[int] = set()
        self._concentration_cache: dict[tuple[int, ...], dict[str, Any]] = {}

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def _metadata(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key = ?", (key,)
        ).fetchone()
        return str(row[0]) if row else None

    def _resolve_as_of(self, value: date | str | None) -> date:
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            parsed = _parse_fda_date(value)
            if parsed:
                return parsed
            raise ValueError(f"Unsupported as_of date: {value}")
        # The snapshot hour is the defensible observation boundary. It avoids
        # pretending that stale snapshot statuses are live through today's date.
        parsed = _parse_fda_date(self.snapshot[:10].replace("_", "-"))
        if parsed:
            return parsed
        if self.generated_at:
            try:
                return datetime.fromisoformat(self.generated_at).date()
            except ValueError:
                pass
        return datetime.now().astimezone().date()

    def _product_ids_for_ndc(self, value: str) -> list[str]:
        ndc = normalize_ndc(value)
        if not ndc:
            raise ValueError(
                f"{value!r} is not an unambiguous 9-digit product or 11-digit package NDC"
            )
        rows = self.connection.execute(
            "SELECT product_id FROM product_ndcs WHERE ndc = ? ORDER BY product_id",
            (ndc,),
        ).fetchall()
        if not rows:
            raise LookupError(f"NDC {value!r} ({ndc}) is not present in the graph")
        return [str(row[0]) for row in rows]

    def _product(self, product_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT p.product_id, p.product_ndc, p.generic_name, p.brand_name,
                   p.dosage_form, p.route, p.finished, p.manufacturer_id,
                   m.display_name AS manufacturer
            FROM drug_products p
            JOIN manufacturers m ON m.manufacturer_id = p.manufacturer_id
            WHERE p.product_id = ?
            """,
            (product_id,),
        ).fetchone()
        if not row:
            raise LookupError(f"Unknown product_id {product_id}")
        result = dict(row)
        result["finished"] = bool(result["finished"])
        result["ndcs"] = [
            str(item[0])
            for item in self.connection.execute(
                "SELECT ndc FROM product_ndcs WHERE product_id = ? ORDER BY ndc",
                (product_id,),
            )
        ]
        result["ingredients"] = [
            {"name": str(item[0]), "strength": str(item[1])}
            for item in self.connection.execute(
                """
                SELECT i.display_name, pi.strength
                FROM product_ingredients pi
                JOIN active_ingredients i ON i.ingredient_id = pi.ingredient_id
                WHERE pi.product_id = ? ORDER BY i.display_name, pi.strength
                """,
                (product_id,),
            )
        ]
        return result

    def _availability_flags(self, product_id: str) -> tuple[bool, bool]:
        row = self.connection.execute(
            """
            SELECT EXISTS(
                       SELECT 1 FROM current_product_shortages
                       WHERE product_id = ?
                   ),
                   EXISTS(
                       SELECT 1 FROM ongoing_product_recalls
                       WHERE product_id = ?
                   )
            """,
            (product_id, product_id),
        ).fetchone()
        return bool(row[0]), bool(row[1])

    def _ingredient_ids_for_products(self, product_ids: list[str]) -> list[int]:
        placeholders = ",".join("?" for _ in product_ids)
        return [
            int(row[0])
            for row in self.connection.execute(
                f"SELECT DISTINCT ingredient_id FROM product_ingredients "
                f"WHERE product_id IN ({placeholders}) ORDER BY ingredient_id",
                product_ids,
            )
        ]

    def _ingredient_ids_for_name(self, active_ingredient: str) -> list[int]:
        normalized = normalize_drug_name(active_ingredient)
        if not normalized:
            raise ValueError("active_ingredient must contain a name")
        exact = self.connection.execute(
            "SELECT ingredient_id FROM active_ingredients WHERE normalized_name = ?",
            (normalized,),
        ).fetchall()
        if exact:
            return [int(row[0]) for row in exact]
        # A unique containment match makes convenient queries such as
        # "lisdexamfetamine" resolve to "lisdexamfetamine dimesylate" without
        # silently merging multiple ingredients.
        matches = self.connection.execute(
            """
            SELECT ingredient_id FROM active_ingredients
            WHERE normalized_name LIKE ? ORDER BY ingredient_id
            """,
            (f"%{normalized}%",),
        ).fetchall()
        if len(matches) == 1:
            return [int(matches[0][0])]
        if not matches:
            raise LookupError(f"Active ingredient {active_ingredient!r} not found")
        raise ValueError(
            f"Active ingredient {active_ingredient!r} is ambiguous ({len(matches)} matches); "
            "use the full FDA ingredient name"
        )

    def _manufacturer_concentration_for_ids(
        self, ingredient_ids: list[int]
    ) -> dict[str, Any]:
        key = tuple(sorted(ingredient_ids))
        if key in self._concentration_cache:
            return self._concentration_cache[key]
        placeholders = ",".join("?" for _ in ingredient_ids)
        names = [
            str(row[0])
            for row in self.connection.execute(
                f"SELECT display_name FROM active_ingredients WHERE ingredient_id IN ({placeholders}) "
                "ORDER BY display_name",
                ingredient_ids,
            )
        ]
        rows = self.connection.execute(
            f"""
            SELECT DISTINCT m.manufacturer_id, m.display_name
            FROM product_ingredients pi
            JOIN drug_products p ON p.product_id = pi.product_id
            JOIN manufacturers m ON m.manufacturer_id = p.manufacturer_id
            WHERE pi.ingredient_id IN ({placeholders})
              AND p.finished = 1
              AND NOT EXISTS (
                  SELECT 1 FROM current_product_shortages s
                  WHERE s.product_id = p.product_id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM ongoing_product_recalls r
                  WHERE r.product_id = p.product_id
              )
            ORDER BY m.display_name
            """,
            ingredient_ids,
        ).fetchall()
        result = {
            "active_ingredients": names,
            "available_manufacturer_count": len(rows),
            "available_manufacturers": [str(row[1]) for row in rows],
            "availability_definition": (
                "finished NDC product with no linked Current shortage and no linked Ongoing recall"
            ),
            "as_of": self.as_of.isoformat(),
            "snapshot": self.snapshot,
        }
        self._concentration_cache[key] = result
        return result

    def manufacturer_concentration(self, active_ingredient: str) -> dict[str, Any]:
        """Count manufacturers with an operationally available product."""
        ids = self._ingredient_ids_for_name(active_ingredient)
        return self._manufacturer_concentration_for_ids(ids)

    def _shortage_rows_for_reference(
        self, ndc_or_shortage_id: str | int
    ) -> list[sqlite3.Row]:
        shortage_reference = self._shortage_id_reference(ndc_or_shortage_id)
        if shortage_reference is not None:
            rows = self.connection.execute(
                "SELECT * FROM shortages WHERE shortage_id = ?",
                (shortage_reference,),
            ).fetchall()
        else:
            product_ids = self._product_ids_for_ndc(ndc_or_shortage_id)
            placeholders = ",".join("?" for _ in product_ids)
            rows = self.connection.execute(
                f"""
                SELECT DISTINCT s.*
                FROM shortages s
                JOIN product_shortages ps ON ps.shortage_id = s.shortage_id
                WHERE ps.product_id IN ({placeholders})
                ORDER BY CASE WHEN s.status = 'Current' THEN 0 ELSE 1 END,
                         s.initial_posting_date, s.shortage_id
                """,
                product_ids,
            ).fetchall()
        if not rows:
            raise LookupError(f"No shortage found for {ndc_or_shortage_id!r}")
        return rows

    @staticmethod
    def _shortage_id_reference(value: str | int) -> int | None:
        if isinstance(value, int):
            return value
        text = str(value).strip()
        if text.casefold().startswith("shortage:"):
            text = text.split(":", 1)[1]
        # Graph shortage IDs are short integers; valid canonical NDCs are 9 or
        # 11 digits, so this does not steal an NDC-shaped string.
        if text.isdigit() and len(text) < 9:
            return int(text)
        return None

    def shortage_duration(self, ndc_or_shortage_id: str | int) -> dict[str, Any]:
        """Calculate durations, using snapshot date for current shortages."""
        records = []
        for row in self._shortage_rows_for_reference(ndc_or_shortage_id):
            start = _parse_fda_date(row["initial_posting_date"])
            current = str(row["status"]).casefold() == "current"
            end = self.as_of if current else _parse_fda_date(row["update_date"])
            duration = max(0, (end - start).days) if start and end else None
            records.append(
                {
                    "shortage_id": int(row["shortage_id"]),
                    "package_ndc": row["package_ndc"],
                    "generic_name": row["generic_name"],
                    "manufacturer": row["company_name"],
                    "status": row["status"],
                    "initial_posting_date": row["initial_posting_date"],
                    "end_date": end.isoformat() if end else None,
                    "duration_days": duration,
                    "ongoing": current,
                    "duration_basis": (
                        "snapshot_date_minus_initial_posting_date"
                        if current
                        else "update_date_minus_initial_posting_date"
                    ),
                }
            )
        current_records = [record for record in records if record["ongoing"]]
        candidates = current_records or records
        selected = max(
            candidates,
            key=lambda item: item["duration_days"]
            if item["duration_days"] is not None
            else -1,
        )
        return {
            **selected,
            "matched_shortages": records,
            "as_of": self.as_of.isoformat(),
            "snapshot": self.snapshot,
        }

    def _recall_linkage_metrics(self) -> dict[str, Any]:
        total = int(self.connection.execute("SELECT COUNT(*) FROM recalls").fetchone()[0])
        linked = int(
            self.connection.execute(
                "SELECT COUNT(DISTINCT recall_id) FROM product_recalls"
            ).fetchone()[0]
        )
        return {
            "overall_recall_linkage_pct": round(100 * linked / total, 2) if total else 0.0,
            "linked_recall_records": linked,
            "total_recall_records": total,
            "identifier_present_linkage_pct": 100.0,
        }

    def recall_overlap(self, ndc: str) -> dict[str, Any]:
        """Find same-ingredient, different-manufacturer active recall overlap."""
        target_ids = self._product_ids_for_ndc(ndc)
        target_manufacturer_ids = {
            int(self._product(product_id)["manufacturer_id"])
            for product_id in target_ids
        }
        target_shortage = any(
            self._availability_flags(product_id)[0] for product_id in target_ids
        )
        ingredient_ids = self._ingredient_ids_for_products(target_ids)
        if not ingredient_ids:
            overlaps: list[dict[str, Any]] = []
        else:
            i_placeholders = ",".join("?" for _ in ingredient_ids)
            m_placeholders = ",".join("?" for _ in target_manufacturer_ids)
            rows = self.connection.execute(
                f"""
                SELECT DISTINCT p.product_id, p.product_ndc, p.generic_name,
                       m.display_name AS manufacturer, r.recall_id,
                       r.recall_number, r.event_id, r.classification,
                       r.reason_for_recall, pr.match_method,
                       i.display_name AS shared_ingredient,
                       EXISTS(
                           SELECT 1 FROM current_product_shortages s
                           WHERE s.product_id = p.product_id
                       ) AS also_in_shortage
                FROM product_ingredients pi
                JOIN active_ingredients i ON i.ingredient_id = pi.ingredient_id
                JOIN drug_products p ON p.product_id = pi.product_id
                JOIN manufacturers m ON m.manufacturer_id = p.manufacturer_id
                JOIN product_recalls pr ON pr.product_id = p.product_id
                JOIN recalls r ON r.recall_id = pr.recall_id
                WHERE pi.ingredient_id IN ({i_placeholders})
                  AND p.manufacturer_id NOT IN ({m_placeholders})
                  AND p.finished = 1
                  AND NOT EXISTS (
                      SELECT 1 FROM product_ingredients extra
                      WHERE extra.product_id = p.product_id
                        AND extra.ingredient_id NOT IN ({i_placeholders})
                  )
                  AND (
                      SELECT COUNT(DISTINCT member.ingredient_id)
                      FROM product_ingredients member
                      WHERE member.product_id = p.product_id
                  ) = ?
                  AND r.status = 'Ongoing'
                ORDER BY m.display_name, p.product_ndc, r.recall_number
                """,
                ingredient_ids
                + sorted(target_manufacturer_ids)
                + ingredient_ids
                + [len(ingredient_ids)],
            ).fetchall()
            overlaps = []
            for row in rows:
                item = dict(row)
                item["also_in_shortage"] = bool(item["also_in_shortage"])
                overlaps.append(item)

        overlap = target_shortage and bool(overlaps)
        methods = sorted({str(item["match_method"]) for item in overlaps})
        if overlap and any(
            method.startswith("openfda_") or method == "explicit_ndc_text"
            for method in methods
        ):
            confidence = "high"
            basis = "positive overlap backed by an explicit or harmonized product identifier"
        elif overlap:
            confidence = "medium"
            basis = "positive overlap linked indirectly (for example, by application number)"
        else:
            confidence = "limited"
            basis = (
                "no linked overlap was found, but unlinked recalls prevent a high-confidence negative"
            )
        return {
            "ndc": normalize_ndc(ndc),
            "shortage_active_for_target": target_shortage,
            "recall_overlap": overlap,
            "scope": (
                "same active-ingredient set (strength may differ), different manufacturer, "
                "Ongoing recall"
            ),
            "overlapping_products": overlaps,
            "overlapping_product_count": len({item["product_id"] for item in overlaps}),
            "overlapping_recall_count": len({item["recall_id"] for item in overlaps}),
            "linkage_confidence": {
                "level": confidence,
                "basis": basis,
                "match_methods": methods,
                **self._recall_linkage_metrics(),
            },
            "as_of": self.as_of.isoformat(),
            "snapshot": self.snapshot,
        }

    def alternative_availability(self, ndc: str) -> dict[str, Any]:
        """List strict Phase 6 proxy equivalents that are operationally available."""
        target_ids = self._product_ids_for_ndc(ndc)
        placeholders = ",".join("?" for _ in target_ids)
        rows = self.connection.execute(
            f"""
            SELECT DISTINCT p.product_id
            FROM product_equivalence_groups selected
            JOIN product_equivalence_groups other
              ON other.group_id = selected.group_id
            JOIN drug_products p ON p.product_id = other.product_id
            WHERE selected.product_id IN ({placeholders})
              AND other.product_id NOT IN ({placeholders})
              AND p.finished = 1
            ORDER BY p.product_id
            """,
            target_ids + target_ids,
        ).fetchall()
        available = []
        unavailable = []
        for row in rows:
            product = self._product(str(row[0]))
            shortage, recall = self._availability_flags(product["product_id"])
            product["currently_in_shortage"] = shortage
            product["under_ongoing_recall"] = recall
            if not shortage and not recall:
                available.append(product)
            else:
                product["unavailable_reasons"] = [
                    reason
                    for active, reason in (
                        (shortage, "current_shortage"),
                        (recall, "ongoing_recall"),
                    )
                    if active
                ]
                unavailable.append(product)
        return {
            "ndc": normalize_ndc(ndc),
            "equivalence_method": self.equivalence_method,
            "orange_book_rated": False,
            "candidate_equivalent_count": len(rows),
            "available_alternative_count": len(available),
            "available_alternatives": available,
            "unavailable_alternative_count": len(unavailable),
            "unavailable_alternatives": unavailable,
            "availability_definition": (
                "finished proxy-equivalent product with no linked Current shortage "
                "and no linked Ongoing recall"
            ),
            "as_of": self.as_of.isoformat(),
            "snapshot": self.snapshot,
        }

    def _load_shortage_causes(self) -> dict[int, dict[str, Any]]:
        if self._cause_by_shortage_id is not None:
            return self._cause_by_shortage_id
        result: dict[int, dict[str, Any]] = {}
        if not self.shortages_enriched_path.exists():
            self._cause_by_shortage_id = result
            return result
        source_to_shortages: dict[str, list[int]] = {}
        for index, record in enumerate(
            _load_results(self.shortages_enriched_path), start=1
        ):
            source_to_shortages.setdefault(_shortage_source_id(record), []).append(
                index
            )
            reason = record.get("shortage_reason")
            if reason is None or str(reason).strip().casefold() in {"", "other"}:
                self._fda_no_reason_shortage_ids.add(index)
        if self.teacher_labels_path.exists():
            with self.teacher_labels_path.open() as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    label = json.loads(line)
                    if label.get("source") != "shortage":
                        continue
                    event = label.get("event") or {}
                    for source_id in label.get("source_record_ids") or []:
                        for shortage_id in source_to_shortages.get(
                            str(source_id), []
                        ):
                            result[shortage_id] = {
                                "primary_cause": event.get("primary_cause", "unknown"),
                                "label_method": label.get("label_method"),
                                "teacher_id": label.get("teacher_id"),
                                "source_record_id": source_id,
                            }
        if self.gold_labels_path.exists():
            with self.gold_labels_path.open() as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    label = json.loads(line)
                    if label.get("source") != "shortage":
                        continue
                    self._evaluation_reserved_shortage_ids.update(
                        source_to_shortages.get(
                            str(label.get("source_record_id")), []
                        )
                    )
        self._cause_by_shortage_id = result
        return result

    def _root_cause(self, shortage_id: int) -> dict[str, Any]:
        found = self._load_shortage_causes().get(shortage_id)
        if found:
            unknown_reason = (
                "fda_reason_not_provided"
                if found["primary_cause"] == "unknown"
                and found["label_method"] == "policy_unknown"
                else None
            )
            return {
                **found,
                "available": True,
                "reserved_for_evaluation": False,
                "unknown_reason": unknown_reason,
            }
        if shortage_id in self._evaluation_reserved_shortage_ids:
            return {
                "primary_cause": "unknown",
                "label_method": "reserved_human_gold_evaluation",
                "teacher_id": None,
                "source_record_id": None,
                "available": False,
                "reserved_for_evaluation": True,
                "unknown_reason": "reserved_for_evaluation",
            }
        if shortage_id in self._fda_no_reason_shortage_ids:
            return {
                "primary_cause": "unknown",
                "label_method": "deterministic_fda_no_reason",
                "teacher_id": None,
                "source_record_id": None,
                "available": True,
                "reserved_for_evaluation": False,
                "unknown_reason": "fda_reason_not_provided",
            }
        return {
            "primary_cause": "unknown",
            "label_method": "needs_teacher_labeling",
            "teacher_id": None,
            "source_record_id": None,
            "available": False,
            "reserved_for_evaluation": False,
            "unknown_reason": "needs_teacher_labeling",
        }

    @staticmethod
    def _concentration_points(count: int) -> tuple[int, str]:
        if count == 0:
            return 20, "0 available manufacturers"
        if count == 1:
            return 18, "1 available manufacturer"
        if count == 2:
            return 15, "2 available manufacturers"
        if count <= 4:
            return 10, "3-4 available manufacturers"
        if count <= 9:
            return 4, "5-9 available manufacturers"
        return 0, "10+ available manufacturers"

    @staticmethod
    def _duration_points(days: int | None, ongoing: bool) -> tuple[int, str]:
        if not ongoing or days is None:
            return 0, "not current or duration unavailable"
        if days < 30:
            return 3, "under 30 days"
        if days < 90:
            return 8, "30-89 days"
        if days < 180:
            return 15, "90-179 days"
        if days < 365:
            return 22, "180-364 days"
        return 30, "365+ days"

    @staticmethod
    def _alternative_points(count: int) -> tuple[int, str]:
        if count == 0:
            return 10, "no available proxy equivalent"
        if count == 1:
            return 7, "1 available proxy equivalent"
        if count <= 3:
            return 3, "2-3 available proxy equivalents"
        return 0, "4+ available proxy equivalents"

    def supply_fragility_score(
        self, ndc_or_shortage_id: str | int
    ) -> dict[str, Any]:
        """Return a 0-100 component score and all supporting observations."""
        duration = self.shortage_duration(ndc_or_shortage_id)
        shortage_id = int(duration["shortage_id"])
        if self._shortage_id_reference(ndc_or_shortage_id) is not None:
            product_rows = self.connection.execute(
                """
                SELECT p.product_id, p.product_ndc
                FROM product_shortages ps
                JOIN drug_products p ON p.product_id = ps.product_id
                WHERE ps.shortage_id = ? ORDER BY p.product_id
                """,
                (shortage_id,),
            ).fetchall()
            if not product_rows:
                raise LookupError(
                    f"Shortage {shortage_id} has no graph-linked NDC product; score unavailable"
                )
            product_id = str(product_rows[0][0])
            ndc = str(product_rows[0][1])
        else:
            product_id = self._product_ids_for_ndc(ndc_or_shortage_id)[0]
            ndc = normalize_ndc(ndc_or_shortage_id) or str(ndc_or_shortage_id)

        ingredient_ids = self._ingredient_ids_for_products([product_id])
        ingredient_concentrations = [
            self._manufacturer_concentration_for_ids([ingredient_id])
            for ingredient_id in ingredient_ids
        ]
        if not ingredient_concentrations:
            raise LookupError(f"Product {product_id} has no active-ingredient relationship")
        # For combination products, the least diversified ingredient is the
        # limiting supply-chain input. A union across ingredients would hide it.
        concentration = min(
            ingredient_concentrations,
            key=lambda item: int(item["available_manufacturer_count"]),
        )
        concentration = {
            **concentration,
            "selection_rule": "least-diversified active ingredient for combination products",
            "ingredient_breakdown": ingredient_concentrations,
        }
        overlap = self.recall_overlap(ndc)
        alternatives = self.alternative_availability(ndc)
        root_cause = self._root_cause(shortage_id)

        concentration_points, concentration_rule = self._concentration_points(
            int(concentration["available_manufacturer_count"])
        )
        duration_points, duration_rule = self._duration_points(
            duration["duration_days"], bool(duration["ongoing"])
        )
        overlap_points = 30 if overlap["recall_overlap"] else 0
        alternative_points, alternative_rule = self._alternative_points(
            int(alternatives["available_alternative_count"])
        )
        cause = str(root_cause["primary_cause"])
        if cause in MANUFACTURING_CAUSES:
            cause_points = 10
            cause_rule = "teacher cause is manufacturing-related"
        elif cause == "unknown":
            cause_points = 3
            cause_rule = "unknown receives a neutral uncertainty prior, not a clean bill of health"
        else:
            cause_points = 0
            cause_rule = "known cause is not in the documented manufacturing-related set"

        components = {
            "manufacturer_concentration": {
                "points": concentration_points,
                "max_points": 20,
                "rule": concentration_rule,
                "observed": concentration["available_manufacturer_count"],
            },
            "shortage_duration": {
                "points": duration_points,
                "max_points": 30,
                "rule": duration_rule,
                "observed_days": duration["duration_days"],
            },
            "recall_overlap": {
                "points": overlap_points,
                "max_points": 30,
                "rule": "30 when an active same-ingredient/different-manufacturer recall overlaps",
                "observed": overlap["recall_overlap"],
                "linkage_confidence": overlap["linkage_confidence"],
            },
            "alternative_availability": {
                "points": alternative_points,
                "max_points": 10,
                "rule": alternative_rule,
                "observed": alternatives["available_alternative_count"],
            },
            "manufacturing_root_cause": {
                "points": cause_points,
                "max_points": 10,
                "rule": cause_rule,
                "observed": cause,
                "teacher_label_available": root_cause["available"],
                "label_method": root_cause["label_method"],
                "reserved_for_evaluation": root_cause["reserved_for_evaluation"],
                "unknown_reason": root_cause["unknown_reason"],
            },
        }
        score = sum(int(component["points"]) for component in components.values())
        return {
            "ndc": ndc,
            "product": self._product(product_id),
            "shortage_id": shortage_id,
            "shortage": duration,
            "score": score,
            "risk_tier": _risk_tier(score),
            "components": components,
            "root_cause": root_cause,
            "manufacturer_concentration": concentration,
            "recall_overlap": overlap,
            "alternative_availability": alternatives,
            "weights": FRAGILITY_WEIGHTS,
            "as_of": self.as_of.isoformat(),
            "snapshot": self.snapshot,
        }


def _one_shot(method: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
    engine_keys = {
        "database",
        "teacher_labels",
        "gold_labels",
        "shortages_enriched",
        "as_of",
    }
    engine_kwargs = {key: kwargs.pop(key) for key in list(kwargs) if key in engine_keys}
    with IntelligenceEngine(**engine_kwargs) as engine:
        return getattr(engine, method)(*args, **kwargs)


def manufacturer_concentration(
    active_ingredient: str, **kwargs: Any
) -> dict[str, Any]:
    return _one_shot("manufacturer_concentration", active_ingredient, **kwargs)


def shortage_duration(ndc_or_shortage_id: str | int, **kwargs: Any) -> dict[str, Any]:
    return _one_shot("shortage_duration", ndc_or_shortage_id, **kwargs)


def recall_overlap(ndc: str, **kwargs: Any) -> dict[str, Any]:
    return _one_shot("recall_overlap", ndc, **kwargs)


def alternative_availability(ndc: str, **kwargs: Any) -> dict[str, Any]:
    return _one_shot("alternative_availability", ndc, **kwargs)


def supply_fragility_score(
    ndc_or_shortage_id: str | int, **kwargs: Any
) -> dict[str, Any]:
    return _one_shot("supply_fragility_score", ndc_or_shortage_id, **kwargs)
