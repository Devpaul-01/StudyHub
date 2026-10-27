# StudyHub — Measurement Plan (P0 batch)

Covers the four P0 experiments from `METRICS_AUDIT.md`: M1 (lock concurrency), M2 (lock fail-closed), M3 (AI error classification), M7 (atomic counter vs. naive read-modify-write). Each follows the same template per the mission spec. None of these have been run yet — designs only, per the Part One scope. No code has been changed in the target repository; experiments are standalone scripts/tests that exercise existing code as-is.

---

## M1 — Distributed lock: exactly-one-execution under concurrent schedulers

- **Research question:** When N scheduler-enabled processes race to acquire the same job lock at the same simulated tick, does exactly one succeed, every time?
- **Hypothesis:** Yes — `acquire()`'s atomic `SET NX EX` should make this true regardless of N, timing, or thread/process boundaries.
- **Target code path:** `services/distributed_lock.py::DistributedLock.acquire()` / `.release()`, as called from `scheduler.py`'s per-job wrapper.
- **Baseline implementation:** A deliberately naive comparison lock — "GET key, if empty then SET" (two round trips, not atomic) — written standalone, never merged into the app, purely to demonstrate why the real implementation needs to be atomic. This is the "simple reference implementation" baseline per Mission Note §9.
- **Candidate implementation:** The actual `DistributedLock` class, unmodified.
- **Workload:** N concurrent acquisition attempts (N = 5, 10, 25) against the same lock key, fired via `concurrent.futures.ThreadPoolExecutor` or `multiprocessing.Pool` — threads first (simpler), processes as a follow-up if thread-level GIL behavior raises any doubt about whether the result generalizes.
- **Dataset/input distribution:** N/A — fixed lock key, varying only N and attempt-launch jitter (0–50ms randomized start delay to avoid all attempts landing in the exact same Python tick, which would be an artificial advantage the real world doesn't guarantee).
- **Warm-up procedure:** One untimed acquire/release cycle against the test Redis instance before the timed runs, to rule out connection-establishment cost skewing the first real attempt.
- **Repetitions:** 30 independent trials per N value, fresh lock key each trial (to avoid any TTL/state leakage between trials).
- **Concurrency level:** N ∈ {5, 10, 25} as above.
- **Environment:** Local `fakeredis` (already a test dependency, with `lupa` for Lua `EVAL` support — confirmed necessary and present) for the primary run; optionally repeat once against a real local `redis-server` to rule out any `fakeredis`-specific behavior the production Redis wouldn't exhibit.
- **Measurement method:** Count of successful `acquire()` calls per trial (must be exactly 1 for the real implementation; expected to be >1 at least some of the time for the naive baseline, which is the point of including it).
- **Correctness assertions:** `sum(successful_acquires) == 1` for every trial of the real implementation. For the naive baseline, report the actual distribution — no assertion, since failing is the expected/demonstrated behavior.
- **Exclusion criteria:** A trial where the test harness itself errors (e.g., a `fakeredis` connection hiccup unrelated to the lock logic) is excluded and re-run, logged as excluded with reason — never silently dropped from the denominator.
- **Result format:** A table: N | trials | real-impl violations (expect 0) | naive-impl violations (expect >0 at least sometimes).
- **Statistical summary:** Violation count and rate per N; no percentile/latency claim here since this is a correctness experiment, not a performance one.
- **Known limitations:** `fakeredis`'s in-process nature means true network-level race conditions (actual TCP round-trip timing) aren't reproduced — a real-Redis run is the stronger evidence, included as the optional second pass above specifically to address this.
- **Resume-safe interpretation:** "Verified zero duplicate executions across N concurrent lock-acquisition attempts in a controlled test, including a side-by-side naive-implementation comparison that does exhibit duplicates" — a correctness claim backed by a reproducible test, not a performance percentage.

---

## M2 — Distributed lock: fail-closed under Redis outage

- **Research question:** Does `acquire()` correctly refuse to grant the lock (rather than raising, hanging, or silently granting it) when Redis is unreachable?
- **Hypothesis:** Yes — the module's own code and existing unit test (`test_acquire_fails_closed_on_redis_error`) already assert this; this experiment is about independently reproducing and extending that result, not taking the existing test's word for it alone.
- **Target code path:** `services/distributed_lock.py::DistributedLock.acquire()`, the `except Exception` branch.
- **Baseline implementation:** N/A — this is a single-implementation correctness check, not a comparison.
- **Candidate implementation:** `DistributedLock`, unmodified.
- **Workload:** Call `acquire()` under three distinct failure injections: (a) point the Redis client at a closed/dead port (connection refused), (b) monkeypatch the client's `set()` to raise a `redis.exceptions.ConnectionError` directly, (c) monkeypatch to raise a `TimeoutError`. All three are realistic failure shapes a real Redis outage can produce.
- **Dataset/input distribution:** N/A.
- **Warm-up procedure:** None needed — this is testing the failure path specifically, so there's nothing to warm up.
- **Repetitions:** 10 per failure-injection type (mostly to confirm determinism, not because flakiness is expected).
- **Concurrency level:** Single-threaded — concurrency isn't the variable under test here (that's M1).
- **Environment:** `fakeredis` with monkeypatched failure injection for (b)/(c); a genuinely closed local port for (a).
- **Measurement method:** Return value of `acquire()` (must be falsy/"not acquired") and absence of any raised exception escaping the call.
- **Correctness assertions:** `acquire()` returns not-acquired in all three failure modes; no exception propagates past `acquire()`; no lock key is left set in a broken state.
- **Exclusion criteria:** None expected — any unexpected exception IS the finding (a failure of the fail-closed guarantee), not something to exclude.
- **Result format:** Pass/fail table per failure-injection type.
- **Statistical summary:** N/A — binary correctness outcome.
- **Known limitations:** Doesn't cover every conceivable Redis failure mode (e.g., a Redis instance that accepts the connection but hangs indefinitely mid-command) — scoped to the three most representative/common failure shapes.
- **Resume-safe interpretation:** "Tested the lock's behavior under three distinct simulated Redis failure modes and confirmed it fails closed (never grants the lock, never raises into the caller) in all three" — a reliability-engineering claim about the failure path specifically, which is the harder and more interview-relevant half of "I built a distributed lock."

---

## M3 — AI provider error classification: boundary-case correctness

- **Research question:** Does `classify_provider_error()` correctly sort every representative failure shape into the right one of 4 categories, including boundary cases the existing test file may not fully cover?
- **Hypothesis:** Largely yes, based on the existing `test_error_classification.py` — this experiment's value is in building a more exhaustive boundary table and confirming no gaps exist, not in expecting to find the function broken.
- **Target code path:** `services/ai_provider_service.py::classify_provider_error()` and `_is_bad_model_signal()`.
- **Baseline implementation:** N/A — correctness-against-specification, not a performance comparison.
- **Candidate implementation:** `classify_provider_error()`, unmodified.
- **Workload:** A constructed table of `ProviderCallError` inputs covering: each status in `{401,402,403,429}` → expect `KEY_FAULT`; each status in `{500,502,503,504}` → expect `PROVIDER_TRANSIENT`; status 400 with a bad-model-shaped body (`error.type`/`code`/`message` containing a model-not-found-style signal) → expect `BAD_MODEL`; status 400 WITHOUT a bad-model signal → expect `NON_RETRYABLE` (boundary case worth confirming explicitly); a network-level error with no status at all → confirm it's handled by the network-error-code path rather than falling through to the message-string fallback; an error with `status=None` and no network error code (forcing the explicit last-resort `raw_message` fallback) → confirm this narrow path behaves as documented, not silently.
- **Dataset/input distribution:** Exhaustive over the documented status sets, plus 3–5 "unusual" body shapes per BAD_MODEL/NON_RETRYABLE boundary (empty body, body with unrelated 400 reason mentioning "model" in prose text but not in the structured field — this is specifically testing the "doesn't misfire on unrelated 400s" claim in `ARCHITECTURE.md` §11.2).
- **Warm-up procedure:** None needed — pure function, no I/O.
- **Repetitions:** 1 per case (deterministic pure function — repetition only matters if nondeterminism is suspected, which it isn't here).
- **Concurrency level:** N/A.
- **Environment:** Pure unit test, no external dependencies at all.
- **Measurement method:** Direct equality assertion, `classify_provider_error(constructed_error) == expected_category`.
- **Correctness assertions:** Every row in the constructed table matches its expected category.
- **Exclusion criteria:** None — this is exhaustive-by-construction over the documented boundary set.
- **Result format:** A table of input → expected → actual → pass/fail, plus a short list of any case not already covered by the existing `test_error_classification.py` (gap analysis as a byproduct).
- **Statistical summary:** N/A — binary per-case correctness; summarize as "X/Y boundary cases pass."
- **Known limitations:** This proves the classifier is internally consistent with its own documented rules, not that those rules correctly anticipate every real provider's actual error response shape — six different LLM providers may format errors in ways this table doesn't anticipate. That's a live-provider-fidelity question, explicitly out of scope here (see `METRICS_AUDIT.md` M12/X3).
- **Resume-safe interpretation:** "Built an exhaustive boundary-case test suite for the 4-category provider-error classifier, confirming correct routing including edge cases around the BAD_MODEL/NON_RETRYABLE boundary" — a correctness/test-engineering claim, not a claim about live provider behavior.

---

## M7 — Atomic counter (INCR) vs. naive read-modify-write under concurrency

- **Research question:** Does `counter_cache_service.increment_unread_notification_count()` (Redis `INCR`) preserve exact counts under concurrent increments where a naive read-then-write implementation would lose updates?
- **Hypothesis:** Yes for the real implementation (`INCR` is atomic by construction); the naive comparison implementation is expected to undercount under real concurrency — that's the whole point of including it as Mission Note §9's "simple reference implementation" baseline.
- **Target code path:** `services/counter_cache_service.py::increment_unread_notification_count()` (and the shared reseed/`INCRBY` helper it calls into).
- **Baseline implementation:** A throwaway, standalone function — never merged into the app — implementing the naive version: `value = redis.get(key); redis.set(key, int(value or 0) + 1)`. This exists purely as the demonstrated negative control.
- **Candidate implementation:** The real `increment_unread_notification_count()`, unmodified.
- **Workload:** N concurrent increment calls (N = 50, 200, 1000) for the same user_id, fired from a real `ThreadPoolExecutor` (and, if time permits, a `multiprocessing.Pool` variant to rule out any GIL-related false confidence — Python threads can sometimes hide race conditions that separate processes would expose).
- **Dataset/input distribution:** N/A — fixed key, varying N.
- **Warm-up procedure:** Reset/delete the counter key before each trial; one untimed call to establish the Redis connection before the timed/counted run.
- **Repetitions:** 20 trials per N value, fresh key each trial.
- **Concurrency level:** N ∈ {50, 200, 1000} as above.
- **Environment:** Local `fakeredis` for the primary pass (matches existing test infra); real local `redis-server` as a confirming second pass, since `fakeredis`'s concurrency model may not perfectly replicate real network-level races — this is the single highest-value experiment to also run against real Redis, since the whole point is demonstrating a race condition that depends on genuine timing.
- **Measurement method:** Final counter value read after all N calls complete, compared against N (the correct answer).
- **Correctness assertions:** Real implementation: `final_count == N` in every trial, every N. Naive implementation: report actual final count (expected `< N` at least some of the time, more reliably so as N increases) — no assertion, the undercounting itself is the result being demonstrated.
- **Exclusion criteria:** A trial where a thread/process itself raises an unexpected exception unrelated to the counter logic is excluded and re-run, logged with reason.
- **Result format:** A table: N | trials | real-impl final count distribution (expect all == N) | naive-impl final count distribution (expect a spread below N, with the gap widening as N increases) | naive-impl average lost-update rate.
- **Statistical summary:** Report the lost-update rate for the naive baseline (e.g., "at N=1000, the naive implementation lost a median of X updates across 20 trials") with median and range, not a single cherry-picked run. No p99/percentile claim needed here — this is a small, fully-enumerable trial count (20), not a latency distribution.
- **Known limitations:** The naive baseline's lost-update rate is itself somewhat timing-dependent (CPU load, thread scheduler behavior) — report the range observed, don't present one run's number as a fixed constant. State Python version and whether threads or processes were used, since that materially affects how visible the race is.
- **Resume-safe interpretation:** "Demonstrated via a controlled concurrent-write test that the atomic `INCR`-based counter preserves exact counts under up to 1,000 concurrent increments, where a naive read-modify-write reference implementation measurably lost updates under the same load" — this is the strongest, most classic "why atomic operations matter" story in the inventory, and it's fully reproducible and cheap.

---

## Cross-cutting notes for all four experiments

- **No production data, no production environment** is touched by any of these — all four run against local `fakeredis`/local Redis/local SQLite-or-Postgres test fixtures, consistent with the Mission's environment-and-safety constraints.
- **Every experiment preserves unfavorable results.** If M1 or M2 ever shows a violation, or M7's real implementation ever loses an update, that is the headline finding, not something to re-run until it goes away — a lock or counter that fails its own correctness test is a more important discovery than a clean benchmark number.
- **None of these experiments have been run yet.** This document is the design; running them, collecting the actual numbers, and writing up results honestly (including if something comes back noisy or wrong) is the next phase, not something to summarize here in advance.
- **Nothing here requires paid external API calls** — M3 is a pure unit test against constructed inputs, not live provider traffic, consistent with the Mission's caution against unnecessary paid calls.

---

*Companion documents: `METRICS_AUDIT.md` (full inventory and prioritization), `RESUME_METRICS_AUDIT.md` (blocked pending resume text).*
