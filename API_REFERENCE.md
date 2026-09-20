# StudyHub API Reference

This covers the JSON HTTP API served by the Flask backend. It's the human-readable companion to
`openapi.yml` — same endpoints, same behavior, more explanation of the "why" behind each one.

**What's not here:** routes that render HTML templates (login/register pages, onboarding page,
profile page, the thread/learnora app shells, `/demo`, `/features`) — those aren't API calls.
Socket.IO events (thread messaging, presence, typing, live-session sync) also aren't covered in
detail; see the [WebSocket Events](#websocket-events) section at the end and `ARCHITECTURE.md`.

## Conventions

**Base URL** — all paths are relative to the deployed host. There's no `/api/v1` prefix.

**Auth** — two mechanisms, checked in this order by `role_required()`:
1. `Authorization: Bearer <token>` header
2. `access_token` cookie (httponly, set on login/register/OAuth)

The access token is a 30-minute JWT. A `refresh_token` cookie (7 days, httponly, rotating) is used
by `POST /student/refresh-token` to get a new one — see `SECURITY.md` for how reuse detection and
the multi-tab race window work. Routes are marked **Auth: required** or **Auth: none** below;
almost everything under `/student/*` requires it except the handful of pre-auth onboarding/auth
routes noted explicitly.

**CSRF** — when `ACCESS_TOKEN_HTTPONLY` is on (the default in production), mutating requests
(POST/PUT/PATCH/DELETE) need an `X-CSRF-Token` header matching the `csrf_token` cookie, enforced
by `student_bp.before_request`. Pre-auth routes (login, register, password reset, refresh, etc.)
are exempt since there's no session to forge yet. If you're calling from a browser with cookies,
read `csrf_token` and echo it back; if you're using the Bearer header instead of cookies, CSRF
doesn't apply to you.

**Response envelope** — success responses are `{"status": "success", "message": "...", "data": {...}}`
(message is often omitted on GETs). Error responses are `{"status": "error", "message": "..."}`,
sometimes with an `errors` object for field-level validation failures. A few older routes
(`homework_service.py`'s helper listing) use a different `{"success": true/false, ...}` shape —
called out where it applies.

**Pagination** — most list endpoints use page-based pagination (`page`, `per_page` query params,
a `pagination` object with `page`/`per_page`/`total`/`pages`/`has_next`/`has_prev` in the
response). Feed-style endpoints (post feed, homework feed, assignments) use cursor pagination
instead (`cursor` query param, `next_cursor`/`has_more` in the response) since the underlying
data changes too fast for stable page numbers. A handful of endpoints return everything with no
pagination at all — noted per-endpoint.

**Rate limiting** — enforced by Flask-Limiter with a Redis (or in-memory fallback) backend.
Tiers referenced below: `SENSITIVE_AUTH` (5/min per IP — login, register, password reset, etc.),
`WRITE_HEAVY` (moderate per-minute limit on creates), `AI_EXPENSIVE` (tighter limit on anything
that calls an LLM), `ADMIN` (admin-only routes), `BURST_OK` (cheap, frequently-polled routes like
auth checks). Exceeding a limit returns `429` with a `Retry-After` header.

**File uploads** — `multipart/form-data` with a single `file` (or `avatar`) field. Images and
documents go through content-based validation (decoding pixel data and re-encoding, not just
trusting the extension/MIME type — see `SECURITY.md` §5) before being pushed to Cloudinary. Other
file types are validated by extension only.

---

## Table of Contents

- [Auth](#auth)
- [Google OAuth](#google-oauth)
- [Onboarding](#onboarding)
- [Profile](#profile)
- [Connections](#connections)
- [Messages](#messages)
- [Threads](#threads)
- [Posts](#posts)
- [Comments](#comments)
- [Bookmarks](#bookmarks)
- [Homework](#homework)
- [Study Sessions](#study-sessions)
- [Study Buddy](#study-buddy)
- [Leaderboard](#leaderboard)
- [Reputation](#reputation)
- [Badges](#badges)
- [Analytics](#analytics)
- [Search](#search)
- [Notifications](#notifications)
- [Learnora (AI Assistant)](#learnora-ai-assistant)
- [Admin](#admin)
- [System](#system)
- [WebSocket Events](#websocket-events)

---

## System

### `GET /`
Landing page. Renders HTML, not JSON — listed here only because it's the root path.
**Auth:** none.

### `GET /health`
Uptime-monitoring endpoint. Checks DB connectivity and reports scheduler job status. Not
rate-limited.

**Auth:** none.

**200 response:**
```json
{
  "status": "healthy",
  "database": "connected",
  "email_configured": true,
  "mail_server": "smtp.example.com",
  "scheduler": {
    "running": true,
    "jobs": [
      { "id": "daily_reputation_decay", "name": "Daily Reputation Decay", "next_run_time": "2026-09-21T03:00:00Z" }
    ]
  }
}
```
Returns `500` with `"status": "unhealthy", "database": "disconnected"` if the DB check fails.

### `GET /robots.txt`
Plain-text robots directives. **Auth:** none.

---

## Google OAuth

### `GET /google/start`
Redirects to Google's OAuth consent screen. Meant for full-page browser navigation, not an AJAX
call. **Auth:** none.

**Response:** `302` redirect to Google.

### `GET /google/callback`
Handles the redirect back from Google. Looks the account up by Google's `sub` claim, not just
email — this matters because it means a Google account can't silently take over an existing
password-based account that happens to share an email (see `SECURITY.md` §1.2). Depending on
account state, redirects to:
- the app homepage, with auth cookies set (existing, approved account)
- `/student/onboard/<email>` (new account, needs onboarding)
- `/student/complete-registration?email=<email>` (needs to pick username/password)
- back to login with `?error=...` on failure

**Auth:** none. **Response:** always a `302`, never JSON.

### `GET /student/google_temp_info`
Reads back the email/name captured in the server-side session mid-OAuth-flow, before any JWT
exists. Used by the frontend to pre-fill the onboarding form.

**Auth:** none.

**200 response:**
```json
{ "status": "success", "email": "jane.doe@example.edu", "name": "Jane Doe" }
```

### `POST /student/clear-session`
Clears the pending `google_email`/`google_name`/`google_id` session keys. CSRF-exempt (no cookie
auth exists at this point).

**Auth:** none. **200 response:** `{"status": "success", "message": "..."}`

---

## Auth

### `GET /student/users/me`
Returns the current user with a larger field set than `/student/auth/me` — includes email,
role, status, verification flag, streaks, post/helpful counts. Note: this route is registered in
the source as `users/me` without a leading slash; it resolves to `/student/users/me` in practice.

**Auth:** required.

**200 response:**
```json
{
  "status": "success",
  "data": {
    "user": {
      "id": 42, "username": "janedoe", "email": "jane.doe@example.edu",
      "name": "Jane Doe", "avatar": "https://res.cloudinary.com/.../avatar.jpg",
      "bio": "CS junior, into ML", "reputation": 340, "reputation_level": "Contributor",
      "role": "student", "status": "approved", "email_verified": true,
      "joined_at": "2026-01-15T10:00:00Z", "last_active": "2026-09-20T08:12:00Z",
      "login_streak": 12, "total_posts": 8, "total_helpful": 21, "in_study_session": false
    }
  }
}
```

### `GET /student/auth/me`
Minimal current-user lookup — id/name/username/avatar only. Cheaper than `/users/me` for UI
elements that just need to render an avatar/name.

**Auth:** required.

### `POST /student/register`
Creates a `pending_verification` account and emails a verification link — unless
`google_verified: true` was sent *and* it matches the server-side OAuth session for that email,
in which case the account is verified immediately and the response points straight at
onboarding. A client-supplied `google_verified: true` with no matching session is ignored; you
can't self-declare verification.

**Auth:** none. **Rate limit:** `SENSITIVE_AUTH` (5/min per IP). **CSRF:** exempt.

**Request:**
```json
{ "full_name": "Jane Doe", "email": "jane.doe@example.edu" }
```

**200 response:**
```json
{
  "status": "success",
  "message": "Verification email sent",
  "data": { "google_verified": false, "redirect_url": "/student/login" }
}
```

**400** — missing fields, invalid email format, or email already registered.

### `POST /student/login`
**Auth:** none. **Rate limit:** `SENSITIVE_AUTH`. **CSRF:** exempt.

**Request:**
```json
{ "username_or_email": "janedoe", "password": "correcthorsebatterystaple" }
```

**200 response:** sets `access_token`/`refresh_token` cookies, returns user summary + `redirect`
+ `login_streak`.

**400** — bad credentials, unverified email, incomplete registration (no username/password set
yet), or account pending approval.

### `POST /student/validate-user`
Requests a password reset. Looks up by email or username; if found, issues a single-use token and
emails a reset link.

**Auth:** none. **Rate limit:** `SENSITIVE_AUTH`. **CSRF:** exempt.

**Request:** `{ "data": "janedoe" }` (email or username)

**400** — value missing or user not found.

### `POST /student/verify-reset/{token}`
Checks whether a reset token is still valid, without consuming it — a read-only peek so the
frontend can show "invalid link" before the user starts typing a new password (see `SECURITY.md`
§1.4 for why this is deliberately non-consuming).

**Auth:** none. **Rate limit:** `SENSITIVE_AUTH`. **CSRF:** exempt.

**400** — token invalid, expired, or already used.

### `POST /student/set-password`
Consumes the reset token and sets a new password.

**Auth:** none. **Rate limit:** `SENSITIVE_AUTH`. **CSRF:** exempt.

**Request:**
```json
{ "token": "eyJ...", "password": "newpassword123", "confirm_password": "newpassword123" }
```

**400** — token invalid/expired/used, passwords don't match, or password under 6 characters.

### `POST /student/verify-email/{token}`
Consumes an email-verification token. Auto-logs the user in on success (sets auth cookies) and
points them at onboarding. Replaying an already-used token for an already-verified account
returns a friendly "already verified" 200 rather than erroring — useful if the user double-clicks
the email link.

**Auth:** none. **Rate limit:** `SENSITIVE_AUTH`. **CSRF:** exempt.

**400** — token invalid/expired and the account isn't already verified.

### `POST /student/check-username`
**Auth:** none.

**Request:** `{ "username": "janedoe2" }` — must match `^[a-z0-9]{3,20}$`.

**400** — missing, wrong format, or already taken.

### `POST /student/complete-registration`
Final step: set username + password. Needs either a `token` (JWT proving email ownership, from
the verification email) or an `email` that matches the caller's own OAuth session/existing JWT.

**Auth:** none. **Rate limit:** `SENSITIVE_AUTH`. **CSRF:** exempt.

**Request:**
```json
{
  "email": "jane.doe@example.edu",
  "username": "janedoe", "password": "correcthorsebatterystaple",
  "confirm_password": "correcthorsebatterystaple"
}
```

**401** — caller doesn't own this email. **400** — missing fields, password issues, invalid/taken
username, user not found.

### `POST /student/refresh-token`
Reads the `refresh_token` cookie, validates and rotates it, sets a fresh `access_token` cookie
(and usually a fresh `refresh_token` too — see `SECURITY.md` §1.3 for the 10-second multi-tab
grace window where the old refresh token is still honored once). If reuse of an already-rotated
token is detected, the *entire token family* is revoked and all auth cookies are cleared — this
is the tell for a stolen refresh token being replayed.

**Auth:** none (uses the refresh cookie, not the access token). **Rate limit:** `SENSITIVE_AUTH`.
**CSRF:** exempt.

**400** — no refresh cookie, expired/invalid token, reuse detected, or account not approved.

### `GET /student/verify-auth`
Cheap check of whether the current access token is still valid. Doesn't require a valid token to
call — always returns a body, with `authenticated: true/false`.

**Auth:** none required to call (but tells you if you have one). **Rate limit:** `BURST_OK`.

**401 response includes `should_refresh: true`** when the access token is expired but the client
should try `/student/refresh-token` before giving up.

### `POST /student/logout`
Disconnects any live WebSocket session, revokes the whole refresh-token family, clears all three
auth cookies. Each step is best-effort — one failing doesn't block the others.

**Auth:** required. **Rate limit:** `BURST_OK`.

---

## Onboarding

These run before a full session exists — auth is proven a different way for each (owning the
email via an active Google OAuth session, or an existing JWT for that email).

### `GET /student/onboard/suggestions-by-email/{email}`
Match suggestions for someone about to onboard, keyed by raw email in the path. Falls back to a
generic top-reputation list if nothing scores highly. **Auth:** none. **Rate limit:**
`SENSITIVE_AUTH` per IP.

### `POST /student/onboard/request-all/{email}`
Sends pending connection requests to a batch of user IDs during onboarding. Existing connections
in either direction are silently skipped.

**Auth:** caller must own `email` (matching Google session or JWT). **Rate limit:**
`SENSITIVE_AUTH`.

**Request:** `{ "ids": [12, 45, 88] }`

**401** if the email isn't yours.

### `POST /student/onboard/{email}`
Saves department/class/subjects/study-schedule/etc, flips the account to `pending_verification`,
and logs the user in (sets fresh cookies).

**Auth:** email-ownership check, same as above. **Rate limit:** `SENSITIVE_AUTH`.

**Request (all optional, but at least department/class recommended):**
```json
{
  "department": "Computer Science", "class_level": "Junior",
  "subjects": ["Data Structures", "Linear Algebra"],
  "learning_style": "Visual learner, prefer worked examples",
  "study_preferences": ["Evening sessions", "Small groups"],
  "help_subjects": ["Python", "Statistics"],
  "strong_subjects": ["Algorithms"],
  "study_schedule": { "monday": ["evening"], "wednesday": ["afternoon", "evening"] },
  "session_length": "60"
}
```

### `GET /student/onboard/suggestions/{token}`
Same as the by-email suggestions endpoint, but resolves the user from a signed JWT in the path
instead of a raw email. **Auth:** none (token proves identity). **400** if the token is
invalid/expired.

---

## Profile

### `GET /student/profile/me/data`
Your own profile summary: stats, onboarding details, learning goals, help streak. **Auth:**
required.

### `GET /student/profile/my-posts`
Your own posts. **Auth:** required.

**Query:** `type` — `all` | `pinned` | `questions` | `resources` | `discussions` (default `all`)

Capped at 30 results, no pagination.

### `GET /student/profile/my-stats`
Detailed stats breakdown (posts, engagement, threads, help, connections, reputation) for the
Stats tab. **Auth:** required.

### `GET /student/profile/academic-info`
Your onboarding academic details. **Auth:** required.

### `PUT /student/profile/academic-info`
**Auth:** required.

**Request:**
```json
{
  "subjects": ["Data Structures"], "strong_subjects": ["Algorithms"],
  "help_subjects": ["Python"], "learning_style": "Visual learner",
  "study_preferences": ["Evening sessions"]
}
```
Caps: 15 subjects, 10 strong/help subjects each, 300-char learning style, 10 preferences.

### `POST /student/profile/avatar/upload`
`multipart/form-data`, field `avatar`. Image is decoded and re-encoded server-side before upload
(not just extension-checked — see `SECURITY.md` §5). Allowed: jpg/jpeg/png/gif/webp.

**Auth:** required. **400** on missing file, wrong type, or failed content validation.

### `DELETE /student/profile/avatar`
**Auth:** required.

### `GET /student/profile/help/suggestions`
Top 10 users who could help you, based on your `help_subjects` overlapping their strong subjects.
**Auth:** required. **400** if you haven't filled in onboarding details yet.

### `GET /student/profile/can-help/suggestions`
Top 10 users you could help, based on your `strong_subjects` overlapping their help subjects.
**Auth:** required. Same 400 condition.

### `POST /student/profile/me`
Full profile payload: user, stats, badges, active threads, activity heatmap, skills, learning
goals. Note the verb — `GET` on this same path renders the HTML profile page; `POST` (no body
needed) is the JSON API.

**Auth:** required.

### `GET /student/profile/visibility-settings` / `POST /student/profile/visibility-settings`
Privacy toggles: `set_profile_private`, `show_active_status`, `set_dark_mode`,
`send_weekly_notification` (all boolean, all default `true` except `set_profile_private` and
`set_dark_mode` which default `false`). **Auth:** required.

### `GET /student/profile/{username}`
View someone else's profile. Response shape depends on their privacy setting:
`data.type: "private"` returns a minimal object (id/username/name/avatar/department/class/pinned
posts); `data.type: "public"` returns the full stats/badges/posts/threads breakdown.

**Auth:** required. **400** if the user or profile doesn't exist.

### `PATCH /student/profile/update`
**Auth:** required.

**Request (all optional):**
```json
{ "name": "Jane A. Doe", "bio": "CS junior, TA for Data Structures", "department": "Computer Science", "class_level": "Junior" }
```
`name` needs at least 3 characters, `bio` capped at 500. Returns "No changes made" if nothing
differed from the stored values.

### `POST /student/profile/skills` / `DELETE /student/profile/skills/{skill_name}`
Add/remove a skill tag. Max 10 skills, 50 chars each, no duplicates. Removal matches
case-insensitively and is idempotent (no error if it wasn't there).

**Auth:** required.

### `POST /student/profile/learning-goals` / `DELETE /student/profile/learning-goals/{index}`
Add/remove a learning goal by list index. Max 5 goals, 100 chars each, no duplicates.

**Auth:** required. **400** on cap/duplicate (POST) or out-of-range index (DELETE).

### `POST /student/profile/pin-post/{post_id}` / `POST /student/profile/unpin-post/{post_id}`
Pin/unpin a post to your profile. Max 5 pinned. **Auth:** required. **400** if not yours, not
found, or (pin) already at the cap.

### `GET /student/profile/study-schedule` / `POST /student/profile/study-schedule`
A weekly availability grid. GET always returns all 7 days (empty arrays where unset). POST body
is a map of lowercase day name → array of `morning`/`afternoon`/`evening`/`night`.

**Auth:** required. **400** on an unrecognized day name or time slot.

### `GET /student/skills/popular`
Top 50 skills across all users, with counts. **Auth:** required.

---

## Connections

Connections are StudyHub's mutual "friend" relationship. Status flows are `pending` →
`accepted`/`rejected`, or straight to `accepted` (see auto-accept below), and separately
`blocked`.

### `GET /student/connections/suggestions-by-email/{email}` / `POST /student/connections/onboard-connect/{email}/{target_user_id}` / `POST /student/connections/onboard-connect-all/{email}`
Pre-auth onboarding variants — connect instantly (idempotent) with one or many users while
setting up an account, before full login exists. **Auth:** none. **Rate limit:**
`SENSITIVE_AUTH` per IP.

### `POST /student/connections/help/broadcast`
Broadcasts "I need help with X" to your most relevant connections (scored by subject match,
notified via push + in-app, top 10). **Auth:** required.

**Request:** `{ "subject": "Linear Algebra", "message": "Stuck on eigenvalues, anyone free?" }`

### `POST /student/connections/help/{request_id}/volunteer` / `GET /student/connections/help/{request_id}/volunteers`
Volunteer for (or, as the requester, view volunteers for) a broadcast help request. **Auth:**
required. **400** on volunteering for your own/expired/already-volunteered request.

### `POST /student/connections/help/find`
Find the top 10 users (by expertise score) who can help with a subject, independent of the
broadcast flow. **Auth:** required.

**Request:** `{ "subject": "Organic Chemistry" }`

### `GET /student/connections/requests/received` / `GET /student/connections/requests/sent`
Pending connection requests, capped at 100, no pagination. **Auth:** required.

### `GET /student/connections/list`
Accepted connections, capped at 200. **Auth:** required.

### `GET /student/connections/unseen/received` / `GET /student/connections/unseen/sent` / `GET /student/connections/unseen/all`
Unseen-count badges for the connections UI. **Auth:** required.

### `GET /student/study-sessions/unseen`
Count of pending study-session requests addressed to you. (Named for a different underlying
model historically — it counts `StudySessionCalendar` rows, not a `StudySession` table.)
**Auth:** required.

### `POST /student/connections/mark-seen/{connection_id}` / `POST /student/connections/mark-received-seen` / `POST /student/connections/mark-sent-seen` / `POST /student/connections/mark-all-seen`
Clear the various unseen badges. **Auth:** required.

### `POST /student/connections/request/{user_id}`
Send a connection request. If computed compatibility between you and the target is ≥70%, this
**auto-accepts instantly** instead of creating a pending request — you'll get `connection_status:
"accepted"` back in the same call. Otherwise it's `pending` as usual.

**Auth:** required.

**Request (optional):** `{ "message": "Saw we're both in Data Structures, want to connect?" }`

**404** — user not found. **403** — target has blocked you. **429** — you're inside the 24-hour
cooldown after this user rejected a previous request from you.

### `POST /student/connections/accept/{request_id}` / `POST /student/connections/reject/{request_id}`
**Auth:** required. **400** — not found, not yours to act on, or not pending.

### `DELETE /student/connections/cancel/{request_id}`
Cancel a request you sent. **Auth:** required. **403** if it's not yours.

### `DELETE /student/connections/remove/{user_id}`
Remove an accepted connection. **Auth:** required.

### `GET /student/connections/status/{user_id}`
Quick status check — `self`/`none`/`pending_sent`/`pending_received`/`connected`/`blocked`/`rejected`,
plus `can_message`/`can_connect` flags. **Auth:** required.

### `POST /student/connections/settings`
Toggle the connection-request notification sound. **Auth:** required.

**Request:** `{ "enable_sound": true }`

### `GET /student/connections/blocked/list` / `GET /student/connections/blocked`
Two endpoints, slightly different response shapes, both list users you've blocked. **Auth:**
required.

### `POST /student/connections/block/{user_id}` / `POST /student/connections/unblock/{user_id}`
Block removes any existing connection and records who initiated it. Unblock deletes the
connection row entirely — you'd need to send a fresh request to reconnect, it doesn't restore
`accepted` status. **Auth:** required. **400** on blocking yourself.

### `GET /student/connections/mutual/{user_id}`
Mutual connections with another user, capped at 50. **Auth:** required.

### `GET /student/connections/suggestions`
Grouped suggestions: `study_partners` and `mentors`, separately scored. **Auth:** required.

### `GET /student/connections/search?search=...`
User search scoped to exclude anyone in a block relationship with you. Query needs 2+ chars.
Capped at 50 results. **Auth:** required.

### `GET /student/connections/mutuals/discover?min_mutuals=1`
Friends-of-friends discovery. Falls back to generic high-quality suggestions if you have no
connections or nothing clears the `min_mutuals` threshold — check `discovery_type` in the
response (`mutual` vs `generic`). **Auth:** required.

### `GET /student/connections/suggestions/flat?limit=20`
Same suggestion pool as `/suggestions` but ungrouped. **Auth:** required.

### `GET /student/connections/available-now?subject=...`
Connections currently available to help — scored by online status, subject match, and whether
their study schedule covers the current time slot. **Auth:** required.

### `GET /student/connections/overview/{user_id}`
AI-generated "why you two should connect" overview, streamed as **Server-Sent Events**
(`text/event-stream`). Falls back to a template-based (non-AI) overview with `ai_available: false`
if no AI provider is up — still a `200`, not an error.

**Auth:** required. **Rate limit:** `AI_EXPENSIVE`. **400** if the target is yourself.

### `GET /student/connections/{connection_id}/details`
Full detail view: connection row, partner's public profile, mutual connections, shared threads,
interaction metrics, a health score, and study-session history together. **Auth:** required
(must be a party to the connection). **403** otherwise.

### `GET /student/connections/online?time_window=30` / `GET /student/connections/online/count` / `GET /student/connections/online/department`
Online connections (optionally scoped to your department), using a configurable "last active
within N minutes" window (max 120). **Auth:** required.

### `GET /student/connections/{connection_id}/notes` / `PUT /student/connections/{connection_id}/notes/update` (also `POST`, same behavior)
Private notes you keep on a connection — not visible to the other party. 500-char cap. **Auth:**
required, must be a party to the connection.

---

## Messages

Direct messages between accepted connections. Messages can't be sent to non-connections; see
`can-message` below to check first.

### `POST /student/messages/resources/upload`
`multipart/form-data`, field `file`, 50MB max. Images/documents are content-validated; other
types trusted by extension. **Auth:** required.

### `GET /student/messages/shared-media/{partner_id}` / `GET /student/messages/shared-media/{partner_id}/count`
Media shared in your conversation with a partner. `type` query: `images`/`videos`/`documents`/`links`/`all`.
Paginated (`page`, `limit` up to 200). **Auth:** required, must be connected. **403** otherwise.

### `DELETE /student/messages/{message_id}/delete-for-everyone`
Sender-only, and only within **5 minutes** of sending. Replaces the text with
"[Message deleted]" for both sides. **Auth:** required. **400** past the window or not the
sender.

### `DELETE /student/messages/{message_id}/delete-for-me`
Removes the message from your own view only. **Auth:** required, must be sender or receiver.

### `DELETE /student/messages/clear/{partner_id}`
Soft-clears your view of an entire conversation. **Auth:** required.

### `GET /student/messages/conversations`
One entry per accepted connection (even ones with zero messages yet), sorted by most recent
visible message, with total unread count. **Auth:** required.

### `GET /student/messages/conversation/{partner_id}`
Message history with a partner. Marks incoming messages as read as a side effect and pushes a
`messages_read` WebSocket event to the sender if they're online. Supports `since` (ISO timestamp)
for polling, plus standard `page`/`per_page` (max 100).

**Auth:** required. **403** if you're not connected and there's no block relationship either
(nothing to show).

### `POST /student/messages/{message_id}/mark-read` / `POST /student/messages/mark-all-read/{partner_id}`
**Auth:** required.

### `GET /student/messages/unread-count`
**Auth:** required.

### `GET /student/messages/search?q=...&partner_id=...`
Search your own messages, optionally scoped to one partner. Capped at 50 results. **Auth:**
required. **400** if `q` is missing.

### `GET /student/messages/can-message/{user_id}`
Check before you try — returns `can_message`, a `reason` string, and `can_connect`. **Auth:**
required.

### `POST /student/messages/block/{user_id}` / `POST /student/messages/unblock/{user_id}`
Same underlying block relationship as the Connections endpoints — this is a convenience alias
reachable from the messaging surface. Unblock restores `accepted` status here (unlike the
Connections unblock, which deletes the row). **Auth:** required.

### `POST /student/messages/report/{message_id}`
Report a message for moderation. Currently just logs server-side — there's no `MessageReport`
model backing this yet, so don't expect a moderation queue to pick it up automatically.

**Auth:** required, must be the message's receiver.

**Request:** `{ "reason": "harassment", "description": "optional detail" }`

---

## Threads

Threads are group study spaces — multi-member chat rooms that can be spun off from a post or
created standalone, with join approval, roles (member/moderator/creator), and their own message
stream (separate from direct Messages).

### `POST /student/threads/create`
Create a thread attached to an existing post (the post must have `thread_enabled: true`).
**Auth:** required.

**Request:**
```json
{
  "post_id": 501, "title": "Study group: Linear Algebra midterm",
  "description": "Weekly sessions leading up to the exam", "tags": ["math", "midterm"],
  "max_members": 10, "requires_approval": true, "member_ids": [12, 45]
}
```
**400** — missing/short title, post not thread-enabled, invalid `max_members`, or `member_ids`
would exceed capacity.

### `POST /student/threads/create-standalone`
Same shape, no `post_id`. Capped at **3 new threads per user per rolling 7 days**. **Auth:**
required. **429** once you hit the cap.

### `POST /student/threads/{resource_id}/details`
Legacy lookup kept for older frontend callers — `resource_id` is a thread ID unless you pass
`{"type": "post"}` in the body, in which case it's treated as a post ID and resolves to that
post's thread. **Auth:** required. **400** (not 404) if nothing matches — a quirk of this
particular legacy route.

### `POST /student/threads/{thread_id}/close` / `POST /student/threads/{thread_id}/reopen`
Creator-only. Closing stops new join requests; existing members can still message. **Auth:**
required. **409** if already in the target state.

### `PATCH /student/threads/{thread_id}`
Creator-only. Update title/description/`max_members`/tags (max 5 tags). Broadcasts
`thread_updated` over WebSocket to members. **Auth:** required.

### `DELETE /student/threads/{thread_id}`
Creator-only. Cascades members, messages, and pending requests; notifies members before deleting.
**Auth:** required.

### `POST /student/threads/{thread_id}/avatar`
Creator-only. `multipart/form-data`, field `file`, 5MB max, content-validated image. **Auth:**
required. **503** if storage isn't configured.

### `GET /student/threads/{thread_id}/stats`
Members-only. Message counts, most-active member, etc. **Auth:** required.

### `GET /student/threads/{thread_id}/settings` / `PATCH /student/threads/{thread_id}/settings`
Creator-only. Settings are `is_open`, `max_members`, `requires_approval`. **Auth:** required.

### `POST /student/threads/{thread_id}/leave`
The creator can't leave — they'd need to delete the thread or hand off ownership first (there's
no ownership-transfer endpoint currently). **Auth:** required, must be a member. **403** for the
creator.

### `DELETE /student/threads/{thread_id}/remove/{user_id}`
Creator or moderator only; can't remove the creator. **Auth:** required.

### `GET /student/threads/{thread_id}/members`
Members-only. Includes a 5-minute-activity-derived `online` flag per member. **Auth:** required.

### `GET /student/threads/pending-requests`
Join requests for threads *you* created, across all your threads. **Auth:** required.

### `GET /student/threads/my-requests`
Your own outstanding join requests, across all threads. **Auth:** required.

### `PATCH /student/threads/{thread_id}/members/{user_id}/role`
Creator-only. `{"role": "moderator"}` or `{"role": "member"}` — the creator's own role can't be
changed here. **Auth:** required. **403** on the creator trying to demote themself via this route.

### `DELETE /student/threads/requests/{request_id}/cancel`
Cancel your own pending join request. **Auth:** required.

### `POST /student/threads/{resource_id}/join`
Request to join. `resource_id` is a thread ID unless `{"type": "post"}` is in the body. Re-request
after a rejection is blocked for **24 hours**. **Auth:** required. **403** if closed, **409** if
already a member/pending/approved, **429** during the rejection cooldown.

### `POST /student/threads/{thread_id}/requests/{request_id}/approve` / `.../reject`
Creator or moderator only. Approve row-locks the thread first to check capacity safely under
concurrent approvals. **Auth:** required. **403** if the thread's now full.

### `POST /student/threads/{thread_id}/invite/{user_id}`
Direct invite, bypassing the approval queue — but the invited user still has to accept. Creator
or moderator only. **Auth:** required. **409** if already a member or already invited.

### `GET /student/threads/invites` / `POST /student/threads/invites/{invite_id}/accept` / `POST /student/threads/invites/{invite_id}/decline`
Manage invites sent to you. **Auth:** required. **403** if the invite isn't addressed to you.

### `POST /student/threads/{thread_id}/members/add`
Bulk-add up to 10 of your accepted connections directly as members (no approval flow). Creator or
moderator only; already-members are silently skipped. **Auth:** required.

**Request:** `{ "user_ids": [12, 45, 88] }`

### `GET /student/threads/{thread_id}/messages`
Cursor-paginated (`before_id`/`after_id`/`limit`, max 50). Marks the page read as a side effect.
Includes `pinned_messages` alongside the regular list. **Auth:** required, members-only.

### `POST /student/threads/{thread_id}/messages`
REST fallback for sending a message — the primary send path is the Socket.IO `send_thread_message`
event, and this endpoint returns a smaller payload (just `message_id`/`sent_at`) than the
WebSocket broadcast does. Use it if you're not maintaining a socket connection.

**Auth:** required, members-only. **400** — empty with no attachment, or over 5000 chars.

### `POST /student/threads/{thread_id}/messages/upload`
25MB max. **Auth:** required, members-only.

### `GET /student/threads/{thread_id}/messages/search?q=...`
Members-only, 2+ char query, capped at 50 results. **Auth:** required.

### `GET /student/threads/{thread_id}/messages/pinned`
**Auth:** required, members-only.

### `PATCH /student/threads/{thread_id}/messages/{message_id}`
Sender-only ownership. AI-generated messages can never be edited. **15-minute edit window** for
ordinary members; moderators/creators have no window. Broadcasts `thread_message_edited`.
**Auth:** required. **403** past the window or on someone else's message.

### `DELETE /student/threads/{thread_id}/messages/{message_id}`
Sender, moderator, or creator can delete. Soft delete — text becomes "[deleted]". Broadcasts
`thread_message_deleted`. **Auth:** required.

### `GET /student/threads/departments`
Thread counts by department (cached 30 min). **Auth:** required.

### `GET /student/threads/popular?limit=20&min_members=3`
Popular threads from departments other than your own — cross-department discovery. **Auth:**
required.

### `GET /student/threads/recommended?limit=10`
Personalized recommendations. **Auth:** required.

### `GET /student/threads/help/suggestions?limit=10`
Users you could help, based on your onboarding `strong_subjects`. **Auth:** required. **400** if
you haven't set any.

### `GET /student/threads/my-threads`
Threads you're a member of, with per-thread unread count and last-message preview. **Auth:**
required.

### `GET /student/threads/open`
All open (joinable) threads, unpaginated. **Auth:** required.

### `GET /student/threads/{thread_id}`
Single thread detail. Members get the full member list; creators/moderators additionally get
pending join requests; non-members get basic info plus their own join/membership status.
**Auth:** required.

### `POST /student/threads/{thread_id}/meeting-notes`
AI-generated meeting notes summarizing the thread's recent messages (configurable `message_range`,
10–500, default 50) into topics/decisions/action items/open questions. Needs at least 3 messages
to summarize. Members-only.

**Auth:** required. **Rate limit:** `AI_EXPENSIVE`. **400** — fewer than 3 messages. **503** — AI
unavailable.

### `GET /student/threads/{thread_id}/meeting-notes?limit=5`
Previously generated notes for a thread. **Auth:** required, members-only.

---

## Posts

### `GET /student/posts/feed`
Main feed, cursor-paginated. `filter`: `all`/`connections`/`department`/`trending`/`unsolved`.
`limit` max 20. Optional `post_type` filter. **Auth:** required.

### `POST /student/posts/{post_id}/react`
Toggle a like. Returns `201` when newly liked, `200` when unliked. **Auth:** required.

### `POST /student/posts/resource/upload`
`multipart/form-data`, field `file`. Images/documents content-validated. **Auth:** required.
**503** if storage's temporarily down.

### `GET /student/posts/by-type?post_type=question`
Filtered listing with `department`/`tags` (comma-separated) filters and standard pagination.
**Auth:** required. **400** on missing/invalid `post_type`.

### `GET /student/posts/{post_id}/options-menu`
Fresh permission/state snapshot for a post's "..." menu (can edit, can delete, etc). **Auth:**
required.

### `GET /student/posts/by-status?status=unsolved&post_type=question`
Questions/problems grouped by solved status, with a `summary` breakdown. **Auth:** required.
**400** on an invalid `status`.

### `POST /student/posts/{post_id}/view`
Idempotent per user — calling this repeatedly doesn't re-increment the view counter (compare to
`GET /student/posts/{post_id}` below, which does increment every time). **Auth:** required.

### `GET /student/posts/{post_id}/metrics`
Views, reactions, comments, bookmarks, engagement rate, daily view breakdown, trending flag.
**Auth:** required.

### `POST /student/posts/{post_id}/report`
`{"reason": "spam"|"harassment"|"inappropriate"|"misinformation"|"other", "description": "..."}`.
**Auth:** required. **409** if you've already reported this post and it's still pending review.

### `POST /student/posts/create`
**Auth:** required. **Rate limit:** `WRITE_HEAVY`, plus an in-app spam check (10 posts/hour →
`429`).

**Request:**
```json
{
  "title": "Struggling with recursion in Data Structures",
  "text_content": "Can someone explain how the call stack unwinds for...",
  "post_type": "question", "tags": ["algorithms", "recursion"],
  "thread_enabled": true, "thread_title": "Recursion study group",
  "resources": ["https://example.com/notes.pdf"]
}
```
`department` defaults to your profile department if omitted. `post_type` defaults to
`discussion`. **400** on missing/short/long title or invalid `post_type`.

### `GET /student/posts/{post_id}/quick-view`
Minimal id/title/content lookup — useful for link previews. **Auth:** required.

### `GET /student/posts/{post_id}`
Full detail — post, stats, author, your interaction state, and edit/delete permissions.
**Increments the view counter on every call**, unlike `/view` above. **Auth:** required.

### `DELETE /student/posts/{post_id}`
Author-only. Cascades comments/reactions/bookmarks/threads and cleans up views/follows/mentions.
**Auth:** required.

### `PATCH /student/posts/{post_id}/edit`
Author-only. `post_type` and `department` can't be changed here. `thread_enabled` only takes
effect if the post has zero threads spun off so far. **Auth:** required. **400** if the new title
is too short.

### `POST /student/posts/{post_id}/mark-solved` / `POST /student/posts/{post_id}/unmark-solved`
Author-only, question/problem posts only. Unmarking also clears `is_solution` on whichever
comment had it. **Auth:** required. **400** on unsupported post types.

### `POST /student/posts/{post_id}/follow` / `DELETE /student/posts/{post_id}/unfollow`
Get notified on new activity. **Auth:** required. **409** (follow) if already following, **404**
(unfollow) if not.

### `GET /student/posts/my-posts?page=1`
Your own posts, 20 per page. **Auth:** required.

---

## Comments

### `POST /student/posts/{post_id}/mark-solution` / `POST /student/posts/{post_id}/unmark-solution`
Mark/unmark a specific comment as the accepted solution. Post-author-only. Marking auto-unmarks
any previous solution and awards **+15 reputation** to the comment's author, and checks for the
Problem Solver / Genius badges.

**Auth:** required.

**Request:** `{ "comment_id": 933 }`

### `POST /student/comments/{comment_id}/like`
Toggle. **Auth:** required. **400** if the comment's deleted or the post is locked.

### `POST /student/comments/{comment_id}/mark-helpful`
Toggle. Can't mark your own comment. Awards/removes reputation to the comment author (only on
the *first* mark, not on re-marks). **Auth:** required. **403** on your own comment.

### `PATCH /student/comments/{comment_id}` / `DELETE /student/comments/{comment_id}`
Author-only. Delete is a soft delete — text becomes "[deleted]" but the comment structure
(and any replies) stays intact. **Auth:** required.

### `POST /student/comments/create`
Max reply depth is **1** — you can reply to a top-level comment, but not reply to a reply.
**Auth:** required. **Rate limit:** `WRITE_HEAVY`, plus an in-app spam check (30 comments/hour →
`429`).

**Request:**
```json
{ "post_id": 501, "text_content": "Try drawing the call stack frame by frame — helped me a lot.", "parent_id": null }
```
**400** — missing fields, over 5000 chars, post is locked, or exceeding max reply depth.

### `GET /student/posts/{post_id}/comments`
All comments for a post — top-level comments with their direct replies nested inline. No
pagination. **Auth:** required.

### `GET /student/comments/{comment_id}/replies`
Direct replies to a top-level comment. Only works on depth-0 comments (a depth-1 comment can't
have replies by design). **Auth:** required. **400** if called on an already-max-depth comment.

---

## Bookmarks

### `POST /student/posts/bookmark/toggle`
Bulk toggle, with folder/notes/tags support — the more full-featured bookmark endpoint.

**Request:** `{ "post_ids": [501, 502], "folder_name": "Midterm prep", "notes": "review before Friday" }`

**Auth:** required.

### `POST /student/posts/bulk/bookmark`
Legacy bulk toggle, folder support only, max 50 IDs at once. **Auth:** required.

### `POST /student/posts/{post_id}/bookmark`
Single-post toggle. Returns `201` when newly bookmarked, `200` when removed. **Auth:** required.

### `GET /student/posts/bookmarked?folder=...`
List your bookmarks, optionally scoped to one folder; response includes a `folders` summary with
per-folder counts. No pagination. **Auth:** required.

---

### `GET /student/comments/{comment_id}/resources` / `GET /student/posts/{post_id}/resources`
Get the attached files/links for a comment or post. **Auth:** required. **400** (not 404) if the
comment/post doesn't exist — a legacy quirk of these two routes.

### `GET /student/posts/tags/{tag}`
Posts filtered by a single tag, paginated. **Auth:** required.

### `GET /student/posts/tags`
Trending tags, top 50, with your own previously-used tags weighted toward the top. Returns a
flat map of tag → count. **Auth:** required.

### `GET /student/posts/{post_id}/ask-learnora` / `POST /student/posts/{post_id}/ask-learnora`
Ask the AI about a specific post. Non-streaming — full answer in one response (contrast with the
chat endpoint under Learnora, which streams). GET uses a default "explain this post" prompt;
POST lets you supply `{"question": "..."}`.

**Auth:** required. **Rate limit:** `AI_EXPENSIVE`. **503** if no AI provider is available.

### `PATCH /student/posts/{post_id}/apply-refinement`
Apply an AI-suggested title/content rewrite back onto the post. Author-only. Re-runs @mention
detection against the new content.

**Auth:** required. **Rate limit:** `AI_EXPENSIVE`.

**Request:** `{ "title": "...", "content": "..." }` (at least one required)

---

## Homework

Assignments you track for yourself, optionally shared for peer help.

### `GET /student/homework/{assignment_id}/helpers`
Owner-only, and only if the assignment is currently shared for help. **Note:** this route uses a
different response envelope than the rest of the API — `{"success": true/false, ...}` instead of
`{"status": "success"/"error", ...}` — and returns a bare `500` with that same shape on unexpected
errors rather than the standard error format.

**Auth:** required.

### `GET /student/activity/feed`
Recent homework activity from your connections, last 2 hours only, capped at 50 items. **Auth:**
required.

### `GET /student/homework/my-streak`
Your help-streak: current/longest, whether you helped today, whether the streak's at risk of
breaking. **Auth:** required.

### `GET /student/homework/champions`
This week's top homework helpers. **Heads up:** the underlying `WeeklyChampion` table has no
write path anywhere in the codebase — nothing currently populates it, so expect this to come back
empty until that job exists (see `ARCHITECTURE.md` §5.6).

**Auth:** required.

### `GET /student/assignments`
Cursor-paginated list. `status`: `active`/`completed`/`all`. `sort`: `priority`/`due_date`/`created_at`.
Includes suggestions and summary stats alongside the list. **Auth:** required.

### `POST /student/assignments`
**Auth:** required.

**Request:**
```json
{
  "title": "Problem Set 4", "subject": "Linear Algebra",
  "due_date": "2026-09-28T23:59:00Z", "difficulty": "hard",
  "estimated_hours": 3, "share_for_help": false,
  "resources": [{ "url": "https://example.com/ps4.pdf", "type": "document" }]
}
```
`due_date` must be in the future. **400** on missing title/due_date, bad date format, past date,
or malformed `resources`.

### `PUT /student/assignments/{assignment_id}` / `DELETE /student/assignments/{assignment_id}`
Owner-only. Delete cascades any help submissions on it. **Auth:** required.

### `POST /student/assignments/{assignment_id}/quick-actions`
Owner-only. `{"action": "mark_complete"|"start_working"|"share_for_help"|"unshare"}`. **Auth:**
required. **400** on an unknown action or a no-op (already shared/unshared).

### `GET /student/homework/feed`
Assignments your connections have shared for help. `sort`: `urgency`/`recent`/`difficulty`.
Cursor-paginated. **Auth:** required.

### `POST /student/homework/{assignment_id}/offer-help`
Requires an accepted connection with the assignment's owner; can't offer help on your own
assignment. **Auth:** required. **403** if not connected to the owner. **400** if not shared or
already helping.

### `GET /student/homework/my-help-requests`
Help requests on assignments *you* posted. `status` filter available. No pagination. **Auth:**
required.

### `GET /student/homework/helping`
Assignments *you're* helping with. Same filter/shape as above. **Auth:** required.

### `GET /student/homework/submission/{submission_id}`
Full submission detail — visible to either the requester or the helper. **Auth:** required.
**403** for anyone else.

### `POST /student/homework/submission/{submission_id}/submit-solution`
Helper-only. **Auth:** required.

**Request:** `{ "solution_text": "Here's the approach...", "resources": [] }`

### `POST /student/homework/submission/{submission_id}/give-feedback`
Requester-only. Updates the helper's streak when marked helpful and completed.

**Request:**
```json
{ "feedback_text": "This saved me!", "reaction_type": "lifesaver", "rating": 5, "is_helpful": true, "mark_complete": true }
```
**Auth:** required. **400** if a solution hasn't been submitted yet.

### `DELETE /student/homework/submission/{submission_id}/cancel`
Requester can cancel anytime; helper can only cancel **before** submitting a solution. **Auth:**
required. **403** for a helper trying to bail after submitting.

### `GET /student/homework/stats` / `GET /student/homework/stats/charts`
Dashboard data — overall stats and chart-ready series (daily activity, subject completion rates,
reactions received, response time). **Auth:** required.

---

## Study Sessions

Two related but distinct systems: **scheduled** sessions (`study-session/*` — planned in advance,
need confirmation) and **live** sessions (`live-session/*` — in-progress, real-time, with
pomodoro timers, shared resources, and an AI assistant scoped to the session).

### `POST /student/live-session/{session_id}/set-goal` / `.../update-progress` / `.../rate`
Set the session's stated goal, update a 0–100 progress percentage, or rate it 1–5 after it ends.
**Auth:** required, must be a session participant.

### `POST /student/live-session/{session_id}/pomodoro/start` / `.../pomodoro/break`
Start a focus or break timer within a session (`duration_minutes`, defaults 25/5). **Auth:**
required, participant-only.

### `GET /student/study-session/templates`
Pre-built session templates (subject/duration/structure presets). **Auth:** required.

### `POST /student/study-session/schedule-with-template`
Schedule using a template ID instead of specifying everything manually.

**Request:** `{ "template_id": "pomodoro-2hr", "partner_id": 45, "scheduled_time": "2026-09-25T18:00:00Z" }`

**Auth:** required. **403** if `partner_id` isn't an accepted connection.

### `POST /student/live-session/{session_id}/link-assignment`
Attach a homework assignment to a live session for context. **Auth:** required, participant-only.

### `GET /student/study-session/{session_id}/details`
**Auth:** required, participant-only.

### `POST /student/study-session/{session_id}/reschedule`
Resets the session back to `pending` — needs re-confirmation from the other party. **Auth:**
required, participant-only. **400** if the new time is in the past.

### `POST /student/study-session/{session_id}/cancel` / `.../decline`
**Auth:** required, participant-only.

### `POST /student/study-session/request`
**Request:** `{ "partner_id": 45, "scheduled_time": "2026-09-25T18:00:00Z", "subject": "Calculus", "duration_minutes": 60 }`

**Auth:** required. **403** if `partner_id` isn't an accepted connection. **400** if the time's in
the past.

### `POST /student/study-session/{session_id}/confirm`
**Auth:** required, participant-only.

### `GET /student/study-session/upcoming` / `GET /student/study-session/all?status=...`
Unpaginated lists. **Auth:** required.

### `POST /student/live-session/start`
Start an instant ad-hoc session with a connection, no scheduling. **Auth:** required. **403** if
not connected. **409** if you're already in an active session.

### `GET /student/live-session/{session_id}` / `POST /student/live-session/{session_id}/end` / `.../cancel`
**Auth:** required, participant-only.

### `GET /student/live-session/{partner_id}/history`
Past sessions with a specific partner, paginated. **Auth:** required.

### `POST /student/live-session/{session_id}/ai/ask`
Ask the AI a question scoped to the live session context. **Auth:** required, participant-only.
**Rate limit:** `AI_EXPENSIVE`. **503** if AI's unavailable.

### `GET /student/live-session/{session_id}/ai/history`
**Auth:** required, participant-only.

### `POST /student/live-session/{session_id}/resource/add` / `DELETE /student/live-session/{session_id}/resource/{resource_id}`
Shared links/files within a live session. **Auth:** required, participant-only.

---

## Study Buddy

A separate, preference-driven matching system from Connections — oriented around long-term study
partnerships rather than general networking.

### `POST /student/study-buddy/preferences` / `GET .../preferences` / `PATCH .../preferences`
Subjects, learning style, availability, preferred group size. POST creates (fails `400` if you
already have preferences — use PATCH); PATCH updates (fails `404` if you haven't created any yet).

**Auth:** required.

### `GET /student/study-buddy/suggestions?limit=10`
**Auth:** required. **400** if preferences aren't set.

### `GET /student/study-buddy/suggestions/details/{user_id}`
Detailed match breakdown for one suggested user. **Auth:** required.

### `POST /student/study-buddy/request/{user_id}`
**Auth:** required. **400** if you already have a pending/active match with them.

### `GET /student/study-buddy/match/{match_id}`
**Auth:** required, must be a party to the match.

### `POST /student/study-buddy/session`
Schedule a session tied to an existing match.

**Request:** `{ "match_id": 12, "scheduled_time": "2026-09-25T18:00:00Z", "subject": "Physics" }`

### `GET /student/study-buddy/sessions`
**Auth:** required.

### `DELETE /student/study-buddy/remove/{match_id}`
End an active match. **Auth:** required, must be a party.

### `GET /student/study-buddy/requests/sent` / `.../received` / `.../connected`
Sent requests, received requests, and currently-active matches, respectively. **Auth:** required.

### `DELETE /student/study-buddy/cancel/{request_id}`
Cancel a request you sent. **Auth:** required.

### `GET /student/study-buddy/success-stories`
Featured pairings — public, no auth needed. **Auth:** none.

### `GET /student/study-buddy/stats`
Program-wide stats. **Auth:** required.

---

## Leaderboard

### `GET /student/leaderboard/global?period=all_time&page=1&per_page=50`
`period`: `all_time`/`weekly`/`monthly`. Max `per_page` 100. **Auth:** required.

### `GET /student/leaderboard/department?department=...&period=...`
Defaults to your own department if `department` isn't supplied. **Auth:** required. **400** if
no department can be determined either way.

### `GET /student/leaderboard/me`
Your standing across global and department scopes, plus percentile and nearby entries. **Auth:**
required.

### `GET /student/leaderboard/nearby?range=5`
Entries ranked just above/below you (max range 20). **Auth:** required.

### `GET /student/leaderboard/connections`
Leaderboard scoped to just your connections. **Auth:** required.

### `GET /student/leaderboard/rising?limit=10`
Biggest rank improvements. **Auth:** required.

### `GET /student/leaderboard/stats`
Aggregate stats across the leaderboard. **Auth:** required.

### `GET /student/leaderboard/filters`
Available period/department filter options — public. **Auth:** none.

### `GET /student/leaderboard/rank-history?days=30`
Your rank/score over time, max 90 days. **Auth:** required.

### `POST /student/leaderboard/snapshot`
Manually trigger a rank snapshot for yourself. Normally this runs automatically via the daily
scheduled job (see `BACKGROUND_JOBS.md`); this endpoint just lets the client force one on demand.
**Auth:** required.

---

## Reputation

### `GET /student/reputation/rising-stars?limit=10&days=7`
Users with the fastest-growing reputation over the window. **Auth:** required.

### `GET /student/reputation/me`
Total, current level, next level, points needed, and a category breakdown. **Auth:** required.

### `GET /student/reputation/history?page=1&per_page=20`
Paginated log of reputation-affecting events. **Auth:** required.

### `GET /student/reputation/stats`
Comparisons against department/global averages. **Auth:** required.

### `GET /student/reputation/levels`
The five reputation tiers (Newbie/Learner/Contributor/Expert/Master) with their point thresholds.
Public — no auth needed, since it's reference data. **Auth:** none.

---

## Badges

### `GET /student/badges/available?category=...`
All badges that exist, optionally filtered by category. **Auth:** none.

### `GET /student/badges/my-badges`
**Auth:** required.

### `GET /student/badges/progress`
Your progress toward badges you haven't earned yet. **Auth:** required.

### `GET /student/badges/{badge_id}/details`
Criteria, your current progress, and who else has earned it. **Auth:** none.

### `POST /student/badges/feature/{badge_id}`
Feature/unfeature an earned badge on your profile — max 3 featured at once. **Auth:** required.
**400** if not earned or already at the cap.

### `GET /student/badges/top-earners?limit=10`
**Auth:** none.

### `POST /student/badges/check-all`
Manually re-run badge-qualification checks and award anything newly earned (normally this
happens as a side effect of the actions that trigger badges — this is a manual re-check).
**Auth:** required.

### `GET /student/badges/user-badges/{id}`
Another user's earned badges. **Auth:** none.

---

## Analytics

All scoped to the calling user. **Auth:** required on every endpoint in this section.

### `GET /student/analytics/overview`
High-level dashboard summary.

### `GET /student/analytics/activity-heatmap?days=90`
Daily activity counts, GitHub-style, max 365 days.

### `GET /student/analytics/engagement?period=month`
`period`: `week`/`month`/`quarter`/`year`.

### `GET /student/analytics/impact`
Your impact on others — people helped, solutions accepted, etc.

### `GET /student/analytics/insights`
Personalized, generated recommendations/observations.

### `GET /student/analytics/comparison`
You vs. department/global averages.

### `GET /student/analytics/post/{post_id}`
Analytics for one of your own posts. **403** if it's not yours.

### `GET /student/analytics/weekly-summary`
This week's activity digest.

### `GET /student/analytics/export?format=json`
`format`: `json` (default, standard envelope) or `csv` (raw `text/csv` body). **400** on an
unrecognized format.

---

## Search

### `GET /student/search/unified?q=...&type=all&limit=...`
Combined users/posts/threads search. `type`: `all`/`users`/`posts`/`threads`. **Auth:** required.
**400** if `q` is under 2 characters.

### `GET /student/search/users?q=...&department=...&class_level=...`
Paginated. **Auth:** required.

### `GET /student/search/users/top-contributors?limit=10&department=...`
**Auth:** required.

### `GET /student/search/posts?q=...&post_type=...&department=...`
Paginated. **Auth:** required.

### `GET /student/search/posts/unanswered?department=...`
Paginated. **Auth:** required.

### `GET /student/search/posts/trending?period=week&limit=20`
`period`: `day`/`week`/`month`. **Auth:** required.

### `GET /student/search/threads?q=...`
Paginated. **Auth:** required. **400** if `q` is under 2 chars.

### `GET /student/search/threads/open?q=...`
Open (joinable) threads only. **Auth:** required.

### `GET /student/search/global?q=...`
Broader alias of `/unified`. **Auth:** required.

### `GET /student/search/suggestions?q=...`
Search-as-you-type completions. **Auth:** required.

### `GET /student/search/tags/popular?limit=20`
Public reference data. **Auth:** none.

### `GET /student/search/filters/departments` / `GET /student/search/filters/class-levels`
Lists for populating filter dropdowns. **Auth:** none.

### `GET /student/search/discovery/for-you`
Personalized mixed-content discovery feed. **Auth:** required.

### `GET /student/search/history?limit=10` / `POST /student/search/history` / `DELETE /student/search/history`
Your recent search queries — view, save one, or clear all. **Auth:** required.

---

## Notifications

### `GET /student/profile/notifications/all?page=1&per_page=20&unread_only=false&type=...`
**Auth:** required.

### `POST /student/profile/notifications/mark-all-read`
**Auth:** required.

### `POST /student/profile/notifications/{notification_id}/mark-read`
**Auth:** required.

### `DELETE /student/profile/notifications/{notification_id}`
**Auth:** required.

### `DELETE /student/profile/notifications/delete-all`
**Auth:** required.

### `GET /student/profile/notifications/settings` / `POST /student/profile/notifications/settings`
Email/push toggles plus a per-type map. **Auth:** required.

---

## Learnora (AI Assistant)

StudyHub's built-in AI chat assistant, separate from the post-level and session-level "ask AI"
endpoints covered elsewhere.

### `POST /student/learnora/api/chat`
Streams the response as **Server-Sent Events**. Creates a new conversation automatically if
`conversation_id` is omitted.

**Auth:** required. **Rate limit:** `AI_EXPENSIVE`.

**Request:** `{ "message": "Explain eigenvalues like I'm 12", "conversation_id": null }`

**400** on an empty message. **503** if no AI provider is up.

### `POST /student/learnora/api/conversation/new`
**Auth:** required.

### `GET /student/learnora/api/conversation/list?page=1`
**Auth:** required.

### `GET /student/learnora/api/conversations/{conversation_id}?page=1`
Messages within a conversation, paginated. **Auth:** required, must own the conversation.

### `DELETE /student/learnora/api/conversation/{conversation_id}`
**Auth:** required, owner-only.

### `POST /student/learnora/api/conversation/{conversation_id}/clear`
Wipes messages, keeps the conversation shell (and its title). **Auth:** required, owner-only.

### `PUT /student/learnora/api/conversation/{conversation_id}/title`
**Request:** `{ "title": "Eigenvalue explanations" }` (max 100 chars). **Auth:** required,
owner-only.

### `POST /student/learnora/api/chat/reset-title`
Reset a conversation's title back to auto-generated (based on its content). **Auth:** required,
owner-only.

**Request:** `{ "conversation_id": 88 }`

### `POST /student/learnora/api/upload/attachment`
`multipart/form-data`, field `file`, content-validated for images/documents. **Auth:** required.

### `GET /student/learnora/api/stats`
Your usage totals — conversations, messages, tokens. **Auth:** required.

---

## Admin

Requires role `admin` or `system`. **Rate limit:** `ADMIN` tier on all four.

### `GET /admin/health`
DB connectivity, Redis latency, email config, which rate-limiter backend is active
(`redis`/`memory`), and current environment.

### `GET /admin/scheduler`
Background scheduler status and its registered jobs (see `BACKGROUND_JOBS.md` for what each job
does).

### `GET /admin/ai-providers`
Per-provider health from the multi-provider AI manager — active/failed/blacklisted providers,
which one's currently selected, and whether that state is backed by Redis or held in-memory only.

### `POST /admin/reconciliation/run`
Manually triggers the denormalized-counter reconciliation job on demand — the same logic the
weekly scheduled job runs, for when you don't want to wait. Returns how many counters were
checked, how many drifts were found, and how many were auto-corrected vs. just flagged.

---

## WebSocket Events

Real-time delivery (thread messages, typing indicators, presence/online status, live-session
timer sync, direct-message delivery receipts) runs over Socket.IO, not plain HTTP, so it isn't
part of this REST reference or `openapi.yml`. The REST endpoints above cover the same underlying
data with a request/response fallback (e.g. `POST /student/threads/{thread_id}/messages` sends a
message even without a socket connection open), but the primary, lower-latency path for anything
live is the WebSocket layer.

For the actual event names, payload shapes, and connection/auth handshake, see `ARCHITECTURE.md`
and the `websocket_*.py` modules (`websocket_events.py`, `websocket_messages.py`,
`websocket_threads.py`, `websocket_config.py`, `websocket_rate_limiter.py`) — that's the source of
truth for the real-time layer, and it's substantial enough to warrant its own document rather than
being squeezed into this one.
