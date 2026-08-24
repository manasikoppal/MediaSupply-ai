import json
import sqlite3
from datetime import date
from pathlib import Path

from src.graph.schema import create_schema
from src.intelligence.engine import IntelligenceEngine, _shortage_source_id


def _build_graph(path: Path) -> tuple[Path, Path]:
    connection = sqlite3.connect(path)
    create_schema(connection)
    connection.executemany(
        "INSERT INTO metadata(key, value) VALUES (?, ?)",
        [
            ("snapshot", "2026-01-31_08"),
            ("generated_at", "2026-01-31T08:00:00-05:00"),
            (
                "equivalence_method",
                "proxy_active_ingredient_strength_dosage_form_route",
            ),
        ],
    )
    connection.executemany(
        "INSERT INTO manufacturers VALUES (?, ?, ?)",
        [(1, "target", "Target Inc."), (2, "recalled", "Recalled Inc."), (3, "available", "Available Inc.")],
    )
    connection.executemany(
        """
        INSERT INTO drug_products(
            product_id, product_ndc, generic_name, dosage_form, route,
            finished, manufacturer_id
        ) VALUES (?, ?, ?, 'TABLET', 'ORAL', 1, ?)
        """,
        [
            ("P1", "000010001", "Example Drug", 1),
            ("P2", "000020001", "Example Drug", 2),
            ("P3", "000030001", "Example Drug", 3),
        ],
    )
    connection.executemany(
        "INSERT INTO ndcs VALUES (?, 'product')",
        [("000010001",), ("000020001",), ("000030001",)],
    )
    connection.executemany(
        "INSERT INTO product_ndcs VALUES (?, ?, 'product_ndc')",
        [("P1", "000010001"), ("P2", "000020001"), ("P3", "000030001")],
    )
    connection.execute(
        "INSERT INTO active_ingredients VALUES (1, 'example ingredient', 'EXAMPLE INGREDIENT')"
    )
    connection.executemany(
        "INSERT INTO product_ingredients VALUES (?, 1, '10 mg/1', '10mg1')",
        [("P1",), ("P2",), ("P3",)],
    )
    connection.execute(
        """
        INSERT INTO shortages VALUES (
            1, '00001000101', 'Example Drug', 'Target Inc.', 'Current',
            'Manufacturing delay', NULL, NULL, '01/31/2025', '01/30/2026'
        )
        """
    )
    connection.execute("INSERT INTO product_shortages VALUES ('P1', 1, 'package_ndc')")
    connection.execute(
        """
        INSERT INTO recalls VALUES (
            'R1', 'D-1', 'E1', 'Ongoing', 'Class II', 'Recalled Inc.',
            'Example Drug NDC 00002-0001', 'Failed dissolution', '20260101'
        )
        """
    )
    connection.execute(
        "INSERT INTO product_recalls VALUES ('P2', 'R1', 'openfda_product_ndc')"
    )
    connection.execute(
        "INSERT INTO equivalence_groups VALUES ('G1', 'signature', 'proxy')"
    )
    connection.executemany(
        "INSERT INTO product_equivalence_groups VALUES (?, 'G1')",
        [("P1",), ("P2",), ("P3",)],
    )
    connection.commit()
    connection.close()

    enriched = path.with_name("shortages_enriched.json")
    record = {
        "package_ndc": "00001-0001-01",
        "initial_posting_date": "01/31/2025",
        "presentation": "Example Drug, 10 mg",
        "company_name": "Target Inc.",
        "shortage_reason": "Manufacturing delay",
    }
    enriched.write_text(json.dumps({"results": [record]}))
    labels = path.with_name("teacher.jsonl")
    labels.write_text(
        json.dumps(
            {
                "source": "shortage",
                "source_record_ids": [_shortage_source_id(record)],
                "teacher_id": "shortage:test",
                "label_method": "claude",
                "event": {"primary_cause": "manufacturing_capacity"},
            }
        )
        + "\n"
    )
    return enriched, labels


def test_phase13_calculations(tmp_path: Path) -> None:
    database = tmp_path / "knowledge_graph.sqlite"
    enriched, labels = _build_graph(database)
    with IntelligenceEngine(
        database,
        teacher_labels=labels,
        shortages_enriched=enriched,
        as_of=date(2026, 1, 31),
    ) as engine:
        concentration = engine.manufacturer_concentration("example ingredient")
        assert concentration["available_manufacturer_count"] == 1
        assert concentration["available_manufacturers"] == ["Available Inc."]

        duration = engine.shortage_duration("00001-0001")
        assert duration["duration_days"] == 365
        assert duration["ongoing"] is True
        assert engine.shortage_duration("shortage:1")["shortage_id"] == 1

        overlap = engine.recall_overlap("00001-0001")
        assert overlap["recall_overlap"] is True
        assert overlap["overlapping_product_count"] == 1
        assert overlap["linkage_confidence"]["level"] == "high"

        alternatives = engine.alternative_availability("00001-0001")
        assert alternatives["candidate_equivalent_count"] == 2
        assert alternatives["available_alternative_count"] == 1
        assert alternatives["available_alternatives"][0]["product_id"] == "P3"

        score = engine.supply_fragility_score(1)
        assert score["score"] == 95
        assert score["risk_tier"] == "high"
        assert score["components"]["manufacturing_root_cause"]["points"] == 10
        assert score["root_cause"]["primary_cause"] == "manufacturing_capacity"


def test_unknown_cause_is_uncertainty_not_manufacturing(tmp_path: Path) -> None:
    database = tmp_path / "knowledge_graph.sqlite"
    enriched, _ = _build_graph(database)
    with IntelligenceEngine(
        database,
        teacher_labels=tmp_path / "missing.jsonl",
        shortages_enriched=enriched,
        as_of=date(2026, 1, 31),
    ) as engine:
        score = engine.supply_fragility_score("00001-0001")
        component = score["components"]["manufacturing_root_cause"]
        assert component["observed"] == "unknown"
        assert component["points"] == 3
        assert component["teacher_label_available"] is False
        assert component["unknown_reason"] == "needs_teacher_labeling"
        assert score["root_cause"]["label_method"] == "needs_teacher_labeling"


def test_human_gold_shortage_is_marked_reserved_without_using_label(
    tmp_path: Path,
) -> None:
    database = tmp_path / "knowledge_graph.sqlite"
    enriched, _ = _build_graph(database)
    record = json.loads(enriched.read_text())["results"][0]
    gold = tmp_path / "gold.jsonl"
    gold.write_text(
        json.dumps(
            {
                "source": "shortage",
                "source_record_id": _shortage_source_id(record),
                "event": {"primary_cause": "manufacturing_capacity"},
            }
        )
        + "\n"
    )
    with IntelligenceEngine(
        database,
        teacher_labels=tmp_path / "missing.jsonl",
        gold_labels=gold,
        shortages_enriched=enriched,
        as_of=date(2026, 1, 31),
    ) as engine:
        score = engine.supply_fragility_score(1)
        cause = score["root_cause"]
        assert cause["primary_cause"] == "unknown"
        assert cause["reserved_for_evaluation"] is True
        assert cause["label_method"] == "reserved_human_gold_evaluation"
        assert score["components"]["manufacturing_root_cause"]["points"] == 3


def test_policy_unknown_is_identified_as_fda_reason_gap(tmp_path: Path) -> None:
    database = tmp_path / "knowledge_graph.sqlite"
    enriched, _ = _build_graph(database)
    record = json.loads(enriched.read_text())["results"][0]
    labels = tmp_path / "policy.jsonl"
    labels.write_text(
        json.dumps(
            {
                "source": "shortage",
                "source_record_ids": [_shortage_source_id(record)],
                "teacher_id": "shortage:policy-test",
                "label_method": "policy_unknown",
                "event": {"primary_cause": "unknown"},
            }
        )
        + "\n"
    )
    with IntelligenceEngine(
        database,
        teacher_labels=labels,
        gold_labels=tmp_path / "missing-gold.jsonl",
        shortages_enriched=enriched,
        as_of=date(2026, 1, 31),
    ) as engine:
        score = engine.supply_fragility_score(1)
        cause = score["root_cause"]
        assert cause["primary_cause"] == "unknown"
        assert cause["unknown_reason"] == "fda_reason_not_provided"
        assert cause["reserved_for_evaluation"] is False
        assert score["components"]["manufacturing_root_cause"][
            "unknown_reason"
        ] == "fda_reason_not_provided"


def test_missing_fda_reason_is_deterministic_without_saved_teacher_label(
    tmp_path: Path,
) -> None:
    database = tmp_path / "knowledge_graph.sqlite"
    enriched, _ = _build_graph(database)
    payload = json.loads(enriched.read_text())
    payload["results"][0]["shortage_reason"] = None
    enriched.write_text(json.dumps(payload))
    with IntelligenceEngine(
        database,
        teacher_labels=tmp_path / "missing.jsonl",
        gold_labels=tmp_path / "missing-gold.jsonl",
        shortages_enriched=enriched,
        as_of=date(2026, 1, 31),
    ) as engine:
        cause = engine.supply_fragility_score(1)["root_cause"]
        assert cause["primary_cause"] == "unknown"
        assert cause["unknown_reason"] == "fda_reason_not_provided"
        assert cause["label_method"] == "deterministic_fda_no_reason"
        assert cause["available"] is True
