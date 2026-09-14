# Tempo provenance and reproduction scope

Version: v2.0.2.

The final L5-P runtime is the source of this release. `release/patches.json` lists15 final file overlays, each with its pinned upstream preimage SHA256 (or explicit expected absence) and final SHA256. All15 public-source preimages were checked against the pinned vLLM/cuda-exl3 archives. All15 final files also match the archived serving implementation. `apply_patches.py` verifies the entire preimage set before writing any overlay. There is no need for a private parent boot or incremental patch chain.

The archived deployment manifest's SHA256 is `3556113254de788ee188f1cc1f929438b30b79b58e0d2299f3c92edf7578d1c5`. Its private host/container records are not distributed. Historic runtime image config ID was `sha256:925c4232f7f1a8c799445b0901dc93c1b1946338bb97fc4598ff84440552d8cc`. That ID is a **local Docker image ID, not a downloadable registry digest**. The public installation path builds from the accessible pinned base digest and all eight pinned source/tool archives. A declared cached historical image can support a reconstruction check; it does not prove that the public image build succeeded.

The vLLM input now uses the public codeload tarball for the identical commit rather than an old locally generated git archive. Archive bytes and leading-directory format differ; source file preimages match. Build stages retain the original stable-extension, FlashInfer/JIT and cuda-exl3 build semantics, with bounded CPU memory and live logs. A fresh image ID and native binary hashes are expected to be recorded, not silently substituted for the old compiled hashes. Source build and native runtime validation are separate gates.

| Component | Public pin |
|---|---|
| Base target and bundled DSpark drafter | DeepSeek-V4.1-Flash `2bc89ac599031fa673cab993f1df02fc4a98c673` |
| EXL3 experts and unchanged target/draft components used by this recipe | bot-lab-21/DeepSeek-V4.1-Flash-EXL3-3.5bpw-Pollard `b60193e0609147553145d1538d935925f2763c1d` |
| Tony/Kai source lineage | tonyd2wild/DeepSeek-V4.1-Flash-vLLM-DGX-Spark `c2c1bb7ee7d2f5faee76c07fd9fc9f27e3a20bc2` |
| vLLM | `e47aa780bccf59f59dfa2cbb18e17a10b4fe69ba` |
| cuda-exl3 | `6a1ffc34866e23f484574ce1922a8bca93eb33b2` |
| Full image/build inputs | [sources.json](../release/sources.json) |

## Where the model files come from

Tempo downloads its model files from [bot-lab-21's DeepSeek release](https://huggingface.co/bot-lab-21/DeepSeek-V4.1-Flash-EXL3-3.5bpw-Pollard/tree/b60193e0609147553145d1538d935925f2763c1d), including the bundled DSpark draft that helps generate answers faster. The official DeepSeek release listed in the provenance table is where this model comes from; you do not need to download it separately.

| Download | Repository and revision |
|---|---|
| All model downloads, including the bundled draft | [bot-lab-21/DeepSeek-V4.1-Flash-EXL3-3.5bpw-Pollard](https://huggingface.co/bot-lab-21/DeepSeek-V4.1-Flash-EXL3-3.5bpw-Pollard/tree/b60193e0609147553145d1538d935925f2763c1d), [b60193e](https://huggingface.co/bot-lab-21/DeepSeek-V4.1-Flash-EXL3-3.5bpw-Pollard/tree/b60193e0609147553145d1538d935925f2763c1d) |
| Original DeepSeek model, recorded for credit and verification; no separate download | [deepseek-ai/DeepSeek-V4.1-Flash](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/tree/2bc89ac599031fa673cab993f1df02fc4a98c673), [2bc89ac](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/tree/2bc89ac599031fa673cab993f1df02fc4a98c673) |

Every model download URL selected by this release's preparation tools uses the
bot-lab-21 revision above. The tools then prepare the files for three Sparks
and check them against the release's recorded hashes.
The JSPARK3 Tempo page on Hugging Face hosts the recipe, not model files.

The model tree must match52 exact file hashes. Shards47/48 are represented by full-hash verified owned sparse ranges plus an unchanged projection sidecar; there is no whole-shard hash claim for sparse files. All six complete264-byte-row packed payloads are pinned. Packing changes physical layout, not mathematical values or quantization. The draft is bundled DSpark, sharing the checkpoint revision; there is no separately selected draft revision or weight upload.

Runtime defaults: TP3, GMU0.80 with automatic KV sizing, configured context300000, active cap8, solo prefill4096, shared mixed-prefill budget2048, retention512/APC, block128, width4 DSpark probabilistic/block sampling with adaptive verification off, graph sizes4/5/8/10/12/15/16/20/24/25/28/30/32/35/40, buffered packed Engram,32 disk threads/chunk16, row cache0. Full argv/environment is authoritative in `release/runtime.json`. Host addresses, RDMA names, directories and ports are the documented configuration differences.

Historical results describe one fleet, not independent-hardware reproduction. The original cohort's full-HTTP prose32.21–35.34/code63.54–68.22tok/s differs from the seven streaming companions' post-first-output prose33.21–34.28/code67.31–75.64tok/s. Work uses a fixed180-second denominator and includes unfinished output; C6short is a distinct eight-category mean. Model quality was not scored by Work. Code correctness remained2/4, and short-behind-long latency40.458s remains unresolved. Native vision,300K capacity and C8 load are not certified by these results.

`release/manifest.json` hashes an explicit public artifact list. It does not hash itself or claim a commit that creates itself. The candidate/tag receipt binds the manifest checksum and actual Git commit externally after the freeze. Runtime/default changes bump the candidate and invalidate affected receipts; documentation fixes update hashes and receive a recorded validation delta. Final publication remains owned by the release coordinator.

## Fresh release validation

The [September 13 fresh-install receipt](../evidence/fresh-install-20260913.json) binds the tested candidate commit, manifest and source-built image. All five build stages, 46 CPU checks, three-rank native/source checks, five API smoke requests, two representative examples and a first request passed. An additional Pi 0.84.2 smoke completed Python, file-write and file-read tool calls.

This is a fresh reconstruction on the same fleet using rehashed cached inputs. The full historical benchmark campaign was not rerun. The final publication adds documentation and evidence to the tested candidate; its serving source, build inputs, model and runtime defaults are unchanged. See the receipt for deviations, timing boundaries and sampled resource minima.
