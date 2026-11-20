# Feature Production-Readiness Audit — Connections

| | |
|---|---|
| **Repository** | `Devpaul-01/StudyHub` (cloned `main` at audit time) |
| **Feature audited** | **Connections** — connection requests/accept/reject/cancel/remove, blocking, discovery & suggestions, compatibility/AI overview, connection health/details/notes, online/available-now, seen-badges, help-request broadcast/volunteer, onboarding auto-connect |
| **Audit date** | 2026-11-19 |
| **Mode** | Read-only investigation. No application source, config, dependency, DB or test file in the repo was modified. This report is the only file added. |

**Evidence labels used throughout**

- **[Reproduced]** – reproduced by running a throwaway test against a *copy* of the repo in `/tmp` (SQLite test harness; the repo itself was not touched).
- **[Code-verified]** – confirmed by reading the exact code path; not executed.
- **[Unverified risk]** – plausible from the code but needs an environment I did not have (Postgres under concurrency, production DB state, a browser).

Existing tests were run first: `tests/integration/test_connections_*.py` + `tests/unit/services/test_connection_service.py` → **113 passed, 2 skipped** (the 2 skips are discussed in §7).

---

## 1. Executive Summary

**Overall assessment: ❌ NOT production-ready in its current state — Critical issues found.**

The feature has clearly had prior hardening (batched queries, a pair-unique index design, `blocked_by_id`, savepoints in bulk onboarding, Redis-backed rate-limit and cache plumbing). But several of the most important guarantees a social-graph feature must give — *consent, blocking, and authorization* — can be bypassed, and a core flow (re-requesting after a rejection) is completely broken.

**Findings: 25 total — 1 Critical, 5 High, 13 Medium, 6 Low.**

Most important findings:

1. **CON-01 (Critical, Reproduced)** – Three *unauthenticated* "onboarding" endpoints identify the acting user only by an email in the URL. Anyone who knows (or uses their own) email can force-accept connections with **any** user, **overriding pending, rejected and *blocked* states**, at any time (not only during onboarding). A blocked user can use this to un-block themselves and then DM the blocker.
2. **CON-02 (High, Reproduced)** – `POST /messages/unblock/<id>` restores a row to `accepted`. Block a stranger → unblock via the messaging endpoint → you are now "connected" to someone who never consented (and `can_message()` is true).
3. **CON-03 (High, Reproduced)** – Re-requesting after a rejection **always fails** (`responded_at = None` violates `NOT NULL`) and returns HTTP 400 for what is a server fault. A latent direction bug sits behind it.
4. **CON-06 (High, Reproduced)** – Any authenticated user can "volunteer" on any help request by guessing sequential IDs (no connection/target check).
5. **CON-04 (High)** – Connection-request `message` is stored raw and unbounded (a 50 KB `<img onerror>` payload was accepted) and rendered by `innerHTML` templates → stored-XSS sink.
6. **CON-05 (High, Unverified risk)** – The race protection the code relies on (`uq_connections_pair`) is created only by a Postgres `after_create` DDL hook; the repo's Alembic history contains no `connections` table or index. On any database whose `connections` table pre-dates that hook, the index silently does not exist.

Major reliability / scalability concerns: no atomic state transitions (accept/cancel/block/remove are read-then-write; **CON-08**), side effects (notifications, Redis counters, WebSocket, push) happen *before* commit (**CON-09**, **CON-17**), JSON read-modify-write for help-request volunteers (**CON-16**), silent unordered truncation and unbounded in-memory loads (**CON-15**), IP-keyed rate limits for authenticated reads and a silent `memory://` fallback (**CON-18**).

**Safe to consider production-ready?** No. CON-01, CON-02, CON-03, CON-04, CON-05 and CON-06 must be fixed first.

---

## 2. Feature Architecture and Execution Flow

### 2.1 Components

| Layer | Files |
|---|---|
| Frontend | `static/js/connection/connection.{api,constants,delegation,events,init,render,state,templates,utils}.js` (~4.3k LOC), `static/css/connection/connection.css`, `templates/onboard.html` (onboarding suggestions/connect) |
| Routes (blueprint `connections_*`, mounted under `/student`) | `routes/student/connections/crud.py` (2132 lines: request lifecycle, seen-badges, help requests, onboarding connect, settings), `blocking.py`, `discovery.py`, `compatibility.py` (AI SSE overview), `health.py` (details, notes, online lists) |
| Related routes that read/write connection state | `routes/student/messages.py` (`/messages/block`, `/messages/unblock`, `can_message` gate), `routes/student/profile.py` (connection_status), `routes/student/auth.py` (parallel onboarding endpoints + `_is_request_authorized_for_email`) |
| Services | `services/connection_service.py` (blocking helpers, `can_message`, compatibility scoring, health, mutual count), `notification_service.py`, `activity_service.py::record_live_activity`, `online_status_service.py`, `cache_service.py`, `counter_cache_service.py`, `rate_limit_service.py`, `websocket_messages.py::message_ws_manager`, `push_notifications.py`, `ai_provider_service.py` |
| Data | `connections`, `help_requests` (+ reads `users`, `student_profiles`, `onboarding_details`, `thread_members`, `messages`, `notifications`, `activity_feed`) |
| Infra | PostgreSQL (prod) / SQLite (tests), Redis (cache, unread counters, rate-limit storage, Socket.IO message queue), FCM push, external LLM providers (overview) |
| Background jobs | **None specific to this feature** (no help-request expiry job; see CON-17) |

### 2.2 Data model (`models.py:1087`)

`Connection(id, requester_id, receiver_id, status[pending|accepted|rejected|blocked], connection_type, requester_notes, receiver_notes, requested_at, responded_at, is_seen, blocked_by_id)`

- `UniqueConstraint(requester_id, receiver_id)` — *directional* only.
- `CheckConstraint(requester_id != receiver_id)`.
- Postgres-only via `DDL.execute_if`: `idx_connections_pair_status` (LEAST/GREATEST,status) and **unique** `uq_connections_pair` (LEAST/GREATEST). *Created only by `after_create` on first table creation.*
- Single-column indexes on `requester_id`, `receiver_id`, `status`, `blocked_by_id`.
- One row per user pair, so a pair has exactly one status; "mutual block" is not representable (CON-10).

`HelpRequest(id, requester_id, subject[100], message[300], status, broadcast_sent, volunteers JSON, created_at, expires_at)`.

### 2.3 Endpoint inventory (all under `/student`)

| Group | Endpoints |
|---|---|
| Lifecycle | `POST /connections/request/<uid>`, `POST /accept/<id>`, `POST /reject/<id>`, `DELETE /cancel/<id>`, `DELETE /remove/<uid>`, `GET /status/<uid>`, `GET /list`, `GET /requests/received`, `GET /requests/sent` |
| Seen badges | `GET /unseen/{received,sent,all}`, `POST /mark-seen/<id>`, `/mark-received-seen`, `/mark-sent-seen`, `/mark-all-seen` |
| Blocking | `POST /block/<uid>`, `POST /unblock/<uid>`, `GET /blocked`, `GET /blocked/list` (+ `POST /messages/block`, `/messages/unblock`) |
| Discovery | `GET /mutual/<uid>`, `/suggestions`, `/suggestions/flat`, `/search`, `/mutuals/discover`, `/available-now` |
| Health / details | `GET /<id>/details`, `/online`, `/online/count`, `/online/department`, `GET/PUT/POST /<id>/notes[/update]`, `GET /overview/<uid>` (AI SSE) |
| Help requests | `POST /help/broadcast`, `POST /help/<id>/volunteer`, `GET /help/<id>/volunteers`, `POST /help/find` |
| Onboarding (**no auth**) | `GET /suggestions-by-email/<email>`, `POST /onboard-connect/<email>/<uid>`, `POST /onboard-connect-all/<email>` |
| Settings | `POST /connections/settings` |

### 2.4 End-to-end flow (request → accept)

```mermaid
sequenceDiagram
    participant UI as connection.*.js
    participant API as crud.py
    participant DB as Postgres (connections)
    participant R as Redis (counters/cache/WS queue)
    participant WS as Socket.IO
    UI->>API: POST /connections/request/<uid> {message}
    API->>DB: SELECT pair row (either direction)
    alt existing row
        API-->>UI: already_connected / pending_sent / re-request path / blocked 403
    else no row
        API->>API: gather_user_data x2, compute compatibility
        alt score >= 70
            API->>DB: INSERT status=accepted
            API->>DB: notify() x2 -> flush
            API->>R: INCR unread x2 (BEFORE commit)
            API->>WS: broadcast x2 (BEFORE commit)
        else
            API->>DB: INSERT status=pending
            API->>DB: notify() -> flush, INCR, broadcast (BEFORE commit)
        end
        API->>DB: COMMIT (IntegrityError on uq_connections_pair -> rollback, "already")
    end
    API-->>UI: {is_instant, connection_status, ...}
    UI->>API: GET x5 (received,sent,suggestions,list,discover)  (reload)
    Note over UI,API: Receiver: GET /requests/received -> POST /accept/<id>
    API->>DB: SELECT by id; check receiver; check pending; UPDATE accepted; notify; COMMIT
    API->>DB: record_live_activity (own commit, swallows errors)
```

### 2.5 Frontend state

`ConnectionState` (in-memory per tab): `connections{connected,received,sent,suggestions,discovery}`, `loadedFilters`, `badges`, `currentTab`, pagination fields (`connectedCurrentPage`, `hasMoreConnected` — **never read outside the state file**), `currentConnect`. A 3-minute `setInterval` (`POLLING_INTERVAL`) refreshes badges. All 5 list calls are issued with `Promise.all` — any one failure fails the whole load.

---
## 3. Findings and Issues

Severity summary: **Critical 1 · High 5 · Medium 13 · Low 6**.

| ID | Title | Sev | Category | Evidence |
|---|---|---|---|---|
| CON-01 | Unauthenticated onboarding-connect endpoints bypass auth, consent and blocks | Critical | Security | Reproduced |
| CON-02 | `/messages/unblock` converts a block into an accepted connection | High | Security | Reproduced |
| CON-03 | Re-request after rejection always fails; latent direction bug | High | Bug | Reproduced |
| CON-04 | Unsanitized, unbounded request message → stored-XSS sink | High | Security | Reproduced (server) / Code-verified (sink) |
| CON-05 | `uq_connections_pair` not guaranteed to exist in deployed DBs | High | Data Integrity | Unverified risk |
| CON-06 | Anyone can volunteer on any help request (no connection/target check) | High | Security | Reproduced |
| CON-07 | Single `is_seen` flag shared by both parties breaks "response" badge | Medium | Bug | Reproduced |
| CON-08 | State transitions are non-atomic read-then-write (accept/cancel/block/remove) | Medium | Reliability | Unverified risk |
| CON-09 | Notifications, Redis counters, WS emits happen before commit | Medium | Data Integrity | Code-verified |
| CON-10 | Block can be taken over by the blocked user; mutual block unrepresentable | Medium | Bug | Reproduced |
| CON-11 | Details/notes endpoints are not status-gated (blocked party keeps access) | Medium | Security | Reproduced |
| CON-12 | Frontend: `ReferenceError` after a successful request; errors swallowed | Medium | Bug | Code-verified |
| CON-13 | Frontend/back-end contract mismatch on blocked-users list | Medium | Bug | Backend reproduced / FE code-verified |
| CON-14 | `available-now`: wrong day casing, UTC slotting, divergent "online" | Medium | Bug | Code-verified |
| CON-15 | Silent unordered truncation + unbounded in-memory loads + N+1 | Medium | Scalability | Code-verified |
| CON-16 | Help-request volunteers: JSON read-modify-write lost updates | Medium | Reliability | Code-verified |
| CON-17 | Help requests: no validation, no expiry, dead link, push before commit | Medium | Reliability | Code-verified |
| CON-18 | Rate limits: IP-keyed authenticated reads, `memory://` fallback | Medium | Scalability | Code-verified |
| CON-19 | `requester_notes` is both the request message and a private note | Medium | Product / Data Integrity | Code-verified |
| CON-20 | Mutual/health caches never invalidated (incl. negative sentinel) | Low | Performance/Product | Code-verified |
| CON-21 | AI overview: no block check, no caching, holds a worker for the stream | Low | Security/Cost | Code-verified |
| CON-22 | Inconsistent status codes, input validation and observability | Low | Reliability | Reproduced (400 on server fault) |
| CON-23 | Health score semantics misleading; logic duplicated | Low | Maintainability | Code-verified |
| CON-24 | Dead/duplicate code and duplicate onboarding endpoint families | Low | Maintainability | Code-verified |
| CON-25 | Activity-feed fan-out leaks connection events by default | Low | Product/Privacy | Code-verified |

---

### CON-01 — Unauthenticated onboarding-connect endpoints bypass auth, consent and blocks
- **Severity:** Critical · **Category:** Security · **Status:** Confirmed (Reproduced)
- **Location:** `routes/student/connections/crud.py` — `connections_suggestions_by_email` (L66), `onboard_connect_single` (L205), `onboard_connect_all` (L301). Caller: `templates/onboard.html` L859, L946, L977.
- **Evidence:**
  - None of the three routes has `@token_required`; identity is `User.query.filter_by(email=<url segment>)`.
  - `onboard_connect_single` (L228-L239): if *any* row exists and isn't `accepted`, it sets `status="accepted"` — including `pending`, `rejected` and **`blocked`** — and does not clear `blocked_by_id`.
  - `onboard_connect_all` (L338-L342) does the same for every id in a client-supplied, unbounded list.
  - No check that the email's owner is actually mid-onboarding (`status == "pending_onboarding"`), that the target consents, or that either user is unblocked.
  - The same codebase already solved this for its sibling endpoints: `routes/student/auth.py::_is_request_authorized_for_email` guards `POST /onboard/<email>` and `/onboard/request-all/<email>` ("A raw email in a URL carries no proof on its own"). The connections versions were never given that guard.
  - **Reproduction (throwaway test, no token sent, only CSRF header):** victim blocks attacker → `POST /student/connections/onboard-connect/<attacker-email>/<victim-id>` → HTTP 200 `"Connection accepted"`; row is now `accepted` with stale `blocked_by_id=1`. A second test force-accepted a pending request the same way.
- **Problem:** Email is not a credential. The endpoints allow forging "connection accepted" for any pair involving a known email, at any time, overriding the target's block.
- **Impact:** Consent bypass; **block bypass** (`can_message()` only needs `accepted`, so a blocked harasser can DM their blocker); fake "X connected with you" notifications; email enumeration (404 "User not found" vs 200); PII in URL/logs; `suggestions-by-email` returns names/usernames/reputation of 10 approved users to unauthenticated callers. Rate limit is only 5/min per IP (`ip_key`), trivially rotated and shared by campus NAT.
- **Recommended solution:**
  1. Require proof of ownership exactly like `auth.py`: reuse/extract `_is_request_authorized_for_email(email)` (Google session or JWT cookie) — or, better, derive identity from `token_required`/the verification token and drop the email from the path entirely.
  2. Only allow while `user.status == "pending_onboarding"`.
  3. Never mutate an existing row to `accepted` here: only create when **no row exists**; otherwise return "already connected/pending". Explicitly refuse when status is `blocked`.
  4. Cap `ids` (e.g. ≤ 25), validate ints, dedupe, batch-load targets (`IN`) instead of `User.query.get` per id.
  5. Product decision (Q1): whether onboarding should auto-accept without the target's consent at all (see `auth.py::request-all`, which sends *requests*).
- **Implementation considerations:** `onboard.html` currently calls these without a token; it already holds `onboarding_email` and the browser has the Google session/JWT cookie, so wiring the guard is low risk. Keep response shapes. Consider emitting a `404`-indistinguishable response to stop enumeration.
- **Verification:** Integration tests: (a) no auth → 401; (b) authenticated as a different email → 401/403; (c) blocked/pending/rejected row → unchanged; (d) non-onboarding user → 403; (e) `ids` > cap → 400; (f) happy path still creates `accepted` rows.

### CON-02 — `/messages/unblock` converts a block into an accepted connection
- **Severity:** High · **Category:** Security · **Status:** Confirmed (Reproduced)
- **Location:** `routes/student/messages.py` L1406-1427 (`unblock_user_messaging`), `services/connection_service.py::unblock_connection(..., restore_to_accepted=True)`; contrast `routes/student/connections/blocking.py::unblock_user` (deletes the row).
- **Evidence:** `block_connection()` creates a row `status="blocked"` when none existed. `unblock_connection(restore_to_accepted=True)` then sets `status="accepted"`. Reproduction: A blocks B (no prior relationship) → A calls `/messages/unblock/B` → row `accepted`.
- **Problem:** Unblock should restore the *prior* relationship (or none), not manufacture a mutual connection. Also, the two unblock endpoints have different semantics for the same action.
- **Impact:** Users can become "connected" (DM-able, appear in each other's lists, receive activity fan-out) without either party consenting; a blocker who only meant to block a stranger ends up connected to them.
- **Recommended solution:** Make both unblock paths consistent. Simplest: always delete the row on unblock (user must re-request), *or* persist the pre-block status (`status_before_block`) and restore only if it was `accepted`. See Q2.
- **Implementation considerations:** Option "restore prior status" needs a nullable column + migration; option "delete" needs none but loses an accepted friendship after a block/unblock cycle — product call. The messaging UI may rely on instant restore.
- **Verification:** Tests: block stranger → unblock (both endpoints) → no accepted row; block accepted friend → unblock → behaviour per decision; both endpoints behave identically.

### CON-03 — Re-request after rejection always fails; latent direction bug
- **Severity:** High · **Category:** Bug · **Status:** Confirmed (Reproduced)
- **Location:** `crud.py` L1626-1655 (`send_connection_request`, `existing.status == "rejected"` branch); `models.py` L1102 (`responded_at ... nullable=False`).
- **Evidence:** The branch sets `existing.responded_at = None`; the column is `NOT NULL`. Reproduction: rejected row older than 24 h → request → HTTP **400** `"Failed to send connection request"`; log: `NOT NULL constraint failed: connections.responded_at` (raised inside `notify()`'s flush). The cooldown logic therefore only ever produces a permanent failure after it expires.
- **Latent bug behind it (Code-verified):** the branch reuses the old row **without swapping direction**. If A asked B, B rejected, and later *B* requests A, the row stays `requester=A → receiver=B, pending`; A is notified "B sent you a connection request again" but A cannot accept (`receiver_id != A`), and B sees their own outgoing request in "received".
- **Impact:** Users can never reconnect after a rejection; wrong-party notifications once the NOT NULL bug is fixed.
- **Recommended solution:** Set `responded_at = requested_at` (or make the column nullable via migration and treat `NULL` as "not responded"); when the re-requester is the original *receiver*, swap `requester_id`/`receiver_id` (safe: the row is `rejected`, and `blocked_by_id` is NULL) and reset `is_seen=False` (CON-07). Compute remaining cooldown with `ceil`.
- **Verification:** Tests for both directions after cooldown; during cooldown → 429 with correct remaining; receiver can accept the re-sent request.

### CON-04 — Unsanitized, unbounded request message → stored-XSS sink
- **Severity:** High · **Category:** Security · **Status:** Server side Reproduced; rendering sink Code-verified (not run in a browser)
- **Location:** `crud.py` L1688-1700/L1787 (message stored as `requester_notes`, `.strip()` only); returned at L972 (`message`) and L1116 (`your_message`); rendered in `static/js/connection/connection.templates.js` L289/L291, L402-404 via template literals into `innerHTML`.
- **Evidence:** A request whose message was `<img src=x onerror=alert(1)>` + 50,000 `A` characters returned 201 and was stored verbatim (50,028 chars). `bleach` is used in thread messaging but nowhere in `routes/student/connections/`. The templates also interpolate `user.name`, `user.bio` unescaped (upstream sanitization of those profile fields is **not verified** in this audit).
- **Impact:** Cross-user script execution in the receiver's session (token theft is limited if cookies are `HttpOnly`, but the app also reads `localStorage.token` in `getOverviewStream`), plus unbounded row/response size.
- **Recommended solution:** Server: cap message length (e.g. 300), `bleach.clean(..., tags=[], strip=True)` like `thread_message_service`, reject non-string. Client: add an `escapeHtml` helper in `connection.utils.js` and use it for every interpolated user-controlled field (or build nodes with `textContent`).
- **Verification:** Unit test on sanitizer; integration test: HTML in message is stored escaped/stripped and over-length → 400; frontend test/lint rule for unescaped interpolation in templates.

### CON-05 — `uq_connections_pair` is not guaranteed to exist in deployed databases
- **Severity:** High · **Category:** Data Integrity · **Status:** Unverified risk (I had no access to a production/staging DB)
- **Location:** `models.py` L1143-1181 (DDL + `after_create` listeners); `migrations/versions/` (only `f75e60334a86` message_reports and `b17c0a9d2e41` bookmarks; **no connections table or index**); `app.py` L629 (`db.create_all()` inside `__main__`); `tests/integration/test_connections_flow.py` L106 (race test skipped; SQLite can't run it).
- **Evidence:** `after_create` fires only when SQLAlchemy actually creates the table. A database whose `connections` table existed before these listeners were added never gets `uq_connections_pair` or `idx_connections_pair_status`. Only the *directional* `unique_connection` constraint is guaranteed. All the `IntegrityError` handling in `send_connection_request` / onboarding therefore may be dead code in production, and A→B + B→A duplicates become possible.
- **Impact:** Duplicate/contradictory rows for one pair (e.g. both `pending`, or `accepted`+`blocked`), violating assumptions in `_find_connection_between(...).first()`, `is_user_blocked`, counts, etc. Also the missing lookup index makes OR-queries slower.
- **Recommended solution:** Add an Alembic migration (idempotent `CREATE UNIQUE INDEX IF NOT EXISTS uq_connections_pair ...`, preceded by a de-duplication step) — and a one-off read-only check first (see Q8). Adopt Alembic as the single schema authority for this table.
- **Implementation considerations:** De-dup needs a rule for which row wins (blocked > accepted > pending > rejected is a reasonable precedence — needs your approval). `CREATE INDEX CONCURRENTLY` avoids locking.
- **Verification:** Run on a Postgres test DB: the skipped race test un-skipped (two reversed inserts → `IntegrityError`); migration test on a DB created without the index.

### CON-06 — Anyone can volunteer on any help request
- **Severity:** High · **Category:** Security · **Status:** Confirmed (Reproduced)
- **Location:** `crud.py` L573-686 (`volunteer_for_help`); also `get_help_volunteers` is correctly owner-only (L688).
- **Evidence:** The route checks only "exists, active, not expired, not own request, not already volunteered". A user with **no connection** to the requester volunteered successfully (HTTP 200). IDs are sequential integers. `broadcast_help_request` only notifies ≤10 connections, but nothing records or enforces that audience. Blocked users are not checked either.
- **Impact:** Harassment/spam vector — iterate IDs and push FCM + in-app + WS notifications to arbitrary students; unconnected/blocked users appear in the requester's volunteer list with name/avatar.
- **Recommended solution:** Require an `accepted` connection between volunteer and requester (reuse `connection_service`), and reject if either side blocked. Optionally persist the notified audience (Q6). Use the atomic volunteer storage from CON-16.
- **Verification:** Stranger → 403; blocked either way → 403; connection → 200; duplicate volunteer → 400 under concurrency.

---

### CON-07 — One `is_seen` flag shared by both parties breaks the "response" badge
- **Severity:** Medium · **Category:** Bug · **Status:** Confirmed (Reproduced)
- **Location:** `models.py` L1103; `crud.py` L1284-1500 (`unseen_*`, `mark_*_seen`), `accept_connection` L1861, `reject_connection` L1929, re-request L1636.
- **Evidence:** The receiver's "unseen requests" and the requester's "unseen responses" both read/write the same boolean. Reproduction: receiver opens the requests page (`mark-received-seen` → `is_seen=True`), then accepts; requester's `GET /unseen/sent` returns **count 0**. Accept/reject/re-request never reset `is_seen`. `mark_connection_seen` lets *either* party set it, so a requester marking their own pending request suppresses the receiver's badge.
- **Impact:** Requesters miss acceptance/rejection badges; receivers can miss new/re-sent requests.
- **Recommended solution:** Split into `requester_seen` / `receiver_seen` (migration with default/backfill = `true` for historical rows), or reset `is_seen=False` on every status transition and scope `mark-seen` to the party the badge belongs to. Splitting is the correct fix; resetting is a stop-gap.
- **Verification:** The reproduced scenario must yield count 1; re-request/re-accept cycles; each party's mark-seen only affects their own badge.

### CON-08 — State transitions are non-atomic read-then-write
- **Severity:** Medium · **Category:** Reliability/Concurrency · **Status:** Unverified risk (needs concurrent sessions on Postgres)
- **Location:** `accept_connection` L1861, `reject_connection` L1929, `cancel_connection_request` L1963, `remove_connection` L1995, `connection_service.block_connection/unblock_connection`. No `with_for_update()` anywhere in the connections code (the rest of the codebase uses it in `threads/membership.py`).
- **Evidence / scenarios:**
  - *Cancel vs accept:* cancel reads `pending`, accept commits, cancel then `DELETE`s the **now-accepted** row → friendship silently destroyed.
  - *Accept vs block:* receiver's accept (read `pending`) races a block that commits first; accept then overwrites `status="accepted"` but leaves `blocked_by_id` set → the block is lost and row is inconsistent.
  - *Double block / first block creating a row:* second writer hits the unique index → generic "Failed to block user".
- **Impact:** Lost blocks (security-relevant), deleted connections, inconsistent rows. Across multiple instances these windows are real, not theoretical.
- **Recommended solution:** Make each transition a single conditional UPDATE (compare-and-set), checking `rowcount`:
  `UPDATE connections SET status='accepted', responded_at=:now, is_seen=false WHERE id=:id AND receiver_id=:me AND status='pending'` (0 rows → "no longer pending"); cancel/delete the same way (`DELETE ... WHERE id AND requester_id AND status='pending'`). Block: upsert on the pair. No new infrastructure needed; `SELECT ... FOR UPDATE` is the alternative.
- **Verification:** Threaded/Postgres tests firing accept+cancel, accept+block, block+block concurrently and asserting a single consistent final row.

### CON-09 — Notifications, Redis counters and WS broadcasts happen before commit
- **Severity:** Medium · **Category:** Data Integrity · **Status:** Code-verified
- **Location:** `services/notification_service.py::notify` L135-151 (flush + `INCR` + `_broadcast`, "does not commit"); `crud.py` L1700-1735 and L1787-1817 (notify, *then* commit with `IntegrityError → rollback`), `accept_connection` L1873.
- **Evidence:** The in-code comment in `onboard_connect_all` already explains that a SQL rollback cannot undo the Redis `INCR` — but `send_connection_request`'s rollback path does exactly that. On a lost race both users get a real-time notification and an incremented unread counter for rows that were rolled back (phantom notification, counter drift). Also failures after `notify()` but before commit (e.g. DB error) behave the same.
- **Impact:** Unread badge drifts upward until reconciliation; users see notifications pointing at nothing.
- **Recommended solution:** Commit the connection row first, then send notifications/broadcasts after a successful commit (or use an `after_commit` hook / outbox). `reconciliation_service` can remain as a safety net.
- **Verification:** Unit test that forces `IntegrityError` and asserts no notification row, no counter change, no WS emit.

### CON-10 — Block ownership can be taken over; mutual block is unrepresentable
- **Severity:** Medium · **Category:** Bug · **Status:** Confirmed (Reproduced)
- **Location:** `connection_service.py::block_connection` L67-97; docstring of `is_user_blocked` ("at most one can be True").
- **Evidence:** A blocks B, then B blocks A → `blocked_by_id` becomes B; A's own unblock now returns **403 "Not authorized"**; only B can lift it. `block_connection` also doesn't check whether the row is already `blocked`.
- **Impact:** The blocked user can seize control of the block and A can no longer manage it; if B unblocks, A's protection disappears without A's action.
- **Recommended solution:** If a `blocked` row already exists, keep `blocked_by_id` and return success (idempotent). If mutual blocks matter (Q4), model blocks as a separate `user_blocks(blocker_id, blocked_id)` table; otherwise document the limitation.
- **Verification:** Test above; plus idempotent re-block.

### CON-11 — Details/notes endpoints are not status-gated
- **Severity:** Medium · **Category:** Security/Privacy · **Status:** Confirmed (Reproduced for `/details`)
- **Location:** `health.py` L51-330 (`get_connection_details`), L665-786 (notes GET/PUT).
- **Evidence:** Authorization is only "caller is requester or receiver". After A blocks B, B's `GET /connections/<id>/details` still returns **200** with `status: "blocked"`, A's bio, onboarding preview, online status/last-active, mutual connections, shared threads, interaction counts and a 100-char last-message preview. Same for `pending`/`rejected` rows. Notes can also be read/written on any status.
- **Impact:** Blocked/rejected users keep a window into the other party's presence and profile; contradicts `block_user`'s docstring.
- **Recommended solution:** Require `status == "accepted"` for details, notes and health (return 404 otherwise); for the blocked party return 404 for the row.
- **Verification:** Matrix test (accepted/pending/rejected/blocked × requester/receiver).

### CON-12 — Frontend: `ReferenceError` after success; errors swallowed
- **Severity:** Medium · **Category:** Bug · **Status:** Code-verified (not executed in a browser)
- **Location:** `static/js/connection/connection.events.js` L21-95, specifically L63 `const card2 = target.closest('.user-card')` — `target` is not a parameter or variable of `sendConnectionRequest(userId, message, button)`.
- **Evidence:** In the non-instant branch the toast "Request sent" fires, then L63 throws `ReferenceError`; the `catch` (L85-94) restores the button text/enabled state and returns without a toast; `loadAllConnectionData()` (L69) is never reached. Also `catch` shows no error to the user for real network/4xx errors, and `L64` references `card` instead of `card2`.
- **Impact:** After a successful request the button reverts to "Send" (inviting a duplicate click, which the backend answers "already pending"), tabs aren't refreshed, real failures are invisible. `blockRequest` (L228) has no in-flight guard (double-click → two blocks).
- **Recommended solution:** Remove/fix the `card2` lines, show an error toast in `catch`, disable block button while pending. Add a shared "in-flight action" guard.
- **Verification:** Frontend unit test (jsdom) for both branches; manual check.

### CON-13 — Blocked-users list contract mismatch
- **Severity:** Medium · **Category:** Bug · **Status:** Backend shape Reproduced; frontend Code-verified
- **Location:** `blocking.py` L58-100 returns `data: {blocked_users: [...], total}`; `connection.api.js::getBlockedUsers` returns `response?.data`; `connection.events.js::viewBlockedUsers` L292-305 treats it as an array (`.length`, `.map`).
- **Evidence:** `data` is an object, so `.length` is `undefined` (not 0) and `.map` throws → the catch renders "Failed to load blocked users" always. Users cannot reach the unblock UI.
- **Recommended solution:** Return `response?.data?.blocked_users || []` in the API module (frontend-only fix) and consolidate the two backend endpoints (CON-24).
- **Verification:** Frontend test with the real response fixture.

### CON-14 — `available-now`: wrong day casing, UTC slotting, divergent "online"
- **Severity:** Medium · **Category:** Bug/Product · **Status:** Code-verified
- **Location:** `discovery.py` L1294-1349 (`get_available_connections`); `health.py` L420/L623 (inline `minutes_ago`); `services/online_status_service.py` (documented as the single source of truth).
- **Evidence:** `day_name = now.strftime("%A").lower()` → `"monday"`, but schedules are stored with capitalised keys (`templates/onboard.html` `data-day="Monday"`; `calculate_schedule_overlap` documents the same contract) so `schedule_available` is effectively always `False`. Time slots use server UTC and invent a `"night"` slot that does not exist in the schema. "Online" here is `last_active < 30 min` while other endpoints use `get_user_online_status` (which also honours `in_study_session`) → the same user can be online in one list and offline in another.
- **Recommended solution:** Capitalise day names; store/derive each user's timezone (Q13) or at least document UTC; use `online_status_service` everywhere; drop `"night"`.
- **Verification:** Test with a frozen clock and a Monday-morning schedule; cross-endpoint online-state consistency test.

### CON-15 — Silent unordered truncation, unbounded loads and N+1 in hot paths
- **Severity:** Medium · **Category:** Scalability/Correctness · **Status:** Code-verified
- **Location & evidence:**
  - `list_connections` L1143: `.limit(200)` **without `ORDER BY`** — users with >200 connections get an arbitrary subset and `total` reports the truncated count.
  - `received_connection_requests` (limit 100), `connection_suggestions` (`.limit(100)` unordered candidate scan), `find_help_with_subject` (first 200 approved users, unordered) — results depend on physical row order and degrade as the user base grows.
  - `discover_mutual_connections`, `get_mutual_connection_count`, `get_connection_details`, `get_mutual_connections` load *all* accepted rows for both users into Python and intersect sets; `connection_suggestions` also uses `notin_(excluded_ids)` with an ever-growing list.
  - `broadcast_help_request` L463-478: `User.query.get` + `OnboardingDetails.query.filter_by(...).first()` **per connection** (2N queries) although only 10 are notified.
  - `list_blocked_users*`: `StudentProfile` / `User.query.get` per row.
- **Impact:** Latency and memory grow with graph size; users with large networks silently lose data. Fine at hundreds of users, a problem at tens of thousands.
- **Recommended solution:** Add deterministic `ORDER BY` + real pagination (keyset on `id`/`responded_at`) with a `has_more`/`total` computed by `COUNT`; compute mutual counts in SQL (`JOIN`/`INTERSECT` on the normalized pair); batch-load in broadcast. Frontend already has unused pagination state (`hasMoreConnected`), so the contract can stay backward compatible by defaulting `limit`.
- **Verification:** Seed 500+ connections: list returns all pages in stable order; query-count assertions on broadcast.

### CON-16 — Help-request volunteers: lost updates under concurrency
- **Severity:** Medium · **Category:** Reliability · **Status:** Code-verified
- **Location:** `models.py` L235 (`volunteers = MutableList(JSON)`), `crud.py` L611-633.
- **Evidence:** Read list → check duplicate → append → assign → commit; no lock or version column. Two volunteers (or one double-click, or two instances) writing concurrently produce last-write-wins: one volunteer lost, or the same user twice.
- **Recommended solution:** Move volunteers to a child table `help_request_volunteers(help_request_id, user_id, created_at, UNIQUE(help_request_id, user_id))` and `INSERT ... ON CONFLICT DO NOTHING` (idempotent, atomic, queryable). Migration must backfill from the JSON. Lighter alternative: `SELECT ... FOR UPDATE` on the help request row.
- **Verification:** Concurrency test with N parallel volunteers → N rows, no duplicates.

### CON-17 — Help requests: validation, lifecycle and side-effect ordering
- **Severity:** Medium · **Category:** Reliability · **Status:** Code-verified
- **Location:** `crud.py` L411-570 (broadcast), L573-686 (volunteer); `models.py` L226-245.
- **Evidence:**
  - **No input validation:** only `not subject`. `subject` is `String(100)`, `message` `String(300)`; on Postgres over-length raises `DataError` → generic failure (Postgres) with no field-level message. `request.get_json()` may be `None`; non-string values break `.strip()`.
  - **Double-submit:** "expire previous active" + insert is not atomic; two concurrent broadcasts both stay `active`.
  - **No expiry job:** `scheduler.py`/jobs contain nothing for `HelpRequest`; `status` stays `active` forever unless someone touches it; `GET /help/<id>/volunteers` reports `status: "active"` for expired requests.
  - **Dead link:** notifications use `link=/help-request/<id>`; no route, template or JS handles that path (grep).
  - **Side effects before commit:** FCM multicast (`send_multicast`) and `notify()` run *before* `db.session.commit()` (L519-545); if the commit fails the push cannot be recalled. Volunteer flow likewise pushes/emits before commit (L637-681).
  - **Targeting vs comment:** the comment says "include everyone who has any relevance, plus a small group of others" but the code just takes the top 10 by score including **score 0** (irrelevant) connections; `subject_lower in subj.lower()` is a substring match ("C" matches "Calculus").
  - `broadcast_sent` is written but never read.
- **Recommended solution:** Validate type/length (400 with message); partial unique index `(requester_id) WHERE status='active'` or a locked check; add an expiry sweep via the existing `job_queue`/scheduler (guard with `distributed_lock`); add the missing page/route or change the link to something that exists; commit before push/emit; filter score > 0 (decide fallback, Q6).
- **Verification:** Over-length → 400; concurrent broadcasts → one active; expiry job test; push mocked and asserted after commit.

### CON-18 — Rate limits: IP-keyed authenticated reads and per-instance fallback
- **Severity:** Medium · **Category:** Scalability · **Status:** Code-verified
- **Location:** `crud.py` (`received/sent/list` use `key_func=ip_key`, `PUBLIC_READ` 300/min), `blocking.py`, `discovery.py`; `services/rate_limit_service.py` L131-160; `config.py` L119/L141/L175.
- **Evidence:** Authenticated, per-user read endpoints are keyed by IP, so everyone behind one campus NAT shares 300/min and a user on a shared IP can exhaust others' limits (the page makes ≥5 calls per load + a 3-minute poll). Storage defaults to `memory://` (base config) and in production silently falls back to `memory://` if `REDIS_URL`/`RATE_LIMIT_STORAGE_URI` is unset — then every instance has its own counters (effective limit × N). Limiter is configured fail-open (`SWALLOW_ERRORS`), so a Redis outage removes limiting entirely, including the 5/min limit that is the only protection on CON-01.
- **Recommended solution:** Use `user_or_ip_key` for authenticated routes; fail startup (or log at ERROR) in production when storage is `memory://` and more than one worker is configured; keep fail-open only where acceptable.
- **Verification:** Unit test for key function; deployment check.

### CON-19 — `requester_notes` is both the request message and a private note
- **Severity:** Medium · **Category:** Product/Data Integrity · **Status:** Code-verified
- **Location:** `crud.py` L1700/L1787/L248/L352 (write), L972/L1116 (read as `message`/`your_message`), `health.py` notes endpoints.
- **Evidence:** The same column stores the message the receiver sees, auto-generated strings ("Instant connection (compatibility: 82%)", "Connected during onboarding") and the requester's later "private notes". Because `update_connection_notes` works on any status, the requester can **edit the message of a still-pending request after sending it**, changing what the receiver sees. Receiver's side uses a different column (`receiver_notes`), so the data model is asymmetric.
- **Recommended solution:** Add `request_message` (immutable after send) and keep `requester_notes`/`receiver_notes` strictly private; backfill `request_message` from existing `requester_notes` for non-accepted rows. Stop writing generated strings into user-visible notes.
- **Implementation considerations:** Migration + two-release rollout (dual-read). Decision Q11.
- **Verification:** Editing notes never changes the received-request message.

### CON-20 — Mutual-count and health caches are never invalidated
- **Severity:** Low · **Category:** Performance/Product · **Status:** Code-verified
- **Location:** `connection_service.py` L426 (`sh:1:conn:mutual:{lo}:{hi}`, 300 s), L587 (`sh:1:conn:health:{a}:{b}`, 180 s, including a cached "no connection" sentinel). No `delete` call exists for either key (grep), and accept/remove/block don't touch them.
- **Impact:** Right after accepting, `health` may be `None` (cached sentinel) for up to 3 minutes; mutual counts are stale up to 5 minutes and after removals. Health key is directional (two keys per pair).
- **Recommended solution:** Delete both keys for the pair on accept/remove/block, or drop the sentinel caching; canonicalise the health key.
- **Verification:** Test: details before/after accept shows fresh health.

### CON-21 — AI overview: no block check, no caching, long-held worker
- **Severity:** Low · **Category:** Security/Cost · **Status:** Code-verified
- **Location:** `compatibility.py` L50-290.
- **Evidence:** Any user can request an overview of any user (also one who blocked them); `gather_user_data` (bio, subjects, badges, recent topics) is sent to an external LLM and streamed back. Nothing is cached per pair, so repeated views re-bill the provider (limited to 10/hour/user). The SSE generator runs inside a worker thread for the whole stream (`threading` mode).
- **Recommended solution:** Refuse when either side blocked; cache the structured part per pair for a short TTL; ensure no DB session is held during streaming (not verified).
- **Verification:** Blocked → 403; second call within TTL makes no provider call.

### CON-22 — Inconsistent status codes, validation and observability
- **Severity:** Low · **Category:** Reliability · **Status:** Reproduced (400 on a server fault)
- **Location:** `error_response` default (400) used for 5xx-type failures across all handlers; `accept_connection` returns 400 for "not found"/"not authorized" while `reject`/`cancel` use 404/403; `connections_settings` (L2107) lacks `rollback`, doesn't validate `enable_sound`, and `request.get_json()` can be `None`; log lines like `"Send connection request error: "` have an empty message.
- **Impact:** Clients and monitoring cannot distinguish bad input from server faults (the CON-03 failure surfaced as a 400); error-rate alerting is blind.
- **Recommended solution:** Return 500 for unexpected exceptions (generic message, keep detail in logs), 404/403 consistently, validate JSON bodies once via a small helper, add request IDs to log lines, and count `connections.*` failures as metrics.

### CON-23 — Health score semantics misleading; logic duplicated
- **Severity:** Low · **Category:** Maintainability/Product
- **Location:** `connection_service.get_connection_health_batch` vs inline copy in `crud.py::list_connections` L1213-1238.
- **Evidence:** "last_interaction" is `responded_at` (acceptance/block/rejection time), not an interaction (messages ignored), so every healthy friendship decays after 30 days regardless of activity; the formula is duplicated in two places and can drift.
- **Recommended solution:** Use the batch service function in `list_connections`; base recency on last message/thread activity (Q-product) or rename the metric.

### CON-24 — Dead/duplicate code and duplicate onboarding endpoint families
- **Severity:** Low · **Category:** Maintainability · **Status:** Code-verified (see §5 for classification)
- **Evidence:** `GET /connections/blocked` duplicates `/connections/blocked/list` (frontend uses only `/list`); `/connections/{unseen/all,mark-seen/<id>,mark-all-seen,status/<uid>}` have no frontend caller in this repo; `study_history` is a hard-coded empty object in `/details` (the frontend deliberately ignores it); `thirty_days_ago` is an unused local; `connectedCurrentPage/hasMoreConnected` unused; every route module imports unused names (`Response`, `stream_with_context`, `json`, `random`, `timedelta`, `case`, `Notification`, …). Two parallel onboarding families exist: `/student/onboard/*` in `auth.py` (guarded, sends *requests*) and `/connections/onboard-*` (unguarded, auto-accept) — divergent security posture (see CON-01).
- **Recommended solution:** After confirming no mobile/third-party consumers (Q14), remove/merge; keep one onboarding implementation.

### CON-25 — Activity-feed fan-out leaks connection events by default
- **Severity:** Low · **Category:** Product/Privacy · **Status:** Code-verified
- **Location:** `crud.py` L1881 (`record_live_activity(... 'connection_accepted', other_user_name)`), L493/L541 (`help_request_broadcast` with subject); `activity_service.record_live_activity` (opt-out via `privacy_settings["show_in_activity_feed"]`, default `True`).
- **Evidence:** Accepting a request broadcasts "A connected with B" to all of A's connections; a help-request subject is shown to **all** connections although only 10 are notified.
- **Recommended solution:** Product decision (Q10): default off or per-event scopes.

---
## 4. Horizontal Scalability Assessment

### 4.1 Current architecture and assumptions
- **Shared state is mostly external:** Postgres is the source of truth; Redis backs the cache (`cache_service`), unread counters (`counter_cache_service`), rate-limit storage (when configured) and the Socket.IO `message_queue` (used by `message_ws_manager.emit_to_user`, so volunteer/notification emits can cross instances).
- **No instance-local state in the connections code itself:** no module-level dicts, locks or queues were found in `routes/student/connections/*` or `connection_service.py`. Frontend state is per-tab only.
- **No background jobs** belong to this feature, so nothing can run twice today. (This is also the gap: help requests never expire, CON-17.)
- `start-all.py` runs API + RQ worker + scheduler in one process; behind a load balancer each replica would run its own scheduler (outside this feature, noted only because any new expiry job must be guarded).

### 4.2 Immediate problems (fail with 2+ instances, or already today)
| # | Concern | Why it fails across instances | Finding |
|---|---|---|---|
| S1 | Non-atomic transitions | Two instances serve accept+cancel/block simultaneously; last write wins; no locks | CON-08 |
| S2 | Pair uniqueness only via Postgres DDL hook | Race protection absent if index missing; both instances insert A→B and B→A | CON-05 |
| S3 | Side effects before commit | Redis `INCR` and WS emit survive a rolled-back transaction on any instance | CON-09, CON-17 |
| S4 | JSON read-modify-write volunteers | Concurrent writers on different instances overwrite each other | CON-16 |
| S5 | Rate-limit storage `memory://` fallback | Counters per instance → limit × N; Redis outage → fail-open | CON-18 |
| S6 | Stale caches without invalidation | TTL-only; every instance serves the same stale value (consistent but wrong) | CON-20 |

### 4.3 Future scaling improvements (not broken now)
- Pagination/keyset ordering and SQL-side mutual counts (CON-15). Memory per request grows with graph size.
- Index review (unverified; needs `EXPLAIN` on production-sized data): the polled `unseen` counts filter `(receiver_id, status, is_seen)` and `(requester_id, status, is_seen)`; single-column indexes may suffice now, composite indexes become worthwhile at scale. `uq_connections_pair`/`idx_connections_pair_status` serve pair lookups only if they exist.
- Help-request fan-out: push/in-app notifications are sent inline in the request. If audiences grow beyond 10, move to the existing `job_queue` (RQ) with idempotency keys.
- AI overview streams hold a worker thread per viewer (threading mode); cap concurrency or cache per pair.

### 4.4 Recommended changes (simplest reliable)
1. Conditional `UPDATE ... WHERE status=...` (compare-and-set) for every transition; no distributed lock needed.
2. Alembic migration for the unique pair index (+ de-dup).
3. Commit first, then notify/emit/push (or an `after_commit` hook).
4. Child table with `UNIQUE(help_request_id, user_id)` for volunteers.
5. Production startup check: refuse/ERROR on `memory://` limiter storage with >1 worker.
6. If an expiry job is added, run it through `job_queue` with `distributed_lock` so replicas don't duplicate it.
No new infrastructure is required.

---

## 5. Data Usage and Cleanup Report

Classifications: **CU** confirmed unused · **PU** potentially unused (needs verification) · **RI** required indirectly · **RD** redundant/consolidate.

| Resource | Current usage | Evidence | Class | Recommendation |
|---|---|---|---|---|
| `connections.connection_type` | Default `"connection"`; only echoed in `/details` | `models.py:1097`; sole reference `health.py:102`; never written elsewhere (grep) | PU | Keep until Q14 (mobile/API consumers) answered; then drop column + field via migration (low risk, data is constant) |
| `connections.requester_notes` | Request message **and** private note | CON-19 | RD (dual-purpose) | Split into `request_message` + private notes; migration + backfill |
| `connections.receiver_notes` | Receiver's private note | `health.py` | RI | Keep |
| `connections.responded_at` | Accept/reject/block time, "connected_at", "last interaction", NOT NULL | many | RI | Keep; fix null handling (CON-03) |
| `connections.is_seen` | Shared badge flag | CON-07 | RD | Replace with `requester_seen`/`receiver_seen` |
| `connections.blocked_by_id` | Block owner | `connection_service`, blocking routes | RI | Keep; clear on any transition out of `blocked` |
| `unique_connection` (directional UC) | Directional uniqueness | `models.py:1116` | RI | Keep: only guaranteed pair protection until CON-05 fixed, and used by SQLite tests |
| `idx_connections_pair_status` | Postgres lookup index | DDL hook; may not exist | RI | Ship via migration |
| Single-column indexes on requester/receiver/status | Used by OR/unseen queries | `models.py` | RI | Keep; review after `EXPLAIN` |
| `ConnectionStatus.REJECTED` | Written/read | crud.py | RI | Keep |
| `help_requests.broadcast_sent` | Written once, never read | `crud.py:545`; no other reference | PU | Drop or use for retry logic |
| `help_requests.volunteers` (JSON) | Read/written | CON-16 | RD | Move to child table; JSON → table backfill migration |
| `help_requests.status` | `active/expired`, never swept | CON-17 | RI | Keep + expiry job |
| `/details` → `study_history` | Hard-coded empty object | `health.py` "study-sessions removed"; FE ignores it | CU | Remove field (no consumer in repo) |
| `/details` local `thirty_days_ago` | Unused variable | `health.py` | CU | Delete |
| FE `connectedCurrentPage`, `hasMoreConnected` and methods | Never read outside state file | grep | CU | Remove, or use when pagination added (CON-15) |
| `GET /connections/blocked` | Duplicate of `/blocked/list` | FE uses only `/list` | RD/PU | Remove after Q14 |
| `GET /unseen/all`, `POST /mark-seen/<id>`, `POST /mark-all-seen`, `GET /status/<uid>` | No caller in `static/`/`templates/` | grep (0 files) | PU | Verify external consumers (Q14) before removal |
| `onboard-connect*` / `suggestions-by-email` (connections) vs `auth.py /onboard/*` | Two parallel onboarding families | CON-01/24 | RD | Keep one; secure it |
| Imported-but-unused names in route modules | `Response`, `stream_with_context`, `json`, `random`, `timedelta`, `case`, `Notification`… | AST/grep | CU | Lint cleanup, no migration |

Nothing above should be removed on a single search result: each PU row lists what must be checked first. Only the column/field removals (`connection_type`, `broadcast_sent`, `volunteers` JSON, `is_seen`, `requester_notes` split) require migrations; for each, deploy code that no longer reads the old column first, then drop in a later release.

---

## 6. End-to-End Data Consistency Report

| Path | Mismatch / gap | Example | Correction |
|---|---|---|---|
| Send request → UI | Backend succeeds, UI resets button and skips refresh (`ReferenceError`) | CON-12 | Fix `card2`, show errors |
| Send request (race) | UI says "already pending" while a phantom notification + counter increment exist | CON-09 | Notify after commit |
| Re-request after reject | Backend 400 generic; UI toast shows "Failed to send request" | CON-03 | Fix null; return 500 for faults (CON-22) |
| Accept → requester badge | Row accepted but `unseen/sent` = 0 | CON-07 | Per-party seen flags |
| Blocked list | Backend `{blocked_users,total}` vs FE expects array | CON-13 | FE unwrap |
| Request message | Stored raw, shown raw, editable after send | CON-04, CON-19 | Sanitize, immutable `request_message` |
| Block → other surfaces | Blocked party still gets details/notes (200), AI overview, help-find results | CON-11, CON-21 | Status gates |
| Unblock | `/connections/unblock` deletes; `/messages/unblock` accepts | CON-02 | One semantics |
| Available-now | `schedule_available` always false; "online" differs from other lists | CON-14 | Shared helper, casing |
| Mutual count / health | Stale after accept/remove (cache) | CON-20 | Invalidate |
| List totals | `total` = truncated count | CON-15 | Real count + paging |
| Help request | Notification link has no page; status "active" after expiry | CON-17 | Route + expiry job |
| `can_message` | Requires `accepted`, but `accepted` can be forced via CON-01/02 | CON-01/02 | Fix upstream, not here |
| Status values | Column is Enum(str) compared to raw strings; works because `ConnectionStatus(str, Enum)`; unvalidated strings written in several places (`"accepted"` literals) | `crud.py`, `connection_service.py` | Use `ConnectionStatus.X` constants (maintainability) |

---

## 7. Testing Gaps

Existing: 6 integration files + `test_connection_service.py` (113 pass). 2 skipped: auto-accept fixture below the 70 threshold (`test_connections_flow.py:86`, scoring changed — the test is stale) and the Postgres race test (`:106`).

| Missing / weak test | Behavior to verify | Level |
|---|---|---|
| Auth guard on onboarding endpoints (CON-01) | 401 without proof; blocked/pending rows untouched; id cap | Integration |
| Unblock parity (CON-02) | Both endpoints leave no accepted row for stranger | Integration |
| Re-request after rejection, both directions (CON-03) | Success after cooldown, correct recipient can accept, 429 during cooldown | Integration |
| Message sanitization/length (CON-04) | HTML stripped, >N chars → 400 | Unit + integration |
| Frontend escape + send/blocked-list flows (CON-04/12/13) | No raw HTML injection; both response branches; blocked fixture shape | JS unit (jsdom) / e2e |
| Postgres-only suite (CON-05, CON-08) | Reversed-pair insert → `IntegrityError`; migration on DB lacking the index; accept+cancel / accept+block / block+block concurrency | Integration on real Postgres (CI service container) |
| Volunteer authorization & concurrency (CON-06, CON-16) | Stranger/blocked 403; N parallel volunteers → N rows, no dupes | Integration |
| Seen badges (CON-07) | Per-party counts through accept/reject/re-request | Integration |
| Side-effect ordering (CON-09, CON-17) | No notification/counter/emit/push on rolled-back or failed commit | Unit with mocks |
| Block semantics (CON-10) | Idempotent re-block; blocker keeps ownership | Unit |
| Status-gated details/notes (CON-11) | 404 for non-accepted × both parties | Integration |
| Available-now (CON-14) | Frozen clock, capitalized keys, consistent online state | Unit |
| Pagination/order (CON-15) | >200 connections all reachable, stable order, correct total | Integration |
| Help-request validation/expiry (CON-17) | Length/type errors; expiry job; single active request | Integration + job test |
| Rate-limit key & prod config (CON-18) | User-keyed; warns on `memory://` | Unit |
| Cache invalidation (CON-20) | Fresh health/mutuals after state change | Unit (fakeredis) |
| Regression test per fixed finding | One named test each | Mixed |

Several existing tests assert 200/400 shapes without checking persisted state or authorization boundaries; these should be reviewed when the above are added (not audited line-by-line here).

---

## 8. Recommended Implementation Plan

Priority order: correctness/data loss → security → concurrency → scalability → consistency → performance → cleanup.

**M1 — Close the security holes (CON-01, 02, 06, 04 server-side, 11)**
- Files: `crud.py` (onboarding, volunteer), `messages.py`, `connection_service.py`, `health.py`, `auth.py` (extract guard).
- Implementation: ownership guard + `pending_onboarding` check + create-only semantics; unified unblock; connection/block check on volunteer; message sanitize/length; status gate on details/notes.
- DB: none (unblock "restore prior status" option needs one nullable column).
- Tests: rows 1,2,4,6,11 above. Dependencies: Q1, Q2. Risks: onboarding UI regression → test with `onboard.html`.

**M2 — Correctness bugs (CON-03, 07, 10, 12, 13, 19-lite)**
- Fix re-request null/direction; reset/split seen flags; idempotent block; FE `card2`, error toasts, blocked-list unwrap, `escapeHtml` in templates (CON-04 client).
- DB: `requester_seen`/`receiver_seen` migration (backfill true). Risks: badge counts change; two-step deploy.

**M3 — Concurrency & integrity (CON-05, 08, 09)**
- Alembic migration: de-dup + `uq_connections_pair` + pair index (`CONCURRENTLY`); CAS updates; notify-after-commit. Needs Q8 first and approval of de-dup precedence. Tests on Postgres.

**M4 — Help requests (CON-16, 17)**
- `help_request_volunteers` table + backfill; validation; single-active guard; expiry job via `job_queue`+`distributed_lock`; fix link/route; commit-before-push.

**M5 — Scalability/perf (CON-15, 18, 20)**
- Deterministic ordering + pagination, SQL mutual counts, batch broadcast, user-keyed limits, prod startup check, cache invalidation. Index changes only after `EXPLAIN`.

**M6 — Product consistency (CON-14, 19, 21, 22, 23, 25)**
- Available-now fixes, `request_message` split, block check on overview, status codes/logging, health recency, activity-feed defaults (per Q10/Q11/Q13).

**M7 — Cleanup (CON-24, §5)**
- Remove CU items; remove PU items only after Q14; lint unused imports; use `ConnectionStatus` constants.

---

## 9. Questions and Decisions Required

| # | Question | Why it matters | Options |
|---|---|---|---|
| Q1 | Should onboarding "connect" auto-accept without the target's consent? | Defines CON-01 fix and whether consent can ever be skipped | (a) keep auto-accept but authenticated + onboarding-only + never override existing rows; (b) send *requests* like `auth.py/onboard/request-all`; (c) auto-accept only when target opted in |
| Q2 | What should unblock do? | CON-02 | (a) always delete row, re-request needed; (b) restore prior status (adds column); (c) restore only if previously accepted |
| Q3 | Re-request after rejection: who may re-request, cooldown length, direction? | CON-03 | Swap row direction vs. new row; keep 24 h or change |
| Q4 | Do you need mutual blocks / a dedicated blocks table? | CON-10 | Keep one-row model (document limit) vs `user_blocks` table + migration |
| Q5 | What should a blocked user still be able to see (profile, posts, threads, presence)? | CON-11/21; docstring promises profile hiding | Define a block matrix; I only audited connections surfaces |
| Q6 | Help-request audience & fallback | CON-06/17/25 | Persist notified targets; only connections; skip zero-relevance; fallback to top-N or none |
| Q7 | Seen-flag design | CON-07 | Two columns (recommended) vs reset-on-transition |
| Q8 | Does `uq_connections_pair` exist in production/staging? May I get read-only access or can you run `SELECT indexname FROM pg_indexes WHERE tablename='connections';` and a duplicate-pair count query? | CON-05 severity and migration shape | Provide output; approve de-dup precedence (blocked > accepted > pending > rejected?) |
| Q9 | Expected max connections per user / pagination contract | CON-15 | Keep default limit for back-compat + add `?limit&cursor` |
| Q10 | Activity-feed privacy default | CON-25 | Default off; per-event; keep |
| Q11 | Split request message from private notes? | CON-19 | Yes (migration + backfill) vs keep and block edits while pending |
| Q12 | Rate-limit keying with shared campus IPs; will prod run >1 worker with Redis? | CON-18 | User-keyed; startup guard |
| Q13 | Timezone model for availability | CON-14 | Per-user tz field vs documented UTC |
| Q14 | Any mobile/external/API consumers of the "unused" endpoints/fields? (`openapi.yml`/`API_REFERENCE.md` not cross-checked exhaustively) | §5 removals | List consumers or accept removal after deprecation |

No product or architecture decision above was made on your behalf.

---

## 10. Final Recommendation

**Must fix before production:** CON-01, CON-02, CON-03, CON-04, CON-05 (after Q8), CON-06; plus CON-11 (blocked party access) and CON-08 (lost blocks / deleted friendships) given they undermine the block guarantee.

**Fix soon:** CON-07, 09, 10, 12, 13, 14, 15, 16, 17, 18, 19.

**Can be postponed:** CON-20–23, 25 (after product decisions), index tuning, health-score redesign.

**Should be removed (after verification):** `study_history`, `thirty_days_ago`, unused FE pagination state, duplicate `/connections/blocked`, `broadcast_sent`, `connection_type`, unused imports; the unguarded onboarding family once consolidated.

**Needs your approval/clarification:** Q1, Q2, Q8 first (they unblock M1/M3); then Q3–Q7, Q9–Q14.

**Recommended order:** M1 → M2 → M3 → M4 → M5 → M6 → M7.

**Verification limits of this audit:** reproductions ran on SQLite in a `/tmp` copy; Postgres behavior, true concurrency, production DB state and browser behavior were not exercised. All such items are labelled *Unverified risk* or *Code-verified*.

*End of report. No fixes were implemented.*

---

## 11. Decisions (recommended options selected on the owner's instruction)

| Q | Decision | Rationale |
|---|---|---|
| Q1 | (a) Keep onboarding auto-connect only if authenticated (reuse `_is_request_authorized_for_email`/JWT), limited to `pending_onboarding`, create-only (never modify an existing row), id cap 25 | Preserves the onboarding UX without bypassing consent/blocks |
| Q2 | (a) Unblock always deletes the row (both endpoints); user must re-request | No migration; never manufactures consent |
| Q3 | Keep 24 h cooldown; on re-request by the original receiver, swap row direction and reset seen flags; fix `responded_at` | Fixes the latent direction bug |
| Q4 | Keep one-row model; idempotent block, `blocked_by_id` never overwritten; document no mutual blocks | Avoids a new table now |
| Q5 | Out of scope here; connections surfaces: details/notes/overview return 404/403 when not `accepted` or when blocked | Block matrix for other features needs a separate audit |
| Q6 | Only connections, score > 0, persist nothing now; volunteer requires accepted connection and no block | Smallest safe fix |
| Q7 | Two columns `requester_seen` / `receiver_seen` (backfill true) | Correct fix, not a stop-gap |
| Q8 | **Pending your input** (needs production DB check) | |
| Q9 | Keep default limit for back-compat, add `?limit&cursor`, deterministic order | Backward compatible |
| Q10 | Default activity-feed sharing of connection events OFF | Privacy by default |
| Q11 | Yes: add immutable `request_message`, keep notes private; dual-read for one release | Fixes edit-after-send |
| Q12 | User-keyed limits for authenticated routes; production startup error on `memory://` with >1 worker | |
| Q13 | Document UTC now; per-user timezone later | Avoids a migration now |
| Q14 | **Pending your input**; do not remove PU items until answered | |

### Decision updates (owner answers)

- **De-dup precedence approved:** blocked > accepted > pending > rejected (applies to the CON-05 migration).
- **Q8 still open:** production `pg_indexes` / duplicate-pair query output not yet provided. CON-05 stays "Unverified risk"; the migration must be idempotent (`CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS`) and run the de-dup step first.
- **Q14 resolved:** no external/mobile consumers. All "Potentially unused" items in §5 are re-classified **Confirmed unused** and may be removed in M7 (`/connections/blocked`, `/unseen/all`, `/mark-seen/<id>`, `/mark-all-seen`, `/status/<uid>`, `connection_type`, `study_history`, `broadcast_sent`). Column drops still follow the two-release pattern.
- **Q12 resolved:** production runs multiple workers with Redis. CON-18's per-instance counter risk is therefore not a current issue if `RATE_LIMIT_STORAGE_URI`/`REDIS_URL` is set (verify the env var is actually set); the IP-keyed authenticated reads and fail-open behavior remain. Severity of CON-18 lowered to **Low**. Because multiple workers are live, CON-08, CON-09 and CON-16 are **current** problems, not future risks.
