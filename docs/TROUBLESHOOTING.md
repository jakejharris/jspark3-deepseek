# Tempo troubleshooting

Version: v2.0.0.

## Fit and preflight

Missing route/NIC/GID or giant latency: validate both direct-leg RoCE interfaces, per-leg IPv4 subnets, MTU 9000, IPv4 GID index 3, and reciprocal routes. Check `rdma link` and `show_gids`; management reachability does not prove RDMA. Use your host networking procedure to repair fabric, then repeat preflight. A memory admission refusal while another model is loaded is expected; coordinate its stop before the resource gate. Do not weaken the floor.

Missing user manager/Docker permission: check `systemctl --user status`, `loginctl show-user "$USER" -p Linger`, and `systemd-run --user --wait --pipe sg docker -c "docker info"`. The public launcher uses `sg docker` for user managers with stale supplementary groups; the user must already belong to the Docker group. Fix host setup, then retry; do not replace persistent guards with an SSH-background process.

## Build or loading stopped

Build: the command streams each stage's compiler log. Each stage has a 7200-second timeout, a 32 GiB memory cap and single-job compilation. Check the failing stage and host memory/disk; retain completed Docker layers and retry the same public command. Missing/inaccessible pinned inputs fail closed. Never install a newer FlashInfer or vLLM to bypass a pin.

Loading: inspect exact per-rank Docker logs, guard status and cgroup OOM/swap counters. Startup normally progresses through Engram payload lines, model loading, KV profiling and graph capture. A failed rendezvous often appears as a worker timeout or NCCL error; validate the fabric before a new start. If a guard tripped, stop remaining owned ranks and investigate; do not restart archived containers.

The pinned EXL3 extension can report that `exl3_moe_glu_had_in` has no `limit` argument. The shipped overlay then clamps in Torch before invoking the kernel. The fresh install and historical L5-P both used this path on all three ranks. Rebuilding the same pinned sources will not add that argument; retain the pinned behavior for reproduction.

## Hash mismatch

A mismatch in an existing download requires a fresh fetch of that file into a new download directory; retain the suspect bytes if needed for diagnosis. A `model` partial directory is never overwritten: choose a new output directory and rerun from verified inputs. Derived config and index must be new files, because editing a hardlink in place would alter upstream source bytes. Projection sidecar extraction requires strict 206 and pinned headers; HTTP200, truncated ranges or missing projection tensors are errors, not a reason to skip those tensors.

## Packing interrupted

A `.incomplete` layer is not serving input. Preserve it and use a fresh packed destination. To reuse one fully completed layer in that new destination, first compare its header/rank/range and full payload hash to `release/packed.json`, copy/link the matching `layerN.packed` and `layerN.json`, and call the public `pack()` function only for the missing layer through `tools/pack_engram.py --layer N`. Finally run `verify_storage.py` over both layers. Packing requires its 100 GiB additional headroom and never overwrites a completed file.

## Output or cache failure

Long first response: distinguish uncached prompt prefill from kernel/model startup. Kernels warm during the smoke's initial generation. Repeated-prefix TTFT alone does not prove APC: verify exact same prefix/salt, retention512 and the observed engine hit counter. Concurrent clients contaminate global counters; drain them and retry only the affected smoke.

Garbled output, wrong count, malformed tool arguments or failed continuation: retain the local smoke JSON, verify all15 source hashes, model52 file hashes and all6 packed payloads, then inspect the exact image/native build receipt. Stop additional load on correctness/resource failure. The historical code2/4 result is a known limitation, not permission to overlook a failed exact-answer smoke.

A short request can still wait behind an existing long prefill. Historical inverse arrival took40.458seconds. The mixed2048 budget protects incumbent decoding; it does not make a later short request bypass that prefill.

## Memory pressure

4–5 GiB MemAvailable is HOLD; stop new tests and drain. Below4GiB, any service swap, OOM, rank restart or stale guard trips the owned service. Check cgroup counters separately from host-wide swap, since optional desktop/Pi processes use resources too. Do not drop all host page caches, prune Docker, kill unrelated desktops or change GMU to hide a failure. Automatic KV capacity varies by boot; configured300K is not a maximum-capacity claim.

## Optional Pi and images

First prove the core API works. Check Pi's provider URL/model ID and image capability metadata. Use a new image with randomized visible text not named in the prompt, and test direct API then the actual Pi attachment/tool-return path. A direct image API response does not prove desktop screenshots are forwarded. Image history is limited to four images per prompt. Native vision and desktop memory are separate from the historical throughput cohort. See [Pi companion](PI.md).
