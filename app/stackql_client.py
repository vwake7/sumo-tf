"""SELECT-only StackQL client. Never issues mutating statements."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Any

from pystackql import StackQL

logger = logging.getLogger("sumo_tf.explorer")

VALID_REGIONS = {"au", "ca", "ch", "de", "eu", "fed", "in", "jp", "kr", "us1", "us2"}
SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
WRITE_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|TRUNCATE|MERGE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


def region() -> str:
    value = os.environ.get("SUMOLOGIC_ENVIRONMENT", "us2").lower()
    if value not in VALID_REGIONS:
        raise ValueError(f"Unsupported SUMOLOGIC_ENVIRONMENT value: {value!r}")
    return value


def require_credentials() -> None:
    if not os.environ.get("SUMOLOGIC_ACCESSID") or not os.environ.get("SUMOLOGIC_ACCESSKEY"):
        raise RuntimeError(
            "Missing SUMOLOGIC_ACCESSID or SUMOLOGIC_ACCESSKEY. "
            "Copy .env.example to .env and restart the web service."
        )


def sanitize_id(value: str) -> str:
    if not SAFE_ID.match(value):
        raise ValueError("Invalid resource id")
    return value


def assert_select(query: str) -> None:
    stripped = query.strip()
    if not stripped.upper().startswith("SELECT"):
        raise ValueError("Only SELECT queries are allowed")
    if WRITE_SQL.search(stripped):
        raise ValueError("Refusing a query that contains a write keyword")


def decode_jsonish(value: Any) -> Any:
    """Parse nested JSON strings so monitor `item` payloads are readable."""
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in "{[" and text[-1:] in "}]":
            try:
                return decode_jsonish(json.loads(text))
            except json.JSONDecodeError:
                return value
        return value
    if isinstance(value, list):
        return [decode_jsonish(item) for item in value]
    if isinstance(value, dict):
        return {key: decode_jsonish(item) for key, item in value.items()}
    return value


class ReadOnlyStackQL:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._client: StackQL | None = None
        self._cache: dict[str, list[dict[str, Any]]] = {}

    def _stackql(self) -> StackQL:
        if self._client is None:
            self._client = StackQL()
        return self._client

    def execute(self, query: str) -> list[dict[str, Any]]:
        assert_select(query)
        require_credentials()
        with self._lock:
            cached = self._cache.get(query)
            if cached is not None:
                return cached
            logger.info("StackQL SELECT: %s", query)
            raw: Any = self._stackql().execute(query)
            rows = _normalize_rows(raw)
            self._cache[query] = rows
            return rows

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


def _normalize_rows(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(raw) from exc
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise RuntimeError(f"Unexpected StackQL result type: {type(raw).__name__}")
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            rows.append({"value": item})
            continue
        if "error" in item and len(item) <= 3:
            raise RuntimeError(str(item.get("error") or item))
        rows.append(decode_jsonish(item))
    return rows


client = ReadOnlyStackQL()
