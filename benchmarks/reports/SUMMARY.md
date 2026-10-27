# Measurement Results — Consolidated Summary

Commit under test: `a2c2996f632734f4186a289b0876988ff4ddc871` (branch `main`).
All four P0 experiments from `MEASUREMENT_PLAN.md` were implemented, validated, and executed. Individual reports: `M1_lock_concurrency.md`, `M2_lock_fail_closed.md`, `M3_ai_classification.md`, `M7_atomic_counter.md`.

| ID | Experiment | Status | Headline result | Resume suitability |
|---|---|---|---|---|
| M1 | Distributed lock mutual exclusion under concurrency | Executed | 0 overlap violations / 90 trials (N up to 25 processes); naive baseline: up to 4,750 violations | Resume-ready |
| M2 | Distributed lock fail-closed under Redis outage | Executed | 30/30 correctly failed closed across 3 failure modes (dead port, ConnectionError, TimeoutError) | Resume-ready |
| M3 | AI error classifier boundary correctness | Executed | 36/37 constructed cases passed; **found a genuine, reproducible gap**: `error.code: "model_not_found"` (underscore) misclassifies as `NON_RETRYABLE` instead of `BAD_MODEL` | Methodology: resume-ready. The discovered bug: interview material, not a resume bullet (it describes an unfixed gap, not a shipped achievement) |
| M7 | Atomic counter vs. naive read-modify-write | Executed, at reduced N (25/50/100 instead of planned 50/200/1000) | 0 lost updates / 15 trials; naive baseline lost a median 63% of updates at N=100 | Resume-ready, with the N-ceiling stated honestly |

## What changed from the original measurement plan, and why

1. **M1's correctness assertion was redesigned mid-implementation.** The plan's original "exactly 1 winner per trial" assertion was discovered, via a failing smoke test, to be wrong for a reusable lock under wide jitter — a lock can be legitimately re-acquired in sequence within one trial. Fixed by adding barrier synchronization and testing the real invariant (no overlapping held-intervals). Documented in `M1_lock_concurrency.md` rather than silently corrected, since catching this was itself part of validating the harness.
2. **M7's concurrency ceiling was reduced from N=1,000 (as specified in `MEASUREMENT_PLAN.md`) to N=100**, after a direct N=200 attempt did not complete in a 2-minute budget on this sandbox's 2 logical CPUs. This is an environmental constraint of `multiprocessing.Process` spawn overhead, not a property of the Redis operation under test — documented with the attempted command and timing in `M7_atomic_counter.md`, along with the exact flag to extend it (`--n 1000 --trials 20`) for anyone running this on more capable hardware.
3. **M3 uncovered a real bug its own gap-analysis method initially over-reported**, then self-corrected: one of two flagged "gaps" in existing test coverage turned out to already be covered (by a differently-worded test), confirmed by direct source reading rather than trusting the automated heuristic. The genuine gap (the `model_not_found` underscore case) was independently reproduced outside the benchmark harness before being reported as a finding.

## Correctness-first summary

Every experiment was designed to check a defined invariant, not just produce a number that completes quickly:

- M1: mutual exclusion (no overlapping holders) — **held** in every trial.
- M2: fail-closed (never silently grants the lock, never raises) — **held** in every trial, across 3 distinct failure injections.
- M3: classification matches the documented 4-category specification — **held in 36/37 cases; 1 genuine violation found and reported, not suppressed.**
- M7: exact-count preservation (no lost updates) under concurrency — **held** in every trial; the included naive baseline's measured failures confirm the test methodology itself is sound (it can detect a real race when one exists).

No result in this summary was selected as "the best run" from multiple attempts — each number above is the full, unfiltered result of the single final run whose JSON file is cited, and every number is traceable to that file.

## Experiments not implemented in this phase

Per the mission's instruction to prioritize feasible, high-value experiments rather than implementing every inventory candidate, the P1/P2 candidates from `METRICS_AUDIT.md` (presence self-healing, refresh-token rotation timing, reconciliation drift handling, rate-limit tier enforcement, WebSocket throughput, N+1 query avoidance, etc.) were not implemented in this phase. They remain designed at the level of detail in `MEASUREMENT_PLAN.md` for M1/M2/M7's P0 siblings, but have not been built as runnable harnesses. See `RESUME_METRIC_RECOMMENDATIONS.md` and the "Remaining work" section of the final report for what a next phase would cover.
