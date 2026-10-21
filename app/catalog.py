"""Verified read-only resource groups for the explorer nav."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.stackql_client import client, region, sanitize_id

RowFilter = Callable[[dict[str, Any]], bool]
RowId = Callable[[dict[str, Any]], str]
RowLabel = Callable[[dict[str, Any]], str]


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


def _id(row: dict[str, Any]) -> str:
    return _str(row, "id")


def _name(row: dict[str, Any]) -> str:
    return _str(row, "name", "email", "path") or _id(row) or "(unnamed)"


def _collector_type(expected: str) -> RowFilter:
    def matches(row: dict[str, Any]) -> bool:
        return _str(row, "collector_type").lower() == expected.lower()

    return matches


def _user_label(row: dict[str, Any]) -> str:
    first = _str(row, "first_name")
    last = _str(row, "last_name")
    email = _str(row, "email")
    full = " ".join(part for part in (first, last) if part)
    return full or email or _id(row)


@dataclass(frozen=True)
class Kind:
    id: str
    label: str
    table: str
    query: str
    row_id: RowId = _id
    row_label: RowLabel = _name
    matches: RowFilter | None = None


@dataclass(frozen=True)
class Group:
    id: str
    label: str
    children: tuple[Kind, ...]


KINDS: dict[str, Kind] = {
    "hosted_collector": Kind(
        id="hosted_collector",
        label="Hosted",
        table="sumologic.collectors.collectors",
        query=(
            "SELECT * FROM sumologic.collectors.collectors "
            "WHERE region = '{region}'"
        ),
        matches=_collector_type("Hosted"),
    ),
    "installed_collector": Kind(
        id="installed_collector",
        label="Installed",
        table="sumologic.collectors.collectors",
        query=(
            "SELECT * FROM sumologic.collectors.collectors "
            "WHERE region = '{region}'"
        ),
        matches=_collector_type("Installable"),
    ),
    "ot_collector": Kind(
        id="ot_collector",
        label="OpenTelemetry",
        table="sumologic.ot_collectors.ot_collectors",
        query=(
            "SELECT * FROM sumologic.ot_collectors.ot_collectors "
            "WHERE region = '{region}' AND include_count = false"
        ),
    ),
    "monitor": Kind(
        id="monitor",
        label="Monitors",
        table="sumologic.monitors.search",
        query=(
            "SELECT * FROM sumologic.monitors.search "
            "WHERE region = '{region}' AND query = 'type:Monitor'"
        ),
    ),
    "monitor_folder": Kind(
        id="monitor_folder",
        label="Folders",
        table="sumologic.monitors.search",
        query=(
            "SELECT * FROM sumologic.monitors.search "
            "WHERE region = '{region}' AND query = 'type:folder'"
        ),
    ),
    "user": Kind(
        id="user",
        label="Users",
        table="sumologic.users.users",
        query="SELECT * FROM sumologic.users.users WHERE region = '{region}'",
        row_label=_user_label,
    ),
    "role": Kind(
        id="role",
        label="Roles",
        table="sumologic.roles.roles",
        query="SELECT * FROM sumologic.roles.roles WHERE region = '{region}'",
    ),
}

NAV: tuple[Group, ...] = (
    Group(
        id="collectors",
        label="Collectors",
        children=(
            KINDS["hosted_collector"],
            KINDS["installed_collector"],
            KINDS["ot_collector"],
        ),
    ),
    Group(
        id="monitors",
        label="Monitors",
        children=(KINDS["monitor"], KINDS["monitor_folder"]),
    ),
    Group(
        id="access",
        label="Access",
        children=(KINDS["user"], KINDS["role"]),
    ),
)


def nav_payload() -> dict[str, Any]:
    return {
        "region": region(),
        "read_only": True,
        "groups": [
            {
                "id": group.id,
                "label": group.label,
                "kinds": [
                    {"id": kind.id, "label": kind.label, "table": kind.table}
                    for kind in group.children
                ],
            }
            for group in NAV
        ],
    }


def list_kind(kind_id: str) -> dict[str, Any]:
    kind = KINDS[kind_id]
    query = kind.query.format(region=region())
    rows = client.execute(query)
    if kind.matches is not None:
        rows = [row for row in rows if kind.matches(row)]
    items = []
    for row in rows:
        object_id = kind.row_id(row)
        if not object_id:
            continue
        items.append({"id": object_id, "label": kind.row_label(row)})
    items.sort(key=lambda item: item["label"].lower())
    return {
        "kind": kind.id,
        "label": kind.label,
        "table": kind.table,
        "query": query,
        "count": len(items),
        "items": items,
    }


def get_object(kind_id: str, object_id: str) -> dict[str, Any]:
    kind = KINDS[kind_id]
    wanted = sanitize_id(object_id)
    query = kind.query.format(region=region())
    rows = client.execute(query)
    for row in rows:
        if kind.matches is not None and not kind.matches(row):
            continue
        if kind.row_id(row) == wanted:
            return {
                "kind": kind.id,
                "label": kind.row_label(row),
                "table": kind.table,
                "query": query,
                "object": row,
            }
    raise KeyError(f"{kind.label} {wanted} was not found")
