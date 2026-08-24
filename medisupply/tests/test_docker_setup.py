import gzip
import json
import sqlite3
import struct
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

import src.api.app as api_app
from src.intelligence.engine import IntelligenceEngine as RealIntelligenceEngine

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_compose_exposes_single_read_only_dashboard_service() -> None:
    compose = yaml.safe_load((REPOSITORY_ROOT / "docker-compose.yml").read_text())
    assert set(compose["services"]) == {"dashboard"}
    dashboard = compose["services"]["dashboard"]
    assert dashboard["ports"] == ["${MEDISUPPLY_PORT:-8000}:8000"]
    assert dashboard["read_only"] is True
    assert dashboard["security_opt"] == ["no-new-privileges:true"]
    assert "environment" not in dashboard
    assert "env_file" not in dashboard


def test_docker_build_is_credential_free_and_allowlisted() -> None:
    dockerfile = (REPOSITORY_ROOT / "Dockerfile").read_text()
    dockerignore = (REPOSITORY_ROOT / ".dockerignore").read_text()
    compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text()
    combined_runtime_configuration = dockerfile + compose
    assert "ANTHROPIC_API_KEY" not in combined_runtime_configuration
    assert "OPENFDA_API_KEY" not in combined_runtime_configuration
    assert "FDA_API_KEY" not in combined_runtime_configuration
    for excluded in (
        ".env",
        "data/raw/",
        "data/snapshots/",
        "data/processed/",
        "logs/",
        "artifacts/",
        "scripts/run_teacher_labeling.py",
        "src/models/teacher_labeling.py",
    ):
        assert excluded in dockerignore
    assert "COPY --chown=medisupply:medisupply src/api" in dockerfile
    assert "data/dashboard/knowledge_graph.sqlite.gz" in dockerfile


def test_packaged_graph_is_sqlite_and_matches_dashboard_snapshot() -> None:
    graph_archive = REPOSITORY_ROOT / "data/dashboard/knowledge_graph.sqlite.gz"
    with gzip.open(graph_archive, "rb") as handle:
        assert handle.read(16) == b"SQLite format 3\x00"
    payload = json.loads(
        (REPOSITORY_ROOT / "data/dashboard/current.json").read_text()
    )
    database = REPOSITORY_ROOT / payload["database"]
    with sqlite3.connect(database) as connection:
        database_snapshot = connection.execute(
            "SELECT value FROM metadata WHERE key = 'snapshot'"
        ).fetchone()[0]
    assert payload["snapshot"] == database_snapshot
    with graph_archive.open("rb") as handle:
        handle.seek(-4, 2)
        uncompressed_size = struct.unpack("<I", handle.read(4))[0]
    assert uncompressed_size == database.stat().st_size


def test_detail_uses_precomputed_cause_without_label_files(
    monkeypatch, tmp_path: Path
) -> None:
    class RuntimeIntelligenceEngine(RealIntelligenceEngine):
        def __init__(self, database: Path) -> None:
            super().__init__(
                database,
                teacher_labels=tmp_path / "missing-teacher.jsonl",
                gold_labels=tmp_path / "missing-gold.jsonl",
                shortages_enriched=tmp_path / "missing-shortages.json",
            )

    monkeypatch.setattr(api_app, "IntelligenceEngine", RuntimeIntelligenceEngine)
    report = REPOSITORY_ROOT / "data/dashboard/current.json"
    with TestClient(api_app.create_app(report)) as client:
        records = client.get(
            "/api/shortages", params={"q": "lisdexamfetamine"}
        ).json()["records"]
        reserved = next(item for item in records if item["reserved_for_evaluation"])
        detail = client.get(
            f"/api/shortages/{reserved['representative_shortage_id']}"
        ).json()
        cause = detail["score"]["root_cause"]
        assert cause["primary_cause"] == "unknown"
        assert cause["unknown_reason"] == "reserved_for_evaluation"
        assert cause["reserved_for_evaluation"] is True
