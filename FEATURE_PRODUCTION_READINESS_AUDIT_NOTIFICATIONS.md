# Feature Production-Readiness Audit — Notifications

> **File naming note.** The repository root already contains a committed `FEATURE_PRODUCTION_READINESS_AUDIT.md` (an earlier audit of *Authentication & Onboarding*). To avoid overwriting it, this report is saved as `FEATURE_PRODUCTION_READINESS_AUDIT_NOTIFICATIONS.md`. Rename it if you prefer the original name.

**Repository:** `Devpaul-01/StudyHub` — audited at commit `d41565a` (2026-11-22, "Merge branch 'main' into fix/auth-onboarding-audit"), cloned 2026-11-23
**Scope:** The notification feature only — in-app notifications (DB + REST), real-time delivery (Socket.IO), the Redis unread counter, notification settings, filter UI, and the FCM push channel. Other systems were read only where notifications depend on them.
**Nothing in the repository was modified** except adding this report. All experiments ran in a throw-away copy outside the repo (see §0.2).

---

## 0. How to read this report

### 0.1 Evidence tags

| Tag | Meaning |
|---|---|
| **[R]** | **Reproduced** — I ran code (in the scratch copy) and observed the behaviour. |
| **[C]** | **Confirmed by code reading** — file/line cited, but not executed (typically because it needs a browser, real concurrency, Postgres, or external services). |
| **[U]** | **Unverified risk** — plausible from the code, not provable with what I had. The missing piece is named. |

### 0.2 What I actually ran, and what I could not

**Ran:**
- Existing notification-related tests (5 files): **62 passed**, before any experiments.
- 19 scratch characterization tests (SQLite + fakeredis, real `create_app()`, real CSRF hook, real rate limiter) in a **copy** of the repo under `/home/claude/scratch/` — *not* added to the repository. They assert the **current (buggy) behaviour** so each finding is reproducible. Referred to below as `V1`…`V16`.
- A Node + jsdom harness that renders the **real** `createNotificationCard()` with an API payload.
- A runtime check that `PushNotificationService.initialize` is never invoked during `create_app()`.

**Could not verify (limits of this audit):**
- No real browser (so XSS execution was verified at DOM-construction level, not by seeing an `alert`).
- No PostgreSQL (tests use SQLite; Postgres-specific behaviour is marked **[U]**).
- No multi-process / multi-instance run; no real Redis or Firebase.
- Production configuration, actual DB indexes, and real traffic volumes are unknown.

The scratch tests are not included in the repo (you asked for no test changes). I can share them if you want to use them as a starting point for the regression tests in §7.

---

## 1. Executive Summary

**Feature audited:** Notifications (in-app + real-time + push + settings).

### Overall assessment: **Critical issues found — not production-ready in its current state.**

The core data path is reasonably built: one funnel (`notification_service.notify`), ownership checks on every route, an atomic bulk "mark all read", and a Redis-backed WebSocket bridge that is multi-instance capable. But the surrounding feature has a **cross-user stored XSS**, a **private-content leak**, and several features that look finished in the UI but do nothing.

| Severity | Count |
|---|---|
| Critical | **1** |
| High | **3** |
| Medium | **12** |
| Low | **8** |
| **Total** | **24** |

### Most important findings

1. **NOTIF-01 (Critical) — Stored XSS.** `createNotificationCard()` inserts `title`, `body` and `link` into `innerHTML` unescaped. Those fields contain other users' display names and post/thread titles, which the real validators accept as raw HTML. Any authenticated user can make a victim's notification panel build live `<img onerror=…>` elements simply by commenting on the victim's post. No CSP is set. **[R]**
2. **NOTIF-02 (High) — Private thread content leaks to non-members.** `@username` in a thread message notifies *any* user on the platform and puts the first 80 characters of the private message in the notification body. No membership or block check. **[R]**
3. **NOTIF-03 (High) — The unread badge does not exist on the main page.** The live template (`feed2.html`) has no `#notification-badge` element, so the badge code and the live-push counter are no-ops there. **[C]**
4. **NOTIF-04 (High) — Push notifications are inert.** `PushNotificationService.initialize()` was deleted from `app.py` in commit `8536738`; Firebase config is still placeholder text; yet the UI shows a working-looking "Push Notifications" toggle. **[R]**
5. **NOTIF-05 (Medium) — "Disable notifications" only hides them.** Rows are still created, counted, and pushed live; the API simply returns an empty list and `unread_count: 0`. **[R]**

### Major reliability / scalability concerns

- The **Redis unread counter is write-only** — nothing in production reads it — and its design is unsound (wrong value on a missing key, TTL refreshed on every write, incremented before the transaction commits). It is currently harmless only *because* nobody reads it. (NOTIF-07, NOTIF-08)
- The badge is populated by downloading **up to 500 full notifications (~136 KB)** on every shell page load. (NOTIF-07)
- The "per-user" rate limit still has a **shared per-IP bucket**: one user exhausting 60 req/min locked out a second user on the same IP. (NOTIF-09)
- **No retention, no pagination**: the table grows forever and the API silently truncates at 500. (NOTIF-14)

### Is it safe to consider production-ready?

**No.** NOTIF-01 and NOTIF-02 should be fixed before real users are on the system. NOTIF-03 and NOTIF-04 mean two headline capabilities (unread badge, push) do not work.

### What is sound (evidence-backed)

See §3.2 — ownership checks, CSRF, mark-all-read correctness, WebSocket cross-instance design, title-length safety, and others were reviewed and found correct.

---

## 2. Feature Architecture and Execution Flow

### 2.1 Components

**Frontend** (`static/js/notification/`, loaded only by `templates/post/feed2.html`, the live SPA page)
- `notification.init.js` — wiring, pull-to-refresh, **WebSocket `new_notification` handler** (≈ lines 285–335).
- `notification.events.js` — `loadNotifications()`, filter chips, mark-all/clear, settings toggles, delete.
- `notification.templates.js` — `createNotificationCard()` (**the XSS sink**, lines 6, 19, 20).
- `notification.state.js` — `activeType`, `isLoading` (+ unused pagination fields).
- `notification.api.js` — REST wrapper; `notification.delegation.js` — click handling; `notification.utils.js` — badge update (`#notification-badge`, line 23).
- `push/push.js`, `push/push.config.js`, `static/firebase-messaging-sw.js` — FCM web push.
- Other consumers of the same API: `static/js/shell/app-shell.js:61` and `templates/base.html:291` (badge on non-SPA pages).

**Backend**
- `services/notification_service.py` — `notify()` funnel (lines 100–155) + 10 named templates (`notify_badge_earned`, `notify_welcome`, …). `_broadcast()` (60–92) calls `message_ws_manager.emit_to_user`.
- `services/counter_cache_service.py` — Redis unread counter (`get/increment/decrement_unread_notification_count`).
- `routes/student/notifications.py` — 7 endpoints (below).
- `services/thread_message_service.py:~500–515` — **creates `Notification` rows directly, bypassing the funnel.**
- `routes/student/threads/messaging.py:47, 691–694` — a second mention implementation used on message edit.
- `services/push_notifications.py` + `routes/student/profile.py:508–560` (FCM token save/clear) + `routes/student/connections/crud.py:243–262, 352` (the only push call sites).
- `services/websocket_messages.py` — Socket.IO manager (Redis `message_queue`), `emit_to_user`.
- `scheduler.py` / `services/jobs/maintenance_jobs.py:~181` — only deletes notifications when purging unverified accounts.

**Endpoints** (all under `/student`, `routes/student/notifications.py`):

| Method & path | Lines | Notes |
|---|---|---|
| `GET /profile/notifications/all` | 29–130 | up to 500 rows, `COUNT(*)` for `unread_count`; early-returns empty if disabled (line 58) |
| `POST /profile/notifications/mark-all-read` | 167–243 | bulk `UPDATE`, rowcount-based decrement |
| `POST /profile/notifications/<id>/mark-read` | 246–280 | read-then-write |
| `DELETE /profile/notifications/<id>` | 284–318 | read-then-delete |
| `DELETE /profile/notifications/delete-all` | 322–385 | `count()` then `delete()` |
| `GET /profile/notifications/settings` | 387–420 | **no frontend caller**; limiter uses `ip_key` |
| `POST /profile/notifications/settings` | 422–480 | **flips** a boolean (not "set") |

**Data**
- Table `notifications` (`models.py:1567–1586`): `id, user_id(FK CASCADE, idx), title(200), body(Text), link(200), notification_type(50, idx, nullable), related_type(20), related_id, is_read(idx), created_at(idx), read_at`. Single-column indexes only. No relationship on `User`.
- `User.notification_settings` (JSON): `enable_notification`, `enable_notification_sound` (+ three dead keys, §5).
- `User.fcm_token` (single string column).
- Redis key `sh:1:notif:unread:{user_id}` (counter, 3600 s TTL refreshed on every write).
- Table is created by `db.create_all()` (`app.py:637`); **no migration creates or alters it.**

### 2.2 End-to-end flow

```mermaid
flowchart TD
  A[Producer: ~35 call sites<br/>comments, threads, connections,<br/>homework, badges...] -->|notify()| B[notification_service.notify]
  A2[thread_message_service<br/>mention] -.->|db.session.add directly<br/>NO push, NO counter| DB
  B --> C[db.session.add + flush]
  C --> D[Redis INCR unread counter<br/>+ EXPIRE 3600]
  C --> E[_broadcast -> emit_to_user<br/>via Redis message_queue]
  C -.-> DB[(notifications table)]
  D -. no reader anywhere .-> X[[write-only]]
  E --> WS[Socket.IO new_notification<br/>user room]
  F[Caller commits LATER] --> DB
  WS --> UI1[notification.init.js handler<br/>prepend card + badge++]
  UI1 -->|innerHTML unescaped| XSS((NOTIF-01))
  P[Page load] -->|GET /profile/notifications/all<br/>up to 500 rows| R[unread_count = COUNT *]
  R --> UI2[shell/base badge]
  R --> UI3[SPA: no badge element NOTIF-03]
  M[Mark read / delete] --> DB
  M --> D2[Redis DECR]
  H[help-request only] -->|FCM multicast, sync,<br/>before commit| FCM[Firebase<br/>never initialised NOTIF-04]
```

Key ordering fact **[C]** (`notification_service.py:145–150`): `flush()` → Redis `INCR` → WebSocket emit — **all before the caller's `commit()`**. If the caller later rolls back, the counter stays incremented and the client has already been told about a notification that does not exist (**[R]** V3).

---

## 3. Findings and Issues

### 3.1 Summary table

| ID | Title | Sev | Category | Tag |
|---|---|---|---|---|
| NOTIF-01 | Stored XSS in notification cards | **Critical** | Security | R |
| NOTIF-02 | @mention leaks private thread text to any user | High | Security | R |
| NOTIF-03 | Unread badge absent on the live SPA page | High | Bug/Product | C |
| NOTIF-04 | Push notifications never initialised; UI toggle is live | High | Product/Reliability | R |
| NOTIF-05 | "Disabled" is only a read-side filter | Medium | Bug/Product | R |
| NOTIF-06 | Editing a thread message re-notifies every mention; two divergent implementations | Medium | Bug | R |
| NOTIF-07 | Badge count requires downloading 500 rows; Redis counter unused | Medium | Performance/Scalability | R |
| NOTIF-08 | Unread counter design is unsound (latent) | Medium | Data Integrity | R/C |
| NOTIF-09 | Per-user rate limit still has a shared per-IP bucket | Medium | Reliability | R |
| NOTIF-10 | Notifications are dead-ends; the links that exist 404 | Medium | Product | R |
| NOTIF-11 | Filter chips don't match produced types | Medium | Product | R |
| NOTIF-12 | Blocked users can still generate notifications | Medium | Security/Abuse | R |
| NOTIF-13 | No de-duplication (like/unlike spam) | Low | Abuse | R |
| NOTIF-14 | No retention, no pagination, silent 500-row cap | Medium | Scalability/Data | R |
| NOTIF-15 | Loading guard silently drops filter/refresh actions | Medium | Bug | C |
| NOTIF-16 | Real-time delivery gaps (pre-commit push, no dedupe, no resync) | Medium | Reliability | R/C |
| NOTIF-17 | FCM token lifecycle and dispatch design (latent) | Medium | Security/Reliability | C |
| NOTIF-18 | Deleting an unread card doesn't update the badge | Low | Bug | C |
| NOTIF-19 | Settings toggle is non-idempotent; lost-update risk | Low | Reliability | R |
| NOTIF-20 | Settings surface advertises unimplemented options | Low | Product | C |
| NOTIF-21 | All failures return HTTP 400 | Low | Maintainability | R |
| NOTIF-22 | Index/migration posture for the hot query | Low | Performance | U |
| NOTIF-23 | No CSP; WebSocket CORS is `*` | Low | Security | U |
| NOTIF-24 | "Follow post" never produces notifications | Low | Product | C |

---

### NOTIF-01 — Stored XSS in notification cards
- **Severity:** Critical  **Category:** Security  **Type:** Confirmed bug
- **Location:** `static/js/notification/notification.templates.js` lines 6, 19, 20 (`data-link="${notification.link}"`, `${notification.title}`, `${notification.body}`); sinks: `notification.events.js:95` (`tempDiv.innerHTML = createNotificationCard(...)`), `notification.init.js:332` (live push). Sources: `routes/student/posts/comments.py` (title `f"{current_user.name} commented on your post"`, body `f'"{post.title}"'`), thread/connection/homework producers (names, titles).
- **Evidence:**
  - `createNotificationCard` interpolates server strings into a template literal with no escaping, then assigns to `innerHTML`.
  - Validators only check length: `services/onboarding_service.py:317–325` (name ≤ 100), `routes/student/posts/crud.py:1084` (title ≤ 200). No sanitisation found in `auth.py`, `profile.py`, `posts/crud.py`, `homework_system.py`.
  - **[R] V7b:** the real `validate_onboarding_payload` returned `cleaned["name"] == '<img src=x onerror=alert(1)>'` with no errors; `POST /student/posts/create` accepted a title of `"><svg onload=alert(2)> hello` (201).
  - **[R] V7:** `GET /profile/notifications/all` returned `"title": "<img src=x onerror=alert(1)> commented on your post"` verbatim.
  - **[R] jsdom:** feeding that exact payload into the real `createNotificationCard()` and assigning to `innerHTML` produced live `<img src="x" onerror="alert(1)">` and `<svg onload="alert(2)">` elements. A `link` of `x" onmouseover="alert(3)` produced an extra `onmouseover` attribute (attribute breakout).
  - No `Content-Security-Policy` header is set in the app (`app.py:351` sets only `X-Content-Type-Options`). A proxy/CDN could add one — **[U]**.
- **Problem:** Attacker-controlled strings are rendered as HTML in the victim's session.
- **Impact:** Any authenticated user can run script as any other user: attacker sets their display name to a payload, then comments on, likes, or mentions the victim. When the victim opens notifications (or while the panel is open, via live push) the script runs with the victim's cookies. The CSRF cookie is JS-readable (double-submit design), so the script can perform any write the victim can. A self-propagating payload is feasible.
- **Reproduction:** (1) Set name to `<img src=x onerror=…>` via onboarding. (2) Comment on victim's post. (3) Victim opens the Notifications section.
- **Not observed:** actual script execution in a real browser (jsdom does not run handlers). `innerHTML`-inserted `<img onerror>` is a well-known execution path, so I treat the finding as confirmed.
- **Recommended solution:** (a) Build cards with DOM APIs / `textContent`, or run every interpolated value through a single `escapeHtml()`; (b) validate `link` as a same-origin relative path (`/…`, reject `javascript:` / `//host`) before storing *and* before navigating (`window.location.href = link`); (c) add a CSP that disallows inline handlers (staged — see Q12); (d) escape on output is the real fix; storage-time sanitisation is optional defence in depth.
- **Implementation considerations:** Check other renderers of `user.name`/titles (outside this audit's scope) — the same sources feed them. Existing stored values are harmless once output is escaped; no data migration required. A CSP may break inline scripts in templates; roll out report-only first.
- **Verification:** Unit test for `createNotificationCard` with hostile fields (no element with `on*` attributes, text preserved); end-to-end test with Playwright asserting no dialog fires; test that `link` values with `javascript:`/`//evil` are rejected.

---

### NOTIF-02 — @mention in a thread message leaks private text to any user
- **Severity:** High  **Category:** Security (privacy)  **Type:** Confirmed bug
- **Location:** `services/thread_message_service.py` `_parse_mentions` (line 190) and notification creation (≈ lines 500–515: `body = f"{text_content[:80]}..."`, `notification_type="thread_mention"`); contrast `routes/student/threads/messaging.py:47–110` (`detect_mentions_in_thread`), which *does* check membership.
- **Evidence:** `_parse_mentions` looks users up by username globally. **[R] V6:** in a thread with one member, a message `"PRIVATE exam answers: 1=B 2=D @outsider_o"` produced, for the non-member `outsider_o`, a notification titled "You were mentioned in a thread" with body `PRIVATE exam answers: 1=B 2=D @outsider_o...`.
- **Problem:** No thread-membership, block, or account-status check; message text copied into another user's notification.
- **Impact:** Anyone in any thread (including private study groups) can push up to 80 characters of private content to any account, and can harass users who are not even in the thread.
- **Recommended solution:** Restrict mention targets to current thread members (reuse the check in `detect_mentions_in_thread`); route creation through `notify()` (gets live push + one code path); decide whether body should carry message text at all (Q7). Add a `link` to the thread (needs a valid thread URL — see NOTIF-10).
- **Implementation considerations:** Behaviour change for existing "@someone-outside" usage (they will stop being notified). Decide whether non-member mentions are silently ignored or rendered as plain text.
- **Verification:** Integration test: mention a non-member → 0 notifications; mention a member → 1 notification via `notify()` with WS emit; blocked member → 0.

---

### NOTIF-03 — The unread badge does not exist on the live page
- **Severity:** High  **Category:** Bug / Product  **Type:** Confirmed bug
- **Location:** `templates/post/feed2.html` (served by `routes/student/profile.py:764, 1574`); `notification.utils.js:23` (`getElementById("notification-badge")`); `notification.delegation.js:38` (`.notification-badge` inside the nav item); `notification.init.js:285–292`.
- **Evidence:** Searching `feed2.html` finds no `notification-badge` element and the Notifications nav item (≈ line 547) has no badge child. `#notification-badge` exists only in `templates/post/feed.html`, which no route renders. `new_notification` has exactly one listener in the codebase (`notification.init.js`), loaded only by `feed2.html`.
- **Problem:** `updateBadgeCount()` finds no element and silently does nothing. Live pushes therefore have no visible effect unless the Notifications section is already open. On non-SPA pages (`base.html` → `#notif-count`, shell → `#shell-notif-count`) the count is loaded once at page load and never updated live (no listener there).
- **Impact:** On the main page users never see that they have unread notifications.
- **Reproduction:** Open the SPA page with unread rows; no badge appears. **[C]** — not run in a browser.
- **Recommended solution:** Add one badge element to the live nav item and make `updateBadgeCount` the single updater (select by one shared class/data attribute used by the SPA, `base.html` and the shell). Fetch the initial count via the lightweight endpoint in NOTIF-07.
- **Implementation considerations:** Three templates currently use three different ids (`notification-badge`, `notif-count`, `shell-notif-count`) — pick one convention.
- **Verification:** E2E: seed 3 unread → badge shows 3; receive a live push → 4; mark one read → 3.

---

### NOTIF-04 — Push notifications are inert, but the UI offers them
- **Severity:** High  **Category:** Product / Reliability  **Type:** Confirmed bug
- **Location:** `app.py` (~lines 115–125, comment only); `services/push_notifications.py:22–60`; `static/js/push/push.config.js`, `static/firebase-messaging-sw.js` (placeholders); `templates/post/feed2.html:885` (`toggle-push-notification`).
- **Evidence:** **[R]** `PushNotificationService.initialize` was called **0 times** during `create_app()`; the only other reference is a comment in `profile.py:505`. Git history: commit `8536738` ("Update app.py") deleted the two lines that imported and called it, leaving the explanatory comment. `send_notification()` returns `False` immediately when `_initialized` is false. `FIREBASE_CONFIG`/`VAPID_KEY` and the service-worker config are literal `'YOUR_…'` placeholders. `notify()` never calls FCM at all; only the two help-request flows in `connections/crud.py` do, and the 8 module-level helpers (`notify_streak_updated`, …) have **0** non-test references.
- **Problem:** The user can toggle "Push Notifications (this device)", grant browser permission and have a token stored, and will never receive a push.
- **Impact:** Advertised feature does not work; silent failure.
- **Recommended solution:** Decision needed (Q4): either **finish it** (initialise at startup with env-driven credentials, move Firebase config to environment/templating, dispatch from `notify()` via the existing RQ worker *after commit*, honour settings, fix token lifecycle — NOTIF-17) or **hide the toggle and remove the dead paths**.
- **Verification:** If kept: test that app startup initialises when credentials exist and fails open when they don't; integration test that a committed notification enqueues one push job. If removed: test that no push UI is rendered.

---

### NOTIF-05 — "Disable notifications" is only a read-side filter
- **Severity:** Medium  **Category:** Bug / Product
- **Location:** `routes/student/notifications.py:52–65` (early return when disabled); `notification_service.notify()` (no settings check); `notification.init.js:285–335` (handler ignores the setting).
- **Evidence:** **[R] V4:** after disabling, a `notify()` call still created the row, incremented Redis (`"1"`), and invoked `emit_to_user`; `GET /all` returned `notifications: []`, `unread_count: 0`.
- **Problem:** The setting hides notifications from the list and badge but does not stop creation, counting, WebSocket pushes (the live handler still prepends a card and bumps the badge) or FCM. Re-enabling reveals the entire backlog. Mark-all-read / delete-all are not gated either.
- **Impact:** Confusing UX; storage and push work for users who opted out; "0 unread" while many unread rows exist.
- **Recommended solution:** Product decision (Q1). Simplest: check the recipient's setting once inside `notify()` and skip creation/push; keep read-side filtering only as a transition measure.
- **Implementation considerations:** `notify()` would need the recipient's settings (one extra read per notification, or pass a pre-loaded `User`). Decide what happens to rows created while disabled.
- **Verification:** With setting off: `notify()` creates no row, no counter change, no emit; with it on: all three occur.

---

### NOTIF-06 — Editing a thread message re-notifies every mentioned user
- **Severity:** Medium  **Category:** Bug
- **Location:** `routes/student/threads/messaging.py:618` (PATCH), `691–694` (`Mention.query.filter_by(...).delete()` then `detect_mentions_in_thread(...)`); `thread_message_service.py:~500–515` (send path).
- **Evidence:** **[R] V5:** one message mentioning `@bobby_b` followed by 3 edits produced **4** notifications for him: `[('thread_mention','hey @bobby_b look...'), ('mention',''), ('mention',''), ('mention','')]`.
- **Problem:** The edit path deletes all `Mention` rows (its own de-dup record) and re-creates notifications. The two paths also disagree on type (`thread_mention` vs `mention`), body (text vs empty), membership checking (none vs yes) and live push (none vs yes). The code's own docstring acknowledges the re-notify risk.
- **Impact:** Notification spam for recipients; inconsistent behaviour for the same user action.
- **Recommended solution:** One mention implementation (a single service function used by send, edit and WebSocket paths). On edit, diff old vs. new mention sets and notify only **newly added** members.
- **Verification:** Send with `@b` + edit three times → exactly 1 notification; edit adding `@c` → `c` gets 1.

---

### NOTIF-07 — Badge count downloads up to 500 notifications; the counter built for it is never read
- **Severity:** Medium  **Category:** Performance / Scalability
- **Location:** `static/js/shell/app-shell.js:61`, `templates/base.html:291` (`api.get('/profile/notifications/all')`, uses only `unread_count`); `notification.api.js:68–71` (`getUnreadCount` → `/all`, with a code comment admitting `/unread-count` "is not a real route"); `routes/student/notifications.py:87` (`.limit(500)`).
- **Evidence:** **[R] V11:** with 505 notifications the endpoint returned 500 objects, **135,941 bytes**, to render one integer. A search of all production code finds **no reader** of `get_unread_notification_count`; it exists only in tests.
- **Problem:** Every shell page load serialises up to 500 rows (plus a `COUNT`) per user. The Redis counter and its INCR/DECR on every write were built for exactly this and are unused.
- **Impact:** Wasted DB, CPU and bandwidth proportional to users × page loads; also consumes the shared rate-limit bucket (NOTIF-09).
- **Recommended solution:** Add `GET /profile/notifications/unread-count` returning a single integer, and use it everywhere a badge is needed. Back it with an indexed `COUNT(*)` (see NOTIF-08 for whether to keep Redis).
- **Verification:** Test that the endpoint returns the right count, is owner-scoped, and its payload is < 200 bytes; assert shell pages call it, not `/all`.

---

### NOTIF-08 — The Redis unread counter is unsound (latent, because it is unread)
- **Severity:** Medium (latent)  **Category:** Data Integrity
- **Location:** `services/counter_cache_service.py` lines 80–86 (read), 108–110 (`incrby` + `expire`), 126–128 (`decrby` + `expire`); `notification_service.py:145–147`; `routes/student/notifications.py:260–270, 296–308`; `ARCHITECTURE.md §10`.
- **Evidence:**
  1. **Wrong value on a missing key — [R] V1/V1b.** With 5 unread rows in the DB and no Redis key, one `notify()` left the key at `1`; `get_unread_notification_count` then returned **1** (DB: 6) because "key exists" skips recompute. A `DECRBY` on a missing key returned **-2**. An existing test (`tests/unit/services/test_counter_cache_service.py:63`, `test_decrement_allows_negative_by_design`) enshrines negative values.
  2. **TTL refreshed on every write — [R] V2:** TTL forced to 5 s was reset to **3600 s** by the next `INCR`. The "self-healing within one TTL" claim in the docs is therefore false for any active user.
  3. **Incremented before commit — [R] V3:** after `notify()` then `rollback()`, the key was `"1"` while the row did not exist and `emit_to_user` had already been called with its id.
  4. **TOCTOU double-decrement — [C]:** `mark_notification_read` checks `if notification.is_read` then issues an unconditional `UPDATE … WHERE id=?`; two concurrent requests both pass the guard and both decrement. The code comment "the is_read guard guarantees this was a genuine transition" is false under concurrency. (`mark-all-read` is fine — it uses the `UPDATE` rowcount.) Same pattern in single delete.
  5. **No reconciliation:** `scheduler.py:233` reconciles other denormalised counters; nothing in `reconciliation_service.py` touches notification/unread.
  6. Decrement runs *after* commit, so a crash between them drifts the counter permanently (given #2).
- **Problem / Impact:** If anyone starts reading the counter (which NOTIF-07 invites), the badge will be wrong in several ordinary situations.
- **Recommended solution (decision — Q2):**
  - **Option A (recommended unless metrics say otherwise):** delete the Redis counter and its INCR/DECR calls; serve the badge from an indexed `COUNT(*)` (NOTIF-22). Removes a whole drift class and a Redis dependency from every notification write.
  - **Option B:** keep it as a real cache: increment only if the key exists (otherwise let the next read seed it), don't refresh TTL on writes, apply INCR/DECR in an `after_commit` hook, make mark-read/delete conditional (`UPDATE … WHERE is_read=false`, use rowcount), add reconciliation.
- **Verification:** For either option: tests for rollback, missing-key, concurrent mark-read (two threads) and delete-then-count.

---

### NOTIF-09 — Per-user rate limit still has a shared per-IP bucket
- **Severity:** Medium  **Category:** Reliability
- **Location:** `routes/student/notifications.py` (all routes `@limiter.limit(…, key_func=user_or_ip_key)` placed *outside* `@token_required`); `services/rate_limit_service.py` (`user_or_ip_key` reads `g.current_user_id`, which `token_required` sets).
- **Evidence:** **[R] V10b:** one user made 65 requests: first 429 at request 62. Limiter storage contained **both** `LIMITER/127.0.0.1/…get_notifications/60/1/minute` and `LIMITER/user:1/…`. A **second user on the same IP** was then immediately rejected with **429**.
- **Problem:** Besides the per-user bucket, an IP-keyed bucket is evaluated (likely because the decorator runs before `token_required` populates `g` — mechanism **[U]**, behaviour **[R]**). `GET /settings` explicitly uses `ip_key`.
- **Impact:** Students on a school/campus NAT share 60 `GET /all` requests/minute — and every page load calls it (NOTIF-07). Badge loads fail silently (the shell only logs). The same pattern probably affects other routes; out of scope but worth checking.
- **Recommended solution:** Authenticate before limiting (e.g., resolve the user in the key function from the token, or apply the limiter inside the authenticated layer) and give the badge endpoint its own generous limit.
- **Verification:** Test: two users, same IP, each under 60/min → no 429.

---

### NOTIF-10 — Notifications are dead-ends; the links that exist 404
- **Severity:** Medium  **Category:** Product
- **Location:** `notification.delegation.js` (click handling uses only `link`); census of 35 producer call sites; `connections/crud.py:267, 367` (`link=f'/help-request/{id}'`); `notification_service.notify_badge_earned` (`/student/badges/#badge-N`).
- **Evidence:** Of 35 `notification_service.*` call sites only the 2 help-request sites pass `link` directly (helpers add one for badges and welcome). Comments, likes, mentions, thread events, connection requests and homework events carry `related_type/related_id` but no link; the frontend never reads those fields. **[R] V9:** `GET /help-request/1` → **404**, `GET /student/badges/` → **404**; no `/help-request/…` route exists.
- **Impact:** Clicking most notifications only marks them read; the two linked types lead to error pages.
- **Recommended solution:** Define a destination per notification type (Q5), generate links server-side in one place (`notify()` or a type→URL map), validate them (NOTIF-01), and fix or remove the broken targets.
- **Verification:** Test every producer's link resolves (`200/302`, not 404) via `url_map`.

---

### NOTIF-11 — Filter chips do not match what is produced
- **Severity:** Medium  **Category:** Product
- **Location:** `routes/student/notifications.py:133–163` (`_build_type_filter`); `notification.constants.js`.
- **Evidence:** Produced types (search of all producers) include `instant_connection`, `homework_*`, `help_request`, `help_volunteer`, `welcome`, `thread_mention`, `solution_accepted`. **[R] V14:** an `instant_connection` row is visible under **All** but not under **Connections**. The **Messages** chip filters `message` and **Engagement** includes `helpful` — **no producer creates either type**. `thread_mention` appears under Threads, not Mentions (`mentions` matches only `mention`). Homework/help/welcome types are reachable only under All.
- **Impact:** Empty or incomplete chips; users can't find notifications by category.
- **Recommended solution:** Single type registry (type → chip group → link builder) used by backend filter, frontend chips and producers (Q10). Remove chips with no producers or create the missing notifications.
- **Verification:** Test that every produced type belongs to a chip (or is intentionally All-only), and every chip has ≥ 1 producer.

---

### NOTIF-12 — Blocked users can still generate notifications
- **Severity:** Medium  **Category:** Security / Abuse
- **Location:** `notification_service.notify()` (no relationship check); producers in `posts/comments.py`, `post_service.py`, `threads/*`; blocking exists only in `connections/crud.py` (`Connection.status == "blocked"`, `blocked_by_id`).
- **Evidence:** **[R] V16:** with a `blocked` connection, the blocked user's comment, comment-like and `@mention` produced `['comment','like','mention']` notifications for the blocker.
- **Impact:** Blocking does not stop harassment via notifications.
- **Recommended solution:** Enforce the block check once, inside `notify()` (skip when recipient has blocked actor) — requires passing an `actor_id`. Decide whether already-delivered notifications from a user who is later blocked should be hidden (Q6).
- **Verification:** Test: blocker receives 0 notifications from the blocked user for every producer type.

---

### NOTIF-13 — No de-duplication (like/unlike spam)
- **Severity:** Low  **Category:** Abuse
- **Location:** `routes/student/posts/comments.py:243–290`; `notify()` always inserts.
- **Evidence:** **[R] V8:** 5 like-toggles on one comment created **3** `like` notifications for the author.
- **Impact:** A user can generate repeated notifications (bounded by the rate limit, ≈ dozens per minute).
- **Recommended solution:** Collapse repeats by key `(user_id, type, related_type, related_id, actor)` within a window, or notify only on first like (Q9). Needs `actor_id` stored (it currently isn't — a schema decision).
- **Verification:** Test toggling 5× → 1 notification.

---

### NOTIF-14 — No retention, no pagination, silent 500-row cap
- **Severity:** Medium  **Category:** Scalability / Data
- **Location:** `routes/student/notifications.py:87`; `scheduler.py` (no notification job); `maintenance_jobs.py:~181`; `notification.state.js` (unused `hasMore`, `lastCursor`).
- **Evidence:** The only deletion of notifications outside user actions is the purge of *unverified accounts*. **[R] V11:** 505 unread rows → API returns 500 and `unread_count: 505`; there is no `has_more`, cursor or offset, and the docstring still says "cursor pagination".
- **Impact:** Table grows without bound; older notifications are unreachable but still counted as unread; users can never clear them except via bulk actions.
- **Recommended solution:** (a) Cursor pagination (`before_id`) with `has_more`; (b) scheduled retention job (e.g. delete read rows > N days, cap per user) using the existing `_run_locked` pattern so only one instance runs it; (c) remove or implement the dead frontend pagination state. Retention values are a product decision (Q3).
- **Verification:** Test pagination across > 500 rows with no duplicates/gaps; test retention job idempotence and single-runner locking.

---

### NOTIF-15 — Loading guard silently drops filter/refresh actions
- **Severity:** Medium  **Category:** Bug (frontend)  **Type:** **[C]** (needs a browser to observe)
- **Location:** `notification.events.js:41` (`if (notificationState.isLoading()) return`), `236–248` (`handleFilterNotifications` sets `activeType` *then* calls `loadNotifications`), `250–273` (mark-all uses `activeType`); `notification.init.js:115` (pull-to-refresh clears the container before loading).
- **Problem:** If a load is in flight (e.g. initial load), a chip tap sets `activeType` but the load is skipped. The list and the chip highlight stay on the previous filter while state says otherwise; **Mark all read** then acts on the hidden filter. Pull-to-refresh clears the container and may then be skipped, leaving it blank.
- **Impact:** Wrong notifications marked read; blank panel; confusing state.
- **Recommended solution:** Replace the boolean guard with a request sequence number (latest request wins) or abort the previous request; set `activeType` only when the response is applied; don't clear the container until data arrives.
- **Verification:** Component test with a delayed mock: tap a chip during initial load → list ends on the chosen filter; mark-all sends the filter that is displayed.

---

### NOTIF-16 — Real-time delivery gaps
- **Severity:** Medium  **Category:** Reliability
- **Location:** `notification_service._broadcast` (before commit), `notification.init.js:285–335`, `static/js/core/socket.js:6–8`.
- **Evidence:** (a) Pre-commit emit: **[R] V3**. A client that clicks the ghost card gets "Notification not found" (400). (b) The handler prepends a card for every event without checking `data-notification-id` already exists and increments the badge each time. (c) `reconnectionAttempts: 10` / `reconnectionDelay: 3000` — the client gives up after roughly 30–50 s of outage and nothing refetches on reconnect; only `thread.init.js` references reconnection. Missed events never appear until reload. (d) Thread-mention notifications (NOTIF-02) are never pushed at all. (e) Pushes ignore `enable_notification` (NOTIF-05). (f) With the default `ACCESS_TOKEN_HTTPONLY=true` the empty `auth` payload works via cookie; if that flag is ever set false, `socket.js` sends no token and the server rejects the handshake — live notifications silently stop **[C]**.
- **Impact:** Ghost notifications, duplicated cards/badge increments, lost notifications after short outages.
- **Recommended solution:** Emit after commit (SQLAlchemy `after_commit` hook or an outbox list flushed post-commit); de-dupe by id on the client; refetch unread count/list on every socket `connect`; treat the socket as a hint and the DB as the source of truth.
- **Verification:** Test that a rolled-back request emits nothing; simulated disconnect/reconnect triggers a resync; duplicate events render one card.

---

### NOTIF-17 — FCM token lifecycle and dispatch design (latent until push is enabled)
- **Severity:** Medium  **Category:** Security / Reliability  **Type:** **[C]**
- **Location:** `routes/student/profile.py:508–560`; `routes/student/auth.py` `logout` (no FCM handling); `services/push_notifications.py` (`UnregisteredError` branch); `connections/crud.py:243–262`.
- **Evidence:** One `fcm_token` column per user, no uniqueness; logout does not clear it; `UnregisteredError` is caught but the code comment "should remove from database" is not implemented; the help-request flow calls `send_multicast` **synchronously inside the request and before the DB commit**, ignores the in-app setting, and always sets the click link to `/`.
- **Problem / Impact:** On a shared device, user A's token remains on A's account after logout; if user B registers the same browser token, A's pushes appear on B's device. Stale tokens accumulate. Network I/O in the request path adds latency and can push about a request that later fails.
- **Recommended solution (if push is kept):** clear the token on logout and on `Unregistered`; make `(token)` unique (move to a `push_tokens` table for multi-device); dispatch via the RQ worker after commit; honour settings; use the real link.
- **Verification:** Logout clears token; unique-token test; push job enqueued only after commit.

---

### NOTIF-18 — Deleting an unread card doesn't update the badge
- **Severity:** Low  **Category:** Bug  **Type:** **[C]**
- **Location:** `notification.events.js:8–37` (`handleDeleteNotification` removes the element, never touches the badge).
- **Impact:** Stale count until the next full load (visible only once NOTIF-03 is fixed).
- **Recommended solution / verification:** Decrement the badge when the removed card was unread (or just refetch the count); test unread-delete → badge −1.

---

### NOTIF-19 — Settings toggle is non-idempotent; lost-update risk
- **Severity:** Low  **Category:** Reliability
- **Location:** `routes/student/notifications.py:448–472`; `notification.events.js:143–200`.
- **Evidence:** **[R] V15:** two identical `POST {setting:"enable_notification_sound"}` returned `new_value` `False` then `True`. The client sends no desired value, so a retry after a timeout, a double click or a second tab flips it back. The whole `notification_settings` JSON is rewritten, so concurrent toggles of *different* keys can overwrite each other **[U]** (needs concurrent requests to confirm). The UI also shows two toasts per toggle (`response.message` then a second success toast).
- **Recommended solution:** Accept an explicit `value` (backward-compatible: flip only when omitted); update with a single-key JSON update or row lock.
- **Verification:** Repeat the same request twice → same result; concurrent updates of two keys keep both.

---

### NOTIF-20 — Settings surface advertises unimplemented options
- **Severity:** Low  **Category:** Product
- **Evidence:** `enable_notification_sound` can be toggled but **nothing plays a sound** (no `Audio`/`.play()` tied to notifications in `static/js`). `notification_category`, `send_email_notification`, `email_frequency` are returned by `GET /settings` (and defaulted in `auth.py:72–75`) but cannot be changed (POST whitelists two keys) and have no consumer. `GET /settings` itself has no frontend caller.
- **Recommended solution:** Implement them or remove them (Q11).

---

### NOTIF-21 — All failures return HTTP 400
- **Severity:** Low  **Category:** Maintainability / Observability
- **Location:** `routes/student/helpers.py:375` (`error_response(..., status_code=400)` default); every `except` in `notifications.py` uses it.
- **Evidence:** **[R] V12:** mark-read of a non-existent id → **400** (not 404); internal failures ("Failed to load notifications") are also 400 **[C]**.
- **Impact:** Server faults look like client errors to monitoring and clients; unknown ids can't be distinguished from validation failures.
- **Recommended solution:** 404 for missing/not-owned rows, 500 for unexpected exceptions (keep the generic message); ensure the exception reaches Sentry.

---

### NOTIF-22 — Index/migration posture for the hot query
- **Severity:** Low  **Category:** Performance  **Type:** **[U]** (no DB access)
- **Evidence:** Hot queries: `WHERE user_id=? [AND is_read=false] ORDER BY created_at DESC LIMIT 500` and `COUNT(*)` on unread. The table has four single-column indexes (`user_id`, `notification_type`, `is_read`, `created_at`); the boolean index is of little use alone. There is no composite index. The table is created via `db.create_all()` (`app.py:637`), and no migration in `migrations/versions/` creates it, so an index added only to the model will **not** reach an existing production database.
- **Recommended solution:** Composite `(user_id, is_read, created_at DESC)` via an Alembic migration; confirm with `EXPLAIN` on production-sized data first. Consider `notification_type NOT NULL` (all producers set it).

---

### NOTIF-23 — No CSP; WebSocket CORS is `*`
- **Severity:** Low  **Category:** Security  **Type:** **[U]**
- **Evidence:** No CSP header in `app.py`; `services/websocket_messages.py:132` sets `cors_allowed_origins="*"` while `ProductionConfig` forbids `*` for REST CORS. Auth cookies are `SameSite=Lax`, which blocks the obvious cross-site socket hijack, so this is defence-in-depth.
- **Recommended solution:** Reuse `CORS_ALLOWED_ORIGINS` for Socket.IO; roll out a CSP (report-only first) — it also hardens NOTIF-01.

---

### NOTIF-24 — "Follow post" never produces notifications
- **Severity:** Low  **Category:** Product  **Type:** **[C]**
- **Evidence:** `PostFollow.notify_on_comment` (`models.py:1198`, docstring "Follow posts for notifications") is **never read**; `PostFollow` is queried only to display follow state (`discovery.py:171`, `posts/crud.py:625, 1288`). Comment notifications go only to the post author.
- **Recommended solution:** Decide whether following should notify (Q); if yes, fan out to followers (with dedupe and caps); if not, remove the column and relabel the UI.

---

### 3.2 Reviewed and found sound (so you know what *not* to touch)

| Area | Why it is fine | Evidence |
|---|---|---|
| Ownership / authorization on every notification route | Every query filters by `user_id=current_user.id`; `token_required` on all 7 routes; another user's id returns "not found", not data | `routes/student/notifications.py` (all `filter_by(id=…, user_id=current_user.id)`); existing management-route tests |
| CSRF on mutating routes | Double-submit hook (`enforce_csrf`) applies to POST/DELETE; integration tests exercise the real hook | `routes/student/__init__.py`, `tests/integration/conftest.py` |
| `mark-all-read` counter delta | Uses the `UPDATE` rowcount, so the decrement is exact even under concurrency | `notifications.py:209–224` |
| Single funnel for ~35 producers | Persist → counter → broadcast in one function; broadcast failure is caught and does not lose the row (tested) | `notification_service.py`, `test_notify_persists_notification_even_if_broadcast_raises` |
| `title` column length | Producers embed only `user.name` (≤ 100) plus a ≤ 40-char suffix → ≤ 140 < 200, so Postgres will not raise a truncation error inside a caller's transaction | census of `title=` arguments; `MAX_NAME_LENGTH = 100` |
| Connection re-request | 24-hour cooldown after rejection, so it is **not** a notification-spam path | `connections/crud.py` (~1355–1375) |
| FK / deletion | `user_id` has `ON DELETE CASCADE`; unverified-account purge removes notifications | `models.py:1574`, `maintenance_jobs.py:~181` |
| WebSocket authentication | Cookie-based auth by default; unauthenticated sockets rejected | `services/websocket_messages.py` connect handler |
| Settings whitelist | Only two known keys accepted | `notifications.py:448–450` |
| Production config guards | App refuses to start with in-memory rate-limit storage or `*` CORS in production | `config.py:196–215` |
| Scheduler single-runner pattern | `_run_locked` ensures one instance runs a job — reusable for a retention job | `scheduler.py:132` |

---

## 4. Horizontal Scalability Assessment

### 4.1 Current architecture and assumptions

- **No instance-local state in the notification path.** `notification_service.py` has no module-level mutable state, caches, locks or queues. The frontend state is per-tab only. **[C]**
- **Source of truth is the database.** Rows are written in the caller's transaction; all reads (`/all`, `COUNT(*)`) go to the DB. This is the right base for multi-instance operation.
- **Real-time delivery** uses Socket.IO with a Redis `message_queue` (`services/websocket_messages.py`, manager init near line 132), so `emit_to_user` from instance A can reach a user connected to instance B **provided `REDIS_URL` is configured**. The emit is wrapped in try/except, so a Redis outage means silent loss, not request failure. **[C]** — not tested with two processes.
- **Rate limiting** uses Redis storage in production (guarded by `config.py:202`), so limits are shared across instances (but see NOTIF-09 for the IP bucket).
- **Scheduler/jobs:** APScheduler runs inside each app process but wraps work in `_run_locked`, so jobs are single-runner. There is currently **no** notification job.

### 4.2 Concern-by-concern (the requested six-point format)

#### S-1 Redis unread counter (NOTIF-07/08)
1. **Does:** `INCR` on every `notify()` and `DECR` on read/delete; key refreshed to a 1-hour TTL on every write; no reader.
2. **Why it can fail across instances:** It is *atomic*, so concurrent instances don't corrupt it by themselves. The failure is semantic: value created from nothing on a missing key, TTL perpetually extended, applied before commit, non-atomic with the DB change.
3. **Scenario:** Instance A creates a notification inside a request that rolls back after `notify()`; instance B later marks all read; the counter is now off by one forever for an active user.
4. **Current problem or future risk:** *Latent today* (nobody reads it); *immediate* the moment the badge starts using it.
5. **Recommended solution:** Remove it and use an indexed `COUNT(*)` (Option A) — or make it a true cache (Option B) as in NOTIF-08.
6. **Trade-offs:** A: one cheap indexed query per badge load, no Redis dependency on the write path. B: faster reads, but you own reconciliation and cache-invalidation logic. No new infrastructure either way.

#### S-2 Pre-commit side effects (NOTIF-16, NOTIF-08)
1. **Does:** Emits the WebSocket event and increments Redis inside the request, before commit.
2. **Why it fails:** With several instances a crash, timeout or rollback between emit and commit leaves *other* instances' clients holding a notification id that never existed.
3. **Scenario:** Request times out after `notify()` but before `commit()`; user sees a card, clicks it, gets 400.
4. **Type:** Actual current problem **[R]**.
5. **Solution:** Run side effects in an `after_commit` hook (or collect them in a request-scoped outbox and flush after commit).
6. **Trade-offs:** Small amount of plumbing; no new infrastructure.

#### S-3 At-most-once real-time delivery, no catch-up (NOTIF-16)
1. **Does:** Socket.IO event is fire-and-forget; client stops reconnecting after ~10 attempts and never refetches.
2. **Why it fails:** Rolling deploys/instance restarts drop sockets; users on the restarting instance miss everything until they reload.
3. **Scenario:** Deploy restarts instance B; clients reconnect within seconds but events emitted during the gap are lost.
4. **Type:** Actual current problem for any deployment with restarts.
5. **Solution:** On socket `connect`/`reconnect`, refetch unread count (and the list if open).
6. **Trade-offs:** One extra small request per reconnect; no infrastructure.

#### S-4 Badge endpoint cost and shared rate limit (NOTIF-07/09)
1. **Does:** `GET /all` (≤ 500 rows) on every shell page load, limited to 60/min per IP bucket.
2. **Why it fails:** Load scales with users × page views; behind a NAT/load balancer a whole campus shares one bucket.
3. **Scenario:** 100 students on one network load pages in the same minute → 429s, badge missing.
4. **Type:** Actual current problem (rate limit **[R]**); cost problem grows with users.
5. **Solution:** Dedicated count endpoint; authenticate before limiting.
6. **Trade-offs:** One new route.

#### S-5 Unbounded table growth and offset-less listing (NOTIF-14)
1. **Does:** Never deletes notifications; returns newest 500.
2. **Why:** Table size and index size grow linearly forever; every instance's queries slow down together.
3. **Scenario:** Active user accumulates 50k rows; list/COUNT become heavier; older unread items are invisible yet counted.
4. **Type:** Future scaling risk (becomes real with volume).
5. **Solution:** Cursor pagination, retention job under `_run_locked`, composite index (migration).
6. **Trade-offs:** Needs retention policy (Q3) and a migration; no new infrastructure.

#### S-6 Push dispatch in the request path (NOTIF-17)
1. **Does:** Synchronous FCM multicast inside the HTTP request, before commit.
2. **Why:** Worker threads block on an external service; failures/latency propagate to users; a crashed instance may have already pushed.
3. **Scenario:** Firebase slow → help-request creation slow or failing.
4. **Type:** Future risk (push is not initialised today).
5. **Solution:** Enqueue to the existing RQ worker after commit.
6. **Trade-offs:** Uses infrastructure that already exists (`worker.py`).

### 4.3 Immediate problems vs. future improvements

| Immediate (fix now) | Future / when scale demands |
|---|---|
| Pre-commit emit and counter increment (S-2) | Composite index + `EXPLAIN` on production-size data (NOTIF-22) |
| No reconnect resync (S-3) | Retention job and per-user cap (S-5) |
| Shared per-IP rate-limit bucket (S-4) | Async push via RQ if push is kept (S-6) |
| Badge via 500-row payload (S-4) | Multi-device `push_tokens` table |

**No distributed locks are needed** for this feature: single-row operations are protected by DB row semantics once mark-read/delete use conditional `UPDATE`s. **No new infrastructure** is required.

### 4.4 Unverified (needs your environment)
- Whether `REDIS_URL` is set in production for the Socket.IO `message_queue` (otherwise cross-instance push silently doesn't work).
- Actual notification volumes and fan-out sizes (the only fan-out I traced is help-request, capped at 10 recipients).
- Real concurrency behaviour of mark-read/delete on PostgreSQL.

---

## 5. Data Usage and Cleanup Report

Classification: **1** Confirmed unused · **2** Potentially unused — verify · **3** Required indirectly — retain · **4** Redundant — consolidate.

| Resource | Current usage | Evidence | Class | Recommendation |
|---|---|---|---|---|
| `notifications` table | Core storage | Written by `notify()`; read by 7 routes | Required | Keep. Add composite index (migration); make `notification_type` NOT NULL |
| `notifications.related_type`, `related_id` | Written by most producers; returned by API | **No frontend reader** (only a comment in `notification.init.js`); not used for links | 3 / 2 | Keep — they are the right basis for server-built links and de-dup (NOTIF-10/13). Do not delete |
| `notifications.read_at` | Written on read; returned by API | No consumer found in `static/js/notification/` | 2 | Keep unless product confirms it's never needed; cheap |
| `notifications.link` | Set by ≤ 4 of 35 producers | Frontend reads it; two targets 404 (V9) | 3 | Keep; fix producers/targets |
| `User.notification_settings.enable_notification` | Honoured on read only | `notifications.py:58` | Required | Keep; enforce at creation (NOTIF-05) |
| `…enable_notification_sound` | Stored/toggled/returned | No code plays a sound | **1** (effect) | Implement or remove (Q11). Needs no migration if removed from code (JSON key can linger) |
| `…notification_category`, `send_email_notification`, `email_frequency` | Defaults in `auth.py:72–75`; echoed by `GET /settings` | No endpoint can change them; no consumer | **1** | Remove from defaults and GET response; existing JSON keys are harmless |
| `GET /profile/notifications/settings` | Backend endpoint | Frontend only `POST`s settings (`notification.api.js:20,25`) | 2 | Remove, or use it to populate toggles instead of piggybacking on `/all` |
| `mark-all-read` param `before_date` | Parsed (`:203`) | No frontend sends it; naive/aware datetime handling **[U]** on Postgres | 2 | Remove or document; if kept, normalise to UTC-naive |
| `GET /all` response `enable_notification*` echo | Used to set toggles | `notification.events.js:69–72` | 3 | Keep unless `GET /settings` is adopted |
| Redis key `sh:1:notif:unread:{id}` | INCR/DECR on every write | **No production reader** (search of all `.py/.js/.html`) ; tests only | **2 / 4** (duplicate of `COUNT(*)`) | Remove (Option A) or finish as real cache (Option B) — Q2. No data migration; keys expire |
| `counter_cache_service` notification wrappers | Callers: `notify()`, 4 routes | Same as above | 2 | Follow the Redis decision. (Message-unread wrappers are a different feature — untouched) |
| `notificationState.hasMore/lastCursor` + `updateHasMore`, `updateCursor`, `getLastCursor`, `canLoadMore`, `reset` | Defined | **0 references** across all `static/js` (5 of 10 methods) | **1** | Remove, or implement pagination (NOTIF-14) |
| `notificationAPI.getUnreadCount` | Defined | **0 call sites** | **1** (until the new endpoint adopts it) | Rewrite to the new endpoint or delete |
| `notification.templates.js` / `.utils.js` badge selectors | `#notification-badge`, `.notification-badge` | Matches no element on live page (NOTIF-03) | — | Fix, not delete |
| `PushNotificationService` 8 module helpers (`notify_streak_updated`, …) | Defined | **0 non-test references** | 2 | Delete if push is removed; else wire or delete individually |
| `PushNotificationService.send_*`, `initialize` | `send_*` called only by help-request code; `initialize` never | Runtime check: `initialize` call count during `create_app()` = 0 | 2 | Decision Q4 |
| `User.fcm_token`, `POST/DELETE /profile/fcm-token`, `push/*.js`, `firebase-messaging-sw.js`, `toggle-push-notification` UI | Present | Inert end-to-end (NOTIF-04) | 2 / 3 | Keep **together** or remove **together**. Removing the column requires a migration and drops stored tokens (acceptable — they're unusable) |
| `PostFollow.notify_on_comment` | Column default `True` | Never read (NOTIF-24) | **1** | Implement follow-notifications or drop (migration) |
| `templates/post/feed.html` (3 000+ lines) incl. a duplicate notification section | Not rendered | No `render_template`/include references it | 2 | Likely dead; verify with product, then remove (outside notification scope otherwise) |
| `static/css/notification.css` (root copy) | Not linked | No template/JS references; 1 069 diff lines vs `css/notification/notification.css` | 2 | Remove after confirming; keep the `notification/` copy used by `feed2.html` |
| `maintenance_jobs` notification delete for unverified accounts | Active | `maintenance_jobs.py:~181` | Required | Keep |

**What depends on removals:** the Redis counter's only dependents are `notify()` and four route handlers, plus the notification-counter tests in `tests/unit/services/test_counter_cache_service.py` and `tests/integration/test_notifications_funnel.py`, which would need updating. Push removal touches `profile.py`, `connections/crud.py`, `feed2.html`, `push/*`, tests.

---

## 6. End-to-End Data Consistency Report

| # | Path | Mismatch / break | Concrete example | Correction |
|---|---|---|---|---|
| E-1 | Producer → API → DOM | Server strings treated as HTML by the client | Name `<img onerror>` → card executes (NOTIF-01) | Escape on output |
| E-2 | `notify()` → commit → WS | Event + counter precede the transaction outcome | Rolled-back request leaves ghost card and counter +1 (V3) | After-commit side effects |
| E-3 | Settings → creation | Setting applies to display only | Disabled user: rows, counter, push all continue (V4) | Enforce in `notify()` |
| E-4 | Settings → UI | Server returns `0`/`[]` while WS handler increments badge | Badge shows live increments the list never shows | One source of truth |
| E-5 | DB → API → badge | `unread_count` = all unread, `notifications` capped at 500 | 505 unread, 500 shown (V11) | Pagination; separate count endpoint |
| E-6 | Frontend badge element → DOM | JS targets `#notification-badge`; live page has none; shell uses `#shell-notif-count`; base uses `#notif-count` | Badge never visible on SPA (NOTIF-03) | One convention |
| E-7 | Type → chip filter | Chips reference types no producer creates; some produced types match no chip | `message`, `helpful` never produced; `instant_connection` not under Connections (V14) | Type registry |
| E-8 | Producer → `link` → router | Links to routes that don't exist | `/help-request/1`, `/student/badges/` → 404 (V9) | Link map + validation |
| E-9 | Send vs. edit → Notification | Two mention implementations diverge | `thread_mention` (text, no push, no membership check) vs `mention` (empty, push, membership check) (V5/V6) | Single mention service |
| E-10 | Error → client | 400 for not-found *and* internal failure | Client cannot tell retryable from permanent (V12) | Proper status codes |
| E-11 | UI toggle → server flip | Client displays desired state; server flips current state | Retry flips back (V15) | Explicit value |
| E-12 | Active filter state → displayed list | `activeType` updated even when load is skipped | Mark-all acts on invisible filter (NOTIF-15) | Apply state on response |
| E-13 | Push toggle → FCM | Token stored; backend never initialised; config placeholders | User sees "enabled", receives nothing (runtime check, NOTIF-04) | Complete or remove |
| E-14 | Docs → code | `ARCHITECTURE.md` §10 (lines 492, 502) and `PRODUCT_OVERVIEW.md` (170–172) say the counter self-heals "on any read" and that drift is bounded to one TTL; in code nothing reads it and every write refreshes the TTL (V2). `PRODUCT_OVERVIEW.md:170` also lists thread `@mention`s among notifications that go through the single funnel — they bypass it (NOTIF-02/06). *(The README's claim that `INCR`/`DECR` are atomic is accurate and not in question.)* | — | Update the docs to match whichever option is chosen for Q2 |

---

## 7. Testing Gaps

### 7.1 What exists (baseline: 62 tests pass)

`tests/integration/test_notifications_funnel.py` (3), `test_notifications_management_routes.py` (18), `tests/unit/services/test_notification_service.py` (3), `test_push_notifications.py` (21), `test_counter_cache_service.py` (10). There are **no JavaScript tests** (no `package.json`, no `*.test.js`/`*.spec.js`) and no tests for thread-mention notifications (`thread_mention` / `detect_mentions_in_thread` have no test references).

### 7.2 Tests that pass but don't verify what they claim

| Test | Problem |
|---|---|
| `test_connection_request_creates_notification_and_increments_unread_counter` | Asserts `after >= before + 1` after a recompute-on-miss seed — weak; would pass with the wrong-value-on-missing-key defect (NOTIF-08, V1) |
| `test_mark_all_read_decrements_unread_counter_by_exact_row_count` | Seeds the counter "to assert the exact decrement" but **ends at `assert marked == 3` and never asserts the counter** |
| `test_decrement_allows_negative_by_design` (`test_counter_cache_service.py:63`) | Locks in a negative unread count as intended behaviour |
| Disabled-notifications test in management routes | Documents "returns empty" as expected, so it will fail (correctly) if NOTIF-05 is fixed by suppressing creation — update deliberately |
| `test_push_notifications.py` (21 tests) | Thoroughly test a service that is never initialised in the running app; none would have caught the deleted `initialize()` call |

### 7.3 Missing tests (behaviour to verify, level)

| # | Behaviour | Level | Finding |
|---|---|---|---|
| T-1 | `createNotificationCard` with hostile `title/body/link`: no element with `on*` attributes, text preserved, `javascript:`/`//host` links rejected | JS unit (jsdom) | 01 |
| T-2 | Hostile display name + comment → victim's list renders inert text; no dialog | E2E (Playwright) | 01 |
| T-3 | Mention of a non-member creates no notification and leaks no text; member mention goes through `notify()` and emits | Integration | 02 |
| T-4 | Blocked user generates 0 notifications for every producer type | Integration (parametrised over producers) | 12 |
| T-5 | Edit message with the same `@b` 3× → still 1 notification; adding `@c` notifies only `c` | Integration | 06 |
| T-6 | Live push on the SPA page updates a visible badge; mark-read decrements; unread-delete decrements | E2E | 03, 18 |
| T-7 | App startup initialises push when credentials exist, fails open otherwise (or: no push UI when removed) | Unit/Integration | 04 |
| T-8 | Setting off → `notify()` creates no row, no counter change, no emit | Integration | 05 |
| T-9 | Caller rolls back → no emit, no counter change (or: counter option A removed) | Integration | 08, 16 |
| T-10 | Unseeded Redis key: increment/decrement/read produce the true DB count (if Option B) | Unit | 08 |
| T-11 | Two concurrent `mark-read` calls for one row → exactly one decrement; concurrent delete similarly | Concurrency (threads against Postgres, not SQLite) | 08 |
| T-12 | Two users on one IP each under 60/min → no 429 | Integration | 09 |
| T-13 | Every producer's `link` resolves (not 404); chips cover every produced type; chips with no producers fail the build | Integration (walk `url_map`) | 10, 11 |
| T-14 | Toggle same like 5× → 1 notification | Integration | 13 |
| T-15 | > 500 rows paginate without gaps/duplicates; `has_more` correct; retention job deletes only eligible rows and only one instance runs it | Integration | 14 |
| T-16 | Chip tap during in-flight load ends on the chosen filter; mark-all uses the displayed filter; pull-to-refresh never leaves a blank panel | JS component | 15 |
| T-17 | Socket disconnect → reconnect triggers a refetch; duplicate events render one card | JS component | 16 |
| T-18 | Logout clears FCM token; token unique; `Unregistered` prunes it; push enqueued only after commit | Integration | 17 |
| T-19 | Same settings request twice → same value; concurrent updates of two keys keep both | Integration | 19 |
| T-20 | Not-found → 404; unexpected exception → 500 and reaches error tracking | Integration | 21 |
| T-21 | Multi-instance: two app processes + shared Redis; push from A reaches a socket on B; restart one instance mid-request | Staging/system test | §4 |

---

## 8. Recommended Implementation Plan

Ordered by your priority list (correctness/data loss → security → reliability/concurrency → scalability → product consistency → performance → cleanup). Nothing below has been implemented.

### Milestone 1 — Stop-ship security and authorization
- **Objective:** Remove the cross-user script execution and the private-content leak.
- **Issues:** NOTIF-01, 02, 12, 23 (CSP part).
- **Files likely to change:** `notification.templates.js`, `notification.init.js`, `notification.delegation.js` (link handling), `thread_message_service.py`, `routes/student/threads/messaging.py`, `notification_service.py` (block check, optional `actor_id`), `app.py` (CSP report-only), Socket.IO origin config.
- **Proposed implementation:** Escape/`textContent` for all card fields; same-origin relative-link validation on write and before navigation; mentions limited to thread members and routed through `notify()`; block check centralised in `notify()`.
- **DB/infra:** None (optional `actor_id` column is deferred to Milestone 2 — see Q6/Q9).
- **Tests:** T-1, T-2, T-3, T-4.
- **Dependencies:** Q6, Q7, Q12.
- **Risks:** Behaviour change for non-member mentions; a CSP can break inline scripts — start report-only.

### Milestone 2 — Correctness of the user-visible feature
- **Objective:** Make badge, live updates, settings and mentions behave as the UI promises.
- **Issues:** NOTIF-03, 05, 06, 10, 11, 15, 16, 18, 19, 20, 21, 24.
- **Files:** `feed2.html` (badge), `notification.utils.js`/`events.js`/`init.js`/`delegation.js`, `socket.js` (resync), `notification_service.py` (settings check, after-commit side effects), a single mention service, a type→chip→link registry, `notifications.py` (explicit-value settings, status codes), `models.py`/`auth.py` (settings defaults).
- **Implementation:** one badge convention; request-sequence guard; refetch on `connect`; client dedupe by id; emit/increment in `after_commit`; one mention implementation with edit-diff; server-built, validated links; remove chips with no producers (or add producers); proper 404/500.
- **DB/infra:** none required (registry is code).
- **Tests:** T-5, T-6, T-8, T-9, T-13, T-16, T-17, T-19, T-20.
- **Dependencies:** Q1, Q5, Q8, Q10, Q11, Q16 and Milestone 1.
- **Risks:** Moving emit to after-commit changes timing slightly; chip/type changes are user-visible.

### Milestone 3 — Counter, concurrency and rate-limit correctness
- **Objective:** Make the unread count trustworthy and cheap; fix the shared IP bucket.
- **Issues:** NOTIF-07, 08, 09, 13 (and the badge endpoint used by M2).
- **Files:** `notifications.py` (new `GET …/unread-count`; conditional `UPDATE` for mark-read/delete), `notification_service.py`, `counter_cache_service.py` (remove wrappers if Option A), `rate_limit_service.py`/route decorators, `app-shell.js`, `base.html`.
- **Implementation:** Option A (indexed `COUNT(*)`, delete Redis counter) or Option B (true cache). Authenticate before limiting. Optional dedupe window for repeat events.
- **DB/infra:** composite index migration (shared with M4).
- **Tests:** T-9, T-10 (B only), T-11, T-12, T-14.
- **Dependencies:** Q2, Q9.
- **Risks:** Option A shifts load to the DB — mitigated by the index; verify with `EXPLAIN` first. Test update for the existing counter tests.

### Milestone 4 — Data lifecycle and performance
- **Objective:** Bound growth; support >500 notifications; index the hot query.
- **Issues:** NOTIF-14, 22.
- **Files:** `notifications.py` (cursor pagination, `has_more`), `scheduler.py` + a new job module (retention under `_run_locked`), Alembic migration, `notification.state.js`/`events.js`.
- **Implementation:** `before_id` cursor; scheduled purge of read rows older than N days and a per-user cap; composite `(user_id, is_read, created_at DESC)`; `notification_type NOT NULL` after backfill.
- **DB/infra:** one migration; no new infrastructure.
- **Tests:** T-15.
- **Dependencies:** Q3. Take a backup first; run retention in dry-run (count only) before enabling deletes.
- **Risks:** Deleting user-visible history — policy must be approved.

### Milestone 5 — Push channel (decide, then finish or remove)
- **Objective:** End the "toggle that does nothing" state.
- **Issues:** NOTIF-04, 17.
- **Files:** `app.py` (init), `push_notifications.py`, `profile.py`, `auth.py` (logout), `connections/crud.py`, RQ job, `push/*`, `firebase-messaging-sw.js`, `feed2.html`.
- **Implementation:** *Keep:* env-driven Firebase config, initialise on startup (fail-open), token table with uniqueness, clear on logout/Unregistered, enqueue after commit, honour settings. *Remove:* delete UI, routes, helpers, column (migration).
- **Tests:** T-7, T-18.
- **Dependencies:** Q4; Firebase credentials if kept.
- **Risks:** Browser permission prompts and service-worker caching if kept.

### Milestone 6 — Cleanup and documentation
- **Objective:** Remove dead code and make docs truthful.
- **Issues:** the §5 table; NOTIF-20, 24; E-14.
- **Files:** dead settings keys, unused state/API members, unreferenced `feed.html` and root `notification.css` (after confirmation), `ARCHITECTURE.md` §10, `PRODUCT_OVERVIEW.md`, `README.md`.
- **Tests:** existing suite must stay green.
- **Risks:** Low; do last.

---

## 9. Questions and Decisions Required

| # | Question | Why it matters | Options |
|---|---|---|---|
| Q1 | Should "Disable notifications" **stop creating** notifications, or only hide them? | Changes storage, push and re-enable behaviour (NOTIF-05) | (a) Suppress at creation *(recommended)*; (b) keep hide-only but also hide live push/badge consistently; (c) per-category settings |
| Q2 | Keep or remove the Redis unread counter? | Decides M3 scope and whether tests/docs are rewritten (NOTIF-07/08) | (A) Remove, use indexed `COUNT(*)` *(recommended unless measured load says otherwise)*; (B) keep as a proper cache with seed-on-miss, no TTL refresh, after-commit updates, reconciliation |
| Q3 | Retention policy? | Deleting history is irreversible (NOTIF-14) | e.g. read rows > 30/60/90 days; per-user cap (e.g. 1 000); never delete unread; or archive instead of delete |
| Q4 | Finish push or remove it? | Large scope difference; needs Firebase credentials (NOTIF-04/17) | (a) Remove UI + code; (b) finish for help-request only; (c) finish for all notification types via RQ; multi-device token table or single token |
| Q5 | Where should each notification type navigate? Does a help-request page exist/planned? Is there a badges page URL? | Links currently 404 or are absent (NOTIF-10) | Provide a type → URL table; or open the related object in the SPA via `related_type/related_id` |
| Q6 | Block semantics for notifications? | Defines abuse protection (NOTIF-12) | Suppress all notifications from blocked users; also hide already-delivered ones; apply to mentions/likes/comments/thread invites |
| Q7 | Thread mentions: members only? Should the notification include message text? | Privacy vs usefulness (NOTIF-02) | Members only, body = generic ("X mentioned you in <thread title>") *(recommended)* vs. with 80-char preview |
| Q8 | On message edit, notify only **newly added** mentions? | Fixes re-notification (NOTIF-06) | New-only *(recommended)*; or never re-notify on edit |
| Q9 | De-duplication rule for repeat events (like/unlike, repeated comments)? | Needs `actor_id` or a window (NOTIF-13) | Notify first like only; collapse within N minutes; add `actor_id` column (migration) |
| Q10 | Should DMs and "helpful" marks produce notifications? Which chips should exist? | Two chips are empty by design today (NOTIF-11) | Add producers, or remove chips; define the type→chip map |
| Q11 | Implement or remove sound, category and email-frequency settings? | Dead options in the UI/API (NOTIF-20) | Remove; or implement sound client-side and email digests via the job queue |
| Q12 | Is a Content-Security-Policy acceptable? | Templates use inline scripts/handlers; CSP can break them (NOTIF-01/23) | Report-only first; then enforce with nonces; or skip CSP and rely on escaping |
| Q13 | Any need to scrub existing stored names/titles for HTML? | Escape-on-output makes it unnecessary for notifications, but other screens may be affected | Output escaping only *(recommended)*; or also sanitise at write + backfill |
| Q14 | What are the target instance count and production `REDIS_URL`/Socket.IO `message_queue` settings? | Cross-instance push only works with the Redis queue (§4.4) | Confirm configuration; run T-21 in staging |
| Q15 | Should "follow post" notify followers? | Model/UI promise it; code doesn't (NOTIF-24) | Implement with caps and dedupe; or drop `notify_on_comment` and relabel |
| Q16 | Is changing the settings endpoint to accept an explicit `value` acceptable? | Existing clients send none (NOTIF-19) | Backward-compatible: flip only when `value` is omitted |
| Q17 | OK to remove apparently-unreferenced assets (`templates/post/feed.html`, root `static/css/notification.css`)? | Static search only; could be used by tooling I can't see | Confirm, then remove in M6 |

---

## 10. Final Recommendation

### Must be fixed before production
1. **NOTIF-01** stored XSS (escape output; validate links; CSP report-only).
2. **NOTIF-02** thread-mention leak (members only; through `notify()`).
3. **NOTIF-12** block enforcement for notifications.
4. **NOTIF-03** badge on the live page, and **NOTIF-04** either make push work or remove the toggle — these are advertised features that don't function.
5. **NOTIF-05, NOTIF-06** user-visible logic errors (disabled-but-still-delivered, mention re-notification).

### Should be fixed soon
- NOTIF-16 (after-commit side effects, resync on reconnect), NOTIF-07/08/09 (badge endpoint, counter decision, rate-limit bucket), NOTIF-10/11 (links and filters), NOTIF-14 (retention + pagination), NOTIF-15, NOTIF-17 (if push is kept).

### Can safely be postponed
- NOTIF-13, 18, 19, 21, 22, 23, 24 — low impact or dependent on earlier milestones. Index work (NOTIF-22) should be driven by `EXPLAIN` on real data.

### Candidates for removal (see §5 for dependencies)
Redis unread counter (if Option A); `notificationState` pagination leftovers; `getUnreadCount` (as currently defined); `notification_category`/`send_email_notification`/`email_frequency`; `GET /settings` (or adopt it); the 8 unused push helpers; `PostFollow.notify_on_comment` (if follow-notify is not wanted); unreferenced `feed.html` and root `notification.css` (after confirmation). Push code in its entirety if Q4 = remove.

### Requires your approval or clarification
Q1–Q17 above. The ones that block implementation start are **Q1, Q2, Q4, Q6, Q7, Q12**.

### Recommended order of implementation
**M1 (security) → M2 (correctness) → M3 (counter/concurrency/rate limit) → M4 (lifecycle/performance) → M5 (push decision) → M6 (cleanup).** M1 and the Q4 decision can proceed in parallel since they don't overlap.

---

*End of report. No application source files were changed during this audit; the only repository change is this file.*
