# Third-party notices

This repository redistributes modified copies of, or builds directly on, the following works. Their licenses and copyrights remain with their authors; our modifications are marked in the files and licensed under MIT.

| work | license | used as |
|---|---|---|
| DeepSeek-V4.1-Flash weights and `inference/` reference code — DeepSeek-AI | MIT | base checkpoint (dense/attention/Engram tensors unchanged); reference forward used for calibration; DSML `encoding.py` |
| vLLM — vLLM project contributors | Apache-2.0 | serving engine; `patches/engram.py` and `roce_port/*.diff` modify vLLM source files |
| DeepSeek-V4.1-Flash-vLLM-DGX-Spark — tonyd2wild (with Kai's Engram-on-disk and SM12x patches) | MIT | 4× Spark recipe, patch set, image chain, bench protocol; `patches/engram.py` derives from this |
| cuda-exl3 — Zeuss5 and contributors | MIT | vLLM plugin and CUDA kernels serving the EXL3 experts; `patches/exl3_config.py` derives from `cuda_exl3/config.py` 1.0.3 |
| exllamav3 — turboderp-org | MIT | EXL3 format and the `quantize_exl3` (LDLQ) routine used to cook the experts |
| b12x — Luke Alonso and contributors; local-inference-lab/vllm | Apache-2.0 | one-shot RoCE/PCIe collectives runtime and the vLLM shim we ported |
| spark-vllm-docker — eugr | MIT | DGX Spark image build lineage |
| vllm-gb10-spin-wait-fix — drowzeys | (as published) | SpinCondition busy-loop fix baked into overlay8 |
| Pollard Weights — WestWaters | (as published) | method framing and contribution format for the quantization work |
| FlashInfer, NVIDIA CUTLASS DSL, NCCL, PyTorch, safetensors | respective licenses | kernels and plumbing in the vehicle image |

Ideas credited without code reuse: tenaiaiai (NCCL channels / Engram thread levers), 0xSero (Engram outside GPU memory), MiaAI-Lab (fused MoE kernel, slot-share KV), kishida (late-layer KV approximation), alexellis (switchless NCCL notes). See `CREDITS.md` for the full ledger.
