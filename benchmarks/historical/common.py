"""Shared, import-safe support for the campaign benchmark copies.

This module performs no I/O at import time.  Network and subprocess activity is
only reachable from an explicitly invoked harness ``main`` function.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import string
import subprocess
import tempfile
import threading
import time
from typing import Any, Iterable
import urllib.error
import urllib.request


UTC = timezone.utc
SAFE_COMPONENT = re.compile(r"[A-Za-z0-9_.-]+\Z")


class HarnessError(RuntimeError):
    """A contract failure which must stop new benchmark submissions."""


class TelemetryMonitor:
    """Sample the externally refreshed measurement record during a request."""

    def __init__(self, manifest: dict[str, Any]):
        self.manifest = manifest
        configured = float(manifest["measurement_telemetry"].get("monitor_interval_seconds", 2.0))
        if not 0.1 <= configured < float(manifest["measurement_telemetry"]["max_age_seconds"]):
            raise HarnessError("telemetry monitor interval must be >=0.1s and below max_age_seconds")
        self.interval = configured
        self.samples: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _sample(self) -> None:
        try:
            self.samples.append(telemetry_snapshot(self.manifest))
        except Exception as error:
            self.errors.append({"at": utc_text(), "error": repr(error)})

    def _run(self) -> None:
        self._sample()
        while not self._stop.wait(self.interval):
            self._sample()

    def start(self) -> "TelemetryMonitor":
        if self._thread is not None:
            raise HarnessError("telemetry monitor was already started")
        self._thread = threading.Thread(target=self._run, name="benchmark-telemetry", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval * 2))
            if self._thread.is_alive():
                self.errors.append({"at": utc_text(), "error": "telemetry monitor thread did not stop"})
        self._sample()
        return {
            "interval_seconds": self.interval,
            "samples": self.samples,
            "errors": self.errors,
            "all_samples_fresh": not self.errors,
        }


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_text(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat().replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    # Docker emits nanoseconds; Python3.10 accepts at most microseconds.
    # Keep original strings in receipts/log bounds; truncate only for datetime math.
    normalized = re.sub(r"(\.\d{6})\d+(?=Z|[+-]|$)", r"\1", value)
    parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise HarnessError(f"UTC timestamp lacks an offset: {value!r}")
    return parsed.astimezone(UTC)


def is_timeout_error(error: BaseException) -> bool:
    if isinstance(error, (TimeoutError, socket.timeout)):
        return True
    return isinstance(error, urllib.error.URLError) and isinstance(
        getattr(error, "reason", None), (TimeoutError, socket.timeout)
    )


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def object_sha256(value: Any) -> str:
    return sha256_bytes(canonical_bytes(value))


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def resolve_path(manifest_path: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (manifest_path.parent / path).resolve()


def _require(mapping: dict[str, Any], fields: Iterable[str], where: str) -> None:
    missing = [field for field in fields if field not in mapping]
    if missing:
        raise HarnessError(f"{where} missing required fields: {', '.join(missing)}")


def safe_component(value: Any, name: str) -> str:
    text = str(value)
    if not SAFE_COMPONENT.fullmatch(text) or text in {".", ".."}:
        raise HarnessError(f"unsafe {name}: {text!r}")
    return text


def load_manifest(path: Path) -> dict[str, Any]:
    """Load and validate only the benchmark-facing immutable manifest fields."""

    path = path.resolve()
    manifest = read_json(path)
    if not isinstance(manifest, dict):
        raise HarnessError("manifest must be a JSON object")
    _require(
        manifest,
        (
            "schema_version",
            "campaign_id",
            "run_id",
            "base_url",
            "model_id",
            "output_root",
            "exclusive_window_owner",
            "exclusive_window_confirmed",
            "salt_namespace_template",
            "ranks",
            "measurement_telemetry",
            "queue_adapter",
            "api",
            "timeouts",
            "logging",
            "concurrency",
            "isolation",
        ),
        "manifest",
    )
    if manifest["schema_version"] != 1:
        raise HarnessError(f"unsupported manifest schema {manifest['schema_version']!r}")
    safe_component(manifest["campaign_id"], "campaign_id")
    safe_component(manifest["run_id"], "run_id")
    if not isinstance(manifest["base_url"], str) or not manifest["base_url"].strip():
        raise HarnessError("base_url must be a nonempty string")
    if not isinstance(manifest["model_id"], str) or not manifest["model_id"].strip():
        raise HarnessError("model_id must be a nonempty string")
    template = manifest["salt_namespace_template"]
    if not isinstance(template, str):
        raise HarnessError("salt_namespace_template must be a string")
    try:
        salt_fields = {
            field_name
            for _, field_name, _, _ in string.Formatter().parse(template)
            if field_name is not None
        }
    except ValueError as error:
        raise HarnessError(f"invalid salt_namespace_template: {error}") from error
    required_salt_fields = {"campaign_id", "run_id", "suite", "phase", "rep", "cache_group"}
    missing_salt_fields = required_salt_fields - salt_fields
    if missing_salt_fields:
        raise HarnessError(
            "salt_namespace_template lacks isolation fields: "
            + ", ".join(sorted(missing_salt_fields))
        )
    if not isinstance(manifest["exclusive_window_owner"], str) or not manifest["exclusive_window_owner"].strip():
        raise HarnessError("exclusive_window_owner must be a nonempty string")
    if not isinstance(manifest["ranks"], list) or not manifest["ranks"]:
        raise HarnessError("manifest.ranks must be a nonempty list")
    ranks_seen: set[int] = set()
    for rank in manifest["ranks"]:
        _require(rank, ("rank", "host", "cid", "started_at", "log_lower_bound"), "rank")
        if not isinstance(rank["rank"], int) or rank["rank"] in ranks_seen:
            raise HarnessError("rank numbers must be unique integers")
        ranks_seen.add(rank["rank"])
        safe_component(rank["host"], "rank.host")
        if not re.fullmatch(r"[a-f0-9]{12,64}", rank["cid"]):
            raise HarnessError(f"invalid full/prefix CID for rank {rank['rank']}")
        started_at = parse_utc(rank["started_at"])
        log_lower_bound = parse_utc(rank["log_lower_bound"])
        if log_lower_bound < started_at:
            raise HarnessError(
                f"rank {rank['rank']} log_lower_bound precedes the recorded container StartedAt"
            )
    telemetry = manifest["measurement_telemetry"]
    _require(
        telemetry,
        ("path", "timestamp_field", "max_age_seconds", "required_top_level_fields"),
        "measurement_telemetry",
    )
    required_telemetry_fields = telemetry["required_top_level_fields"]
    if not isinstance(required_telemetry_fields, list) or not required_telemetry_fields:
        raise HarnessError("measurement_telemetry.required_top_level_fields must be a nonempty list")
    if any(not isinstance(field, str) or not field for field in required_telemetry_fields):
        raise HarnessError("measurement telemetry required field names must be nonempty strings")
    maximum_age = float(telemetry["max_age_seconds"])
    if not 0 < maximum_age <= 15:
        raise HarnessError("measurement telemetry maximum age must be in (0, 15] seconds")
    _require(manifest["api"], ("chat_completions_route",), "api")
    _require(manifest["timeouts"], ("short_seconds", "long_seconds"), "timeouts")
    if not 0 < float(manifest["timeouts"]["short_seconds"]) <= 120:
        raise HarnessError("short request timeout exceeds the binding 120 second ceiling")
    if not 0 < float(manifest["timeouts"]["long_seconds"]) <= 900:
        raise HarnessError("long request timeout exceeds the binding 900 second ceiling")
    logging = manifest["logging"]
    _require(
        logging,
        (
            "command_argv_template",
            "controller_minus_rank_clock_seconds",
            "clock_skew_method",
            "lead_margin_seconds",
            "tail_margin_seconds",
            "generation_request_regex",
            "cached_tokens_regex",
            "expected_request_log_count",
        ),
        "logging",
    )
    if not isinstance(logging["command_argv_template"], list) or not logging["command_argv_template"]:
        raise HarnessError("logging.command_argv_template must be a nonempty argv list")
    allowed_log_fields = {
        "host", "rank", "cid", "started_at", "log_lower_bound", "since", "until"
    }
    try:
        log_fields = {
            field_name
            for part in logging["command_argv_template"]
            for _, field_name, _, _ in string.Formatter().parse(str(part))
            if field_name is not None
        }
    except ValueError as error:
        raise HarnessError(f"invalid logging command argv template: {error}") from error
    unknown_log_fields = log_fields - allowed_log_fields
    if unknown_log_fields:
        raise HarnessError(
            "logging command argv template has unknown fields: "
            + ", ".join(sorted(unknown_log_fields))
        )
    skew_map = logging["controller_minus_rank_clock_seconds"]
    if not isinstance(skew_map, dict):
        raise HarnessError("logging.controller_minus_rank_clock_seconds must be an object")
    for rank in manifest["ranks"]:
        if str(rank["rank"]) not in skew_map and rank["host"] not in skew_map:
            raise HarnessError(f"rank {rank['rank']} lacks an explicit measured clock offset")
    if not isinstance(logging["clock_skew_method"], str) or not logging["clock_skew_method"].strip():
        raise HarnessError("logging.clock_skew_method must be a nonempty string")
    if float(logging["lead_margin_seconds"]) < 0 or float(logging["tail_margin_seconds"]) < 0:
        raise HarnessError("logging attribution margins cannot be negative")
    try:
        re.compile(logging["generation_request_regex"])
        re.compile(logging["cached_tokens_regex"])
    except (TypeError, re.error) as error:
        raise HarnessError(f"invalid logging regex: {error}") from error
    attribution = logging.get("cached_tokens_attribution")
    if attribution is not None:
        if not isinstance(attribution, dict) or attribution.get("kind") not in (
                "log_regex", "response_field", "metrics_delta"):
            raise HarnessError(
                "logging.cached_tokens_attribution must declare kind "
                "log_regex, response_field or metrics_delta")
        if attribution["kind"] == "response_field" and not isinstance(
                attribution.get("object_path"), list):
            raise HarnessError(
                "response_field cached-token attribution needs object_path")
        if attribution["kind"] == "metrics_delta" and not isinstance(
                attribution.get("metric_name"), str):
            raise HarnessError(
                "metrics_delta cached-token attribution needs metric_name")
    if int(logging["expected_request_log_count"]) < 1:
        raise HarnessError("logging.expected_request_log_count must be positive")
    concurrency = manifest["concurrency"]
    _require(concurrency, ("max_active_requests",), "concurrency")
    if int(concurrency["max_active_requests"]) < 1:
        raise HarnessError("concurrency.max_active_requests must be positive")
    isolation = manifest["isolation"]
    _require(
        isolation,
        ("idle_snapshot_spacing_seconds", "post_request_monitor_seconds"),
        "isolation",
    )
    spacing = float(isolation["idle_snapshot_spacing_seconds"])
    monitoring = float(isolation["post_request_monitor_seconds"])
    if not 0 <= spacing <= 30:
        raise HarnessError("isolation.idle_snapshot_spacing_seconds must be in [0, 30]")
    if not 0 <= monitoring <= 900:
        raise HarnessError("isolation.post_request_monitor_seconds must be in [0, 900]")
    unresolved = manifest.get("unresolved_flags")
    if isinstance(unresolved, list) and unresolved:
        raise HarnessError(
            "manifest carries unresolved flags; harness execution refused: "
            + "; ".join(str(item) for item in unresolved))
    if manifest.get("queue_adapter", {}).get("unverified_by_http_probe"):
        raise HarnessError(
            "queue adapter is unverified by HTTP probe; harness execution "
            "refused until owner probe evidence freezes it")
    manifest["_manifest_path"] = str(path)
    manifest["_manifest_sha256"] = sha256_file(path)
    manifest["_output_root"] = str(resolve_path(path, manifest["output_root"]))
    return manifest


def require_execution_window(manifest: dict[str, Any]) -> None:
    if manifest["exclusive_window_confirmed"] is not True:
        raise HarnessError("benchmark execution requires exclusive_window_confirmed=true")


def load_fixtures(manifest: dict[str, Any]) -> tuple[dict[str, Any], Path, str]:
    spec = manifest.get("fixtures")
    if not isinstance(spec, dict):
        raise HarnessError("manifest.fixtures must name the frozen fixture catalog")
    _require(spec, ("path", "sha256"), "fixtures")
    path = resolve_path(Path(manifest["_manifest_path"]), spec["path"])
    actual = sha256_file(path)
    if actual != spec["sha256"]:
        raise HarnessError(f"fixture catalog hash mismatch: expected {spec['sha256']}, got {actual}")
    catalog = read_json(path)
    if catalog.get("schema_version") != 1:
        raise HarnessError("unsupported fixture catalog schema")
    return catalog, path, actual


def output_root(manifest: dict[str, Any]) -> Path:
    return Path(manifest["_output_root"])


def acquire_runner_lock(manifest: dict[str, Any]) -> None:
    """Hold a nonblocking process-lifetime lock for this attempt output root."""

    if "_runner_lock_handle" in manifest:
        return
    root = output_root(manifest)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / "HARNESS.lock"
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        handle.close()
        raise HarnessError(f"another benchmark harness holds {lock_path}") from error
    handle.seek(0)
    handle.truncate()
    handle.write(json.dumps({"pid": os.getpid(), "run_id": manifest["run_id"], "at": utc_text()}) + "\n")
    handle.flush()
    manifest["_runner_lock_handle"] = handle


def receipt_path(
    manifest: dict[str, Any], suite: str, phase: str, cell: str, repetition: int
) -> Path:
    for value, name in ((suite, "suite"), (phase, "phase"), (cell, "cell")):
        safe_component(value, name)
    if not isinstance(repetition, int) or repetition < 1:
        raise HarnessError("repetition must be a positive integer")
    return output_root(manifest) / "REQUESTS" / suite / phase / f"{cell}-{repetition}.json"


def receipt_identity(
    manifest: dict[str, Any], fixture_hash: str, suite: str, phase: str, cell: str, repetition: int
) -> dict[str, Any]:
    return {
        "campaign_id": manifest["campaign_id"],
        "run_id": manifest["run_id"],
        "manifest_sha256": manifest["_manifest_sha256"],
        "fixture_sha256": fixture_hash,
        "suite": suite,
        "phase": phase,
        "cell": cell,
        "repetition": repetition,
    }


def existing_receipt(path: Path, identity: dict[str, Any]) -> dict[str, Any] | None:
    """Resume only an exact, terminal receipt; refuse stale/mixed collisions."""

    if not path.exists():
        return None
    record = read_json(path)
    mismatches = {key: (record.get(key), value) for key, value in identity.items() if record.get(key) != value}
    if mismatches:
        raise HarnessError(f"stale or mixed receipt at {path}: {mismatches}")
    if record.get("state") not in {
        "PASSED",
        "FAILED",
        "CONTAMINATED",
        "TIMED_OUT",
        "INSUFFICIENT_COVERAGE",
        "COMPLETED",
    }:
        raise HarnessError(f"existing receipt is not terminal: {path}")
    return record


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def telemetry_snapshot(manifest: dict[str, Any]) -> dict[str, Any]:
    spec = manifest["measurement_telemetry"]
    path = resolve_path(Path(manifest["_manifest_path"]), spec["path"])
    if not path.exists():
        raise HarnessError(f"measurement telemetry is missing: {path}")
    raw = path.read_bytes()
    sample = json.loads(raw)
    field = spec["timestamp_field"]
    if field not in sample:
        raise HarnessError(f"measurement telemetry lacks timestamp field {field!r}")
    observed = parse_utc(sample[field])
    age = (utc_now() - observed).total_seconds()
    if age < -float(spec.get("future_skew_tolerance_seconds", 2)):
        raise HarnessError(f"measurement telemetry timestamp is in the future by {-age:.3f}s")
    if age >= float(spec["max_age_seconds"]):
        raise HarnessError(f"measurement telemetry is stale ({age:.3f}s)")
    for key in spec.get("required_top_level_fields", []):
        if key not in sample:
            raise HarnessError(f"measurement telemetry lacks required field {key!r}")
    # Historical guard files are intentionally neither opened nor consulted.
    return {
        "path": str(path),
        "sha256": sha256_bytes(raw),
        "observed_at": utc_text(observed),
        "age_seconds": age,
        "sample": sample,
        "source": "fresh_read_only_measurement_telemetry",
        "external_guard_used": False,
    }


def _url(base: str, route: str) -> str:
    return base.rstrip("/") + "/" + route.lstrip("/")


def get_bytes(manifest: dict[str, Any], route: str, timeout: float = 15) -> bytes:
    with urllib.request.urlopen(_url(manifest["base_url"], route), timeout=timeout) as response:
        return response.read()


def get_json(manifest: dict[str, Any], route: str, timeout: float = 15) -> Any:
    return json.loads(get_bytes(manifest, route, timeout).decode("utf-8"))


def _path_get(value: Any, path: list[Any]) -> Any:
    current = value
    for component in path:
        current = current[component]
    return current


def queue_snapshot(manifest: dict[str, Any]) -> dict[str, Any]:
    """Read a render-time-selected queue schema without guessing engine fields."""

    adapter = manifest["queue_adapter"]
    _require(adapter, ("kind", "route"), "queue_adapter")
    kind = adapter["kind"]
    if kind == "json_fields":
        _require(adapter, ("object_path", "running_field", "waiting_field"), "JSON queue adapter")
        raw = get_json(manifest, adapter["route"])
        item = _path_get(raw, adapter["object_path"])
        running = int(item[adapter["running_field"]])
        waiting = int(item[adapter["waiting_field"]])
    elif kind == "prometheus_metrics":
        _require(adapter, ("running_metric", "waiting_metric"), "Prometheus queue adapter")
        text = get_bytes(manifest, adapter["route"]).decode("utf-8")

        def metric_total(name: str) -> float:
            pattern = re.compile(rf"^{re.escape(name)}(?:\{{[^}}]*\}})?\s+([-+\d.eE]+)$")
            values = [float(match.group(1)) for line in text.splitlines() if (match := pattern.match(line))]
            if not values:
                raise HarnessError(f"resolved queue metric absent at runtime: {name}")
            return sum(values)

        raw = text
        running = int(metric_total(adapter["running_metric"]))
        waiting = int(metric_total(adapter["waiting_metric"]))
    else:
        raise HarnessError(f"unresolved/unsupported queue adapter kind: {kind!r}")
    if running < 0 or waiting < 0:
        raise HarnessError("negative queue counters")
    return {
        "at": utc_text(),
        "adapter": copy.deepcopy(adapter),
        "running": running,
        "waiting": waiting,
        "raw": raw,
    }


def require_idle(manifest: dict[str, Any], *, label: str) -> dict[str, Any]:
    telemetry = telemetry_snapshot(manifest)
    queue = queue_snapshot(manifest)
    if queue["running"] or queue["waiting"]:
        raise HarnessError(
            f"external traffic or undrained engine at {label}: "
            f"running={queue['running']} waiting={queue['waiting']}"
        )
    return {"label": label, "telemetry": telemetry, "queue": queue}


def post_request_pause(manifest: dict[str, Any]) -> None:
    """Allow asynchronous load snapshots to settle outside the timed interval."""
    delay = float(manifest["isolation"]["post_request_monitor_seconds"])
    if not 0 <= delay <= 900:
        raise HarnessError("post-request observation delay must be in [0, 900]")
    if delay:
        time.sleep(delay)


def initial_idle_pair(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    first = require_idle(manifest, label="suite_idle_snapshot_1")
    spacing = float(manifest.get("isolation", {}).get("idle_snapshot_spacing_seconds", 1.0))
    if not 0 <= spacing <= 30:
        raise HarnessError("idle snapshot spacing must be in [0, 30] seconds")
    if spacing:
        time.sleep(spacing)
    second = require_idle(manifest, label="suite_idle_snapshot_2")
    return [first, second]


def render_salt(
    manifest: dict[str, Any], *, suite: str, phase: str, cell: str, repetition: int, cache_group: str
) -> str:
    values = {
        "campaign_id": manifest["campaign_id"],
        "run_id": manifest["run_id"],
        "suite": suite,
        "phase": phase,
        "cell": cell,
        "rep": repetition,
        "cache_group": cache_group,
    }
    try:
        salt = manifest["salt_namespace_template"].format_map(values)
    except (KeyError, ValueError) as error:
        raise HarnessError(f"invalid salt namespace template: {error}") from error
    if not salt or len(salt.encode()) > 256:
        raise HarnessError("rendered cache salt must contain 1..256 bytes")
    return salt


def request_body(
    manifest: dict[str, Any], fixture: dict[str, Any], *, salt: str, stream: bool, ids_phase: bool = False
) -> dict[str, Any]:
    body = copy.deepcopy(fixture["application_request"])
    body["model"] = manifest["model_id"]
    body["cache_salt"] = salt
    body["stream"] = stream
    if stream:
        body["stream_options"] = {"include_usage": True}
    phase = manifest.get("engine_adapter", {}).get("request_fields", {})
    if ids_phase:
        for key, value in phase.get("id_companion", {}).items():
            body[key] = copy.deepcopy(value)
    else:
        for key, value in phase.get("stream", {}).items():
            body[key] = copy.deepcopy(value)
    return body


def _declared_id_fields(manifest: dict[str, Any]) -> dict[str, Any]:
    """The manifest's engine_adapter.id_fields, or the historical SGLang shape
    when undeclared (original Mia semantics are the default, never guessed)."""
    fields = manifest.get("engine_adapter", {}).get("id_fields")
    if fields is None:
        return {
            "output_ids": ["choice.response_token_ids", "response.response_token_ids"],
            "prompt_ids": ["choice.prompt_token_ids", "response.prompt_token_ids"],
            "shape": "sglang",
        }
    if not isinstance(fields, dict) or not isinstance(fields.get("output_ids"), list) \
            or not isinstance(fields.get("prompt_ids"), list):
        raise HarnessError(
            "engine_adapter.id_fields must declare output_ids and prompt_ids "
            "paths; unresolved adapters must not be rendered")
    return fields


def _id_path_lookup(choice: dict[str, Any], response: dict[str, Any], path: str):
    scope, _, name = path.partition(".")
    source = {"choice": choice, "response": response}.get(scope)
    if source is None or not name:
        raise HarnessError(f"unsupported id path {path!r}")
    return source.get(name)


def _extract_output_ids(manifest, choice, response):
    for path in _declared_id_fields(manifest)["output_ids"]:
        value = _id_path_lookup(choice, response, path)
        if value:
            return [int(token_id) for token_id in value], path
    return [], None


def _extract_prompt_ids(manifest, choice, response):
    for path in _declared_id_fields(manifest)["prompt_ids"]:
        value = _id_path_lookup(choice, response, path)
        if value:
            return [int(token_id) for token_id in value], path
    return [], None


def _response_cached_tokens(manifest, response):
    """Cached tokens from the response when the manifest declares a
    response_field attribution.  Absent data stays None (unavailable), never
    an invented zero.  Log-regex attribution (SGLang) is handled by
    log_accounting and returns None here."""
    attribution = manifest.get("logging", {}).get("cached_tokens_attribution")
    if not attribution or attribution.get("kind") != "response_field":
        return None
    value: Any = response
    for key in attribution["object_path"]:
        if not isinstance(value, dict):
            value = None
            break
        value = value.get(key)
    if value is None:
        return None  # explicitly unavailable, not zero
    return int(value)


def _delta_parts(delta: dict[str, Any]) -> tuple[str, str, bool]:
    reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
    visible = delta.get("content") or ""
    has_tool = bool(delta.get("tool_calls"))
    return reasoning, visible, has_tool


def chat_request(
    manifest: dict[str, Any], body: dict[str, Any], *, timeout_seconds: float
) -> dict[str, Any]:
    """Submit one chat request and preserve raw timing/content for recomputation."""

    route = manifest["api"]["chat_completions_route"]
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    request = urllib.request.Request(
        _url(manifest["base_url"], route),
        data=encoded,
        headers={"Content-Type": "application/json"},
    )
    start_mono = time.monotonic()
    start_utc = utc_text()
    first_event = first_reasoning = first_visible = None
    events: list[dict[str, Any]] = []
    raw_lines: list[str] = []
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    calls: dict[str, dict[str, str]] = {}
    token_ids: list[int] = []
    usage = None
    finish = None
    done = False
    done_seconds = None
    # ID-source provenance applies to BOTH transports (SSE initializes here;
    # the nonstream branch assigns from the manifest-declared paths).
    token_id_source = None
    prompt_id_source = None
    response_object = None
    prompt_token_ids: list[int] = []
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            if body.get("stream"):
                for raw in response:
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    raw_lines.append(line)
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        done = True
                        done_seconds = time.monotonic() - start_mono
                        break
                    event = json.loads(payload)
                    if event.get("error"):
                        raise HarnessError(f"server error event: {event['error']!r}")
                    elapsed = time.monotonic() - start_mono
                    if first_event is None:
                        first_event = elapsed
                    events.append({"seconds": elapsed, "response": event})
                    if event.get("prompt_token_ids") and not prompt_token_ids:
                        prompt_token_ids = [int(token_id) for token_id in event["prompt_token_ids"]]
                        prompt_id_source = "stream.event.prompt_token_ids"
                    usage = event.get("usage") or usage
                    for choice in event.get("choices", []):
                        delta = choice.get("delta") or {}
                        reasoning, visible, has_tool = _delta_parts(delta)
                        if reasoning and first_reasoning is None:
                            first_reasoning = elapsed
                        if (visible or has_tool) and first_visible is None:
                            first_visible = elapsed
                        reasoning_parts.append(reasoning)
                        content_parts.append(visible)
                        for token_id in choice.get("token_ids") or []:
                            token_ids.append(int(token_id))
                            if token_id_source is None:
                                token_id_source = "stream.choice.token_ids"
                        for call in delta.get("tool_calls") or []:
                            index = str(call.get("index", 0))
                            target = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                            target["id"] += call.get("id") or ""
                            function = call.get("function") or {}
                            target["name"] += function.get("name") or ""
                            target["arguments"] += function.get("arguments") or ""
                        finish = choice.get("finish_reason") or finish
            else:
                raw = response.read()
                raw_lines.append(raw.decode("utf-8", "replace"))
                response_object = json.loads(raw)
                if response_object.get("error"):
                    raise HarnessError(f"server error response: {response_object['error']!r}")
                done = True
                done_seconds = time.monotonic() - start_mono
                if not response_object.get("choices"):
                    raise HarnessError("nonstream response has no choices")
                choice = response_object["choices"][0]
                message = choice.get("message") or {}
                content_parts.append(message.get("content") or "")
                reasoning_parts.append(message.get("reasoning_content") or message.get("reasoning") or "")
                calls = {
                    str(index): {
                        "id": call.get("id") or "",
                        "name": (call.get("function") or {}).get("name") or "",
                        "arguments": (call.get("function") or {}).get("arguments") or "",
                    }
                    for index, call in enumerate(message.get("tool_calls") or [])
                }
                usage = response_object.get("usage")
                finish = choice.get("finish_reason")
                token_ids, token_id_source = _extract_output_ids(manifest, choice, response_object)
                prompt_token_ids, prompt_id_source = _extract_prompt_ids(manifest, choice, response_object)
    except TimeoutError:
        raise
    elapsed = time.monotonic() - start_mono
    completion_tokens = (usage or {}).get("completion_tokens")
    return {
        "start_monotonic": start_mono,
        "start_utc": start_utc,
        "end_monotonic": time.monotonic(),
        "end_utc": utc_text(),
        "elapsed_seconds": elapsed,
        "first_sse_event_seconds": first_event,
        "first_nonempty_reasoning_delta_seconds": first_reasoning,
        "first_visible_content_or_tool_delta_seconds": first_visible,
        "first_nonempty_model_output_seconds": min(
            [value for value in (first_reasoning, first_visible) if value is not None],
            default=None,
        ),
        "done_seconds": done_seconds,
        "done": done,
        "raw_reply": "\n".join(raw_lines),
        "events": events,
        "response": response_object,
        "text": "".join(content_parts),
        "reasoning_text": "".join(reasoning_parts),
        "calls": calls,
        "output_token_ids": token_ids,
        "rendered_input_ids": prompt_token_ids,
        "output_id_source": token_id_source,
        "prompt_id_source": prompt_id_source,
        "cached_tokens": _response_cached_tokens(
            manifest,
            response_object if response_object is not None
            else ({"usage": usage} if usage is not None else None)),
        "usage": usage,
        "finish_reason": finish,
        "complete_answer_tokens_per_http_second": (
            float(completion_tokens) / elapsed if completion_tokens is not None and elapsed > 0 else None
        ),
        "visible_delta_to_done_seconds": (
            done_seconds - first_visible
            if first_visible is not None and done_seconds is not None
            else None
        ),
    }


def validate_expected(fixture: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    expected = fixture.get("expected", {})
    kind = expected.get("validator", "nonempty")
    passed = False
    detail: dict[str, Any] = {"validator": kind}
    if kind == "exact_text":
        actual = result["text"].strip()
        wanted = expected["value"]
        passed = actual == wanted
        detail.update(actual=actual, expected=wanted)
    elif kind == "exact_tool_call":
        parsed = []
        try:
            parsed = [
                {"name": call["name"], "arguments": json.loads(call["arguments"])}
                for _, call in sorted(result["calls"].items(), key=lambda item: int(item[0]))
            ]
        except (json.JSONDecodeError, KeyError, ValueError) as error:
            detail["parse_error"] = repr(error)
        wanted = expected["calls"]
        passed = parsed == wanted
        detail.update(actual=parsed, expected=wanted)
    elif kind == "json_schema_subset":
        try:
            actual = json.loads(result["text"])
            required = expected["required_values"]
            passed = all(actual.get(key) == value for key, value in required.items())
            detail.update(actual=actual, expected_subset=required)
        except (json.JSONDecodeError, AttributeError) as error:
            detail["parse_error"] = repr(error)
    elif kind == "json_exact":
        try:
            actual = json.loads(result["text"])
            wanted = expected["value"]
            passed = actual == wanted
            if expected.get("key_order_required") and isinstance(actual, dict):
                passed = passed and list(actual) == list(wanted)
            detail.update(actual=actual, expected=wanted)
        except json.JSONDecodeError as error:
            detail["parse_error"] = repr(error)
    elif kind == "nonempty":
        passed = bool(result["text"].strip() or result["calls"])
    elif kind == "long_output_validator":
        spec = expected["validator_spec"]
        lines = result["text"].splitlines()
        errors = []
        if len(lines) != int(spec["row_count"]):
            errors.append(f"row_count={len(lines)} expected={spec['row_count']}")
        factor_match = re.fullmatch(r"\(i\*(\d+)\+(\d+)\)%997", spec["v_formula"])
        if not factor_match:
            raise HarnessError(f"unsupported frozen long-output formula: {spec['v_formula']!r}")
        factor, offset = map(int, factor_match.groups())
        for index, line in enumerate(lines, 1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                errors.append(f"line {index}: {error}")
                break
            if not isinstance(value, dict) or list(value) != spec["keys_in_order"]:
                errors.append(f"line {index}: wrong object/key order")
                break
            if value != {"i": index, "v": (index * factor + offset) % 997}:
                errors.append(f"line {index}: wrong values")
                break
            if line != json.dumps(value, separators=(",", ":")):
                errors.append(f"line {index}: not compact canonical JSON")
                break
        passed = not errors
        detail.update(errors=errors, observed_rows=len(lines), expected_rows=spec["row_count"])
    elif kind in {"external_code_validator", "manual_prose_rubric"}:
        # These are scored by their frozen external/declarative validator, not by transport code.
        passed = bool(result["text"].strip())
        detail["transport_only"] = True
    else:
        raise HarnessError(f"unsupported expected validator {kind!r}")
    detail["passed"] = passed
    return detail


def log_argv(
    manifest: dict[str, Any], rank: dict[str, Any], *, since: datetime, until: datetime
) -> list[str]:
    logging = manifest.get("logging")
    if not isinstance(logging, dict) or "command_argv_template" not in logging:
        raise HarnessError("manifest.logging.command_argv_template is required for log attribution")
    skew_map = logging.get("controller_minus_rank_clock_seconds", {})
    skew = float(skew_map.get(str(rank["rank"]), skew_map.get(rank["host"], 0.0)))
    lead = float(logging.get("lead_margin_seconds", 0.0))
    tail = float(logging.get("tail_margin_seconds", 0.0))
    adjusted_since = since - timedelta(seconds=skew + lead)
    adjusted_until = until - timedelta(seconds=skew - tail)
    lower = parse_utc(rank["log_lower_bound"])
    adjusted_since = max(adjusted_since, lower)
    values = {
        "host": rank["host"],
        "rank": rank["rank"],
        "cid": rank["cid"],
        "started_at": rank["started_at"],
        "log_lower_bound": rank["log_lower_bound"],
        "since": utc_text(adjusted_since),
        "until": utc_text(adjusted_until),
    }
    return [str(part).format_map(values) for part in logging["command_argv_template"]]


def collect_rank_logs(
    manifest: dict[str, Any], *, since: str, until: str
) -> list[dict[str, Any]]:
    start = parse_utc(since)
    end = parse_utc(until)
    if end < start:
        raise HarnessError("log interval ends before it starts")
    records = []
    for rank in manifest["ranks"]:
        argv = log_argv(manifest, rank, since=start, until=end)
        # Docker and server wrappers may preserve the container's stderr stream.
        # Merge it here so request/cache evidence is not silently omitted.
        completed = subprocess.run(
            argv,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        record = {
            "rank": rank["rank"],
            "host": rank["host"],
            "cid": rank["cid"],
            "started_at": rank["started_at"],
            "log_lower_bound": rank["log_lower_bound"],
            "argv": argv,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": None,
        }
        records.append(record)
        if completed.returncode:
            raise HarnessError(
                f"rank {rank['rank']} log command failed with {completed.returncode}: {completed.stdout[-500:]}"
            )
    return records


def log_accounting(manifest: dict[str, Any], logs: list[dict[str, Any]],
                   response_cached_tokens: int | None = None) -> dict[str, Any]:
    logging = manifest.get("logging", {})
    request_pattern = re.compile(logging.get("generation_request_regex", r"POST /v1/chat/completions"))
    attribution = logging.get("cached_tokens_attribution") or {"kind": "log_regex"}
    kind = attribution.get("kind", "log_regex")
    if kind == "log_regex":
        hit_pattern = re.compile(logging.get("cached_tokens_regex", r"#cached-token:\s*(\d+)"))
    else:
        # response_field / metrics_delta attribution is NOT log-shaped: the
        # cached-token value comes from receipts (chat_request result /
        # owner-supplied metric deltas).  The log regex stays informational
        # only and log hits are reported as not applicable, never zero-filled.
        hit_pattern = None
    per_rank = []
    total_posts = 0
    total_hits = 0
    for entry in logs:
        posts = len(request_pattern.findall(entry["stdout"]))
        total_posts += posts
        if hit_pattern is not None:
            hits = sum(int(value) for value in hit_pattern.findall(entry["stdout"]))
            total_hits += hits
        else:
            hits = None
        per_rank.append({"rank": entry["rank"], "chat_posts": posts, "cached_tokens": hits})
    # Most distributed stacks log HTTP admission on one rank.  The manifest declares
    # the expected aggregate to avoid assuming that topology here.
    expected_posts = int(logging.get("expected_request_log_count", 1))
    result = {
        "per_rank": per_rank,
        "chat_posts": total_posts,
        "cached_tokens": total_hits if kind == "log_regex" else None,
        "expected_chat_posts": expected_posts,
        "external_traffic_possible": total_posts != expected_posts,
    }
    if kind != "log_regex":
        result["cached_tokens_attribution"] = {
            "kind": kind,
            "note": "cached tokens come from receipts (response usage field "
                    "or owner-supplied metric deltas), not log hits; "
                    "unavailable stays None and is never zero-filled",
            **({"object_path": attribution["object_path"]}
               if kind == "response_field" else {}),
            **({"metric_name": attribution["metric_name"]}
               if kind == "metrics_delta" else {}),
        }
        if kind == "response_field":
            # The report-facing value: the response receipt's cached-token
            # count when present, explicitly None when unavailable.
            result["cached_tokens"] = (
                int(response_cached_tokens)
                if response_cached_tokens is not None else None)
    return result


def fixture_hash(fixture: dict[str, Any]) -> str:
    declared = fixture.get("fixture_sha256")
    material = {key: value for key, value in fixture.items() if key != "fixture_sha256"}
    actual = object_sha256(material)
    if declared and declared != actual:
        raise HarnessError(f"fixture hash mismatch: expected {declared}, got {actual}")
    return actual
