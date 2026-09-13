#!/usr/bin/env python3
"""Run the 40 frozen APC cells and their isolated ID companions.

Unlike the historical script, importing this module is inert.  All endpoint,
identity, telemetry, log, salt, and output values come from ``--manifest``.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
import time
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


SUITE = "cache-screen"


def _cell_fixture(catalog: dict[str, Any], cell: dict[str, Any]) -> dict[str, Any]:
    fixture = copy.deepcopy(catalog["fixtures"][cell["fixture_id"]])
    if "expected" in cell:
        fixture["expected"] = copy.deepcopy(cell["expected"])
    return fixture


def run_cell(
    manifest: dict[str, Any],
    catalog: dict[str, Any],
    catalog_hash: str,
    cell: dict[str, Any],
    *,
    phase: str,
    repetition: int,
) -> dict[str, Any]:
    name = cell["id"]
    fixture = _cell_fixture(catalog, cell)
    combined_hash = fixture_hash(
        {
            "catalog_sha256": catalog_hash,
            "cell": cell,
            "fixture": fixture,
            "phase": phase,
        }
    )
    identity = receipt_identity(manifest, combined_hash, SUITE, phase, name, repetition)
    path = receipt_path(manifest, SUITE, phase, name, repetition)
    previous = existing_receipt(path, identity)
    if previous is not None:
        print(json.dumps({"cell": name, "phase": phase, "state": previous["state"], "resumed": True}), flush=True)
        return previous

    cache_group = cell["cache_group"]
    salt = render_salt(
        manifest,
        suite=SUITE,
        phase=phase,
        cell=name,
        repetition=repetition,
        cache_group=cache_group,
    )
    stream = phase == "stream"
    body = request_body(manifest, fixture, salt=salt, stream=stream, ids_phase=phase == "ids")
    before = require_idle(manifest, label=f"before:{SUITE}:{phase}:{name}:{repetition}")
    record: dict[str, Any] = {
        **identity,
        "state": "RUNNING",
        "reason": None,
        "request": body,
        "request_body_sha256": fixture_hash({"request": body}),
        "fixture_id": cell["fixture_id"],
        "historical_label": cell.get("historical_label", name),
        "cache_salt_identity": {
            "value": salt,
            "cache_group": cache_group,
            "control_kind": cell["control_kind"],
            "historical_off_reinterpreted": bool(cell.get("historical_off_reinterpreted", False)),
        },
        "effective_limits": {"max_tokens": body["max_tokens"], "timeout_seconds": manifest["timeouts"]["short_seconds"]},
        "reasoning": copy.deepcopy(body.get("chat_template_kwargs", {})),
        "isolation_before": before,
        "offered_concurrency": 1,
        "configured_active_concurrency_cap": manifest.get("concurrency", {}).get("max_active_requests"),
        "observed_active_concurrency": None,
        "observed_active_concurrency_reason": "No engine-neutral during-request active gauge; raw telemetry/logs retained.",
        "queued_before": before["queue"]["waiting"],
        "rendered_input_ids": {"state": "PENDING_REQUEST"},
        "raw_reply": None,
        "contamination": {"flag": None, "reasons": []},
        "source_api_image_guard_identity": before["telemetry"]["sample"],
    }
    write_json_atomic(path, record)
    request_started = utc_text()
    monitor = TelemetryMonitor(manifest).start()
    try:
        result = chat_request(
            manifest,
            body,
            timeout_seconds=float(manifest["timeouts"]["short_seconds"]),
        )
        record.update(result)
        record["rendered_input_ids"] = (
            result["rendered_input_ids"]
            if result["rendered_input_ids"]
            else {"state": "UNAVAILABLE", "reason": "engine response omitted prompt token IDs"}
        )
        validation = validate_expected(fixture, result)
        record["expected_validation"] = validation
        expected_finish = fixture.get("expected", {}).get("finish_reason")
        finish_ok = expected_finish is None or result["finish_reason"] == expected_finish
        record["finish_reason_validation"] = {
            "expected": expected_finish,
            "actual": result["finish_reason"],
            "passed": finish_ok,
        }
        if phase == "ids":
            ids_ok = isinstance(record["rendered_input_ids"], list) and bool(record["output_token_ids"])
            record["id_companion_validation"] = {
                "passed": ids_ok,
                "reason": None if ids_ok else "ID companion response omitted input or output IDs",
            }
        else:
            ids_ok = True
        if not result["done"]:
            raise HarnessError("stream ended without [DONE]")
        if not result.get("usage"):
            raise HarnessError("response omitted usage")
        if not (validation["passed"] and finish_ok and ids_ok):
            raise HarnessError("functional, finish-reason, or ID-companion validation failed")
        record["state"] = "PASSED"
    except Exception as error:
        if is_timeout_error(error):
            record.update(state="TIMED_OUT", reason="REQUEST_TIMEOUT", error=repr(error))
        else:
            record.update(state="FAILED", reason="REQUEST_OR_VALIDATION_FAILURE", error=repr(error))
    finally:
        request_ended = record.get("end_utc", utc_text())
        record["telemetry_during"] = monitor.stop()
        if not record["telemetry_during"]["all_samples_fresh"]:
            record["contamination"]["reasons"].append("measurement_telemetry_stale_or_missing_during_request")
        try:
            monitoring_delay = float(manifest.get("isolation", {}).get("post_request_monitor_seconds", 0.0))
            if monitoring_delay:
                time.sleep(monitoring_delay)
            record["telemetry_after"] = telemetry_snapshot(manifest)
            record["queue_after"] = queue_snapshot(manifest)
            if record["queue_after"]["running"] or record["queue_after"]["waiting"]:
                record["contamination"]["reasons"].append("engine_not_drained_or_external_traffic_after_request")
            logs = collect_rank_logs(manifest, since=request_started, until=request_ended)
            record["server_logs"] = logs
            accounting = log_accounting(
                manifest, logs,
                response_cached_tokens=record.get("cached_tokens"))
            record["log_attribution"] = accounting
            if accounting["external_traffic_possible"]:
                record["contamination"]["reasons"].append("unexpected_generation_post_count")
        except Exception as observation_error:
            record["observation_error"] = repr(observation_error)
            record["contamination"]["reasons"].append("post_request_observation_failed")
        record["contamination"]["flag"] = bool(record["contamination"]["reasons"])
        if record["contamination"]["flag"] and record["state"] == "PASSED":
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
                "phase": phase,
                "state": record["state"],
                "ttft": record.get("first_visible_content_or_tool_delta_seconds"),
                "elapsed": record.get("elapsed_seconds"),
                "usage": record.get("usage"),
                "contaminated": record["contamination"]["flag"],
                "error": record.get("error"),
            }
        ),
        flush=True,
    )
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--phase", choices=("stream", "ids", "both"), default="both")
    parser.add_argument("--cell", action="append", help="run only this frozen cell; may be repeated")
    parser.add_argument("--repetition", type=int, default=1)
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    require_execution_window(manifest)
    acquire_runner_lock(manifest)
    catalog, _, catalog_hash = load_fixtures(manifest)
    suite = catalog["suites"]["cache_screen"]
    cells = suite["cells"]
    selected = set(args.cell or [])
    unknown = selected - {cell["id"] for cell in cells}
    if unknown:
        raise HarnessError(f"unknown cache-screen cells: {sorted(unknown)}")
    phases = ("stream", "ids") if args.phase == "both" else (args.phase,)
    if "ids" in phases:
        id_fields = manifest.get("engine_adapter", {}).get("request_fields", {}).get("id_companion")
        if not isinstance(id_fields, dict) or id_fields.get("return_token_ids") is not True:
            raise HarnessError(
                "ID companion phase requires engine_adapter.request_fields.id_companion.return_token_ids=true"
            )
    # The canonical order is the PLAN.json order frozen into FIXTURES.json.
    initial_idle_pair(manifest)
    for phase in phases:
        for cell in cells:
            if selected and cell["id"] not in selected:
                continue
            result = run_cell(
                manifest,
                catalog,
                catalog_hash,
                cell,
                phase=phase,
                repetition=args.repetition,
            )
            if result["state"] != "PASSED":
                return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HarnessError as error:
        print(f"HARNESS_ERROR: {error}", file=sys.stderr, flush=True)
        raise SystemExit(2)
