"""Explorer catalog from the StackQL Sumo provider: service → resource."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.stackql_client import client, region, sanitize_id

_schema: list[dict[str, Any]] | None = None


def _str(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    item = row.get("item")
    if isinstance(item, dict):
        for key in keys:
            value = item.get(key)
            if value not in (None, ""):
                return str(value)
    return ""


def row_id(row: dict[str, Any]) -> str:
    value = _str(
        row,
        "id",
        "field_id",
        "playbook_id",
        "event_id",
        "uuid",
        "field_name",
        "type",
        "subdomain",
        "name",
        "title",
    )
    if value:
        return value
    blob = json.dumps(row, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def row_label(row: dict[str, Any]) -> str:
    first = _str(row, "first_name")
    last = _str(row, "last_name")
    full = " ".join(part for part in (first, last) if part)
    return (
        _str(
            row,
            "name",
            "title",
            "field_name",
            "event_name",
            "email",
            "path",
            "type",
            "subdomain",
            "account_owner",
        )
        or full
        or row_id(row)
        or "(unnamed)"
    )


@dataclass(frozen=True)
class Kind:
    id: str
    label: str
    service: str
    resource: str
    table: str


def clear_schema() -> None:
    global _schema
    _schema = None


def schema() -> list[dict[str, Any]]:
    global _schema
    if _schema is not None:
        return _schema
    services = client.execute("SHOW SERVICES IN sumologic")
    groups: list[dict[str, Any]] = []
    kinds: dict[str, Kind] = {}
    for service in sorted(services, key=lambda row: str(row.get("name") or "")):
        name = str(service.get("name") or "")
        if not name:
            continue
        resources = client.execute(f"SHOW RESOURCES IN sumologic.{name}")
        children: list[Kind] = []
        for resource in sorted(resources, key=lambda row: str(row.get("name") or "")):
            res = str(resource.get("name") or "")
            if not res:
                continue
            kind = Kind(
                id=f"{name}.{res}",
                label=res,
                service=name,
                resource=res,
                table=str(resource.get("id") or f"sumologic.{name}.{res}"),
            )
            children.append(kind)
            kinds[kind.id] = kind
        groups.append(
            {
                "id": name,
                "label": name,
                "title": str(service.get("title") or name),
                "kinds": children,
            }
        )
    _schema = [{"_kinds": kinds, "_groups": groups}]
    return _schema


def all_kinds() -> dict[str, Kind]:
    return schema()[0]["_kinds"]


def get_kind(kind_id: str) -> Kind:
    kind = all_kinds().get(kind_id)
    if kind is None:
        raise KeyError(f"Unknown resource {kind_id}")
    return kind


def nav_payload() -> dict[str, Any]:
    groups = schema()[0]["_groups"]
    return {
        "region": region(),
        "read_only": True,
        "layout": "stackql",
        "groups": [
            {
                "id": group["id"],
                "label": group["label"],
                "title": group["title"],
                "kinds": [
                    {"id": kind.id, "label": kind.label, "table": kind.table}
                    for kind in group["kinds"]
                ],
            }
            for group in groups
        ],
    }


def _queries(kind: Kind) -> list[str]:
    loc = region()
    table = kind.table
    attempts = [
        f"SELECT * FROM {table} WHERE region = '{loc}'",
        f"SELECT * FROM {table} WHERE region = '{loc}' AND include_count = false",
    ]
    if kind.resource == "search":
        attempts.extend(
            [
                f"SELECT * FROM {table} WHERE region = '{loc}' AND query = '*'",
                f"SELECT * FROM {table} WHERE region = '{loc}' AND query = 'type:Monitor'",
            ]
        )
    return attempts


def execute_kind(kind: Kind) -> tuple[str, list[dict[str, Any]]]:
    last_error: Exception | None = None
    empty_query = ""
    for query in _queries(kind):
        try:
            rows = client.execute(query)
        except RuntimeError as exc:
            last_error = exc
            continue
        if rows:
            return query, rows
        empty_query = query
    if empty_query:
        return empty_query, []
    raise last_error or RuntimeError(f"No SELECT succeeded for {kind.table}")


def list_kind(kind_id: str) -> dict[str, Any]:
    kind = get_kind(kind_id)
    query, rows = execute_kind(kind)
    items = []
    for row in rows:
        object_id = row_id(row)
        if not object_id:
            continue
        items.append({"id": object_id, "label": row_label(row)})
    items.sort(key=lambda item: item["label"].lower())
    return {
        "kind": kind.id,
        "label": f"{kind.service}.{kind.resource}",
        "table": kind.table,
        "query": query,
        "count": len(items),
        "items": items,
    }


def get_object(kind_id: str, object_id: str) -> dict[str, Any]:
    kind = get_kind(kind_id)
    wanted = sanitize_id(object_id)
    query, rows = execute_kind(kind)
    for row in rows:
        if row_id(row) == wanted:
            return {
                "kind": kind.id,
                "label": row_label(row),
                "table": kind.table,
                "query": query,
                "object": row,
            }
    raise KeyError(f"{kind.service}.{kind.resource} {wanted} was not found")
