import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

from src.api.app import create_app


def test_dashboard_and_health_routes() -> None:
    with TestClient(create_app()) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "MediSupply Intelligence" in page.text
        header = page.text.split("</header>", maxsplit=1)[0]
        footer = page.text.split("<footer>", maxsplit=1)[1]
        assert 'id="snapshot-label"' not in header
        assert 'id="snapshot-label"' in footer
        assert "Loading data freshness" in footer
        assert client.get("/static/styles.css").status_code == 200
        assert client.get("/static/app.js").status_code == 200

        health = client.get("/api/health").json()
        assert health["status"] == "ok"
        assert health["groups"] > 0
        metadata = client.get("/api/meta").json()
        assert metadata["manufacturers"] == sorted(
            metadata["manufacturers"], key=str.casefold
        )
        assert metadata["evaluation_reserved_package_records"] > 0
        assert metadata["fda_no_reason_package_records"] > 0


def test_shortage_list_is_grouped_sorted_and_searchable() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/shortages")
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] < 1107
        assert payload["records"][0]["score"] >= payload["records"][1]["score"]
        assert payload["records"][0]["package_count"] >= 1
        assert payload["records"][0]["requires_human_review"] is True
        assert all(item["requires_human_review"] for item in payload["records"])

        searched = client.get("/api/shortages", params={"q": "lisdexamfetamine"}).json()
        assert searched["count"] > 0
        assert all(
            "lisdexamfetamine" in item["generic_name"].casefold()
            for item in searched["records"]
        )

        filtered = client.get("/api/shortages", params={"tier": "high"}).json()
        assert filtered["count"] > 0
        assert all(item["risk_tier"] == "high" for item in filtered["records"])


def test_search_tier_and_manufacturer_filters_combine() -> None:
    with TestClient(create_app()) as client:
        first = client.get("/api/shortages", params={"tier": "high"}).json()[
            "records"
        ][0]
        combined = client.get(
            "/api/shortages",
            params={
                "q": first["generic_name"],
                "tier": first["risk_tier"],
                "manufacturer": first["manufacturer"],
            },
        ).json()
        assert combined["count"] >= 1
        assert all(
            item["risk_tier"] == first["risk_tier"]
            and item["manufacturer"] == first["manufacturer"]
            and first["generic_name"].casefold()
            in item["generic_name"].casefold()
            for item in combined["records"]
        )


def test_related_manufacturer_counts_clarify_repeated_drugs() -> None:
    with TestClient(create_app()) as client:
        records = client.get(
            "/api/shortages", params={"q": "Dobutamine Hydrochloride Injection"}
        ).json()["records"]
        assert len({item["manufacturer"] for item in records}) >= 2
        assert all(item["related_manufacturer_count"] >= 2 for item in records)
        assert all(item["related_package_count"] >= item["package_count"] for item in records)


def test_detail_exposes_components_confidence_and_review_flags() -> None:
    with TestClient(create_app()) as client:
        search = client.get(
            "/api/shortages", params={"q": "lisdexamfetamine"}
        ).json()
        shortage_id = search["records"][0]["representative_shortage_id"]
        response = client.get(f"/api/shortages/{shortage_id}")
        assert response.status_code == 200
        payload = response.json()

        assert payload["requires_human_review"] is True
        assert "Not medical or clinical advice" in payload["disclaimer"]
        assert payload["score"]["risk_tier"] == "high"
        assert payload["score"]["components"]["recall_overlap"][
            "linkage_confidence"
        ]["level"] == "high"
        cause = payload["score"]["components"]["manufacturing_root_cause"]
        assert cause["observed"] == "active_ingredient_shortage"
        assert cause["teacher_label_available"] is True

        page = client.get(f"/drug/{shortage_id}")
        assert page.status_code == 200
        assert "requires human review" in page.text


def test_limited_recall_confidence_generates_visible_warning_data() -> None:
    with TestClient(create_app()) as client:
        records = client.get("/api/shortages").json()["records"]
        limited = next(
            item for item in records if item["recall_linkage_confidence"] == "limited"
        )
        payload = client.get(
            f"/api/shortages/{limited['representative_shortage_id']}"
        ).json()
        assert payload["score"]["recall_overlap"]["linkage_confidence"][
            "level"
        ] == "limited"
        assert any("limited-confidence" in warning for warning in payload["warnings"])


def test_human_gold_records_are_identified_as_evaluation_reserved() -> None:
    with TestClient(create_app()) as client:
        records = client.get("/api/shortages").json()["records"]
        reserved = next(item for item in records if item["evaluation_reserved_count"])
        assert reserved["evaluation_reserved_count"] >= 1

        payload = client.get(
            f"/api/shortages/{reserved['representative_shortage_id']}"
        ).json()
        assert any("held-out human evaluation" in warning for warning in payload["warnings"])

        javascript = client.get("/static/app.js").text
        assert "unknown (reserved for evaluation)" in javascript
        assert javascript.count("All manufacturers") == 1
        styles = client.get("/static/styles.css").text
        assert ".manufacturer-filter > span" not in styles


def test_fda_no_reason_records_are_distinct_from_evaluation_reserved() -> None:
    with TestClient(create_app()) as client:
        records = client.get("/api/shortages").json()["records"]
        unknown_reasons = {
            item["unknown_reason"]
            for item in records
            if item["primary_cause"] == "unknown"
        }
        assert unknown_reasons == {
            "fda_reason_not_provided",
            "reserved_for_evaluation",
        }
        fda_gap = next(
            item
            for item in records
            if item["unknown_reason"] == "fda_reason_not_provided"
        )
        assert fda_gap["reserved_for_evaluation"] is False
        assert fda_gap["primary_cause"] == "unknown"

        payload = client.get(
            f"/api/shortages/{fda_gap['representative_shortage_id']}"
        ).json()
        root_cause = payload["score"]["root_cause"]
        assert root_cause["unknown_reason"] == "fda_reason_not_provided"
        assert any("FDA provided no usable shortage reason" in warning for warning in payload["warnings"])

        javascript = client.get("/static/app.js").text
        assert "unknown (no reason provided by FDA)" in javascript
        styles = client.get("/static/styles.css").text
        assert ".cause-evaluation" in styles
        assert ".cause-fda-gap" in styles
        assert ".cause-classified" in styles
        assert ".cause-needs-labeling" in styles
        assert "unknown (needs teacher labeling)" in javascript


def test_running_dashboard_reloads_atomically_promoted_artifact(
    tmp_path: Path,
) -> None:
    source = Path("data/dashboard/current.json")
    report = tmp_path / "current.json"
    payload = json.loads(source.read_text())
    report.write_text(json.dumps(payload))

    with TestClient(create_app(report)) as client:
        assert client.get("/api/health").json()["snapshot"] == payload["snapshot"]
        payload["generated_at"] = "test-promoted-artifact"
        report.write_text(json.dumps(payload, indent=2))
        os.utime(report, None)
        assert (
            client.get("/api/meta").json()["generated_at"]
            == "test-promoted-artifact"
        )


def test_unknown_tier_and_shortage_return_errors() -> None:
    with TestClient(create_app()) as client:
        assert client.get("/api/shortages", params={"tier": "urgent"}).status_code == 422
        assert client.get("/api/shortages/9999999").status_code == 404
        assert client.get("/drug/9999999").status_code == 404
