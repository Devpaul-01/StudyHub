# StudyHub — Security

This covers the security model as it's actually implemented: auth, authorization, transport,
input handling, rate limiting, and a few incidents from this codebase's own history that shaped
how things work now. Where something's unfinished, it says so — no point writing this up as more
complete than it is.

A chunk of this is sourced from inline comments in `auth.py` that reference a numbered series of
fixes ("Finding #1" through "Finding #8") and similar audit-style comments elsewhere. Those read
like the record of an actual security review, not planning notes, so where a comment describes
what a bug used to be and how it got closed, that's treated as the authoritative account below.

---

## 1. Authentication

### 1.1 Credentials

Passwords are hashed with Werkzeug's `generate_password_hash` (`auth_service.py`) and checked with
`check_password_hash` (`auth.py`). No custom hashing, nothing reversible. Minimum length is 6
characters, enforced where passwords get set (`auth.py::set_password`). That's a low bar by
current standards — worth saying plainly rather than pretending otherwise.

New accounts get a sentinel value (`pin = "PENDING_VERIFICATION"`) instead of a real password hash
until registration finishes. `check_password_hash` will never match against that string, so a
half-registered account has no working password, full stop. It also doubles as the signal
`login()` checks to give someone a "finish setting up your account" message instead of a generic
"wrong password."

### 1.2 Google OAuth — keyed on Google's account ID, not email

`google_callback()` in `auth.py` resolves the account by Google's `sub` claim — the stable,
non-reassignable ID from the v2 userinfo response — not by email address. This is the fix for
Finding #2 (marked Critical in the code). The original version logged into *any* account matching
the OAuth-returned email, including accounts created with a password that had nothing to do with
that Google identity. A shared or former mailbox is the obvious case: whoever controls that inbox
today could get Google to authenticate the address and walk straight into someone else's account,
no password required.

The current version checks account state before allowing a Google login:

- `google_id` is already set and matches the incoming `sub` → genuine returning user, proceed.
- `google_id` is `NULL` and `pin` is still the untouched `"PENDING_VERIFICATION"` sentinel → a
  legacy Google-created account from before this column existed. No real password was ever set,
  so there's nothing to bypass — backfilling `google_id` here isn't a new grant of access.
- `google_id` is `NULL` and `pin` is a real hash → this account was created with a password.
  Google auth is refused, and the client gets redirected with
  `?error=account_exists_use_password`.

There's a fourth case — `google_id` set but not matching the incoming `sub` — which should be
close to impossible under normal Google behavior. It's still treated as a refusal rather than an
auto-correction. The code doesn't try to guess when the identity signal disagrees with itself.

### 1.3 Session tokens — short-lived access, rotating refresh

Two token types, different shapes because they cover different threats.

**`access_token`** is a stateless JWT (HS256, signed with `SECRET_KEY`), good for 30 minutes,
carrying `user_id` and role. There's no server-side revocation for it — the short lifetime *is*
the mitigation if one leaks.

**`refresh_token`** is DB-backed (`RefreshToken` model), good for 7 days, hashed at rest (SHA-256
— the raw value never touches the database, so a DB read alone can't mint sessions), rotated on
every use, and revocable. A `family_id` ties together every token descended from one login, which
is what makes revoking a whole family possible.

Rotation with reuse detection works like this: every `/refresh-token` call marks the presented
token used and issues a new one in the same family. If an already-used token shows up again,
`auth_service.rotate_refresh_token()` checks the timing. Within 10 seconds of its own rotation, it's
treated as two open tabs racing to refresh at the same moment — the caller gets a fresh access
token without a new refresh rotation. Outside that window, it's treated as a compromised token
being replayed, and the entire family gets revoked server-side. Every session descended from that
original login has to log in again.

### 1.4 Email verification and password reset — single-use, DB-backed

Both flows use opaque, single-use, database-backed tokens (`EmailVerificationToken`,
`PasswordResetToken`) instead of a signed JWT the server can't invalidate individually. This one's
a direct fix for Finding #3 (marked Important). Email verification used to issue a stateless JWT
good for its full 5-hour life, and it didn't matter how many times it got used — plus successful
verification auto-logs the user in. So a verification email that got forwarded, or cached by a
link-preview bot, or leaked any other way, was a live session-hijack vector for the whole 5 hours,
not a one-time link like it looked. Tokens now get marked used the moment they're consumed, so a
replayed link just fails on the second attempt.

Password reset splits peeking from consuming on purpose. `GET /verify-reset/<token>` does a
read-only validity check (`is_valid()`) so a reset link can be opened or previewed without burning
it. The token only actually gets marked used inside `set_password()`, right when a new password is
written. This sidesteps a real class of bug: email clients and chat apps routinely pre-fetch
links, and if opening the link consumed the token, a preview fetch could silently burn a one-time
token before the actual user ever clicked it.

### 1.5 Registration hardening

Registration accepts a client-supplied `google_verified` flag, but that flag alone doesn't get
trusted. `register()` also checks that the server-side OAuth session (`session["google_email"]`)
actually matches the submitted email before treating the registration as Google-verified. Without
that check, anyone could POST `{"google_verified": true}` directly and skip email verification
with zero proof they ever did Google OAuth for that address.

A related fix (Finding #5) clears the transient OAuth session state (`google_email`/`google_name`/
`google_id`) on every completed auth event server-side — login, onboarding save, registration —
instead of counting on the frontend to call a separate clear-session endpoint. Left uncleared, a
stale `google_email` from an abandoned OAuth attempt, or from a previous user on a shared device,
would keep working as an authorization proof for onboarding/registration routes on an unrelated
email.

### 1.6 WebSocket auth happens at the handshake

Flask-SocketIO's `connect` handler runs inside a real Flask request context, so `request.cookies`
is available exactly like in an ordinary HTTP route. StudyHub uses that directly:
`websocket_messages.py::handle_connect` reads the `access_token` cookie straight from the
handshake request when `ACCESS_TOKEN_HTTPONLY` is on (the default — see §2.1), decodes and
validates the JWT, and returns `False` to reject the connection if the token's missing, expired,
or invalid. The client gets disconnected before any event handler runs — it's not left connected
and merely unauthenticated. There's also a secondary `authenticate` event as a fallback for the
non-httponly config, where the token has to travel in the handshake payload instead of a cookie.
Its own docstring calls the `connect`-based check the primary path.

---

## 2. Transport & cookie security

### 2.1 Cookie configuration

| Cookie | HttpOnly | Secure | SameSite | Lifetime |
|---|---|---|---|---|
| `access_token` | `ACCESS_TOKEN_HTTPONLY` (default `true`) | `SESSION_COOKIE_SECURE` config | `Lax` | 30 minutes |
| `refresh_token` | always `true` | `SESSION_COOKIE_SECURE` config | `Lax` | 7 days |
| `csrf_token` | always `false` (has to be JS-readable) | `SESSION_COOKIE_SECURE` config | `Lax` | 30 minutes, reissued with `access_token` |

`csrf_token` only gets issued when `ACCESS_TOKEN_HTTPONLY` is on — which, since that flag defaults
to `true`, means it's issued by default. Here's the honest state of it: the cookie goes out on
every authenticated response, but nothing in this codebase actually validates it against a
submitted header. No `X-CSRF-Token` check, no `validate_csrf` call, anywhere in the request path.
It's the client-side half of a double-submit pattern that never got wired up to server-side
enforcement.

That gap is narrower than "no CSRF protection" in practice, because `SameSite=Lax` on all three
auth cookies already blocks the cross-site POST-with-cookie forgery this pattern exists to stop,
in every modern browser. Still, treat it as an open item, not a working control, until the token's
either actually checked or removed.

### 2.2 CORS

`supports_credentials=True` isn't optional here — the app authenticates via cookies, not an
`Authorization` header, for browser clients. A cross-origin frontend can't complete a request at
all unless the browser's told it can send/receive those cookies. That has a real consequence:
`CORS_ALLOWED_ORIGINS` can't sit at a wildcard (`["*"]`, the config default outside development)
once credentials are involved. Browsers won't honor `Access-Control-Allow-Credentials: true`
paired with a wildcard origin — they reject the request at the browser layer before this app's
logic ever runs. `CORS_ALLOWED_ORIGINS` needs to be set to the real frontend origin(s) in any
environment where frontend and API aren't same-origin.

### 2.3 Security headers

Set on every response via a Flask `after_request` hook, with a guard to skip WebSocket upgrade
connections (setting headers on an already-upgraded connection throws an `AssertionError` under
threading mode, and the hook still fires for those requests otherwise):

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `X-XSS-Protection: 1; mode=block`
- `Strict-Transport-Security: max-age=31536000; includeSubDomains` — only set when the app isn't
  in debug mode *and* `SESSION_COOKIE_SECURE` is on, so HSTS never gets advertised for a
  deployment that isn't actually serving HTTPS.

### 2.4 Logout — real session teardown, not just clearing cookies

`POST /logout` does three things. Each is best-effort and wrapped independently, so one failing
doesn't block the others or the response itself.

First, it disconnects any live WebSocket session tied to the user. This came out of a real gap —
`access_token` doubles as the WebSocket credential now, and a socket that stayed connected through
a logout would keep working.

Second, it revokes the whole refresh-token family server-side. This is a side effect of Finding #6.
Before the fix, logout only cleared cookies on the client, while the refresh token — a
stateless JWT with nothing to revoke, at the time — stayed valid for the rest of its 7-day life. A
copy of that cookie sitting in another tab, another device, or an attacker's hands would keep
minting new access tokens after "logout."

Third, it clears all three auth cookies.

`GET`-based logout was removed entirely — Finding #8. A `GET` logout can be forged with a plain
`<img src="...">` tag on some other site, since browsers don't apply CSRF protections to simple
cross-origin `GET` navigations the way they effectively do for POST under this app's cookie/CORS
setup.

---

## 3. Authorization

### 3.1 One decorator, reused everywhere

`role_required(*allowed_roles)` in `helpers.py` is the single authorization gate for the HTTP API.
It takes credentials from either an `Authorization: Bearer <token>` header or the `access_token`
cookie (header checked first), decodes and validates the JWT, loads the `User`, and checks
`user.role` against the allowed set.

`token_required` is just `role_required("student")` under another name. It's kept as its own alias
specifically so the roughly 250 existing `@token_required` routes kept working unchanged when the
role check got generalized — the fix went into how the decorator's defined, not into touching
every call site. `admin_required = role_required("admin", "system")` is the parameterized version
for the handful of privileged routes.

Failure responses are specific, not one generic 401: no token → 401 "Authentication required.
Please login." User row missing (deleted account, still-valid token) → 401 "User not found."
Expired signature → 401 "Token expired. Please refresh your session." Malformed/invalid token →
401 "Invalid token." Role mismatch → 403 via `AuthorizationError`, "Access denied for this role."

### 3.2 Ownership and membership checks live in the service layer

The role decorator answers "is this caller a student/admin at all" — it doesn't answer "does this
caller own this specific resource." That check happens per-domain in services instead:
`connection_service.can_message` gates direct messaging behind a mutual-accept `Connection`;
`thread_authorization.py` gates moderation actions inside a thread on membership/role; similar
ownership checks exist per-resource for posts, homework assignments, and study sessions.

These are two deliberately separate layers. A valid student token proves who you are. It doesn't
prove you're allowed to act on an arbitrary resource ID — that's a second check, every time.

### 3.3 No raw SQL injection surface

Every query path reviewed goes through SQLAlchemy's ORM and parameterized query construction. The
only raw `text()` calls found are static, literal SQL with nothing request-derived concatenated in
— health-check `SELECT 1` statements (`app.py`, `admin.py`) and two static partial-index `WHERE`
predicates in `models.py` (`is_deleted = FALSE`, `ai_personality IS NOT NULL`). No string-formatted
or f-string-interpolated SQL anywhere in the routes or services.

---

## 4. Rate limiting

HTTP-layer rate limiting (`rate_limit_service.py`) runs on Flask-Limiter, fixed-window strategy,
Redis-backed in production and falling back to in-memory in dev/test. Tiers are chosen per route
by risk and cost, not one global number:

| Tier | Limit | Applied to |
|---|---|---|
| `SENSITIVE_AUTH` | 5/minute | Login, registration, password-reset request, email verification, onboarding writes — every pre-auth route, keyed by IP |
| `WRITE_HEAVY` | 30/minute | Create/update/delete on posts, threads, comments, connections |
| `AI_EXPENSIVE` | 10/hour | AI chat, refinement, meeting-notes generation — these cost real money per call |
| `BURST_OK` | 60/minute | Reactions, likes, other low-risk high-frequency actions |
| `PUBLIC_READ` | 300/minute | Read-only, often-unauthenticated endpoints |
| `WEBHOOK` | 100/minute | External-service callback endpoints, if any are exposed |
| `ADMIN` | 20/minute | Operational/admin endpoints — low volume by nature, privileged callers only |

A global default (`200/day`, `50/hour`) covers any route that doesn't opt into a specific tier.
`/health`, `/ping`, and `/ready` are exempt from rate limiting entirely. Authenticated routes key
by `user_or_ip_key()` — per-user once `g.current_user_id` is set by the auth decorator, falling
back to IP otherwise — rather than IP alone. Straight IP limiting would incorrectly throttle
unrelated students sharing the same campus network.

**Fails open, on purpose.** If Redis errors, the limiter swallows it
(`RATELIMIT_SWALLOW_ERRORS=True`) and falls back to in-memory
(`RATELIMIT_IN_MEMORY_FALLBACK_ENABLED=True`) instead of raising or blocking requests. A rate
limiter that takes the whole app down when its own backing store goes away is a worse failure mode
than temporarily looser limits. A short connect/socket timeout (1 second) on the Redis storage
options means a down Redis fails fast instead of hanging every request for the default multi-second
timeout.

WebSocket-layer rate limiting is separate, and Redis-backed for correctness across instances —
thread-message sends and thread-level AI actions each have their own per-user cap. Full mechanics
in `ARCHITECTURE.md` §13.

---

## 5. File upload security

Every image upload — avatars, post attachments, thread images — goes through
`upload_validation_service.validate_and_normalize_image()` rather than trusting the file extension
or declared MIME type. The route layer does a cheap extension allowlist check first
(`{jpg, jpeg, png, gif, webp}`), but that's early rejection only, not the real gate. The actual
enforcement: every accepted file gets opened with Pillow, structurally verified, fully decoded
(which catches a truncated or corrupt file that a shallow header check would miss), and
re-encoded into a fresh output buffer before it's ever forwarded to storage.

Re-encoding from genuinely decoded pixel data is what makes it structurally impossible to smuggle
a polyglot file through — an SVG or an embedded script payload wearing a `.jpg` extension, say.
The bytes that reach Cloudinary or local storage were generated by Pillow from decoded image data.
Nothing from the original upload gets copied through as-is.

Non-image document uploads are checked against a small magic-byte signature table instead of being
trusted by filename extension alone.

---

## 6. Error handling & information disclosure

A typed exception hierarchy (`errors.py`: `ValidationError` 400, `NotFoundError` 404,
`AuthorizationError` 403, `ConflictError` 409, `RateLimitedError` 429, `ExternalServiceError` 502)
feeds one centralized Flask error handler. It produces `{"status": "error", "message": "...",
"errors": {...}}`, with `errors` present only when there's structured validation detail to show.

Several routes explicitly avoid putting a raw caught exception's message into the response — marked
inline with `# FIX: no str(e) leak` at the specific spots this got corrected. Registration and
login failures return a fixed, generic message no matter what the underlying exception says, so
internal detail — stack traces, SQL fragments, library-specific error text — never reaches the
client on those paths. Anything with `status_code >= 500` gets logged server-side with
`exc_info=True` and forwarded to Sentry. Nothing below 500 triggers a Sentry capture.

Sentry itself (`error_tracking.py`) fails open at every stage. A missing DSN, an init failure, a
runtime capture error — all of it degrades to "no error tracking," never to an app-breaking
exception. The capture call is wrapped in a bare `except: pass` so a failure in the error-reporting
path can't become the source of a second, unrelated error. A `before_send` hook strips the three
auth cookie names (`access_token`, `refresh_token`, `csrf_token`) and password-hash-related field
names, by name, from any event before it leaves the process. `send_default_pii=False` is set
explicitly rather than left to the library default.

---

## 7. Known gaps and accepted trade-offs

Naming what isn't finished, same as everywhere else in this doc:

- **CSRF double-submit is half-wired.** The `csrf_token` cookie gets issued (by default, since
  `ACCESS_TOKEN_HTTPONLY` defaults to `true`) but nothing validates it against a submitted header
  anywhere in the reviewed routes. `SameSite=Lax` on all three auth cookies is the actual
  protection in effect right now; the double-submit infrastructure just isn't finished.
- **Password minimum is 6 characters**, no complexity requirement beyond that. This is a real
  current limit, not a placeholder waiting to be filled in.
- **`_auto_reply_buckets`** — the limiter gating Learnora's unprompted auto-replies (no `@mention`
  needed) in threads — is still process-local instead of Redis-coordinated (see
  `ARCHITECTURE.md` §11.8). Under multiple app instances, a user's cap on triggering unprompted AI
  replies can reset if their connection lands on a different instance between messages. That's a
  cost-control gap, not an auth or data-exposure one.
- **Search runs on unindexed `ILIKE`** against live tables instead of a dedicated search index
  (`ARCHITECTURE.md` §5.6). Not a security issue by itself, but worth knowing here too since it
  affects how much load a low-privilege search query can generate.

---

*For the broader system this sits inside, see [`ARCHITECTURE.md`](ARCHITECTURE.md). For the HTTP
surface these mechanisms protect, see [`openapi.yml`](openapi.yml).*
