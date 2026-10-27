# StudyHub Measurement Infrastructure

Standalone benchmark/correctness harnesses for the P0 experiments selected in `../MEASUREMENT_PLAN.md`, implemented against this repository's real source (not reimplementations, not mocks of the code under test) at commit `a2c2996f632734f4186a289b0876988ff4ddc871`.

**This directory is not part of the application.** Nothing here is imported by `app.py`, `worker.py`, or any route/service. It exists to produce reproducible evidence for `RESUME_METRIC_RECOMMENDATIONS.md`.

## Layout

```
benchmarks/
  _meta.py                    Shared metadata-capture helper (commit SHA, env, deps) — imported by every script
  m1_lock_concurrency.py      Distributed lock mutual exclusion under concurrent processes
  m2_lock_fail_closed.py      Distributed lock fail-closed behavior under 3 Redis-outage shapes
  m3_ai_classification.py     AI provider error classifier boundary-case correctness
  m7_atomic_counter.py        Atomic Redis INCR vs. naive read-modify-write under concurrency
  results/<experiment>/<run_id>.json   Raw, unmodified output of every run — never overwritten, never hand-edited
  reports/                    Human-readable analysis of each experiment's results (see reports/SUMMARY.md first)
```

## Prerequisites

- Python 3.11+ (developed and run against 3.13.16).
- A reachable Redis instance (real `redis-server`, not `fakeredis` — these experiments specifically test real network-level timing behavior). All four scripts read the connection string from the `REDIS_URL` environment variable the same way the application's `extensions.py` does; default is `redis://localhost:6379/0` if unset.
- Python packages: `redis`, `requests`, `Flask-SQLAlchemy`, `Flask-Mail` (the last two are pulled in transitively because `services/distributed_lock.py` and `services/counter_cache_service.py` import the real `extensions.py`, which constructs `db`/`mail` singletons alongside the Redis client — importing the actual unmodified module, rather than re-implementing its Redis client construction, was a deliberate choice to exercise the real code path).

```bash
python3 -m venv venv
source venv/bin/activate
pip install "redis>=4,<8.0" requests Flask-SQLAlchemy Flask-Mail
```

Start a local Redis for testing (do **not** point this at a shared/production Redis — M1 and M7 write and delete benchmark-prefixed keys, and M2 deliberately breaks the connection):

```bash
redis-server --daemonize yes --port 6399 --save '' --appendonly no
export REDIS_URL="redis://localhost:6399/0"
```

## Running each experiment

All four scripts are runnable from the repository root and write results to `benchmarks/results/<experiment_name>/<run_id>.json` — a fresh, timestamped file every run, never an overwrite.

### M1 — Lock concurrency

```bash
python3 benchmarks/m1_lock_concurrency.py --n 5,10,25 --trials 30
```
Runtime: ~3 minutes for the default parameters (90 trials × 2 implementations × barrier-synchronized process spawns). Reduce `--trials` for a faster smoke test.

### M2 — Lock fail-closed

```bash
python3 benchmarks/m2_lock_fail_closed.py --repetitions 10
```
Runtime: ~5 seconds.

### M3 — AI classification boundary correctness

```bash
export AI_PROVIDER_REDIS_STATE_ENABLED=false   # avoids incidental Redis state writes during this pure-function test
python3 benchmarks/m3_ai_classification.py
```
Runtime: under 1 second (pure function, no I/O, no concurrency).

### M7 — Atomic counter vs. naive read-modify-write

```bash
python3 benchmarks/m7_atomic_counter.py --n 25,50,100 --trials 5
```
Runtime: ~3 minutes at these parameters on a 2-CPU machine. **The originally planned N=1,000 was not reachable in this sandbox** (see `reports/M7_atomic_counter.md` → Limitations) — on a machine with more CPU cores, or accepting threads instead of full OS processes as the concurrency primitive, extend with:
```bash
python3 benchmarks/m7_atomic_counter.py --n 200,500,1000 --trials 10
```
and budget significantly more wall-clock time; process-spawn overhead, not the Redis operation itself, is the limiting factor (see the report for the reasoning).

## Interpreting results

Every result JSON has the same top-level shape:

```jsonc
{
  "metadata": { /* commit SHA, timestamp, run_id, dependency versions, OS/hardware — see _meta.py */ },
  "status": "completed" | "failed",
  "error": null | "repr of the exception if status is failed",
  "duration_seconds": <float>,
  "results": { /* experiment-specific payload */ }
}
```

Read `reports/SUMMARY.md` first for the consolidated headline numbers, then the per-experiment report (`reports/M1_lock_concurrency.md` etc.) for the full objective/environment/workload/results/interpretation/limitations writeup, then the raw JSON in `results/` only if you need to re-derive a specific number or verify a report claim against the unmodified raw data.

## A note on what these scripts do NOT do

- They do not modify any application file under `services/`, `routes/`, `models.py`, or any other non-`benchmarks/` path.
- They do not push commits, publish packages, or touch any deployed environment.
- The "naive baseline" implementations in M1 and M7 (a broken GET-then-SET lock, a broken read-modify-write counter) are defined **only** inside the benchmark scripts themselves, specifically as negative controls demonstrating why the real implementation's atomicity matters — they are never imported into, or merged with, the application.
- Results are never hand-edited after a run. If a run's numbers look wrong, the fix is re-running the script (producing a new, separately-timestamped result file) after correcting the script itself — not editing a JSON file in `results/`. The M1 harness's own validation history (see `reports/M1_lock_concurrency.md`) is an example of this: an incorrect assertion was found via a failing smoke test, the *script* was fixed, and the smoke-test result files from the broken version were deleted rather than kept alongside the corrected ones, since they measured the wrong thing and would mislead a reader who didn't know the harness itself had a bug at that point.
