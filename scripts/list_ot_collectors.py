"""Fetch Sumo Logic OpenTelemetry collectors and save a JSON snapshot."""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pystackql import StackQL

logger = logging.getLogger("sumo_tf.ot_collectors")

QUERY_TEMPLATE = (
    "SELECT * FROM sumologic.ot_collectors.ot_collectors "
    "WHERE region = '{region}' AND include_count = false LIMIT 10"
)

VALID_REGIONS = {"au", "ca", "ch", "de", "eu", "fed", "in", "jp", "kr", "us1", "us2"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        help="Snapshot path (defaults to inventory/ot_collectors_<UTC timestamp>.json).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Python log verbosity (default: INFO).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    region = os.environ.get("SUMOLOGIC_ENVIRONMENT", "us2").lower()
    if region not in VALID_REGIONS:
        raise SystemExit(f"Unsupported SUMOLOGIC_ENVIRONMENT value: {region!r}")

    query = QUERY_TEMPLATE.format(region=region)
    logger.info("Starting OT collector inventory for Sumo deployment %s", region)
    logger.debug("StackQL query:\n%s", query.strip())
    results: Any = StackQL().execute(query)
    if results is None:
        results = []
    elif not isinstance(results, list):
        results = [results]

    collected_at = datetime.now(timezone.utc).isoformat()
    output_path = args.output or Path("inventory") / (
        "ot_collectors_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json"
    )
    snapshot = {
        "provider": "sumologic",
        "resource": "ot_collectors.ot_collectors",
        "region": region,
        "collected_at": collected_at,
        "query": query.strip(),
        "count": len(results),
        "results": results,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True, default=str) + "\n")

    logger.info("StackQL query completed; %d OT collector(s) returned", len(results))
    logger.info("Snapshot written to %s", output_path)
    print(json.dumps(snapshot, indent=2, sort_keys=True, default=str))
    print(f"\nSaved {len(results)} OT collector(s) to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
