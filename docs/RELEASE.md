# Candidate and publication procedure

`release/identity.json` is the single source for the version, publication state and fresh validation gates. Historical metrics remain immutable. Each gate (`fresh_source_build`, `fresh_install`, `fresh_runtime_smoke`) starts `PENDING`; never mark it `PASS` from historical measurements or source hashes alone.

After an installer finishes, retain the full local receipt privately. Prepare a small public JSON under `evidence/` containing `validated_candidate`, `validated_commit`, `validated_manifest_sha256`, and `checks` with each actually passed gate. Include useful sanitized facts such as built image ID, source hashes, native binary/JIT observations, complete smoke outcome and resource maxima/minima; exclude private hosts, paths, credentials and prompts. This receipt binds the tested candidate, not the later publication metadata commit. Set each passed identity gate's `evidence` to its relative receipt path and `evidence_sha256` to the complete receipt hash. `release_check.py` checks those bindings and requires the named gate to pass in the receipt.

Work in a new branch/worktree. Preserve every prior frozen candidate commit/archive externally. Update `identity.candidate` and `publication_status` to the actual intended state (`pending` until publication is ready), then run `python3 tools/export_release.py`. It regenerates shared summaries/cards/results plus version references and install validation wording. Inspect the diff: upgrading status must not change serving code, sources, weights or historical metrics without another validation cycle.

After reviewing the new candidate, remove only its inherited `release/manifest.json` from this new checkout, then run:

```bash
python3 tools/release_check.py --freeze
python3 tools/release_check.py
python3 -m pytest -q
python3 tools/package_release.py --output dist/NEW_VERSION
```

Commit the entire frozen artifact inventory. Write an **external** receipt with the new commit, manifest SHA256, archive SHA256 and source validation receipt hash. Do not insert that commit into its own hashed manifest. Verify the clean archive again before uploading the same files to GitHub/Hugging Face and binding the website summary. Publication is coordinator-owned; these commands upload nothing.

Build caching is ordinary Docker layer caching. Changing `build/run_stage.sh`, `build/stage.py`, the Dockerfile or compiler inputs can invalidate completed compile layers. In particular rc.2 corrects rc.1's compiler log wrapper and therefore does not reuse rc.1 compiler layers under an unchanged cache key. Final documentation, Work harness and launcher changes are copied after compilation.
