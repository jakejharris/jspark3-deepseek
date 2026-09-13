#!/usr/bin/env python3
"""Deterministic publication snapshots; --check refuses drift."""
import argparse,json,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def render():
 i=json.loads((ROOT/'release/identity.json').read_text()); b=json.loads((ROOT/'release/benchmarks.json').read_text());tag=i['identity']['candidate'];base='https://github.com/jakejharris/jspark3-deepseek'
 links=dict(install=f'{base}/blob/{tag}/docs/INSTALL.md',source=f'{base}/tree/{tag}',release=f'{base}/releases/tag/{tag}',evidence=f'{base}/blob/{tag}/docs/BENCHMARKS.md',huggingface='https://huggingface.co/jakejharris/jspark3-tempo',website='https://jakejh.com/jspark3/deepseek/')
 summary=dict(**i,links=links,selected_metrics=[{k:m[k] for k in ['id','label','value','unit','conditions','estimator','samples','cohort','cache_state','timing_boundary','concurrency','active_cap']} for m in b['metrics']],benchmarks_sha256=hashlib.sha256((ROOT/'release/benchmarks.json').read_bytes()).hexdigest())
 rows=['| Measurement | Result | Conditions |','|---|---:|---|']
 for m in b['metrics']:
  value='–'.join(map(str,m['value'])) if isinstance(m['value'],list) else str(m['value'])
  rows.append(f"| {m['label']} | {value} {m['unit']} | {m['conditions']} |")
 table='\n'.join(rows)
 selected={'ttft_cold_64k','ttft_repeat_64k','generation_prose','generation_code','short_c6','work_c6_repeat'}
 hf_table='\n'.join(rows[:2]+[row for row,m in zip(rows[2:],b['metrics']) if m['id'] in selected])
 md=f'# Tempo measured results\n\nVersion: {tag} · historical L5-P cohort, September 13, 2026.\n\n'+table+'\n\nMachine definitions and source hashes: [benchmarks.json](../release/benchmarks.json). Selected original observations: [evidence](../evidence/selected-measurements.json). These are historical measurements, not a fresh installation receipt.\n\n'+ '\n'.join('- '+x for x in i['limitations'])+'\n'
 hf=f'''---
license: apache-2.0
tags:
  - serving-recipe
  - dgx-spark
  - deepseek
---
# JSPARK3 v2 — Tempo

DeepSeek-V4.1 Flash on three DGX Sparks. Our current three-Spark daily driver.

**This repository contains a serving recipe, not loadable model weights.** It is not a `from_pretrained()` model ID. Download the pinned target, EXL3 experts and bundled DSpark draft from their upstream source as described in the guide. No weights are mirrored here.

Built on tonyd2wild and Kai's Spark serving work, and bot-lab-21's EXL3 experts using WestWaters' Pollard method; credit also to DeepSeek, vLLM, turboderp and cuda-exl3 contributors.

Version: {tag} · experimental · publication {i['publication_status']}.

**[Start here / Install]({links['install']})** · [GitHub source]({links['source']}) · [Full evidence]({links['evidence']})

Exactly three GB10 Sparks with 128 GB unified memory each, local NVMe, a management network and dual-port RoCE triangle. Allow approximately 400 GB per host for weights, sparse Engram and packed rows, plus build/cache space. See the exact fit check before downloading.

{hf_table}

'''+ '\n'.join('- '+x for x in i['limitations'])+f'\n\nRecipe code is Apache-2.0; this does not relicense upstream code or weights. [License boundaries]({base}/blob/{tag}/THIRD_PARTY_NOTICES.md). The identical release archive and SHA256SUMS accompany this card at publication.\n'
 readme=f'''# JSPARK3 v2 — Tempo

DeepSeek-V4.1 Flash on three NVIDIA DGX Sparks. Our current three-Spark daily driver, using EXL3 experts and vLLM with changes to prompt reuse, prefill scheduling and Engram reads.

Built on **tonyd2wild and Kai's Spark serving work**, and **bot-lab-21's EXL3 experts using WestWaters' Pollard method**.

Version: **{tag}** · experimental · public release {i['publication_status']}. Historical measurements are complete; fresh portable installation validation is pending. Previous daily driver: [JSPARK3 v1.1 — Cadence (GLM-5.3 Flash)](https://github.com/jakejharris/jspark3).

## Fit check

Exactly three ARM64 DGX Sparks (GB10,128GB unified memory each), local NVMe, dual-port RoCE-v2 triangle with separate IPv4 subnets at MTU9000, management network, Docker/NVIDIA runtime and cgroupv2. Controller: Linux, Python3.10+, SSH, rsync and persistent systemd user services.

Budget at least550GB free storage per host for verified model files, sparse Engram, packed rows and preparation headroom; the build host needs additional image/cache space. CPU compilation requires40GiB available RAM, and serving preflight requires100GiB available per host after the outgoing model stops. See the guide's exact worksheet before downloading.

**[Start here / Install](docs/INSTALL.md)**

## Measured results

{hf_table}

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
'''
 return {'README.md':readme,'release/summary.json':json.dumps(summary,indent=2,ensure_ascii=False)+'\n','docs/BENCHMARKS.md':md,'huggingface/README.md':hf}
def main():
 p=argparse.ArgumentParser();p.add_argument('--check',action='store_true');a=p.parse_args()
 for name,text in render().items():
  path=ROOT/name
  if a.check:
   if not path.exists() or path.read_text()!=text:raise SystemExit('Generated release drift: '+name)
  else:path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
 print('Release exports match' if a.check else 'Release exports written')
if __name__=='__main__':main()
