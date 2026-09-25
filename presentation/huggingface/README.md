---
license: apache-2.0
tags:
  - serving-recipe
  - dgx-spark
  - deepseek
---

<div style="display:flex;align-items:center;gap:14px;margin:6px 0 12px;">
<img src="https://huggingface.co/jakejharris/jspark3-tempo/resolve/main/assets/jspark3-mark.svg" width="48" height="48" alt="JSPARK3: three connected nodes" style="flex-shrink:0;">
<h1 style="margin:0;font-size:2em;line-height:1.15;letter-spacing:-0.02em;">JSPARK3 <span style="font-weight:500;color:#8b8b90;">Tempo</span></h1>
</div>

**DeepSeek-V4.1 Flash on three DGX Sparks. A named JSPARK3 release: our DeepSeek experiment.**

<p>
  <a href="https://github.com/jakejharris/jspark3-deepseek/releases/tag/v2.0.3"><img src="https://img.shields.io/badge/recipe-Tempo%20v2.0.3-0a7c3f?style=for-the-badge&logo=github&logoColor=white" alt="recipe: Tempo v2.0.3"></a>
  <a href="https://github.com/jakejharris/jspark3-deepseek/blob/v2.0.3/docs/INSTALL.md"><img src="https://img.shields.io/badge/hardware-3%C3%97%20DGX%20Spark-76b900?style=for-the-badge&logo=nvidia&logoColor=white" alt="hardware: 3× DGX Spark"></a>
  <a href="https://github.com/jakejharris/jspark3-deepseek/blob/v2.0.3/docs/BENCHMARKS.md"><img src="https://img.shields.io/badge/configured%20context-300%2C000%20tokens-1f6feb?style=for-the-badge" alt="configured context: 300,000 tokens"></a>
  <a href="https://github.com/jakejharris/jspark3-deepseek/tree/v2.0.3"><img src="https://img.shields.io/badge/serving-vLLM%20%C2%B7%20TP3-8250df?style=for-the-badge" alt="serving: vLLM · TP3"></a>
  <a href="https://github.com/jakejharris/jspark3-deepseek/blob/v2.0.3/LICENSE"><img src="https://img.shields.io/badge/recipe%20license-Apache--2.0-d73a49?style=for-the-badge" alt="recipe license: Apache-2.0"></a>
</p>

EXL3 experts and vLLM, with changes to prompt reuse, prefill scheduling and Engram reads. Built on **tonyd2wild and Kai’s serving work** and **bot-lab-21’s EXL3 experts using WestWaters’ Pollard method**.

**This page hosts the serving recipe.** The model files are downloaded separately by the install guide. Tempo downloads its model files from [bot-lab-21's DeepSeek release](https://huggingface.co/bot-lab-21/DeepSeek-V4.1-Flash-EXL3-3.5bpw-Pollard/tree/b60193e0609147553145d1538d935925f2763c1d), including the bundled DSpark draft that helps generate answers faster. The official DeepSeek release listed in the provenance table is where this model comes from; you do not need to download it separately. The 300,000-token context is configured, not certified capacity.

**[Start here / Install](https://github.com/jakejharris/jspark3-deepseek/blob/v2.0.3/docs/INSTALL.md)** · [Versioned release](https://github.com/jakejharris/jspark3-deepseek/releases/tag/v2.0.3) · [Project page](https://jakejh.com/jspark3/deepseek/)

## v2.0.3 naming update

Tempo was first published as "JSPARK3 v2". It is now JSPARK3 Tempo, a named release. Numbered JSPARK3 versions belong to the main line, which runs GLM-5.3 Flash. The recipe keeps its v2.0.x numbers, so existing links and tags still work. Nothing that runs changed since v2.0.1.

## v2.0.2 documentation update

This release only clarifies which model files Tempo downloads. The model files, container image, build inputs, settings and runtime tools match v2.0.1. Existing installations need no download, rebuild or restart.

## v2.0.1 update

Swap usage is now reported without automatically shutting down the three-Spark service. Existing checks for low memory and actual service failures remain. No new weights or inference settings. [Upgrade instructions](https://github.com/jakejharris/jspark3-deepseek/blob/v2.0.3/docs/OPERATIONS.md#upgrading-from-v200) · [Patch validation](./swap-telemetry-20260913.json).

## Measured on our three Sparks

Historical L5-P results, September 2026. Different workloads measure different parts of the experience.

<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(225px,1fr));gap:14px;margin:18px 0 14px;">
  <div style="background:linear-gradient(160deg,#1d1d20 0%,#0e0e10 100%);border:1px solid rgba(255,255,255,0.09);border-radius:12px;padding:18px 20px 16px;box-shadow:2px 2px 0 #f0a8a8,inset 0 1px 0 rgba(255,255,255,0.05);">
    <div style="font-size:11px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#b0b0b6;">Prose generation</div>
    <div style="margin-top:10px;font-size:30px;line-height:1.15;font-weight:800;letter-spacing:-0.02em;color:#f0a8a8;white-space:nowrap;">33.21–34.28</div>
    <div style="margin-top:3px;font-size:13px;color:#c9c9ce;">tok/s</div>
    <div style="margin-top:12px;font-size:14px;font-weight:600;color:#f2f2f3;">After the first output</div>
    <div style="margin-top:5px;font-size:12px;line-height:1.5;color:#b0b0b6;">Three prose tasks; one answer per task. Includes transport and finalization.</div>
  </div>
  <div style="background:linear-gradient(160deg,#1d1d20 0%,#0e0e10 100%);border:1px solid rgba(255,255,255,0.09);border-radius:12px;padding:18px 20px 16px;box-shadow:2px 2px 0 #f2c6a6,inset 0 1px 0 rgba(255,255,255,0.05);">
    <div style="font-size:11px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#b0b0b6;">Code generation</div>
    <div style="margin-top:10px;font-size:30px;line-height:1.15;font-weight:800;letter-spacing:-0.02em;color:#f2c6a6;white-space:nowrap;">67.31–75.64</div>
    <div style="margin-top:3px;font-size:13px;color:#c9c9ce;">tok/s</div>
    <div style="margin-top:12px;font-size:14px;font-weight:600;color:#f2f2f3;">After the first output</div>
    <div style="margin-top:5px;font-size:12px;line-height:1.5;color:#b0b0b6;">Four code tasks; one answer per task. Same timing boundary as prose.</div>
  </div>
  <div style="background:linear-gradient(160deg,#1d1d20 0%,#0e0e10 100%);border:1px solid rgba(255,255,255,0.09);border-radius:12px;padding:18px 20px 16px;box-shadow:2px 2px 0 #f2e3a6,inset 0 1px 0 rgba(255,255,255,0.05);">
    <div style="font-size:11px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#b0b0b6;">64K uncached prompt</div>
    <div style="margin-top:10px;font-size:30px;line-height:1.15;font-weight:800;letter-spacing:-0.02em;color:#f2e3a6;white-space:nowrap;">42.623</div>
    <div style="margin-top:3px;font-size:13px;color:#c9c9ce;">seconds</div>
    <div style="margin-top:12px;font-size:14px;font-weight:600;color:#f2f2f3;">Time to first token</div>
    <div style="margin-top:5px;font-size:12px;line-height:1.5;color:#b0b0b6;">Three-trial median. New salted prompt with kernels already warm.</div>
  </div>
  <div style="background:linear-gradient(160deg,#1d1d20 0%,#0e0e10 100%);border:1px solid rgba(255,255,255,0.09);border-radius:12px;padding:18px 20px 16px;box-shadow:2px 2px 0 #a8e6c7,inset 0 1px 0 rgba(255,255,255,0.05);">
    <div style="font-size:11px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#b0b0b6;">64K exact repeat</div>
    <div style="margin-top:10px;font-size:30px;line-height:1.15;font-weight:800;letter-spacing:-0.02em;color:#a8e6c7;white-space:nowrap;">0.708</div>
    <div style="margin-top:3px;font-size:13px;color:#c9c9ce;">seconds</div>
    <div style="margin-top:12px;font-size:14px;font-weight:600;color:#f2f2f3;">Time to first token</div>
    <div style="margin-top:5px;font-size:12px;line-height:1.5;color:#b0b0b6;">Three-trial median. Identical prompt with its committed prefix retained.</div>
  </div>
  <div style="background:linear-gradient(160deg,#1d1d20 0%,#0e0e10 100%);border:1px solid rgba(255,255,255,0.09);border-radius:12px;padding:18px 20px 16px;box-shadow:2px 2px 0 #a6d2f2,inset 0 1px 0 rgba(255,255,255,0.05);">
    <div style="font-size:11px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#b0b0b6;">Six-stream aggregate</div>
    <div style="margin-top:10px;font-size:30px;line-height:1.15;font-weight:800;letter-spacing:-0.02em;color:#a6d2f2;white-space:nowrap;">146.12</div>
    <div style="margin-top:3px;font-size:13px;color:#c9c9ce;">tok/s</div>
    <div style="margin-top:12px;font-size:14px;font-weight:600;color:#f2f2f3;">Short-answer screen</div>
    <div style="margin-top:5px;font-size:12px;line-height:1.5;color:#b0b0b6;">Mean across eight categories, 150–256-token caps. Includes initial wait.</div>
  </div>
  <div style="background:linear-gradient(160deg,#1d1d20 0%,#0e0e10 100%);border:1px solid rgba(255,255,255,0.09);border-radius:12px;padding:18px 20px 16px;box-shadow:2px 2px 0 #c8b1f1,inset 0 1px 0 rgba(255,255,255,0.05);">
    <div style="font-size:11px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#b0b0b6;">Six-agent Work repeat</div>
    <div style="margin-top:10px;font-size:30px;line-height:1.15;font-weight:800;letter-spacing:-0.02em;color:#c8b1f1;white-space:nowrap;">110.49</div>
    <div style="margin-top:3px;font-size:13px;color:#c9c9ce;">tok/s</div>
    <div style="margin-top:12px;font-size:14px;font-weight:600;color:#f2f2f3;">Across the full task window</div>
    <div style="margin-top:5px;font-size:12px;line-height:1.5;color:#b0b0b6;">One 180-second round. Counts unfinished output; no quality score.</div>
  </div>
</div>

Generation rates use **(completion tokens − 1) ÷ (HTTP duration − TTFT)**. They include transport, finalization and speculative chunks; they are not GPU-only decode. The Work repeat starts fresh Pi sessions with the same initial payload and server caches retained; later tool trajectories diverge. Active cap: 8.

[Full measurements, definitions and limitations](https://github.com/jakejharris/jspark3-deepseek/blob/v2.0.3/docs/BENCHMARKS.md) · [Machine-readable results](./benchmarks.json)

## Before you download

Exactly **three ARM64 GB10 Sparks with 128 GB unified memory each**, local NVMe, Docker/NVIDIA runtime and a working dual-port RoCE-v2 triangle. The controller needs Linux, Python, SSH and persistent systemd user services.

Budget **at least 550 GB free local storage per host**, including preparation headroom, plus **100 GB additional image/build cache space on the build host**. Read the [fit check and configuration worksheet](https://github.com/jakejharris/jspark3-deepseek/blob/v2.0.3/docs/INSTALL.md) first.

## What the recipe includes

- Pinned source builds, verified model downloads, all 15 final source overlays and lossless packed Engram preparation.
- TP3, DSpark width 4, prefix caching with retention 512, solo prefill 4096 and shared mixed-prefill budget 2048.
- A guarded fleet launcher, bounded API smoke, redacted diagnostics and recovery instructions.
- An optional [Pi setup](https://github.com/jakejharris/jspark3-deepseek/blob/v2.0.3/docs/PI.md) and [Work benchmark](https://github.com/jakejharris/jspark3-deepseek/blob/v2.0.3/benchmarks/work/README.md).

The [fresh-install receipt](./fresh-install-20260913.json) records a five-stage source build, 46 CPU checks, eight completed API requests and a separate Pi smoke with three successful tool calls. This was a new installation on the same fleet using fully rehashed cached inputs; the historical benchmark campaign was not rerun.

## Known limits

- Functional code passed **2/4** small tasks; this is not a broad quality ranking.
- A short request arriving behind an existing 64K prefill waited **40.458 seconds**.
- Configured 300K context and C8 capacity are not certified by these measurements.
- Uncached prompt timing uses warmed kernels. Work counts unfinished output and does not score quality.
- Native vision and desktop use are separate from the throughput cohort.

## Release files and credits

Recipe **v2.0.3**, experimental. [Download the recipe archive](https://huggingface.co/jakejharris/jspark3-tempo/resolve/main/v2.0.3.tar.gz) · [SHA256SUMS](https://huggingface.co/jakejharris/jspark3-tempo/resolve/main/SHA256SUMS) · [Release binding](./release-receipt.json).

The recipe archive is identical to the GitHub release. v2.0.3 renames Tempo; v2.0.2 clarifies the model download instructions; v2.0.1 introduced the host-side swap stop policy. The original source-build evidence, weights and historical benchmark data are unchanged; the operational patch keeps its own validation receipt.

Original Tempo code and prose are Apache-2.0; dependencies and weights keep their own terms. Credit to DeepSeek-AI, tonyd2wild, Kai, bot-lab-21, WestWaters, vLLM, turboderp, cuda-exl3, FlashInfer and NVIDIA contributors. [Full credits and license boundaries](https://github.com/jakejharris/jspark3-deepseek/blob/v2.0.3/THIRD_PARTY_NOTICES.md).

Numbered JSPARK3 releases are the main line and run GLM-5.3 Flash: [jakejharris/jspark3](https://huggingface.co/jakejharris/jspark3). The animated three-node mark and card styling are reused from JSPARK3 v1.1 (Cadence).
