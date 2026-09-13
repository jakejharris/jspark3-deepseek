#!/usr/bin/env python3
"""Parameterized single-cell APC probe over the frozen campaign fixtures."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from common import acquire_runner_lock, HarnessError, initial_idle_pair, load_fixtures, load_manifest, require_execution_window, sha256_file
from screen import run_cell


def verify_frozen(manifest: dict) -> None:
    catalog, catalog_path, _ = load_fixtures(manifest)
    failures = []
    for item in catalog["source_artifacts"]:
        source = Path(item["path"])
        if not source.is_absolute():
            source = (catalog_path.parent / source).resolve()
        actual = sha256_file(source) if source.exists() else None
        if actual != item["sha256"]:
            failures.append({"path": str(source), "expected": item["sha256"], "actual": actual})
    if failures:
        raise HarnessError(f"frozen APC/source bytes changed: {failures}")
    print(f"verified {len(catalog['source_artifacts'])} frozen source artifacts", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("action", choices=("verify-frozen", "run"))
    parser.add_argument("--cell")
    parser.add_argument("--phase", choices=("stream", "ids"), default="stream")
    parser.add_argument("--repetition", type=int, default=1)
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    if args.action == "verify-frozen":
        verify_frozen(manifest)
        return 0
    require_execution_window(manifest)
    acquire_runner_lock(manifest)
    if not args.cell:
        raise HarnessError("run requires --cell")
    catalog, _, catalog_hash = load_fixtures(manifest)
    cells = catalog["suites"]["cache_screen"]["cells"]
    matches = [cell for cell in cells if cell["id"] == args.cell]
    if len(matches) != 1:
        raise HarnessError(f"unknown or duplicate cache-screen cell: {args.cell!r}")
    initial_idle_pair(manifest)
    result = run_cell(
        manifest,
        catalog,
        catalog_hash,
        matches[0],
        phase=args.phase,
        repetition=args.repetition,
    )
    return 0 if result["state"] == "PASSED" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HarnessError as error:
        print(f"HARNESS_ERROR: {error}", file=sys.stderr, flush=True)
        raise SystemExit(2)
