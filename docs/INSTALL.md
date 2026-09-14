# Install Tempo

Version: **v2.0.1** · experimental · fresh source build: PASS; fresh install: PASS; fresh runtime smoke: PASS; operational patch: PASS.

This is the single installation guide shared by GitHub, the website and Hugging Face. Commands below are run from a checkout of this exact candidate. An unpublished candidate may be supplied as a local archive; public clone URLs become usable only after publication.

## 1. Check the fit before downloading

You need exactly three NVIDIA DGX Sparks: ARM64 Linux, GB10 with 128 GB unified memory each, NVIDIA driver and NVIDIA Container Toolkit, Docker, cgroup v2, Python 3.10+ with NumPy (`python3 -c "import numpy"`), local NVMe, `rdma-core`, and systemd user services. The controller needs Linux, Python 3.10+, OpenSSH, rsync, Docker only if distributing an image locally, and an always-on systemd user manager for the guard relay. Each Spark's serving user must already belong to the `docker` group (or be root); `sg docker` activates that membership inside guard units, including user managers started before the group was granted. Enable lingering for those users through your normal host administration procedure.

Two RoCE-v2 ports on each Spark form a triangle; each direct leg uses a separate IPv4 subnet and MTU 9000. All three hosts also share a management network. RoCE fabric setup is a host prerequisite; the recipe checks configured routes and interfaces but does not rewrite networking. Verify the NIC's GID index 3 is its IPv4 RoCE-v2 GID (`show_gids`), both ports are active, and each direct peer responds to `ping -M do -s 8972 PEER_FABRIC_IP`. Configure direct routes on both ends. A successful management ping alone does not validate fabric.

```text
Controller -- SSH / management LAN --> rank 0 (HTTP :8888)
                                  --> rank 1
                                  --> rank 2
             rank 0 ===== subnet A ===== rank 1
                 \\                       //
               subnet B                subnet C
                    \\                 //
                         rank 2
```

Per host: 256.96 GB model shards plus metadata/315 MB projection sidecar, 67.59 GB allocated sparse Engram ranges, and 67.59 GB packed rows. Sparse files appear about 203 GB in `ls`; use `du` for allocated storage. Preparation requires another **100 GiB free beyond the packed output**. Budget **at least 550 GB free local storage per host**, and an additional 100 GB for image build/cache on the build host. Hardlinks avoid a second 257 GB model copy on the same filesystem. Cross-filesystem copies need that extra space. Never delete an existing daily driver's weights to make room.

Source compilation is CPU-only but needs **40 GiB MemAvailable**, with a **32 GiB build cgroup cap and no swap allowance**. Serving starts only after **100 GiB MemAvailable** on every host. This is a startup admission floor, separate from the inherited 4/5 GiB serving guard bands. Plan an exclusive service transition if another model is loaded. Stop only that service's recorded exact containers through its owner; Tempo never stops unrelated containers.

Checkpoint: hardware, disk, fabric and user services meet these requirements. Recovery: [fit/preflight failure](TROUBLESHOOTING.md#fit-and-preflight).

The controller may be colocated on rank 0; its relay is lightweight and does not require a fourth machine. Run all **Controller** commands from that Spark's persistent recipe checkout. A WSL shell without an active systemd user manager is suitable for editing/copying files but cannot own the relay. The selected controller must have unattended SSH access to all three configured aliases, including its own alias when colocated. Provision authorized keys and trusted host keys through normal SSH setup; the recipe does not copy credentials or depend on any existing fleet aliases.

Before continuing, run these checks as the serving user on each Spark:

```bash
id
getent group docker
systemctl --user show-environment
systemd-run --user --wait --pipe --collect sg docker -c 'docker version --format "{{.Server.Version}}"'
```

The final command must return the Docker server version without a password prompt. If membership is absent, have the host administrator grant it; do not restart the user manager or unrelated services to repair stale supplementary groups. Guards run with `Restart=no`, `MemoryMax=128M`, and `MemorySwapMax=0`; the relay treats stale or missing guard samples as a fleet-stop condition.

## 2. Get the candidate and fill one worksheet

**Controller**, an empty directory:

```bash
git clone --branch v2.0.1 https://github.com/jakejharris/jspark3-deepseek.git
cd jspark3-deepseek
python3 tools/release_check.py
cp recipe/config.example.json config.json
```

Edit `config.json`; it is ignored by Git and never included in diagnostics. Set these values once:

| Field | Meaning |
|---|---|
| `deployment` | Fresh `tempo-...` name for every start; no reused barrier/cache namespace |
| `image_id` | Exact `sha256:...` output of the build, identical on all ranks |
| `image_provenance` | `source-build`; `verified-historical-cache` is only for a declared reproduction using the exact archived image |
| `api_port`, `master_port`, `endpoint` | HTTP port, distributed rendezvous port, controller-reachable rank-0 HTTP root (no `/v1`) |
| `state_dir` | Absolute writable controller directory for ownership, relay and smoke records |
| each host's `rank`, `ssh`, `ip` | Rank 0/1/2, trusted SSH alias, management IPv4 address |
| `interface`, `hca`, `fabric_subnets` | Management interface, two RDMA device names, the two local direct-leg CIDRs |
| `recipe` | Absolute path where this checkout will live on that Spark |
| `model`, `engram`, `packed`, `work` | Absolute local data directories and writable runtime directory |

The sample uses documentation IPs, not a working fleet. Keep source paths, prepared model paths and work paths separate. All serving defaults (TP3, graphs, DSpark, fabric environment) are fixed in `release/runtime.json`; changing them creates a different runtime requiring a new candidate/receipt. Context 300000 is configured, not a certification.

**Controller:** verify each worksheet alias, including the local rank when colocated, with `ssh -o BatchMode=yes -o ConnectTimeout=10 ALIAS true`. These aliases must resolve from the controller itself, not only from a workstation. Keep the controller checkout and `state_dir` persistent across SSH logout; lingering keeps its user services alive.

**Controller:** copy this checkout and your config to each declared `recipe` directory. Example, repeat with your own aliases and paths:

```bash
rsync -a --exclude .git --exclude config.json --exclude __pycache__ ./ spark-a:/opt/tempo/
scp config.json spark-a:/opt/tempo/config.json
```

The remote user must own that directory; create it with your normal host setup if `/opt` is not writable. Commands below run **on each Spark, working directory its configured `recipe`**, unless marked otherwise.

Checkpoint: `python3 tools/release_check.py` passes on all three hosts. Recovery: [hash mismatch](TROUBLESHOOTING.md#hash-mismatch).

## 3. Download and build the pinned image

**Build Spark**, from its recipe checkout, with the serving model stopped if required by the memory fit check:

```bash
python3 tools/fetch_sources.py --destination /srv/tempo/build-inputs
python3 tools/build_image.py --inputs /srv/tempo/build-inputs --work /srv/tempo/image-build --tag jspark3-tempo:v2.0.1
```

Use your chosen absolute paths. The downloader verifies all eight public inputs and rehashes existing files before reuse. It never substitutes a newer revision. Partial archive downloads are restarted; completed archives are retained. Build stages have no network access; base-image pull happens first. Expected stages: vLLM stable extension → FlashInfer → MXFP8 JIT → sparse MLA JIT → cuda-exl3 → 15 final source overlays. Compiler output is retained in `/receipts/stageN.build.log` in each successful image layer. Docker build output marks each checkpoint. A failed stage stops; retrying the same unchanged build reuses completed Docker layers. Changes to the stage wrapper or compiler inputs invalidate those layers and require recompilation.

The base is a pinned public ARM64 image digest. A fresh build may have a new image ID and native binary hashes; record them rather than claiming bit-identical compiled output. Exact Python source overlays must match. See [provenance](PROVENANCE.md).

Read `/srv/tempo/image-build/image.json`, enter its `image_id` in the shared config, then export the same built image to the other Sparks. **Build Spark:** `docker save IMAGE_ID -o /srv/tempo/tempo-image.tar`; **controller:** copy that file using `scp`, record `sha256sum`, and copy it to the other hosts. **Each receiving Spark:** verify the same tar SHA256, then `docker load -i /srv/tempo/tempo-image.tar` and `docker image inspect --format '{{.Id}}' IMAGE_ID`. All three IDs must equal the configured ID. Redistribute the updated config.

Checkpoint: same exact image ID on all ranks, source-build label present. Recovery: [build stopped](TROUBLESHOOTING.md#build-or-loading-stopped). Do not infer native health from build success.

## 4. Prepare model files without changing quantization

Tempo downloads its model files from [bot-lab-21's DeepSeek release](https://huggingface.co/bot-lab-21/DeepSeek-V4.1-Flash-EXL3-3.5bpw-Pollard/tree/b60193e0609147553145d1538d935925f2763c1d), including the bundled DSpark draft that helps generate answers faster. The official DeepSeek release listed in the provenance table is where this model comes from; you do not need to download it separately.

**Each Spark**, in its recipe directory (substitute your paths):

```bash
python3 tools/prepare_weights.py download --source /srv/tempo/downloads
python3 tools/prepare_weights.py model --source /srv/tempo/downloads --model /srv/tempo/model-tp3
```

This downloads 46 shards plus five metadata files from the exact upstream EXL3 revision. Downloads may also be copied from another host; every file is still fully SHA256 verified. Download responses can be large: allow time and watch disk use. Interrupted shard downloads restart that shard, retaining all completed verified files. No Hugging Face token is required for the currently public inputs; inaccessible inputs are an explicit failure.

The `model` step hardlinks/copies the unchanged shards, creates a new TP3 config (64 real heads / 8 output groups represented as 72 / 9 with virtual padding), extracts eight unchanged projection tensors from pinned HTTP ranges, and filters only the disk-resolved Engram table keys out of the inference index. It validates the resulting **52 files against the historical L5-P hashes**. It never writes to the source download files. DSpark is bundled in this same snapshot, not a separately fetched DFlash model.

Checkpoint: `PASS: derived model matches all 52 historical files`. Recovery: preserve a partial model directory, select a fresh output name and update config; [hash or projection failure](TROUBLESHOOTING.md#hash-mismatch). Do not manually drop projection tensors to make loading proceed.

## 5. Prepare the per-rank Engram store and packed rows

**Each Spark**, set `--rank` to its own rank (0, 1 or 2):

```bash
python3 tools/range_store.py --config /srv/tempo/downloads/config.json --index /srv/tempo/downloads/model.safetensors.index.json --shards release/shards.json --rank 0 --dst /srv/tempo/engram
python3 tools/pack_engram.py --source /srv/tempo/engram --destination /srv/tempo/engram-packed
python3 tools/verify_storage.py --rank 0 --engram /srv/tempo/engram --packed /srv/tempo/engram-packed
```

The sparse downloader uses strict HTTP 206 ranges, exact content ranges and bounded buffers. Add `--resume` only for an interrupted store: it rechecks every journaled chunk. It does not fetch all of shards 47/48 and therefore does not claim their whole-file LFS hashes. Instead, the four complete owned ranges are verified against the release's frozen range hashes. A completed store is never silently overwritten.

Packing interleaves each row's **256 weight bytes + 8 scale bytes** behind a 4096-byte JSON header. There is no dequantization, requantization or training. Each layer is independently read back and deinterleaved, and its complete payload must match `release/packed.json`. Header paths and elapsed time vary by host, so the immutable checksum covers the payload, not the host-specific header. Row cache is zero; buffered packed reads are the chosen mode.

Packing has no mid-layer resume. On interruption, retain the `.incomplete` file for inspection and choose a fresh packed destination. Existing completed layers can be validated and reused through the documented [packing recovery](TROUBLESHOOTING.md#packing-interrupted). Do not delete a source store or reuse unverified partial bytes.

Checkpoint: two complete packed payload hashes pass per rank. Then seal the verified inputs on each host:

```bash
python3 tools/seal_inputs.py --config config.json --rank 0
```

This fully rechecks the 52 model files and all sparse/packed ranges, then records size/inode/mtime so startup can detect changed inputs without repeating hours of hashing. The seal is host-local under `work`; it is not portable proof by itself. Reusing preexisting downloads or a daily-driver store requires this exact full check. Sealing can read hundreds of GB; run it before the transition window.

## 6. Preflight and controlled start

**Controller**, recipe checkout:

```bash
python3 tools/tempo.py preflight --config config.json
python3 tools/tempo.py render --config config.json > rendered-launch.json
```

Inspect the rendered commands and compare model/image/configuration to `release/manifest.json`. Structural preflight checks hashes/seals, directories, image identity, RDMA devices/routes and systemd access. It does not claim enough free memory while an outgoing model still serves.

Record the outgoing service's exact IDs/start times and recovery command with its owner, drain its clients, and stop that service through its own lifecycle. Tempo will not stop it for you. Keep its containers and all data for rollback. Desktop/Pi containers are outside this change.

```bash
python3 tools/tempo.py preflight --config config.json --resources
python3 tools/tempo.py start --config config.json
```

Expected: ranks created in order 2,1,0, exact ownership saved, per-host guards running, controller relay running, then guarded release in order 2,1,0. No GPU loading occurs before guard readiness. Start returns after barrier release; loading is not yet health. Each new start needs a fresh `deployment` name.

```bash
python3 tools/tempo.py status --config config.json
python3 tools/tempo.py health --config config.json
python3 tools/tempo.py native --config config.json
```

Use each exact CID from the controller's `owned.json` with `docker logs --tail 80 CID` on that host to watch progress. Expected loading signals: `JSPARK_PACKED_ENGRAM` with the correct layer/range/payload, model loading, automatic KV profiling and CUDA graph capture, then HTTP health. Initial loading/capture can take many minutes; the startup barrier timeout and backend-ready timeout are 3600 seconds. Inspect logs if progress stops; do not repeatedly restart a partially loaded model.

Checkpoint: all guards healthy, native source hashes pass, `/health` succeeds and `/v1/models` contains `deepseek-v4.1-flash`. Recovery: [loading/fabric/memory](TROUBLESHOOTING.md).

## 7. First request and useful smoke

**Controller**, replace the sample endpoint with yours:

```bash
curl http://192.0.2.11:8888/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"deepseek-v4.1-flash","messages":[{"role":"user","content":"What is 2 + 2?"}],"max_tokens":32,"temperature":0,"chat_template_kwargs":{"thinking":false}}'
python3 tools/smoke.py --config config.json --output smoke.json
```

Expected first answer: 4. The smoke checks complete SSE, count-to-20 correctness, an uncached salted prefix after a generation warmup, its exact repeat and engine-observed reuse, a parsed tool call and deterministic continuation. Each request is capped at 192 output tokens and a 180-second read/wall budget. Public test prompts and request parameters are recorded in the local JSON. It reports TTFT, output/full-HTTP tok/s, and post-first-output tok/s separately. No universal speed threshold is imposed. Run with other clients drained: extra requests invalidate global cache-counter attribution.

The JSON binds deployment IDs/image and manifest checksum, includes before/after host/cgroup memory observations, and distinguishes HTTP readiness from application correctness. These are local validation files, not automatically public diagnostics. Historical headline metrics remain in [Benchmarks](BENCHMARKS.md).

Checkpoint: `Tempo smoke PASS`, no service swap/OOM/restart, at least 5 GiB observed host headroom. Recovery: [bad output or reuse](TROUBLESHOOTING.md#output-or-cache-failure).

## 8. Stop, restart, recover

```bash
python3 tools/tempo.py stop --config config.json
```

This stops only the recorded Tempo ranks and their guard units, retaining containers, logs, model files, packing and caches. Restart means a fresh `deployment` name followed by preflight/start/health/native/smoke, never `docker restart` on an old incarnation. If a candidate fails, stop it and restore the previously recorded service using its own valid recovery procedure; do not mix ranks or images. [Operations](OPERATIONS.md) explains guard bands, failure ownership and diagnostics. [Optional Pi](PI.md) is a separate companion, outside core throughput conditions.
