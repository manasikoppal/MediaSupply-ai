"""Fetch the complete openFDA National Drug Code dataset."""

try:
    from ._openfda import fetch_source
except ImportError:  # Allow: python src/ingestion/fetch_ndc.py
    from _openfda import fetch_source


if __name__ == "__main__":
    path, count = fetch_source("ndc")
    print(f"Saved {count:,} NDC records to {path}")
