#!/usr/bin/env python3
"""Parameterized bounded SSE exact-marker smoke check."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from common import acquire_runner_lock, HarnessError, initial_idle_pair, load_fixtures, load_manifest, require_execution_window
from single_request import run_single_fixture


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--repetition", type=int, default=1)
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    require_execution_window(manifest)
    acquire_runner_lock(manifest)
    catalog, _, catalog_hash = load_fixtures(manifest)
    fixture = catalog["fixtures"]["rapid-exact-marker"]
    initial_idle_pair(manifest)
    record = run_single_fixture(
        manifest,
        catalog_hash,
        fixture,
        suite="rapid",
        phase="stream",
        cell="exact-marker",
        repetition=args.repetition,
        cache_group="rapid-marker",
        stream=True,
        timeout_class="short",
    )
    print(json.dumps({"state": record["state"], "text": record.get("text"), "error": record.get("error")}), flush=True)
    return 0 if record["state"] == "PASSED" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HarnessError as error:
        print(f"HARNESS_ERROR: {error}", file=sys.stderr, flush=True)
        raise SystemExit(2)
