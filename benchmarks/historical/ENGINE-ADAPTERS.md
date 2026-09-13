# Engine adapters

These notes are a render-time contract, not a capability claim about a future boot. The manifest must contain the adapter actually observed on that incarnation. A renderer must reject unresolved fields; the harness never falls back from one engine's schema to another.

## Queue and idle state

For the current Mia/SGLang incarnation, the verified endpoint is `GET /v1/loads`. The JSON response has a `loads` array, and the idle fields are `loads[0].num_running_reqs` and `loads[0].num_waiting_reqs`. `num_reqs` belongs to the deprecated `/get_load` projection and must not be used against `/v1/loads`. The source definitions are in `engines/sglang/python/sglang/srt/entrypoints/v1_loads.py` and `engines/sglang/python/sglang/srt/managers/load_snapshot.py`. The measured Mia endpoint returns 404 for `/metrics`, so Prometheus data is unavailable on that incarnation even though the source tree contains metric definitions.

The corresponding manifest fragment is:

```json
{
  "queue_adapter": {
    "kind": "json_fields",
    "route": "/v1/loads",
    "object_path": ["loads", 0],
    "running_field": "num_running_reqs",
    "waiting_field": "num_waiting_reqs"
  }
}
```

For vLLM, resolve the running boot's metrics endpoint and names during render/capability inspection. This checkout defines the gauges `vllm:num_requests_running` and `vllm:num_requests_waiting` in `engines/vllm/vllm/v1/metrics/loggers.py`; that source evidence does not prove that a particular boot exposes them. Only after an HTTP probe has confirmed the endpoint and both names may the renderer freeze:

```json
{
  "queue_adapter": {
    "kind": "prometheus_metrics",
    "route": "/metrics",
    "running_metric": "vllm:num_requests_running",
    "waiting_metric": "vllm:num_requests_waiting"
  }
}
```

Every benchmark suite takes two idle snapshots, rechecks immediately before each request or concurrent batch, and reads fresh measurement telemetry before and after. Telemetry must be younger than 15 seconds. No historical `LIVE-GUARD.json` is opened, and Mia's intentionally absent external guard is not reconstructed.

## Streaming and token identity

SGLang chat streaming explicitly rejects `return_prompt_token_ids`, `return_token_ids`, and `return_meta_info`; see `engines/sglang/python/sglang/srt/entrypoints/openai/serving_chat.py`. Therefore the stream phase measures event timing and content, while a separate nonstream ID companion uses:

```json
{
  "return_token_ids": true,
  "return_meta_info": true,
  "return_cached_tokens_details": true
}
```

The nonstream choice exposes `prompt_token_ids`, `response_token_ids`, and `meta_info`. Stream and companion use separate salt namespaces, so the companion cannot warm or consume the timed stream's namespace.

This vLLM checkout supports `return_token_ids` on chat requests in both streaming and nonstreaming mode. In a stream, `prompt_token_ids` appears only in the first chunk and each choice's `token_ids` contains its delta IDs; in a full response, prompt IDs are top-level and generated IDs are attached to choices. The exact response layout must still be captured from the rendered boot. For token-level probability evidence, vLLM chat accepts `logprobs`, `top_logprobs`, and nonstream `prompt_logprobs`; SGLang accepts its `logprobs` count and returns input/output logprob metadata when requested. Logprobs are companions to authoritative IDs, not a substitute for an ID list.

Both engines receive identical application messages, tools, sampling values, and limits from `FIXTURES.json`. Only transport extensions are adapter-specific. Record complete ID arrays where available, then calculate actual input length, longest common prefix, eligible aligned prefix, actual reused tokens, and recomputed suffix length. Never infer exact-token parity from equal token counts.

## Speculative acceptance

SGLang exposes request-level speculative details when `return_spec_tokens_details=true`. Its `spec_correct_drafts_histogram[j]` counts verify steps that accepted exactly `j` draft tokens, excluding the bonus token. `spec_accept_length` is `completion_tokens / spec_verify_ct` and therefore includes the bonus token; `spec_accept_rate` is accepted drafts divided by proposed drafts. The same payload contains proposed/correct draft totals and verify-step count. This is enough to derive position `p` acceptance as `sum(histogram[p:]) / sum(histogram)`, but it is not an ordered per-step trace. If only `/v1/loads` or logs are available, report the exact aggregate definition; do not invent per-position samples.

The relevant SGLang paths are `engines/sglang/python/sglang/srt/managers/tokenizer_manager.py`, `engines/sglang/python/sglang/srt/managers/schedule_batch.py`, and `engines/sglang/python/sglang/srt/entrypoints/openai/utils.py`.

vLLM exposes aggregate per-position counters as `vllm:spec_decode_num_accepted_tokens_per_pos` when metrics are enabled. For a cleaner isolated request, render `per_request_spec_decode_metrics` as `summary` or `detailed`: the response field is `metrics.speculative_decoding`. Summary includes mean acceptance length, draft acceptance rate, an acceptance histogram, verify-step count, accepted-draft count, proposed-draft count, and configured width. Detailed additionally supplies ordered `per_step_accepted` and `per_step_drafted` arrays. These fields are only attributed for single-sequence requests (`n == 1`). See `engines/vllm/vllm/config/observability.py`, `engines/vllm/vllm/v1/metrics/stats.py`, and `engines/vllm/vllm/v1/spec_decode/metrics.py`.

For both engines, committed tokens include the always-accepted bonus token only where the named metric definition says so. Preserve raw counters, draft/verify-step count, cycle time if genuinely exposed, and the adapter definition in each receipt.

## Clock skew and log bounds

Measure every rank immediately before a suite with at least five midpoint samples:

1. Record controller UTC and monotonic time before invoking a manifest-provided remote `date` argv.
2. Parse the rank's UTC timestamp, then record controller UTC and monotonic time after return.
3. Estimate `controller_minus_rank_clock_seconds` at the controller midpoint; uncertainty is half the round-trip time.
4. Retain all samples and select the minimum-RTT sample for that rank. Derive lead/tail margins from measured uncertainty plus timestamp precision, bounded below the observed inter-request gap.

Freeze the selected offsets, sample receipts, margins, and method in the run manifest. Each log query is bounded below by that rank's own freshly inspected `log_lower_bound`/`StartedAt`, then by the request's skew-adjusted start and end. Never reuse the historical “head 35 ms ahead / 150 ms tail” estimate as a default.

The harness sign convention is `controller_minus_rank_clock_seconds = controller UTC - rank UTC`. Log commands are structured argv from `logging.command_argv_template`; no shell string is constructed.
