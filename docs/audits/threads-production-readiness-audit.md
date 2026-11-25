# Feature Production-Readiness Audit — Threads

| | |
|---|---|
| **Repository** | `Devpaul-01/StudyHub` @ `d41565a` (HEAD of default branch, 2026-11-22) |
| **Feature audited** | **Threads** — group discussion rooms (post-linked and standalone): creation, membership/join/invite workflow, real-time messaging (Socket.IO), reactions/pins/read-state, attachments, discovery, Learnora AI assistance, AI meeting notes |
| **Audit date** | 2026-11-24 |
| **Mode** | Read-only investigation. No application source, config, dependency or DB was modified. All verification ran in a throw-away copy of the repo (SQLite test DB, fakeredis). |
| **Note on this file** | A previous audit of a different feature (Auth) was committed under this same filename. It is unchanged in git history (`git show HEAD:FEATURE_PRODUCTION_READINESS_AUDIT.md`). |

---

## 1. Executive Summary

### Overall assessment: **Critical issues found — not production-ready in its current state.**

The Threads feature is broad and has a visible hardening history (many `FIX`/`ENG-n` annotations, 210 passing tests, an atomic `member_count` update, row-locking on one join path, a centralized authorization helper, a shared message service). The happy path works. But end-to-end tracing exposed one cross-thread confidentiality hole, several authorization gaps in the real-time layer, a membership lifecycle that traps users, a post-linked creation path that appears to leave the creator outside their own thread, and a reconnect model that silently loses messages. Many of these sit exactly where the existing tests do not look (WebSocket handlers, cross-layer contracts, concurrency, lifecycle transitions).

### Findings by severity

| Severity | Count |
|---|---|
| Critical | 1 |
| High | 7 |
| Medium | 17 |
| Low | 7 |
| **Total** | **32** |

Confirmation level: **11 findings were reproduced by executing code** (tagged `Verified (V#)`, see §7.2), **18 are confirmed by tracing the code but not executed** (`Static`), and **3 are explicitly unverified risks** (`Unverified risk`: TH-13, TH-15, TH-32).

### Most important findings

1. **TH-01 (Critical)** — `thread_ai_action` / `_call_learnora_action` fetch the target message by ID with no check that it belongs to the thread the caller is a member of. Any member of *any* thread can have the AI process a message from *any other* private thread and get the result posted into their own thread. Reproduced (V12).
2. **TH-02 (High)** — Removing a member, a member leaving, or deleting a thread never removes their sockets from the `thread_<id>` Socket.IO room. They keep receiving live messages, edits, reactions and pins until they disconnect.
3. **TH-03 (High)** — The post-linked thread created by `create_post` inserts a `Thread` but no `ThreadMember(role="creator")` row, while every permission and send check is membership-based. (Static finding; the existing test suite creates the creator row by hand in fixtures, which masks it.)
4. **TH-04 (High)** — Users who leave or are removed can never request to rejoin (permanent `409 "already approved"`), and a user who was *invited* and then clicks *Join* hits a `UNIQUE` violation (`400`). Both reproduced (V1, V2).
5. **TH-06 (High)** — `@mention` notifications are created for *any* user (not only thread members) and contain the first 80 characters of the private message. Reproduced (V9).
6. **TH-07 (High)** — The `request_ai_response` socket event has **no rate limit at all**; AI work runs on an unbounded in-process queue; a failing job leaves every client stuck on "Learnora is thinking…".
7. **TH-08 (High)** — On reconnect the client reloads the thread list and rejoins the room but never re-fetches missed messages; the server's `after_id` cursor returns the *newest* page, not the next one, so a gap cannot be filled even if the client tried. Reproduced (V6).
8. **TH-13 (High)** — The documented deployment (`gunicorn -w N`, `async_mode="threading"`, polling-first transport, in-process `socket_to_user`) is not safe for more than one worker/instance, and `Procfile` actually launches the Werkzeug dev server via `start-all.py`.

### Major reliability / scalability concerns

- Realtime layer requires sticky routing and single-process assumptions (TH-13); AI work is process-local and unbounded (TH-07).
- No idempotency for message sends; a commit-then-error path makes retries duplicate messages (TH-12).
- Capacity/limit checks are check-then-act; the one `SELECT … FOR UPDATE` used for capacity is defeated by a SQLAlchemy identity-map quirk (TH-14, reproduced V13).
- Rate limiting on 15 read endpoints is keyed by IP, and `ProxyFix` is configured without `x_for` (TH-15).
- Pervasive N+1 queries and unbounded list endpoints (TH-23).

### Is the feature safe to consider production-ready today?

**No.** TH-01, TH-02, TH-06 are confidentiality problems for a feature whose product promise is *private* collaboration groups; TH-03/TH-04 break core membership flows; TH-08/TH-12 undermine trust in message delivery. None of these requires a large redesign — see §8 for a milestone plan (the first milestone is small and high-value).

### What could *not* be verified (read this before relying on the report)

- **WebSocket handlers were not executed live.** Findings about handlers (`handle_send`, `request_ai_response`, `thread_ai_action`, room membership, typing, read receipts) come from reading the code. Where the logic lives in a service (`thread_message_service`, `_call_learnora_action`) I executed it directly.
- **No PostgreSQL / multi-instance run.** Tests used SQLite; `SELECT FOR UPDATE` is a no-op there. Lock/contention behavior, partial indexes and cascade performance are reasoned from the models and migrations, not measured.
- **No load test, no production config.** Deployment topology (reverse proxy, worker count, Redis availability) is inferred from `Procfile`, `start-all.py`, `app.py`, `config.py` and docs.
- **No browser/runtime check of the frontend.** Escaping and contract findings come from reading `static/js/thread/*` and `static/js/feed/*`.
- **TH-03 was not executed** (the `create_post` route needs a large fixture); it is a direct reading of `routes/student/posts/crud.py` L1153-1167 and should be confirmed with a test first (V-TH03 in §7).

---

## 2. Feature Architecture and Execution Flow

### 2.1 Components

**Frontend** (vanilla ES modules, `static/js/thread/`, ~7.2k LOC): `thread.api.js` (REST client), `thread.websocket.js` (Socket.IO emit/handlers), `thread.state.js` (global store incl. `activeThreadId`, pending messages), `thread.templates.js` (HTML rendering; `esc`/`escAttr`/`renderMessageText`), `thread.init.js` (boot, reconnect handler). Thread creation is also triggered from the **feed** (`static/js/feed/feed.events.js`, `feed.api.js`) and **connections** (`static/js/connection/connection.api.js`), and thread links are read from **post view** (`post-view.js`). Legacy copies (`static/js/post/{feed.js,feed.html,newfeed.html,resp.js}`) are unreferenced.

**REST** (`routes/student/threads/`, blueprints mounted under `/student`):

| Module | Purpose | Key endpoints |
|---|---|---|
| `crud.py` (822 LOC) | create/update/close/delete/avatar/stats/settings | `POST /threads/create`, `POST /threads/create-standalone`, `POST /threads/<id>/details` (read via POST), `PATCH/DELETE /threads/<id>`, `/close`, `/reopen`, `/avatar`, `/stats`, `/settings` |
| `membership.py` (1189 LOC) | join/approve/reject/invite/add/remove/leave/roles | `/join`, `/requests/<id>/approve\|reject`, `/invite/<uid>`, `/invites/<id>/accept\|decline`, `/members/add`, `/remove/<uid>`, `/leave`, `/members/<uid>/role`, `/pending-requests`, `/my-requests`, `/invites` |
| `messaging.py` (796 LOC) | history, search, pinned, send/edit/delete (REST fallback), upload | `GET/POST /threads/<id>/messages`, `PATCH/DELETE …/<mid>`, `/messages/search`, `/messages/pinned`, `/messages/upload` |
| `discovery.py` (853 LOC) | department stats, popular, recommended, help suggestions, my-threads, open, single thread | `/threads/departments`, `/popular`, `/recommended`, `/help/suggestions`, `/my-threads`, `/open`, `GET /threads/<id>` |
| `ai.py` (165 LOC) | AI meeting notes | `POST/GET /threads/<id>/meeting-notes` |
| `services/thread_authorization.py` | `require_moderator_or_creator` etc. | — |
| `services/thread_message_service.py` | create/edit/delete message (shared by REST + WS) | — |

**Real-time** (`services/websocket_threads.py`, 2344 LOC): `ThreadWebSocketManager` registers `join_thread_room`, `leave_thread_room`, `send_thread_message`, `thread_typing[_stop]`, `add/remove_thread_reaction`, `edit/delete/pin/unpin_thread_message`, `mark_thread_read`, `message_delivered`, `request_ai_response`, `thread_ai_action`. Rooms: `thread_<id>` (broadcast) and `user_<id>` (personal). AI runs in a per-process `ThreadPoolExecutor(8)`. Socket.IO is created in `services/websocket_messages.py` (`async_mode="threading"`, `cors_allowed_origins="*"`, Redis message queue only if `REDIS_URL` set). Sid→user mapping lives in the in-process dict `message_ws_manager.socket_to_user`.

**Data** (`models.py`): `Thread`, `ThreadMember`, `ThreadJoinRequest` (also stores invites as `status="invited"`), `ThreadMessage`, `ThreadMessageReaction`, `ThreadMessageAttachment`, `ThreadMessageReadReceipt`, `ThreadMeetingNote`; plus `Mention` and `Notification` written from the message path.

**Infrastructure touched:** PostgreSQL (SQLite in tests), Redis (rate limiter, presence, cache `sh:1:thread:dept_stats`, notification counters, Socket.IO MQ), Cloudinary/Supabase (attachments, avatars), LLM provider chain (`provider_manager`), in-process executor.

### 2.2 End-to-end flow

```mermaid
flowchart TD
    subgraph Client
      UI[Thread UI / thread.state.js]
      WSC[thread.websocket.js]
      API[thread.api.js]
    end
    subgraph "App process (threading mode)"
      REST[routes/student/threads/*]
      WSM[ThreadWebSocketManager]
      MSG[thread_message_service]
      EXEC["ThreadPoolExecutor(8) — Learnora"]
      SID["socket_to_user (in-process dict)"]
    end
    DB[(PostgreSQL)]
    R[(Redis: limiter, presence, counters, MQ)]
    CL[(Cloudinary / Supabase)]
    LLM[LLM provider chain]

    UI -->|optimistic message, client_temp_id| WSC
    WSC -->|send_thread_message| WSM
    WSM --> MSG --> DB
    WSM -->|"emit thread_<id> room + user_<id> rooms"| R
    R --> WSC
    UI --> API -->|history, join, create...| REST --> DB
    REST -->|notify() + broadcast after commit| WSM
    REST --> CL
    WSM -->|"@learnora / request_ai_response"| EXEC --> LLM
    EXEC --> DB
    EXEC -->|broadcast_ai_message| WSM
    WSM -. reads .-> SID
    WSM --> R
```

**Send-message path (WS):** UI adds a pending bubble keyed by `client_temp_id` → `send_thread_message` → per-user rate check (Redis, fail-open) → `thread_message_service.create_thread_message` (membership check, sanitize with `bleach`, insert, mention parsing, `message_count`/`last_activity` update, commit) → ack to sender with `client_temp_id` → `new_thread_message` to `thread_<id>` → `thread_list_update` to each member's `user_<id>` room → optional Learnora trigger (executor) → recipients emit `mark_thread_read` or `message_delivered`.

**Membership path (REST):** `join` (always creates a `ThreadJoinRequest`) → creator/moderator `approve` (locks thread row, inserts `ThreadMember`, atomic `member_count +1`) → broadcast `thread_member_joined`. Invites reuse the same table; `accept` creates the member row. `remove`/`leave` delete the member row and decrement the counter; **no room eviction, no join-request cleanup**.

**Catch-up path:** on reconnect `thread.init.js` re-emits `join_thread_room` and reloads the thread *list*; message history for the active thread is **not** re-fetched (`afterId` is defined in `thread.api.js` and never called).

---

## 3. Findings and Issues

**Legend.** *Status*: `Verified (V#)` = reproduced by running code (scratch tests listed in §7.2); `Static` = confirmed by tracing the implementation, not executed; `Unverified risk` = plausible from code/config, runtime behaviour not exercised. Line numbers are as of commit `d41565a`.

### 3.1 Critical

#### TH-01 — AI message actions read any message by ID, across thread boundaries
- **Severity:** Critical · **Category:** Security · **Status:** Verified (V12)
- **Location:** `services/websocket_threads.py` → `handle_thread_ai_action` (~L2013-2060, `target_msg` lookup ~L2048) and `_call_learnora_action` (L2261; `ThreadMessage.query.get(message_id)` at ~L2276).
- **Evidence:** The handler checks that the caller is a member of the `thread_id` *in the payload*, then loads the message by `message_id` alone. `_call_learnora_action` does the same, builds the prompt from `target_msg.text_content`, and inserts the AI reply into the payload's `thread_id` with `reply_to_id=message_id`. V12: with attacker in thread A and a secret message in thread B, the prompt contained B's text and an AI message in A was created whose `reply_to_id` points at B's message.
- **Problem:** Missing `message.thread_id == thread_id` check (and no `is_deleted` check).
- **Impact:** Any authenticated user can create a thread (or join an open one), then enumerate sequential integer message IDs and have the AI explain/translate/summarize/convert private messages from any thread; the output is posted into the attacker's thread. Additionally, `get_thread_messages` resolves reply previews by `reply_to_id` with no thread filter, so the first 120 characters of B's message can also surface as a reply preview in A.
- **Reproduction:** Join any thread A; emit `thread_ai_action {thread_id: A, message_id: <id in thread B>, action: "explain"}`.
- **Recommended solution:** In both the handler and `_call_learnora_action`, load the message with `filter_by(id=message_id, thread_id=thread_id, is_deleted=False)`; reject otherwise with a `thread_error`. In `get_thread_messages` and `_build_message_payload`, restrict the parent lookup to `thread_id` (defense in depth).
- **Implementation considerations:** Tiny change; no migration. Existing rows with cross-thread `reply_to_id` (if any) can be found with `SELECT … FROM thread_messages m JOIN thread_messages p ON p.id=m.reply_to_id WHERE p.thread_id<>m.thread_id`; decide whether to null them.
- **Verification:** Regression test (WS-level and service-level): action on a message from another thread is rejected and creates no row; reply preview never crosses threads.

### 3.2 High

#### TH-02 — Removed/left members stay in the Socket.IO room and keep receiving messages
- **Severity:** High · **Category:** Security / Data Integrity · **Status:** Static
- **Location:** `routes/student/threads/membership.py` → `remove_member` (L116), `leave_thread` (L58); `crud.py` → `delete_thread` (L537); `services/websocket_threads.py` → `handle_join_thread_room` (membership checked once, ~L552-600).
- **Evidence:** Room membership is established only at `join_thread_room`. `grep` for `leave_room|close_room|disconnect` across `routes/` and `services/` finds no eviction on removal, leave or delete; `remove_member` only broadcasts `thread_member_removed` (and wraps the broadcast in `except: pass`).
- **Problem:** Authorization is evaluated at join time and never re-evaluated for fan-out.
- **Impact:** A removed (e.g., for abuse) or departed member continues to receive `new_thread_message`, edits, reactions, pins and AI messages — including attachments — for as long as the socket lives (hours on a mobile client). For deleted threads the room persists.
- **Reproduction:** A joins the room; creator removes A via REST; another member sends a message; A's socket still receives `new_thread_message`.
- **Recommended solution:** Smallest reliable option: on removal/leave/delete, emit a `thread_access_revoked` event to `user_<id>` and, server-side, evict the sids. With the Redis message queue, `socketio.server.disconnect(sid)` is supported cross-process but disconnects the whole socket; for single-instance, `leave_room(room, sid)` works. Practical pattern: track `user_id → sid set` in Redis (presence already exists), and publish a "revoke" message that each instance handles by calling `leave_room` for its local sids. Fallback (no eviction): fan out per-user rooms (`user_<id>`) instead of `thread_<id>`, re-checking membership per message (costs N emits per message; acceptable up to the 50-member cap).
- **Implementation considerations:** Needs a decision on cross-instance eviction mechanism (see §9 Q7). Client must handle `thread_access_revoked` (close the thread view). Pair with TH-04 so removed users are not locked out of legitimate rejoin.
- **Verification:** Integration test with Socket.IO test client: member joins room, is removed, a message is sent, assert no event received; same for leave and delete.

#### TH-03 — Post-linked thread is created without a creator `ThreadMember` row
- **Severity:** High · **Category:** Bug / Data Integrity · **Status:** Static (not executed — see §1 caveat)
- **Location:** `routes/student/posts/crud.py` → `create_post`, thread block L1153-1167 (inserts `Thread(...)`, never `ThreadMember`); compare `routes/student/threads/crud.py` → `create_thread`/`create_standalone_thread`, which add `ThreadMember(role="creator")`.
- **Evidence:** `require_moderator_or_creator`, message send, edit/delete and room join all call membership checks on the `ThreadMember` row (`services/thread_authorization.py`, `thread_message_service`). The Thread defaults `member_count=1`. The existing integration tests build a creator `ThreadMember` by hand in their fixtures (`_make_thread_with_creator`), so this path is not exercised end-to-end. The same block also omits `department`/`tags`, uses `requires_approval=False` (other paths default to `True`), and takes `max_members`/`thread_title` from the payload without type validation.
- **Problem:** The creator of a post's thread is not a member of it; counts say 1 member while the membership table has 0 rows.
- **Impact:** Creator likely cannot send messages, join the room, approve requests, close or manage the thread (a 403/404 on every membership-gated call); `member_count` is wrong from creation; the thread has `department=NULL` so TH-26 hides it from discovery.
- **Reproduction:** Create a post with `thread_enabled=true`; query `thread_members` for the new thread (expected 0 rows); attempt `POST /threads/<id>/messages` as the creator.
- **Recommended solution:** Add `ThreadMember(thread_id, student_id=current_user.id, role="creator")` in the same transaction; copy `department` from the creator's profile and normalize `tags`; reuse a single `_create_thread_row()` helper shared with the other creation paths (also fixes defaults drift). Backfill: insert missing creator rows for existing post-threads (`INSERT … SELECT creator_id … WHERE NOT EXISTS`) and recompute `member_count` from the table.
- **Implementation considerations:** Requires a one-off data fix; confirm product intent for `requires_approval` default (§9 Q2). If production data shows the frontend compensates with a second call, adjust accordingly — the feed does call `/threads/create` in some flows (TH-16), which could mask this in some user journeys.
- **Verification:** Integration test: `create_post(thread_enabled=True)` → creator has `ThreadMember(role="creator")`, can send a message, appears in `/my-threads`; migration check query returns 0 rows after backfill.

#### TH-04 — Membership lifecycle traps users (rejoin lockout; invite + join crash)
- **Severity:** High · **Category:** Bug / Data Integrity · **Status:** Verified (V1, V2)
- **Location:** `membership.py` → `request_join_thread` (L410-516; the `existing_request` branches ~L450-486), `leave_thread`, `remove_member`; `UniqueConstraint("thread_id","requester_id")` on `ThreadJoinRequest`.
- **Evidence:** V1: join → approve → leave → join again returns `409 "Your request was already approved"`. V2: invite → the invited user presses Join returns `400 "Failed to send join request"` with `sqlite3.IntegrityError: UNIQUE constraint failed: thread_join_requests…` in the log. The approved request row is never cleaned on leave/remove, and the `existing_request.status == "invited"` case matches none of the `if/elif` branches and falls through to an `INSERT`.
- **Problem:** The request table doubles as membership history and invite store, but state transitions on leave/remove/invite are not handled.
- **Impact:** Anyone who leaves (or is removed and later forgiven) cannot rejoin except via a new invite; invited users who choose "Join" get an opaque failure; `get_thread` reports `can_join=true` in both states (TH-26) so the UI invites a click that cannot succeed.
- **Reproduction:** As in V1/V2.
- **Recommended solution:** On leave/remove, delete the user's `ThreadJoinRequest` (or set a terminal `left`/`removed` status that `request_join_thread` treats as re-requestable; for removal keep a `removed` status plus optional cool-down if bans are wanted — product decision §9 Q3). In `request_join_thread`, handle `invited` explicitly: either auto-accept the invite or return the invite id. Replace the generic `except` by catching `IntegrityError` and returning `409`.
- **Implementation considerations:** Possible small migration if a new status value is introduced (column is a non-native enum/string; check `models.py` before choosing). Removing the row also drops the 24h-cooldown history for rejected requests — confirm acceptable.
- **Verification:** Tests V1/V2 turned into permanent regression tests; add leave→rejoin and remove→invite→accept.

#### TH-06 — @mention notifications reach non-members and leak private message text
- **Severity:** High · **Category:** Security / Data Integrity · **Status:** Verified (V9)
- **Location:** `services/thread_message_service.py` → `_parse_mentions` (~L190) and the mention block in `create_thread_message` (~L500-517); `messaging.py` → `detect_mentions_in_thread` (L47, member-only variant used on REST edit).
- **Evidence:** V9: a member writes `secret exam answers @<outsider> here`; the outsider (not a member) receives `Notification(type="thread_mention", body="secret exam answers @user16 here...")`. `_parse_mentions` resolves any user; up to 20 per message. The `Notification` row is constructed directly instead of through `notification_service.notify()`, so the Redis unread counter (`sh:1:notif:unread:<id>`) is not incremented (the stated reason `notify()` exists). Two parsers with different regexes/semantics exist (REST edit vs service).
- **Problem:** No membership filter on mention targets; message text embedded in notifications; bypass of the notification funnel.
- **Impact:** Private thread content (first 80 chars) is disclosed to arbitrary users; any member can spam-notify arbitrary users (20 per message, 30 messages/min); unread badge for these notifications is inconsistent.
- **Reproduction:** As V9.
- **Recommended solution:** Restrict mention targets to `ThreadMember` rows of the thread (one `IN` query instead of N+1); use `notify()` with a neutral body ("X mentioned you in <thread title>") — no message excerpt for non-public threads; unify the two parsers into the service.
- **Implementation considerations:** Behavior change: mentions of non-members stop notifying (intended). If product wants "invite by mention", route it through the invite flow instead.
- **Verification:** Service-level tests: non-member mention creates no notification; member mention increments the Redis counter once; body contains no message text.

#### TH-07 — Unlimited, unbounded AI triggering over WebSocket; failures leave clients stuck
- **Severity:** High · **Category:** Reliability / Security (cost abuse) · **Status:** Static
- **Location:** `websocket_threads.py` → `handle_request_ai_response` (~L1933, no limiter), `handle_send` AI trigger paths (~L940-982), `_learnora_executor = ThreadPoolExecutor(8)` (L192), `_call_learnora_for_thread` (L2069), `_auto_reply_buckets` (L176).
- **Evidence:** `_is_rate_limited` (30 msg/min) guards `send_thread_message`; `_is_ai_action_rate_limited` guards `thread_ai_action`; **`request_ai_response` calls neither** and accepts a `question` of unbounded length. `executor.submit` has no queue bound or per-user/per-thread cap. `learnora_thinking` is broadcast *before* the job runs, and `_call_learnora_for_thread` returns silently (no `learnora_error`/`learnora_done`) on missing bot id, no provider, empty output, commit error or exception.
- **Problem:** The costliest operation (LLM call) has the weakest controls, and the UI state depends on a terminal event that is never guaranteed.
- **Impact:** One member can enqueue unlimited LLM calls (cost + provider quota exhaustion for the whole app); the 8 workers each hold a DB session; a provider outage leaves every thread participant seeing "Learnora is thinking…" indefinitely; auto-reply throttling is per process, so it multiplies with workers.
- **Reproduction:** Emit `request_ai_response` in a loop; observe executor queue growth and no server-side limit. Disable the provider; observe `learnora_thinking` with no completion event.
- **Recommended solution:** (1) Apply a Redis-backed limiter (reuse `_ai_action_rate_limiter`) to every AI entry point, including `request_ai_response` and the manual @mention path; (2) cap `question` length; (3) bound the executor (semaphore or `queue.Queue(maxsize)`), reject with `thread_error` when saturated; (4) always emit a terminal event (`learnora_done` / `learnora_error`) in a `finally`; (5) move `_auto_reply_buckets` to Redis (`INCR` + `EXPIRE`) when running >1 process.
- **Implementation considerations:** No new infrastructure (Redis already used). A durable queue (RQ is already a dependency) is the longer-term option; do not add it until usage warrants.
- **Verification:** Unit tests for limiter on each entry; test that a raising provider yields exactly one terminal event; load test that the queue rejects beyond the bound.

#### TH-08 — Missed messages are never recovered (no reconnect catch-up, broken `after_id`, no ack timeout)
- **Severity:** High · **Category:** Reliability / End-to-end consistency · **Status:** Verified (V6) + Static (frontend)
- **Location:** `messaging.py` → `get_thread_messages` (L114-285, `after_id` branch); `static/js/thread/thread.init.js` (reconnect handler ~L205-262); `thread.api.js` (`fetchMessages({afterId})` ~L223, never called); `thread.websocket.js` (`sendMessage`); `static/js/core/socket.js` (`reconnectionAttempts: 10`, `reconnectionDelay: 3000`).
- **Evidence:** V6: 6 messages, `after_id=<first id>&limit=2` returns ids `[5, 6]` with `has_more=true` — the **newest** page; messages 2-4 are unreachable via forward paging. Reconnect handler reloads the thread list and re-emits `join_thread_room`; `afterId` appears nowhere else in `static/js`. `sendMessage` has no ack timeout; a pending bubble stays pending if the socket drops after emit. After ~10 attempts the client stops reconnecting until page reload.
- **Problem:** Delivery relies on live fan-out only; the catch-up API is both unused and incorrect.
- **Impact:** Any network blip, mobile app suspend, deploy or instance restart silently drops messages from the user's view (they appear only after a full reload) and may leave a sent message stuck "sending" (and later duplicated on manual retry, TH-12).
- **Reproduction:** Disconnect a client, send 3 messages from another user, reconnect; the active thread shows none of them.
- **Recommended solution:** (a) Fix `after_id` to `ORDER BY id ASC LIMIT n` (return oldest-after cursor, keep `has_more`); (b) on reconnect and on `visibilitychange`, call `fetchMessages({afterId: lastSeenServerId})` for the active thread and loop while `has_more`; (c) add an ack timeout (e.g., 10 s) that marks the bubble failed with a retry action reusing the same `client_temp_id`; (d) make reconnection effectively unlimited with capped backoff.
- **Implementation considerations:** The REST `after_id` change is backward-compatible (no other caller). Retry only becomes safe once TH-12 lands.
- **Verification:** Route test for `after_id` pagination order; frontend test (or manual script) simulating disconnect; assert no gaps and no duplicate bubbles.

#### TH-13 — Realtime stack is not safe beyond one worker/instance; production entrypoint is a dev server
- **Severity:** High · **Category:** Scalability / Reliability · **Status:** Static; multi-worker runtime behaviour **Unverified risk**
- **Location:** `Procfile` (`web: python start-all.py`), `start-all.py` L178 and `app.py` L655 (`socketio.run(..., allow_unsafe_werkzeug=True)`), `services/websocket_messages.py` (`async_mode="threading"`, `cors_allowed_origins="*"`, `socket_to_user` dict, Redis MQ only if `REDIS_URL`), `static/js/core/socket.js` (default transports), `ARCHITECTURE.md` (§3, L79 and L746) which names `app.py` under Gunicorn (`-w N`) plus standalone `worker.py` as the production topology and calls `-w N > 1` safe.
- **Evidence:** See file/line list. `_get_current_user` in `websocket_threads.py` (~L410) resolves the user from the process-local `socket_to_user`. The Engine.IO session for polling transport lives in the process that created it. `Procfile` (`web: python start-all.py`) starts the Werkzeug development server with `allow_unsafe_werkzeug=True` and runs web, RQ worker and scheduler in one process — which contradicts the documented production topology (Gunicorn + separate `worker.py`). The ARCHITECTURE.md safety statement for `-w N` is about the scheduler/presence bugs it fixed; it does not address Socket.IO session stickiness or the process-local state listed here. Requirements include `eventlet`/`gunicorn[eventlet]` while `async_mode="threading"`.
- **Problem:** Cross-instance fan-out is correctly delegated to Redis (`message_queue`) **only if `REDIS_URL` is set**, but sid→user state, Engine.IO polling sessions, the typing manager, auto-reply buckets and the AI executor are process-local. Flask-SocketIO's documented constraint is that polling-first Socket.IO needs sticky routing; gunicorn with `-w N>1` cannot provide it.
- **Impact:** With >1 gunicorn worker or >1 instance behind a non-sticky LB: random "invalid session"/disconnect loops on polling, `thread_error: not authenticated` on events routed to a process without the sid mapping; each extra process multiplies AI throttles and executors. Today (single process) these do not occur, but the feature cannot be scaled out without changes, contrary to the documentation.
- **Reproduction (not run):** `gunicorn -w 2` plus a polling Socket.IO client; or two instances behind a round-robin LB.
- **Recommended solution (simplest reliable):** (1) Use the already-supported separated topology in production (Gunicorn for `app.py` + standalone `worker.py`) instead of the `Procfile`'s `start-all.py`; for Socket.IO either run a single Gunicorn worker with threads per container (scale by containers) or put sticky sessions at the LB; (2) force `transports: ["websocket"]` on the client so no stickiness is required for the session; (3) make `REDIS_URL` mandatory when `FLASK_ENV=production`; (4) move `socket_to_user` authority to the connect-time `request.sid` → JWT claims stored in the Socket.IO session (`flask_socketio` session) rather than a dict; (5) rate-limit buckets/typing dedupe/auto-reply in Redis. Do **not** introduce a broker or new service for threads yet.
- **Implementation considerations:** WebSocket-only transport drops clients behind hostile proxies (rare on campus networks, but note); sticky sessions are an LB/hosting capability (confirm provider — §9 Q8). Gunicorn worker class must match `async_mode`.
- **Verification:** Run a 2-worker/2-instance smoke test with the Socket.IO client for connect/join/send/receive across instances; kill an instance mid-conversation and confirm TH-08's catch-up recovers.

### 3.3 Medium

#### TH-05 — `requires_approval` is stored, displayed and editable but never enforced
- **Severity:** Medium · **Category:** Bug / Product · **Status:** Verified (V3)
- **Location:** `membership.py` → `request_join_thread` (L410-516); `crud.py` → `thread_settings` PATCH (~L787); creation paths (defaults differ: `True` in create-standalone, `False` in `create_post`).
- **Evidence:** `grep requires_approval` shows no read in the join path. V3: thread with `requires_approval=False`; join returns `201 "Join request sent"` and creates a pending request, no `ThreadMember`.
- **Problem:** The "open to join" setting has no effect.
- **Impact:** Creators who disable approval still have to approve every request; the setting is misleading; `get_thread.can_join` implies a different flow.
- **Reproduction:** V3.
- **Recommended solution:** Decide semantics (§9 Q2). If "open join" is intended: when `requires_approval=False` and capacity allows, insert `ThreadMember` directly under the capacity-safe update from TH-14 and return `200 joined`. Otherwise remove the toggle from settings/UI and the column from the PATCH contract. Unify the creation default.
- **Implementation considerations:** Changes join behavior for existing threads created with `False` (post-threads default to `False` today and would suddenly become open) — confirm before enabling.
- **Verification:** Route tests for both settings values, including capacity-full and concurrent-join cases.

#### TH-09 — Messages are HTML-escaped at write time and escaped again at render; empty messages are possible
- **Severity:** Medium · **Category:** Bug · **Status:** Verified (V8) + Static (frontend)
- **Location:** `thread_message_service.py` → `create_thread_message` (`bleach.clean(text, tags=[], strip=True)` ~L428; length check ~L409), `edit_thread_message`; `static/js/thread/thread.templates.js` → `esc()` (L22) via `renderMessageText`/`_escapeAndBreak`/`_renderCodeBlock`.
- **Evidence:** V8: input `Tom & Jerry <3 a<b` is stored as `Tom &amp; Jerry &lt;3 a&lt;b`; the client escapes `&` again, so users see literal `&amp;`/`&lt;`. Input `<b></b>` passes validation (non-empty) and is stored as `''`. The 5000-char limit is checked before sanitizing, which can lengthen the text. `ilike` search runs on the escaped text, so searching `&` or `<` finds nothing.
- **Problem:** Output encoding is applied to storage; sanitized value is not re-validated.
- **Impact:** Corrupted display of `&`, `<`, `>` — especially damaging in code snippets (` ```a < b && c``` ` renders as `a &lt; b &amp;&amp; c`) on a study platform; blank message bubbles; search misses; reply previews may cut an entity in half.
- **Recommended solution:** Store the user's text as-is (strip control characters, normalize newlines), enforce length and non-empty **after** normalization, and rely on output escaping (already done by the client; AI messages and notifications must also be escaped where rendered). Keep `bleach` only if some consumer renders stored text as HTML — none was found.
- **Implementation considerations:** Existing rows contain entities; decide on a one-time `html.unescape` data migration (§9 Q9). Notification bodies and reply previews built from stored text need the same treatment.
- **Verification:** Service tests for `&`, `<`, backticks, empty-after-strip; frontend render test; search test with `&`.

#### TH-10 — Former members can edit their own old messages (and bypass the edit window)
- **Severity:** Medium · **Category:** Security / Authorization · **Status:** Verified (V7)
- **Location:** `thread_message_service.py` → `edit_thread_message` (L539-587; `if membership and not _is_moderator_or_creator(membership)` ~L572); REST `messaging.py` → `edit_thread_message` (L618); WS `handle_edit_thread_message`.
- **Evidence:** V7: member posts 3-day-old message, leaves, then `PATCH …/messages/<id>` returns `200` and the DB text changes. `delete_thread_message` correctly requires membership (V7: `403`), so edit and delete disagree. When `membership` is `None` the 15-minute window check is skipped entirely.
- **Problem:** Missing membership requirement on edit.
- **Impact:** A removed user can rewrite history in a group they were expelled from; the edit is broadcast live (and TH-02 means they still receive the room).
- **Reproduction:** V7.
- **Recommended solution:** Require an existing `ThreadMember` row for edit (same as delete); apply the window to non-moderators unconditionally. Combine edit/delete into `UPDATE … WHERE id=:id AND is_deleted=false` to close the edit-vs-delete race that can overwrite `"[deleted]"`.
- **Implementation considerations:** None beyond tests.
- **Verification:** Regression tests: ex-member edit → 403; edit after window → 403; edit vs delete race leaves `"[deleted]"` intact.

#### TH-11 — `POST /threads/<id>/details` exposes the member list to non-members; error text leaks
- **Severity:** Medium · **Category:** Security · **Status:** Verified (V5)
- **Location:** `crud.py` → `thread_details` (L314-418; `return error_response(str(e))` ~L411); compare `discovery.py` → `get_thread` (L731) which correctly limits `members` to members.
- **Evidence:** V5: a user with no relation to the thread receives `members_data` (ids, names, reputation level, class level, connection status) and creator info; `GET /threads/<id>` for the same user returns no members. The endpoint also uses a POST for a read, an IP-keyed limiter, and returns raw exception strings.
- **Problem:** Two endpoints with different authorization for the same data.
- **Impact:** Membership roster of any thread (including closed/private) is enumerable by any logged-in user; exception messages can disclose internals.
- **Reproduction:** V5.
- **Recommended solution:** Apply the `get_thread` visibility rules to `thread_details` (members only for members; or reduce to counts), return generic errors, and plan to retire the endpoint in favour of `GET /threads/<id>` plus a `?by_post=<id>` lookup (the frontend uses `type:"post"`).
- **Implementation considerations:** `feed.api.js` and `post-view.js` call this endpoint; update them together. Legacy `static/js/post/feed.js` calls it with GET (would 405) but is unreferenced.
- **Verification:** Route test: non-member gets no `members_data`; error response contains no exception text.

#### TH-12 — No idempotency for message sends (WS retry, REST retry, success-reported-as-failure)
- **Severity:** Medium · **Category:** Reliability / Data Integrity · **Status:** Static
- **Location:** `websocket_threads.py` → `handle_send` (~L669-900); `thread_message_service.create_thread_message` (commit ~L519, then `_build_message_payload`); `messaging.py` → `send_thread_message` (L478).
- **Evidence:** The client generates `client_temp_id`, but the server only echoes it; there is no column, unique key or cache keyed by it. In the service the row is committed *before* the payload is built (4+ queries); an exception there makes REST return `400 "Failed to send message"` although the message exists. The REST path has no idempotency key at all.
- **Problem:** At-least-once client behaviour (retry after missing ack, after TH-08's fix, or after a failed REST call) produces duplicates.
- **Impact:** Duplicate messages and inflated `message_count`/unread counts; users see "failed" for messages that were stored.
- **Reproduction:** Send, drop the ack, resend with the same `client_temp_id`; two rows exist. Or force an exception in `_build_message_payload` after commit.
- **Recommended solution:** Add nullable `client_msg_id` (String(36)) to `ThreadMessage` with a partial unique index on `(thread_id, sender_id, client_msg_id)`; on conflict return the existing message and ack it. Wrap payload building after commit so a failure returns the persisted message minimally rather than an error. Accept the id from REST too.
- **Implementation considerations:** Migration adds a column + index (cheap, nullable; old clients unaffected). Retention: ids are only needed for a short window, but keeping them is simpler.
- **Verification:** Duplicate-send test returns the same message id; fault-injection test for post-commit failure.

#### TH-14 — Capacity and limit checks are check-then-act; the row lock used for capacity is ineffective on two paths
- **Severity:** Medium · **Category:** Reliability / Concurrency · **Status:** Verified (V13, semantic) + Static
- **Location:** `membership.py` → `approve_join_request` (L522-650), `add_members_to_thread` (L990-1189); `crud.py` → `update_thread`/`thread_settings` (`max_members`), `create_standalone_thread` (3-per-week cap L212-218); `websocket_threads.py` → pin (`MAX_PINS` count ~L1474).
- **Evidence:** Both membership paths first call `Thread.query.get(thread_id)` (for the authorization check) and later `Thread.query.with_for_update().get(thread_id)`. V13 shows the second call returns the **same identity-mapped object with the old `member_count`** (9, DB value 10): the `SELECT … FOR UPDATE` executes (blocking) but the capacity check reads the stale attribute. `accept_thread_invite` is fine (first load is the locked one). Other limits are count-then-insert without a lock.
- **Problem:** Row lock + stale ORM state ≠ correct check.
- **Impact:** Two concurrent approvals/adds can exceed `max_members`; concurrent creates can exceed the weekly cap; concurrent pins can exceed 5; lowering `max_members` can race a join.
- **Reproduction:** Two moderators approve different pending requests at member_count = max−1 against PostgreSQL.
- **Recommended solution:** Replace check-then-act by a conditional atomic update: `UPDATE threads SET member_count = member_count + :n WHERE id=:id AND member_count + :n <= max_members` and check `rowcount`; insert the member rows only if it succeeded (same transaction). Alternatively `db.session.refresh(thread, with_for_update=True)` / `.populate_existing()`. For weekly cap and pin cap, use the same pattern (conditional insert) or accept the race with a documented tolerance.
- **Implementation considerations:** No migration; keep `member_count` reconciliation job. Add a DB `CHECK (member_count <= max_members)` only after cleaning existing rows.
- **Verification:** Concurrency test against PostgreSQL with N parallel approvals; unit test showing refreshed state.

#### TH-15 — IP-keyed rate limits on 15 read endpoints; `ProxyFix` lacks `x_for`
- **Severity:** Medium · **Category:** Reliability / Performance · **Status:** Unverified risk (code confirmed; impact depends on deployment)
- **Location:** `routes/student/threads/{crud,discovery,membership,messaging}.py` (15 decorators with `key_func=ip_key`, `PUBLIC_READ` = 300/min); `app.py` L105 `ProxyFix(app.wsgi_app, x_proto=1, x_host=1)`; `services/rate_limit_service.py` (its own docstring says per-user keys are required on `@token_required` routes because of campus NAT).
- **Problem:** Authenticated reads share a bucket per IP; without `x_for`, `remote_addr` is the proxy's address when behind a reverse proxy (as `Procfile`/hosting implies).
- **Impact:** All users behind the proxy (or a campus NAT) share 300 reads/min; the thread list, history and discovery calls are chatty, so legitimate users can receive 429s under moderate load.
- **Reproduction (not run):** Deploy behind a proxy, drive >300 thread-read requests/min from several accounts.
- **Recommended solution:** Switch these decorators to `user_or_ip_key`; set `x_for=<number of trusted proxies>` in `ProxyFix` for the real topology.
- **Implementation considerations:** Wrong `x_for` allows spoofed IPs; confirm hop count with the host (§9 Q8).
- **Verification:** Test that two users from one IP have independent buckets; staging check of `remote_addr`.

#### TH-16 — Creation endpoints disagree with each other and with the frontend contract
- **Severity:** Medium · **Category:** Bug / End-to-end consistency · **Status:** Verified (V4, V10) + Static (frontend)
- **Location:** `crud.py` → `create_thread` (L55-190), `create_standalone_thread` (L190-312); `feed.events.js` (~L1228-1390), `feed.api.js` (L165-200), `connection.api.js` (~L209).
- **Evidence:** V4: `POST /threads/create` with `post_id` creates a thread whose `post_id` is `NULL` (the parameter is read but never assigned) and no "one thread per post" check exists. The avatar is read from `resource`, but the feed sends an `avatar` **object** (`{url,type,filename}`), so it is silently dropped; the feed's `menber_ids` is a typo no one reads. The weekly cap (3) exists only in `create-standalone`, yet the feed and connections call `/threads/create` without `post_id` — effectively a second, uncapped creator. `member_ids` here are inserted without the connection requirement enforced by `add_members` (so strangers can be force-added). V10: duplicate IDs raise `UNIQUE` → `400 "Failed to create thread"`. `max_members` is not int-coerced (type errors become the generic failure); `tags` and `description` are not length/type validated; unbounded `member_ids` loops with one `User.query.get` each; `request.get_json()` returning `None` raises `AttributeError`.
- **Problem:** Three creation paths (`create_post`, `/create`, `/create-standalone`) with different validation, defaults and policies.
- **Impact:** Policy bypass (cap, connection requirement), lost avatars, orphaned uploads, opaque 400s, unlinked post-threads.
- **Reproduction:** V4/V10.
- **Recommended solution:** One `create_thread_row()` service used by all paths with a single schema-validated payload (types, lengths, dedupe `member_ids`, cap list length ≤ `max_members`); enforce cap and connection rule centrally; fix the frontend key (`resource` vs `avatar`) or accept both; either wire `post_id` properly (with a unique index on `Thread.post_id`) or delete the parameter; add partial unique index `post_id WHERE post_id IS NOT NULL`.
- **Implementation considerations:** Unique index on `post_id` requires checking existing duplicates first. Behavior change: member_ids from non-connections stop working on `/create` (§9 Q4).
- **Verification:** Tests V4/V10 as regressions; cap enforced on all three paths.

#### TH-17 — AI meeting notes: unbounded prompt, N+1, no cooldown, unvalidated output, sync call in request
- **Severity:** Medium · **Category:** Reliability / Performance · **Status:** Static
- **Location:** `routes/student/threads/ai.py` → `generate_meeting_notes` (L50-135), `get_meeting_notes` (L138-165); `ThreadMeetingNote`.
- **Evidence:** Up to 500 messages (each ≤5000 chars) are concatenated with no truncation (≤ ~2.5 MB prompt); `User.query.get` per message (up to 500 queries); the provider call is synchronous inside the HTTP request while a DB session is open; `json.loads(model_output)` is stored in `notes_json` without schema validation (any JSON type); any member can regenerate repeatedly (limit is 10/hour per user, so N members × 10/hour per thread); rows accumulate with no retention or delete endpoint; `get_meeting_notes` has no `try/except` and `int(limit)` raising `ValueError` (or a negative `limit`) returns 500; `message_range` accepts 10-500 while the model comment says 50|100|500.
- **Problem:** The most expensive synchronous operation in the feature has the loosest bounds.
- **Impact:** Provider errors/timeouts tie up a worker; cost amplification; garbage or oversized JSON reaches clients; 500s on bad input.
- **Reproduction:** Call with `message_range=500` on a long thread; call `GET …/meeting-notes?limit=abc`.
- **Recommended solution:** Batch-load senders (`IN`), truncate/summarize input to a token budget, validate the model JSON against a small schema (fallback to plain text), add a per-thread cooldown (Redis `SET NX EX`), cap stored notes per thread, validate `limit`. Consider moving generation to the existing RQ worker later; not required now.
- **Implementation considerations:** Cooldown semantics need a product decision (§9 Q10).
- **Verification:** Tests for schema failure handling, cooldown, bad `limit`; query-count assertion.

#### TH-18 — Three overlapping read-state mechanisms; per-user receipts are incomplete and write-only
- **Severity:** Medium · **Category:** Data Integrity / Performance · **Status:** Static
- **Location:** `websocket_threads.py` → `handle_mark_thread_read` (L1676-1824), `handle_message_delivered` (L1824-1930); `messaging.py` → `get_thread_messages` (marks `last_read_at`); `ThreadMember.last_read_at`, `ThreadMessage.status`, `ThreadMessageReadReceipt`.
- **Evidence:** `mark_thread_read` selects messages with `status != 'read'` and writes a receipt for those only; the first reader flips `status` to `'read'` for everyone, so later readers get **no** receipt (the code comment claims "all of them should get a read receipt"). `grep` shows `ThreadMessageReadReceipt` is referenced only by this write/dedupe path and the model — nothing reads it. REST history fetch updates `last_read_at` only (any page with ≥1 unread marks the whole thread read, even when more unread exist beyond the page). `initial_status='read'` is set when *any one* other member is viewing. `message_delivered` runs a SELECT + member check + commit **per message** (the client emits one per rendered message). The unread badge is derived from `last_read_at`, which `mark_thread_read` sets to `now()` at the end — messages arriving between the SELECT and the UPDATE are counted as read.
- **Problem:** No single source of truth; receipts table cannot represent what it appears to.
- **Impact:** Inconsistent ticks/badges, write amplification (up to members × messages rows), DB load from per-message delivered events.
- **Reproduction:** Three members; two read; inspect receipts (only first reader's row exists).
- **Recommended solution:** Pick `ThreadMember.last_read_message_id` (monotonic, one row per member) as the source of truth; derive unread counts and group "seen by" from it; drop `ThreadMessageReadReceipt` writes (classification in §5) and make `status` a derived/optional value (`delivered` per-message events can be dropped for groups). If per-message receipts are required for the UI, define it first (§9 Q5).
- **Implementation considerations:** Migration adds `last_read_message_id` (nullable, backfill from `last_read_at`); table removal is a separate later step after confirming nothing reads it.
- **Verification:** Tests for monotonic read pointer, badge counts, no per-message writes.

#### TH-19 — Attachments: client-supplied URLs, public storage URLs, orphans, no quotas
- **Severity:** Medium · **Category:** Security / Data Integrity · **Status:** Static
- **Location:** `thread_message_service.py` (attachment handling ~L473-484); `messaging.py` → `upload_thread_attachment` (L290-380); `thread.templates.js` (~L215-250: `escAttr(url)` placed in `<a href>`, `<img src>`, `<video src>`).
- **Evidence:** `attachment_url`, `_name`, `_type`, `_size` come from the client and are stored unvalidated (any host/scheme; `String(500)` overflow → DB error; non-dict items → `AttributeError`). The renderer escapes quotes but does not constrain the scheme or host. Uploads are returned as URLs and are only linked on send, so abandoned uploads remain; `delete_thread_message` replaces text but leaves the attachment rows and files reachable; Cloudinary/Supabase URLs are unauthenticated with a 64-bit random token as the only protection; video files are not content-validated; filename is stored raw; 25 MB × 30 uploads/min/user has no quota.
- **Problem:** Server trusts client metadata and storage has no lifecycle.
- **Impact:** Arbitrary external URLs rendered as images/links (IP-leak/tracking pixel, phishing, and — pending browser behavior — possible `javascript:` navigation via `<a href>`); private-group files accessible by URL after deletion or removal; storage growth.
- **Reproduction:** Send `attachments_data=[{"attachment_url":"https://attacker.example/p.png","attachment_type":"image/png"}]`.
- **Recommended solution:** Accept only attachment IDs/URLs previously issued by `upload_thread_attachment` (record uploads in a table keyed by user/thread, mark consumed on send); validate scheme/host allow-list; sanitize filename; clean up unconsumed uploads and deleted-message files in a scheduled job; per-user/thread quota; consider signed/expiring URLs for private threads (trade-off: cost/complexity).
- **Implementation considerations:** Needs a small `thread_uploads` table (or Redis set with TTL) — see §5; cleanup job via the existing scheduler.
- **Verification:** Tests: foreign URL rejected; unconsumed upload expires; delete removes file reference.

#### TH-20 — Notifications (and Redis counters) are emitted before the transaction commits
- **Severity:** Medium · **Category:** Data Integrity / Reliability · **Status:** Static
- **Location:** `membership.py` (`leave_thread`, `approve_join_request`, `invite_to_thread`, `add_members_to_thread`), `crud.py` (`create_thread`, `delete_thread`); `services/notification_service.py` → `notify()` (flush + atomic Redis `INCR` + best-effort broadcast, "does not commit").
- **Evidence:** Callers invoke `notify()` and commit afterwards; the Redis increment and the push are immediate. `delete_thread` also broadcasts `thread_deleted` before deleting/committing.
- **Problem:** Side effects that cannot be rolled back precede the commit.
- **Impact:** If the commit fails (e.g., the `UNIQUE` errors in TH-04/TH-16), users get "thread deleted"/"you were added" notifications and inflated unread counters for changes that did not happen.
- **Reproduction:** Force an `IntegrityError` after `notify()` (V10-style duplicate ids on create).
- **Recommended solution:** Collect notification intents and dispatch after a successful commit (or use SQLAlchemy `after_commit`); broadcast only after commit; keep a periodic unread-counter reconciliation.
- **Implementation considerations:** Small refactor per endpoint; no migration.
- **Verification:** Test that a failing commit emits no notification and leaves the counter unchanged.

#### TH-21 — WebSocket handlers fail silently and parse input before their `try`
- **Severity:** Medium · **Category:** Reliability / UX · **Status:** Static
- **Location:** `websocket_threads.py` → `handle_send` (parsing at ~L719 before `try` at ~L731), `join_thread_room`, `thread_ai_action`, edit/delete/pin/unpin/reaction handlers (generic `except Exception: rollback; log` at ~L1175, 1360, 1460, 1587, 1662); reaction emoji accepted as any string up to `String(10)`.
- **Problem:** `data.get(...).strip()` on `None`/non-dict payloads raises outside the guarded block; most error branches never emit to the client.
- **Impact:** Optimistic UI stays in "sending"/inconsistent states; failures are visible only in server logs; oversized/odd emoji strings fail at the DB with no feedback.
- **Reproduction:** Emit `send_thread_message` with `text_content: null`.
- **Recommended solution:** A single decorator for all thread handlers: validate payload shape/types, catch exceptions, emit `thread_error {op, client_temp_id, code}`, log with context. Validate emoji against an allow-list (or Unicode emoji regex).
- **Implementation considerations:** Frontend must handle the new error shape for pending bubbles.
- **Verification:** Handler tests for malformed payloads asserting an error event is emitted.

#### TH-22 — Editing re-notifies mentioned users every time; REST edit is not atomic; WS edit skips mentions
- **Severity:** Medium · **Category:** Bug / Product · **Status:** Static
- **Location:** `messaging.py` → `edit_thread_message` (L618-720; mention re-detection after the service commit ~L691-695); `websocket_threads.py` edit handler.
- **Evidence:** The service commits the edit; then the route deletes existing `Mention` rows, re-detects, calls `notify()` and commits again. Because old rows were just deleted, every mention is "new" again. If the second step fails, the edit is already saved but the API returns "Failed to edit message". The WS edit path does no mention handling.
- **Problem:** Behavior depends on transport; non-atomic two-commit flow.
- **Impact:** A user edited ten times in the 15-minute window gets ten notifications; a failed mention step reports failure for a successful edit; mentions added via WS edits never notify.
- **Reproduction:** Edit a message containing `@user` repeatedly via REST.
- **Recommended solution:** Move mention reconciliation into `edit_thread_message` (same transaction), diff old vs new mention sets, notify only for newly added members (see TH-06 for scoping).
- **Implementation considerations:** Combine with TH-06 parser unification.
- **Verification:** Edit twice → one notification; edit adding a mention → one new notification.

#### TH-23 — Pervasive N+1 queries and unbounded list endpoints
- **Severity:** Medium · **Category:** Performance / Scalability · **Status:** Static
- **Location:** `discovery.py` → `get_my_threads` (L558; ~5 queries per membership, plus an unused `msg_status` query), `get_open_threads` (L655; no pagination, `.all()`), `get_recommended_threads` (L230; `.limit(200)` without `ORDER BY`, loads all member rows for those threads), `get_help_suggestions` (L399; loads **all** approved users + onboarding + profile per request); `membership.py` (`get_thread_members`, `get_pending_requests`, `get_my_join_requests`, `get_my_invites`: `User.query.get` per row); `messaging.py` (`get_pinned_messages` 2 per pin, `search` per result, `_build_message_payload` 4+ per message); `ai.py` (500 queries, TH-17); `crud.py` (`thread_stats`, `create` loops).
- **Problem:** Row-by-row loading with no batching; some endpoints are O(all users/threads).
- **Impact:** Latency and connection-pool pressure grow linearly with membership/thread/user counts; the `my-threads` call (the app's main screen) is the worst case. Not a correctness bug today at small scale.
- **Reproduction:** Enable SQL echo and call `/my-threads` with 30 memberships (expected ~150 queries).
- **Recommended solution:** Batch-load users with `IN`; aggregate unread counts and last messages with one grouped query/window function per page; paginate `/open` and cap list endpoints; add `ORDER BY` + `LIMIT` in SQL for recommendations (or compute candidates by indexed filters); replace `/help/suggestions` full scan with a bounded candidate query.
- **Implementation considerations:** Response shapes stay the same; add pagination params with defaults for `/open` (frontend change if it relies on all rows — check `thread.api.js`).
- **Verification:** Query-count assertions (e.g., `assert_queries <= k`) in route tests.

#### TH-24 — `delete_thread` ordering, cascade cost and leftovers
- **Severity:** Medium · **Category:** Data Integrity / Performance · **Status:** Static
- **Location:** `crud.py` → `delete_thread` (L537-598).
- **Evidence:** `thread_deleted` is broadcast and notifications created before the delete commits; relationships are `lazy="dynamic"` with ORM `cascade="all, delete-orphan"`, so deleting a large thread loads message rows before deleting; Cloudinary/Supabase files, Redis presence/unread keys and sockets in the room are not cleaned (TH-02).
- **Problem:** Heavy, non-atomic delete with orphaned external resources.
- **Impact:** Slow/timeout deletes for large threads; orphaned files remain reachable; clients show a deleted thread if the commit fails after the broadcast.
- **Reproduction:** Delete a thread with tens of thousands of messages (not run).
- **Recommended solution:** Commit first, then notify/broadcast (TH-20); use DB-level `ondelete="CASCADE"` with `passive_deletes=True`; schedule file cleanup from collected attachment URLs; evict room members (TH-02). Consider soft-delete first (§9 Q6).
- **Implementation considerations:** Verify existing FKs have `ondelete` in migrations before enabling `passive_deletes`.
- **Verification:** Test that failed delete emits nothing; large-thread delete query count and time.

#### TH-31 — No server-side throttling on typing/reaction/read/delivered events
- **Severity:** Medium · **Category:** Performance / Abuse · **Status:** Static
- **Location:** `websocket_threads.py` → `handle_thread_typing` (L1003; `_is_member` + `User.query.get` per event + `cleanup_expired`), `handle_thread_typing_stop` (no membership check), reactions, `mark_thread_read`, `message_delivered`; limiter covers only `send_thread_message` and `thread_ai_action`.
- **Problem:** Cheap-looking events execute DB queries and broadcasts without any limit.
- **Impact:** A buggy or hostile client can generate large DB/Redis load; `remove_thread_reaction` has no membership check and broadcasts for any message ID.
- **Reproduction:** Emit `thread_typing` in a tight loop.
- **Recommended solution:** Generic Redis-backed per-user/event limiter (fail-closed for expensive events, fail-open for cosmetic ones); cache membership for the room in the socket session (revoked under TH-02); coalesce delivered events (send ids in batches); add the missing membership check on reaction removal.
- **Implementation considerations:** Fail-open vs fail-closed policy decision when Redis is down (§9 Q11); existing limiter fails open.
- **Verification:** Limiter tests per event; assert no DB hits on throttled typing events.

### 3.4 Low

#### TH-25 — Dead code, duplicated logic and a write-only persona field
- **Severity:** Low · **Category:** Maintainability / Product · **Status:** Static
- **Location:** `discovery.py` `get_my_threads` (`msg_status` computed ~L599-609, never used); `crud.py` avatar upload (`folder, filename = …` immediately overwritten); `feed.events.js` (`menber_ids` typo); `static/js/post/{feed.js,feed.html,newfeed.html,resp.js}` (~350 KB, unreferenced, call `GET …/details` which would 405); `_build_message_payload` duplicated between `thread_message_service.py` and `websocket_threads.py`; `ThreadMessage.ai_personality`.
- **Evidence:** `ai_personality` is written by the AI paths and **read by the frontend** (`thread.templates.js` L320, `thread.state.js` L126), but no REST serializer, WS payload or `broadcast_ai_message` returns it; the partial index `idx_tm_ai_personality` has no query that uses it.
- **Problem / Impact:** Persona labels never appear; extra query per `my-threads` row; drift risk between two payload builders.
- **Reproduction:** Inspect any message JSON for `ai_personality`.
- **Recommended solution:** Return `ai_personality` in the single shared payload builder (or drop the field and index if personas are cut); delete the dead computation; remove the legacy static files after confirming no template references them; remove the typo field.
- **Implementation considerations:** Index removal needs a migration; verify no other consumer first.
- **Verification:** Payload contract test for AI messages; build/lint check that legacy files are unreferenced.

#### TH-26 — Discovery and visibility inconsistencies
- **Severity:** Low · **Category:** Bug / Product · **Status:** Static
- **Location:** `discovery.py` (`get_popular` L127: `Thread.department != user_dept` excludes `NULL` departments; `limit*2` then `[:limit]` while `total_found=len`; `get_recommended` without `ORDER BY`; `get_open_threads` envelope `data=[…]` differs from the others; `get_thread` `can_join` ignores stale `approved`/`invited`/cooldown (TH-04); `get_department_stats` cache `sh:1:thread:dept_stats` TTL 1800 s never invalidated); `int(request.args.get('limit'))` without validation.
- **Impact:** Post-linked threads (no department, TH-03) never appear in popular/department views; stale counts for up to 30 min; UI shows Join where the server refuses; negative/non-numeric limits return generic errors.
- **Recommended solution:** `OR department IS NULL` handling (or make department mandatory at creation), deterministic ordering, accurate `total_found`, consistent envelopes, correct `can_join`, invalidate or drop the cache.
- **Verification:** Discovery route tests including NULL department and invalid limits.

#### TH-27 — Permission model gaps (moderators, ownership, blocks)
- **Severity:** Low · **Category:** Product / Security · **Status:** Static
- **Location:** `thread_authorization.py`; `membership.py` (`remove_member`, `get_pending_requests` L246, `request_join_thread` notifies only the creator, `leave_thread` message "Transfer ownership"); `crud.py` (`max_members` may be set to 1; `update_thread` vs settings validation differ).
- **Evidence:** A moderator can remove another moderator; moderators can approve/reject requests but cannot list them (creator-only) and are not notified; the creator cannot leave and there is **no transfer-ownership endpoint** in thread code (only the delete path); invite does not check `is_open`/target user status; no block/mute concept is consulted anywhere in the feature (`models.py` has no block model — confirm that is intended).
- **Impact:** Moderator abuse potential; creators stuck owning threads; moderators' tools are half-usable.
- **Recommended solution:** Decide the role hierarchy (§9 Q12), add `transfer ownership`, list/notify requests for moderators, validate status/open on invites, unify `max_members` bounds (≥ current members and ≥ 2).
- **Verification:** Authorization matrix tests per role.

#### TH-28 — Rejections and declined invites are silent
- **Severity:** Low · **Category:** Product · **Status:** Verified (V11)
- **Location:** `membership.py` → `reject_join_request` (L653), `decline_thread_invite` (L965).
- **Evidence:** V11: after rejection the requester has 0 notifications; `my-requests` lists only pending items so the request just disappears. Declining an invite sets `status="rejected"`, which starts the 24 h join cooldown without telling anyone.
- **Impact:** Users wait indefinitely or retry blindly.
- **Recommended solution:** Send a neutral notification on rejection (wording per §9 Q13) and show a "request declined" state; do not start the cooldown for a declined invite.
- **Verification:** Route tests asserting notification and state.

#### TH-29 — Input-validation class: unvalidated JSON shapes, IDs and limits
- **Severity:** Low · **Category:** Reliability · **Status:** Static
- **Location:** `crud.py` (`request.get_json()` returning `None`, `data["title"].strip()` on non-strings, `max_members` comparisons), `membership.py` (`data.get("message","").strip()` with `null`, `user_ids` duplicates/non-ints, role `None`), `messaging.py` (`limit<=0`), WS handlers (TH-21), `discovery.py`/`ai.py` (`int()` conversions).
- **Impact:** Most surface as the generic `"Failed to …"` 400/500 and hide the cause; some insert duplicates that hit `UNIQUE` (TH-16, `add_members`).
- **Recommended solution:** One request-schema layer (e.g., pydantic/marshmallow or a small validator helper) for thread payloads; return `422` with field errors.
- **Verification:** Parametrized tests of malformed bodies per endpoint.

#### TH-30 — Observability gaps and information leakage in logs/errors
- **Severity:** Low · **Category:** Reliability / Security · **Status:** Static
- **Location:** broad `except Exception` → `"Failed to …"`; `thread_details` returns `str(e)`; `_summarize_payload` includes the first 40 chars of `token` in the join-auth-failed warning (the client sends `token` in every WS payload, though the server ignores it); no metrics for executor queue depth, AI latency/failure, WS auth failures.
- **Impact:** Slow diagnosis; partial JWT material in logs.
- **Recommended solution:** Stop sending/logging `token`; structured error codes; counters for AI jobs (queued/failed), socket auth failures, rate-limit rejections.
- **Verification:** Log assertion test; dashboards/alerts after instrumentation.

#### TH-32 — Socket authentication is evaluated once; wildcard CORS
- **Severity:** Low · **Category:** Security · **Status:** Unverified risk
- **Location:** `services/websocket_messages.py` (connect handler, `cors_allowed_origins="*"`); `thread_message_service` (no `user.status` check).
- **Evidence:** The JWT is validated at connect; later events trust `socket_to_user`. Suspended/deactivated users or expired tokens keep an authenticated socket until disconnect. Whether wildcard origins enables cross-site WebSocket hijacking depends on how the access-token cookie is configured (`ACCESS_TOKEN_HTTPONLY`, `SameSite`), which was not exercised.
- **Impact:** Delayed enforcement of account suspension; possible CSWSH if the cookie is sent cross-site.
- **Recommended solution:** Restrict `cors_allowed_origins` to the app's origins; re-check user status/token expiry on a timer or on sensitive events (send/AI); close sockets on suspension.
- **Verification:** Test with a disallowed `Origin`; test that a suspended user's next event is rejected.

---

## 4. Horizontal Scalability Assessment

### 4.1 Current architecture and assumptions

The `Procfile` entry is `start-all.py`: **one process** hosting the Flask app (Werkzeug dev server with `allow_unsafe_werkzeug=True`), the RQ worker and the scheduler. Socket.IO runs with `async_mode="threading"`; cross-process emits go through a Redis `message_queue` **only if `REDIS_URL` is set**. Redis is also used for rate limits, presence, caches and notification counters. `ARCHITECTURE.md` documents a different production topology (Gunicorn `-w N` + standalone `worker.py`) and treats `-w N > 1` as supported; `start-all.py` is described as the single-small-deployment fallback, yet it is what the `Procfile` runs.

### 4.2 Instance-local state and coordination

| State | Where | Multi-instance behavior | Today or future? |
|---|---|---|---|
| sid → user map (`socket_to_user`) | `websocket_messages.py` | Events routed to another process find no user; requires sticky routing or WS-only transport | Future (single process today) — TH-13 |
| Engine.IO polling sessions | python-engineio | Must stay on one process; polling-first client | Future — TH-13 |
| AI executor (`ThreadPoolExecutor(8)`) + queue | `websocket_threads.py` | Per-process, unbounded; capacity multiplies with processes; jobs lost on restart | **Current** (cost/DoS, TH-07) |
| Auto-reply buckets | `_auto_reply_buckets` | Per-process counters → limits multiply | Future — TH-07 |
| Typing manager | in-process | Dedupe only; acceptable | Low |
| Active-thread presence | Redis `set_active_thread(user_id, thread_id)` | One active thread per *user* (multi-tab/device last-writer-wins) → incorrect ticks/unread | **Current** (minor) |
| Thread rate limiter | Redis fixed window | Shared, but **fails open** on Redis errors | Current/Future |
| Room membership | Socket.IO rooms (+Redis MQ) | Shared if MQ configured; never evicted (TH-02) | **Current** |
| Department stats cache | Redis | Shared; never invalidated | Low |

### 4.3 Concurrency and race-condition risks

- Capacity/limit checks (TH-14), weekly creation cap, pin cap, `max_members` edits.
- Duplicate join/invite/approve under concurrency raise `IntegrityError` that surface as generic 400s (TH-04, TH-16).
- Message send has no idempotency (TH-12); `message_count` and `member_count` use atomic SQL expressions (good), with reconciliation jobs expected to correct drift.
- Edit vs delete on one message can overwrite `"[deleted]"` text (TH-10 fix covers it).

### 4.4 Background jobs and event processing

AI work runs in-process threads, not in RQ; a restart drops queued jobs and leaves `learnora_thinking` unresolved (TH-07). Broadcasts are emitted after commit with no outbox, so a crash between commit and emit loses the live event; recovery depends on catch-up that does not currently work (TH-08).

### 4.5 Database and Redis concerns

- Connections held across network calls: attachment/avatar uploads (Cloudinary) and the AI call in `generate_meeting_notes` run after a query has begun a transaction.
- N+1 patterns (TH-23) multiply connection time per request under load.
- Redis: limiter and counters are shared (good); no key expiry issues were found in thread keys, but fail-open limiter plus unbounded AI path (TH-07) is the weak spot; `message_queue` is silently optional.
- Indexes: the models define thread/member/unread indexes; one unused partial index (`idx_tm_ai_personality`, TH-25). Query plans were not measured (no PostgreSQL run).

### 4.6 Recommended changes — separated

**Immediate (affect correctness or cost today, no new infrastructure):** TH-01, TH-02, TH-07 (limiter + bounded executor + terminal event), TH-08 (catch-up + `after_id`), TH-14 (atomic conditional updates), TH-12 (idempotency key), TH-15 (user keys).

**Before running more than one process/instance:** TH-13 — separate web from worker/scheduler, real WSGI server with a worker class matching `async_mode`, WebSocket-only transport or sticky sessions, mandatory `REDIS_URL` in production, Redis-backed buckets, user/session data via Socket.IO session instead of a dict, instance-restart behavior validated against TH-08.

**Later / only if usage demands:** move AI generation to RQ; per-user rooms instead of per-thread rooms (only if revocation requires it); signed URLs for private attachments; read-pointer table partitioning (not expected to be needed).

---

## 5. Data Usage and Cleanup Report

Classification key: **1** Confirmed unused · **2** Potentially unused — requires verification · **3** Required indirectly — retain · **4** Redundant — can potentially be consolidated.

| Resource | Current Usage | Evidence | Classification | Recommendation |
|---|---|---|---|---|
| `thread_message_read_receipts` (`ThreadMessageReadReceipt`) | Written in `handle_mark_thread_read`; read only for dedupe in the same function | `grep`: only `websocket_threads.py` (write/dedupe) and `models.py`; incomplete for groups (only first reader, TH-18) | **2** Write-only; potentially unused | Confirm no analytics/export uses it, then stop writes and drop table (migration + optional archive). Depends on §9 Q5. |
| `ThreadMessage.status` (`sent/delivered/read`) | Set on send; flipped by delivered/read events; shown in `my-threads` last message | Read in `discovery.get_my_threads`, frontend ticks | **4** Redundant with read pointer | Keep until read-pointer lands; then derive. |
| `ThreadMember.last_read_at` | Unread badge, REST history fetch | `get_my_threads`, `get_thread_messages`, `mark_thread_read` | **3** Required (until replaced by `last_read_message_id`) | Retain; migrate per TH-18. |
| `ThreadMessage.ai_personality` + index `idx_tm_ai_personality` | Written by AI paths; frontend reads it | No serializer returns it; no query uses the index | **2** Potentially unused (column) / **1** unused (index) | Return it in payloads (preferred) or drop column+index (migration). |
| `ThreadMessage.attachment_url/_name/_type/_size` (legacy single-attachment columns) | Still written/read as fallback beside `ThreadMessageAttachment` | Serializer has legacy fallback path (`messaging.py`) | **4** Redundant | Consolidate onto `ThreadMessageAttachment`; backfill legacy rows, then drop columns. |
| `ThreadJoinRequest` rows with `status="invited"` + `[INVITE]` message prefix | Invite store | `invite_to_thread`, `accept/decline_thread_invite` | **4** Overloaded table | Keep for now; consider a dedicated `thread_invites` table only if invite semantics grow. Do not migrate during bug fixes. |
| `ThreadJoinRequest.reviewed_by` (also holds inviter) | Dual meaning | `membership.py` | **3** Required; semantics unclear | Document; split only with the invites table above. |
| `ThreadMeetingNote` rows | One per generation; no retention/delete | `ai.py` | **3** Required | Add retention cap/cleanup (TH-17). |
| `Thread.post_id` | Set only by `create_post`; read by `details type=post` | `/threads/create` never sets it (TH-16) | **3** Required | Keep; add partial unique index. |
| `Thread.department`, `Thread.tags`, `Thread.avatar` | Discovery/recommendation scoring, UI | Unset for post-threads (TH-03); avatar dropped from feed payload (TH-16) | **3** Required | Fix population, do not remove. |
| `Thread.requires_approval` | Stored/displayed/PATCHable; **not enforced** | `grep` shows no read in join path (TH-05) | **2** Potentially unused (behavior) | Enforce or remove per §9 Q2. |
| `Mention` table (shared with posts/comments) | Thread rows written by `thread_message_service.create_thread_message` and the REST edit path; in thread code it is only read for de-duplication/deletion (`messaging.py` L69, L691) | `grep Mention` — post/comment routes read it for their own mentions | **3** Required (shared); thread-side readers **2** unverified | Keep the table; verify whether any UI consumes thread mentions; unify the two writers (TH-06/TH-22). |
| API field `members_data` in `thread_details` | Returned to anyone | TH-11 | **3** (frontend uses it) | Restrict visibility; keep field. |
| Frontend `avatar` object in feed create-thread payload | Sent, ignored | TH-16 | **1** Unused as sent | Fix key or remove. |
| Frontend `menber_ids` (feed create-post payload) | Sent, ignored | typo; backend reads none | **1** Confirmed unused | Remove. |
| Frontend `token` in every WS payload | Sent; server ignores for thread events | `websocket_threads.py` uses `socket_to_user` | **1** Unused (and logged) | Stop sending (TH-30). |
| Frontend `afterId` option (`thread.api.js`) | Defined, never called | `grep afterId static/js` | **3** Needed once TH-08 lands | Use it in reconnect catch-up. |
| `static/js/post/{feed.js,feed.html,newfeed.html,resp.js}` | Legacy | No template/script references (only a comment) | **2** Potentially unused (outside this feature's runtime) | Confirm with the product owner, then delete. |
| `msg_status` variable in `get_my_threads` | Computed, discarded | Code read | **1** Confirmed unused | Remove (saves a query). |
| `/threads/open` endpoint | Registered; consumer not found in `static/js` | `grep` of frontend | **2** Potentially unused | Verify mobile/other clients; paginate or remove. |
| `/help/suggestions` endpoint | Heavy, in threads package | Frontend consumer exists (`thread` UI) per grep; matches users not threads | **3** Required (verify) | Bound the query (TH-23). |
| `idx_tm_status`, thread/unread indexes | Support `mark_thread_read` / unread counts | Present in models | **3** Required | Keep; re-check after read-pointer change. |

**Dependencies for removals:** table/column drops need migrations and a data-preservation decision. `ThreadMessageReadReceipt` has no readers, so no data-preservation is required unless analytics are planned (ask, §9 Q5). Legacy attachment columns require a backfill first. Index drops are safe once no query planner dependency is confirmed in PostgreSQL (`pg_stat_user_indexes`).

---

## 6. End-to-End Data Consistency Report

| # | Data path | Mismatch | Concrete example | Correction |
|---|---|---|---|---|
| 1 | Post creation → thread | Creator row missing; department/tags missing; default differs | `create_post(thread_enabled)` → `member_count=1`, 0 member rows (TH-03) | Single creation service; backfill |
| 2 | Feed "create thread" modal → `/threads/create` | Key `avatar` (object) vs backend `resource` (string); `menber_ids` typo; no `post_id` | Avatar uploaded then silently dropped (V4) | Align contract; one schema |
| 3 | Message text → DB → UI | Encoded on write, encoded again on render | `&` shows as `&amp;` (V8) | Store raw; escape on output |
| 4 | Send → ack → retry | No idempotency; success-reported-as-failure | Duplicate message after retry (TH-12) | `client_msg_id` + unique index |
| 5 | Live delivery → catch-up | No refetch on reconnect; `after_id` returns newest page | 2-4 unreachable (V6) | Fix cursor; wire client |
| 6 | Join workflow → UI | `can_join=true` while server returns 409/400 | Left user sees Join; gets 409 (V1) | Compute `can_join` from true state; fix transitions |
| 7 | `requires_approval` UI → server | Setting ignored | Open thread still pending (V3) | Enforce or remove |
| 8 | AI message payload → UI | `ai_personality` expected, never sent; WS AI payload lacks `attachments`/`reply_to_id` | Persona label absent | One shared serializer |
| 9 | Timestamps | `sent_at` has `Z` in most payloads but not in REST send / `my-threads` | Naive ISO parsed as local time by `Date` | Always emit UTC with `Z` |
| 10 | Read state | `last_read_at` vs `status` vs receipts | Group ticks flip on first reader; badges differ (TH-18) | Single read pointer |
| 11 | Member removal → live view | REST success, sockets still subscribed | Removed user still receives messages (TH-02) | Evict + client event |
| 12 | Edit via REST vs WS | Mentions handled only on REST; re-notify on each edit | TH-22 | Do it in the service |
| 13 | Leave/remove → request history | Approved request survives | Rejoin blocked (V1) | Clean up/terminal status |
| 14 | Notification before commit | Counter incremented, change not persisted | TH-20 | Dispatch after commit |
| 15 | `thread_details` vs `get_thread` | Different authorization for same data | V5 | Share one visibility function |

---

## 7. Testing Gaps

### 7.1 Existing tests (observed)

210 tests, all passing in the scratch copy: `test_threads_crud_routes.py` (23), `…_crud_settings_routes.py` (12), `…_discovery_routes.py` (16), `…_membership_routes.py` (28), `…_messaging_edit_delete_upload_routes.py` (15), `…_messaging_read_send_routes.py` (16), `…_recommendations_routes.py` (9), `test_posts_threads_ai_routes.py` (17), `tests/unit/services/test_thread_authorization.py` (9), `…test_thread_message_service.py` (25), `…test_websocket_threads_manager.py` (40). No concurrency tests, no Socket.IO integration tests, no frontend tests and no e2e directory. Fixtures create the creator `ThreadMember` manually (masks TH-03). Tests run on SQLite, which cannot exercise row locks.

### 7.2 Verification log — scratch tests used for this audit

Executed in a copy (`tests/integration/test_audit_verification.py`, not committed; the audited clone is unmodified). All 13 ran against the real Flask app + SQLite + fakeredis.

| ID | Behaviour verified | Result | Finding |
|---|---|---|---|
| V1 | join → approve → leave → join | `409 already approved` | TH-04 |
| V2 | invite → invited user joins | `400`, `UNIQUE constraint failed: thread_join_requests` | TH-04 |
| V3 | `requires_approval=False` join | `201`, pending request, no member | TH-05 |
| V4 | `/threads/create` with `post_id`, `avatar`/`resource` | `post_id=None`; avatar from `resource` only | TH-16 |
| V5 | `thread_details` as unrelated user | `members_data` returned | TH-11 |
| V6 | `after_id=1&limit=2` over 6 messages | returned `[5,6]`, `has_more=true` | TH-08 |
| V7 | ex-member edit/delete of 3-day-old message | edit `200` (text changed), delete `403` | TH-10 |
| V8 | text `Tom & Jerry <3 a<b`, `<b></b>` | stored `Tom &amp; Jerry &lt;3 a&lt;b`; `''` stored | TH-09 |
| V9 | mention of non-member | `thread_mention` notification with 80-char excerpt | TH-06 |
| V10 | duplicate `member_ids` on create | `400`, `UNIQUE thread_members` | TH-16/TH-20 |
| V11 | reject join request | requester notifications = 0 | TH-28 |
| V12 | AI action on message from another thread (provider stubbed) | prompt contained other thread's text; AI message created with cross-thread `reply_to_id` | TH-01 |
| V13 | `Thread.query.get` then `with_for_update().get` after concurrent update | `member_count` stale (9 vs 10), same object | TH-14 |

Not executed: any WebSocket handler, PostgreSQL locking, multi-process Socket.IO, frontend rendering, `create_post` (TH-03; first test below).

### 7.3 Recommended tests

| # | Test | Behaviour to verify | Level |
|---|---|---|---|
| T1 | V-TH03 | `create_post(thread_enabled)` creates creator `ThreadMember`; creator can send, appears in `/my-threads`; department/tags set | Integration |
| T2 | Cross-thread AI | `thread_ai_action` with foreign `message_id` rejected, no row; reply preview never crosses threads | Integration (WS client) + unit |
| T3 | Room eviction | Removed/left/deleted → no further events received | Integration (Socket.IO test client) |
| T4 | Rejoin/invite matrix | leave→rejoin, remove→invite→accept, invited→join, concurrent duplicate join returns 409 | Integration |
| T5 | Mention scoping | Non-member mention: no notification; counter via `notify()`; body contains no message text | Unit + integration |
| T6 | AI limiter coverage | Each AI entry point rate-limited; bounded executor rejects; terminal event on failure/provider down | Unit/integration |
| T7 | Catch-up | `after_id` ascending pagination; reconnect refetch; ack timeout/retry with same `client_temp_id` | Route + frontend/e2e |
| T8 | Idempotency | Same `client_msg_id` twice → one row; post-commit failure returns persisted message | Service/integration |
| T9 | Capacity concurrency | N parallel approvals/adds at max−1 → exactly one succeeds (PostgreSQL) | Concurrency (real DB) |
| T10 | Text handling | `&`, `<`, backticks, empty-after-strip, 5000-char boundary, search for `&` | Service + frontend |
| T11 | Edit authorization | Ex-member edit rejected; window enforced; edit-vs-delete race | Service |
| T12 | Visibility parity | `thread_details` vs `get_thread` expose the same data | Route |
| T13 | Creation parity | Cap, connection rule, dedupe and payload keys identical across the three creation paths | Route |
| T14 | Multi-instance smoke | 2 workers/instances: connect/join/send/receive; instance restart recovered by catch-up | E2E/infra |
| T15 | Query budgets | `my-threads`, members, pinned, meeting notes assert query counts | Route |
| T16 | Attachment validation | Foreign URL rejected; unconsumed upload cleanup; deleted message hides file | Route + job |
| T17 | Notification ordering | Failed commit emits nothing | Route |
| T18 | Handler robustness | Malformed WS payloads produce `thread_error`, never silence | Unit (handler) |
| T19 | Read model | Monotonic read pointer, group "seen by", unread badge races | Unit/integration |
| T20 | Meeting notes | Bad `limit`, schema-invalid model output, cooldown, input truncation | Route |

Do not write or modify these tests as part of this audit.

---

## 8. Recommended Implementation Plan

Priority order follows the requested ranking (correctness/data loss → security → reliability/concurrency → scalability → product/E2E consistency → performance → cleanup).

### Milestone 1 — Close the confidentiality and authorization holes (Critical/Security)
- **Objective:** Stop cross-thread and post-removal data exposure.
- **Issues:** TH-01, TH-02, TH-06, TH-10, TH-11.
- **Files:** `services/websocket_threads.py`, `routes/student/threads/{messaging,membership,crud}.py`, `services/thread_message_service.py`, `static/js/thread/thread.websocket.js`.
- **Proposed implementation:** thread-scoped message lookups (AI + reply parents); room eviction + `thread_access_revoked`; member-only mention targets via `notify()`, no excerpts; require membership for edit; align `thread_details` visibility with `get_thread`.
- **DB/infra:** none (one optional data check for cross-thread `reply_to_id`).
- **Tests:** T2, T3, T5, T11, T12.
- **Dependencies:** §9 Q7 (eviction mechanism), Q1 (visibility model).
- **Risks:** Eviction across instances (use Redis presence + revoke message); clients must handle the new event.

### Milestone 2 — Fix broken core flows and data lifecycle (Critical correctness)
- **Objective:** Make creation and membership work end to end.
- **Issues:** TH-03, TH-04, TH-05, TH-16, TH-28, TH-27 (ownership transfer), TH-26 (`can_join`).
- **Files:** `routes/student/posts/crud.py`, `routes/student/threads/{crud,membership,discovery}.py`, `feed.events.js`, `feed.api.js`, `connection.api.js`.
- **Proposed implementation:** shared `create_thread_row()` service; creator member row; payload/schema validation; join-request cleanup/terminal statuses and explicit `invited` handling; enforce or remove `requires_approval`; notifications for reject/decline.
- **DB/infra:** backfill creator rows + recompute `member_count`; optional partial unique index on `post_id`.
- **Tests:** T1, T4, T13.
- **Dependencies:** §9 Q2, Q3, Q4, Q12, Q13.
- **Risks:** Behavior changes (`requires_approval`, connection rule on `/create`).

### Milestone 3 — Message delivery reliability and AI safety (High reliability)
- **Objective:** No lost, duplicated or hung messages; bounded AI cost.
- **Issues:** TH-07, TH-08, TH-12, TH-21, TH-22, TH-09, TH-20, TH-31.
- **Files:** `websocket_threads.py`, `thread_message_service.py`, `messaging.py`, `thread.init.js`, `thread.websocket.js`, `thread.api.js`, `models.py` + migration.
- **Proposed implementation:** Redis limiter on all AI/WS events, bounded executor + terminal events; `client_msg_id` unique index; ascending `after_id` + reconnect catch-up + ack timeout; handler decorator emitting errors; raw-text storage with output escaping; mention diffing in the service; notifications after commit.
- **DB/infra:** `ThreadMessage.client_msg_id` + partial unique index; optional `html.unescape` data migration.
- **Tests:** T6, T7, T8, T10, T17, T18.
- **Dependencies:** §9 Q9, Q11.
- **Risks:** Client and server must ship compatibly (new error/event shapes).

### Milestone 4 — Concurrency correctness
- **Objective:** Make limits race-free.
- **Issues:** TH-14.
- **Files:** `membership.py`, `crud.py`, `websocket_threads.py` (pin).
- **Proposed implementation:** conditional atomic updates (`member_count` / caps) with rowcount checks.
- **DB/infra:** none; optional `CHECK (member_count <= max_members)` after cleanup.
- **Tests:** T9 (PostgreSQL).
- **Risks:** Needs real PostgreSQL in CI.

### Milestone 5 — Horizontal scalability readiness
- **Objective:** Run safely with more than one process.
- **Issues:** TH-13, TH-15, TH-32, parts of TH-07.
- **Files:** `Procfile`, `start-all.py`, `app.py`, `config.py`, `services/websocket_messages.py`, `services/rate_limit_service.py`, `static/js/core/socket.js`.
- **Proposed implementation:** separate web and worker/scheduler processes; production WSGI server with matching worker class; mandatory `REDIS_URL`; WS-only transport (or sticky sessions); Socket.IO session instead of dict; per-user rate keys; `ProxyFix x_for`; restricted CORS; Redis-backed buckets.
- **DB/infra:** hosting/LB configuration.
- **Tests:** T14.
- **Dependencies:** §9 Q8.
- **Risks:** Transport restrictions on some networks; deployment migration.

### Milestone 6 — Data model and read-state simplification
- **Objective:** One read-state source; consolidated attachments and invites where justified.
- **Issues:** TH-18, TH-19 (upload tracking), TH-24.
- **Files:** `models.py` + migrations, `websocket_threads.py`, `messaging.py`, scheduler jobs.
- **Proposed implementation:** `ThreadMember.last_read_message_id`; stop receipt writes; upload registry + cleanup job; DB-level cascades; file cleanup on delete.
- **DB/infra:** new column/table, backfills, later drops.
- **Tests:** T16, T19.
- **Dependencies:** §9 Q5, Q6.
- **Risks:** Backfill correctness; staged drops.

### Milestone 7 — Performance and cleanup
- **Objective:** Remove N+1/unbounded queries and dead code.
- **Issues:** TH-17, TH-23, TH-25, TH-26, TH-29, TH-30.
- **Files:** `discovery.py`, `membership.py`, `messaging.py`, `ai.py`, `static/js/post/*` (delete), schema/validation layer.
- **Proposed implementation:** batch loading, pagination, query budgets; meeting-notes bounds/cooldown/schema; remove dead code and legacy static files; logging/metrics.
- **DB/infra:** possible index drop; none otherwise.
- **Tests:** T15, T20.
- **Risks:** Frontend pagination changes for `/open`.

---

## 9. Questions and Decisions Required

| # | Question | Why it matters | Options |
|---|---|---|---|
| Q1 | Are threads meant to be **private** (hidden from non-members) or **discoverable** (title/description/member count visible to every student)? Today every authenticated user can see every thread, open or closed, and `thread_details` also shows members. | Decides TH-11's fix and whether closed/private threads should be searchable/visible. | (a) all discoverable, roster for members only; (b) add a `visibility` (public/private) flag; (c) private by default. |
| Q2 | What should `requires_approval` mean, and what should the default be (post-threads `False`, standalone `True`)? | TH-05, TH-03: enforcing it changes existing behavior. | (a) enforce "open join" and keep default `True`; (b) remove the setting; (c) enforce and migrate post-threads to `True`. |
| Q3 | Can a removed member rejoin? Is there a ban or cool-down? Should leaving users be able to rejoin freely? | TH-04 cleanup design. | (a) free rejoin after leave, request after removal; (b) removal = ban until a moderator re-invites; (c) timed cool-down. |
| Q4 | Must members added at creation (`member_ids`) be connections, like `add_members`? | Policy bypass (spam/harassment) in TH-16. | (a) enforce connections everywhere; (b) allow any student but require consent (invite) ; (c) keep as is. |
| Q5 | Do you need per-user "seen by" receipts in groups, or only unread badges? Is `ThreadMessageReadReceipt` data used for analytics? | Determines whether the table can be dropped (TH-18, §5). | (a) read pointer only; (b) keep receipts for small threads only; (c) fix receipts as designed. |
| Q6 | Should thread deletion be soft (recoverable) or hard? Retention of messages/files? | TH-24 cascade and cleanup design. | (a) hard delete + file cleanup; (b) soft delete + purge after N days. |
| Q7 | Which mechanism is acceptable for evicting sockets across instances? | TH-02 implementation. | (a) Redis presence + revoke message with local `leave_room`; (b) disconnect the user's sockets (simple, noisy); (c) per-user room fan-out with membership checks (more emits). |
| Q8 | What is the actual hosting topology (reverse proxy, number of proxies, worker model, sticky sessions available, Redis always present)? | TH-13/TH-15 settings (`x_for`, transports, workers). | Provide host/LB details; choose WS-only vs sticky. |
| Q9 | Migrate existing messages containing HTML entities (`&amp;` etc.) with `html.unescape`? | TH-09 data correction vs leaving history as-is. | (a) one-time unescape; (b) fix forward only; (c) unescape on read for legacy rows. |
| Q10 | Meeting-notes policy: who may generate, cooldown, retention, may moderators delete notes? | TH-17 cost control. | e.g., moderators only; 1 per thread per hour; keep last 10. |
| Q11 | When Redis is unavailable, should limits fail open (current) or closed for expensive events (AI, uploads)? | Availability vs cost protection (TH-07/TH-31). | (a) fail closed for AI/upload, open for typing; (b) all open; (c) all closed. |
| Q12 | Role model: may moderators remove other moderators? Can the creator transfer ownership? Should moderators see/receive join requests? | TH-27. | Define hierarchy and add transfer endpoint, or restrict removals to the creator. |
| Q13 | Should rejected join requests and declined invites notify the other party, and with what wording? | TH-28 (tone/safety). | Neutral notification, status badge only, or nothing. |
| Q14 | Are `/threads/open`, `/help/suggestions`, and the legacy `static/js/post/*` files still used by any client (mobile app, other pages)? | Safe deletion/pagination (§5). | Confirm usage or approve removal. |

---

## 10. Final Recommendation

### Must be fixed before production
TH-01 (cross-thread AI), TH-02 (room eviction), TH-06 (mention scope/leak), TH-10 (ex-member edit), TH-11 (details visibility), TH-03 (creator membership), TH-04 (join lifecycle), TH-07 (AI limiter/queue/terminal event), TH-08 (catch-up/`after_id`), TH-13 **if** more than one process/instance will be used (otherwise at least the dev-server entrypoint).

### Should be fixed soon
TH-12, TH-09, TH-14, TH-05, TH-16, TH-20, TH-21, TH-22, TH-15, TH-31, TH-19, TH-18, TH-24.

### Can safely be postponed
TH-17 (until usage grows; the limiter in TH-07 contains cost), TH-23 (until list sizes grow; do `my-threads` first), TH-25-TH-30, TH-32 (verify cookie settings now; hardening later).

### What should be removed
Dead computation in `get_my_threads`; `menber_ids`; client `token` in WS payloads; legacy `static/js/post/{feed.js,feed.html,newfeed.html,resp.js}` (after confirmation); `ThreadMessageReadReceipt` writes/table (after Q5); `idx_tm_ai_personality` (unless `ai_personality` is surfaced); legacy single-attachment columns (after backfill); `requires_approval` **or** its dead behavior (Q2).

### Requires your approval or clarification
Q1-Q14 above, in particular Q1 (privacy model), Q2 (`requires_approval`), Q4 (connection rule), Q7 (eviction mechanism), Q8 (hosting topology), Q9 (history migration).

### Recommended order of implementation
1. Milestone 1 (small, highest value) → 2. Milestone 2 → 3. Milestone 3 → 4. Milestone 4 → 5. Milestone 5 (before any scale-out) → 6. Milestone 6 → 7. Milestone 7.

*End of report. No implementation has been performed; this document is intended to serve as the specification for the next stage once approved.*
