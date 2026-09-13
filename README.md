# JSPARK3 v2 — Tempo

DeepSeek-V4.1 Flash on three NVIDIA DGX Sparks. Our current three-Spark daily driver, using EXL3 experts and vLLM with changes to prompt reuse, prefill scheduling and Engram reads.

Built on **tonyd2wild and Kai's Spark serving work**, and **bot-lab-21's EXL3 experts using WestWaters' Pollard method**.

Version: **v2.0.0-rc.1** · experimental · public release pending. Historical measurements are complete; fresh portable installation validation is pending. Previous daily driver: [JSPARK3 v1.1 — Cadence (GLM-5.3 Flash)](https://github.com/jakejharris/jspark3).

## Fit check

Exactly three ARM64 DGX Sparks (GB10,128GB unified memory each), local NVMe, dual-port RoCE-v2 triangle with separate IPv4 subnets at MTU9000, management network, Docker/NVIDIA runtime and cgroupv2. Controller: Linux, Python3.10+, SSH, rsync and persistent systemd user services.

Budget at least550GB free storage per host for verified model files, sparse Engram, packed rows and preparation headroom; the build host needs additional image/cache space. CPU compilation requires40GiB available RAM, and serving preflight requires100GiB available per host after the outgoing model stops. See the guide's exact worksheet before downloading.

**[Start here / Install](docs/INSTALL.md)**

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

- Code correctness passed2/4 small tasks; Work does not grade quality.
- A short request behind an existing64K prefill waited40.458seconds.
- Configured300K context, C8 capacity and native vision are not certified by the throughput cohort.
- Uncached TTFT uses warmed kernels; post-first-output rates include transport/finalization and speculative chunks.
- One-fleet historical results do not establish universal gains or independent reproduction. Fresh install/smoke remain separate release gates.

## How it works

TP3, DSpark width4, APC with retention512, solo prefill4096/shared mixed cap2048, automatic KV at GMU0.80, active cap8 and buffered264-byte Engram rows with row cache0. The package includes all15 final source overlays, pinned builds/downloads, lossless storage preparation, guarded lifecycle, bounded smoke and redacted diagnostics. It trains nothing and does not change model quantization.

[Provenance](docs/PROVENANCE.md) · [Operations](docs/OPERATIONS.md) · [Troubleshooting](docs/TROUBLESHOOTING.md) · [Research harness](benchmarks/README.md) · [Optional Pi](docs/PI.md)

Weights are downloaded separately from pinned upstream sources. The target, EXL3 experts and bundled DeepSeek DSpark drafter are identified separately in the manifest. The Hugging Face package is a serving recipe, not a loadable model or weight mirror.

Original recipe code and prose are Apache-2.0. Dependencies and weights keep their own terms. Credit to DeepSeek-AI, vLLM, turboderp, cuda-exl3, FlashInfer and NVIDIA contributors. [Full credits and license boundaries](THIRD_PARTY_NOTICES.md).
