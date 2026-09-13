#!/usr/bin/env python3
"""Run frozen prose/code quality fixtures with manifest-selected plumbing."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
from typing import Any

from common import (
    acquire_runner_lock,
    HarnessError,
    TelemetryMonitor,
    chat_request,
    collect_rank_logs,
    existing_receipt,
    fixture_hash,
    initial_idle_pair,
    is_timeout_error,
    load_fixtures,
    load_manifest,
    log_accounting,
    queue_snapshot,
    post_request_pause,
    receipt_identity,
    receipt_path,
    render_salt,
    require_idle,
    request_body,
    require_execution_window,
    telemetry_snapshot,
    utc_text,
    validate_expected,
    write_json_atomic,
)


def run_quality_cell(
    manifest: dict[str, Any],
    catalog: dict[str, Any],
    catalog_hash: str,
    cell: dict[str, Any],
    repetition: int,
) -> dict[str, Any]:
    suite = "quality"
    phase = "response"
    name = cell["id"]
    fixture = copy.deepcopy(catalog["fixtures"][cell["fixture_id"]])
    cell_material = {"catalog_sha256": catalog_hash, "cell": cell, "fixture": fixture}
    combined_hash = fixture_hash(cell_material)
    identity = receipt_identity(manifest, combined_hash, suite, phase, name, repetition)
    path = receipt_path(manifest, suite, phase, name, repetition)
    previous = existing_receipt(path, identity)
    if previous is not None:
        print(json.dumps({"cell": name, "state": previous["state"], "resumed": True}), flush=True)
        return previous

    salt = render_salt(
        manifest,
        suite=suite,
        phase=phase,
        cell=name,
        repetition=repetition,
        cache_group=f"{name}-isolated",
    )
    stream = bool(cell.get("stream", False))
    body = request_body(manifest, fixture, salt=salt, stream=stream)
    timeout_class = cell.get("timeout_class", "short")
    timeout = float(manifest["timeouts"][f"{timeout_class}_seconds"])
    before = require_idle(manifest, label=f"before:{suite}:{name}:{repetition}")
    record: dict[str, Any] = {
        **identity,
        "state": "RUNNING",
        "request": body,
        "request_body_sha256": fixture_hash({"request": body}),
        "fixture_id": cell["fixture_id"],
        "cache_salt_identity": {"value": salt, "cache_group": f"{name}-isolated"},
        "effective_limits": {"max_tokens": body["max_tokens"], "timeout_seconds": timeout},
        "reasoning": copy.deepcopy(body.get("chat_template_kwargs", {})),
        "isolation_before": before,
        "offered_concurrency": 1,
        "configured_active_concurrency_cap": manifest.get("concurrency", {}).get("max_active_requests"),
        "observed_active_concurrency": None,
        "observed_active_concurrency_reason": "No engine-neutral during-request active gauge; raw telemetry/logs retained.",
        "queued_before": before["queue"]["waiting"],
        "contamination": {"flag": None, "reasons": []},
        "source_api_image_guard_identity": before["telemetry"]["sample"],
    }
    write_json_atomic(path, record)
    started = utc_text()
    monitor = TelemetryMonitor(manifest).start()
    try:
        result = chat_request(manifest, body, timeout_seconds=timeout)
        record.update(result)
        record["rendered_input_ids"] = (
            result["rendered_input_ids"]
            if result["rendered_input_ids"]
            else {"state": "UNAVAILABLE", "reason": "engine response omitted prompt token IDs"}
        )
        validation = validate_expected(fixture, result)
        record["expected_validation"] = validation
        expected_finish = fixture.get("expected", {}).get("finish_reason", "stop")
        if not result["done"] or not result.get("usage"):
            raise HarnessError("incomplete response or missing usage")
        if result["finish_reason"] != expected_finish:
            raise HarnessError(
                f"unexpected finish reason {result['finish_reason']!r}; expected {expected_finish!r}"
            )
        if not validation["passed"]:
            raise HarnessError("transport-level expected validator failed")
        validator_kind = fixture.get("expected", {}).get("validator")
        if validator_kind in {"external_code_validator", "manual_prose_rubric"}:
            record.update(
                state="COMPLETED",
                quality_state="PENDING_FROZEN_VALIDATOR",
                metric="completion_tokens / full HTTP wall time",
            )
        else:
            record.update(state="PASSED", quality_state="PASSED")
    except Exception as error:
        if is_timeout_error(error):
            record.update(state="TIMED_OUT", reason="REQUEST_TIMEOUT", error=repr(error))
        else:
            record.update(state="FAILED", reason="REQUEST_OR_VALIDATION_FAILURE", error=repr(error))
    finally:
        ended = record.get("end_utc", utc_text())
        record["telemetry_during"] = monitor.stop()
        if not record["telemetry_during"]["all_samples_fresh"]:
            record["contamination"]["reasons"].append("measurement_telemetry_stale_or_missing_during_request")
        try:
            post_request_pause(manifest)
            record["telemetry_after"] = telemetry_snapshot(manifest)
            record["queue_after"] = queue_snapshot(manifest)
            if record["queue_after"]["running"] or record["queue_after"]["waiting"]:
                record["contamination"]["reasons"].append("engine_not_drained_or_external_traffic_after_request")
            logs = collect_rank_logs(manifest, since=started, until=ended)
            record["server_logs"] = logs
            record["log_attribution"] = log_accounting(
                manifest, logs,
                response_cached_tokens=record.get("cached_tokens"))
            if record["log_attribution"]["external_traffic_possible"]:
                record["contamination"]["reasons"].append("unexpected_generation_post_count")
        except Exception as observation_error:
            record["observation_error"] = repr(observation_error)
            record["contamination"]["reasons"].append("post_request_observation_failed")
        record["contamination"]["flag"] = bool(record["contamination"]["reasons"])
        if record["contamination"]["flag"] and record["state"] in {"PASSED", "COMPLETED"}:
            record.update(state="CONTAMINATED", reason="EXTERNAL_TRAFFIC_OR_UNATTRIBUTABLE_INTERVAL")
        if record["state"] == "TIMED_OUT":
            record["timeout_recovery"] = {
                "new_submissions_allowed": False,
                "queue_observed": record.get("queue_after"),
                "cancellation_and_drain_must_be_confirmed_by_campaign_owner": True,
            }
        write_json_atomic(path, record)
    print(
        json.dumps(
            {
                "cell": name,
                "state": record["state"],
                "quality_state": record.get("quality_state"),
                "usage": record.get("usage"),
                "elapsed": record.get("elapsed_seconds"),
                "error": record.get("error"),
            }
        ),
        flush=True,
    )
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("cells", nargs="*")
    parser.add_argument("--repetition", type=int, default=1)
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    require_execution_window(manifest)
    acquire_runner_lock(manifest)
    catalog, _, catalog_hash = load_fixtures(manifest)
    cells = catalog["suites"]["quality"]["cells"]
    selected = set(args.cells)
    unknown = selected - {cell["id"] for cell in cells}
    if unknown:
        raise HarnessError(f"unknown quality cells: {sorted(unknown)}")
    initial_idle_pair(manifest)
    for cell in cells:
        if selected and cell["id"] not in selected:
            continue
        result = run_quality_cell(manifest, catalog, catalog_hash, cell, args.repetition)
        if result["state"] not in {"PASSED", "COMPLETED"}:
            return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HarnessError as error:
        print(f"HARNESS_ERROR: {error}", file=sys.stderr, flush=True)
        raise SystemExit(2)
