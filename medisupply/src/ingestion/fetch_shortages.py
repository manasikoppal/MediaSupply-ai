"""Fetch the complete openFDA drug shortages dataset."""

try:
    from ._openfda import fetch_source
except ImportError:  # Allow: python src/ingestion/fetch_shortages.py
    from _openfda import fetch_source


if __name__ == "__main__":
    path, count = fetch_source("shortages")
    print(f"Saved {count:,} shortage records to {path}")
