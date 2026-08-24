import json
import tempfile
import unittest
from pathlib import Path

from src.ingestion.diff_snapshots import _write_report, compare_snapshots


class DiffSnapshotsTest(unittest.TestCase):
    def test_reports_required_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous = root / "2026-08-20_12"
            latest = root / "2026-08-21_12"
            previous.mkdir()
            latest.mkdir()

            old_stable = self.shortage("111-11", "Current", "Unavailable", "Manufacturing")
            old_removed = self.shortage("222-22", "Current", "Unavailable", "Demand")
            new_stable = self.shortage("111-11", "Resolved", "Available", "Supply restored")
            new_shortage = self.shortage("333-33", "Current", "Limited Availability", "Demand")

            self.write(previous / "shortages.json", [old_stable, old_removed])
            self.write(latest / "shortages.json", [new_stable, new_shortage])
            self.write(previous / "recalls.json", [{"recall_number": "R-1"}])
            self.write(latest / "recalls.json", [{"recall_number": "R-1"}, {"recall_number": "R-2"}])

            report = compare_snapshots(previous, latest)
            report_path = latest / "diff.json"
            _write_report(report, report_path)
            saved_report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(
            report["summary"],
            {
                "new_shortages": 1,
                "resolved_shortages": 2,
                "new_recalls": 1,
                "changed_shortage_fields": 1,
            },
        )
        self.assertEqual(
            report["changed_shortage_fields"][0]["changes"],
            {
                "shortage_reason": {"before": "Manufacturing", "after": "Supply restored"},
                "availability": {"before": "Unavailable", "after": "Available"},
            },
        )
        self.assertEqual(saved_report["summary"], report["summary"])

    @staticmethod
    def shortage(ndc: str, status: str, availability: str, reason: str) -> dict[str, str]:
        return {
            "package_ndc": ndc,
            "initial_posting_date": "01/01/2026",
            "presentation": f"Product {ndc}",
            "status": status,
            "availability": availability,
            "shortage_reason": reason,
        }

    @staticmethod
    def write(path: Path, records: list[dict[str, str]]) -> None:
        path.write_text(json.dumps({"results": records}), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
