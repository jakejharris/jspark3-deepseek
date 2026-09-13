#!/usr/bin/env python3
"""Parameterized three-repetition published greedy C1 speed sample."""

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
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    require_execution_window(manifest)
    acquire_runner_lock(manifest)
    catalog, _, catalog_hash = load_fixtures(manifest)
    fixture = catalog["fixtures"][catalog["suites"]["published_c1"]["fixture_id"]]
    initial_idle_pair(manifest)
    for repetition in range(1, int(catalog["suites"]["published_c1"]["repetitions"]) + 1):
        record = run_single_fixture(
            manifest,
            catalog_hash,
            fixture,
            suite="published-c1",
            phase="response",
            cell="greedy-story",
            repetition=repetition,
            cache_group="published-c1-repeat",
            stream=False,
            timeout_class="short",
        )
        print(
            json.dumps(
                {
                    "state": record["state"],
                    "sample": repetition,
                    "elapsed_seconds": record.get("elapsed_seconds"),
                    "complete_answer_tokens_per_http_second": record.get("complete_answer_tokens_per_http_second"),
                    "scope": "300-token speed cap; length is intentional truncation, not completed-story quality",
                }
            ),
            flush=True,
        )
        if record["state"] != "PASSED":
            return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HarnessError as error:
        print(f"HARNESS_ERROR: {error}", file=sys.stderr, flush=True)
        raise SystemExit(2)
