"""Read-only probe of StackQL list tables in the current Sumo account."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from pystackql import StackQL

ALREADY_IN_EXPLORER = {
    "sumologic.collectors.collectors",
    "sumologic.ot_collectors.ot_collectors",
    "sumologic.monitors.search",
    "sumologic.users.users",
    "sumologic.roles.roles",
}

SKIP_NAME = re.compile(
    r"(job|result|upgrade|permission|usage|quota|preview|test|facet|aggregate)",
    re.I,
)


def rows(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if isinstance(raw, dict):
        if "error" in raw:
            raise RuntimeError(str(raw.get("error") or raw))
        return [raw]
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if isinstance(item, dict) and "error" in item and len(item) <= 3:
            raise RuntimeError(str(item.get("error") or item))
        if isinstance(item, dict):
            out.append(item)
    return out


def label(row: dict[str, Any]) -> str:
    for key in ("name", "email", "title", "id", "path", "displayName", "display_name"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return "(unnamed)"


def main() -> int:
    region = os.environ.get("SUMOLOGIC_ENVIRONMENT", "us2").lower()
    stackql = StackQL()
    services = rows(stackql.execute("SHOW SERVICES IN sumologic"))
    found: list[tuple[str, int, list[str]]] = []
    empty: list[str] = []
    failed: list[tuple[str, str]] = []

    for service in services:
        svc = service.get("name") or service.get("service")
        if not svc:
            continue
        try:
            resources = rows(stackql.execute(f"SHOW RESOURCES IN sumologic.{svc}"))
        except Exception as exc:  # noqa: BLE001 — probe continues
            failed.append((f"sumologic.{svc}", str(exc)))
            continue
        for resource in resources:
            res = resource.get("name") or resource.get("resource")
            if not res or SKIP_NAME.search(res):
                continue
            table = f"sumologic.{svc}.{res}"
            if table in ALREADY_IN_EXPLORER:
                continue
            queries = [
                f"SELECT * FROM {table} WHERE region = '{region}' LIMIT 20",
                (
                    f"SELECT * FROM {table} WHERE region = '{region}' "
                    "AND include_count = false LIMIT 20"
                ),
            ]
            ok = False
            for query in queries:
                try:
                    result = rows(stackql.execute(query))
                except Exception as exc:  # noqa: BLE001
                    last_err = str(exc)
                    continue
                ok = True
                if not result:
                    empty.append(table)
                    break
                names = [label(row) for row in result[:8]]
                found.append((table, len(result), names))
                break
            if not ok:
                failed.append((table, last_err if "last_err" in dir() else "query failed"))

    print(json.dumps({"region": region, "found": found, "empty": empty, "failed_count": len(failed)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
