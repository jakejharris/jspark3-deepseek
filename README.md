# JSPARK3 Tempo

DeepSeek-V4.1 Flash on three NVIDIA DGX Sparks. JSPARK3 Tempo is a named release: our DeepSeek experiment, using EXL3 experts and vLLM with changes to prompt reuse, prefill scheduling and Engram reads.

Built on **tonyd2wild and Kai's Spark serving work**, and **bot-lab-21's EXL3 experts using WestWaters' Pollard method**.

Version: **v2.0.3** · experimental · public release published. Historical measurements are complete. Validation: fresh source build: PASS; fresh install: PASS; fresh runtime smoke: PASS; operational patch: PASS. Numbered JSPARK3 releases are the main line and run GLM-5.3 Flash: [jakejharris/jspark3](https://github.com/jakejharris/jspark3).

## Fit check

Exactly three ARM64 DGX Sparks (GB10, 128 GB unified memory each), local NVMe, dual-port RoCE-v2 triangle with separate IPv4 subnets at MTU 9000, management network, Docker/NVIDIA runtime and cgroup v2. Controller: Linux, Python 3.10+, SSH, rsync and persistent systemd user services.

Budget at least 550 GB free storage per host for verified model files, sparse Engram, packed rows and preparation headroom; the build host needs additional image/cache space. CPU compilation requires 40 GiB available RAM, and serving preflight requires 100 GiB available per host after the outgoing model stops. See the guide's exact worksheet before downloading.

**[Start here / Install](docs/INSTALL.md)**

Tempo downloads its model files from [bot-lab-21's DeepSeek release](https://huggingface.co/bot-lab-21/DeepSeek-V4.1-Flash-EXL3-3.5bpw-Pollard/tree/b60193e0609147553145d1538d935925f2763c1d), including the bundled DSpark draft that helps generate answers faster. The official DeepSeek release listed in the provenance table is where this model comes from; you do not need to download it separately.

## Measured results

| Measurement | Result | Conditions |
|---|---:|---|
| 64K cold TTFT | 42.623 s | Three trials; uncached salted prompt, warmed kernels. |
| 64K repeat TTFT | 0.708 s | Three trials; same-salt committed prefix; exact repeat. |
| Prose post-first-output rate | 33.21–34.28 tok/s | One full streaming answer per task; includes transport/finalization and speculative chunks; not GPU-only decode. |
| Code post-first-output rate | 67.31–75.64 tok/s | One full streaming answer per task; includes transport/finalization and speculative chunks; not GPU-only decode. |
| C6 short-answer aggregate | 146.12 tok/s | Eight categories, 150–256-token caps, one wave per category; transport fixtures, not answer-quality grades. |
| Work C6-REPEAT | 110.49 tok/s | One three-minute task round; later tool trajectories diverge; no output-quality score. |

[All metrics, samples and conditions](docs/BENCHMARKS.md) · [Machine-readable definitions](release/benchmarks.json)

## Known limits

- Code correctness passed 2/4 small tasks; Work does not grade quality.
- A short request behind an existing 64K prefill waited 40.458 seconds.
- Configured 300K context, C8 capacity and native vision are not certified by the throughput cohort.
- Uncached TTFT uses warmed kernels; post-first-output rates include transport/finalization and speculative chunks.
- One-fleet historical results do not establish universal gains or independent reproduction. Fresh install/smoke are separately recorded release gates.

## v2.0.3 naming update

Tempo was first published as "JSPARK3 v2". It is now JSPARK3 Tempo, a named release. Numbered JSPARK3 versions belong to the main line, which runs GLM-5.3 Flash. Tempo keeps its own recipe numbers (v2.0.x), so existing tags, links and install commands keep working. Nothing that runs changed: model files, container image, build inputs, serving settings and runtime tools match v2.0.1. Existing installations need no download, rebuild or restart.

## v2.0.2 documentation update

This release only clarifies which model files Tempo downloads. The model files, container image, build inputs, serving settings and runtime tools are unchanged from v2.0.1. Existing installations need no download, rebuild or restart. Validation receipts and benchmark results keep their original dates and release identities.

## v2.0.1 operational update

Swap is now reported without automatically stopping the service. Both the local guards and fleet relay accept nonzero swap; existing low-memory and service-failure checks remain. No weights, inference settings or benchmark values changed. [Upgrade instructions](docs/OPERATIONS.md#upgrading-from-v200) · [Patch validation](evidence/swap-telemetry-20260913.json). Source-build/install evidence above remains the original rc.2 reconstruction; this patch has separate operational validation.

## How it works

TP3, DSpark width 4, APC with retention 512, solo prefill 4096/shared mixed cap 2048, automatic KV at GMU 0.80, active cap 8 and buffered 264-byte Engram rows with row cache 0. The package includes all 15 final source overlays, pinned builds/downloads, lossless storage preparation, guarded lifecycle, bounded smoke and redacted diagnostics. It trains nothing and does not change model quantization.

[Provenance](docs/PROVENANCE.md) · [Operations](docs/OPERATIONS.md) · [Troubleshooting](docs/TROUBLESHOOTING.md) · [Research harness](benchmarks/README.md) · [Optional Pi](docs/PI.md)

Tempo downloads its model files from [bot-lab-21's DeepSeek release](https://huggingface.co/bot-lab-21/DeepSeek-V4.1-Flash-EXL3-3.5bpw-Pollard/tree/b60193e0609147553145d1538d935925f2763c1d), including the bundled DSpark draft that helps generate answers faster. The official DeepSeek release listed in the provenance table is where this model comes from; you do not need to download it separately. The JSPARK3 Tempo page on Hugging Face hosts the recipe, not the model files.

Original recipe code and prose are Apache-2.0. Dependencies and weights keep their own terms. Credit to DeepSeek-AI, vLLM, turboderp, cuda-exl3, FlashInfer and NVIDIA contributors. [Full credits and license boundaries](THIRD_PARTY_NOTICES.md).
