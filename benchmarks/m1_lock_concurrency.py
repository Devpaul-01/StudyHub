#!/usr/bin/env python3
"""
benchmarks/m1_lock_concurrency.py

Experiment M1 (METRICS_AUDIT.md / MEASUREMENT_PLAN.md): does
services.distributed_lock.DistributedLock guarantee exactly one winner
when N processes race to acquire the same lock key at the same tick?

This exercises the REAL DistributedLock class (services/distributed_lock.py,
unmodified) against a REAL local redis-server — not fakeredis, not a mock —
because the whole point is testing genuine network-level race behavior.

A naive, deliberately-broken comparison lock (GET-then-SET, two round
trips, no atomicity) is included as the negative control Mission Note §9
calls for: it demonstrates why DistributedLock's atomic SET NX EX is
necessary, by showing the naive version actually let through more than
one "winner" under real concurrency.

Concurrency model: genuine OS processes (multiprocessing), not threads —
chosen because Python threads share one GIL and one Redis connection pool
in ways that can hide races a naive lock would actually have under real
process-level concurrency (separate connections, separate interpreters,
genuine OS-scheduled interleaving).

Usage:
    REDIS_URL=redis://localhost:6399/0 python3 benchmarks/m1_lock_concurrency.py \\
        --n 5,10,25 --trials 30

Requires: a running Redis instance reachable via $REDIS_URL (defaults to
redis://localhost:6379/0 if unset — see services/extensions.py). Does
NOT require fakeredis/lupa, since this uses a real Redis server's native
Lua EVAL support.
"""

from __future__ import annotations

import argparse
import multiprocessing
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks._meta import build_run_metadata, redis_server_info, write_result


# ---------------------------------------------------------------------------
# Worker functions. Each runs in its OWN process (multiprocessing.Process),
# with its own Python interpreter and its own Redis connection — genuine
# process-level concurrency, not threads sharing one GIL/connection pool.
# ---------------------------------------------------------------------------

def _real_lock_worker(key: str, ttl: int, jitter_ms: int, hold_s: float, barrier, result_queue):
    """Imports and uses the REAL DistributedLock, unmodified.

    Uses a multiprocessing.Barrier so every worker calls acquire() at
    (as close as the OS scheduler allows to) the same instant, rather
    than at independently-jittered times. A wide independent jitter
    window lets workers legitimately acquire-then-release in sequence
    without ever overlapping — which is CORRECT behavior for a
    reusable lock, not evidence of a race. The barrier is what actually
    tests concurrent contention; a small residual jitter AFTER the
    barrier (jitter_ms) adds scheduler-level noise without reintroducing
    enough spread to let the trial degenerate into sequential acquires.

    Records wall-clock acquire/release timestamps so the harness can
    verify not just "did more than one process get a truthy acquire()"
    but the stronger invariant: no two processes' held-intervals overlap.
    """
    random.seed(os.getpid() ^ int(time.time() * 1e6))

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from services.distributed_lock import DistributedLock  # real, unmodified

    barrier.wait()  # synchronize start across all N processes
    if jitter_ms:
        time.sleep(random.uniform(0, jitter_ms) / 1000.0)

    lock = DistributedLock(key=key, ttl_seconds=ttl)
    t_acquire_attempt = time.time()
    acquired = lock.acquire()
    t_acquire_done = time.time()
    t_release_done = None
    if acquired:
        time.sleep(hold_s)
        lock.release()
        t_release_done = time.time()
    result_queue.put({
        "pid": os.getpid(),
        "acquired": acquired,
        "owner_token": lock.owner_token,
        "t_acquire_attempt": t_acquire_attempt,
        "t_acquire_done": t_acquire_done,
        "t_release_done": t_release_done,
    })


def _naive_lock_worker(redis_url: str, key: str, ttl: int, jitter_ms: int, hold_s: float, barrier, result_queue):
    """Deliberately broken reference baseline: GET-then-SET, two round
    trips, no atomicity — included ONLY as the negative control. Never
    imported from the application; defined here standalone."""
    random.seed(os.getpid() ^ int(time.time() * 1e6))

    import redis as redis_lib
    client = redis_lib.Redis.from_url(redis_url)

    barrier.wait()
    if jitter_ms:
        time.sleep(random.uniform(0, jitter_ms) / 1000.0)

    t_acquire_attempt = time.time()
    existing = client.get(key)
    if existing is not None:
        result_queue.put({
            "pid": os.getpid(), "acquired": False,
            "t_acquire_attempt": t_acquire_attempt, "t_acquire_done": time.time(),
            "t_release_done": None,
        })
        return
    # Window between GET and SET is where the race lives.
    time.sleep(0.01)
    client.set(key, "naive-owner", ex=ttl)
    t_acquire_done = time.time()
    time.sleep(hold_s)
    client.delete(key)  # naive unconditional delete — not even compare-and-delete
    t_release_done = time.time()
    result_queue.put({
        "pid": os.getpid(), "acquired": True,
        "t_acquire_attempt": t_acquire_attempt, "t_acquire_done": t_acquire_done,
        "t_release_done": t_release_done,
    })


def _intervals_overlap(a_start, a_end, b_start, b_end) -> bool:
    if a_end is None or b_end is None:
        return False  # can't assess overlap without both endpoints
    return a_start < b_end and b_start < a_end


def _run_trial(worker_fn, worker_args_fn, n: int) -> dict:
    """Launches n processes, synchronized via a Barrier so they attempt
    acquire() at (as close as the OS allows to) the same instant.

    Returns a dict with:
      - winners: count of truthy acquire() results
      - overlap_violations: count of PAIRS of acquirers whose held
        intervals [t_acquire_done, t_release_done] overlapped in time
        — this is the real mutual-exclusion invariant, distinct from
        "winners", since a reusable lock legitimately allows sequential
        re-acquisition after a release (winners > 1 across a trial is
        EXPECTED when jitter spreads attempts out; overlapping intervals
        is NOT).
    """
    result_queue = multiprocessing.Queue()
    barrier = multiprocessing.Barrier(n)
    procs = []
    for _ in range(n):
        args = worker_args_fn() + (barrier, result_queue)
        p = multiprocessing.Process(target=worker_fn, args=args)
        procs.append(p)
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=15)
        if p.is_alive():
            p.terminate()

    rows = []
    while not result_queue.empty():
        rows.append(result_queue.get())

    winners = [r for r in rows if r["acquired"]]
    overlap_violations = 0
    for i in range(len(winners)):
        for j in range(i + 1, len(winners)):
            a, b = winners[i], winners[j]
            if _intervals_overlap(
                a["t_acquire_done"], a["t_release_done"],
                b["t_acquire_done"], b["t_release_done"],
            ):
                overlap_violations += 1

    return {
        "winners": len(winners),
        "overlap_violations": overlap_violations,
        "workers_reported": len(rows),
        "workers_expected": n,
    }


def run_experiment(n_values: list[int], trials: int, jitter_ms: int, ttl: int) -> dict:
    import redis as redis_lib

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    probe_client = redis_lib.Redis.from_url(redis_url)
    server_info = redis_server_info(probe_client)
    if not server_info.get("connected"):
        raise RuntimeError(
            f"Cannot reach Redis at {redis_url} — M1 requires a live Redis instance. "
            f"Error: {server_info.get('error')}"
        )

    hold_s = 0.05
    results_by_n = {}
    for n in n_values:
        real_trials = []
        naive_trials = []

        for trial_idx in range(trials):
            key = f"benchmark:m1:real:{n}:{trial_idx}:{random.randint(0, 1_000_000)}"
            r = _run_trial(
                _real_lock_worker,
                lambda: (key, ttl, jitter_ms, hold_s),
                n,
            )
            real_trials.append(r)

        for trial_idx in range(trials):
            key = f"benchmark:m1:naive:{n}:{trial_idx}:{random.randint(0, 1_000_000)}"
            r = _run_trial(
                _naive_lock_worker,
                lambda: (redis_url, key, ttl, jitter_ms, hold_s),
                n,
            )
            naive_trials.append(r)

        real_overlap_violations = sum(t["overlap_violations"] for t in real_trials)
        naive_overlap_violations = sum(t["overlap_violations"] for t in naive_trials)
        real_dropped_workers = sum(
            1 for t in real_trials if t["workers_reported"] != t["workers_expected"]
        )
        naive_dropped_workers = sum(
            1 for t in naive_trials if t["workers_reported"] != t["workers_expected"]
        )

        results_by_n[str(n)] = {
            "n_concurrent_processes": n,
            "trials": trials,
            "real_implementation": {
                "per_trial": real_trials,
                "total_overlap_violations": real_overlap_violations,
                "trials_with_dropped_worker_reports": real_dropped_workers,
                "correctness_assertion": (
                    "no two acquirers' held-intervals [acquire_done, release_done] "
                    "overlap in wall-clock time, across all barrier-synchronized "
                    "concurrent attempts in every trial"
                ),
                "assertion_held": real_overlap_violations == 0 and real_dropped_workers == 0,
            },
            "naive_baseline": {
                "per_trial": naive_trials,
                "total_overlap_violations": naive_overlap_violations,
                "trials_with_dropped_worker_reports": naive_dropped_workers,
                "note": (
                    "naive GET-then-SET baseline; interval overlaps here are the "
                    "EXPECTED and DEMONSTRATED result of the GET/SET race window, "
                    "not a bug in the benchmark"
                ),
            },
        }

    return {
        "server_info": server_info,
        "parameters": {
            "n_values": n_values,
            "trials_per_n": trials,
            "jitter_ms_max": jitter_ms,
            "lock_ttl_seconds": ttl,
            "concurrency_mechanism": "multiprocessing.Process (genuine OS processes)",
        },
        "results_by_n": results_by_n,
    }


def main():
    parser = argparse.ArgumentParser(description="M1: Distributed lock concurrency correctness")
    parser.add_argument("--n", type=str, default="5,10,25", help="Comma-separated concurrency levels")
    parser.add_argument("--trials", type=int, default=30, help="Trials per concurrency level")
    parser.add_argument(
        "--jitter-ms", type=int, default=5,
        help=(
            "Max random jitter AFTER the synchronization barrier (small, adds "
            "scheduler-level noise without letting acquires spread into a "
            "sequential, non-contending pattern — the barrier is what creates "
            "genuine contention, not this jitter)"
        ),
    )
    parser.add_argument("--ttl", type=int, default=30, help="Lock TTL in seconds")
    args = parser.parse_args()

    n_values = [int(x) for x in args.n.split(",")]

    metadata = build_run_metadata("m1_lock_concurrency")
    print(f"Run ID: {metadata['run_id']}")
    print(f"Commit: {metadata['repository']['commit_sha']}")
    print(f"N values: {n_values}, trials/N: {args.trials}")

    t0 = time.time()
    try:
        experiment_results = run_experiment(n_values, args.trials, args.jitter_ms, args.ttl)
        status = "completed"
        error = None
    except Exception as exc:
        experiment_results = None
        status = "failed"
        error = repr(exc)
        print(f"EXPERIMENT FAILED: {error}", file=sys.stderr)

    duration_s = time.time() - t0

    payload = {
        "metadata": metadata,
        "status": status,
        "error": error,
        "duration_seconds": duration_s,
        "results": experiment_results,
    }

    out_path = write_result("m1_lock_concurrency", metadata["run_id"], payload)
    print(f"Results written to: {out_path}")

    if status == "completed":
        for n_key, r in experiment_results["results_by_n"].items():
            real = r["real_implementation"]
            naive = r["naive_baseline"]
            print(
                f"N={n_key}: real impl overlap_violations={real['total_overlap_violations']} "
                f"across {r['trials']} trials (assertion_held={real['assertion_held']}) | "
                f"naive baseline overlap_violations={naive['total_overlap_violations']}"
            )
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
