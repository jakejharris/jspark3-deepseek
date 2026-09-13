#!/usr/bin/env python3
"""Run one frozen real-Pi fixture through manifest-provided installed-Pi argv."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from common import (
    acquire_runner_lock,
    HarnessError,
    TelemetryMonitor,
    collect_rank_logs,
    existing_receipt,
    fixture_hash,
    initial_idle_pair,
    load_fixtures,
    load_manifest,
    log_accounting,
    queue_snapshot,
    post_request_pause,
    receipt_identity,
    receipt_path,
    require_idle,
    require_execution_window,
    telemetry_snapshot,
    utc_text,
    write_json_atomic,
)


def _format_argv(template: list[Any], values: dict[str, Any]) -> list[str]:
    try:
        return [str(part).format_map(values) for part in template]
    except (KeyError, ValueError) as error:
        raise HarnessError(f"invalid Pi argv template: {error}") from error


def _redact_pi_result(value: dict[str, Any]) -> dict[str, Any]:
    """Keep metrics/identity while excluding prompts, answers, and private file contents."""

    allowed = {
        "state",
        "fixture_sha256",
        "case",
        "elapsed_seconds",
        "returncode",
        "event_log",
        "errors",
        "assistant_messages",
        "tools",
        "timings",
        "stderr_tail",
        "final_characters",
        "exact_marker_match",
        "source_session_unchanged",
        "request_count",
        "input_rendering",
        "output_cap",
        "reasoning_setting",
    }
    return {key: value[key] for key in allowed if key in value}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--cell", default="pi-hidden-marker-two-read")
    parser.add_argument("--repetition", type=int, default=1)
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    require_execution_window(manifest)
    acquire_runner_lock(manifest)
    catalog, catalog_path, catalog_hash = load_fixtures(manifest)
    cells = catalog["suites"]["real_pi"]["cells"]
    matches = [cell for cell in cells if cell["id"] == args.cell]
    if len(matches) != 1:
        raise HarnessError(f"unknown or duplicate real-Pi cell: {args.cell!r}")
    cell = matches[0]
    fixture = catalog["fixtures"][cell["fixture_id"]]
    exact_fixture_hash = fixture_hash(fixture)
    combined_hash = fixture_hash(
        {"catalog_sha256": catalog_hash, "cell": cell, "frozen_fixture_sha256": exact_fixture_hash}
    )
    identity = receipt_identity(
        manifest, combined_hash, "real-pi", "installed-provider", args.cell, args.repetition
    )
    path = receipt_path(manifest, "real-pi", "installed-provider", args.cell, args.repetition)
    previous = existing_receipt(path, identity)
    if previous is not None:
        print(json.dumps({"cell": args.cell, "state": previous["state"], "resumed": True}), flush=True)
        return 0 if previous["state"] == "PASSED" else 1

    pi = manifest.get("pi")
    if not isinstance(pi, dict):
        raise HarnessError("manifest.pi is required")
    for key in ("command_argv_template", "provider", "model", "profile_identity"):
        if key not in pi:
            raise HarnessError(f"manifest.pi missing {key!r}")
    profile = pi["profile_identity"]
    if profile.get("base_url") != manifest["base_url"] or profile.get("model_id") != manifest["model_id"]:
        raise HarnessError("Pi profile identity does not match manifest base URL/model ID")
    timeout_class = cell.get("timeout_class", "long")
    timeout = float(manifest["timeouts"][f"{timeout_class}_seconds"])
    values = {
        "campaign_id": manifest["campaign_id"],
        "run_id": manifest["run_id"],
        "base_url": manifest["base_url"],
        "model_id": manifest["model_id"],
        "provider": pi["provider"],
        "pi_model": pi["model"],
        "cell": args.cell,
        "rep": args.repetition,
        "fixture_catalog": str(catalog_path),
        "fixture_id": cell["fixture_id"],
        "fixture_sha256": exact_fixture_hash,
        "output_root": manifest["_output_root"],
    }
    argv = _format_argv(pi["command_argv_template"], values)
    before_pair = initial_idle_pair(manifest)
    before = require_idle(manifest, label=f"before:real-pi:{args.cell}:{args.repetition}")
    record: dict[str, Any] = {
        **identity,
        "state": "RUNNING",
        "fixture_id": cell["fixture_id"],
        "pi_fixture_sha256": exact_fixture_hash,
        "pi_profile_identity": profile,
        "command_argv": argv,
        "command_stdin": None,
        "private_content_policy": "Pi prompts/answers/file contents remain in the manifest-declared private evidence location",
        "effective_limits": {
            "timeout_seconds": timeout,
            "output_cap": fixture["application_request"]["max_tokens"],
            "reasoning": fixture["application_request"].get("reasoning_setting"),
        },
        "idle_snapshots": before_pair,
        "isolation_before": before,
        "offered_concurrency": 1,
        "configured_active_concurrency_cap": manifest.get("concurrency", {}).get("max_active_requests"),
        "observed_active_concurrency": None,
        "observed_active_concurrency_reason": "Installed Pi may issue serial tool turns; exact request count comes from logs and Pi receipt.",
        "queued_before": before["queue"]["waiting"],
        "source_api_image_guard_identity": before["telemetry"]["sample"],
        "contamination": {"flag": None, "reasons": []},
    }
    write_json_atomic(path, record)
    monitor = TelemetryMonitor(manifest).start()
    start_mono = time.monotonic()
    record.update(start_monotonic=start_mono, start_utc=utc_text())
    write_json_atomic(path, record)
    try:
        completed = subprocess.run(argv, text=True, capture_output=True, timeout=timeout, check=False)
        record.update(
            end_utc=utc_text(),
            end_monotonic=time.monotonic(),
            command_returncode=completed.returncode,
            raw_stdout_sha256=hashlib.sha256(completed.stdout.encode()).hexdigest(),
            stderr_tail=completed.stderr[-3000:],
        )
        record["elapsed_seconds"] = record["end_monotonic"] - start_mono
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise HarnessError(f"Pi runner stdout is not one JSON receipt: {error}") from error
        record["pi_result"] = _redact_pi_result(result)
        timings = result.get("timings") or []
        record["first_pi_event_seconds"] = min(
            (float(item["t"]) for item in timings if "t" in item), default=None
        )
        record["first_nonempty_reasoning_delta_seconds"] = min(
            (
                float(item["t"])
                for item in timings
                if item.get("delta_type") == "thinking_delta" and "t" in item
            ),
            default=None,
        )
        record["first_visible_content_or_tool_delta_seconds"] = min(
            (
                float(item["t"])
                for item in timings
                if item.get("delta_type") in {"text_delta", "toolcall_delta"} and "t" in item
            ),
            default=None,
        )
        record["done_seconds"] = max(
            (
                float(item["t"])
                for item in timings
                if item.get("type") == "message_end" and "t" in item
            ),
            default=None,
        )
        record["rendered_input_ids"] = result.get(
            "input_rendering",
            {"state": "UNAVAILABLE", "reason": "installed Pi runner did not expose rendered IDs"},
        )
        record["actual_request_body"] = {
            "state": "PRIVATE",
            "fixture_sha256": exact_fixture_hash,
            "private_event_log": result.get("event_log"),
        }
        if result.get("fixture_sha256") != exact_fixture_hash:
            raise HarnessError("Pi runner did not attest the exact frozen fixture hash")
        if completed.returncode or result.get("state") != "PASSED":
            raise HarnessError(
                f"Pi runner failed: returncode={completed.returncode}, state={result.get('state')!r}"
            )
        if result.get("output_cap") != fixture["application_request"]["max_tokens"]:
            raise HarnessError("Pi runner effective output cap differs from frozen fixture")
        if result.get("reasoning_setting") != fixture["application_request"].get("reasoning_setting"):
            raise HarnessError("Pi runner effective reasoning setting differs from frozen fixture")
        expected_requests = int(cell["expected_model_requests"])
        if int(result.get("request_count", -1)) != expected_requests:
            raise HarnessError("Pi model-request count differs from the frozen cell contract")
        if "preserve_argv_template" in pi:
            preserve_values = dict(values)
            preserve_values["event_log"] = str(result.get("event_log", ""))
            preserved = subprocess.run(
                _format_argv(pi["preserve_argv_template"], preserve_values),
                text=True,
                capture_output=True,
                check=False,
            )
            if preserved.returncode:
                raise HarnessError(f"private Pi evidence preservation failed: {preserved.stderr[-1000:]}")
            record["preserved_private_evidence"] = preserved.stdout.strip()
        record["state"] = "PASSED"
    except subprocess.TimeoutExpired as error:
        record.update(
            state="TIMED_OUT",
            reason="PI_TIMEOUT",
            error=repr(error),
            end_utc=utc_text(),
            end_monotonic=time.monotonic(),
        )
        record["elapsed_seconds"] = record["end_monotonic"] - start_mono
    except Exception as error:
        record.update(
            state="FAILED",
            reason="PI_OR_VALIDATION_FAILURE",
            error=repr(error),
            end_utc=record.get("end_utc", utc_text()),
            end_monotonic=record.get("end_monotonic", time.monotonic()),
        )
        record["elapsed_seconds"] = record.get(
            "elapsed_seconds", record["end_monotonic"] - start_mono
        )
    finally:
        record["telemetry_during"] = monitor.stop()
        if not record["telemetry_during"]["all_samples_fresh"]:
            record["contamination"]["reasons"].append("measurement_telemetry_stale_or_missing_during_pi")
        try:
            post_request_pause(manifest)
            record["telemetry_after"] = telemetry_snapshot(manifest)
            record["queue_after"] = queue_snapshot(manifest)
            if record["queue_after"]["running"] or record["queue_after"]["waiting"]:
                record["contamination"]["reasons"].append("engine_not_drained_or_external_traffic_after_pi")
            logs = collect_rank_logs(manifest, since=record["start_utc"], until=record["end_utc"])
            record["server_logs"] = logs
            record["log_attribution"] = log_accounting(manifest, logs)
            expected_posts = int(cell["expected_model_requests"])
            actual_posts = record["log_attribution"]["chat_posts"]
            if actual_posts != expected_posts:
                record["contamination"]["reasons"].append(
                    f"expected_{expected_posts}_chat_posts_observed_{actual_posts}"
                )
        except Exception as observation_error:
            record["observation_error"] = repr(observation_error)
            record["contamination"]["reasons"].append("post_pi_observation_failed")
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
                "cell": args.cell,
                "state": record["state"],
                "elapsed": record.get("elapsed_seconds"),
                "error": record.get("error"),
            }
        ),
        flush=True,
    )
    return 0 if record["state"] == "PASSED" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HarnessError as error:
        print(f"HARNESS_ERROR: {error}", file=sys.stderr, flush=True)
        raise SystemExit(2)
