Version: v2.0.0-rc.1. Historical research harness archive; see ../README.md for the new catalog hashes and execution scope.

# Parameterized campaign harnesses

This directory is PREP deliverable 5. It does not launch, stop, restart, or mutate a serving deployment. No module performs network or subprocess work when imported. Endpoint requests, installed-Pi commands, validator containers, and log commands occur only after the corresponding script is explicitly invoked with `--manifest`.

The historical sources remain unchanged. Frozen application inputs live in `FIXTURES.json` (SHA256 `4179cd655ebbf0dbc6a203f21a3c8a7df011ec2f53fdc0f5ca90604c25f97a78`); held-out tasks live in `HELD-OUT-TASKS.json` (SHA256 `2e78e77370ee5362672cf6da5bd231ff013a05d22e216e5ee6bc626f2acd298b`). Existing receipts are resumable only when campaign, run, manifest hash, fixture hash, suite, phase, cell, and repetition all match. A stale/mixed filename is a hard error.

## Source mapping

| Copy | Historical source and SHA256 | Parameterized or corrected fields |
|---|---|---|
| `apc_probe.py` | `research/prefill-20260911/apc_probe.py` — `bd13a8c82a827c76e12efd411b665c03088b3f02cd375399f0c26f740df5d77d` | base URL, model, output root, run identity, fixture catalog, salt namespace, telemetry, queue schema, rank log identities/bounds; removed workload-freezing/tokenize mutation and guard-file readiness |
| `screen.py` | `research/mia-serve31-comparison-20260911/screen.py` — `599dd4ae975b0b8413072b5095a8f53201ca66d6c6556e85a6fe9f5d798bfdb5` | all of the above plus every rank host/CID/StartedAt/log bound, log argv/regex, clock offsets/margins, timeouts, concurrency cap, and canonical catalog hash; `/get_load`/`num_reqs` replaced by selected adapter |
| `reconcile_logs.py` | `research/mia-serve31-comparison-20260911/reconcile_logs.py` — `0e47645801b4c20bf6635ac8e391530694db0282487fe261152483df7e6e631d` | output root, receipt set, all rank log identities, clock offsets, margins, argv, and regexes; no fixed 35 ms/150 ms assumption |
| `run_pi.py` | `research/mia-serve31-comparison-20260911/run_pi.py` — `0e6817c83f9a0b9a9520dc99769387d2ccc18c799e32a2bb7f0af22f813941c4` | installed-Pi argv, provider/model/profile identity, fixture/hash, private evidence preservation argv, output root, rank logs/bounds, and timeouts; returned runner must attest fixture hash/effective cap/reasoning/request count |
| `summarize.py` | `research/mia-serve31-comparison-20260911/summarize.py` — `3e8accdf433fbf08b2a51b0373895c346c91ecd817455eddced391dd4f2b3efa` | receipt/output/comparator paths and identities; guard history removed; missing server timers remain `NA` |
| `benchmark.py` | `research/mia-recipe-20260911/benchmark.py` — `b33a8eb7991bd9b3eae1623039dbcf68cd1aef9b7c9eb1722208933dc0a30b7a` | base URL, model, output root, frozen request catalog, run/salt/telemetry/queue/log identities, timeouts, validator linkage |
| `validate_code.py` | `research/mia-recipe-20260911/validate_code.py` — `778b56e4a59d9b0bc0371b980ba35d9c7864f5149e162e790d9aa8413927f49f` | validation host, immutable image, structured transport argv, remote work root, response/output identity, validator path/hash; sandbox remains fixed at network none, read-only, 256 MiB, one CPU, 32 PIDs, and 15 seconds |
| `stream_check.py` | `research/mia-recipe-20260911/stream_check.py` — `e3c63be6e572bb15e2aa1d4763eaa54dd4d71dcd0601fc6b061824c17b01fed8` | base/model/output/telemetry/queue/log/salt/run fields; stale guard check removed |
| `published_c1.py` | `research/mia-recipe-20260911/published_c1.py` — `106a361916f47ca29f9fc1dd61d8ec873c654e283908cf59767f99e5bf35e6dd` | base/model/output/telemetry/queue/log/salt/run fields; three repetitions and exact request are catalog data |
| `v41bench.py` | `upstream-tony/bench/v41bench.py` — `e0d6b2d25bd585d11fbdf39c2ddcdf7a4de8ab685af6bd42465e69f3ee6e80a8` | base/model/output/levels/prefill targets/comparison tag/salts/concurrency cap/telemetry/queue/logs; emits raw request receipts and blocks prefill until the context-ramp gate is `PASSED` |

Supporting copies are `common.py` (manifest, isolation, receipt, HTTP, timing, log and validation primitives) and `single_request.py` (one-fixture runner). The already-generated synthetic catalogs are shipped; their historical one-shot source construction helper is not required or included. `held_out_code_validator.py` contains the functional tests for the three held-out code tasks.

Additional frozen source identities are inside `FIXTURES.json`, including:

- `research/mia-serve31-comparison-20260911/PLAN.json` — `afe9a9501bc0fb540130d2ea055d76f63d920ca020a4e17b11b912ff276d1f54`
- `research/mia-recipe-20260911/BENCHMARK-REQUESTS.json` — `2b6d7218ab25751f22b9ed854532fbce4ba130cd17e84ccdf95e69194ff4ff1e`
- Tony `prompts-v1.json` — `f8106746dc729d287d3509376d683719862b13f3657847e86153856be6c80bb3`
- archived context gate — `648e534ec1db175c61f8f70e3173200f3885db95c9a43a93988fcd73d9f17c3a`

The parameterized Tony implementation necessarily has a new harness hash because it adds campaign receipts, isolation, and manifest handling. The category prompts, caps, warmup category selection, deterministic prefill generator, and legacy formula are preserved. The formula is always labeled **Tony legacy decode metric**; decision data uses completion tokens divided by full HTTP wall seconds. The manifest `tony.comparison_tag` must be identical for paired boots because the front tag is application input.

## Manifest interface

Each executable copy requires `--manifest /absolute/path/MANIFEST.json`. Required benchmark-facing fields are:

```json
{
  "schema_version": 1,
  "campaign_id": "exl3-mia-20260911",
  "run_id": "RENDERED-RUN-ID",
  "base_url": "RENDERED-SERVICE-ROOT",
  "model_id": "RENDERED-SERVED-MODEL",
  "output_root": "NEW-ATTEMPT-ROOT",
  "exclusive_window_owner": "COORDINATED-OWNER",
  "exclusive_window_confirmed": true,
  "salt_namespace_template": "{campaign_id}:{run_id}:{suite}:{phase}:r{rep}:{cache_group}",
  "ranks": [
    {
      "rank": 0,
      "host": "RENDERED-HOST",
      "cid": "FULL-OR-UNAMBIGUOUS-CONTAINER-ID",
      "started_at": "FRESH-DOCKER-STARTED-AT",
      "log_lower_bound": "SAME-INCARNATION-LOWER-BOUND"
    }
  ],
  "measurement_telemetry": {
    "path": "CURRENT-READ-ONLY-TELEMETRY.json",
    "timestamp_field": "observed_at",
    "max_age_seconds": 15,
    "required_top_level_fields": ["engine_identity", "api_identity", "image_identity", "ranks"]
  },
  "queue_adapter": {
    "kind": "json_fields",
    "route": "/v1/loads",
    "object_path": ["loads", 0],
    "running_field": "num_running_reqs",
    "waiting_field": "num_waiting_reqs"
  },
  "api": {"chat_completions_route": "/v1/chat/completions"},
  "timeouts": {"short_seconds": 120, "long_seconds": 900},
  "fixtures": {
    "path": "PATH-TO/FIXTURES.json",
    "sha256": "4179cd655ebbf0dbc6a203f21a3c8a7df011ec2f53fdc0f5ca90604c25f97a78"
  },
  "engine_adapter": {
    "request_fields": {
      "stream": {},
      "id_companion": {
        "return_token_ids": true,
        "return_meta_info": true,
        "return_cached_tokens_details": true
      }
    }
  },
  "logging": {
    "command_argv_template": ["ssh", "{host}", "docker", "logs", "--timestamps", "--since", "{since}", "--until", "{until}", "{cid}"],
    "controller_minus_rank_clock_seconds": {"0": 0.0},
    "clock_skew_method": "FIVE-SAMPLE-MINIMUM-RTT-MIDPOINT",
    "lead_margin_seconds": 0.0,
    "tail_margin_seconds": 0.0,
    "generation_request_regex": "POST /v1/chat/completions",
    "cached_tokens_regex": "#cached-token: (\\d+)",
    "expected_request_log_count": 1
  },
  "concurrency": {"max_active_requests": 4},
  "isolation": {"idle_snapshot_spacing_seconds": 1.0, "post_request_monitor_seconds": 0.0}
}
```

The strings above are placeholders, not runnable defaults. A Mia render uses the verified `/v1/loads` adapter. A vLLM render must inspect that boot and select a proven adapter; it must not copy the example by assumption.

`validate_code.py` additionally requires `validation.host`, `validation.image`, `validation.transport_argv_prefix`, and `validation.remote_work_root_template`. The last may use `{campaign_id}`, `{run_id}`, `{task}`, `{rep}`, and `{host}`.

`run_pi.py` additionally requires `pi.command_argv_template`, `pi.provider`, `pi.model`, and `pi.profile_identity` with `base_url` and `model_id` equal to the manifest. The invoked Pi fixture driver must print exactly one JSON result and attest `fixture_sha256`, `output_cap`, `reasoning_setting`, and `request_count`. Optional `pi.preserve_argv_template` receives the same placeholders plus `{event_log}`. Raw Pi prompts, answers, and event logs stay in the manifest-declared private location.

`v41bench.py` additionally requires `tony.comparison_tag`, `tony.levels`, and `tony.prefill_targets`. Nonempty prefill targets also require `gates.context_ramp == "PASSED"`. The historical default sweep is intentionally not a default here because it is about 93K actual input tokens despite the 64K target label.

## Usage

Representative invocations, only during an owner-coordinated exclusive window:

```bash
python3 apc_probe.py --manifest MANIFEST.json verify-frozen
python3 screen.py --manifest MANIFEST.json --phase both
python3 benchmark.py --manifest MANIFEST.json
python3 validate_code.py --manifest MANIFEST.json --task ttl_lru_cache --response ATTEMPT/REQUESTS/quality/response/ttl_lru_cache-1.json
python3 run_pi.py --manifest MANIFEST.json --cell pi-hidden-marker-two-read
python3 v41bench.py --manifest MANIFEST.json
python3 reconcile_logs.py --manifest MANIFEST.json --suite cache-screen --phase stream
python3 summarize.py --manifest MANIFEST.json
```

On timeout, a receipt is retained as `TIMED_OUT`, new submission stops, and the campaign owner must confirm cancellation and drain before any replacement. Contaminated intervals are retained as `CONTAMINATED` and never scored. The six historical OFF labels are explicitly recorded as isolated-salt ON controls.

## Render-time open items

These values cannot be frozen honestly without observing the intended incarnation. They are manifest inputs or required attestations, not defaults:

- vLLM queue endpoint, enabled state, and exact running/waiting metric names;
- current measurement-telemetry producer/path/schema and source/API/image identity fields;
- all fresh rank CIDs, `StartedAt`/log lower bounds, controller-minus-rank clock offsets, timestamp precision, and safe attribution margins;
- exact per-incarnation log request/cache-hit regexes and whether HTTP admission is logged once or per rank;
- vLLM per-request speculative metric level and SGLang `return_spec_tokens_details` availability on the built image;
- actual active concurrency and queueing under C6 offered load;
- installed Pi provider/profile/extension hashes, exact command/preservation argv, private evidence path, and a fixture driver capable of attesting the frozen long-history rendering;
- engine-specific grammar request mapping and any response field movement discovered by capability checks;
- actual 64K/76K template-rendered lengths, ID equality/LCP/aligned reuse, and actual output-token counts for the >5K fixtures.

## Contract ambiguities recorded during PREP

- `BENCHMARKS.md` says the long-conversation inputs are “~65536” and “~76000” actual rendered tokens but gives no numeric tolerance. The catalog freezes exact application messages and target labels, requires actual IDs/lengths, and makes no pass claim from the labels alone.
- “valid stop/continuation checks” for the >5K generation does not say whether a second semantic follow-up is mandatory. The catalog requires a normal `stop`, complete JSONL validation, and separate low-cap 1–6, grammar, and tool terminal fixtures; it does not silently spend an undeclared extra long-output attempt.
- SGLang exposes an accepted-draft-count histogram that can derive per-position acceptance rates, but not an ordered per-step trace. The adapter notes preserve that distinction rather than calling the derived rates raw per-position events.
