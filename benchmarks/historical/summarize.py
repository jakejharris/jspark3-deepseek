#!/usr/bin/env python3
"""Offline summary of hash-bound cache-screen receipts."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
from typing import Any

from common import (
    acquire_runner_lock,
    HarnessError,
    load_fixtures,
    load_manifest,
    output_root,
    read_json,
    resolve_path,
    utc_text,
    write_json_atomic,
)


def _receipt(manifest: dict[str, Any], phase: str, label: str, repetition: int) -> dict[str, Any]:
    path = output_root(manifest) / "REQUESTS" / "cache-screen" / phase / f"{label}-{repetition}.json"
    if not path.exists():
        raise HarnessError(f"missing cache-screen receipt: {path}")
    value = read_json(path)
    if value.get("manifest_sha256") != manifest["_manifest_sha256"]:
        raise HarnessError(f"mixed manifest receipt: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--repetition", type=int, default=1)
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    acquire_runner_lock(manifest)
    catalog, _, catalog_hash = load_fixtures(manifest)
    cells = catalog["suites"]["cache_screen"]["cells"]
    baseline_rows = {}
    baseline_path = manifest.get("comparators", {}).get("serve31_summary_path")
    if baseline_path:
        path = resolve_path(Path(manifest["_manifest_path"]), baseline_path)
        baseline = read_json(path)
        baseline_rows = {row["label"]: row for row in baseline["rows"]}
    rows = []
    for cell in cells:
        label = cell["id"]
        stream = _receipt(manifest, "stream", label, args.repetition)
        ids = _receipt(manifest, "ids", label, args.repetition)
        usage = stream.get("usage") or {}
        row = {
            "label": label,
            "fixture_id": cell["fixture_id"],
            "control_kind": cell["control_kind"],
            "historical_off_reinterpreted": cell["historical_off_reinterpreted"],
            "state": stream["state"],
            "id_companion_state": ids["state"],
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "time_to_first_model_output_seconds": min(
                [
                    value
                    for value in (
                        stream.get("first_nonempty_reasoning_delta_seconds"),
                        stream.get("first_visible_content_or_tool_delta_seconds"),
                    )
                    if value is not None
                ],
                default=None,
            ),
            "time_to_first_visible_answer_seconds": stream.get("first_visible_content_or_tool_delta_seconds"),
            "http_wall_seconds": stream.get("elapsed_seconds"),
            "complete_answer_tokens_per_http_second": stream.get("complete_answer_tokens_per_http_second"),
            "visible_delta_to_done_seconds": stream.get("visible_delta_to_done_seconds"),
            "prefix_hit_tokens": stream.get("log_attribution", {}).get("cached_tokens"),
            "rendered_input_ids": ids.get("rendered_input_ids"),
            "output_token_ids": ids.get("output_token_ids"),
            "finish_reason": stream.get("finish_reason"),
            "functional_validation": stream.get("expected_validation"),
            "offered_concurrency": stream.get("offered_concurrency"),
            "active_concurrency_cap": stream.get("configured_active_concurrency_cap"),
            "queued_before": stream.get("queued_before"),
            "contamination": stream.get("contamination"),
            "telemetry_before": stream.get("isolation_before", {}).get("telemetry"),
            "telemetry_after": stream.get("telemetry_after"),
            "server_queue_seconds": None,
            "server_prefill_seconds": None,
            "server_decode_seconds": None,
            "server_timer_note": "NA unless exposed directly by the selected engine adapter",
            "serve31_historical": baseline_rows.get(label),
        }
        rows.append(row)
    summary = {
        "schema_version": 1,
        "created_at": utc_text(),
        "campaign_id": manifest["campaign_id"],
        "run_id": manifest["run_id"],
        "manifest_sha256": manifest["_manifest_sha256"],
        "fixtures_sha256": catalog_hash,
        "state": "COMPLETED" if all(row["state"] == row["id_companion_state"] == "PASSED" for row in rows) else "INCOMPLETE_OR_FAILED",
        "stream_requests": len(rows),
        "id_companion_requests": len(rows),
        "rows": rows,
        "method_limits": [
            "Six OFF labels are isolated-salt controls on an ON engine; no genuine OFF boot is implied.",
            "Streaming and ID-companion namespaces are isolated because SGLang streaming exposes no token IDs.",
            "Missing engine timers are NA and are not reconstructed from token counts.",
            "No historical guard file is a readiness or freshness source; receipts use <15s measurement telemetry.",
            "Offered, active-cap, and queued concurrency are separate fields.",
        ],
    }
    artifact_root = output_root(manifest) / "ARTIFACTS"
    target = artifact_root / "CACHE-SCREEN-SUMMARY.json"
    csv_path = artifact_root / "CACHE-SCREEN-SIDE-BY-SIDE.csv"
    if target.exists() or csv_path.exists():
        raise HarnessError("refusing to overwrite an existing cache-screen summary")
    write_json_atomic(target, summary)
    artifact_root.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow([
            "label", "state", "prompt_tokens", "completion_tokens", "ttft_visible_s",
            "wall_s", "complete_tok_s", "prefix_hit_tokens", "offered", "active_cap",
            "queued_before", "contaminated", "historical_off_reinterpreted",
        ])
        for row in rows:
            writer.writerow([
                row["label"], row["state"], row["prompt_tokens"], row["completion_tokens"],
                row["time_to_first_visible_answer_seconds"], row["http_wall_seconds"],
                row["complete_answer_tokens_per_http_second"], row["prefix_hit_tokens"],
                row["offered_concurrency"], row["active_concurrency_cap"], row["queued_before"],
                row["contamination"]["flag"], row["historical_off_reinterpreted"],
            ])
    print(json.dumps({"state": summary["state"], "rows": len(rows), "artifact": str(target)}), flush=True)
    return 0 if summary["state"] == "COMPLETED" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HarnessError as error:
        print(f"HARNESS_ERROR: {error}", file=sys.stderr, flush=True)
        raise SystemExit(2)
