# Current Hugging Face presentation

The immutable v2.0.0 recipe archive and its release manifests remain under their original tag. This directory maintains the current Hugging Face card's presentation independently: changes here do not change runtime, weights, benchmark values or the published archive.

`huggingface/render.py` reads `release/summary.json` and generates the styled `huggingface/README.md`. The animated three-node SVG is copied byte for byte from Cadence; `huggingface/assets/SOURCE.json` pins its original revision and hash.

```bash
python3 presentation/huggingface/render.py
python3 presentation/huggingface/render.py --check
python3 tools/release_check.py
```

Preview the card in Hugging Face's actual prose wrapper, inspect desktop and mobile in both themes, and verify images, links, grid sizing and measurement conditions. Publish only `presentation/huggingface/README.md` as the Hub root README and `presentation/huggingface/assets/jspark3-mark.svg` as `assets/jspark3-mark.svg`, using the observed current HF commit as the parent. Read back both files and verify hashes. Keep the HF `v2.0.0` tag and all archive/evidence/data files unchanged.

The original plain card inside the frozen archive is a release snapshot. The current Hub card may have a newer presentation commit while referring to the same versioned serving recipe.
