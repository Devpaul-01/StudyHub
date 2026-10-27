# M1 — Distributed Lock Concurrency Correctness

**Status: executed successfully.**
**Script:** `benchmarks/m1_lock_concurrency.py`
**Result file:** `benchmarks/results/m1_lock_concurrency/m1_lock_concurrency_20261014T105206Z_cc44c3e8.json`
**Run ID:** `m1_lock_concurrency_20261014T105206Z_cc44c3e8`
**Commit:** `a2c2996f632734f4186a289b0876988ff4ddc871`

## Objective

Does `services.distributed_lock.DistributedLock` guarantee mutual exclusion — no two concurrent callers ever simultaneously hold the lock — when N processes genuinely race to acquire the same lock key at the same instant?

## Implementation under test

`services/distributed_lock.py::DistributedLock.acquire()` / `.release()`, unmodified, as called from `scheduler.py`'s per-job wrapper in the real application. The acquisition path is a single atomic `SET key value NX EX ttl`; release is a Lua compare-and-delete (`EVAL`) that only deletes if the caller's `owner_token` still matches.

**Design note on what "correctness" means here, fixed mid-implementation:** the first draft of this harness asserted "exactly 1 acquire() call returns True per trial." That assertion is wrong for a *reusable* lock — if jitter spreads N attempts out in time, a lock can be legitimately acquired, released, and re-acquired by a different process multiple times within one trial, producing winner counts >1 that are correct behavior, not a bug. This was caught during harness validation (see §3, "Validate the harness on a small workload," in the implementation instructions) before any result was trusted: a smoke-test run showed 2/3 winners and investigation of the raw timestamps showed the two acquisitions were sequential (one released before the other began), not overlapping. The harness was corrected to (a) synchronize all N workers with a `multiprocessing.Barrier` so they genuinely attempt acquisition at the same instant, and (b) assert the real invariant — **no two acquirers' held intervals `[acquire_done, release_done]` overlap in wall-clock time** — rather than counting total winners. This correction, and the reasoning behind it, is preserved here rather than silently fixed, since it's a real part of the experiment's validation history.

## Environment

- Real local `redis-server` 7.0.15 (standalone mode), not fakeredis — genuine network round trips.
- Python 3.13.16, `multiprocessing.Process` (fork start method on Linux) — genuine OS processes, not threads or `asyncio` tasks, specifically so the result generalizes to real multi-process contention rather than being an artifact of the GIL or a single event loop.
- 2 logical CPUs, 7.8 GiB RAM (sandbox container).
- Full metadata (dependency versions, commit SHA, OS) embedded in the result JSON's `metadata` block.

## Workload

- **Concurrency levels (N):** 5, 10, 25 processes racing for the same lock key.
- **Trials per N:** 30.
- **Synchronization:** `multiprocessing.Barrier(n)` — all N workers block until every worker has reached the barrier, then all attempt `acquire()` together.
- **Hold time:** each winner holds the lock for 50ms (simulated "doing the job") before releasing, widening the window during which a genuine double-acquisition, if the implementation were broken, would be observable.
- **Residual jitter:** 5ms max random delay *after* the barrier, to add scheduler-level noise without reintroducing enough spread to turn contention into sequential, non-overlapping attempts.
- **Negative control:** a deliberately naive GET-then-SET lock (two Redis round trips, no atomicity), implemented standalone in the benchmark script only, run through the identical trial harness as the real implementation.

## Results

| N (concurrent processes) | Trials | Real impl: overlap violations | Naive baseline: overlap violations |
|---|---|---|---|
| 5 | 30 | **0** | 296 |
| 10 | 30 | **0** | 1,185 |
| 25 | 30 | **0** | 4,750 |

**Total: 0 overlap violations for the real `DistributedLock` across all 90 trials (5+10+25 concurrency levels × 30 trials each), covering up to 25 genuinely concurrent OS processes.** The naive baseline's violation count scales with N, as expected for a race whose probability increases with contention — confirming the negative control actually demonstrates the race it's meant to demonstrate, not just failing for unrelated reasons.

Sample count: 90 trials for the real implementation (zero failures, so no percentile/distribution statistics are meaningful beyond "0 in 90"). Correctness assertion checked per trial: no two winners' `[acquire_done, release_done]` intervals overlapped.

## Baseline comparison

The naive baseline performs the same nominal operation (try to become the sole holder of a named resource) under the identical workload, trial count, and barrier-synchronized concurrency — the only implementation difference is atomicity (one `SET NX EX` call vs. a `GET` then a separate `SET`). This isolates atomicity as the variable under test, per the mission's requirement that baseline and candidate "perform equivalent work" under "comparable" and "controlled" conditions.

## Interpretation

**What this demonstrates:** under up to 25 genuinely concurrent OS processes, repeated 30 times each, `DistributedLock` never allowed two holders' lock-held intervals to overlap. This is consistent with — and is independent empirical evidence for — the atomic `SET NX EX` acquisition design the module's docstring claims.

**What this does not demonstrate:** correctness at higher concurrency (N > 25) was not tested in this environment (see Limitations). It does not demonstrate correctness under real multi-host network partitions, clock skew across physical machines (this test ran all processes on one host), or Redis failover/replication scenarios — only single-instance Redis under process-level contention on one host.

## Limitations

- **Concurrency ceiling tested:** 25, not higher — chosen as the top of the plan's originally proposed range (5/10/25). Not extended further in this environment due to the 2-logical-CPU sandbox; `multiprocessing.Process` spawn overhead begins to dominate wall-clock time well before it would meaningfully stress the lock itself at higher N (see M7's report for a directly measured instance of this same ceiling).
- **Single-host only.** All processes ran on the same container, connecting to the same local Redis over loopback. This does not exercise real network latency/partition behavior between physically separate app instances, which is the actual production deployment shape the lock is designed for.
- **`fakeredis` was not used for this experiment** — deliberately, since a real network round trip matters for a race-condition test. This means the result does not confirm (or need to) the `fakeredis`+`lupa` compatibility already independently verified by the project's own existing unit test (`test_release_does_not_delete_when_no_longer_owner`).

## Resume suitability: **Resume-ready.**

This is a correctness claim backed by a reproducible, source-grounded experiment with a demonstrated negative control — the strongest evidentiary shape in the whole inventory. It directly supports a claim like "verified zero lock-ordering violations across 90 trials of up to 25 concurrent processes racing for a Redis-backed distributed lock, including a side-by-side naive-implementation comparison that measurably failed the same test."
