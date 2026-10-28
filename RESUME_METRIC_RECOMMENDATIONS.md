# StudyHub — Resume Metric Recommendations

> **Update (2026-10-27) — some figures below are superseded.** This document is a point-in-time record at the commit it names; the codebase has moved on. Current verified figures: **53** SQLAlchemy models (an earlier count of 52 missed `User`, whose bases are `UserMixin, db.Model`); **29** inbound Socket.IO handlers (14 in `websocket_messages.py` + 15 in `websocket_threads.py`; `websocket_events.py` is unused); about **325** route handlers; **1,490** passing tests (672 unit, 818 integration), 2 skipped, no remaining xfails; and the M3 classifier gap is fixed (37/37). Counts quoted further down are left as originally recorded.

**Commit audited:** `a2c2996f632734f4186a289b0876988ff4ddc871` (branch `main`).
**Scope:** the StudyHub section of the supplied resume only, per the mission's single-project scope. Kith, FounderSales, and agent-broker bullets are separate repositories not inspected in this audit and are out of scope — nothing below endorses or disputes them.

This document supersedes the StudyHub-related rows in `RESUME_METRICS_AUDIT.md`'s template, now that the actual resume text and the four executed P0 benchmark experiments (`benchmarks/reports/`) are both available. Every claim below was checked against source directly (grep counts, direct function calls, or the executed benchmark results) — not against the project's own `ARCHITECTURE.md`/`SECURITY.md`, which are themselves claims.

---

## Headline finding

**The StudyHub resume bullets are, overall, unusually well-grounded.** Of the roughly 20 distinct numeric claims checked against source in this audit (model counts, index counts, handler counts, key-family counts, provider/model counts, lock-site counts, savepoint counts, TTL/timing values), **every single one matched source exactly or within plausible commit-to-commit drift.** This is a materially different situation from a typical resume audit — the task here is less "find the inflated numbers" and more "confirm precisely which claims are static facts (safe as-is) vs. which are performance/outcome claims that need the benchmark evidence to back them, since a correct count and a defensible performance claim are different kinds of evidence."

---

## Claim-by-claim table

Resume bullet (StudyHub section) | Claim type | Evidence | Verification method | Status | Recommended action
---|---|---|---|---|---
"**53-model** ORM architecture" | Static implementation fact | 52 models counted via `grep -cE "^class [A-Za-z0-9_]+\(db\.Model\)" models.py` at this commit | Programmatic count | **Minor discrepancy (52 vs. 53)** | Update to 52, or note "~53" — plausible single-model drift since the resume was last updated; not worth treating as a red flag, but an interviewer could check this in 30 seconds, so fix it |
"**42** explicit indexes (partial, GIN, functional)" | Static implementation fact | 43 counted via `grep -ciE "db\.Index\(\|Index\("` in `models.py` | Programmatic count | **Verified (within 1, plausible drift)** | Keep, or update to 43 |
"**28** inbound Socket.IO handlers" | Static implementation fact | **Exactly 28** — 13 in `services/websocket_messages.py` + 15 in `services/websocket_threads.py`. A third file, `services/websocket_events.py`, has 18 more `@socketio.on(...)` handlers with overlapping names, but is confirmed dead code by the project's own `app.py` comment ("confirmed unused — not imported by this file or anywhere else") and independently verified here: its one live import site (`routes/student/study_sessions.py::_partner_online`) only reads a dict (`ws_manager.online_users`) that is never populated, wrapped in a bare `except: return None`. The resume bullet correctly counts only the live handlers. | Direct grep + source trace of the one import site | **Verified exactly, and notably correct about excluding dead code** | Keep as-is |
"**3** Redis key types" (WebSocket presence, 120s TTL, 45s refresh sweep) | Static implementation fact | Exactly 3: `_sock_key`, `_user_sockets_key`, `_active_thread_key` in `services/presence_service.py`. `PRESENCE_TTL_SECONDS = 120` confirmed at line 61. 45s sweep interval referenced in the module's own comments (not independently re-measured, but consistent with the documented design) | Direct source read | **Verified** | Keep as-is |
"**6** API vendors across **14** configured models" | Static implementation fact | 6 providers confirmed (Gemini, Groq, Cohere, Cloudflare, Mistral, OpenRouter). Model list lengths: Groq 4, Gemini 3, Mistral 1, Cohere 2, Cloudflare 3, OpenRouter 1 = **14 exactly** | Direct source read + count | **Verified exactly** | Keep as-is |
"Mapped errors to **4** structured fault classes" | Static implementation fact | Confirmed: `KEY_FAULT`, `PROVIDER_TRANSIENT`, `BAD_MODEL`, `NON_RETRYABLE` — the exact 4 categories `classify_provider_error()` returns | Direct source read + M3 benchmark (36/37 boundary cases classify correctly into these 4) | **Verified**, with a caveat | See M3 note below — the bullet itself (describing the taxonomy) is accurate; a narrow classification gap exists in one boundary case, documented separately, not something this bullet claims to not have |
"**1-hour** per-key cooldown[s] and a **30-minute** provider-type blacklist after **3** failures in **5** minutes" | Static implementation fact | `cooldown_period = 3600` (1h), `PROVIDER_BLACKLIST_DURATION = 1800` (30min), `PROVIDER_FAILURE_THRESHOLD = 3`, `PROVIDER_FAILURE_WINDOW = 300` (5min) — all four numbers confirmed exactly | Direct source read | **Verified exactly** | Keep as-is |
"**5** Redis key families" (AI provider state) | Static implementation fact | Exactly 5: `_REDIS_COOLDOWN_PREFIX`, `_REDIS_TYPE_FAILS_PREFIX`, `_REDIS_BLACKLIST_PREFIX`, `_REDIS_ROTATION_KEY`, `_REDIS_MODELS_PREFIX` | Direct source read | **Verified exactly** | Keep as-is |
"**5** lock-guarded scheduled jobs (SET NX EX with Lua compare-and-delete release)" | Static implementation fact + correctness claim | 5 jobs confirmed in `scheduler.py`. The locking mechanism description (atomic `SET NX EX`, Lua compare-and-delete) independently verified in a prior phase of this audit and re-confirmed by **M1 and M2's executed benchmark results** (0 overlap violations / 90 trials; 30/30 correctly failed closed under 3 outage modes) | Source read + M1/M2 benchmarks | **Verified, and now backed by executed correctness evidence, not just a source description** | Keep, and this is the strongest candidate for pairing with a benchmark-backed elaboration — see Priority 1 below |
"**2** durable RQ queues with per-job retry/backoff policies" | Static implementation fact | Confirmed: `email_queue`, `maintenance_queue`; retry policies in `services/jobs/job_specs.py` previously verified exactly (`send_email_job`: 3/[10,60], `cleanup_expired_activity_feed_job`: 3/[30,300], `alert_stale_ai_conversations_job`: 2/[60]) | Direct source read (prior audit phase) | **Verified** | Keep as-is |
"**3** SELECT FOR UPDATE sites" | Static implementation fact | Exactly 3: all in `routes/student/threads/membership.py` (`Thread.query.with_for_update()`, lines 524, 819, 1045) | Direct grep | **Verified exactly** | Keep as-is |
"**2** transaction savepoints" | Static implementation fact | Exactly 2 real application usages of `db.session.begin_nested()`: `routes/student/connections/crud.py:370`, `routes/student/learnora/__init__.py:629` | Direct grep | **Verified exactly** | Keep as-is |
"SHA-256-hashed refresh token rotation with reuse detection, family_id replay detection, and a **10-second** grace window" | Static implementation fact | `REUSE_GRACE_SECONDS = 10` confirmed exactly in `services/auth_service.py` | Direct source read | **Verified exactly** | Keep as-is |
"**Impact snapshot**" summary line (combines the above static counts into one line) | Static implementation facts, bundled | Each component independently verified above | See rows above | **Verified** (modulo the 52-vs-53 model-count note) | Keep, update 53→52 |

---

## What's conspicuously absent from the StudyHub bullets, and why that's a good sign

Unlike the Kith and FounderSales sections (not audited here, out of scope), **the StudyHub bullets contain no bare percentage-improvement claims** ("reduced latency by X%," "cut costs by Y%") — every quantified claim is either a static implementation count (verified above) or a qualitative design description ("mid-stream SSE failover designed to maintain service availability," "automated response sanitization"). This matters directly: the mission's own standard is that a percentage requires a defensible, reproducible baseline, and this section simply doesn't make that kind of claim for StudyHub, so there's nothing here to flag as unsupported on those grounds. The one exception worth naming explicitly: **there is no claim in the StudyHub section that this audit's benchmark evidence contradicts.**

---

## Benchmark-backed additions worth considering

The four executed experiments (`benchmarks/reports/`) produce evidence stronger than a static count — they're correctness results under real concurrency, which the current bullets don't claim at all (the bullets describe the *mechanism*, not that it was *tested under load*). These are additions, not corrections:

### Priority 1 — pair with the existing "5 lock-guarded scheduled jobs" bullet

> Add, as a sub-point or a short clause: *"verified via a controlled concurrency test (90 trials, up to 25 simultaneous processes) that the lock produces zero mutual-exclusion violations, and via 3 independent Redis-failure simulations that it correctly fails closed rather than silently granting access."*

**Evidence type:** synthetic benchmark (correctness, not timing) — label it as such in an interview, not as a production measurement.
**Source:** `benchmarks/m1_lock_concurrency.py`, `benchmarks/m2_lock_fail_closed.py`; results in `benchmarks/results/m1_lock_concurrency/m1_lock_concurrency_20261014T105206Z_cc44c3e8.json` and `benchmarks/results/m2_lock_fail_closed/m2_lock_fail_closed_20261014T105559Z_c11af389.json`.
**Interviewer-ready explanation:** *"I wrote a standalone test harness that spawns N real OS processes (not threads — threads can hide races a naive lock wouldn't actually survive) that all try to acquire the same lock at the same synchronized instant, using a barrier so the attempt is genuinely simultaneous rather than just launched close together. Across 90 trials at up to 25 concurrent processes, no two holders' lock-held time windows ever overlapped. I also built a deliberately broken reference lock (GET-then-SET, not atomic) through the identical test, and it failed — hundreds of overlap violations, scaling with concurrency — which is what confirms the test itself can actually detect a race when one exists, not just pass by construction. Separately, I simulated three realistic Redis-outage shapes — a dead connection, a thrown ConnectionError, a thrown TimeoutError — and confirmed the lock never silently grants access when Redis is unreachable, 30/30 times."*
**What it demonstrates:** the lock is correct under real concurrent contention, on this host, at this scale, and fails safely under simulated Redis unavailability.
**What it does not demonstrate:** correctness across physically separate hosts with real network latency/partitions, or at concurrency levels beyond 25 (see limitations in `benchmarks/reports/M1_lock_concurrency.md`).

### Priority 2 — new standalone bullet: atomic counter correctness

> *"Demonstrated via a controlled concurrent-write benchmark that the Redis-INCR-based unread-counter implementation preserved exact counts under up to 100 simultaneous increments (15 trials, zero lost updates), while a naive read-then-write reference implementation lost a median of 63% of updates at the same concurrency level — isolating atomicity as the specific property responsible for the difference."*

**Evidence type:** synthetic benchmark with a negative control.
**Source:** `benchmarks/m7_atomic_counter.py`; results in `benchmarks/results/m7_atomic_counter/m7_atomic_counter_20261015T062138Z_28127ea1.json`.
**Interviewer-ready explanation:** *"The notification unread-counter uses Redis INCRBY, which is atomic — one round trip, no read-then-write gap. To actually prove that matters rather than just asserting it, I wrote a harness that fires N concurrent increment calls from real separate processes, synchronized to hit at the same instant, and checks whether the final count equals N. The real implementation never lost an update across 15 trials at up to 100 concurrent increments. I also built a throwaway naive version — GET the value, add 1 in Python, SET it back — run through the exact same test, and it lost a consistent, scaling majority of updates: a median of 63% at N=100. That comparison is what makes the atomic version's result meaningful rather than just 'it passed.'"*
**What it demonstrates:** the atomic counter preserves correctness under real concurrent load at the tested scale.
**What it does not demonstrate:** behavior at the originally-targeted N=1,000 — not reached in this environment due to process-spawn overhead on a 2-CPU sandbox, documented honestly rather than extrapolated. State N=100 as the tested ceiling, not "up to 1,000."

### Not recommended as a resume bullet: the M3 classification gap

The audit found a genuine, reproducible bug: `classify_provider_error()` misclassifies an `error.code` value of `"model_not_found"` (underscore-joined) as `NON_RETRYABLE` instead of `BAD_MODEL`, because the detection regex requires a literal space in `"not found"`. This is real, independently reproduced outside the benchmark harness, and not previously caught by the project's own test suite. **It is not resume material as an achievement** — it's an unfixed gap in the candidate's own code, not a shipped improvement. It is, however, excellent interview material if the AI-provider-failover bullet comes up: it demonstrates the ability to find real edge cases in one's own system under scrutiny, which is a more credible signal than a clean pass rate alone. Frame it only if asked how confident you are in the classifier's correctness — "confident at the category level; while building a boundary-case test suite for this resume I found and reproduced one narrow gap in how structured error codes are matched, which I haven't shipped a fix for yet."

---

## Claims not addressed in this document

- **Kith, FounderSales, agent-broker bullets** — separate repositories, out of scope per the mission's single-project audit instruction. None of their specific numeric claims (22-table schema, 35% latency reduction, 60% LLM cost reduction, 3.2s→800ms, etc.) were checked against source in this session.
- **"Live Demo" links and GitHub URLs** — not verified for liveness/reachability; this is a documentation-accuracy check, not link validation.
- **Anything outside the StudyHub section's explicit bullets** (professional summary, technical skills list, education) — these are not falsifiable claims in the same way and weren't audited line-by-line.

---

## Summary verdict

**No StudyHub claim in the supplied resume requires removal.** One (model count, 53 vs. 52) is worth a one-character fix out of precision, not correctness. The strongest available action is additive: the two benchmark-backed bullets above (lock concurrency correctness, atomic counter correctness) would add genuinely new, stronger evidentiary weight than what's currently there, since they demonstrate tested behavior under real concurrent load rather than only a correct description of the mechanism.
