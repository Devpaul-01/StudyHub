#!/usr/bin/env python3
"""
benchmarks/m2_lock_fail_closed.py

Experiment M2 (METRICS_AUDIT.md / MEASUREMENT_PLAN.md): does
DistributedLock.acquire() correctly refuse to grant the lock — return
falsy, never raise, never silently grant — when Redis is unreachable?

Three failure injections, each a realistic shape a real Redis outage
can take:
  (a) dead port — the real services.extensions.redis_client is pointed
      at a closed TCP port before the process starts (via REDIS_URL),
      so the actual connection-refused path is exercised for real, not
      mocked.
  (b) ConnectionError monkeypatch — redis_client.set() is patched to
      raise redis.exceptions.ConnectionError directly.
  (c) TimeoutError monkeypatch — redis_client.set() is patched to raise
      redis.exceptions.TimeoutError directly.

This exercises the REAL DistributedLock.acquire(), unmodified — only the
underlying redis_client's behavior is manipulated, and only for (b)/(c);
(a) doesn't patch anything, it just points the real client at nothing.

Usage:
    python3 benchmarks/m2_lock_fail_closed.py --repetitions 10
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks._meta import build_run_metadata, write_result


def _case_dead_port(repetitions: int) -> dict:
    """(a) Point REDIS_URL at a closed port, then import DistributedLock
    fresh (so extensions.redis_client is constructed against the dead
    port) and call acquire() for real."""
    import subprocess

    # Find a port nothing is listening on.
    dead_port = 1  # privileged, essentially guaranteed closed to a non-root client;
    # fall back check below in case something unexpected is bound.
    check = subprocess.run(["bash", "-c", f"echo > /dev/tcp/127.0.0.1/{dead_port}"],
                            capture_output=True, timeout=2)
    if check.returncode == 0:
        dead_port = 65535  # fallback, still essentially guaranteed unbound in this sandbox

    results = []
    for i in range(repetitions):
        # Run in a subprocess so each repetition gets a fresh module import
        # (extensions.redis_client is a module-level singleton — re-importing
        # in-process would reuse a client already pointed at a live Redis).
        proc = subprocess.run(
            [sys.executable, "-c", f"""
import sys
sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})
import os
os.environ["REDIS_URL"] = "redis://127.0.0.1:{dead_port}/0"
os.environ.setdefault("SECRET_KEY", "benchmark-only")
os.environ.setdefault("DATABASE_NEW_URL", "sqlite:///:memory:")
from services.distributed_lock import DistributedLock
lock = DistributedLock(key="benchmark:m2:deadport:{i}", ttl_seconds=10)
try:
    result = lock.acquire()
    print("RESULT:" + repr(result))
except Exception as exc:
    print("EXCEPTION:" + repr(exc))
"""],
            capture_output=True, text=True, timeout=15,
        )
        stdout = proc.stdout
        stderr_tail = proc.stderr[-500:] if proc.stderr else ""
        outcome = "unknown"
        acquired_value = None
        if "RESULT:False" in stdout:
            outcome = "correctly_failed_closed"
            acquired_value = False
        elif "RESULT:True" in stdout:
            outcome = "INCORRECTLY_GRANTED_LOCK"
            acquired_value = True
        elif "EXCEPTION:" in stdout:
            outcome = "RAISED_EXCEPTION"
        else:
            outcome = "subprocess_error"

        results.append({
            "repetition": i,
            "outcome": outcome,
            "acquired_value": acquired_value,
            "stdout_tail": stdout[-300:],
            "stderr_tail": stderr_tail,
            "returncode": proc.returncode,
        })
    return {"case": "dead_port", "dead_port_used": dead_port, "repetitions": results}


def _case_monkeypatched_exception(repetitions: int, exception_name: str) -> dict:
    """(b)/(c) Monkeypatch the real redis_client's set() to raise a
    specific exception type, then call the REAL DistributedLock.acquire()
    against that broken client."""
    os.environ.setdefault("REDIS_URL", os.environ.get("REDIS_URL", "redis://localhost:6399/0"))
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    import redis as redis_lib
    from services import distributed_lock as dl_module

    if exception_name == "ConnectionError":
        exc_to_raise = redis_lib.exceptions.ConnectionError("benchmark-injected connection error")
    elif exception_name == "TimeoutError":
        exc_to_raise = redis_lib.exceptions.TimeoutError("benchmark-injected timeout")
    else:
        raise ValueError(exception_name)

    original_set = dl_module.redis_client.set

    def _broken_set(*args, **kwargs):
        raise exc_to_raise

    results = []
    for i in range(repetitions):
        dl_module.redis_client.set = _broken_set
        try:
            lock = dl_module.DistributedLock(key=f"benchmark:m2:{exception_name}:{i}", ttl_seconds=10)
            try:
                acquired = lock.acquire()
                outcome = "correctly_failed_closed" if acquired is False else "INCORRECTLY_GRANTED_LOCK"
                results.append({"repetition": i, "outcome": outcome, "acquired_value": acquired})
            except Exception as exc:
                results.append({
                    "repetition": i, "outcome": "RAISED_EXCEPTION",
                    "acquired_value": None, "exception": repr(exc),
                })
        finally:
            dl_module.redis_client.set = original_set

    return {"case": exception_name, "repetitions": results}


def run_experiment(repetitions: int) -> dict:
    results = {
        "dead_port": _case_dead_port(repetitions),
        "connection_error_monkeypatch": _case_monkeypatched_exception(repetitions, "ConnectionError"),
        "timeout_error_monkeypatch": _case_monkeypatched_exception(repetitions, "TimeoutError"),
    }

    summary = {}
    for case_name, case_result in results.items():
        reps = case_result["repetitions"]
        correct = sum(1 for r in reps if r["outcome"] == "correctly_failed_closed")
        granted = sum(1 for r in reps if r["outcome"] == "INCORRECTLY_GRANTED_LOCK")
        raised = sum(1 for r in reps if r["outcome"] == "RAISED_EXCEPTION")
        other = len(reps) - correct - granted - raised
        summary[case_name] = {
            "total": len(reps),
            "correctly_failed_closed": correct,
            "incorrectly_granted_lock": granted,
            "raised_exception": raised,
            "other_unexpected": other,
            "assertion_held": (granted == 0 and raised == 0 and other == 0 and correct == len(reps)),
        }

    return {"parameters": {"repetitions_per_case": repetitions}, "results": results, "summary": summary}


def main():
    parser = argparse.ArgumentParser(description="M2: Distributed lock fail-closed under Redis outage")
    parser.add_argument("--repetitions", type=int, default=10)
    args = parser.parse_args()

    metadata = build_run_metadata("m2_lock_fail_closed")
    print(f"Run ID: {metadata['run_id']}")
    print(f"Commit: {metadata['repository']['commit_sha']}")

    t0 = time.time()
    try:
        experiment_results = run_experiment(args.repetitions)
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

    out_path = write_result("m2_lock_fail_closed", metadata["run_id"], payload)
    print(f"Results written to: {out_path}")

    if status == "completed":
        for case_name, s in experiment_results["summary"].items():
            print(
                f"{case_name}: {s['correctly_failed_closed']}/{s['total']} correctly failed closed, "
                f"{s['incorrectly_granted_lock']} incorrectly granted, "
                f"{s['raised_exception']} raised an exception "
                f"(assertion_held={s['assertion_held']})"
            )
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
