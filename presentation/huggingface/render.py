#!/usr/bin/env python3
"""Render the current HF card from frozen Tempo data; never modify release archives."""
import argparse
import hashlib
import html
import json
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent

def render():
    data = json.loads((ROOT / 'release/summary.json').read_text())
    metrics = {row['id']: row for row in data['selected_metrics']}
    links = data['links']
    version = data['identity']['candidate']
    blob = links['source'].replace('/tree/', '/blob/')
    esc = html.escape
    def value(key):
        val = metrics[key]['value']
        return '–'.join(str(x) for x in val) if isinstance(val, list) else str(val)
    def badge(label, text, color, href, logo=None):
        # Shields treats doubled hyphens as literal hyphens.
        alt = esc(label + ': ' + text)
        label, text = [quote(x.replace('-', '--'), safe='') for x in (label, text)]
        src = f'https://img.shields.io/badge/{label}-{text}-{color}?style=for-the-badge'
        if logo:
            src += f'&logo={logo}&logoColor=white'
        return f'  <a href="{href}"><img src="{src}" alt="{alt}"></a>'
    badges = '\n'.join([
        badge('recipe', 'Tempo ' + version, '0a7c3f', links['release'], 'github'),
        badge('hardware', '3× DGX Spark', '76b900', links['install'], 'nvidia'),
        badge('configured context', '300,000 tokens', '1f6feb', links['evidence']),
        badge('serving', 'vLLM · TP3', '8250df', links['source']),
        badge('recipe license', 'Apache-2.0', 'd73a49', blob + '/LICENSE'),
    ])
    cards = [
        ('generation_prose', 'Prose generation', 'tok/s', 'After the first output', 'Three prose tasks; one answer per task. Includes transport and finalization.', '#f0a8a8'),
        ('generation_code', 'Code generation', 'tok/s', 'After the first output', 'Four code tasks; one answer per task. Same timing boundary as prose.', '#f2c6a6'),
        ('ttft_cold_64k', '64K uncached prompt', 'seconds', 'Time to first token', 'Three-trial median. New salted prompt with kernels already warm.', '#f2e3a6'),
        ('ttft_repeat_64k', '64K exact repeat', 'seconds', 'Time to first token', 'Three-trial median. Identical prompt with its committed prefix retained.', '#a8e6c7'),
        ('short_c6', 'Six-stream aggregate', 'tok/s', 'Short-answer screen', 'Mean across eight categories, 150–256-token caps. Includes initial wait.', '#a6d2f2'),
        ('work_c6_repeat', 'Six-agent Work repeat', 'tok/s', 'Across the full task window', 'One 180-second round. Counts unfinished output; no quality score.', '#c8b1f1'),
    ]
    tiles = []
    for key, label, unit, subtitle, detail, color in cards:
        tiles.append(f'''  <div style="background:linear-gradient(160deg,#1d1d20 0%,#0e0e10 100%);border:1px solid rgba(255,255,255,0.09);border-radius:12px;padding:18px 20px 16px;box-shadow:2px 2px 0 {color},inset 0 1px 0 rgba(255,255,255,0.05);">
    <div style="font-size:11px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#b0b0b6;">{label}</div>
    <div style="margin-top:10px;font-size:30px;line-height:1.15;font-weight:800;letter-spacing:-0.02em;color:{color};white-space:nowrap;">{value(key)}</div>
    <div style="margin-top:3px;font-size:13px;color:#c9c9ce;">{unit}</div>
    <div style="margin-top:12px;font-size:14px;font-weight:600;color:#f2f2f3;">{subtitle}</div>
    <div style="margin-top:5px;font-size:12px;line-height:1.5;color:#b0b0b6;">{detail}</div>
  </div>''')
    body = f'''---
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
{badges}
</p>

EXL3 experts and vLLM, with changes to prompt reuse, prefill scheduling and Engram reads. Built on **tonyd2wild and Kai’s serving work** and **bot-lab-21’s EXL3 experts using WestWaters’ Pollard method**.

**This page hosts the serving recipe.** The model files are downloaded separately by the install guide. Tempo downloads its model files from [bot-lab-21's DeepSeek release](https://huggingface.co/bot-lab-21/DeepSeek-V4.1-Flash-EXL3-3.5bpw-Pollard/tree/b60193e0609147553145d1538d935925f2763c1d), including the bundled DSpark draft that helps generate answers faster. The official DeepSeek release listed in the provenance table is where this model comes from; you do not need to download it separately. The 300,000-token context is configured, not certified capacity.

**[Start here / Install]({links['install']})** · [Versioned release]({links['release']}) · [Project page]({links['website']})

## v2.0.3 naming update

Tempo was first published as "JSPARK3 v2". It is now JSPARK3 Tempo, a named release. Numbered JSPARK3 versions belong to the main line, which runs GLM-5.3 Flash. The recipe keeps its v2.0.x numbers, so existing links and tags still work. Nothing that runs changed since v2.0.1.

## v2.0.2 documentation update

This release only clarifies which model files Tempo downloads. The model files, container image, build inputs, settings and runtime tools match v2.0.1. Existing installations need no download, rebuild or restart.

## v2.0.1 update

Swap usage is now reported without automatically shutting down the three-Spark service. Existing checks for low memory and actual service failures remain. No new weights or inference settings. [Upgrade instructions]({blob}/docs/OPERATIONS.md#upgrading-from-v200) · [Patch validation](./swap-telemetry-20260913.json).

## Measured on our three Sparks

Historical L5-P results, September 2026. Different workloads measure different parts of the experience.

<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(225px,1fr));gap:14px;margin:18px 0 14px;">
{chr(10).join(tiles)}
</div>

Generation rates use **(completion tokens − 1) ÷ (HTTP duration − TTFT)**. They include transport, finalization and speculative chunks; they are not GPU-only decode. The Work repeat starts fresh Pi sessions with the same initial payload and server caches retained; later tool trajectories diverge. Active cap: 8.

[Full measurements, definitions and limitations]({links['evidence']}) · [Machine-readable results](./benchmarks.json)

## Before you download

Exactly **three ARM64 GB10 Sparks with 128 GB unified memory each**, local NVMe, Docker/NVIDIA runtime and a working dual-port RoCE-v2 triangle. The controller needs Linux, Python, SSH and persistent systemd user services.

Budget **at least 550 GB free local storage per host**, including preparation headroom, plus **100 GB additional image/build cache space on the build host**. Read the [fit check and configuration worksheet]({links['install']}) first.

## What the recipe includes

- Pinned source builds, verified model downloads, all 15 final source overlays and lossless packed Engram preparation.
- TP3, DSpark width 4, prefix caching with retention 512, solo prefill 4096 and shared mixed-prefill budget 2048.
- A guarded fleet launcher, bounded API smoke, redacted diagnostics and recovery instructions.
- An optional [Pi setup]({blob}/docs/PI.md) and [Work benchmark]({blob}/benchmarks/work/README.md).

The [fresh-install receipt](./fresh-install-20260913.json) records a five-stage source build, 46 CPU checks, eight completed API requests and a separate Pi smoke with three successful tool calls. This was a new installation on the same fleet using fully rehashed cached inputs; the historical benchmark campaign was not rerun.

## Known limits

- Functional code passed **2/4** small tasks; this is not a broad quality ranking.
- A short request arriving behind an existing 64K prefill waited **40.458 seconds**.
- Configured 300K context and C8 capacity are not certified by these measurements.
- Uncached prompt timing uses warmed kernels. Work counts unfinished output and does not score quality.
- Native vision and desktop use are separate from the throughput cohort.

## Release files and credits

Recipe **{version}**, experimental. [Download the recipe archive](https://huggingface.co/jakejharris/jspark3-tempo/resolve/main/{version}.tar.gz) · [SHA256SUMS](https://huggingface.co/jakejharris/jspark3-tempo/resolve/main/SHA256SUMS) · [Release binding](./release-receipt.json).

The recipe archive is identical to the GitHub release. v2.0.3 renames Tempo; v2.0.2 clarifies the model download instructions; v2.0.1 introduced the host-side swap stop policy. The original source-build evidence, weights and historical benchmark data are unchanged; the operational patch keeps its own validation receipt.

Original Tempo code and prose are Apache-2.0; dependencies and weights keep their own terms. Credit to DeepSeek-AI, tonyd2wild, Kai, bot-lab-21, WestWaters, vLLM, turboderp, cuda-exl3, FlashInfer and NVIDIA contributors. [Full credits and license boundaries]({blob}/THIRD_PARTY_NOTICES.md).

Numbered JSPARK3 releases are the main line and run GLM-5.3 Flash: [jakejharris/jspark3](https://huggingface.co/jakejharris/jspark3). The animated three-node mark and card styling are reused from JSPARK3 v1.1 (Cadence).
'''
    return body

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    target = HERE / 'README.md'
    output = render()
    if args.check:
        assert target.read_text() == output, 'HF presentation drift'
        source = json.loads((HERE / 'assets/SOURCE.json').read_text())
        assert hashlib.sha256((HERE / 'assets/jspark3-mark.svg').read_bytes()).hexdigest() == source['sha256']
        print('PASS: current card matches frozen metrics and Cadence mark')
    else:
        target.write_text(output)
        print('Rendered current Hugging Face card')
