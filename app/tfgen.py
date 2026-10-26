"""Generate Terraform HCL from the StackQL explorer snapshot and the provider schema.

Resource attributes are taken from `terraform providers schema -json` and from
each object's GET JSON. Nothing about a Sumo resource's fields is hardcoded.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.catalog import inventory as catalog_inventory
from app.catalog import row_label

logger = logging.getLogger("sumo_tf.tfgen")

OUTPUT_DIR = Path("generated/tf")
SCHEMA_CACHE = Path("generated/terraform_schema.json")
SCHEMA_WORK = Path("generated/tf_schema")
PROVIDER_SOURCE = "SumoLogic/sumologic"
PROVIDER_VERSION = "~> 3.3.4"
NULLISH = {"", "null", "None", "none"}
SKIP_ATTRS = {"id", "region"}
ID_KEYS = {"id", "uuid", "content_id"}


def generate(
    *,
    kinds: list[str] | None = None,
    objects: list[dict[str, str]] | None = None,
    output: Path | None = None,
) -> dict[str, Any]:
    inventory = catalog_inventory()
    if not inventory:
        raise RuntimeError("No StackQL snapshot is loaded. Refresh from Sumo first.")
    selected = _resolve_selection(inventory, kinds or [], objects or [])
    if not selected:
        raise ValueError("Select at least one resource in the explorer.")
    expanded = _expand_dependencies(inventory, selected)
    schemas = _sumologic_resource_schemas()
    used_names: dict[str, set[str]] = {}
    written: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    imports: list[str] = []
    out = output or OUTPUT_DIR
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "versions.tf").write_text(_versions_tf())

    for kind_id, object_id in sorted(expanded):
        listed = inventory.get(kind_id) or {}
        item = _find_item(listed, object_id)
        if item is None:
            skipped.append({"kind": kind_id, "id": object_id, "reason": "not in snapshot"})
            continue
        obj = item.get("object")
        if not isinstance(obj, dict):
            skipped.append({"kind": kind_id, "id": object_id, "reason": "no GET JSON"})
            continue
        tf_type, schema, score = _pick_tf_resource(kind_id, obj, schemas)
        if tf_type is None or schema is None:
            skipped.append(
                {
                    "kind": kind_id,
                    "id": object_id,
                    "reason": "no Terraform resource in the provider schema matches this StackQL table",
                }
            )
            continue
        body, missing = _emit_block(schema.get("block") or {}, obj, indent=1)
        if missing:
            skipped.append(
                {
                    "kind": kind_id,
                    "id": object_id,
                    "terraform": tf_type,
                    "reason": f"snapshot is missing required Terraform fields: {missing}",
                }
            )
            continue
        loc = _local_name(
            row_label(obj) or object_id,
            object_id,
            used_names.setdefault(tf_type, set()),
        )
        rel = Path(kind_id.replace(".", "/")) / f"{loc}.tf"
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f'resource "{tf_type}" "{loc}" {{\n{body}\n}}\n'
        )
        imports.append(
            f'import {{\n  to = {tf_type}.{loc}\n  id = "{_escape(object_id)}"\n}}\n'
        )
        written.append(
            {
                "kind": kind_id,
                "id": object_id,
                "address": f"{tf_type}.{loc}",
                "file": str(rel),
                "match_score": score,
            }
        )

    (out / "imports.tf").write_text("\n".join(imports) + ("\n" if imports else ""))
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "output": str(out),
        "selected": len(selected),
        "expanded": len(expanded),
        "generated": written,
        "skipped": skipped,
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def _versions_tf() -> str:
    return (
        "terraform {\n"
        "  required_providers {\n"
        "    sumologic = {\n"
        f'      source  = "{PROVIDER_SOURCE}"\n'
        f'      version = "{PROVIDER_VERSION}"\n'
        "    }\n"
        "  }\n"
        "}\n\n"
        'provider "sumologic" {}\n'
    )


def _resolve_selection(
    inventory: dict[str, dict[str, Any]],
    kinds: list[str],
    objects: list[dict[str, str]],
) -> set[tuple[str, str]]:
    selected: set[tuple[str, str]] = set()
    kind_set = {k for k in kinds if k}
    for kind_id in kind_set:
        listed = inventory.get(kind_id) or {}
        for item in listed.get("items") or []:
            oid = str(item.get("id") or "")
            if oid:
                selected.add((kind_id, oid))
    for spec in objects:
        kind_id = str(spec.get("kind") or "")
        oid = str(spec.get("id") or "")
        if kind_id in kind_set:
            continue
        if kind_id and oid:
            selected.add((kind_id, oid))
    return selected


def _find_item(listed: dict[str, Any], object_id: str) -> dict[str, Any] | None:
    for item in listed.get("items") or []:
        if str(item.get("id") or "") == object_id:
            return item
    return None


def _index_inventory(
    inventory: dict[str, dict[str, Any]],
) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    index: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for kind_id, listed in inventory.items():
        for item in listed.get("items") or []:
            oid = str(item.get("id") or "")
            if not oid:
                continue
            index.setdefault(oid, []).append((kind_id, item))
    return index


def _is_id_key(key: str) -> bool:
    k = key.lower().replace("-", "_")
    if k in ID_KEYS:
        return True
    return k.endswith("_id") or k.endswith("_ids")


def _walk_ref_ids(value: Any, skip: str | None) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if _is_id_key(str(key)):
                if isinstance(child, list):
                    for entry in child:
                        if isinstance(entry, (str, int)) and str(entry) != skip:
                            found.add(str(entry))
                        else:
                            found.update(_walk_ref_ids(entry, skip))
                elif isinstance(child, (str, int)):
                    sid = str(child)
                    if sid and sid != skip:
                        found.add(sid)
                else:
                    found.update(_walk_ref_ids(child, skip))
            else:
                found.update(_walk_ref_ids(child, skip))
    elif isinstance(value, list):
        for entry in value:
            found.update(_walk_ref_ids(entry, skip))
    return found


def _expand_dependencies(
    inventory: dict[str, dict[str, Any]],
    selected: set[tuple[str, str]],
) -> set[tuple[str, str]]:
    index = _index_inventory(inventory)
    expanded = set(selected)
    changed = True
    while changed:
        changed = False
        selected_ids = {oid for _, oid in expanded}
        for kind_id, object_id in list(expanded):
            item = _find_item(inventory.get(kind_id) or {}, object_id)
            obj = item.get("object") if item else None
            if not isinstance(obj, dict):
                continue
            for ref in _walk_ref_ids(obj, object_id):
                for ref_kind, _item in index.get(ref, []):
                    pair = (ref_kind, ref)
                    if pair not in expanded:
                        expanded.add(pair)
                        changed = True
        for kind_id, listed in inventory.items():
            for item in listed.get("items") or []:
                oid = str(item.get("id") or "")
                pair = (kind_id, oid)
                if not oid or pair in expanded:
                    continue
                obj = item.get("object")
                if not isinstance(obj, dict):
                    continue
                refs = _walk_ref_ids(obj, oid)
                if refs & selected_ids:
                    expanded.add(pair)
                    changed = True
        selected_ids = {oid for _, oid in expanded}
    return expanded


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _singular(value: str) -> str:
    if value.endswith("ies") and len(value) > 4:
        return value[:-3] + "y"
    if value.endswith("sses"):
        return value[:-2]
    if value.endswith("s") and not value.endswith("ss") and len(value) > 3:
        return value[:-1]
    return value


def _to_snake(name: str) -> str:
    text = str(name).replace("-", "_")
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", text)
    return re.sub(r"_+", "_", text).strip("_").lower()


def _camel(name: str) -> str:
    parts = _to_snake(name).split("_")
    if not parts:
        return name
    return parts[0] + "".join(p.title() for p in parts[1:])


def _plural(value: str) -> str:
    if value.endswith("y") and len(value) > 1 and value[-2] not in "aeiou":
        return value[:-1] + "ies"
    if value.endswith("s"):
        return value
    return value + "s"


def _key_candidates(name: str) -> list[str]:
    snake = _to_snake(name)
    camel = _camel(snake)
    singular = _singular(snake)
    plural = _plural(snake)
    out: list[str] = []
    for key in (snake, camel, singular, plural, _camel(singular), _camel(plural), name):
        if key and key not in out:
            out.append(key)
    return out


def _lookup(obj: dict[str, Any], name: str) -> Any:
    keys = {k.lower(): k for k in obj}
    for cand in _key_candidates(name):
        if cand in obj:
            return obj[cand]
        real = keys.get(cand.lower())
        if real is not None:
            return obj[real]
    return None


def _has_key(obj: dict[str, Any], name: str) -> bool:
    keys = {k.lower() for k in obj}
    return any(c.lower() in keys for c in _key_candidates(name))


def _name_score(kind_id: str, tf_type: str) -> int:
    service, _, resource = kind_id.partition(".")
    rest = tf_type.removeprefix("sumologic_")
    rest_n = _norm(rest)
    rest_s = _norm(_singular(rest))
    res_n = _norm(resource)
    res_s = _norm(_singular(resource))
    svc_n = _norm(service)
    svc_s = _norm(_singular(service))
    if rest_n in {res_n, res_s} or rest_s in {res_n, res_s}:
        return 100
    if res_s and (res_s in rest_n or rest_n.endswith(res_s) or rest_s in res_n):
        return 80
    if rest_n in {svc_n, svc_s} or rest_s in {svc_n, svc_s}:
        return 40
    return 0


def _schema_key_set(block: dict[str, Any]) -> set[str]:
    keys = set()
    for name in (block.get("attributes") or {}):
        keys.add(_norm(name))
    for name in (block.get("block_types") or {}):
        keys.add(_norm(name))
        keys.add(_norm(_singular(name)))
    return keys


def _json_key_set(obj: dict[str, Any]) -> set[str]:
    return {_norm(str(k)) for k in obj}


def _required_missing(block: dict[str, Any], obj: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for name, spec in (block.get("attributes") or {}).items():
        if name in SKIP_ATTRS:
            continue
        if spec.get("required") and _lookup(obj, name) is None:
            if _type_kind(spec.get("type")) == "string":
                continue
            missing.append(name)
    for name, spec in (block.get("block_types") or {}).items():
        if int(spec.get("min_items") or 0) < 1:
            continue
        if _lookup(obj, name) is None and not any(
            _match_nested_block(tag, block.get("block_types") or {})
            for tag in _type_tags(obj)
        ):
            missing.append(name)
    return missing


def _overlap_score(obj: dict[str, Any], block: dict[str, Any]) -> int:
    jk = _json_key_set(obj)
    sk = _schema_key_set(block)
    shared = len(jk & sk)
    for key in list(jk):
        if _singular(key) in sk or key + "s" in sk:
            shared += 1
    missing = _required_missing(block, obj)
    return shared * 10 - len(missing) * 40


def _pick_tf_resource(
    kind_id: str,
    obj: dict[str, Any],
    schemas: dict[str, dict[str, Any]],
) -> tuple[str | None, dict[str, Any] | None, int]:
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for tf_type, schema in schemas.items():
        nscore = _name_score(kind_id, tf_type)
        if nscore <= 0:
            continue
        block = schema.get("block") or {}
        score = nscore * 10 + _overlap_score(obj, block)
        if _required_missing(block, obj):
            score -= 200
        ranked.append((score, tf_type, schema))
    if not ranked:
        return None, None, 0
    ranked.sort(reverse=True)
    score, tf_type, schema = ranked[0]
    if score < 500:
        return None, None, score
    return tf_type, schema, score


def _sumologic_resource_schemas() -> dict[str, dict[str, Any]]:
    payload = _load_tf_schema()
    providers = payload.get("provider_schemas") or {}
    chosen = None
    for name, schema in providers.items():
        resources = schema.get("resource_schemas") or {}
        if any(key.startswith("sumologic_") for key in resources):
            chosen = resources
            break
        if "sumologic" in str(name).lower() and resources:
            chosen = resources
            break
    if not chosen:
        raise RuntimeError("Terraform provider schema did not include Sumo Logic resources.")
    return chosen


def _load_tf_schema() -> dict[str, Any]:
    if SCHEMA_CACHE.is_file():
        try:
            data = json.loads(SCHEMA_CACHE.read_text())
            if data.get("provider_schemas"):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    SCHEMA_WORK.mkdir(parents=True, exist_ok=True)
    (SCHEMA_WORK / "versions.tf").write_text(_versions_tf())
    init = subprocess.run(
        ["terraform", "init", "-backend=false", "-input=false", "-no-color"],
        cwd=SCHEMA_WORK,
        capture_output=True,
        text=True,
        check=False,
    )
    if init.returncode != 0:
        raise RuntimeError(
            "Could not initialize the Sumo Logic Terraform provider to read its schema. "
            f"{(init.stderr or init.stdout).strip()[-800:]}"
        )
    dumped = subprocess.run(
        ["terraform", "providers", "schema", "-json"],
        cwd=SCHEMA_WORK,
        capture_output=True,
        text=True,
        check=False,
    )
    if dumped.returncode != 0:
        raise RuntimeError(
            "Could not read Terraform provider schema. "
            f"{(dumped.stderr or dumped.stdout).strip()[-800:]}"
        )
    data = json.loads(dumped.stdout)
    SCHEMA_CACHE.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_CACHE.write_text(json.dumps(data))
    return data


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


def _is_type_key(key: str) -> bool:
    name = _to_snake(key)
    return name == "type" or name.endswith("_type")


def _type_tags(obj: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    for key, value in obj.items():
        if _is_type_key(str(key)) and isinstance(value, str) and value.strip():
            tags.append(value.strip())
    return tags


def _match_nested_block(tag: str, blocks: dict[str, Any]) -> str | None:
    snake = _to_snake(tag)
    if snake in blocks:
        return snake
    matches = [
        name
        for name in blocks
        if snake == name or snake.startswith(f"{name}_")
    ]
    if matches:
        return max(matches, key=len)
    return None


def _expand_map_blocks(value: Any, spec: dict[str, Any]) -> Any:
    block = spec.get("block") or {}
    attrs = block.get("attributes") or {}
    if not isinstance(value, dict):
        return value
    if "label" in attrs and "values" in attrs and "label" not in value:
        data = value.get("data") if isinstance(value.get("data"), dict) else value
        if isinstance(data, dict) and all(isinstance(v, list) for v in data.values()):
            return [{"label": key, "values": vals} for key, vals in data.items()]
    return value


def _emit_block(block: dict[str, Any], obj: Any, indent: int) -> tuple[str, list[str]]:
    if not isinstance(obj, dict):
        return "", ["<not an object>"]
    obj = {k: coerce(v) for k, v in obj.items()}
    pad = "  " * indent
    lines: list[str] = []
    missing: list[str] = []
    attrs = block.get("attributes") or {}
    blocks = block.get("block_types") or {}
    consumed: set[str] = set()

    for name, spec in attrs.items():
        if name in SKIP_ATTRS:
            continue
        if spec.get("computed") and not spec.get("optional") and not spec.get("required"):
            continue
        raw = _lookup(obj, name)
        if raw is None:
            if spec.get("required") and _type_kind(spec.get("type")) == "string":
                lines.append(f'{pad}{name} = ""')
                continue
            if spec.get("required"):
                missing.append(name)
            continue
        rendered = _hcl_attr(raw, spec.get("type"), indent)
        if rendered is None:
            if spec.get("required"):
                missing.append(name)
            continue
        lines.append(f"{pad}{name} = {rendered}")
        consumed.add(name)

    used_blocks: set[str] = set()
    for name, spec in blocks.items():
        raw = _lookup(obj, name)
        if raw is None:
            continue
        used_blocks.add(name)
        lines.extend(_emit_nested(name, spec, raw, indent))

    for tag in _type_tags(obj):
        nested_name = _match_nested_block(tag, blocks)
        if nested_name and nested_name not in used_blocks:
            inner = {
                k: v
                for k, v in obj.items()
                if not _is_type_key(str(k)) and _to_snake(str(k)) not in used_blocks
            }
            lines.extend(_emit_nested(nested_name, blocks[nested_name], inner, indent))
            used_blocks.add(nested_name)

    for name, spec in blocks.items():
        if int(spec.get("min_items") or 0) >= 1 and name not in used_blocks:
            # Discriminator may have filled a sibling; only flag if none of the
            # required nested groups were written.
            if not used_blocks:
                missing.append(name)

    return "\n".join(lines), missing


def _emit_nested(name: str, spec: dict[str, Any], raw: Any, indent: int) -> list[str]:
    pad = "  " * indent
    raw = _expand_map_blocks(raw, spec)
    nesting = spec.get("nesting_mode") or "list"
    inner_block = spec.get("block") or {}
    items: list[Any]
    if nesting == "single":
        items = [raw]
    elif isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict) and nesting == "map":
        lines = []
        for key, value in raw.items():
            body, _missing = _emit_block(inner_block, value if isinstance(value, dict) else {"value": value}, indent + 1)
            open_block = f'{pad}{name} "{_escape(str(key))}" {{'
            if body:
                lines.append(f"{open_block}\n{body}\n{pad}}}")
            else:
                lines.append(f"{open_block}\n{pad}}}")
        return lines
    else:
        items = [raw]
    lines = []
    for item in items:
        if item is None:
            continue
        payload = item
        if not isinstance(payload, dict):
            continue
        body, _missing = _emit_block(inner_block, payload, indent + 1)
        if body:
            lines.append(f"{pad}{name} {{\n{body}\n{pad}}}")
        else:
            child_types = inner_block.get("block_types") or {}
            for tag in _type_tags(payload):
                child_name = _match_nested_block(tag, child_types)
                if not child_name:
                    continue
                child_body, _m = _emit_block(
                    (child_types[child_name].get("block") or {}),
                    {
                        k: v
                        for k, v in payload.items()
                        if not _is_type_key(str(k))
                    },
                    indent + 2,
                )
                if child_body:
                    lines.append(
                        f"{pad}{name} {{\n{pad}  {child_name} {{\n{child_body}\n{pad}  }}\n{pad}}}"
                    )
                    break
    return lines


def _hcl_attr(value: Any, tf_type: Any, indent: int) -> str | None:
    value = coerce(value)
    if value is None:
        return None
    kind = _type_kind(tf_type)
    if kind == "string":
        if isinstance(value, (dict, list)):
            text = json.dumps(value, indent=2)
            return _hcl_string(text)
        return _hcl_string(str(value))
    if kind == "bool":
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, str) and value.lower() in {"true", "false"}:
            return value.lower()
        return None
    if kind in {"number", "int"}:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            text = value.strip()
            if re.fullmatch(r"-?\d+", text):
                return text
            if re.fullmatch(r"-?\d+\.\d+", text):
                return text
        return None
    if kind == "list":
        if not isinstance(value, list):
            value = [value]
        inner_type = None
        if isinstance(tf_type, list) and len(tf_type) > 1:
            inner_type = tf_type[1]
        parts = []
        for item in value:
            rendered = _hcl_attr(item, inner_type, indent)
            if rendered is not None:
                parts.append(rendered)
        return "[" + ", ".join(parts) + "]"
    if kind == "map" or kind == "object":
        if not isinstance(value, dict):
            return _hcl_string(str(value))
        pad = "  " * indent
        inner = "  " * (indent + 1)
        lines = []
        for key, child in value.items():
            rendered = _hcl_attr(child, None, indent + 1)
            if rendered is None:
                continue
            lines.append(f'{inner}"{_escape(str(key))}" = {rendered}')
        return "{\n" + "\n".join(lines) + f"\n{pad}}}"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        return _hcl_attr(value, ["list", "string"], indent)
    if isinstance(value, dict):
        return _hcl_attr(value, ["map", "string"], indent)
    return _hcl_string(str(value))


def _type_kind(tf_type: Any) -> str | None:
    if isinstance(tf_type, str):
        return tf_type
    if isinstance(tf_type, list) and tf_type:
        return str(tf_type[0])
    return None


def _hcl_string(value: str) -> str:
    if "\n" in value:
        marker = "JSON" if value.lstrip().startswith("{") else "EOT"
        while f"\n{marker}\n" in f"\n{value}\n":
            marker += "X"
        return f"<<-{marker}\n{value}\n{marker}"
    return f'"{_escape(value)}"'


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _local_name(name: str, remote_id: str, used: set[str]) -> str:
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
