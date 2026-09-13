"""Shared runner for one catalog fixture; no work occurs when imported."""

from __future__ import annotations

import copy
from typing import Any

from common import (
    HarnessError,
    TelemetryMonitor,
    chat_request,
    collect_rank_logs,
    existing_receipt,
    fixture_hash,
    is_timeout_error,
    log_accounting,
    queue_snapshot,
    post_request_pause,
    receipt_identity,
    receipt_path,
    render_salt,
    require_idle,
    request_body,
    telemetry_snapshot,
    utc_text,
    validate_expected,
    write_json_atomic,
)


def run_single_fixture(
    manifest: dict[str, Any],
    catalog_hash: str,
    fixture: dict[str, Any],
    *,
    suite: str,
    phase: str,
    cell: str,
    repetition: int,
    cache_group: str,
    stream: bool,
    timeout_class: str,
) -> dict[str, Any]:
    material = {
        "catalog_sha256": catalog_hash,
        "fixture": fixture,
        "suite": suite,
        "phase": phase,
        "cell": cell,
        "stream": stream,
    }
    identity = receipt_identity(
        manifest, fixture_hash(material), suite, phase, cell, repetition
    )
    path = receipt_path(manifest, suite, phase, cell, repetition)
    previous = existing_receipt(path, identity)
    if previous is not None:
        return previous
    salt = render_salt(
        manifest,
        suite=suite,
        phase=phase,
        cell=cell,
        repetition=repetition,
        cache_group=cache_group,
    )
    body = request_body(manifest, fixture, salt=salt, stream=stream)
    timeout = float(manifest["timeouts"][f"{timeout_class}_seconds"])
    before = require_idle(manifest, label=f"before:{suite}:{phase}:{cell}:{repetition}")
    record: dict[str, Any] = {
        **identity,
        "state": "RUNNING",
        "request": body,
        "request_body_sha256": fixture_hash({"request": body}),
        "fixture_id": fixture["id"],
        "cache_salt_identity": {"value": salt, "cache_group": cache_group},
        "effective_limits": {"max_tokens": body["max_tokens"], "timeout_seconds": timeout},
        "reasoning": copy.deepcopy(body.get("chat_template_kwargs", {})),
        "isolation_before": before,
        "source_api_image_guard_identity": before["telemetry"]["sample"],
        "offered_concurrency": 1,
        "configured_active_concurrency_cap": manifest.get("concurrency", {}).get("max_active_requests"),
        "observed_active_concurrency": None,
        "observed_active_concurrency_reason": "No engine-neutral during-request active gauge; raw telemetry/logs retained.",
        "queued_before": before["queue"]["waiting"],
        "contamination": {"flag": None, "reasons": []},
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
        expected = fixture.get("expected", {})
        allowed = expected.get("allowed_finish_reasons")
        finish_ok = result["finish_reason"] in allowed if allowed else result["finish_reason"] == expected.get("finish_reason", "stop")
        if not result["done"] or not result.get("usage") or not finish_ok:
            raise HarnessError("incomplete response, missing usage, or expected-result/finish validation failed")
        completion = int(result["usage"].get("completion_tokens", 0))
        if expected.get("validator") == "long_output_validator" and completion <= int(
            expected["validator_spec"]["minimum_completion_tokens_exclusive"]
        ):
            record.update(state="INSUFFICIENT_COVERAGE", reason="EARLY_EOS_BEFORE_REQUIRED_OUTPUT_REGION")
        elif not validation["passed"]:
            raise HarnessError("expected-result validator failed")
        else:
            record.update(state="PASSED", reason=None)
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
        if record["contamination"]["flag"] and record["state"] == "PASSED":
            record.update(state="CONTAMINATED", reason="EXTERNAL_TRAFFIC_OR_UNATTRIBUTABLE_INTERVAL")
        if record["state"] == "TIMED_OUT":
            record["timeout_recovery"] = {
                "new_submissions_allowed": False,
                "queue_observed": record.get("queue_after"),
                "cancellation_and_drain_must_be_confirmed_by_campaign_owner": True,
            }
        write_json_atomic(path, record)
    return record
