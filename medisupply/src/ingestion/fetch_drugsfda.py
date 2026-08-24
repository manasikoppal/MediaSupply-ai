"""Fetch the complete openFDA Drugs@FDA dataset."""

try:
    from ._openfda import fetch_source
except ImportError:  # Allow: python src/ingestion/fetch_drugsfda.py
    from _openfda import fetch_source


if __name__ == "__main__":
    path, count = fetch_source("drugsfda")
    print(f"Saved {count:,} Drugs@FDA records to {path}")
