# StudyHub — Peer Academic Collaboration Platform

**Status:** Actively in development — core systems are functional and I use them daily while building, but the product is not yet feature-complete.

**Live:** [https://studyhub-two-psi.vercel.app/](https://studyhub-two-psi.vercel.app/)

StudyHub is a peer-to-peer academic platform for a university student body, built around one idea: reputation earned by helping other students should be the platform's actual currency, not a vanity number bolted onto a forum. It combines a Q&A/discussion feed, a connections-based social graph, private group chat (Threads), a homework help marketplace, live collaborative study sessions, and a multi-provider AI study assistant ("Learnora") into a single system where almost every feature either produces reputation, consumes it as a signal, or reinforces the behavior that generates it.

A student stuck on a problem set at 11pm has no reliable way to find a classmate who already understands the material, is online right now, and is willing to help — outside of scattered group chats with no structure, no accountability, and no way to reward the people who actually show up. StudyHub's bet is that peer tutoring is abundant but undiscoverable: every class has students who are strong in a subject and students who need help in that same subject, but there's no matching layer, no reputation signal for who's reliable, and no lightweight tooling to make an ad hoc study session productive. The product surfaces the right people to connect with (department, subject overlap, complementary skills, mutual connections), gives every helpful action a transparent point value, and wraps live collaboration — shared timers, a collaborative notepad, embedded AI tutoring — directly around the connections graph instead of leaving students to coordinate over unrelated tools.

The backend is a Flask monolith on PostgreSQL (SQLAlchemy), with real-time features over Socket.IO, Cloudinary for media, Flask-Mail for transactional email, Firebase Cloud Messaging for push, and a self-built multi-provider AI layer (Gemini, Groq, Cohere, Cloudflare Workers AI, Mistral, OpenRouter) with automatic failover, Redis-backed cross-instance state, and horizontal scaling powering every AI-touching feature on the platform. The frontend is vanilla JavaScript.

---

## Why I Built This

Every university has the same quiet inefficiency: the person who could explain a concept in two minutes is somewhere in the same building, and there's no way to find them. Group chats are ephemeral and low-signal. Nobody gets credit for being the person who always answers. I built StudyHub to see what a campus looks like when helping is legible — when a marked-helpful answer, a completed study session, or a login streak all roll up into something visible and comparable, and when an AI assistant is embedded directly in the places students are already working instead of living on a separate chatbot page.

I also used this project to go deep on things that don't show up in a typical CRUD app: a multi-provider AI layer resilient enough that a rate-limited key or a down provider degrades the feature instead of breaking it; a service/route architectural boundary enforced by CI rather than code-review convention; and a reputation system with exactly one write path, after inheriting a version of the codebase where the same tier table existed in four places and quietly disagreed with itself at the boundaries.

---

## Core Features

**Reputation & gamification as the connective layer.** Every meaningful contribution — a helpful answer, a marked solution, a completed homework help, a login streak — awards or deducts reputation through a single write path (`award_reputation()`), which also writes an auditable history row and can trigger a level-up notification. Reputation maps deterministically to one of five tiers (Newbie → Master) via one shared lookup table that every other module in the system reads from — replacing four independently-drifting copies of the same table. An 18-badge achievement system, weekly per-subject "champions," help streaks, and multi-scope leaderboards (global, department, connections-only, "rising stars") all sit on top of this same currency.

**A connections graph that gates messaging by design.** You cannot DM a stranger cold — a mutual-accept `Connection` is required first, which is a deliberate anti-spam/anti-harassment decision baked into the data model itself rather than enforced only in the UI. High-compatibility requests (≥70% match score) auto-accept to reduce friction for good matches, while low-compatibility cold contact still has to clear the request/accept flow. The same table encodes blocking via an explicit `blocked_by_id` column — a fix over an earlier design that overloaded `requester_id`/`receiver_id` to also mean "the blocker," which made block state ambiguous under certain sequences.

**Threads — structured group chat with real moderation.** A thread can spawn from a post or stand alone, with roles (creator/moderator/member), join requests, direct invites, and direct-add from existing connections. Delivery status is computed intelligently and three-state (sent → delivered → read) based on whether other members are actively viewing the thread, merely online, or offline — and it only ever upgrades, never downgrades, even under concurrent race conditions. A single shared authorization helper is the sole place that checks "is this membership privileged," used identically by the REST layer and the WebSocket layer, closing a previous divergence where the two implemented the same permission check slightly differently.

**A homework marketplace built on top of the connections graph.** A private `Assignment` becomes visible to a student's accepted connections the moment it's marked `is_shared_for_help`. Priority scoring (urgency × difficulty × status) is computed fresh on every read rather than persisted as a side effect of viewing — closing a prior design where simply looking at the assignment list could accidentally trigger a database write. Helping someone and getting marked helpful updates a dedicated help-streak using the same consecutive-day logic as login streaks.

**Live study sessions with real collaborative state.** Two connected students can start an ad hoc session with independent, server-computed pomodoro timers (elapsed time is calculated from wall-clock deltas since start, not trusted from the client), a shared versioned markdown notepad, live progress broadcasting, and an AI tutor scoped to the session's subject and current notepad content — reachable without leaving the session view.

**Learnora, embedded rather than standalone.** The AI assistant is reachable from a dedicated chat surface, `@mentions` inside thread messages (five distinct personas, each with its own system prompt), per-post Q&A, live-session tutoring, and on-demand AI-generated thread meeting notes — all through the same underlying multi-provider layer, described in detail below.

---

## AI Systems

This is the part of the codebase I'd point a reviewer to first.

### Multi-provider AI layer with real failover, shared across every instance

Learnora runs on six LLM providers in priority order — Gemini, Groq, Cohere, Cloudflare Workers AI, Mistral, and OpenRouter (pay-per-use fallback) — with support for multiple rotating API keys per provider. This isn't a single API call wrapped in a try/except.

Every failure is classified into one of four categories before the system decides what to do about it, rather than treating every failure identically: `KEY_FAULT` (bad credentials, this specific key hit its rate limit or ran out of quota) cools that key and moves on; `PROVIDER_TRANSIENT` (a 5xx or network-level failure) advances to the next provider *without* penalizing the key, since the key did nothing wrong; `BAD_MODEL` (the provider's own error body says the model is invalid or decommissioned) evicts just that model rather than the whole provider; `NON_RETRYABLE` stops the fallback chain immediately instead of burning through every remaining provider on a request that's never going to succeed anywhere. Earlier versions of this code treated a provider-wide outage exactly like a genuinely bad API key — cooling a perfectly good key for an hour over a failure that had nothing to do with it. The classification is driven by the real structured error data (status code, provider id, parsed error body), never a re-parsed message string.

Cooldown state, provider-type blacklist status, the round-robin rotation index, and the discovered-model cache are now Redis-backed and shared across every running instance, behind a fail-open kill switch (`AI_PROVIDER_REDIS_STATE_ENABLED`). Before this, each instance's view of "which keys are currently bad" was purely local — an instance that saw a key fail had no way to tell any other instance, so under more than one worker, other instances kept sending traffic to a key already known to be dead. Every Redis read/write here is fail-open: a Redis hiccup degrades to an instance occasionally retrying a key another instance already knows is bad, it never raises out and breaks an AI call. Flipping the kill switch off reverts every one of these to the original per-process, in-memory behavior with no code deploy required — the highest-value rollback lever available for a change this central, since this manager sits behind every AI feature on the platform.

- **Per-key cooldown** — a specific key that fails is benched for an hour, now visible to every instance rather than just the one that saw the failure.
- **Provider-type blacklisting** — if a provider *type* (not just one key) fails three or more times inside a 5-minute window, the whole provider type is blacklisted for 30 minutes platform-wide, so a genuinely down provider doesn't get hammered with every remaining key across every instance.
- **Model-level fallback** — evicting a bad model updates the shared cache, so other instances stop routing to it too instead of rediscovering the same failure independently.

On startup, a background thread queries each provider's model catalogue and re-ranks the priority list, folding in newly available models the static config didn't know about (skipped for providers with self-updating aliases or static lists). It checks the shared Redis cache first — on a hit, it applies the cached ranked list with no HTTP call at all, closing a real gap where two instances could independently discover slightly different model lists from a transiently-inconsistent provider response and route two requests for the same feature to different underlying models. That discovery call was also deliberately pulled out of the manager's constructor and into an explicit startup call — instantiating the manager at import time no longer makes a live network request, which matters for running the test suite without network access.

All of this consolidates into one call path, `call_ai_response()`, which walks a flattened provider×model queue and reacts per the classification above — replacing four separately hand-rolled retry loops that used to exist across different call sites (post Q&A, connection overviews, live-session AI, thread meeting notes), each with its own retry count and timeout that had quietly drifted apart from the others, and each treating every failure the same regardless of cause.

### Response reliability, not just response generation

Every AI response is sanitized before it's ever stored or shown: stripping leading `<think>`/`<reasoning>` blocks some reasoning models emit, removing stray SSE protocol artifacts, unwrapping responses a model mistakenly wrapped entirely in a bare code fence (with a heuristic that avoids unwrapping genuine code the model intended to fence), and collapsing runaway blank lines — while deliberately leaving real markdown formatting untouched, since the frontend renderer depends on it surviving.

Vision handling is provider-aware rather than one-size-fits-all: image attachments are base64-encoded and sent as `image_url` parts only when the active model actually supports vision, otherwise the image is replaced with an explicit text placeholder rather than silently dropped or crashing the request. Several providers reject the multimodal array message format outright unless a real image is present, so the system collapses to a plain string whenever there's no image to embed — a provider-compatibility detail that only shows up once you're actually running multiple providers against the same code path.

Conversation history longer than 10 messages is automatically summarized — older turns condensed into a digest, the most recent 5 kept verbatim — before being sent to the provider, so prompt size stays bounded without losing all prior context.

### Streaming with mid-stream recovery

AI chat, connection overviews, and live-session tutoring are all delivered as Server-Sent Event streams, not blocking JSON responses. If a provider fails partway through a stream, the system switches providers mid-stream and emits an explicit `provider_switch` event to the client — the request doesn't fail outright and the user never has to resubmit.

### Graceful degradation instead of a hard failure

The AI-generated connection overview feature — a personalized, streamed explanation of *why* two users should connect — has a fully-functional, template-based fallback built from the exact same compatibility-scoring data the AI prompt would have used. If every provider is unavailable simultaneously, the feature still returns a coherent answer instead of an error. That's a deliberate design pattern in this codebase, not a one-off: build the deterministic version first, let AI enhance it, and never let the enhancement become a single point of failure.

### AI-assisted product surfaces

- **Personalized connection overviews** — an SSE-streamed, AI-written explanation of why two specific users should connect, using the same compatibility heuristics (shared subjects, complementary skills, schedule overlap, department match) the connection-request scorer uses, so the explanation is auditable rather than a black box.
- **AI meeting notes** — on demand, summarizes the last 10–500 thread messages into structured JSON (topics, decisions, action items, open questions, summary), persisted so past summaries stay retrievable.
- **Per-message AI actions** — one-click `summarize` / `translate` / `explain` / `to_code` / `fact_check` transformations of any existing thread message.
- **Five AI personas in threads**, each with a distinct system prompt, triggerable by `@mention` — plus an auto-reply mode that continues a conversation with Learnora automatically if a user replies to an AI message without an explicit trigger, rate-limited to prevent runaway usage.
- **AI-generated conversation titles** — an instant truncated fallback is shown immediately, then upgraded to a real AI-generated title in the background once it's ready.

---

## Engineering Highlights

**A CI-enforced architectural boundary.** The backend was migrated from business logic embedded directly in Flask route handlers into a two-layer architecture: a `services/` layer with zero Flask dependency, and a `routes/` layer that only handles HTTP concerns. This isn't a documented convention people are trusted to follow — `check_layering.py` statically parses every service file's AST and fails the build if a service ever imports a Flask request-scoped object or reaches into `routes/`.

**One reputation write path.** `award_reputation()` is the only function that ever touches `User.reputation`. It floors at zero, recomputes the tier from a single shared lookup table, and writes an auditable before/after history row in the same operation — replacing a previous state where the tier table existed in four separate places that could (and did) disagree at the boundary values.

**Atomic, race-safe counters.** Member counts and similar aggregates are updated via SQL-level atomic expressions (a `CASE`-guarded floor for thread member counts, for example) rather than a read-modify-write in Python, which would lose updates under concurrent requests.

**Delivery status computed from live presence, never downgraded.** A thread message's `sent → delivered → read` status is computed at send-time from whether recipients are actively viewing the thread, online, or offline — and the status can only move forward, even under concurrent read/delivered race conditions.

**Batched queries where N+1 would otherwise creep in.** The main feed endpoint batch-loads authors, the viewer's own reactions, connection statuses, and top comments per post in a small, fixed number of queries regardless of page size. Study-buddy suggestion scoring was reworked from an earlier version issuing roughly 150 extra queries (three per candidate × fifty candidates) down to a fixed number of batched, `IN`-clause and `GROUP BY` aggregate queries.

**Indexes matched to actual query shapes**, not just foreign keys — partial indexes that skip the terminal "read" state most thread messages settle into, composite indexes built for the specific unread-count query pattern, and directional indexes stored both ways when two orderings genuinely serve different access patterns.

**A typed exception hierarchy with one centralized error handler.** Services raise typed errors (`ValidationError`, `NotFoundError`, `ConflictError`, `RateLimitedError`, and others); one Flask error handler turns all of them into the same response envelope a hand-built error response would produce, so no API consumer can tell which code path actually failed.

**Notification delivery that never blocks the operation it's reporting on.** Every notification write attempts a best-effort real-time WebSocket push, wrapped in its own try/except — a push failure is logged but never propagates, because the badge was still awarded and the connection was still accepted regardless of whether the live push succeeded. The notification row itself persists and surfaces on the next fetch either way.

**Background threads for anything that shouldn't block a response.** Email delivery, AI conversation title generation, and AI model discovery all run off the request path in daemon threads, with synchronous fallbacks available for testing.

**A scheduler with an honest single-process boundary — and a documented exception where that boundary was actually removed.** Weekly and monthly leaderboard snapshots run via APScheduler, guarded so a snapshot only ever runs once per day even if triggered twice. The system is explicit — in its own documentation, not just in this README — that scheduler state and WebSocket presence tracking still live in the memory of a single process, and names the exact mitigation (`SCHEDULER_ENABLED` toggle, eventually Celery Beat) rather than leaving that as an unstated assumption. AI provider failover state used to sit in the same category; it's since been moved to Redis specifically because that assumption stopped holding once more than one instance needed to share a consistent view of which keys were bad.

---

## Stack

| Layer | Technology |
|---|---|
| Backend | Flask (Python), two-layer `services/` + `routes/` architecture |
| Database | PostgreSQL via SQLAlchemy |
| Real-time | Socket.IO / Flask-SocketIO (threading mode) |
| AI | Self-built multi-provider layer — Gemini, Groq, Cohere, Cloudflare, Mistral, OpenRouter (Redis-backed cross-instance state) |
| Media storage | Cloudinary (with a Supabase-backed path as an alternate/legacy option) |
| Email | Flask-Mail |
| Push notifications | Firebase Cloud Messaging |
| Auth | JWT (PyJWT), Google OAuth via Flask-Dance |
| Background jobs | APScheduler (single-process) |
| Frontend | Vanilla JavaScript |

---

## Status & Known Limitations

I'm building this in the open and it isn't finished — there's no live deployment yet, and some rough edges are still being worked through. In the interest of the same honesty the rest of this document tries to have:

- **Single-process constraints, narrowed but not eliminated.** The scheduler and WebSocket presence/typing state still live in one process's memory, which means running more than one web worker would fragment both. AI provider failover state (cooldowns, blacklist, rotation, discovered models) no longer has this limitation — it moved to Redis specifically so it works correctly across multiple instances, with a kill switch back to the old in-process behavior if needed. A distributed scheduler is the named future direction for the two subsystems that remain single-process.
- **Two real-time transports exist side-by-side.** A legacy general-purpose WebSocket manager still handles some non-messaging broadcasts, while a newer, purpose-built manager owns all direct-message delivery with stricter read-receipt semantics. This is an intentional interim state from an in-progress migration, not an oversight.
- **Redis is now load-bearing for one subsystem, groundwork for others.** It's the shared state store for AI provider failover (above); `RATE_LIMIT_STORAGE_URI` is still defined ahead of the code that will use it, as groundwork for a planned rate-limiting phase.
- **Live deployment:** [https://studyhub-two-psi.vercel.app/](https://studyhub-two-psi.vercel.app/) — while the remaining features are finished.

---

## License

MIT
