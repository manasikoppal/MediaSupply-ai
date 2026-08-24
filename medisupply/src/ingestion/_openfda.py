"""Shared, dependency-free helpers for openFDA ingestion."""

from __future__ import annotations

import json
import os
import re
import ssl
import sys
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


PAGE_SIZE = 1_000
MAX_SKIP = 25_000
REQUEST_DELAY_SECONDS = 0.26
MAX_ATTEMPTS = 5
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_CONFIGS = {
    "shortages": ("https://api.fda.gov/drug/shortages.json", "package_ndc"),
    "recalls": ("https://api.fda.gov/drug/enforcement.json", "recall_number"),
    "ndc": ("https://api.fda.gov/drug/ndc.json", "product_id"),
    "drugsfda": ("https://api.fda.gov/drug/drugsfda.json", "application_number"),
}


def _ssl_context() -> ssl.SSLContext:
    """Use Python's CA bundle, with the macOS system bundle as a safe fallback."""
    context = ssl.create_default_context()
    default_cafile = ssl.get_default_verify_paths().cafile
    macos_cafile = Path("/etc/ssl/cert.pem")
    if default_cafile is None and macos_cafile.is_file():
        context.load_verify_locations(cafile=macos_cafile)
    return context


SSL_CONTEXT = _ssl_context()


def _with_query(url: str, **params: object) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({key: str(value) for key, value in params.items()})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _request_json(url: str) -> tuple[dict[str, Any], str | None]:
    request = Request(url, headers={"User-Agent": "medisupply-openfda-ingestion/1.0"})

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with urlopen(request, timeout=60, context=SSL_CONTEXT) as response:
                payload = json.load(response)
                link = response.headers.get("Link")
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise ValueError("openFDA response did not contain a results list")
            return payload, link
        except HTTPError as error:
            retryable = error.code == 429 or 500 <= error.code < 600
            if not retryable or attempt == MAX_ATTEMPTS:
                raise
            retry_after = error.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
        except (URLError, TimeoutError, json.JSONDecodeError):
            if attempt == MAX_ATTEMPTS:
                raise
            delay = 2**attempt

        print(f"Request failed; retrying in {delay:g}s ({attempt}/{MAX_ATTEMPTS})", file=sys.stderr)
        time.sleep(delay)

    raise RuntimeError("unreachable")


def _next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        match = re.match(r'\s*<([^>]+)>\s*;\s*rel=["\']?next["\']?', part, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _total(payload: dict[str, Any]) -> int:
    try:
        total = payload["meta"]["results"]["total"]
    except (KeyError, TypeError) as error:
        raise ValueError("openFDA response did not contain meta.results.total") from error
    if not isinstance(total, int) or total < 0:
        raise ValueError("openFDA returned an invalid record total")
    return total


def _write_page(handle: Any, records: list[dict[str, Any]], first: bool) -> bool:
    for record in records:
        if not first:
            handle.write(",\n")
        json.dump(record, handle, ensure_ascii=False, separators=(",", ":"))
        first = False
    return first


def fetch_all(
    endpoint: str,
    source: str,
    sort_field: str,
    output_path: Path | None = None,
) -> tuple[Path, int]:
    """Fetch an entire endpoint into one valid openFDA-style JSON response."""
    api_key = os.environ.get("OPENFDA_API_KEY") or os.environ.get("FDA_API_KEY")
    auth = {"api_key": api_key} if api_key else {}

    probe, _ = _request_json(_with_query(endpoint, limit=1, **auth))
    expected_total = _total(probe)
    use_search_after = expected_total > MAX_SKIP + PAGE_SIZE

    if use_search_after:
        next_url = _with_query(endpoint, limit=PAGE_SIZE, sort=f"{sort_field}:asc", **auth)
    else:
        next_url = _with_query(endpoint, limit=PAGE_SIZE, skip=0, **auth)

    if output_path is None:
        output_path = (
            REPOSITORY_ROOT / "data" / "raw" / source / f"{date.today().isoformat()}.json"
        )
    output_path = Path(output_path)
    output_dir = output_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=output_dir, prefix=f".{output_path.name}.", delete=False
    )
    temp_path = Path(temp_handle.name)

    count = 0
    page_number = 0
    first_record = True
    meta: dict[str, Any] | None = None

    try:
        with temp_handle as handle:
            handle.write('{"meta":')

            while next_url:
                payload, link = _request_json(next_url)
                if meta is None:
                    meta = payload["meta"]
                    json.dump(meta, handle, ensure_ascii=False, separators=(",", ":"))
                    handle.write(',"results":[\n')

                records = payload["results"]
                first_record = _write_page(handle, records, first_record)
                count += len(records)
                page_number += 1

                if count >= expected_total or len(records) < PAGE_SIZE:
                    next_url = None
                elif use_search_after:
                    next_url = _next_link(link)
                    if next_url and api_key and "api_key=" not in next_url:
                        next_url = _with_query(next_url, api_key=api_key)
                else:
                    next_url = _with_query(
                        endpoint, limit=PAGE_SIZE, skip=page_number * PAGE_SIZE, **auth
                    )

                if count and count % 10_000 == 0:
                    print(f"Fetched {count:,}/{expected_total:,} {source} records...", file=sys.stderr)
                if next_url:
                    time.sleep(REQUEST_DELAY_SECONDS)

            handle.write("\n]}\n")
            handle.flush()
            os.fsync(handle.fileno())

        if count != expected_total:
            raise RuntimeError(
                f"Incomplete {source} response: parsed {count:,} of {expected_total:,} records"
            )

        os.chmod(temp_path, 0o644)
        os.replace(temp_path, output_path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise

    return output_path, count


def fetch_source(source: str, output_path: Path | None = None) -> tuple[Path, int]:
    """Fetch a configured openFDA source to a raw or caller-provided path."""
    try:
        endpoint, sort_field = SOURCE_CONFIGS[source]
    except KeyError as error:
        raise ValueError(f"Unknown openFDA source: {source}") from error
    return fetch_all(endpoint, source, sort_field, output_path)
