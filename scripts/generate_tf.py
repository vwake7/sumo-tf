"""Generate Terraform from the StackQL explorer snapshot using the provider schema."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.tfgen import generate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("generated/tf"),
    )
    parser.add_argument(
        "--kind",
        action="append",
        dest="kinds",
        default=[],
        help="StackQL kind id to include in full, e.g. dashboards.dashboards. Repeatable.",
    )
    parser.add_argument(
        "--object",
        action="append",
        dest="objects",
        default=[],
        help="kind:id pair. Repeatable.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    objects = []
    for spec in args.objects:
        kind_id, sep, object_id = spec.partition(":")
        if not sep or not kind_id or not object_id:
            raise SystemExit(f"expected kind:id, got {spec!r}")
        objects.append({"kind": kind_id, "id": object_id})
    report = generate(kinds=args.kinds, objects=objects, output=args.output)
    print(
        json.dumps(
            {
                "output": report["output"],
                "selected": report["selected"],
                "expanded": report["expanded"],
                "generated": len(report["generated"]),
                "skipped": len(report["skipped"]),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
