#!/usr/bin/env python3
"""Manifest-driven Tony v1 reproduction lane with raw per-request receipts.

The historical decode formula remains available only as the explicitly named
``tony_legacy_decode_metric_tokens_per_second``.  Decision metrics use complete
completion tokens divided by full HTTP wall time.
"""

from __future__ import annotations

import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import threading
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
    post_request_pause,
    receipt_identity,
    receipt_path,
    render_salt,
    require_idle,
    require_execution_window,
    telemetry_snapshot,
    utc_text,
    validate_expected,
    write_json_atomic,
)


SUITE = "tony-reproduction"


def _first_model_output(result: dict[str, Any]) -> float | None:
    values = [
        value
        for value in (
            result.get("first_nonempty_reasoning_delta_seconds"),
            result.get("first_visible_content_or_tool_delta_seconds"),
        )
        if value is not None
    ]
    return min(values) if values else None


def _legacy_decode(result: dict[str, Any]) -> float | None:
    completion = (result.get("usage") or {}).get("completion_tokens")
    first = _first_model_output(result)
    elapsed = result.get("elapsed_seconds")
    if completion is None or completion <= 1 or first is None or elapsed is None or elapsed <= first:
        return None
    return (completion - 1) / (elapsed - first)


def _batch_paths(
    manifest: dict[str, Any], category: str, concurrency: int, repetition: int, phase: str
) -> list[Path]:
    return [
        receipt_path(manifest, SUITE, phase, f"{category}-c{concurrency}-s{slot}", repetition)
        for slot in range(concurrency)
    ]


def run_batch(
    manifest: dict[str, Any],
    catalog_hash: str,
    fixture: dict[str, Any],
    *,
    category: str,
    concurrency: int,
    repetition: int,
    phase: str,
    comparison_tag: str,
    max_tokens_override: int | None = None,
    timeout_class: str = "short",
    tag_prompt: bool = True,
    cache_group: str | None = None,
    stream: bool = True,
    slot_fixtures: list[dict[str, Any]] | None = None,
    arrival_offsets: list[float] | None = None,
) -> dict[str, Any]:
    fixtures = slot_fixtures if slot_fixtures is not None else [fixture] * concurrency
    offsets = arrival_offsets if arrival_offsets is not None else [0.0] * concurrency
    if len(fixtures) != concurrency or len(offsets) != concurrency or any(x < 0 for x in offsets):
        raise HarnessError("one nonnegative arrival offset and fixture required per stream")
    timeout = float(manifest["timeouts"][f"{timeout_class}_seconds"])
    paths = _batch_paths(manifest, category, concurrency, repetition, phase)
    identities = []
    bodies = []
    for slot in range(concurrency):
        fixture = fixtures[slot]
        application = copy.deepcopy(fixture["application_request"])
        if tag_prompt:
            tag = fixture["prompt_tag_template"].format(
                version=fixture["prompt_set_version"], comparison_tag=comparison_tag,
                category=category, offered=concurrency, slot=slot,
            )
            application["messages"][0]["content"] = tag + application["messages"][0]["content"]
        if max_tokens_override is not None:
            application["max_tokens"] = max_tokens_override
        salt = render_salt(
            manifest,
            suite=SUITE,
            phase=phase,
            cell=category,
            repetition=repetition,
            cache_group=f"{cache_group or category}-c{concurrency}-s{slot}-isolated",
        )
        body = application
        body["model"] = manifest["model_id"]
        body["cache_salt"] = salt
        body["stream"] = stream
        if stream:
            body["stream_options"] = {"include_usage": True}
        adapter_phase = "stream" if stream else "id_companion"
        for key, value in manifest.get("engine_adapter", {}).get("request_fields", {}).get(adapter_phase, {}).items():
            body[key] = copy.deepcopy(value)
        material = {
            "catalog_sha256": catalog_hash,
            "fixture": fixture,
            "rendered_application_request": application,
            "phase": phase,
            "concurrency": concurrency,
            "slot": slot,
            "arrival_offset_seconds": offsets[slot],
        }
        identities.append(
            receipt_identity(
                manifest,
                fixture_hash(material),
                SUITE,
                phase,
                f"{category}-c{concurrency}-s{slot}",
                repetition,
            )
        )
        bodies.append(body)

    prior = [existing_receipt(path, identity) for path, identity in zip(paths, identities)]
    if any(item is not None for item in prior):
        if not all(item is not None for item in prior):
            raise HarnessError(
                f"partial existing C{concurrency} {category} batch cannot be resumed without changing offered load"
            )
        states = [item["state"] for item in prior if item]
        print(json.dumps({"phase": phase, "category": category, "c": concurrency, "states": states, "resumed": True}), flush=True)
        return {"state": "PASSED" if all(state == "PASSED" for state in states) else "FAILED", "receipts": prior}

    before = require_idle(manifest, label=f"before:{SUITE}:{phase}:{category}:c{concurrency}")
    barrier = threading.Barrier(concurrency)

    def one(slot: int) -> dict[str, Any]:
        barrier.wait()
        if offsets[slot]:
            time.sleep(offsets[slot])
        started_mono = time.monotonic()
        try:
            return chat_request(
                manifest,
                bodies[slot],
                timeout_seconds=timeout,
            )
        except Exception as error:
            timed_out = is_timeout_error(error)
            request_error = repr(error)
        return {
            "_request_error": request_error,
            "_timed_out": timed_out,
            "start_monotonic": started_mono,
            "start_utc": utc_text(),
            "end_monotonic": time.monotonic(),
            "end_utc": utc_text(),
            "elapsed_seconds": time.monotonic() - started_mono,
            "first_sse_event_seconds": None,
            "first_nonempty_reasoning_delta_seconds": None,
            "first_visible_content_or_tool_delta_seconds": None,
            "first_nonempty_model_output_seconds": None,
            "done_seconds": None,
            "done": False,
            "raw_reply": "",
            "events": [],
            "response": None,
            "text": "",
            "reasoning_text": "",
            "calls": {},
            "output_token_ids": [],
            "rendered_input_ids": [],
            "usage": None,
            "finish_reason": None,
            "complete_answer_tokens_per_http_second": None,
            "visible_delta_to_done_seconds": None,
        }

    batch_start = utc_text()
    monitor = TelemetryMonitor(manifest).start()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        results = list(executor.map(one, range(concurrency)))
    batch_end = utc_text()
    during = monitor.stop()
    contamination_reasons = []
    if not during["all_samples_fresh"]:
        contamination_reasons.append("measurement_telemetry_stale_or_missing_during_batch")
    observation_errors = []
    post_request_pause(manifest)
    try:
        after_telemetry = telemetry_snapshot(manifest)
    except Exception as error:
        after_telemetry = None
        observation_errors.append(repr(error))
        contamination_reasons.append("post_batch_telemetry_failed")
    try:
        after_queue = queue_snapshot(manifest)
        if after_queue["running"] or after_queue["waiting"]:
            contamination_reasons.append("engine_not_drained_or_external_traffic_after_batch")
    except Exception as error:
        after_queue = None
        observation_errors.append(repr(error))
        contamination_reasons.append("post_batch_queue_observation_failed")
    try:
        logs = collect_rank_logs(manifest, since=batch_start, until=batch_end)
        response_hits = [result.get("cached_tokens") for result in results]
        accounting = log_accounting(
            manifest, logs,
            response_cached_tokens=(sum(response_hits)
                                    if all(x is not None for x in response_hits)
                                    else None))
        accounting["expected_chat_posts"] = concurrency
        accounting["external_traffic_possible"] = accounting["chat_posts"] != concurrency
        if accounting["chat_posts"] != concurrency:
            contamination_reasons.append(
                f"expected_{concurrency}_chat_posts_observed_{accounting['chat_posts']}"
            )
    except Exception as error:
        logs = []
        accounting = None
        observation_errors.append(repr(error))
        contamination_reasons.append("batch_log_attribution_failed")
    records = []
    batch_makespan = max(result['end_monotonic'] for result in results) - min(result['start_monotonic'] for result in results)
    for slot, (path, identity, body, result) in enumerate(zip(paths, identities, bodies, results)):
        fixture = fixtures[slot]
        validation = validate_expected(fixture, result)
        state = "PASSED"
        reason = None
        if result.get("_request_error"):
            state, reason = ("TIMED_OUT", "REQUEST_TIMEOUT") if result.get("_timed_out") else ("FAILED", "REQUEST_FAILURE")
        elif not result["done"] or not result.get("usage") or not validation["passed"]:
            state, reason = "FAILED", "INCOMPLETE_OR_INVALID_RESPONSE"
        if result["finish_reason"] not in fixture["expected"].get("allowed_finish_reasons", ["stop", "length"]):
            state, reason = "FAILED", "UNEXPECTED_FINISH_REASON"
        if contamination_reasons and state == "PASSED":
            state, reason = "CONTAMINATED", "EXTERNAL_TRAFFIC_OR_UNATTRIBUTABLE_INTERVAL"
        record = {
            **identity,
            **result,
            "state": state,
            "reason": reason,
            "request": body,
            "request_body_sha256": fixture_hash({"request": body}),
            "cache_salt_identity": {"value": body["cache_salt"], "control_kind": "front_tag_and_isolated_salt"},
            "effective_limits": {"max_tokens": body["max_tokens"], "timeout_seconds": timeout},
            "reasoning": copy.deepcopy(body.get("chat_template_kwargs", {})),
            "fixture_id": fixture["id"],
            "slot": slot,
            "arrival_offset_seconds": offsets[slot],
            "batch_makespan_seconds": batch_makespan,
            "offered_concurrency": concurrency,
            "configured_active_concurrency_cap": manifest.get("concurrency", {}).get("max_active_requests"),
            "maximum_possible_active_concurrency": min(
                concurrency, int(manifest.get("concurrency", {}).get("max_active_requests", concurrency))
            ),
            "observed_active_concurrency": None,
            "observed_active_concurrency_reason": "No engine-neutral per-request active gauge was exposed; preserve batch logs/telemetry and configured cap.",
            "queued_before": before["queue"]["waiting"],
            "isolation_before": before,
            "telemetry_after": after_telemetry,
            "telemetry_during_shared_batch": during,
            "queue_after": after_queue,
            "source_api_image_guard_identity": before["telemetry"]["sample"],
            "server_logs_shared_batch": logs,
            "log_attribution_shared_batch": accounting,
            "observation_errors_shared_batch": observation_errors,
            "contamination": {"flag": bool(contamination_reasons), "reasons": contamination_reasons},
            "expected_validation": validation,
            "tony_legacy_decode_metric_tokens_per_second": _legacy_decode(result),
            "tony_legacy_decode_metric_definition": "(completion_tokens - 1) / (HTTP wall seconds - first nonempty model delta seconds); retained for reproduction only",
            "decision_metric": "complete_answer_tokens_per_http_second",
        }
        if state == "TIMED_OUT":
            record["timeout_recovery"] = {
                "new_submissions_allowed": False,
                "queue_observed": after_queue,
                "cancellation_and_drain_must_be_confirmed_by_campaign_owner": True,
            }
        write_json_atomic(path, record)
        records.append(record)
    states = [record["state"] for record in records]
    print(
        json.dumps(
            {
                "phase": phase,
                "category": category,
                "offered": concurrency,
                "active_cap": manifest.get("concurrency", {}).get("max_active_requests"),
                "states": states,
                "aggregate_complete_tokens_per_wall_second": (
                    sum((record.get("usage") or {}).get("completion_tokens", 0) for record in records)
                    / batch_makespan
                ),
            }
        ),
        flush=True,
    )
    return {"state": "PASSED" if all(state == "PASSED" for state in states) else "FAILED", "receipts": records}


def run_prefill(
    manifest: dict[str, Any], catalog_hash: str, fixture: dict[str, Any], repetition: int
) -> dict[str, Any]:
    target = fixture["target"]
    category = f"prefill-{target}"
    # Materialized prompt bytes and hash are frozen in FIXTURES.json; no live
    # tokenizer is used to rewrite or pad this reproduction input.
    return run_batch(
        manifest,
        catalog_hash,
        fixture,
        category=category,
        concurrency=1,
        repetition=repetition,
        phase="prefill",
        comparison_tag=manifest["tony"]["comparison_tag"],
        timeout_class="long",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--repetition", type=int, default=1)
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    require_execution_window(manifest)
    acquire_runner_lock(manifest)
    catalog, _, catalog_hash = load_fixtures(manifest)
    tony = manifest.get("tony")
    if not isinstance(tony, dict):
        raise HarnessError("manifest.tony is required")
    for key in ("comparison_tag", "levels", "prefill_targets"):
        if key not in tony:
            raise HarnessError(f"manifest.tony missing {key!r}")
    levels = [int(value) for value in tony["levels"]]
    if not levels or any(value < 1 for value in levels):
        raise HarnessError("Tony levels must be positive")
    suite = catalog["suites"]["tony_reproduction"]
    fixtures = catalog["fixtures"]
    categories = [fixtures[fixture_id] for fixture_id in suite["category_fixture_ids"]]
    initial_idle_pair(manifest)
    for fixture_id in suite["warmup_fixture_ids"]:
        fixture = fixtures[fixture_id]
        result = run_batch(
            manifest,
            catalog_hash,
            fixture,
            category=fixture["category"],
            concurrency=1,
            repetition=args.repetition,
            phase="warmup",
            comparison_tag=tony["comparison_tag"],
            max_tokens_override=64,
        )
        if result["state"] != "PASSED":
            return 1
    for concurrency in levels:
        for fixture in categories:
            result = run_batch(
                manifest,
                catalog_hash,
                fixture,
                category=fixture["category"],
                concurrency=concurrency,
                repetition=args.repetition,
                phase="stream",
                comparison_tag=tony["comparison_tag"],
            )
            if result["state"] != "PASSED":
                return 1
    targets = [int(value) for value in tony["prefill_targets"]]
    if targets and manifest.get("gates", {}).get("context_ramp") != "PASSED":
        raise HarnessError("Tony prefill is blocked until manifest.gates.context_ramp is PASSED")
    available = {int(fixtures[item]["target"]): fixtures[item] for item in suite["prefill_fixture_ids"]}
    unknown = set(targets) - set(available)
    if unknown:
        raise HarnessError(f"Tony prefill targets lack frozen materialized fixtures: {sorted(unknown)}")
    for target in targets:
        result = run_prefill(manifest, catalog_hash, available[target], args.repetition)
        if result["state"] != "PASSED":
            return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HarnessError as error:
        print(f"HARNESS_ERROR: {error}", file=sys.stderr, flush=True)
        raise SystemExit(2)
