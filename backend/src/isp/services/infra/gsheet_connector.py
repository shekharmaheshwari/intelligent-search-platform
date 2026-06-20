"""
Google Sheets connector: fetch a spreadsheet via the Sheets API and return
a list of ISP document dicts ready for embedding and upsert.

This module is the *only* place that calls the Google Sheets SDK.  It never
imports from routers or domain services — it is a pure infrastructure module.

Credentials are loaded at call time from the ``GOOGLE_SERVICE_ACCOUNT_JSON``
environment variable (a JSON-encoded service account key).  The variable is
*not* cached at module level so rotating credentials takes effect without a
restart.

Row-to-document mapping strategy:
- Row ``header_row - 1`` (0-based) supplies column names.
- All subsequent rows become documents.
- ``field_map`` renames column headers before the ISP field shortcuts
  (``id_column``, ``title_column``, …) are resolved, so renaming and the
  shortcut mapping compose cleanly.
- When ``id_column`` is unset or the named column is absent, a stable 16-char
  MD5 hash of ``spreadsheet_id:row_index`` is used as the document id.
- Rows where every cell is an empty string are skipped when
  ``skip_empty_rows`` is True (the default).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from typing import Any

from isp.models import GSheetConnectorConfig

# Read-only scope — the connector never writes back to the spreadsheet.
_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def _load_credentials() -> Any:
    """
    Build a Google service account Credentials object from the environment.

    Raises RuntimeError when the env var is missing or contains an empty JSON
    object, which catches both "var not set" and "var set to {}" misconfiguration.
    """
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "{}")
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON — "
            "set it to the full service account key JSON string."
        ) from exc

    if not info:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is missing or empty. "
            "Export the service account key JSON as this environment variable."
        )

    # Lazy import keeps this module importable in environments that lack the
    # Google libraries (e.g. test runners that mock the connector entirely).
    from google.oauth2.service_account import Credentials  # type: ignore[import]

    return Credentials.from_service_account_info(info, scopes=_SCOPES)


def _fetch_sync(cfg: GSheetConnectorConfig) -> list[dict]:
    """
    Synchronous inner function that calls the Google Sheets REST API.

    Wrapped by ``fetch_and_parse`` in an executor so it does not block the
    asyncio event loop.  All CPU-light transformation (header extraction,
    field_map renaming, ISP field mapping) also happens here so the async
    wrapper stays thin.
    """
    # Lazy import — googleapiclient is an optional dependency that may not be
    # present in every environment; defer the ImportError to call time.
    from googleapiclient.discovery import build  # type: ignore[import]

    from googleapiclient.errors import HttpError  # type: ignore[import]

    creds = _load_credentials()
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)

    # Fetch the entire sheet in one call.
    # A1:ZZ10000 is used instead of the column-only A:ZZ because the Sheets API
    # rejects column-only ranges (e.g. "account!A:ZZ") for some sheet names —
    # the explicit row bounds avoid the parse error without limiting practical use.
    try:
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=cfg.spreadsheet_id, range=f"{cfg.sheet_name}!A1:ZZ10000")
            .execute()
        )
    except HttpError as exc:
        if exc.status_code == 400 and "Unable to parse range" in str(exc):
            # Tab name doesn't match — fetch available names and give a clear error.
            meta = service.spreadsheets().get(spreadsheetId=cfg.spreadsheet_id).execute()
            available = [s["properties"]["title"] for s in meta.get("sheets", [])]
            raise ValueError(
                f"Sheet tab '{cfg.sheet_name}' not found in spreadsheet '{cfg.spreadsheet_id}'. "
                f"Available tabs: {', '.join(available)}"
            ) from exc
        raise

    rows: list[list[str]] = result.get("values", [])

    if not rows:
        return []

    # cfg.header_row is 1-based; convert to 0-based index.
    header_idx = cfg.header_row - 1
    if header_idx >= len(rows):
        return []

    raw_headers = [str(h).strip() for h in rows[header_idx]]
    data_rows = rows[header_idx + 1 :]

    # Apply field_map renames to headers before resolving ISP shortcut columns.
    # A field_map value of '' signals "drop this column".
    headers = [cfg.field_map.get(h, h) for h in raw_headers]

    now = int(time.time())
    docs: list[dict] = []

    for row_index, row in enumerate(data_rows):
        # Pad short rows so zip covers all header positions.
        padded = list(row) + [""] * (len(headers) - len(row))

        row_dict: dict[str, str] = {}
        for col_name, cell_value in zip(headers, padded):
            if col_name == "":
                # field_map mapped this column to '' — drop it.
                continue
            row_dict[col_name] = str(cell_value).strip()

        if cfg.skip_empty_rows and all(v == "" for v in row_dict.values()):
            continue

        # Resolve ISP shortcut columns (id_column, title_column, …) to the
        # standard ISP field names.  Only applied when the column name is
        # non-empty and present in the row dict.
        doc: dict = dict(row_dict)  # start with all columns as extra fields

        # Build a case-insensitive lookup so users can enter "title" and match
        # a sheet column named "Title" or "TITLE" without extra configuration.
        ci = {k.lower(): v for k, v in row_dict.items()}

        if cfg.id_column and cfg.id_column.lower() in ci:
            doc["id"] = ci[cfg.id_column.lower()]
        else:
            doc["id"] = hashlib.md5(
                f"{cfg.spreadsheet_id}:{row_index}".encode()
            ).hexdigest()[:16]

        if cfg.title_column and cfg.title_column.lower() in ci:
            doc["title"] = ci[cfg.title_column.lower()]

        if cfg.content_column and cfg.content_column.lower() in ci:
            doc["content"] = ci[cfg.content_column.lower()]

        if cfg.url_column and cfg.url_column.lower() in ci:
            doc["url"] = ci[cfg.url_column.lower()]

        doc.setdefault("source_type", "gsheet")
        doc.setdefault("indexed_at", now)

        docs.append(doc)

    return docs


async def fetch_and_parse(cfg: GSheetConnectorConfig) -> list[dict]:
    """
    Fetch a Google Sheet and return a list of ISP document dicts.

    The Google API client is synchronous, so the blocking call is offloaded to
    the default thread pool executor to avoid blocking the asyncio event loop.

    Returns:
        List of dicts with at least ``id``, ``source_type``, and ``indexed_at``.
        Column values are included as string fields.  Rows with all-empty cells
        are excluded when ``cfg.skip_empty_rows`` is True.

    Raises:
        RuntimeError: If ``GOOGLE_SERVICE_ACCOUNT_JSON`` is missing or invalid.
        googleapiclient.errors.HttpError: If the API call fails (bad spreadsheet
            id, permission denied, quota exceeded, etc.).
    """
    loop = asyncio.get_event_loop()
    # Run the sync Google API call in a thread pool to avoid blocking the loop.
    return await loop.run_in_executor(None, _fetch_sync, cfg)
