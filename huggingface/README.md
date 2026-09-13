---
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

Version: v2.0.1 · experimental · publication published.

Validation: fresh source build: PASS; fresh install: PASS; fresh runtime smoke: PASS; operational patch: PASS.

**[Start here / Install](https://github.com/jakejharris/jspark3-deepseek/blob/v2.0.1/docs/INSTALL.md)** · [GitHub source](https://github.com/jakejharris/jspark3-deepseek/tree/v2.0.1) · [Full evidence](https://github.com/jakejharris/jspark3-deepseek/blob/v2.0.1/docs/BENCHMARKS.md)

Exactly three GB10 Sparks with 128 GB unified memory each, local NVMe, a management network and dual-port RoCE triangle. Weights, sparse Engram and packed rows occupy approximately 400 GB per host. Budget at least 550 GB free local storage per host including preparation headroom, plus an additional 100 GB for image build/cache on the build host. See the exact fit check before downloading.

| Measurement | Result | Conditions |
|---|---:|---|
| 64K cold TTFT | 42.623 s | Three trials; uncached salted prompt, warmed kernels. |
| 64K repeat TTFT | 0.708 s | Three trials; same-salt committed prefix; exact repeat. |
| Prose post-first-output rate | 33.21–34.28 tok/s | One full streaming answer per task; includes transport/finalization and speculative chunks; not GPU-only decode. |
| Code post-first-output rate | 67.31–75.64 tok/s | One full streaming answer per task; includes transport/finalization and speculative chunks; not GPU-only decode. |
| C6 short-answer aggregate | 146.12 tok/s | Eight categories, 150–256-token caps, one wave per category; transport fixtures, not answer-quality grades. |
| Work C6-REPEAT | 110.49 tok/s | One three-minute task round; later tool trajectories diverge; no output-quality score. |

- Experimental serving recipe; historical measurements come from one three-Spark fleet.
- Functional code passed 2/4 tasks; this is not a broad quality ranking.
- A short request arriving two seconds into a 64K prefill waited 40.458 seconds.
- 300,000 tokens is configured context, not certified usable context or C8 capacity.
- Uncached prompt measurements use warmed kernels; they are not cold machine starts.
- Work counts unfinished output and does not score quality. Mia active cap 4 differs from Tempo cap 8.
- Native vision and optional Pi desktop use are separate from the throughput cohort.

Recipe code is Apache-2.0; this does not relicense upstream code or weights. [License boundaries](https://github.com/jakejharris/jspark3-deepseek/blob/v2.0.1/THIRD_PARTY_NOTICES.md). The identical release archive and SHA256SUMS accompany this card at publication.
