# StudyHub — Security

**Scope:** The actual security model as implemented — authentication, authorization, transport, input handling, rate limiting, and the specific incidents in this codebase's own history that shaped it. Where a mechanism has a documented gap, this file says so directly rather than presenting partial coverage as complete.

**A note on sourcing:** several sections below draw on inline code comments that reference a series of internal fixes (numbered "Finding #1" through "Finding #8" in `auth.py`, and similar audit-style comments elsewhere). These read as the record of a real security review that already happened against this codebase, not aspirational documentation — where a comment describes what a vulnerability used to be and how it was closed, this document treats that as the authoritative account of the fix, cited by file rather than paraphrased from memory.

---

## 1. Authentication

### 1.1 Credentials

Passwords are hashed with Werkzeug's `generate_password_hash` (`auth_service.py`) and verified with `check_password_hash` (`auth.py`) — no custom hashing, no reversible storage. Minimum password length is enforced at 6 characters at the point passwords are set (`auth.py::set_password`); this is a low floor by modern standards and is called out here as a known, current limit rather than implied to be stronger than it is.

New accounts are seeded with a sentinel value (`pin = "PENDING_VERIFICATION"`) in place of a real password hash until registration completes. This sentinel does double duty: `check_password_hash` will never validate against it (so a half-registered account has no working password by construction), and it's also the signal `login()` uses to reject a login attempt on an account that hasn't finished setting a real password, with an explicit message rather than a generic credential failure.

### 1.2 Google OAuth — anchored on Google's account ID, not email

`google_callback()` (`auth.py`) resolves the account using Google's `sub` claim (the stable, non-reassignable per-account identifier returned as `id` on the v2 userinfo response), not the email address alone. This distinction is the fix for a specific, documented vulnerability (Finding #2, marked Critical in the code): the original implementation logged in *any* existing account matching the OAuth-returned email, including accounts that were originally created with a password and had no relationship to that Google account at all — meaning anyone who could get Google to authenticate a given address (a shared or former mailbox being the obvious case) could log into a StudyHub account they never registered and don't hold the password for.

The fix distinguishes three account states before allowing a Google-initiated login:

- **`google_id` already set and matches** the incoming `sub` → genuine returning Google user, proceed.
- **`google_id` is `NULL` and `pin` is still the untouched `"PENDING_VERIFICATION"` sentinel** → a legacy Google-created account from before this column existed. It has never had a real password set, so there's no password-based access to bypass, and backfilling `google_id` here is not a new grant of access.
- **`google_id` is `NULL` and `pin` is a real password hash** → this is a password-created account. Google auth is refused outright, with the client redirected to `?error=account_exists_use_password`.

A fourth case — `google_id` is already set but doesn't match the incoming `sub` — is treated as a refusal, not an auto-correction, even though this should be essentially impossible under normal Google behavior: the code explicitly chooses not to guess when the identity signal disagrees with itself.

### 1.3 Session tokens — short-lived access, rotating refresh

Two distinct token types, deliberately different in shape because they have different threat profiles:

- **`access_token`** — a stateless JWT (`HS256`, signed with `SECRET_KEY`), 30-minute lifetime, carrying `user_id` and role. Its short life is the entire mitigation for a leaked token; there is no server-side revocation for it, by design.
- **`refresh_token`** — DB-backed (`RefreshToken` model), 7-day lifetime, **hashed at rest** (SHA-256 — the raw value is never persisted, so a database read alone can't be used to mint new sessions), rotated on every use, and revocable. A `family_id` groups every token descended from one login, which is what makes family-wide revocation possible on reuse detection.

**Rotation with reuse detection.** Every `/refresh-token` call marks the presented token used and issues a new one in the same family. If an already-used token is presented again, `auth_service.rotate_refresh_token()` distinguishes two cases: presented again within a **10-second grace window** of its own rotation → treated as a legitimate multi-tab race (two open tabs both attempted to refresh around the same moment) and handed a fresh access token without a new refresh-token rotation; presented again outside that window → treated as a genuine compromise signal, and **the entire token family is revoked server-side**, forcing every session descended from that original login to re-authenticate.

### 1.4 Email verification and password reset — single-use, DB-backed, not stateless

Both flows use opaque, single-use, database-backed tokens (`EmailVerificationToken`, `PasswordResetToken`) rather than a signed JWT the server can't invalidate individually. This is a direct fix (Finding #3, marked Important) for a real prior gap: email verification originally issued a stateless JWT valid for its full 5-hour life *no matter how many times it was used*, and successful verification auto-logs the user in — meaning a verification email forwarded, cached by a link-preview bot, or otherwise leaked was a live session-hijack vector for the entire window, not a one-time link. The current tokens are marked used at the moment they're consumed, so a replayed link fails outright on its second use.

Password reset tokens follow a deliberate **peek-vs-consume split**: `GET /verify-reset/<token>` performs a read-only validity check (`is_valid()`) so a reset link can be opened or previewed without burning the single-use token, and the token is only actually marked used inside `set_password()`, at the exact point a new password is genuinely written. This avoids a class of bug where a link-preview fetch (email clients and chat apps routinely pre-fetch links) would silently consume a one-time token before the real user ever clicked it.

### 1.5 Registration hardening

Registration accepts a client-supplied `google_verified` flag, but that flag alone is never trusted — `register()` also checks that the server-side OAuth session (`session["google_email"]`) actually matches the submitted email before treating the registration as Google-verified. Without this check, any direct POST to the registration endpoint could set `{"google_verified": true}` and skip email verification entirely with no proof the caller ever completed Google OAuth for that address.

A related fix (Finding #5) ensures the transient OAuth session state (`google_email`/`google_name`/`google_id`) is cleared on every completed authentication event server-side — login, onboarding-save, registration — rather than relying solely on the frontend remembering to call a dedicated clear-session endpoint. Left uncleared, a stale `google_email` session value from an abandoned OAuth attempt (or a previous user, on a shared device) would remain a valid authorization proof for onboarding/registration routes for an unrelated email address.

### 1.6 WebSocket authentication happens at the handshake

Flask-SocketIO's `connect` event handler runs inside a genuine Flask request context, so `request.cookies` is available there exactly as in an ordinary HTTP route. StudyHub uses this directly: the primary WebSocket auth path (`websocket_messages.py::handle_connect`) reads the `access_token` cookie straight from the handshake request when `ACCESS_TOKEN_HTTPONLY` is enabled (the default — see §2.1), decodes and validates the JWT, and **returns `False` to reject the connection outright** if the token is missing, expired, or invalid — the client is disconnected before any event handler runs, not merely left unauthenticated. A secondary `authenticate` event exists as an explicit fallback/legacy path (its own docstring calls the `connect`-based check primary) for the non-httponly configuration, where the token has to be sent in the handshake payload instead of read from a cookie.

---

## 2. Transport & cookie security

### 2.1 Cookie configuration

| Cookie | HttpOnly | Secure | SameSite | Lifetime |
|---|---|---|---|---|
| `access_token` | `ACCESS_TOKEN_HTTPONLY` (**default `true`**) | `SESSION_COOKIE_SECURE` config | `Lax` | 30 minutes |
| `refresh_token` | always `true` | `SESSION_COOKIE_SECURE` config | `Lax` | 7 days |
| `csrf_token` | always `false` (must be JS-readable) | `SESSION_COOKIE_SECURE` config | `Lax` | 30 minutes (reissued with `access_token`) |

`csrf_token` is only issued at all when `ACCESS_TOKEN_HTTPONLY` is enabled — which, since that flag now defaults to `true`, means it's issued by default. **This is a real, current gap worth stating plainly: the `csrf_token` cookie is issued on every authenticated response, but nothing in this codebase's provided routes validates it against a submitted header.** There is no `X-CSRF-Token` check, no `validate_csrf` call, anywhere in the request path. The cookie exists as the client-side half of a double-submit CSRF pattern that was never wired up to server-side enforcement. In practice, `SameSite=Lax` on all three auth cookies already blocks the cross-site `POST`-with-cookie forgery this pattern is meant to guard against in every modern browser, so the exposure this gap represents is narrower than "no CSRF protection at all" — but it should be treated as an open item, not a working control, until the `csrf_token` cookie is either actually validated or removed.

### 2.2 CORS

`supports_credentials=True` is required, not incidental — the app authenticates via cookies rather than an `Authorization` header for browser clients, so a cross-origin frontend cannot complete a request at all unless the browser is told it may send/receive those cookies. This has a direct, load-bearing consequence: `CORS_ALLOWED_ORIGINS` **cannot** be left at a wildcard (`["*"]`, the config default in non-development environments) once credentials are involved — browsers refuse to honor `Access-Control-Allow-Credentials: true` paired with a wildcard `Access-Control-Allow-Origin`, and reject the request at the browser's own enforcement layer before this app's logic is ever reached. `CORS_ALLOWED_ORIGINS` must be set to the real frontend origin(s) in any environment where the frontend isn't same-origin with the API.

### 2.3 Security headers

Applied to every response via a Flask `after_request` hook, with an explicit guard to skip WebSocket upgrade connections (setting headers on an already-upgraded connection raises an `AssertionError` under threading mode, since the hook still fires for those requests):

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `X-XSS-Protection: 1; mode=block`
- `Strict-Transport-Security: max-age=31536000; includeSubDomains` — only set when the app is not in debug mode **and** `SESSION_COOKIE_SECURE` is enabled, so HSTS is never advertised for a deployment that isn't actually serving over HTTPS.

### 2.4 Logout — session teardown, not just cookie clearing

`POST /logout` does three things, each best-effort and independently wrapped so a failure in one never blocks the others or the logout response itself: disconnects any live WebSocket session(s) tied to the user (Finding-driven fix — access_token doubles as the WebSocket credential now, so a stale-but-connected socket surviving a logout was a real gap); revokes the entire refresh-token family server-side (Finding #6's side effect — previously, logout only cleared cookies client-side while the refresh token itself, a stateless JWT with nothing to revoke, remained valid for the rest of its 7-day life, meaning a copy of that cookie retained by another tab, device, or an attacker could still mint new access tokens after "logout"); and clears all three auth cookies. `GET`-based logout was removed entirely (Finding #8) — a `GET` logout is CSRF-forgeable via a plain `<img src="...">` tag on a third-party page, since browsers don't apply CSRF protections to simple cross-origin `GET` navigations the way they effectively do for `POST` requests under this app's cookie/CORS configuration.

---

## 3. Authorization

### 3.1 One decorator, reused everywhere

`role_required(*allowed_roles)` (`helpers.py`) is the single authorization gate for the HTTP API. It accepts credentials from either an `Authorization: Bearer <token>` header or the `access_token` cookie (header checked first), decodes and validates the JWT, loads the corresponding `User`, and checks `user.role` against the allowed set. `token_required` is a plain alias, `role_required("student")` — kept as its own name specifically so the roughly 250 existing `@token_required`-decorated routes kept working unchanged when the role check was generalized to support other roles; the fix went into how the decorator is *defined*, not into rewriting every call site. `admin_required = role_required("admin", "system")` is the parameterized form for the handful of privileged routes.

Failure responses are specific rather than a single generic 401: no token present → `401` "Authentication required. Please login."; user row missing (a deleted account with a still-valid token, for instance) → `401` "User not found."; expired signature → `401` "Token expired. Please refresh your session."; malformed/invalid token → `401` "Invalid token."; role mismatch → `403` via `AuthorizationError` ("Access denied for this role").

### 3.2 Ownership and membership checks live in services

Route-level role checking answers "is this caller a student/admin at all" — it does not answer "does this specific caller own this specific resource." That check is performed per-domain in the service layer: connection state (`connection_service.can_message`) gates direct messaging behind a mutual-accept `Connection`; thread membership/role checks (`thread_authorization.py`) gate moderation actions inside a thread; and equivalent ownership checks exist per-resource across posts, homework assignments, and study sessions. The role decorator and the ownership check are deliberately two separate layers — a valid student token proves identity, not the right to act on an arbitrary resource ID.

### 3.3 No raw SQL injection surface

Every query path reviewed uses SQLAlchemy's ORM and parameterized query construction. The only raw `text()` usage found is for static, literal SQL with no request-derived input concatenated into it — health-check `SELECT 1` statements (`app.py`, `admin.py`) and two static partial-index `WHERE` predicates in `models.py` (`is_deleted = FALSE`, `ai_personality IS NOT NULL`). There is no string-formatted or f-string-interpolated SQL anywhere in the reviewed route or service files.

---

## 4. Rate limiting

HTTP-layer rate limiting (`rate_limit_service.py`) runs on Flask-Limiter with a fixed-window strategy, Redis-backed in production and falling back to an in-memory store in dev/test. Named tiers, chosen per route by risk and cost rather than a single global limit:

| Tier | Limit | Applied to |
|---|---|---|
| `SENSITIVE_AUTH` | 5/minute | Login, registration, password-reset request, email verification, onboarding writes — every pre-authentication route, keyed by IP |
| `WRITE_HEAVY` | 30/minute | Create/update/delete on posts, threads, comments, connections |
| `AI_EXPENSIVE` | 10/hour | AI chat, refinement, meeting-notes generation — these cost real money per call |
| `BURST_OK` | 60/minute | Reactions, likes, other low-risk high-frequency actions |
| `PUBLIC_READ` | 300/minute | Read-only, often-unauthenticated endpoints |
| `WEBHOOK` | 100/minute | External-service callback endpoints, if any are exposed |
| `ADMIN` | 20/minute | Operational/admin endpoints — low volume by nature, privileged callers only |

A global default (`200/day`, `50/hour`) applies to any route that doesn't opt into a specific tier. `/health`, `/ping`, and `/ready` are explicitly exempt from all rate limiting. Authenticated routes key by `user_or_ip_key()` (per-user once `g.current_user_id` is set by the auth decorator, falling back to IP) rather than IP alone — IP-based limiting would incorrectly rate-limit unrelated students sharing a campus network together.

**Fail-open by explicit design.** If the Redis-backed storage errors, the limiter is configured to swallow the error (`RATELIMIT_SWALLOW_ERRORS=True`) and fall back to an in-memory limiter (`RATELIMIT_IN_MEMORY_FALLBACK_ENABLED=True`) rather than raising or blocking requests — a rate limiter that takes the whole app down when its own backing store is unavailable is a worse outcome than temporarily degraded limiting. A short connect/socket timeout (1 second) on the Redis storage options ensures a down Redis fails fast instead of hanging each request for the default multi-second timeout.

WebSocket-layer rate limiting is separate and Redis-backed for cross-instance correctness (thread-message sends and thread-level AI actions each have their own per-user cap) — full mechanics in `ARCHITECTURE.md` §13.

---

## 5. File upload security

Every image upload — avatars, post attachments, thread images — is validated by `upload_validation_service.validate_and_normalize_image()`, not by trusting the file extension or declared MIME type. The route layer does perform a cheap extension allowlist check first (`{jpg, jpeg, png, gif, webp}`), but that check is explicitly early rejection only — the real enforcement is that every accepted file is opened with Pillow, structurally verified, fully decoded (catching a truncated or corrupt file a shallow header check alone would miss), and **re-encoded into a fresh output buffer** before it's ever forwarded to storage. Re-encoding from genuinely decoded pixel data is what makes it structurally impossible to smuggle a polyglot file (an SVG or an embedded script payload mislabeled with a `.jpg` extension, for instance) through the upload path — the bytes that reach Cloudinary or local storage were generated by Pillow from decoded image data, never copied from the original upload.

Non-image document uploads are checked against a small magic-byte signature table rather than trusted by filename extension alone.

---

## 6. Error handling & information disclosure

A typed exception hierarchy (`errors.py`: `ValidationError` 400, `NotFoundError` 404, `AuthorizationError` 403, `ConflictError` 409, `RateLimitedError` 429, `ExternalServiceError` 502) feeds one centralized Flask error handler, which produces `{"status": "error", "message": "...", "errors": {...}}` — the `errors` key present only when structured validation detail exists. Several routes explicitly avoid interpolating a raw caught exception's message into the client-facing response (marked inline with `# FIX: no str(e) leak` at the specific call sites this was corrected) — registration and login failures return a fixed, generic message regardless of the underlying exception, so internal error detail (stack traces, SQL fragments, library-specific messages) never reaches the client on these paths. Errors with `status_code >= 500` are logged server-side with `exc_info=True` and forwarded to Sentry; nothing below 500 triggers Sentry capture.

Sentry itself (`error_tracking.py`) is fail-open at every stage — a missing DSN, an init failure, or a runtime capture error all degrade to "no error tracking," never to an app-breaking exception, and the capture call itself is wrapped in a bare `except: pass` so a failure in the *error-reporting path* can never become the source of a second, unrelated error. A `before_send` hook strips the three auth cookie names (`access_token`, `refresh_token`, `csrf_token`) and password-hash-related field names, by name, from any event before it leaves the process; `send_default_pii=False` is set explicitly rather than relied on as a library default.

---

## 7. Known gaps and accepted trade-offs

Stated directly, in the same spirit as the rest of this project's documentation: an accurate security posture includes naming what isn't finished.

- **CSRF double-submit is half-wired.** The `csrf_token` cookie is issued (by default, since `ACCESS_TOKEN_HTTPONLY` defaults to `true`) but never validated against a submitted header anywhere in the reviewed routes. `SameSite=Lax` cookies provide the actual practical CSRF mitigation currently in effect; the double-submit infrastructure is incomplete.
- **Password minimum is 6 characters**, with no complexity requirement beyond length. This is a real, current limit, not a placeholder.
- **`_auto_reply_buckets`**, the rate limiter gating Learnora's auto-reply-without-`@mention` behavior in threads, is still process-local rather than Redis-coordinated (see `ARCHITECTURE.md` §11.8) — under multiple app instances, a user's cap on triggering unprompted AI replies can reset if their connection lands on a different instance between messages. This is a rate-limiting/cost-control gap, not an authentication or data-exposure one.
- **Search runs on unindexed `ILIKE`** against live tables rather than a dedicated search index (`ARCHITECTURE.md` §5.6) — not itself a security issue, but worth knowing alongside the rest of this document since it affects how much load an unauthenticated or low-privilege search query can generate.

---

*For the broader system this security model sits inside, see [`ARCHITECTURE.md`](ARCHITECTURE.md). For the HTTP surface these mechanisms protect, see [`openapi.yml`](openapi.yml).*
