# Research harness

Version: v2.0.1.

The ordinary installer runs `tools/smoke.py`. The larger original, parameterized API/quality/cache/concurrency harness is retained under `historical/` for research use. It is not run automatically and does not launch or stop model services. Read `historical/README-harness.md` for the explicit manifest interface and each suite's command.

Its original synthetic application fixtures are retained. Source-location strings were sanitized to relative upstream paths; fixture JSON hashes therefore differ from the archived campaign catalog while application prompt contents are preserved. Supply the new hash of the shipped catalog in your new measurement manifest. Never point it at a prior deployment receipt. Output and telemetry directories, exact rank identities, endpoint, queue adapter and clocks must be filled for the current incarnation. The supplied schema example is a template, not a ready fleet manifest.

This archive contains the complete parameterized harness used as the foundation of the historical API measurements, including bounded code validators. The optional [Work v0.1 runner](work/README.md) separately provides public synthetic prompts, a verified public Cadence fixture, the pinned Pi adapter and bounded C3/C6 first/repeat orchestration. Neither path includes private campaign launch code, installed Pi histories or task workspaces. Re-running creates a new cohort and must not overwrite published historical measurements.

Use an isolated test endpoint. Quality validators execute generated code only in the explicitly configured, immutable, network-disabled Docker sandbox; never run generated code directly on the host. The research harness requires substantially more setup than the release smoke and is outside the clean install fast path.
