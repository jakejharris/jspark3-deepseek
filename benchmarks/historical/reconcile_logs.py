#!/usr/bin/env python3
"""Reconcile immutable request receipts with per-rank timestamp-bounded logs."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import sys
from common import (
    acquire_runner_lock,
    HarnessError,
    collect_rank_logs,
    load_manifest,
    log_accounting,
    object_sha256,
    output_root,
    read_json,
    utc_text,
    write_json_atomic,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--suite", default="cache-screen")
    parser.add_argument("--phase", default="stream")
    parser.add_argument("--fetch-missing", action="store_true")
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    acquire_runner_lock(manifest)
    source = output_root(manifest) / "REQUESTS" / args.suite / args.phase
    paths = sorted(source.glob("*.json"))
    if not paths:
        raise HarnessError(f"no receipts found under {source}")
    rows = []
    compressed_lines = []
    for path in paths:
        receipt = read_json(path)
        if receipt.get("manifest_sha256") != manifest["_manifest_sha256"]:
            raise HarnessError(f"mixed manifest receipt: {path}")
        if not receipt.get("start_utc") or not receipt.get("end_utc"):
            raise HarnessError(f"receipt lacks exact UTC interval: {path}")
        logs = receipt.get("server_logs")
        if not logs:
            if not args.fetch_missing:
                raise HarnessError(f"receipt lacks stored server logs: {path}; use --fetch-missing explicitly")
            logs = collect_rank_logs(manifest, since=receipt["start_utc"], until=receipt["end_utc"])
        accounting = log_accounting(manifest, logs)
        rows.append(
            {
                "receipt": str(path),
                "receipt_sha256": object_sha256(receipt),
                "cell": receipt["cell"],
                "repetition": receipt["repetition"],
                "state": receipt["state"],
                "start_utc": receipt["start_utc"],
                "end_utc": receipt["end_utc"],
                "accounting": accounting,
                "contamination_flag_in_receipt": receipt.get("contamination", {}).get("flag"),
            }
        )
        for entry in logs:
            compressed_lines.append(
                json.dumps(
                    {
                        "receipt": str(path),
                        "rank": entry["rank"],
                        "host": entry["host"],
                        "cid": entry["cid"],
                        "started_at": entry["started_at"],
                        "log_lower_bound": entry["log_lower_bound"],
                        "stdout": entry["stdout"],
                    },
                    ensure_ascii=False,
                )
            )
    accounting = {
        "schema_version": 1,
        "created_at": utc_text(),
        "campaign_id": manifest["campaign_id"],
        "run_id": manifest["run_id"],
        "manifest_sha256": manifest["_manifest_sha256"],
        "suite": args.suite,
        "phase": args.phase,
        "clock_attribution": {
            "method": manifest.get("logging", {}).get("clock_skew_method"),
            "controller_minus_rank_clock_seconds": manifest.get("logging", {}).get(
                "controller_minus_rank_clock_seconds"
            ),
            "lead_margin_seconds": manifest.get("logging", {}).get("lead_margin_seconds"),
            "tail_margin_seconds": manifest.get("logging", {}).get("tail_margin_seconds"),
            "fixed_historical_150ms_assumption_used": False,
        },
        "rows": rows,
        "all_intervals_attributable": all(
            not row["accounting"]["external_traffic_possible"] for row in rows
        ),
    }
    target = output_root(manifest) / "ARTIFACTS" / "log-accounting" / f"{args.suite}-{args.phase}.json"
    archive = target.with_suffix(".logs.jsonl.gz")
    if target.exists() or archive.exists():
        raise HarnessError(f"refusing to overwrite prior reconciliation artifact: {target} / {archive}")
    write_json_atomic(target, accounting)
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(gzip.compress(("\n".join(compressed_lines) + "\n").encode()))
    print(
        json.dumps(
            {
                "rows": len(rows),
                "all_intervals_attributable": accounting["all_intervals_attributable"],
                "artifact": str(target),
            }
        ),
        flush=True,
    )
    return 0 if accounting["all_intervals_attributable"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HarnessError as error:
        print(f"HARNESS_ERROR: {error}", file=sys.stderr, flush=True)
        raise SystemExit(2)
