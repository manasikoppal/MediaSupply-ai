"""Fetch the complete openFDA drug recall enforcement dataset."""

try:
    from ._openfda import fetch_source
except ImportError:  # Allow: python src/ingestion/fetch_recalls.py
    from _openfda import fetch_source


if __name__ == "__main__":
    path, count = fetch_source("recalls")
    print(f"Saved {count:,} recall records to {path}")
