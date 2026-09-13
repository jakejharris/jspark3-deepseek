# Tempo operations

Version: v2.0.0-rc.1.

The portable lifecycle creates exactly three labeled containers and records their full IDs and fresh Docker `StartedAt`. Mutations resolve those exact IDs, verify the deployment label and incarnation, and never use broad Docker name patterns. It retains stopped containers, caches and all model/user data. A fresh start needs a fresh namespace because the entrypoint release file is single-use.

The archived L5-P service used an EXL3 guard. Its intended behavior is retained: per-rank persistent guards sample host MemAvailable and service cgroup memory/swap/events; **below 4 GiB available**, any service swap, new OOM event, rank death/restart/incarnation change, or telemetry blindness causes an exact-container emergency stop. **4–5 GiB is HOLD**: stop new tests and drain work; this is not a promotion band. At least 5 GiB observed throughout a test is required for its memory pass. Host swap belonging to other processes is not automatically attributed to this service.

The controller's persistent systemd user relay checks all three local guard heartbeats. It propagates a trip to peers and stops owned ranks if a peer fails, identity drifts or heartbeats become stale (15 seconds). Rank guards remain local if the controller is briefly disconnected; keep the controller and relay running for coupling. A controller outage is not a validated operating mode: restore the relay or stop the owned service through the saved configuration. Systemd must be configured to restart failed guard/relay processes; both units use `Restart=on-failure`. No Mia guard is installed or reinstated.

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
