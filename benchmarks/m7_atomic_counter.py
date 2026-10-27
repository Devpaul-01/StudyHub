#!/usr/bin/env python3
"""
benchmarks/m7_atomic_counter.py

Experiment M7 (METRICS_AUDIT.md / MEASUREMENT_PLAN.md): does
services.counter_cache_service.increment_unread_notification_count()
(backed by Redis INCRBY) preserve exact counts under concurrent
increments, where a naive read-modify-write implementation would lose
updates?

Exercises the REAL increment_unread_notification_count(), unmodified,
against a REAL local redis-server (genuine network round trips, not
fakeredis). The naive baseline (GET, add 1 in Python, SET) is a
standalone function defined only in this file — never imported into or
merged with the application — included specifically as the negative
control Mission Note §9 calls for.

Concurrency model: genuine OS processes (multiprocessing), synchronized
with a Barrier so all N increments are attempted at (as close as the OS
scheduler allows to) the same instant — the condition under which a
read-modify-write race actually manifests.

Usage:
    REDIS_URL=redis://localhost:6399/0 python3 benchmarks/m7_atomic_counter.py \\
        --n 50,200,1000 --trials 20
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


def _real_increment_worker(user_id: str, barrier):
    """Imports and calls the REAL increment_unread_notification_count,
    unmodified."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from services.counter_cache_service import increment_unread_notification_count

    barrier.wait()
    increment_unread_notification_count(user_id)


def _naive_increment_worker(redis_url: str, key: str, barrier):
    """Deliberately broken reference baseline: GET, add 1 in Python,
    SET — two round trips, classic lost-update shape. Never imported
    from the application; defined here standalone, as the negative
    control."""
    import redis as redis_lib
    client = redis_lib.Redis.from_url(redis_url)

    barrier.wait()
    current = client.get(key)
    current_val = int(current) if current is not None else 0
    # Small sleep widens the read-modify-write window, making the race
    # reliably observable rather than dependent on pure scheduler luck —
    # a real naive implementation in production code has this same
    # vulnerability without needing an artificial delay; the delay here
    # only makes the SAME underlying race manifest more consistently
    # within a short-lived subprocess trial. The RMW gap itself (not the
    # delay) is what's being measured.
    time.sleep(0.002)
    client.set(key, current_val + 1)


def _run_concurrent_increments(worker_fn, worker_args_fn, n: int) -> float:
    """Launches n processes performing one increment each, synchronized
    via Barrier. Returns elapsed wall-clock seconds for the whole burst."""
    barrier = multiprocessing.Barrier(n)
    procs = []
    for _ in range(n):
        args = worker_args_fn() + (barrier,)
        p = multiprocessing.Process(target=worker_fn, args=args)
        procs.append(p)
    t0 = time.time()
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
        if p.is_alive():
            p.terminate()
    t1 = time.time()
    return t1 - t0


def run_experiment(n_values: list[int], trials: int) -> dict:
    import redis as redis_lib

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    probe_client = redis_lib.Redis.from_url(redis_url)
    server_info = redis_server_info(probe_client)
    if not server_info.get("connected"):
        raise RuntimeError(
            f"Cannot reach Redis at {redis_url} — M7 requires a live Redis instance. "
            f"Error: {server_info.get('error')}"
        )

    results_by_n = {}
    for n in n_values:
        real_trial_results = []
        naive_trial_results = []

        for trial_idx in range(trials):
            user_id = f"benchmark-m7-real-{n}-{trial_idx}-{random.randint(0, 1_000_000)}"
            key = f"sh:1:notif:unread:{user_id}"
            probe_client.delete(key)  # ensure clean start

            elapsed = _run_concurrent_increments(
                _real_increment_worker, lambda: (user_id,), n
            )
            final_val = probe_client.get(key)
            final_val = int(final_val) if final_val is not None else 0
            probe_client.delete(key)

            real_trial_results.append({
                "trial": trial_idx,
                "n": n,
                "expected": n,
                "actual": final_val,
                "lost_updates": n - final_val,
                "elapsed_seconds": elapsed,
            })

        for trial_idx in range(trials):
            key = f"benchmark:m7:naive:{n}:{trial_idx}:{random.randint(0, 1_000_000)}"
            probe_client.delete(key)

            elapsed = _run_concurrent_increments(
                _naive_increment_worker, lambda: (redis_url, key), n
            )
            final_val = probe_client.get(key)
            final_val = int(final_val) if final_val is not None else 0
            probe_client.delete(key)

            naive_trial_results.append({
                "trial": trial_idx,
                "n": n,
                "expected": n,
                "actual": final_val,
                "lost_updates": n - final_val,
                "elapsed_seconds": elapsed,
            })

        real_lost = [t["lost_updates"] for t in real_trial_results]
        naive_lost = [t["lost_updates"] for t in naive_trial_results]

        results_by_n[str(n)] = {
            "n_concurrent_increments": n,
            "trials": trials,
            "real_implementation": {
                "per_trial": real_trial_results,
                "total_lost_updates_across_trials": sum(real_lost),
                "trials_with_any_lost_update": sum(1 for x in real_lost if x != 0),
                "correctness_assertion": "final_count == n (zero lost updates) in every trial",
                "assertion_held": all(x == 0 for x in real_lost),
            },
            "naive_baseline": {
                "per_trial": naive_trial_results,
                "total_lost_updates_across_trials": sum(naive_lost),
                "trials_with_any_lost_update": sum(1 for x in naive_lost if x != 0),
                "median_lost_updates": sorted(naive_lost)[len(naive_lost) // 2] if naive_lost else None,
                "min_lost_updates": min(naive_lost) if naive_lost else None,
                "max_lost_updates": max(naive_lost) if naive_lost else None,
                "note": (
                    "naive GET-then-SET baseline; lost updates here are the EXPECTED "
                    "and DEMONSTRATED result of the read-modify-write race, not a bug "
                    "in the benchmark"
                ),
            },
        }

    return {
        "server_info": server_info,
        "parameters": {
            "n_values": n_values,
            "trials_per_n": trials,
            "concurrency_mechanism": "multiprocessing.Process (genuine OS processes) + Barrier synchronization",
        },
        "results_by_n": results_by_n,
    }


def main():
    parser = argparse.ArgumentParser(description="M7: Atomic counter vs naive read-modify-write")
    parser.add_argument("--n", type=str, default="50,200,1000", help="Comma-separated concurrency levels")
    parser.add_argument("--trials", type=int, default=20, help="Trials per concurrency level")
    args = parser.parse_args()

    n_values = [int(x) for x in args.n.split(",")]

    metadata = build_run_metadata("m7_atomic_counter")
    print(f"Run ID: {metadata['run_id']}")
    print(f"Commit: {metadata['repository']['commit_sha']}")
    print(f"N values: {n_values}, trials/N: {args.trials}")

    t0 = time.time()
    try:
        experiment_results = run_experiment(n_values, args.trials)
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

    out_path = write_result("m7_atomic_counter", metadata["run_id"], payload)
    print(f"Results written to: {out_path}")

    if status == "completed":
        for n_key, r in experiment_results["results_by_n"].items():
            real = r["real_implementation"]
            naive = r["naive_baseline"]
            print(
                f"N={n_key}: real impl lost_updates={real['total_lost_updates_across_trials']} "
                f"across {r['trials']} trials (assertion_held={real['assertion_held']}) | "
                f"naive baseline lost_updates total={naive['total_lost_updates_across_trials']} "
                f"median/trial={naive['median_lost_updates']}"
            )
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
