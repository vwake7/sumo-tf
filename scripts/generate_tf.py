"""Generate Terraform HCL and import blocks from StackQL inventory JSON."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

NULLISH = {"", "null", "None", "none"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inventory",
        type=Path,
        nargs="+",
        required=True,
        help="StackQL inventory JSON snapshot(s).",
    )
    parser.add_argument(
        "--mappings",
        type=Path,
        default=Path("mappings/sumologic.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("generated/tf"),
    )
    return parser.parse_args()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def coerce(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if text in NULLISH:
            return None
        if text in {"true", "false"}:
            return text == "true"
        if (text.startswith("{") and text.endswith("}")) or (
            text.startswith("[") and text.endswith("]")
        ):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return value
        return value
    return value


def hcl_value(value: Any, indent: int = 2) -> str:
    pad = " " * indent
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        inner = ", ".join(hcl_value(v, indent) for v in value)
        return f"[{inner}]"
    if isinstance(value, dict):
        lines = []
        for key in sorted(value):
            lines.append(f'{pad}  "{key}" = {hcl_value(value[key], indent + 2)}')
        return "{\n" + "\n".join(lines) + f"\n{pad}}}"
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def local_name(name: str, remote_id: str, used: set[str]) -> str:
    slug = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_").lower()
    if not slug or not slug[0].isalpha():
        slug = f"obj_{slug or remote_id}"
    candidate = slug
    n = 2
    while candidate in used:
        candidate = f"{slug}_{n}"
        n += 1
    used.add(candidate)
    return candidate


def match_mapping(obj: dict[str, Any], mapping: dict[str, Any]) -> bool:
    for key, expected in (mapping.get("match") or {}).items():
        if str(obj.get(key, "")).lower() != str(expected).lower():
            return False
    return True


def generate(inventories: list[dict[str, Any]], mappings: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    used_names: set[str] = set()
    resources_hcl: list[str] = []
    imports_hcl: list[str] = []
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated": [],
        "skipped": [],
        "unmapped_stackql": [],
    }
    mapped_stackql = {m["stackql"] for m in mappings["resources"]}

    for snapshot in inventories:
        resource = snapshot.get("resource", "")
        if resource not in mapped_stackql:
            report["unmapped_stackql"].append(
                {
                    "resource": resource,
                    "count": snapshot.get("count"),
                    "reason": "no verified mapping",
                }
            )
            continue
        for obj in snapshot.get("results") or []:
            chosen = None
            for mapping in mappings["resources"]:
                if mapping["stackql"] == resource and match_mapping(obj, mapping):
                    chosen = mapping
                    break
            if chosen is None:
                report["skipped"].append(
                    {
                        "resource": resource,
                        "id": obj.get("id"),
                        "reason": "no mapping matched object predicates",
                    }
                )
                continue
            missing = [
                field
                for field in chosen.get("required_inventory") or []
                if coerce(obj.get(field)) is None
            ]
            if missing:
                report["skipped"].append(
                    {
                        "resource": resource,
                        "terraform": chosen["terraform"],
                        "id": obj.get("id"),
                        "reason": f"missing required inventory fields: {missing}",
                    }
                )
                continue
            remote_id = str(obj.get("id") or "")
            if not remote_id:
                report["skipped"].append(
                    {"resource": resource, "reason": "missing id for import"}
                )
                continue
            loc = local_name(str(obj.get("name") or remote_id), remote_id, used_names)
            args: list[str] = []
            for tf_attr, api_field in chosen["fields"].items():
                if tf_attr in (chosen.get("omit") or []):
                    continue
                value = coerce(obj.get(api_field))
                if value is None:
                    continue
                args.append(f"  {tf_attr} = {hcl_value(value)}")
            body = "\n".join(args)
            resources_hcl.append(
                f'resource "{chosen["terraform"]}" "{loc}" {{\n{body}\n}}\n'
            )
            imports_hcl.append(
                f'import {{\n  to = {chosen["terraform"]}.{loc}\n  id = "{remote_id}"\n}}\n'
            )
            report["generated"].append(
                {
                    "address": f"{chosen['terraform']}.{loc}",
                    "import_id": remote_id,
                    "stackql": resource,
                    "dependencies": chosen.get("dependencies") or [],
                }
            )

    provider = (
        'terraform {\n'
        "  required_providers {\n"
        "    sumologic = {\n"
        '      source = "SumoLogic/sumologic"\n'
        "    }\n"
        "  }\n"
        "}\n\n"
        'provider "sumologic" {}\n'
    )
    return provider + "\n" + "\n".join(resources_hcl), "\n".join(imports_hcl), report


def main() -> int:
    args = parse_args()
    mappings = load_json(args.mappings)
    inventories = [load_json(path) for path in args.inventory]
    resources, imports, report = generate(inventories, mappings)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "generated.tf").write_text(resources + "\n")
    (args.output / "imports.tf").write_text(imports + "\n")
    (args.output / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "generated": len(report["generated"]),
                "skipped": len(report["skipped"]),
                "unmapped_stackql": len(report["unmapped_stackql"]),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
