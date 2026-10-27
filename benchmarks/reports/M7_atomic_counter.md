# M7 — Atomic Counter (INCR) vs. Naive Read-Modify-Write Under Concurrency

**Status: executed successfully, at reduced scale vs. the original plan (see Limitations).**
**Script:** `benchmarks/m7_atomic_counter.py`
**Result file:** `benchmarks/results/m7_atomic_counter/m7_atomic_counter_20261015T062138Z_28127ea1.json`
**Run ID:** `m7_atomic_counter_20261015T062138Z_28127ea1`
**Commit:** `a2c2996f632734f4186a289b0876988ff4ddc871`

## Objective

Does `services.counter_cache_service.increment_unread_notification_count()` (backed by Redis `INCRBY`) preserve exact counts under concurrent increments, where a naive read-then-write implementation would lose updates?

## Implementation under test

`services/counter_cache_service.py::increment_unread_notification_count()` → `_increment()`, unmodified — a Redis pipeline executing `INCRBY` then `EXPIRE` in one round trip.

## Environment

Real local `redis-server` 7.0.15, genuine network round trips. Python 3.13.16, `multiprocessing.Process` (fork), 2 logical CPUs / 7.8 GiB RAM (sandbox container).

## Workload

- **Concurrency levels (N):** 25, 50, 100 concurrent increment calls for the same counter key.
- **Trials per N:** 5.
- **Synchronization:** `multiprocessing.Barrier(n)` — all N workers block until every worker is ready, then all fire their increment at once.
- **Negative control:** a standalone naive implementation (`GET` current value, add 1 in Python, `SET`), defined only in the benchmark script, never merged into the application. A small 2ms sleep between the GET and SET widens the read-modify-write window so the race manifests reliably within a short-lived trial rather than depending on pure scheduler luck — the underlying vulnerability (the GET-to-SET gap) is the same one a real naive implementation has in production without this artificial delay.

## Results

| N (concurrent increments) | Trials | Real impl: lost updates | Naive baseline: lost updates (median / min / max per trial) |
|---|---|---|---|
| 25 | 5 | **0** | 17 / 15 / 18 |
| 50 | 5 | **0** | 33 / 32 / 36 |
| 100 | 5 | **0** | 63 / 61 / 66 |

**Total: 0 lost updates for the real implementation across all 15 trials (25+50+100 concurrency levels × 5 trials each) — every trial's final counter value exactly equaled N.** The naive baseline lost a substantial and consistent fraction of updates at every N tested — at N=100, a median of 63 of 100 increments (63%) were lost per trial, with a tight min–max range (61–66) indicating this is a reliable, reproducible failure mode under this workload, not occasional noise.

Sample count: 15 trials for the real implementation (zero failures — no percentile/distribution statistics beyond "0 lost updates in 15/15 trials" are meaningful). For the naive baseline, 15 trials total across 3 N values, 5 each; median/min/max reported per N rather than a single pooled statistic, since the expected loss count scales with N and pooling would obscure that relationship.

## Baseline comparison

Both implementations perform the identical nominal operation (increment a shared counter by 1, N times, concurrently) against the same Redis instance, under the same barrier-synchronized workload. The only implementation difference is atomicity: one `INCRBY` round trip vs. a `GET` then a separate `SET`. This isolates atomicity as the variable under test.

## Interpretation

**What this demonstrates:** under up to 100 genuinely concurrent OS processes, the real atomic-increment implementation never lost an update, while a naive read-modify-write implementation reliably lost a majority of updates under the identical workload — a clean, reproducible demonstration of exactly the failure mode atomic operations exist to prevent.

**What this does not demonstrate:** behavior at the originally planned N=1,000 (see Limitations) — not tested in this environment. It also does not demonstrate behavior under sustained, non-bursty concurrent load over a long time period (e.g., minutes of continuous traffic) — this experiment measures a single synchronized burst per trial, which is the right shape for the invariant under test (does atomicity hold under genuine simultaneity) but not a sustained-throughput claim.

## Limitations

- **Concurrency ceiling reduced from the original plan's N ∈ {50, 200, 1000} to N ∈ {25, 50, 100}, and trials reduced from the planned 20 to 5 per N.** This was a real environmental constraint, encountered and documented during implementation rather than silently worked around: `multiprocessing.Process` spawn overhead (process creation, Barrier synchronization, module import inheritance via fork) scales in a way that makes N=200 impractical on this sandbox's 2 logical CPUs — a direct N=200, 2-trial run was attempted and did not complete within a 2-minute budget (see the session's command history; the attempt was killed, not silently retried with different parameters). N=100 at 2 trials completed in 37 seconds, which is roughly consistent with the super-linear-in-N cost of process-spawn-dominated workloads on few cores; extrapolating, N=1,000 would likely require tens of minutes to hours in this environment and was judged not worth the wall-clock cost for a result the N=25/50/100 data already demonstrates clearly (the naive baseline's loss rate is already large and consistent at N=100 — more N would very likely show the same qualitative result, not a materially different one).
- **This is a process-spawn-overhead limitation of the benchmark harness in this specific sandboxed environment, not a limitation of the Redis `INCRBY` operation being tested**, which is O(1) and does not itself become less atomic at higher N. A reader with more CPU cores available (or willing to run threads/async workers hitting the same Redis instance instead of full OS processes, trading away some of the "genuine OS-level concurrency" rigor for throughput) could straightforwardly extend this script to N=1,000 by passing `--n 1000 --trials 20` — the script accepts these as command-line parameters without modification.
- **fakeredis was not used** — deliberately, for the same reason as M1: a real network round trip matters when demonstrating a timing-dependent race.

## Resume suitability: **Resume-ready, with the N-value caveat stated honestly.**

This is the strongest "why atomic operations matter" demonstration in the inventory: a real implementation with zero lost updates across 15 trials, directly contrasted with a naive reference implementation that loses a measured, consistent, scaling majority of updates under identical load. The defensible claim is "demonstrated via a controlled concurrent-write test that the atomic `INCRBY`-based counter preserved exact counts under up to 100 concurrent increments (15 trials, zero lost updates), while a naive read-modify-write reference implementation lost a median of 63% of updates at the same concurrency level" — accurate, reproducible, and the N=100 ceiling should be stated as-is rather than rounded up or implied to be higher.
