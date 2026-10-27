# M2 — Distributed Lock Fail-Closed Under Redis Outage

**Status: executed successfully.**
**Script:** `benchmarks/m2_lock_fail_closed.py`
**Result file:** `benchmarks/results/m2_lock_fail_closed/m2_lock_fail_closed_20261014T105559Z_c11af389.json`
**Run ID:** `m2_lock_fail_closed_20261014T105559Z_c11af389`
**Commit:** `a2c2996f632734f4186a289b0876988ff4ddc871`

## Objective

Does `DistributedLock.acquire()` correctly refuse to grant the lock — return a falsy value, never raise, never silently succeed — across three realistic shapes of Redis unavailability?

## Implementation under test

`services/distributed_lock.py::DistributedLock.acquire()`, unmodified — specifically its `except Exception: return False` branch, which the module's own docstring calls "the one deliberate fail-closed exception in an otherwise fail-open codebase."

## Environment

Python 3.13.16; real local `redis-server` 7.0.15 for the baseline-connectivity cases; no Redis reachable at all for the dead-port case (by design). Each of the three failure cases below was implemented with a distinct, realistic injection mechanism rather than one generic mock:

1. **Dead port.** `REDIS_URL` pointed at TCP port 65535 on localhost (confirmed unbound before the run) and a *fresh subprocess* imports `DistributedLock` and calls `acquire()` for real — this exercises the actual `redis-py` connection-refused code path, not a mock. A fresh subprocess per repetition was required because `extensions.redis_client` is a module-level singleton; reusing one Python process across repetitions would reuse a client already bound to a prior (possibly live) connection.
2. **`redis.exceptions.ConnectionError` monkeypatch.** The real `redis_client.set()` method (the exact one `DistributedLock.acquire()` calls) was monkeypatched in-process to raise `ConnectionError` directly, isolating "what does `acquire()` do when this specific exception type escapes" from any particular transport-level cause.
3. **`redis.exceptions.TimeoutError` monkeypatch.** Same approach, different exception type — a genuinely different failure shape (a stalled connection vs. an outright refusal) that could plausibly be handled differently by a less careful `except` clause.

## Workload

10 repetitions per case (30 total `acquire()` calls under simulated failure).

## Results

| Failure mode | Repetitions | Correctly failed closed | Incorrectly granted lock | Raised exception | Assertion held |
|---|---|---|---|---|---|
| Dead port (real connection-refused) | 10 | **10/10** | 0 | 0 | ✅ |
| `ConnectionError` monkeypatch | 10 | **10/10** | 0 | 0 | ✅ |
| `TimeoutError` monkeypatch | 10 | **10/10** | 0 | 0 | ✅ |

**30/30 total: every call to `acquire()` under every simulated failure mode returned `False` and never raised past the call boundary.** The module's own internal `logger.warning(..., exc_info=True)` call does print a traceback to stderr as part of its logging — that traceback is the *module choosing to log the exception*, not the exception escaping `acquire()` uncaught; this is confirmed by `acquired_value: False` being recorded in every case's raw result row, not by the presence or absence of log output.

## Baseline comparison

Not applicable — M2 is a single-implementation correctness check, not a comparison between two implementations. There is no "naive" version of this test; the invariant under test is binary (never grants the lock when Redis is unreachable).

## Interpretation

**What this demonstrates:** across three distinct, realistic Redis-outage shapes, `DistributedLock.acquire()` reliably refused to grant the lock rather than raising into the caller or silently succeeding. Combined with M1's result, this is evidence the lock is correct both when Redis is healthy (M1: no two holders overlap) and when it isn't (M2: no holder is ever granted on a broken connection).

**What this does not demonstrate:** every conceivable Redis failure mode. A Redis instance that accepts the TCP connection but hangs indefinitely mid-command (as opposed to raising `TimeoutError` promptly) was not separately tested — the `TimeoutError` monkeypatch tests "what does the code do when this exception type is raised," not "does the underlying `redis-py` client library actually raise `TimeoutError` promptly under every possible hang scenario," which is a property of the library's own socket-timeout configuration, not of `DistributedLock`'s own logic.

## Limitations

- Scoped to three representative failure shapes, not exhaustive over every way Redis can misbehave (e.g., returning a malformed response, a partial write, or a cluster-mode failover mid-command).
- The dead-port case required a fresh subprocess per repetition (documented above) — this makes that specific case's timing/startup overhead irrelevant to the result (correctness, not speed, is what's measured) but is worth noting as a methodological detail distinguishing it from the other two cases, which ran in-process.

## Resume suitability: **Resume-ready.**

A correctness claim under three distinct, realistic failure injections, each run 10 times with zero violations — directly supports "tested the lock's behavior under three distinct simulated Redis failure modes (dead connection, connection error, timeout) and confirmed it fails closed in all 30 trials," which is the harder and more interview-defensible half of "I built a distributed lock" (most people only demonstrate the happy path).
