# Tempo operations

Version: v2.0.3.

The portable lifecycle creates exactly three labeled containers and records their full IDs and fresh Docker `StartedAt`. Mutations resolve those exact IDs, verify the deployment label and incarnation, and never use broad Docker name patterns. It retains stopped containers, caches and all model/user data. A fresh start needs a fresh namespace because the entrypoint release file is single-use.

Per-rank persistent guards sample host MemAvailable and service cgroup memory, swap and events. **Swap usage is telemetry-only:** nonzero service swap does not stop a rank or cause the relay to stop its peers. Current/peak swap, host paging deltas and memory pressure remain visible. This replaces the inherited experiment policy that stopped the fleet on any service swap.

The existing emergency checks remain: **below 4 GiB available**, OOM events, rank death/restart/incarnation change, or telemetry blindness cause an exact-container stop. **4–5 GiB is HOLD** for benchmark qualification. A benchmark memory pass still requires at least 5 GiB observed throughout the run and zero service swap; that annotation is not an automatic serving shutdown. Host swap belonging to other processes is not attributed to the model service.

The Docker memory allocation and requested swap limit are unchanged. If the actual cgroup swap limit differs from Docker's configuration, the guard reports the mismatch. Telemetry-only handling does not increase the configured swap allowance.

The controller's persistent systemd user relay checks all three local guard heartbeats. It propagates a trip to peers and stops owned ranks if a peer fails, identity drifts or heartbeats become stale (15 seconds). Rank guards remain local if the controller is briefly disconnected; keep the controller and relay running for coupling. A controller outage is not a validated operating mode: restore the relay or stop the owned service through the saved configuration. Local guard units use `Restart=no`; the controller relay uses `Restart=on-failure`. No Mia guard is installed or reinstated.

The new lifecycle and relay are operational adaptations to make the final source/configuration portable. They replace campaign registration and private parent-state lookup; they are not benchmarked scheduler changes. The final scheduler, Engram reader and other 15 overlays are byte-identical to the archived runtime. Fresh reconstruction still requires its own runtime validation.

Use `tempo.py status` for current resources/guard samples, `health` for backend readiness and `native` for mounted source hash identity. All measured API claims need `smoke.py` or the separately configured research harness. Backend health does not certify response correctness, maximum context or sustained concurrency. The compiled native modules must also be checked in the installer receipt; Python source identity alone is insufficient.

The final environment retains `JSPARK_TRACE_SCHEDULER=1` and `JSPARK_ENGRAM_TRACE=1` because these switches alone do not activate tracing. Fresh state directories contain no `TRACE-SCHEDULER`, `TRACE-ENGRAM` or `TRACE-CACHE` markers. Do not create these during timed checks. Row cache remains zero. The removed historical Mia guard has no role here.

## Recovery ownership

Before a controlled switch, save outgoing exact IDs, start times, image/configuration and a validated recovery command. Tempo will not discover or stop unrelated models. If a rank fails, first stop Tempo through its saved config. Preserve logs, inspect the fault and restore the previously recorded service using its own lifecycle. Never recover by mixing an old worker with a new head, repeatedly restarting a faulted container, weakening a memory floor, or removing data.

A failed `start` can leave evidence in both the controller state directory and each host's deployment directory. The host `owned.json` is written before starting its guard. If a controller transport failed after a host created a container, inspect that exact host receipt, verify its full ID and label, then stop only that ID. Do not infer absence from a lost SSH response.

## Diagnostics

```bash
python3 tools/tempo.py diagnostics --config config.json
```

The output is a local, readable `diagnostics-redacted.json`: candidate version, release manifest checksum, rank ordinal, running/restart/OOM/trip flags, host MemAvailable, and service cgroup memory/swap/event counters. It excludes prompts, raw responses, logs, environment, credentials, URLs, hostnames, IPs, container IDs, private paths and desktop files by an explicit allowlist. Inspect it before attaching it to an issue. Nothing uploads automatically. Full `smoke.json`, rendered commands and ownership receipts are local operational records and are not the default diagnostic attachment.

## v2.0.3 naming update

v2.0.3 changes names and documentation only. Existing v2.0.1 and v2.0.2 installations need no download, image rebuild, tool replacement or service restart.

## v2.0.2 documentation update

v2.0.2 only clarifies the model download instructions. Existing v2.0.1 installations need no download, image rebuild, tool replacement or service restart. The operational upgrade below applies to installations still using v2.0.0.

## Upgrading from v2.0.0

This is a host-side operational patch. Existing compatible installations reuse the same inference image, weights and packed Engram stores; no image rebuild, download or repack is needed. Both the controller and all rank hosts need the new tools. Replacing a file alone does not update an already-running Python guard or relay.

1. Save your current configuration, exact container IDs and logs. Stage a separate checkout of `v2.0.1` on the controller and each rank host:
   ```bash
   git clone --branch v2.0.1 https://github.com/jakejharris/jspark3-deepseek.git tempo-v2.0.1
   ```
2. Copy your existing configuration to that checkout. Keep the image ID/provenance, model, Engram, packed, work and state-directory settings. Set each host's `recipe` to its new checkout's absolute path and choose a fresh `deployment`, such as `tempo-v201-001`. Preserve the old frozen config for stopping the outgoing service. Existing `verified-inputs.json` seals remain usable because source inputs and store paths are unchanged.
3. Coordinate active clients. From the old controller checkout, stop only the recorded old deployment with its saved config:
   ```bash
   python3 tools/tempo.py stop --config /absolute/path/to/old-config.json
   ```
4. From the new controller checkout, run:
   ```bash
   python3 tools/tempo.py render --config config.json
   python3 tools/tempo.py preflight --resources --config config.json
   python3 tools/tempo.py start --config config.json
   ```
   Start releases the loading barrier; it does not mean the API is ready. Once loading finishes, run `health`, `native` and the bounded `smoke.py` checks from the install guide. Confirm the new guard/relay units use the new checkout. Old containers and logs stay intact.

The smoke benchmark still flags service swap as a qualification failure; it does not stop serving. Inspect the reported swap and latency before interpreting a benchmark result. Do not restart archived containers or mix old and new rank incarnations.
