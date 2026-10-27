# StudyHub — Resume Claims Audit

**Status: blocked on input.** The mission brief for this audit (supplied as the task instructions) says a resume would be provided alongside the repository URL. Only the repository URL and the audit instructions were actually supplied in this conversation — no resume file or resume text was attached. This document cannot responsibly invent resume line items to audit against; doing so would defeat the point of the exercise (auditing *your* actual claims, not a guess at what they might be).

What's here instead:

1. The one resume-relevant line already on file from prior conversation context, audited as far as it can be.
2. A ready-to-use table template — send the actual resume text (or the specific bullet points covering this project) and this table gets filled in claim-by-claim, cross-referenced against the verified findings already produced in `METRICS_AUDIT.md`.
3. A pre-built list of verified facts and verified gaps from the source audit, organized so that whatever resume language exists can be checked against them quickly.

---

## 1. What's auditable right now

The only StudyHub-related line on record: **"StudyHub (AI + real-time academic platform)."**

| Resume claim | Claim type | Current evidence | Verification method | Status | Recommended action |
|---|---|---|---|---|---|
| "AI + real-time academic platform" | Verified static implementation fact (descriptive, not quantitative) | Confirmed: 6 LLM providers with real classified failover (`ai_provider_service.py`), 5 AI entry points (2 fully through the classified engine, 1 sharing classification logic via a separate loop, 2 bypassing classification — see `METRICS_AUDIT.md` §4); real-time layer is Flask-SocketIO with Redis-backed cross-instance presence, not a toy WebSocket demo | Direct source read, this audit | Verified | Keep as-is — it's accurate and appropriately non-quantitative. No baseline/percentage attached to it, so nothing here needs defending beyond "yes, I can describe both subsystems," which this audit confirms you can |

Everything else — endpoint counts, latency percentages, "reduced X by Y%," team size, timeline, any other line from the actual document — is unaudited because the document isn't here yet.

---

## 2. Template for the real audit (fill on resume delivery)

Resume claim | Claim type | Current evidence | Verification method | Status | Recommended action
---|---|---|---|---|---
*(paste exact resume line)* | | | | |

**Status values to use**, per the mission spec:
`Verified static implementation fact` · `Verified benchmark result` · `Verified test result` · `Verified production observation` · `Partially supported` · `Unsupported` · `Outdated or inconsistent` · `Requires a better definition`

**Decision rule for any percentage-based claim** ("reduced latency by X%", "improved throughput by X%"): per the mission's own instruction, a claim like this requires a defensible comparison against a documented baseline. Given what's in this codebase:

- If the claim refers to the **before/after of the horizontal-scaling refactor** (process-local state → Redis-coordinated state, documented in `ARCHITECTURE.md` §20), there is no preserved timing data from "before" in this repository — that comparison cannot currently be reproduced, because the "before" code no longer exists to re-run. This should be marked **Unsupported** or **Requires a better definition**, with the recommended fix being: rephrase as a correctness/capability claim ("moved presence tracking and rate limiting from process-local state to Redis-coordinated state, enabling safe multi-instance deployment") rather than a percentage, since the capability change is real and verifiable but the percentage is not reconstructable.
- If the claim refers to something **measurable today** (cache hit vs. miss latency, concurrent-vs-sequential throughput, query count before/after a batching fix), it is a **P0/P1 candidate in `METRICS_AUDIT.md`** and should be qualified as "pending benchmark" until that experiment actually runs — not asserted as verified in the meantime.
- Any number currently on the resume that cannot be traced to either of the above should default to **Unsupported**, with the recommended action being either removal, qualification ("based on local development testing, not a controlled benchmark"), or replacement with one of the P0 metrics once measured.

---

## 3. Verified facts worth drawing resume language from (source-grounded, this audit)

These are things this audit directly confirmed in source and tests, organized as safe building blocks for resume bullets — not resume bullets themselves, since exact phrasing should be the user's own:

- A Redis-backed distributed lock (atomic `SET NX EX` + Lua compare-and-delete release, fail-closed by design) coordinates 5 cron jobs across multiple scheduler-enabled processes — directly tested (`test_release_does_not_delete_when_no_longer_owner`, `test_acquire_fails_closed_on_redis_error`).
- A 4-category provider-failure classifier (`classify_provider_error`) reads structured HTTP status + parsed error body (not string-matching) to decide retry behavior across 6 LLM providers — tested with real assertions in `test_error_classification.py`.
- Multi-device WebSocket presence (TTL'd per-socket key + untimed per-user index set, self-healing/pruning on read) — confirmed directly in `presence_service.py:143-176`.
- An append-only reputation ledger (`ReputationHistory`) where the running total is independently reconstructable by summing history rows, with a single enforced write path (`award_reputation()`) — confirmed as the only mutator of `User.reputation` via a full-codebase grep.
- A denormalized-counter reconciliation job with a real, deliberate alert-vs-auto-correct split (cosmetic counters silently corrected; capacity-gating `Thread.member_count` alert-only, never auto-corrected) — directly tested (`test_reconcile_thread_member_count_drift_alert_only_never_auto_corrected`).
- CSRF double-submit validation genuinely enforced via a blueprint-wide `before_request` hook (correcting this codebase's own stale `ARCHITECTURE.md` claim that it wasn't wired up — see `METRICS_AUDIT.md` §5).
- 319 API endpoints, 52 SQLAlchemy models, 475 unit test functions across 33 files, 42 integration test functions across 10 files — all grep-verified counts at commit `a2c2996`, safe to cite as static facts (not as a proxy for quality).

## 4. Verified gaps worth knowing before an interview (so they aren't a surprise)

- The "5 entry points, one AI engine" framing needs the narrower phrasing described in `METRICS_AUDIT.md` §4 — 2 of 5 entry points bypass the classification logic entirely.
- `SearchIndex` and `WeeklyChampion` are schema-complete, UI-complete (in `WeeklyChampion`'s case) tables with no working data pipeline — don't claim full-text search or a working champions feature exists.
- `_auto_reply_buckets` is still a process-local rate limiter, acknowledged in the codebase's own migration notes as unmigrated — don't claim full horizontal-scaling parity across every rate limiter.
- Password minimum is 6 characters with no complexity requirement — a real, current limitation, not resume material either way but worth knowing if asked directly about security posture.

---

**To complete this document:** send the resume text (or just the StudyHub-related bullets). Each claim will be checked against the verified facts/gaps above and the full source audit behind `METRICS_AUDIT.md`, and this file will be republished with the completed table.
