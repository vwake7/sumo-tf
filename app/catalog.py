"""Explorer catalog from the StackQL Sumo provider: service → resource."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.stackql_client import client, region, sanitize_id

logger = logging.getLogger("sumo_tf.explorer")

SNAPSHOT_PATH = Path("inventory/explorer_snapshot.json")

_schema: list[dict[str, Any]] | None = None
_inventory: dict[str, dict[str, Any]] | None = None


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
    global _schema, _inventory
    _schema = None
    _inventory = None


def _serialize_groups() -> list[dict[str, Any]]:
    groups = schema()[0]["_groups"]
    return [
        {
            "id": group["id"],
            "label": group["label"],
            "title": group["title"],
            "kinds": [asdict(kind) for kind in group["kinds"]],
        }
        for group in groups
    ]


def _restore_schema(groups_data: list[dict[str, Any]]) -> None:
    global _schema
    kinds: dict[str, Kind] = {}
    groups: list[dict[str, Any]] = []
    for group in groups_data:
        children: list[Kind] = []
        for raw in group.get("kinds") or []:
            kind = Kind(
                id=str(raw["id"]),
                label=str(raw["label"]),
                service=str(raw["service"]),
                resource=str(raw["resource"]),
                table=str(raw["table"]),
            )
            children.append(kind)
            kinds[kind.id] = kind
        groups.append(
            {
                "id": group["id"],
                "label": group["label"],
                "title": group.get("title") or group["id"],
                "kinds": children,
            }
        )
    _schema = [{"_kinds": kinds, "_groups": groups}]


def load_snapshot() -> dict[str, Any] | None:
    if not SNAPSHOT_PATH.is_file():
        return None
    try:
        data = json.loads(SNAPSHOT_PATH.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read snapshot %s: %s", SNAPSHOT_PATH, exc)
        return None
    if not isinstance(data, dict):
        return None
    return data


def save_snapshot(inventory: dict[str, dict[str, Any]]) -> None:
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "region": region(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "groups": _serialize_groups(),
        "inventory": inventory,
    }
    tmp = SNAPSHOT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, default=str))
    tmp.replace(SNAPSHOT_PATH)
    logger.info("Wrote snapshot with %s resource(s) to %s", len(inventory), SNAPSHOT_PATH)


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
        "preloaded": _inventory is not None,
        "groups": [
            {
                "id": group["id"],
                "label": group["label"],
                "title": group["title"],
                "kinds": [
                    {
                        "id": kind.id,
                        "label": kind.label,
                        "table": kind.table,
                        **_nav_counts(kind.id),
                    }
                    for kind in group["kinds"]
                ],
            }
            for group in groups
        ],
    }


def _nav_counts(kind_id: str) -> dict[str, int]:
    if _inventory is None or kind_id not in _inventory:
        return {}
    listed = _inventory[kind_id]
    return {
        "count": int(listed.get("count") or 0),
        "get_count": int(listed.get("get_count") or 0),
    }


def _identity(row: dict[str, Any]) -> str:
    return _str(row, "id", "field_id", "playbook_id", "event_id", "uuid")


def _get_tables(kind: Kind) -> list[str]:
    tables = [kind.table]
    if kind.resource == "search":
        sibling = f"sumologic.{kind.service}.{kind.service}"
        if sibling not in tables:
            tables.append(sibling)
        if kind.service == "monitors":
            tables.append("sumologic.monitors.monitors")
    return tables


def _get_queries(kind: Kind, object_id: str) -> list[str]:
    loc = region()
    safe = sanitize_id(object_id)
    queries: list[str] = []
    for table in _get_tables(kind):
        queries.append(f"SELECT * FROM {table} WHERE region = '{loc}' AND id = '{safe}'")
        queries.append(
            f"SELECT * FROM {table} WHERE region = '{loc}' AND field_id = '{safe}'"
        )
        queries.append(f"SELECT * FROM {table} WHERE region = '{loc}' AND uuid = '{safe}'")
    return queries


def fetch_detail(
    kind: Kind, list_row: dict[str, Any]
) -> tuple[str, dict[str, Any], str]:
    """Prefer a StackQL GET-by-id row; fall back to the list row."""
    object_id = _identity(list_row)
    if not object_id:
        return "", list_row, "list"
    for query in _get_queries(kind, object_id):
        try:
            rows = client.execute(query)
        except (RuntimeError, ValueError):
            continue
        if len(rows) == 1:
            return query, rows[0], "get"
    return "", list_row, "list"


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
    if _inventory is not None and kind_id in _inventory:
        return _inventory[kind_id]
    return _list_kind_live(kind_id)


def _list_kind_live(kind_id: str) -> dict[str, Any]:
    kind = get_kind(kind_id)
    query, rows = execute_kind(kind)
    items = []
    get_count = 0
    for row in rows:
        object_id = row_id(row)
        if not object_id:
            continue
        detail_query, detail, source = fetch_detail(kind, row)
        if source == "get":
            get_count += 1
        items.append(
            {
                "id": object_id,
                "label": row_label(detail),
                "source": source,
                "query": detail_query or query,
                "object": detail,
            }
        )
    items.sort(key=lambda item: item["label"].lower())
    return {
        "kind": kind.id,
        "label": f"{kind.service}.{kind.resource}",
        "table": kind.table,
        "query": query,
        "count": len(items),
        "get_count": get_count,
        "items": items,
    }


def get_object(kind_id: str, object_id: str) -> dict[str, Any]:
    kind = get_kind(kind_id)
    wanted = sanitize_id(object_id)
    listed = list_kind(kind_id)
    for item in listed["items"]:
        if item["id"] == wanted:
            return {
                "kind": kind.id,
                "label": item["label"],
                "table": kind.table,
                "query": item["query"],
                "source": item["source"],
                "object": item["object"],
            }
    raise KeyError(f"{kind.service}.{kind.resource} {wanted} was not found")


def preload(*, resume: bool = True) -> None:
    """List every resource and GET each object before the server accepts traffic.

    Progress is written to inventory/explorer_snapshot.json after each resource so
    a restart can continue instead of starting over.
    """
    global _inventory
    inventory: dict[str, dict[str, Any]] = {}
    if resume:
        snapshot = load_snapshot()
        if snapshot and snapshot.get("region") == region():
            groups = snapshot.get("groups") or []
            saved = snapshot.get("inventory") or {}
            if groups and isinstance(saved, dict):
                _restore_schema(groups)
                inventory = saved
                logger.info(
                    "Resuming snapshot from %s (%s resource(s) already saved)",
                    snapshot.get("updated_at"),
                    len(inventory),
                )
    kinds = all_kinds()
    total = len(kinds)
    if inventory and len(inventory) >= total:
        _inventory = inventory
        logger.info("Loaded complete snapshot; skipping live Sumo fetch")
        return
    logger.info("Preloading %s StackQL resources for region %s", total, region())
    for index, kind_id in enumerate(kinds, start=1):
        kind = kinds[kind_id]
        if kind_id in inventory:
            logger.info("Preload %s/%s %s (cached)", index, total, kind.table)
            continue
        logger.info("Preload %s/%s %s", index, total, kind.table)
        try:
            inventory[kind_id] = _list_kind_live(kind_id)
            listed = inventory[kind_id]
            logger.info(
                "Preloaded %s: %s object(s), %s GET",
                kind.table,
                listed["count"],
                listed["get_count"],
            )
        except Exception as exc:  # noqa: BLE001 — keep remaining resources loading
            logger.warning("Preload failed for %s: %s", kind.table, exc)
            inventory[kind_id] = {
                "kind": kind.id,
                "label": f"{kind.service}.{kind.resource}",
                "table": kind.table,
                "query": "",
                "count": 0,
                "get_count": 0,
                "items": [],
                "error": str(exc),
            }
        save_snapshot(inventory)
    _inventory = inventory
    save_snapshot(inventory)
    logger.info("Account preload complete")
