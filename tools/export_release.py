#!/usr/bin/env python3
"""Deterministic publication snapshots; --check refuses drift."""
import argparse,json,hashlib,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def render():
 i=json.loads((ROOT/'release/identity.json').read_text()); b=json.loads((ROOT/'release/benchmarks.json').read_text());tag=i['identity']['candidate'];base='https://github.com/jakejharris/jspark3-deepseek'
 validation='; '.join(key.replace('_',' ')+': '+row['status'] for key,row in i['validation'].items())+'.'
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
# JSPARK3 Tempo

DeepSeek-V4.1 Flash on three DGX Sparks. A named JSPARK3 release: our DeepSeek experiment.

**This repository contains a serving recipe, not loadable model weights.** It is not a `from_pretrained()` model ID. Tempo downloads its model files from [bot-lab-21's DeepSeek release](https://huggingface.co/bot-lab-21/DeepSeek-V4.1-Flash-EXL3-3.5bpw-Pollard/tree/b60193e0609147553145d1538d935925f2763c1d), including the bundled DSpark draft that helps generate answers faster. The official DeepSeek release listed in the provenance table is where this model comes from; you do not need to download it separately.

Built on tonyd2wild and Kai's Spark serving work, and bot-lab-21's EXL3 experts using WestWaters' Pollard method; credit also to DeepSeek, vLLM, turboderp and cuda-exl3 contributors.

Version: {tag} · experimental · publication {i['publication_status']}.

Validation: {validation}

v2.0.3 renames the release from "JSPARK3 v2" to JSPARK3 Tempo. The model files, image, build inputs, settings and runtime tools match v2.0.1. Existing installations need no download, rebuild or restart. Validation receipts and benchmark results retain their original scope.

**[Start here / Install]({links['install']})** · [GitHub source]({links['source']}) · [Full evidence]({links['evidence']})

Exactly three GB10 Sparks with 128 GB unified memory each, local NVMe, a management network and dual-port RoCE triangle. Weights, sparse Engram and packed rows occupy approximately 400 GB per host. Budget at least 550 GB free local storage per host including preparation headroom, plus an additional 100 GB for image build/cache on the build host. See the exact fit check before downloading.

{hf_table}

'''+ '\n'.join('- '+x for x in i['limitations'])+f'\n\nRecipe code is Apache-2.0; this does not relicense upstream code or weights. [License boundaries]({base}/blob/{tag}/THIRD_PARTY_NOTICES.md). The identical release archive and SHA256SUMS accompany this card at publication.\n'
 readme=f'''# JSPARK3 Tempo

DeepSeek-V4.1 Flash on three NVIDIA DGX Sparks. JSPARK3 Tempo is a named release: our DeepSeek experiment, using EXL3 experts and vLLM with changes to prompt reuse, prefill scheduling and Engram reads.

Built on **tonyd2wild and Kai's Spark serving work**, and **bot-lab-21's EXL3 experts using WestWaters' Pollard method**.

Version: **{tag}** · experimental · public release {i['publication_status']}. Historical measurements are complete. Validation: {validation} Numbered JSPARK3 releases are the main line and run GLM-5.3 Flash: [jakejharris/jspark3](https://github.com/jakejharris/jspark3).

## Fit check

Exactly three ARM64 DGX Sparks (GB10, 128 GB unified memory each), local NVMe, dual-port RoCE-v2 triangle with separate IPv4 subnets at MTU 9000, management network, Docker/NVIDIA runtime and cgroup v2. Controller: Linux, Python 3.10+, SSH, rsync and persistent systemd user services.

Budget at least 550 GB free storage per host for verified model files, sparse Engram, packed rows and preparation headroom; the build host needs additional image/cache space. CPU compilation requires 40 GiB available RAM, and serving preflight requires 100 GiB available per host after the outgoing model stops. See the guide's exact worksheet before downloading.

**[Start here / Install](docs/INSTALL.md)**

Tempo downloads its model files from [bot-lab-21's DeepSeek release](https://huggingface.co/bot-lab-21/DeepSeek-V4.1-Flash-EXL3-3.5bpw-Pollard/tree/b60193e0609147553145d1538d935925f2763c1d), including the bundled DSpark draft that helps generate answers faster. The official DeepSeek release listed in the provenance table is where this model comes from; you do not need to download it separately.

## Measured results

{hf_table}

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
'''
 outputs={'README.md':readme,'release/summary.json':json.dumps(summary,indent=2,ensure_ascii=False)+'\n','docs/BENCHMARKS.md':md,'huggingface/README.md':hf}
 # Version references and validation wording follow identity; prose remains editable.
 for name in ['docs/INSTALL.md','docs/OPERATIONS.md','docs/PROVENANCE.md','docs/TROUBLESHOOTING.md','docs/PI.md','benchmarks/README.md']:
  body=re.sub(r'^(Version: (?:\*\*)?)v2\.\d+\.\d+(?:-rc\.\d+)?',lambda m:m[1]+tag,(ROOT/name).read_text(),flags=re.MULTILINE)
  if name=='docs/INSTALL.md':
   body=re.sub(r'(?<=--branch )v2\.\d+\.\d+(?:-rc\.\d+)?',tag,body)
   body=re.sub(r'(?<=--tag jspark3-tempo:)v2\.\d+\.\d+(?:-rc\.\d+)?',tag,body)
  if name=='docs/INSTALL.md':body=re.sub(r'^Version:.*$',f'Version: **{tag}** · experimental · {validation}',body,flags=re.MULTILINE)
  outputs[name]=body
 return outputs
def main():
 p=argparse.ArgumentParser();p.add_argument('--check',action='store_true');a=p.parse_args()
 for name,text in render().items():
  path=ROOT/name
  if a.check:
   if not path.exists() or path.read_text()!=text:raise SystemExit('Generated release drift: '+name)
  else:path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
 print('Release exports match' if a.check else 'Release exports written')
if __name__=='__main__':main()
