# Tempo measured results

Version: v2.0.3 · historical L5-P cohort, September 13, 2026.

| Measurement | Result | Conditions |
|---|---:|---|
| 64K cold TTFT | 42.623 s | Three trials; uncached salted prompt, warmed kernels. |
| 64K repeat TTFT | 0.708 s | Three trials; same-salt committed prefix; exact repeat. |
| 64K suffix TTFT | 0.691 s | Three trials; same-salt committed prefix; changed suffix. |
| 76K cold TTFT | 49.659 s | Three trials; uncached salted prompt, warmed kernels. |
| 76K repeat TTFT | 0.471 s | Three trials; same-salt committed prefix; exact repeat. |
| 76K suffix TTFT | 0.475 s | Three trials; same-salt committed prefix; changed suffix. |
| Prose post-first-output rate | 33.21–34.28 tok/s | One full streaming answer per task; includes transport/finalization and speculative chunks; not GPU-only decode. |
| Code post-first-output rate | 67.31–75.64 tok/s | One full streaming answer per task; includes transport/finalization and speculative chunks; not GPU-only decode. |
| C1 short-answer aggregate | 49.05 tok/s | Eight categories, 150–256-token caps, one wave per category; transport fixtures, not answer-quality grades. |
| C2 short-answer aggregate | 73.55 tok/s | Eight categories, 150–256-token caps, one wave per category; transport fixtures, not answer-quality grades. |
| C3 short-answer aggregate | 97.21 tok/s | Eight categories, 150–256-token caps, one wave per category; transport fixtures, not answer-quality grades. |
| C4 short-answer aggregate | 114.16 tok/s | Eight categories, 150–256-token caps, one wave per category; transport fixtures, not answer-quality grades. |
| C5 short-answer aggregate | 131.46 tok/s | Eight categories, 150–256-token caps, one wave per category; transport fixtures, not answer-quality grades. |
| C6 short-answer aggregate | 146.12 tok/s | Eight categories, 150–256-token caps, one wave per category; transport fixtures, not answer-quality grades. |
| Work C3-FIRST | 80.12 tok/s | One three-minute task round; later tool trajectories diverge; no output-quality score. |
| Work C3-REPEAT | 81.98 tok/s | One three-minute task round; later tool trajectories diverge; no output-quality score. |
| Work C6-FIRST | 102.79 tok/s | One three-minute task round; later tool trajectories diverge; no output-quality score. |
| Work C6-REPEAT | 110.49 tok/s | One three-minute task round; later tool trajectories diverge; no output-quality score. |
| Functional code correctness | 2 passed / 4 | 2/4 small code tasks passed; interval ordering and dependency topology failed. |
| Short behind long TTFT | 40.458 s | No matched historical inverse-arrival control; unresolved admission weakness. |

Machine definitions and source hashes: [benchmarks.json](../release/benchmarks.json). Selected original observations: [evidence](../evidence/selected-measurements.json). These are historical measurements, not a fresh installation receipt.

- Experimental serving recipe; historical measurements come from one three-Spark fleet.
- Functional code passed 2/4 tasks; this is not a broad quality ranking.
- A short request arriving two seconds into a 64K prefill waited 40.458 seconds.
- 300,000 tokens is configured context, not certified usable context or C8 capacity.
- Uncached prompt measurements use warmed kernels; they are not cold machine starts.
- Work counts unfinished output and does not score quality. Mia active cap 4 differs from Tempo cap 8.
- Native vision and optional Pi desktop use are separate from the throughput cohort.
