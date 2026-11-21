# Feature Production-Readiness Audit — User Profile

| | |
|---|---|
| **Repository** | `Devpaul-01/StudyHub` (cloned fresh; working tree untouched apart from this file) |
| **Feature audited** | **Profile** — own profile (`/student/profile/me*`), public profile view (`/student/profile/<username>`, `/profile/view/<username>`), edit profile, avatar, skills, learning goals, pinned posts, study schedule, academic info, privacy/visibility settings, FCM token, profile-scoped suggestion endpoints, popular skills |
| **Audit date** | 2026-11-20 |
| **Scope rule** | Profile feature only. Other code was read only to understand dependencies (auth, connections, threads, notifications, storage, rate limiting). |
| **Mode** | Investigation only. No application source, config, tests, dependencies or data were modified. |

> **How to read evidence labels.** Every finding carries one of:
> - **Confirmed (probe)** — reproduced against the real Flask app using the project's own test fixtures (SQLite + fakeredis). Probe scripts lived in `/tmp/audit_probes`, outside the repo; results are in Appendix A.
> - **Confirmed (code)** — directly visible in the code path, not executed.
> - **Potential risk** — plausible from the code but **not reproduced**; the missing verification is stated.

---

## 1. Executive Summary

**Feature audited:** User Profile (frontend `static/js/profile/*`, `static/js/profile-view/*`; backend `routes/student/profile.py`; models `User`, `StudentProfile`, `OnboardingDetails`, `ProfileChangeHistory`).

**Overall assessment: ⚠️ Important improvements required — not safe to call production-ready in its current state.**
No confirmed data-loss or authentication-bypass defect was found, so no finding is rated *Critical*. There are, however, **3 High** findings (one stored-XSS path, one privacy-clobbering API bug, one cross-user input-poisoning bug) that should be fixed before production.

| Severity | Count |
|---|---|
| Critical | 0 |
| High | 3 |
| Medium | 16 |
| Low | 14 |
| **Total** | **33** |

**Most important findings**

1. **PRF-01 (High) — Stored XSS path.** `PATCH /profile/update` stores `name`/`bio`/`department` verbatim (probe: `"><img src=x onerror=alert(1)>` accepted). `profile.templates.js` renders other users' `name`/`department` unescaped in the Connections tab and in the badge "recently earned by" list (attribute context). The separate public-profile page escapes correctly; the own-profile SPA does not.
2. **PRF-02 (High) — Privacy settings are clobbered.** `POST /profile/visibility-settings` resets every omitted key to a default. The UI itself omits `set_dark_mode`, so every save resets it; any partial client call can silently flip a private profile to public. Non-boolean values (`"false"`) are stored verbatim and are truthy.
3. **PRF-03 (High) — One user can poison features for everyone.** `PUT /profile/academic-info` stores arbitrary JSON list items. A dict in `strong_subjects` made `/profile/help/suggestions` return 400 for another user (probe). First-party code in `connections/discovery.py`, `connections/crud.py` and `threads/discovery.py` runs `.lower()`/`set()` over the same lists (code-evidenced, not exercised).
4. **Data disconnects (PRF-07, PRF-09, PRF-11, PRF-13).** Department/class are stored in two places that drift; the profile study-schedule editor writes a column that no matching code reads; `/profile/me/data` omits `skills` so the edit modal always opens empty; the Stats tab shows a hard-coded `0` for "Marked Helpful".
5. **Scalability (PRF-14, PRF-15).** Both profile suggestion endpoints and the **unauthenticated** `/skills/popular` load every user into Python memory per request; 11 authenticated read routes are rate-limited by IP, contradicting the rate-limit service's own documented policy for authenticated routes.

**Horizontal-scalability headline.** `profile.py` holds no process-local state (no in-memory caches, locks or counters), and rate-limit storage is Redis-backed when `REDIS_URL` is set, so the feature is **largely safe to run on multiple instances**. The concerns are per-request full scans (PRF-14), IP-keyed limiting (PRF-15), read-modify-write races on JSON columns (PRF-16), and a dead local-disk avatar fallback (PRF-23).

**Production-ready now?** **No.** Fix PRF-01/02/03 first; PRF-05, 07, 09, 11, 12, 13 are cheap and remove visible user-facing breakage.

---

## 2. Feature Architecture and Execution Flow

### 2.1 Components

**Frontend (vanilla JS, no framework)**

| Piece | File(s) | Role |
|---|---|---|
| Own-profile SPA tab | `static/js/profile/profile.{init,state,api,delegation,templates}.js`, markup in `templates/post/feed2.html` | Header, Posts/Stats/Connections/Badges/Reputation tabs, edit/academic/schedule/privacy modals |
| Public profile page | `static/js/profile-view/profile-view.{init,api,templates}.js`, `templates/profile-view.html` | Other users' profiles; has `escapeHtml` |
| API wrapper | `static/js/core/api.js` | `handleResponse` **throws** `Error(data.message)` on non-2xx; cookie JWT + CSRF double-submit |
| Push | `static/js/push/push.js` | `POST/DELETE /profile/fcm-token` |

Frontend state: `profile.state.js` keeps `profileData`, `posts`, `loaded{}` flags, current tab/filter. Own profile data comes from `GET /profile/me/data`; `POST /profile/me` has **no first-party caller**.

**Backend** — `routes/student/profile.py` (1,574 lines, blueprint `profile_bp`). Business logic and DB queries live directly in the route module; the only services used are storage/upload validation (`services/storage.py`, `services/upload_validation_service.py`). Auth via `@token_required` (`routes/student/helpers.py`), rate limits via `Flask-Limiter` tiers in `services/rate_limit_service.py`.

| Endpoint | Method | Lines | Limiter key |
|---|---|---|---|
| `/profile/me/data` | GET | 38–101 | **IP** |
| `/profile/my-posts` | GET | 104–243 | **IP** |
| `/profile/my-stats` | GET | 245–326 | **IP** |
| `/profile/academic-info` | GET / PUT | 328–418 | IP / user |
| `/profile/avatar/upload`, `/profile/avatar` | POST / DELETE | 420–505 | user |
| `/profile/fcm-token` | POST / DELETE | 508–560 | user |
| `/profile/help/suggestions`, `/profile/can-help/suggestions` | GET | 563–745 | **IP** |
| `/profile/me` | GET (HTML shell) / POST (JSON) | 750–918 | **IP** (POST) |
| `/profile/visibility-settings` | POST / GET | 921–993 | user / **IP** |
| `/profile/view/<username>` | GET (HTML) | 996–1020 | none |
| `/profile/<username>` | GET | 1023–1246 | **IP** |
| `/profile/update` | PATCH | 1249–1316 | user |
| `/profile/skills` (+ `/<skill_name>`) | POST / DELETE | 1319–1366 | user |
| `/profile/learning-goals` (+ `/<int:index>`) | POST / DELETE | 1369–1426 | user |
| `/profile/pin-post/<id>`, `/unpin-post/<id>` | POST | 1429–1476 | user |
| `/profile/study-schedule` | GET / POST | 1479–1534 | **IP** (GET) / user |
| `/skills/popular` | GET | 1537–1569 | **IP, unauthenticated** |
| `/profile/homepage` | GET | 1572 | none |

**Data model** (`models.py`)

| Model | Profile-relevant columns |
|---|---|
| `User` (L367) | `name`, `bio`, `avatar`, `skills` (MutableList JSON), `learning_goals`, `study_schedule` (L408), `privacy_settings` (MutableDict JSON), `fcm_token`, `last_active`, `login_streak`, counters `total_posts`, `total_helpful`, … |
| `StudentProfile` (L507) | `full_name`, `username`, `pin`, **`department`**, **`class_name`**, `date_of_birth`, `guardian_name`, `guardian_contact`, `registered_at`, `status` |
| `OnboardingDetails` (L535) | `department`, `class_level`, `subjects`, `strong_subjects`, `help_subjects`, `learning_style`, `study_preferences`, **`study_schedule`** (L550), `session_length`, `last_updated` |
| `ProfileChangeHistory` (L1680) | defined, **never referenced** |

JSON columns use `MutableDict/MutableList`, so in-place mutation *is* tracked (I checked this specifically; it is **not** a bug).

**Infrastructure touched:** Postgres (SQLAlchemy; schema via `db.create_all()` at `app.py:629`, only 2 Alembic revisions), Redis (limiter storage), Cloudinary (avatars, `services/storage.py`), Firebase Cloud Messaging (token stored on `User`), Sentry (`services/error_tracking.py`, ERROR-level logs become events). No background job, queue, or WebSocket belongs to the profile feature.

### 2.2 End-to-end flow

```mermaid
flowchart LR
  subgraph FE["Frontend (vanilla JS)"]
    A[profile.delegation.js<br/>click handlers] --> B[profile.api.js<br/>try/catch → null]
    B --> C[core/api.js<br/>fetch + CSRF + refresh]
    T[profile.templates.js<br/>innerHTML]:::risk
    V[profile-view.templates.js<br/>escapeHtml]
  end
  C -->|HTTPS + HttpOnly JWT cookie + X-CSRF| R
  subgraph BE["Flask: routes/student/profile.py"]
    R[token_required → limiter<br/>IP or user key] --> H[Route handler<br/>validation + queries inline]
  end
  H --> DB[(Postgres<br/>User / StudentProfile /<br/>OnboardingDetails / Post / Connection ...)]
  H --> CL[Cloudinary<br/>avatars/&lt;user_id&gt;]
  H --> FCM[(users.fcm_token)]
  RL[(Redis<br/>limiter counters)] -.-> R
  H -. logger.error .-> S[Sentry]
  DB --> H --> |"{status,message,data}"| C --> A
  H --> T
  H --> V
  classDef risk fill:#fee,stroke:#c00;
```

**Main journeys**

1. *Open own profile:* `initProfile()` → `GET /profile/me/data` → header; tabs lazily call `/profile/my-posts`, `/profile/my-stats`, connections, badges, reputation endpoints.
2. *Edit profile:* modal → `PATCH /profile/update` (name/bio/department/class) → header re-render. Skills/goals via separate POST/DELETE calls that return the full list.
3. *Privacy:* modal → `POST /profile/visibility-settings` (4 booleans) → `privacy_settings` JSON replaced wholesale.
4. *View someone else:* `/profile/view/<username>` (HTML shell) → `GET /profile/<username>` → either full public payload or reduced "private" payload.
5. *Avatar:* multipart → `validate_and_normalize_image` (Pillow re-encode) → `cloudinary_storage.upload_file(folder="avatars/<id>")` → `users.avatar` URL.

---

## 3. Findings and Issues

Legend — **Status:** *Confirmed (probe)* / *Confirmed (code)* / *Potential risk*. Findings are ordered by severity, then by ID.

### PRF-01 — Stored XSS: profile text stored raw and rendered unescaped in the own-profile SPA
- **Severity:** High  **Category:** Security  **Status:** Confirmed (probe) for storage; Confirmed (code) for the sinks; execution in a browser **not** exercised.
- **Location:** `routes/student/profile.py` `update_profile` L1252–1316 (no sanitisation of `name`, `bio`, `department`, `class_level`); `add_skill` L1322, `add_learning_goal` L1372, `update_academic_info` L372 (same raw storage). Sinks in `static/js/profile/profile.templates.js`: L76 (`buildAvatar` alt), L91 `${data.name}`, L96 `${data.bio}`, L126 goals, **L508/L514 `${u.name}` and L516 `${u.department}` (Connections tab — *other users'* data)**, **L900 `alt="${e.name}" title="${e.name}"` (badge "recently earned by" — attribute context, other users' data)**.
- **Evidence:** Probe P4: `PATCH /profile/update {"name":"\"><img src=x onerror=alert(1)>","bio":"<script>alert(1)</script>"}` → 200; `GET /profile/me/data` returns both strings verbatim. `profile-view.templates.js` defines and uses `escapeHtml` (L7, L48, L50), proving the escaping convention exists but was not applied to `profile.templates.js`. No `Content-Security-Policy` header is set in `app.py` (only `X-Frame-Options`). `bleach` is a dependency but is not used by the profile routes. Usernames are regex-validated (`^[a-z0-9]{3,20}$`, `auth.py` L1339/L1425), so `@${u.username}` is not an injection point.
- **Problem:** Attacker-controlled free text reaches `innerHTML` in other users' browsers.
- **Impact:** Script executes in the victim's origin. The JWT cookie is HttpOnly, but injected script can issue same-origin authenticated requests and `core/api.js` itself reads the CSRF cookie from JS, so it can pass CSRF — i.e. act as the victim (post, message, change privacy, send connection requests).
- **Reproduction:** (1) Attacker sets name to an attribute-breaking payload (≤100 chars is enough). (2) Attacker becomes a connection of the victim (request/accept; docs say ≥70 % compatibility requests auto-accept) **or** earns a badge the victim later inspects. (3) Victim opens *Connections* tab or the badge-details sheet.
- **Recommended solution:** (a) Add one shared `escapeHtml` (move the existing one from `profile-view.templates.js` into a shared util) and wrap **every** interpolated user-controlled value in `profile.templates.js`, including attribute contexts (`alt`, `title`, `data-*`, `src`). (b) Server-side defence in depth: treat `name`, `department`, `skills`, goals and subjects as plain text — reject or strip `<` `>` (and control chars); optionally `bleach.clean(..., tags=[], strip=True)` for `bio`. (c) Add a CSP (`script-src 'self'` plus the CDNs actually used) — decision needed, see Q9. (d) One-off scan of existing rows for `<`/`>` in `users.name`, `users.bio`, `student_profiles.department`, `users.skills`, `users.learning_goals`.
- **Implementation considerations:** Escaping at render is the real fix; server-side stripping must not mangle legitimate names (apostrophes, `&`). Registration (`auth.py`, out of scope) writes `name` the same way and should get the same rule. CSP may break inline handlers — roll out in report-only mode first.
- **Verification:** Playwright/jsdom test rendering a profile and connection list with payload names asserts no element/handler is created; API test asserts angle brackets are rejected/stripped; manual two-account check of Connections tab and badge details.

### PRF-02 — `POST /profile/visibility-settings` resets every omitted setting; no type validation
- **Severity:** High  **Category:** Data Integrity / Security (privacy)  **Status:** Confirmed (probe P1, P1b)
- **Location:** `routes/student/profile.py` L924–952 (defaults at L933–937, assignment L939–943); `static/js/profile/profile.delegation.js` L574–577.
- **Evidence:** Handler does `data.get(key, DEFAULT)` for all five keys and overwrites the stored value. Probe P1: after storing `{private:true, show_active:false, …}`, sending only `{"set_dark_mode":true}` produced `set_profile_private:false, show_active_status:true, send_weekly_notification:true, show_in_activity_feed:true`. Probe P1b: the exact four-key payload the UI sends leaves `set_dark_mode:false` even though it had been `true`. Probe P1: `{"set_profile_private":"false"}` is stored as the string `'false'`, which is truthy — consumers using `.get("set_profile_private")` (`profile.py`, `services/search_service.py` L218, `services/activity_service.py` L212) would treat the profile as private.
- **Problem:** Replace-all semantics with silent defaults on an endpoint whose payload is documented as optional per-key; values are not validated as booleans; `request.get_json()` (non-silent) raises on malformed bodies and is swallowed into a generic 400.
- **Impact:** A client that updates one setting silently changes the others — including making a private profile public or re-enabling activity-feed visibility. First-party UI silently resets `set_dark_mode` on every save.
- **Reproduction:** See Probe P1/P1b in Appendix A.
- **Recommended solution:** Merge semantics: iterate over an allow-list of keys; update only keys present in the payload; require `isinstance(v, bool)` else 400 naming the field; reject unknown keys. Use `get_json(silent=True)`. Keep the POST verb/URL for compatibility.
- **Implementation considerations:** One-off migration to coerce stored string booleans (`'true'/'false'`) to real booleans. Decide the fate of `set_dark_mode` and `send_weekly_notification` first (Q6) — both currently have no consumer (see Section 5).
- **Verification:** Tests: partial update preserves other keys; non-boolean rejected with 400; UI payload does not alter `set_dark_mode`; stored `"false"` is normalised.

### PRF-03 — `PUT /profile/academic-info` persists unvalidated list items that other users' features consume
- **Severity:** High  **Category:** Reliability / Security (cross-user availability)  **Status:** Confirmed (probe P6) for the profile suggestion endpoints; **Potential risk** for the first-party consumers listed below (same pattern, not exercised).
- **Location:** `routes/student/profile.py` `update_academic_info` L372–418; consumers: `help_suggestions` L609 (`set(help_subjects) & set(details.strong_subjects)`), `can_help_suggestions` L700; outside the feature but dependent: `routes/student/connections/discovery.py` L345–346, L1115–1126; `routes/student/connections/crud.py` L118–119 (compatibility check on connection request); `routes/student/threads/discovery.py` L241.
- **Evidence:** Handler only checks `isinstance(list)` and slices (`[:15]`/`[:10]`). Probe P6: attacker `PUT {"strong_subjects":[{"a":1},["nested"]],"subjects":["s"*100000]}` → 200. Victim (with non-empty `help_subjects`) `GET /profile/help/suggestions` → **400** "Error encountered while loading help suggestions"; control `/can-help/suggestions` → 200. Discovery/crud code calls `s.lower()` on each stored item, which fails for non-strings.
- **Problem:** No per-item type, length or normalisation checks; a single malformed candidate row aborts the whole candidate loop for every requester matched against it.
- **Impact:** Any authenticated user can degrade suggestion/discovery/compatibility endpoints for other users, and can store very large strings (bounded only by the 16 MB request limit × 30 writes/min).
- **Reproduction:** Appendix A, probe P6.
- **Recommended solution:** Validate each item: `str`, trimmed, non-empty, max length (suggest 50), dedupe case-insensitively; reject with a field-specific 400 instead of silently coercing. Make consumers defensive (skip non-str entries / per-candidate try-except) so one bad row cannot fail a whole response. One-off scan/cleanup of existing `onboarding_details.{subjects,strong_subjects,help_subjects,study_preferences}` rows.
- **Implementation considerations:** Canonical subject vocabulary (Q5) would also fix the case/whitespace mismatch that makes `set()` intersections miss ("Python" vs "python" in the profile suggestion endpoints, which — unlike discovery — do not lower-case).
- **Verification:** API tests for rejected payloads; consumer tests with a pre-seeded malformed row asserting the endpoint still returns 200.

### PRF-04 — `last_active` and `login_streak` exposed on the public profile even when `show_active_status` is off
- **Severity:** Medium  **Category:** Security (privacy)  **Status:** Confirmed (probe P2)
- **Location:** `routes/student/profile.py` L1217 (`"last_active"` unconditional), L1215 (`login_streak`); the *private* branch at L1196 correctly gates `last_active` on `show_active`.
- **Evidence:** Probe P2: with `show_active_status=false` the response still contains `last_active` (an ISO timestamp) and `login_streak`, alongside `active_status:false`.
- **Problem:** The flag only toggles a boolean for the UI; the raw value is still sent.
- **Impact:** Users who opted out of showing activity are still trackable by any authenticated user calling the API directly.
- **Reproduction:** Probe P2.
- **Recommended solution:** Null out `last_active` (and decide on `login_streak`, Q3) when `show_active` is false, in the public branch.
- **Implementation considerations:** Check the profile-view UI does not rely on the field when inactive status is hidden.
- **Verification:** Test: viewer sees `last_active: null` when owner disabled it.

### PRF-05 — Connected users never get past the "private profile" view although the UI says "Connect to view details"
- **Severity:** Medium  **Category:** Product  **Status:** Confirmed (probe P3)
- **Location:** `routes/student/profile.py` L1176 (`if profile_private:` — no connection check); the promise is made by the backend message at L1193, `"This profile is private. Connect to view details."`.
- **Evidence:** Probe P3: after `make_connection(viewer, target, "accepted")`, response is still `type:"private"` with `connection_status:"connected"`. Note: pinned-post titles on private profiles are **documented** behaviour (`API_REFERENCE.md`), not a defect.
- **Problem:** The privacy gate ignores relationship; the copy promises otherwise.
- **Impact:** Users who connect to a private profile can never see more; the call-to-action is misleading.
- **Reproduction:** Probe P3.
- **Recommended solution:** Product decision (Q1): either (a) let accepted connections see the full profile, or (b) change the copy to match current behaviour. Implementation of (a) is a one-line condition on `connection_status == "connected"`.
- **Implementation considerations:** If (a), re-check `search_service` L218 and `activity_service` L212, which also read the private flag.
- **Verification:** Test matrix: stranger/connected/pending/self × public/private.

### PRF-06 — Block relationship not consulted when viewing a profile
- **Severity:** Medium  **Category:** Product / Security  **Status:** Confirmed (probe P3b) as behaviour; **intent unconfirmed**.
- **Location:** `routes/student/profile.py` `view_profile` L1026–1246. `is_user_blocked` is used only in the messaging routes.
- **Evidence:** Probe P3b: a user blocked by the profile owner receives `200`, `type:"public"`, full stats, with `connection_status:"blocked"`.
- **Problem:** Blocking has no effect on profile visibility. No document states that it should.
- **Impact:** A blocked user can keep reading the blocker's profile, stats and posts.
- **Reproduction:** Probe P3b.
- **Recommended solution:** Decide (Q2). If blocking should hide the profile, return the same "User not found" response used for missing users in `view_profile` when either side blocked the other (avoids confirming the block).
- **Implementation considerations:** Check other read surfaces (search, leaderboard) for the same policy to keep behaviour consistent — outside this audit's scope.
- **Verification:** Test: blocked viewer gets not-found.

### PRF-07 — Department / class are stored twice and drift
- **Severity:** Medium  **Category:** Data Integrity  **Status:** Confirmed (probe P5)
- **Location:** `update_profile` L1283/L1289 writes `StudentProfile.department/class_name`; `get_academic_info` L331 reads `OnboardingDetails.department/class_level`; `update_academic_info` cannot change either.
- **Evidence:** Probe P5: after `PATCH /profile/update {"department":"Biology"}`, `/profile/me/data.department == "Biology"` but `/profile/academic-info.department == "Physics"`. `StudentProfile` is the value consumed by feed, leaderboard and discovery (e.g. `discovery.py` L284 uses `profile.class_name`); `OnboardingDetails.department` is read by `/academic-info` and the onboarding match code (`auth.py` ~L735).
- **Problem:** Two sources of truth, one write path.
- **Impact:** The same screen set shows two different departments; onboarding-time matching uses the stale value.
- **Reproduction:** Probe P5.
- **Recommended solution:** Make `StudentProfile` authoritative; have `update_profile` also update the `OnboardingDetails` copy **or** have `get_academic_info` read from `StudentProfile`. Longer term consolidate (Section 5).
- **Implementation considerations:** No migration needed for the minimal fix; a back-fill aligning existing `OnboardingDetails` to `StudentProfile` is advisable.
- **Verification:** Test: update department → both endpoints agree.

### PRF-08 — `name`, `department`, `class_level` accept arbitrary values; server limits differ from client and DB
- **Severity:** Medium  **Category:** Data Integrity  **Status:** Confirmed (probe P4) for acceptance; Postgres length failure is a **Potential risk** (tests run on SQLite, which does not enforce `VARCHAR` length).
- **Location:** `update_profile` L1263–1292; client limits in `feed2.html` (`name` maxlength 80, `bio` 400); DB columns `User.name` String(100), `StudentProfile.department` String(100), `class_name` String(50), `OnboardingDetails.department` String(50), `class_level` String(20).
- **Evidence:** Server enforces only name ≥ 3 chars and bio ≤ 500; probe P4: `class_level:"Year 3"` accepted; a 500-char `department` accepted on SQLite; non-string `name` (`12345`) → 400 "Failed to update profile" (an `AttributeError` on `.strip()` swallowed by `except Exception`). Mentor matching maps `class_name` through a fixed `class_hierarchy` (`discovery.py` L280–284); unknown strings score level 0. Department is free-text and is grouped on by the leaderboard.
- **Problem:** No max length on name, no type checks, no canonical vocabulary for class level/department.
- **Impact:** On Postgres, over-length values raise `DataError` → generic "Failed to update profile"; non-canonical class levels silently break mentor matching; free-text departments fragment leaderboard groups.
- **Reproduction:** Probe P4; Postgres behaviour needs a Postgres run.
- **Recommended solution:** Validate types and lengths to the DB limits (and align the two tables' limits); restrict `class_level` to the onboarding set; for `department` allow the onboarding list plus "other" with trimmed/normalised text (Q5).
- **Implementation considerations:** Existing rows may contain non-canonical values — decide on mapping vs. leaving them.
- **Verification:** Tests for each rejected input; a Postgres-backed test for the length cases.

### PRF-09 — Profile study-schedule editor writes a column nothing reads
- **Severity:** Medium  **Category:** Product / Data Integrity  **Status:** Confirmed (probe P10)
- **Location:** `get_study_schedule`/`update_study_schedule` L1482–1534 use `User.study_schedule` (`models.py` L408); matching/compatibility/discovery use `OnboardingDetails.study_schedule` (L550).
- **Evidence:** Probe P10: onboarding had `{"monday":["morning"]}`; the profile endpoint returned an empty schedule for that user; after `POST /profile/study-schedule {"friday":["night"]}` the onboarding schedule was unchanged. A repo-wide search found no consumer of `User.study_schedule` besides profile routes.
- **Problem:** Two schedules; the editable one is disconnected from the product behaviour it implies.
- **Impact:** Users edit their schedule expecting better matches; nothing changes. Their onboarding schedule is shown nowhere and cannot be edited.
- **Reproduction:** Probe P10.
- **Recommended solution:** Single source of truth. Simplest: point the profile endpoints at `OnboardingDetails.study_schedule` (verify slot/day vocabulary matches onboarding's), then retire `User.study_schedule`.
- **Implementation considerations:** Data back-fill if some users already saved profile schedules; confirm the format used by the compatibility scorer before switching.
- **Verification:** Test: profile edit changes the compatibility inputs; GET reflects onboarding data.

### PRF-10 — Skill names containing `/` can be added but never removed; no skill validation
- **Severity:** Medium  **Category:** Bug  **Status:** Confirmed (probe P7)
- **Location:** `add_skill` L1322–1349, `remove_skill` L1355–1366 (`/profile/skills/<skill_name>`); `profile.api.js` uses `encodeURIComponent`.
- **Evidence:** Probe P7: `POST {"skill":"C/C++"}` → 201; `DELETE /profile/skills/C%2FC%2B%2B` → **404**; the list still contains `C/C++`. Add-time check is case-insensitive, whereas `add_learning_goal` duplicates are case-sensitive.
- **Problem:** The skill name is the URL identity; path converters reject `/`. No character-set validation on add.
- **Impact:** Permanent un-removable skill (the user can only clear it via an unrelated update). Also feeds PRF-01 (raw text).
- **Reproduction:** Probe P7.
- **Recommended solution:** Delete by request body or query string (`DELETE /profile/skills` with JSON `{"skill": …}`), keeping the old route for compatibility; validate add input (`str`, trimmed, ≤50, no angle brackets).
- **Implementation considerations:** Frontend must switch to the body form; keep both routes during rollout.
- **Verification:** Test add/remove of `C/C++`, `C#`, `.NET`, unicode names.

### PRF-11 — `/profile/me/data` omits `skills`, so the Edit modal always opens with no skills
- **Severity:** Medium  **Category:** Bug (frontend ↔ backend contract)  **Status:** Confirmed (probe P7 key check + code)
- **Location:** `get_my_profile_data` L41–101 (no `skills` key); `profile.delegation.js` L238 `buildSkillsList(d.skills || [])`.
- **Evidence:** Probe P7: `"skills" in /me/data` → `false`. A code comment in the frontend claims skills "is already part of the same profile payload" — it is part of `POST /profile/me`, which the frontend never calls.
- **Problem:** The modal's initial state is always `[]`.
- **Impact:** Users with saved skills see none in their own edit modal; their skills only reappear after adding another one (the add response returns the full list).
- **Reproduction:** Save a skill, reload, open Edit.
- **Recommended solution:** Add `skills` to `/profile/me/data` (additive, backwards-compatible).
- **Implementation considerations:** None beyond the response-size increase (≤10 short strings).
- **Verification:** Contract test asserting `/me/data` includes `skills`; Playwright check that the modal lists saved skills.

### PRF-12 — Frontend API wrappers discard the server's error messages
- **Severity:** Medium  **Category:** Product (UX) / Reliability  **Status:** Confirmed (code); not exercised in a browser.
- **Location:** `static/js/core/api.js` `handleResponse` (throws `Error(data.message)` on non-2xx); `static/js/profile/profile.api.js` (every wrapper `catch (e)` returns `null`); `profile.delegation.js` (`res?.message || 'Could not …'`).
- **Evidence:** The thrown message is caught and dropped; callers only see `null`, so `res?.message` is always `undefined` on failure.
- **Problem:** Validation messages the backend carefully produces ("Name must be at least 3 characters", "Maximum 10 skills allowed", "Skill already added", the pin-limit message) never reach the user. Rate-limit (429), auth and validation failures are indistinguishable.
- **Impact:** Users see generic "Failed to save" toasts and cannot self-correct.
- **Reproduction:** Submit a 2-character name; observe the generic toast.
- **Recommended solution:** In `profile.api.js`, return `{status:'error', message: e.message}` (or rethrow) instead of `null`; update call sites to show `res.message`.
- **Implementation considerations:** Touches ~15 small wrappers; no backend change.
- **Verification:** Unit tests of wrappers with mocked failing fetch; Playwright check of the displayed message.

### PRF-13 — "Marked Helpful" always shows 0; `posts_liked` is mislabeled; dead stat fields
- **Severity:** Medium  **Category:** Product / Bug  **Status:** Confirmed (probe P9)
- **Location:** `get_my_stats` L283 (`helpful_received = 0`), `get_own_profile` L776/L784, `view_profile` L1065/L1073 (`posts_liked`, `helpful_count = 0`); `profile.templates.js` L465 renders `${card('💡', e.helpful_received, 'Marked Helpful')}`.
- **Evidence:** Probe P9: `helpful_received` is `0`; `posts_liked` equals reactions **received** on the user's posts (author = 1, the actual liker = 0). The frontend consumes neither `posts_liked`, `helpful_count` nor `total_created_threads`.
- **Problem:** A hard-coded constant is displayed as a real metric; one field name says the opposite of what it counts.
- **Impact:** Misleading profile statistics.
- **Reproduction:** Probe P9.
- **Recommended solution:** Either compute the real value (the data model already has `total_helpful` on `User`) or remove the card; drop/rename dead fields in a compatible way (Q7).
- **Implementation considerations:** Removing response fields may affect undocumented clients — deprecate first.
- **Verification:** Test: values match seeded data; UI no longer shows a fake zero.

### PRF-14 — Per-request full scans of all users (suggestions ×2, popular skills)
- **Severity:** Medium  **Category:** Scalability / Performance  **Status:** Confirmed (code); load impact is a **Potential risk** (no load test run).
- **Location:** `help_suggestions` L566–652, `can_help_suggestions` L658–745 (load every `OnboardingDetails` joined to users, intersect in Python); `get_popular_skills` L1539–1569 (`User.query.filter(User.skills.isnot(None)).all()`, then count in Python) — **no `@token_required`** (probe P13: 200 unauthenticated).
- **Evidence:** Code reading; the popular-skills handler materialises full `User` rows (all columns) just to read one JSON column. No caching (the project's `cache_service` is not used here). Rate limit is `PUBLIC_READ` (300/min per IP).
- **Problem:** O(N users) memory and CPU per call, repeated on every instance, plus an unauthenticated amplifier.
- **Impact:** Latency and memory grow linearly with user count; an anonymous client can keep every worker busy within the limiter.
- **Reproduction:** Seed N users, time the endpoints (not run).
- **Recommended solution:** (a) `/skills/popular`: cache the aggregated result in Redis for a few minutes (this is shared by all users), and load only the `skills` column (`db.session.query(User.skills)`). (b) Suggestions: pre-filter in SQL (same department / overlapping subjects) and `LIMIT` candidates, and/or cache per-user for a short TTL; note these two endpoints currently have **no first-party caller** (Section 5) — if they stay unused, the simplest mitigation is to retire them (Q8). (c) Require auth on `/skills/popular` unless the public use case is intentional (Q8).
- **Implementation considerations:** Redis is already a dependency; TTL-based staleness is acceptable for popularity data. A normalised skills table is only warranted if per-skill queries are needed.
- **Verification:** Query-count/time test with 10k seeded users before/after; cache-hit test.

### PRF-15 — IP-keyed rate limits on authenticated read routes
- **Severity:** Medium  **Category:** Scalability / Reliability  **Status:** Confirmed (code) for the inconsistency; user impact is a **Potential risk** (deployment topology not verified).
- **Location:** `routes/student/profile.py` limiter decorators at L39, 105, 246, 329, 564, 656, 751, 956, 1024, 1480, 1538 (`key_func=ip_key`). `services/rate_limit_service.py`: `ip_key` docstring says it is "for pre-auth routes (login/register)", while `user_or_ip_key` says it "is used instead of ip_key() on every @token_required-protected route". `ProxyFix(x_proto=1, x_host=1)` at `app.py` L101.
- **Evidence:** 11 of the profile read routes are `@token_required` yet keyed by IP; `PUBLIC_READ` = 300/min. Opening one profile triggers several of these routes.
- **Problem:** Students behind one campus NAT/proxy share one bucket; conversely, if the proxy chain is mis-trusted, all users could share the proxy's IP.
- **Impact:** Intermittent 429s for legitimate users at peak (and, per PRF-12, the UI shows only a generic error).
- **Reproduction:** Not run; needs the real proxy topology.
- **Recommended solution:** Switch these decorators to `key_func=user_or_ip_key` (already used by the other 14 profile routes); keep `ip_key` for `/skills/popular` only if it stays unauthenticated.
- **Implementation considerations:** Confirm the number of trusted proxy hops for `get_remote_address` in production (`ProxyFix` hop count). If `REDIS_URL` is unset the limiter storage falls back (see `config.py` ~L171); I did not confirm the fallback target.
- **Verification:** Test that two users from one IP have independent buckets.

### PRF-16 — Read-modify-write races on JSON columns and counters
- **Severity:** Medium  **Category:** Reliability (concurrency)  **Status:** **Potential risk** — code-evidenced, **not reproduced** (the test DB is single-connection SQLite; no concurrency harness).
- **Location:** `pin_post` L1432–1452 (count `is_pinned` rows, then set flag — check-then-act); `add_skill` L1322, `remove_skill` L1355, `add_learning_goal` L1372, `remove_learning_goal` L1405 (read the JSON list, modify, assign back); `remove_learning_goal` addresses goals **by list index**.
- **Evidence:** Handlers read the list and commit the entire value; no row lock, version column, or atomic JSON operation. Index-based deletion uses the index from the client's last render.
- **Problem:** Two concurrent requests (two tabs, retries, two instances) can (a) exceed the 5-pin cap, (b) lose one of two simultaneous skill/goal additions (last write wins), (c) delete the wrong goal when the list changed between render and click.
- **Impact:** Quota bypass, lost user edits, wrong deletion — low frequency but silent.
- **Reproduction:** Two clients adding different skills at the same instant; two tabs, delete goal #1 in one then #1 in the other (second removes what is now the first item).
- **Recommended solution:** Lock the user row (`SELECT … FOR UPDATE` / `with_for_update()`) inside these handlers; for pins, count after locking the user row; delete goals by value (or stable id) instead of index.
- **Implementation considerations:** Row lock on `users` is short and per-user — low contention. Index-delete change needs a small frontend adjustment.
- **Verification:** Threaded test against Postgres (not SQLite) asserting cap and no lost update.

### PRF-17 — Avatar upload: no dimension cap or downscale; synchronous provider call in the request
- **Severity:** Medium  **Category:** Performance / Reliability  **Status:** Confirmed (probe P12) for no downscale; resource exhaustion is a **Potential risk**.
- **Location:** `upload_avatar` L423–477; `services/upload_validation_service.py` (`validate_and_normalize_image`: Pillow decode + re-encode); `services/storage.py` `upload_file` L334.
- **Evidence:** Probe P12: a 6000×6000 PNG was forwarded to the storage layer unchanged (119,587 bytes compressed, 6000×6000 px). Max request is 16 MB; no pixel-count limit beyond Pillow's defaults; `DecompressionBombError` is not specifically handled (falls into the generic `except`). The Cloudinary call is synchronous inside the worker. Avatars are displayed at ~28–72 px.
- **Problem:** Full-size originals are stored and served everywhere; a highly compressible huge-dimension image forces a large in-memory decode.
- **Impact:** Bandwidth/latency on every list showing avatars; possible memory spikes per request (bounded by the 30/min write limiter).
- **Reproduction:** Probe P12; for the memory risk, upload ~80 MP monochrome PNG (not run).
- **Recommended solution:** Set an explicit max pixel count (reject above, e.g., 25 MP) and downscale to a sane maximum (e.g., 512 px) before upload; or request a Cloudinary transformation / use delivery-time `w_…,h_…` URLs; set an explicit timeout on the upload call.
- **Implementation considerations:** Resizing changes stored bytes — fine for avatars. Animated GIFs are re-encoded without `save_all` (only the first frame survives) — decide whether to keep GIF support (Q4).
- **Verification:** Test that a >limit image is rejected and a large one is stored ≤ target dimensions.

### PRF-18 — FCM token can be bound to several accounts and is not cleared server-side on logout
- **Severity:** Medium  **Category:** Security (privacy) / Reliability  **Status:** Confirmed (probe P13) that two users can hold the same token; logout path **not verified end-to-end**.
- **Location:** `save_fcm_token` L511–541 (writes `current_user.fcm_token`, no uniqueness handling); `clear_fcm_token` L547–560 (called only from `push.js` `disablePush`, L92–105, 135); `routes/student/auth.py` has no `fcm` reference.
- **Evidence:** Probe P13: two users saved `"SAME-DEVICE-TOKEN"` → `User.query.filter_by(fcm_token=…).count() == 2`. Grep shows no caller of `disablePush` on logout.
- **Problem:** A device token belongs to one browser/device; after account switching on a shared device, both accounts point at it.
- **Impact:** Push notifications for account A can appear on a device now used by account B (message previews leak across accounts).
- **Reproduction:** Log in as A, enable push, log out, log in as B, enable push; send A a notification.
- **Recommended solution:** On save, null out the same token on any other user row (one UPDATE) so a token maps to one user; clear the token in the logout path (server-side).
- **Implementation considerations:** Multi-device-per-user is a separate (larger) design (token table) — only needed if users should receive pushes on several devices (Q10).
- **Verification:** Test: saving a token for B removes it from A; logout clears it.

### PRF-19 — Own-profile tabs can show the wrong content; a failed first load can never retry
- **Severity:** Medium  **Category:** Reliability (frontend)  **Status:** Confirmed (code); not reproduced in a browser.
- **Location:** `profile.delegation.js` (`renderStatsTab`, connections/badges/reputation renderers, `profile-filter-posts`); `profile.init.js` (`setInitialized(true)` set before data loads; `renderHeaderIfNeeded` swallows failures).
- **Evidence:** Tab renderers `await` a fetch and then call `setTabContent(...)` without re-checking the current tab, so a slow response for tab A overwrites tab B. Post-filter switches assign `profileState.posts` from whichever response arrives last. If `fetchProfileData()` returns `null`, the header shows "Failed to load profile" while `initialized` stays `true`, so re-activating the tab never retries.
- **Problem:** Missing stale-response guards and no retry path.
- **Impact:** Mismatched tab/content; user must reload the page after a transient failure.
- **Reproduction:** Throttle the network, click Stats then Connections quickly; or fail `/profile/me/data` once.
- **Recommended solution:** Capture a request token (current tab/filter) before awaiting and discard stale results; reset `initialized` (or add a Retry button) when the header load fails.
- **Implementation considerations:** Small, local JS changes.
- **Verification:** Playwright with delayed route mocks; assert final DOM matches the selected tab.

### PRF-20 — `learning_style` cannot be cleared; the request "succeeds" with no change
- **Severity:** Low  **Category:** Bug  **Status:** Confirmed (probe P6)
- **Location:** `update_academic_info` L397 (`if "learning_style" in data and data["learning_style"]:`).
- **Evidence:** Probe P6: `PUT {"learning_style":""}` → 200, `changes: []`, stored value still `"Visual"`. The frontend sends `''` when the field is emptied and then shows a success toast. `last_updated` is also bumped even when nothing changed (L412).
- **Problem:** Falsy values are treated as "not provided".
- **Impact:** Users cannot remove a learning style; the UI claims success.
- **Reproduction:** Probe P6.
- **Recommended solution:** Distinguish "absent" from "empty": if the key is present, store `None`/empty string. Only touch `last_updated` when `changes` is non-empty.
- **Implementation considerations:** Check any consumer assuming a non-empty `learning_style`.
- **Verification:** Test clearing the value.

### PRF-21 — "Active X ago" reflects last login, not activity
- **Severity:** Low  **Category:** Product  **Status:** Confirmed (code)
- **Location:** `services/auth_service.py` L136 (`user.last_active = now`) is the **only** writer found (inside the login-streak helper); read at `profile.py` L888, L1196, L1217.
- **Evidence:** Repo-wide search for assignments to `last_active` finds only that line. Sessions last days (refresh tokens), so a continuously active user can appear "inactive" for days. A separate online-status service exists but the profile does not use it.
- **Problem:** The label implies presence/recent activity; the data is "last login".
- **Impact:** Misleading status; combined with PRF-04, a privacy-relevant timestamp that is also inaccurate.
- **Reproduction:** Stay logged in for 3 days; view own profile from another account.
- **Recommended solution:** Either relabel ("Last signed in") or have the profile read the presence service (Q3).
- **Implementation considerations:** Writing `last_active` on every request is a write-amplification trade-off; prefer reading presence.
- **Verification:** Test that the displayed value tracks the chosen source.

### PRF-22 — Removing an avatar leaves the image on Cloudinary; provider error text is echoed to clients
- **Severity:** Low  **Category:** Security (privacy / information disclosure)  **Status:** Confirmed (code, probe P12)
- **Location:** `remove_avatar` L483–505 (only sets `current_user.avatar = None`); `services/storage.py` `delete_file` L379 exists but is not called; `upload_avatar` returns `f"Upload failed: {result.get('error')}"`.
- **Evidence:** Probe P12: a simulated provider failure message `Invalid api_key abc123 / internal detail` is returned verbatim to the client. Uploads reuse a per-user `public_id`, so at most one asset per user is left behind.
- **Problem:** "Remove" is DB-only; provider error strings may contain configuration detail.
- **Impact:** A removed (possibly sensitive) photo stays reachable by its URL; internal detail leakage on errors.
- **Reproduction:** Upload, remove, open the old URL.
- **Recommended solution:** Call `delete_file` for the user's avatar `public_id` on removal (best-effort, log failures); return a generic message and log the provider error server-side.
- **Implementation considerations:** Requires knowing the `public_id` (it is deterministic per user; confirm the exact naming in `upload_avatar`).
- **Verification:** Test with a mocked storage asserting `delete_file` is invoked.

### PRF-23 — Local-disk avatar fallback is dead and broken
- **Severity:** Low  **Category:** Maintainability (and a latent multi-instance hazard)  **Status:** Confirmed (probe P11)
- **Location:** `upload_avatar` L460–470 (`except ImportError` branch calling `save_file(file, "avatars", ALLOWED_IMAGE_EXT)`, L466).
- **Evidence:** Probe P11 (forcing the `ImportError`): response 200 "Avatar updated successfully" with `avatar_url:"/static/upload/avatars/uploads/avatars/<hash>_me.png"`; the file written had **0 bytes** (the stream was already consumed by validation, and the *validated* buffer is ignored); the URL path is doubled and uses `upload` vs `uploads`; GIF (allowed by the route) is rejected by `save_file` (400). The branch is only reachable if `services.storage` fails to import, which does not happen in a normal deployment (`cloudinary` is a requirement).
- **Problem:** Unreachable in practice, but if it ever triggers it reports success with a broken avatar, and local disk is not shared between instances.
- **Impact:** Latent; would break avatars and silently lose uploads on multi-instance deployments.
- **Reproduction:** Probe P11.
- **Recommended solution:** Remove the fallback (preferred) or make it fail loudly; do not keep a silent success path.
- **Implementation considerations:** Confirm no local/dev workflow depends on it (Q4).
- **Verification:** Test that a storage failure returns an error, never success.

### PRF-24 — Study schedule (and other lists) have no per-item bounds
- **Severity:** Low  **Category:** Security (abuse) / Data Integrity  **Status:** Confirmed (probe P10)
- **Location:** `update_study_schedule` L1500–1534.
- **Evidence:** Probe P10: `{"monday": ["morning"] * 50000}` → 200. Day keys and slot names are not checked against a vocabulary, and slots are not deduplicated.
- **Problem:** Arbitrary size/shape persisted in a JSON column and returned by the GET.
- **Impact:** Storage and response bloat; garbage inputs to any future consumer.
- **Reproduction:** Probe P10.
- **Recommended solution:** Validate day ∈ {monday…sunday}, slot ∈ the onboarding set, dedupe, cap list length. Apply the same pattern to `study_preferences`.
- **Implementation considerations:** Align the vocabulary with onboarding (see PRF-09).
- **Verification:** Test oversize/unknown key rejection.

### PRF-25 — Viewing your own profile through the public route returns the "private" view with `is_own_profile: false`
- **Severity:** Low  **Category:** Bug  **Status:** Confirmed (probe P14)
- **Location:** `view_profile` L1026–1246 (`"is_own_profile": False` hard-coded at L1236; no self check before the privacy gate at L1176).
- **Evidence:** Probe P14: with `set_profile_private=true`, `GET /profile/<own username>` → `type:"private"`, `is_own_profile:false`.
- **Problem:** The handler never compares `user.id` to `current_user.id`.
- **Impact:** A user opening their own `/profile/view/<username>` link sees the "private" placeholder; share links to yourself look broken.
- **Reproduction:** Probe P14.
- **Recommended solution:** If viewing self, return the full payload with `is_own_profile: true` (or redirect the page to the own-profile tab).
- **Implementation considerations:** Check the profile-view page already special-cases own username.
- **Verification:** Test self-view for public and private profiles.

### PRF-26 — `POST /profile/me` fails with a generic 400 when `last_active` is NULL
- **Severity:** Low  **Category:** Reliability  **Status:** Confirmed (probe P8). The endpoint has no first-party caller, which limits the impact.
- **Location:** `get_own_profile` L888 (`current_user.last_active.isoformat() if show_active else None`) vs `get_my_profile_data` (null-safe).
- **Evidence:** Probe P8: user with `last_active=None` → `POST /profile/me` → 400 "Failed to load profile"; `GET /profile/me/data` → 200.
- **Problem:** Missing `and current_user.last_active`; the error is a 400 for a server-side fault.
- **Impact:** Any API client using this endpoint fails for users who never logged in through the login helper.
- **Reproduction:** Probe P8.
- **Recommended solution:** Add the null check (or retire the endpoint — Q8).
- **Implementation considerations:** None.
- **Verification:** Test with `last_active=None`.

### PRF-27 — Unauthenticated page routes return raw JSON 401; username `homepage` is un-viewable
- **Severity:** Low  **Category:** Product (UX) / Bug  **Status:** Confirmed (probe P15 for 401; code for the collision)
- **Location:** `view_profile_page` L998 and `/profile/me` (GET) use `@token_required`; `homepage` L1573 is a static route that shadows the `<username>` converter.
- **Evidence:** Probe P15: unauthenticated `GET /student/profile/me` and `/student/profile/view/someone` → `401 application/json`; `/student/profile/homepage` → 200 HTML. Registration allows `homepage` (matches `^[a-z0-9]{3,20}$`; there is no reserved-name list).
- **Problem:** Browser navigation without a session shows JSON instead of a login redirect; a user named `homepage` cannot be viewed (the SPA's `GET /profile/homepage` receives HTML).
- **Impact:** Cosmetic/edge; shared links open to raw JSON for logged-out users.
- **Reproduction:** Probe P15.
- **Recommended solution:** Redirect unauthenticated HTML routes to the login page; add a reserved-username list (`homepage`, `me`, `update`, `avatar`, `skills`, …) at registration.
- **Implementation considerations:** Existing users already named `homepage` (if any) need handling.
- **Verification:** Test redirect and reserved-name rejection.

### PRF-28 — Contract/documentation drift and settings with no effect
- **Severity:** Low  **Category:** Maintainability / Product  **Status:** Confirmed (code)
- **Location:** `API_REFERENCE.md`, `openapi.yml`; `profile.py` L933–937 vs `get_own_profile` privacy defaults.
- **Evidence:** `fcm-token`, `profile/view/<username>` and the `show_in_activity_feed` setting are missing from the docs/OpenAPI. `send_weekly_notification` defaults to `False` in `/profile/me` (POST) but `True` in the settings GET/POST. No weekly-notification job exists (`BACKGROUND_JOBS.md` lists five jobs; none sends a weekly digest), and a repo search finds no consumer of `send_weekly_notification` or `set_dark_mode` apart from defaults/UI. The UI exposes a "weekly notification" toggle but no dark-mode toggle.
- **Problem:** A user-visible toggle with no behaviour; docs that do not match the API.
- **Impact:** Users believe they have configured something they have not; integrators work from stale docs.
- **Reproduction:** Toggle weekly notification; observe no job/email behaviour.
- **Recommended solution:** Decide whether weekly notifications will exist (Q6): implement or hide the toggle; unify defaults; update `API_REFERENCE.md`/`openapi.yml`.
- **Implementation considerations:** Do not remove `set_dark_mode` until its intended owner (theme persistence) is decided.
- **Verification:** Contract test comparing OpenAPI to the route map.

### PRF-29 — Business logic and queries live in the route module; large duplicated blocks
- **Severity:** Low  **Category:** Maintainability  **Status:** Confirmed (code)
- **Location:** `view_profile` L1026–1246 vs `get_own_profile` L753–918 (stats, badges, posts, heatmap computed twice); `help_suggestions` vs `can_help_suggestions` (mirror images).
- **Evidence:** The architecture document says routes should only parse the request and call one service; `profile.py` does not. The duplication already produced inconsistencies (PRF-04, PRF-13, PRF-26: the same stat computed with and without null checks/gating).
- **Problem:** Fixes must be applied in several places and drift.
- **Impact:** Recurrence of bugs of the PRF-04/13/26 kind.
- **Reproduction:** n/a.
- **Recommended solution:** **Not a refactor of the module.** Only extract a shared stats builder and a single privacy-gating helper as part of fixing PRF-04/13, and let the two suggestion endpoints share one function. Leave the rest as is.
- **Implementation considerations:** Keep response shapes identical.
- **Verification:** Existing + new tests pass unchanged.

### PRF-30 — Public profile view does the work before the privacy check
- **Severity:** Low  **Category:** Performance  **Status:** Confirmed (code); not benchmarked.
- **Location:** `view_profile` L1026–1246 (stats, badges, posts, thread counts and heatmap are queried before `if profile_private:` at L1176); `activity_heatmap` is returned but not rendered by `profile-view.templates.js`.
- **Evidence:** Roughly a dozen sequential queries per view; the private branch discards most results.
- **Problem:** Wasted queries for private profiles; an unused heatmap query on every public view.
- **Impact:** Latency and DB load that scale with profile-view traffic.
- **Reproduction:** Count queries with SQLAlchemy event logging (not run).
- **Recommended solution:** Move the privacy gate above the expensive queries; drop `activity_heatmap` from this response if no consumer needs it (Q7).
- **Implementation considerations:** Behaviour-preserving.
- **Verification:** Query-count assertions in a test.

### PRF-31 — Blanket `except Exception` returns 400 and logs without a traceback
- **Severity:** Low  **Category:** Reliability / Observability  **Status:** Confirmed (code)
- **Location:** Essentially every handler in `profile.py`; `grep exc_info` finds none in the file.
- **Evidence:** Handlers `logger.error(f"...{str(e)}")` and return `error_response(...)` (HTTP 400). Sentry's logging integration does capture ERROR logs (`services/error_tracking.py`), but the events carry no stack trace, and per-exception strings fragment grouping. Probe P4 (`name: 12345`) shows an `AttributeError` surfaced to the user as "Failed to update profile" with status 400.
- **Problem:** Server faults appear as client errors; dashboards keyed on 5xx do not see them; diagnosing needs the exact message.
- **Impact:** Slower incident response; wrong status codes for clients.
- **Reproduction:** Probe P4 (non-string name).
- **Recommended solution:** Use `current_app.logger.exception(...)` (or `exc_info=True`) and return 500 for unexpected exceptions; return 400 only for validation failures.
- **Implementation considerations:** Changing 400→500 for faults may affect clients that retry only on 5xx — desirable.
- **Verification:** Test that a forced exception yields 500 and a logged traceback.

### PRF-32 — Profile mixes a counter column and live counts for the same statistic
- **Severity:** Low  **Category:** Data Integrity  **Status:** Confirmed (code); drift is a **Potential risk**.
- **Location:** `get_my_profile_data`/`get_my_stats` use `User.total_posts`; `get_own_profile`/`view_profile` count `Post` rows live. `routes/student/posts/crud.py` L1179 (`total_posts = (total_posts or 0) + 1`) and L1515 (`-= 1`) are Python-side read-modify-write; `services/reconciliation_service.py` does not reconcile `total_posts`, `total_helpful`, `total_helps_given`, `total_helps_received`.
- **Evidence:** Code reading; no concurrency test.
- **Problem:** Two sources of truth for "posts"; the counter is not atomic and not self-healing.
- **Impact:** Own header and public profile can disagree after concurrent or out-of-band changes.
- **Reproduction:** Create two posts concurrently on one account (not run).
- **Recommended solution:** Pick one source (live `COUNT(*)` for profile, which is cheap with the existing `student_id` index, or add these counters to the reconciliation job and use atomic `UPDATE … SET total_posts = total_posts + 1`). The write path is in the posts feature, outside this audit.
- **Implementation considerations:** Coordinate with whoever owns the posts feature.
- **Verification:** Test that both endpoints return the same number after creation/deletion.

### PRF-33 — Other UI surfaces may keep the old avatar/name after a change
- **Severity:** Low  **Category:** Product  **Status:** **Potential risk** (not verified in a browser)
- **Location:** `static/js/shell/app-shell.js` (`updateShellUserInfo` populates the shell avatar/name at init); profile avatar/edit handlers refresh only the profile header.
- **Evidence:** I found no call from the profile handlers that updates the shell/nav copy or other cached user objects.
- **Problem:** Cached current-user data is not refreshed on change.
- **Impact:** The nav avatar/name may stay stale until reload.
- **Reproduction:** Change avatar, observe the nav avatar.
- **Recommended solution:** After a successful avatar/name change, refresh the shared current-user object and call the shell updater.
- **Implementation considerations:** Needs a quick look at all consumers of the cached user.
- **Verification:** Playwright check of the nav avatar after upload.

---

## 4. Horizontal Scalability Assessment

### 4.1 Current architecture and assumptions
- **Request handling is stateless.** Auth is a cookie JWT decoded per request (`token_required`), so any instance can serve any request. `profile.py` declares no module-level caches, locks, counters or queues, and the profile frontend keeps state only in the browser.
- **Shared state lives in external systems:** Postgres (all profile data), Redis (rate-limit counters when `REDIS_URL` is set; `config.py` ~L171), Cloudinary (avatars). The profile feature has **no background jobs, queues or WebSockets** of its own.
- **Idempotency / crash safety.** Each write handler is a single transaction (`update_profile`, skills, goals, schedule, privacy, pin/unpin) and replays are harmless (replace-all or duplicate-checked). Avatar upload uses a deterministic per-user `public_id`, so a crash between the Cloudinary call and the DB commit leaves at most the same asset that the next upload overwrites. An instance dying mid-request therefore does not corrupt profile data.

### 4.2 Instance-local state / coordination
None found in the live code path. The single local-disk dependency is the **dead avatar fallback** (PRF-23): not reachable in a normal deployment, and would be wrong across instances if it ever triggered.

### 4.3 Concerns — current vs future

| # | Concern | What it does today | Why it matters with N instances | Realistic failure | Current or future? | Recommended solution | Trade-offs / infra |
|---|---|---|---|---|---|---|---|
| S1 (PRF-14) | Per-request full scans | `/skills/popular` loads every `User` row; both suggestion endpoints load every `OnboardingDetails` and intersect in Python | Every instance repeats the same O(N) work; no shared cache | Latency/memory growth; anonymous traffic keeps workers busy | **Current** for `/skills/popular` (unauthenticated); future for suggestions as N grows | Redis cache (TTL minutes) for popular skills + column-only query; SQL pre-filter + `LIMIT` (or retire the unused suggestion endpoints) | Redis already present; small staleness |
| S2 (PRF-15) | IP-keyed limits on authenticated reads | 11 routes use `ip_key` | Users sharing an IP (campus NAT) share one 300/min bucket; behind more proxy hops the key could be the proxy | Spurious 429s at peak | **Current** (depends on topology — unverified) | `user_or_ip_key` | None |
| S3 (PRF-16) | Read-modify-write on JSON lists, pin cap | Whole list read, changed, committed; pin cap via count-then-set | Two instances can process the same user concurrently | Lost skill/goal, 6 pins, wrong goal removed | Present but rare; grows with multi-tab/multi-instance use | `SELECT … FOR UPDATE` on the user row; delete goals by value | Short row lock, per user |
| S4 (PRF-32) | Python-side counter updates | `total_posts = total_posts + 1` in app memory (posts feature) | Concurrent requests overwrite each other; not reconciled | Counter drift between header and live counts | Present, rare | Atomic `UPDATE … + 1` or add to reconciliation job; or use live counts | Touches posts feature |
| S5 (PRF-17) | Synchronous Cloudinary call in the request | Upload blocks a worker; no explicit timeout | Slow provider ties up workers on every instance; if the user row was loaded earlier in the request the DB transaction may stay open during the call (**inference — pool behaviour not verified**) | Worker/pool exhaustion during provider slowness | Future/low | Explicit timeout; commit/close the read transaction before the external call; downscale before upload | Minor code change |
| S6 (PRF-18) | `fcm_token` is a single column per user, not unique | A token can sit on multiple users | Not instance-related, but any instance may deliver to the wrong account | Cross-account push | Current | Null the token elsewhere on save; clear on logout | Multi-device design only if required |
| S7 | Limiter storage fallback | Redis when `REDIS_URL` is set | If unset, counters may be per-process | Limits multiplied by instance count | Deployment-dependent (**not verified**) | Ensure `REDIS_URL` in every environment that runs >1 instance | None |

### 4.4 Immediate vs future

- **Immediate:** S1 (`/skills/popular`), S2, S6.
- **Before adding instances/traffic:** S3, S4, S5, S7.
- **Not needed:** distributed locks, queues or new infrastructure — row locks and a Redis TTL cache are sufficient for this feature.

---

## 5. Data Usage and Cleanup Report

Classification key: **1** Confirmed unused · **2** Potentially unused — requires verification · **3** Required indirectly — retain · **4** Redundant — can be consolidated.

| Resource | Current usage | Evidence | Class. | Recommendation |
|---|---|---|---|---|
| Table `profile_change_history` / model `ProfileChangeHistory` (`models.py` L1680) | None | Repo-wide search (py/js/html/tests/migrations) finds only the class definition; table is created by `db.create_all()` (`app.py` L629) | **1** | Decide: wire up audit writes for profile edits, or drop. Nothing depends on it. Check the production row count is 0 first. Dropping needs an explicit migration (schema is not Alembic-driven — Q11). |
| `student_profiles.date_of_birth` | Set to `None` at registration; never read | Search of reads/writes (`auth.py`) | **1** | Drop after confirming all NULL in production (data-minimisation: PII-shaped column). |
| `student_profiles.guardian_name`, `guardian_contact` | Never written or read | Search | **1** | Drop (same check). |
| `student_profiles.registered_at` | Default only; never read | Search | **2** | Verify no admin SQL/report uses it (`users.joined_at` also exists) before dropping. |
| `student_profiles.status` | Written `"active"` once at registration; never read | Search | **2** | Verify no admin/ops use; otherwise drop. |
| `student_profiles.username`, `student_profiles.pin` | Written at registration; no reads found | Search; duplicates `users.username` and `users.pin` (a credential hash) | **4** | Stop writing, then drop after verifying no scripts/admin queries read them. Removes a duplicate copy of the PIN hash. |
| `student_profiles.full_name` | Kept in sync on name update; never read | Search | **4** | Consolidate onto `users.name`. |
| `student_profiles.department`, `class_name` | Read by feed, leaderboard, discovery (`discovery.py` L284), search | Code references | **3** | Keep — this is the authoritative copy (PRF-07). |
| `onboarding_details.department`, `class_level` | Read by `/profile/academic-info` and onboarding matching | `profile.py` L331; `auth.py` ~L735 | **4** (duplicate of `StudentProfile`) | Keep until consumers are repointed; then consolidate. No data loss if `StudentProfile` wins. |
| `users.study_schedule` | Read/written only by profile endpoints | Probe P10; no matching consumer | **4** | Repoint profile endpoints at `onboarding_details.study_schedule`, back-fill non-empty profile schedules where onboarding is empty, then drop. |
| `onboarding_details.study_schedule` | Compatibility/discovery inputs | Code references | **3** | Keep; make it the single source. |
| `users.skills` and index `idx_users_skills_gin` | Profile, search filter (`search_service.py` L188–192) | Code references | **3** | Keep. |
| `users.learning_goals`, `users.privacy_settings`, `users.fcm_token` | Profile; `fcm_token` used by `services/push_notifications.py` | Code references | **3** | Keep. |
| `privacy_settings.set_profile_private` | `profile.py`, `search_service.py` L218, `activity_service.py` L212 | Code references | **3** | Keep. |
| `privacy_settings.show_active_status` | Only `view_profile` | Code references | **3** | Keep (fix PRF-04). |
| `privacy_settings.show_in_activity_feed` | `activity_service` | Code references | **3** | Keep; add to docs. |
| `privacy_settings.send_weekly_notification` | UI toggle only; no job or reader | Search; `BACKGROUND_JOBS.md` has no weekly digest | **2** | Verify nothing outside the repo (cron/external) reads it; implement the feature or hide the toggle (Q6). |
| `privacy_settings.set_dark_mode` | Written by the API; no UI control, no reader | Search | **2** | Decide owner of theme persistence (Q6) before removal. |
| `POST /profile/me` | No first-party caller (frontend uses `/profile/me/data`) | `profile.api.js` | **2** | May serve external clients (documented in OpenAPI) — confirm, then deprecate or fix PRF-26 (Q8). |
| `GET /profile/help/suggestions`, `/profile/can-help/suggestions` | No first-party caller (frontend calls `/threads/help/suggestions`) | Search of `static/` and `templates/` | **2** | Confirm external use; retire or fix PRF-03/14 (Q8). |
| API field `posts_liked`, `helpful_count`, `total_created_threads` | No frontend consumer; `posts_liked` mislabeled | Probe P9; search of `static/js` | **2** | Deprecate/rename (Q7). |
| API field `helpful_received` | Consumed by Stats tab, always 0 | Probe P9 | **1** (as a real metric) | Compute or remove the card (PRF-13). |
| API field `activity_heatmap` in `view_profile` and `POST /profile/me` | Not rendered by `profile-view`; own profile uses `/analytics/activity-heatmap` | `profile.api.js` L50; template search | **2** | Drop from `view_profile` once no consumer is confirmed (PRF-30). |
| `/profile/me/data` fields `onboarding`, `joined_at`, `last_active`, `help_streak`, `id` | No consumer found in `profile.*.js` | Search of `profileData.*`/header template | **2** | Verify other scripts do not read them; trim or keep for API stability. |
| Frontend state (`profileState.*`) | Referenced by handlers/templates | Read in full | **3** | No purposeless state found. |
| Data collected but not persisted | `learning_style` cleared in the modal is silently ignored | Probe P6 | — | Fix PRF-20. |
| Local avatar fallback (`upload_avatar` L460–470) | Unreachable in normal deployment; broken | Probe P11 | **1** (dead code) | Remove (PRF-23). |

**Dependencies and migration notes for every removal/consolidation**
- I found no foreign keys, relationships or code paths that point at the listed *columns*; `ProfileChangeHistory` itself has no references in code. Confirm against a production schema dump and any ops/admin SQL (not available to me) before dropping anything.
- The repository uses `db.create_all()` and has two Alembic revisions, so `create_all` will **not** drop anything: removals require explicit `ALTER/DROP` statements or new Alembic revisions (Q11).
- Data preservation: only `users.study_schedule` (back-fill into onboarding) and `StudentProfile.department/class_name` ↔ `OnboardingDetails` (align) carry user-entered data; everything else listed as unused should be empty — **verify counts in production before dropping**.

---

## 6. End-to-End Data Consistency Report

| # | Path | Where it breaks | Evidence | Recommended correction |
|---|---|---|---|---|
| E1 | Edit department/class → `PATCH /profile/update` → `StudentProfile` → header (OK) → **`/academic-info` (stale)** | Two stores, one writer | Probe P5 | PRF-07: single source / write both |
| E2 | Add skill → list returned → local state; **reload → `/me/data` has no `skills` → modal empty** | Payload omission | Probe P7 | PRF-11: add `skills` to `/me/data` |
| E3 | Add `C/C++` → 201 → **remove → 404** | URL-path identity | Probe P7 | PRF-10 |
| E4 | Edit study schedule → `User.study_schedule` → **matching reads `OnboardingDetails.study_schedule`** | Wrong column | Probe P10 | PRF-09 |
| E5 | Privacy modal (4 keys) → server expects 5, defaults the rest → **`set_dark_mode` reset, partial calls clobber** | Replace-all semantics | Probes P1/P1b | PRF-02 |
| E6 | `show_active_status=false` → UI shows "inactive" → **API still returns `last_active`** | Flag only toggles a boolean | Probe P2 | PRF-04 |
| E7 | Private profile → message "Connect to view details" → **connected users still see private view** | Missing relationship check | Probe P3 | PRF-05 |
| E8 | Clear learning style → UI toast "saved" → **value unchanged** | Falsy treated as absent | Probe P6 | PRF-20 |
| E9 | Validation error on server → `api.js` throws → wrapper returns `null` → **generic toast** | Message dropped | Code | PRF-12 |
| E10 | Stats tab "Marked Helpful" ← hard-coded `0`; `posts_liked` ← reactions received | Constants and mislabels | Probe P9 | PRF-13 |
| E11 | Header `total_posts` ← counter; `/profile/me` & public view ← live count | Mixed sources | Code | PRF-32 |
| E12 | Avatar upload → header updated; **nav/shell avatar not refreshed** (unverified); remove → asset stays on Cloudinary | Missing cache refresh / delete | Code | PRF-33, PRF-22 |
| E13 | "Active 3h ago" ← `last_active` written **only at login** | Semantics | Code | PRF-21 |
| E14 | Server fault (e.g. non-string `name`) → **HTTP 400** "Failed to update profile" | Blanket `except` | Probe P4 | PRF-31 |
| E15 | Logged-out navigation to `/profile/me` → **raw JSON 401** | API-style auth on a page route | Probe P15 | PRF-27 |

Success/failure mismatches identified: E8 (UI success, nothing changed), E9/E14 (failure shown with the wrong reason), PRF-23 fallback (server reports success with a 0-byte file — only if triggered).

---

## 7. Testing Gaps

**Existing coverage.** `tests/integration/test_profile_routes.py` (37 tests, **all pass**) covers academic info, visibility settings, profile update, skills, learning goals, pin/unpin, study schedule. `test_file_upload_validation.py` has one avatar-upload test. The file's own header states the suggestion endpoints are deliberately out of scope. A search of `tests/` finds **no references** to: `GET /profile/<username>`, `/profile/view/<username>`, `POST /profile/me`, `/profile/my-posts`, `/profile/my-stats`, `/profile/fcm-token`, `DELETE /profile/avatar`, and the profile suggestion endpoints.

Existing tests also **pass while the bugs above exist** (e.g. visibility tests send all keys; no test sends a partial payload), and run on SQLite, which does not enforce `VARCHAR` lengths or row locks.

| # | Missing test | Behaviour to verify | Level |
|---|---|---|---|
| T1 | Partial `visibility-settings` payload | Omitted keys unchanged; non-bool → 400; stored `"false"` normalised (PRF-02) | Integration |
| T2 | `view_profile` privacy matrix | stranger/connected/pending/blocked/self × public/private; `last_active`/`login_streak` hidden when opted out (PRF-04/05/06/25) | Integration |
| T3 | XSS rendering | Payload names/departments in Connections tab, badge details, own header produce no DOM elements/handlers (PRF-01) | E2E (Playwright/jsdom) |
| T4 | Input validation | `name` type/length, `department`/`class_level` vocabulary, subject/skill/goal item type/length, angle brackets (PRF-01/03/08/10/24) | Unit + integration |
| T5 | Poisoned-row resilience | A malformed `strong_subjects` row does not fail suggestions/discovery for others (PRF-03) | Integration |
| T6 | Contract test | `/me/data` includes `skills`; OpenAPI ↔ route map parity (PRF-11/28) | Contract |
| T7 | Skills with `/`, `#`, `.`, unicode | Add and remove round-trip (PRF-10) | Integration |
| T8 | Department/class consistency | Update then read via both endpoints (PRF-07) | Integration |
| T9 | Schedule consumer | Profile edit changes compatibility input (PRF-09) | Integration |
| T10 | Concurrency (Postgres) | Parallel pin → ≤5; parallel add-skill → both kept; goal delete by value (PRF-16) | Concurrency (Postgres, threads) |
| T11 | Avatar | >pixel-limit rejected; downscale; GIF policy; storage failure → error (never success); removal calls `delete_file` (PRF-17/22/23) | Integration (mocked storage) |
| T12 | FCM | Token saved for B is removed from A; logout clears (PRF-18) | Integration |
| T13 | Rate-limit keys | Two users on one IP have independent buckets (PRF-15) | Integration |
| T14 | Null `last_active` | `POST /profile/me` returns 200 (PRF-26) | Unit |
| T15 | Error mapping | Forced exception → 500 + logged traceback; validation → 400 (PRF-31) | Integration |
| T16 | Frontend wrappers | Failed fetch surfaces the server message (PRF-12); stale tab response discarded (PRF-19) | Unit/E2E |
| T17 | Failure & recovery | Redis down (limiter behaviour), DB error mid-request → rollback, no partial profile update | Integration |
| T18 | Stats | `helpful_received` source; counter vs live agreement (PRF-13/32) | Integration |

No tests were written or modified during this audit.

---

## 8. Recommended Implementation Plan

Priority order follows your list: correctness/data → security/authorization → reliability/concurrency → scalability → product/E2E consistency → performance → cleanup. Each milestone is independently shippable.

### M1 — Privacy correctness (small, backend-only)
- **Objective:** Stop privacy settings being clobbered or leaked through the API.
- **Issues:** PRF-02, PRF-04 (and decide Q3).
- **Files:** `routes/student/profile.py` (`visibility_settings`, `view_profile`).
- **Implementation:** Merge-update with boolean validation and allow-listed keys; null `last_active` (and, if decided, `login_streak`) when `show_active_status` is false.
- **DB/infra:** One-off data fix normalising stored string booleans.
- **Tests:** T1, T2 (partial).
- **Dependencies:** Q3, Q6.
- **Risks:** Clients depending on reset-to-default (none known).

### M2 — XSS and input hygiene
- **Objective:** Remove the stored-XSS path and bound what profile fields may contain.
- **Issues:** PRF-01, PRF-03, PRF-08, PRF-10 (validation part), PRF-24.
- **Files:** `static/js/profile/profile.templates.js` (+ shared `escapeHtml` util); `routes/student/profile.py` (`update_profile`, `add_skill`, `add_learning_goal`, `update_academic_info`, `update_study_schedule`); defensive guards in `connections/discovery.py`, `connections/crud.py`, `threads/discovery.py` (outside the feature, minimal guards only).
- **Implementation:** Escape every interpolated value at render; server-side type/length/charset validation; optional CSP in report-only mode.
- **DB/infra:** Scan/cleanup existing rows for markup and non-string subjects.
- **Tests:** T3, T4, T5.
- **Dependencies:** Q5, Q9.
- **Risks:** Over-strict name rules rejecting legitimate names; CSP breaking inline handlers.

### M3 — Device, asset and visibility policy
- **Objective:** Fix cross-account push delivery, asset retention and block behaviour.
- **Issues:** PRF-18, PRF-22, PRF-06 (if approved).
- **Files:** `profile.py` (`save_fcm_token`, `remove_avatar`, `view_profile`), logout path in `auth.py`.
- **Implementation:** Unique-per-user token handling and logout clearing; best-effort Cloudinary delete; generic provider error message; block check in `view_profile`.
- **DB/infra:** None (optional token table only if multi-device is wanted — Q10).
- **Tests:** T2, T11 (partial), T12.
- **Dependencies:** Q2, Q4, Q10.
- **Risks:** Logout-path change touches the auth feature.

### M4 — Reliability and concurrency
- **Objective:** Make writes safe under concurrent requests and failures diagnosable.
- **Issues:** PRF-16, PRF-26, PRF-31, PRF-19.
- **Files:** `profile.py` (skills, goals, pin handlers, error handling), `profile.delegation.js`, `profile.init.js`.
- **Implementation:** `with_for_update()` on the user row; delete goals by value; null checks; `logger.exception` and correct status codes; stale-response guards and retry for first load.
- **DB/infra:** None.
- **Tests:** T10 (Postgres), T14, T15, T16.
- **Dependencies:** A Postgres test target.
- **Risks:** Frontend change for goal deletion.

### M5 — Horizontal-scalability hygiene
- **Objective:** Remove per-request full scans and unfair rate limiting.
- **Issues:** PRF-15, PRF-14, PRF-17, PRF-23.
- **Files:** `profile.py` decorators and handlers, `services/upload_validation_service.py` use, cache via existing `cache_service`.
- **Implementation:** `user_or_ip_key` on the 11 routes; cache `/skills/popular`; SQL pre-filter/limit (or retire) suggestion endpoints; pixel cap + downscale + upload timeout; remove the local fallback.
- **DB/infra:** Redis cache keys only.
- **Tests:** T11, T13, plus a query-count test.
- **Dependencies:** Q4, Q8.
- **Risks:** Cache staleness (acceptable); confirm proxy-hop count.

### M6 — Product and end-to-end consistency
- **Objective:** Make what the user sees match what is stored and used.
- **Issues:** PRF-05, PRF-07, PRF-09, PRF-11, PRF-12, PRF-13, PRF-20, PRF-21, PRF-25, PRF-27, PRF-32, PRF-33.
- **Files:** `profile.py`, `profile.api.js`, `profile.delegation.js`, `profile.templates.js`, `profile-view.*`, `routes/student/auth.py` (reserved names).
- **Implementation:** As described per finding; keep response shapes additive.
- **DB/infra:** Back-fill: align `OnboardingDetails` department/class to `StudentProfile`; copy non-empty `users.study_schedule` to onboarding when onboarding is empty.
- **Tests:** T6, T7, T8, T9, T16, T18.
- **Dependencies:** Q1, Q3, Q5, Q7.
- **Risks:** Behaviour change visible to users (private-profile access).

### M7 — Cleanup and maintainability
- **Objective:** Remove unused data and reduce duplication that caused the bugs.
- **Issues:** Section 5 items, PRF-28, PRF-29, PRF-30.
- **Files:** `models.py`, new migration(s), `profile.py`, docs.
- **Implementation:** Drop confirmed-unused columns/table after production verification; deprecate dead API fields; update `API_REFERENCE.md`/`openapi.yml`; share one stats builder and privacy gate; reorder queries after the privacy gate.
- **DB/infra:** Migrations (strategy per Q11).
- **Tests:** Existing + T6.
- **Dependencies:** Q7, Q8, Q11.
- **Risks:** Dropping data that ops scripts use — verify first.

---

## 9. Questions and Decisions Required

| # | Question | Why it matters | Options |
|---|---|---|---|
| Q1 | Should **accepted connections** see a private profile's full details? | Decides PRF-05 (UI promises it; code does not do it) and consumers in search/activity | (a) Yes, connected users see full profile; (b) No — change the copy |
| Q2 | Should a **block** hide a profile from the blocked user? | PRF-06; consistency with messaging, where blocks are enforced | (a) Return "not found" for blocked pairs; (b) Keep current behaviour and document it |
| Q3 | What should "Active X ago" mean, and should `login_streak` be visible when activity is hidden? | PRF-04/21 | Presence service vs last login relabelled; hide or show streak |
| Q4 | Avatar policy: keep **GIF** support? delete the Cloudinary asset on removal? remove the local fallback? max dimensions? | PRF-17/22/23 | GIF→PNG/static only; downscale to 512 px; delete on removal (best effort) |
| Q5 | Is there a **canonical vocabulary** for department, class level and subjects? How to treat existing free-text values? | PRF-03/07/08; mentor matching, leaderboard grouping | Fixed list + "other"; normalise/map existing; leave existing as is |
| Q6 | Will **weekly notifications** exist? Where should **dark mode** be persisted? | PRF-02/28: two settings have no behaviour | Implement; hide the toggle; remove the key |
| Q7 | May dead/misleading API fields (`posts_liked`, `helpful_count`, `total_created_threads`, `activity_heatmap`, unused `/me/data` fields) be removed? Are there **external API consumers** (mobile, scripts)? | PRF-13/30 and Section 5 | Deprecate for a release then remove; keep for compatibility |
| Q8 | Keep, fix or retire `POST /profile/me`, the two profile suggestion endpoints, and the **unauthenticated** `/skills/popular`? | PRF-14/26/03 | Retire unused; fix and cache; require auth for popular skills |
| Q9 | Is a **Content-Security-Policy** acceptable (inline handlers/CDNs in use)? | PRF-01 defence in depth | Report-only first, then enforce; skip |
| Q10 | Should one user receive push on **multiple devices**? | PRF-18 | Single token (simple fix); token table |
| Q11 | Migration strategy: the schema is built by `db.create_all()` with two Alembic revisions. Should removals/back-fills go through Alembic, manual SQL, or a one-off script? | Section 5, M6, M7 | Adopt Alembic for these changes; manual SQL with a rollback plan |

No product or architectural decision above has been assumed in this report; recommendations that depend on one say so.

---

## 10. Final Recommendation

**Must be fixed before production**
- PRF-01 (stored XSS), PRF-02 (privacy clobbering), PRF-03 (poisoned lists), PRF-04 (activity data leak).
- PRF-18 (cross-account push) is small and should ship with them.

**Should be fixed soon**
- PRF-15, PRF-14 (limits and full scans), PRF-16/17/19 (concurrency, avatar resources, frontend races).
- User-visible breakage: PRF-05, 07, 09, 10, 11, 12, 13.
- PRF-06 once Q2 is answered.

**Can safely be postponed**
- PRF-20, 21, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33 (Low); the data consolidation in Section 5 beyond the confirmed-unused items.

**Should be removed (after your approval and a production check)**
- `ProfileChangeHistory`/`profile_change_history`; `StudentProfile.date_of_birth`, `guardian_name`, `guardian_contact`; the local avatar fallback; later: duplicate `StudentProfile.username/pin/full_name` and `User.study_schedule` after consolidation.

**Requires your approval or clarification:** Q1–Q11, especially Q1, Q2, Q5, Q6, Q8 and Q11.

**Recommended order:** M1 → M2 → M3 → M4 → M5 → M6 → M7. M1 and the validation half of M2 are small and remove most of the risk.

---

## Appendix A — Verification log

**Existing tests:** `pytest tests/integration/test_profile_routes.py` → **37 passed**.

**Probes** (throwaway pytest file in `/tmp/audit_probes`, reusing the project's fixtures; SQLite + fakeredis; **17 passed**; no repository files were changed):

| Probe | Observation |
|---|---|
| P1 | After stored `{private:true, show_active:false, …}`, sending only `{"set_dark_mode":true}` → `set_profile_private:false, show_active_status:true, send_weekly_notification:true, show_in_activity_feed:true`. `{"set_profile_private":"false"}` stored as `'false'`. |
| P1b | UI's four-key payload leaves `set_dark_mode:false` (was true). |
| P2 | `show_active_status=false` → response still has `last_active` (ISO timestamp), `login_streak`, and `active_status:false`. |
| P3 | Private profile: `type:"private"`; pinned titles included (documented behaviour). Connected viewer still `type:"private"`, `connection_status:"connected"`. |
| P3b | Blocked user viewing blocker → `200`, `type:"public"`, stats present, `connection_status:"blocked"`. |
| P4 | `name:"\"><img src=x onerror=alert(1)>"`, `bio:"<script>alert(1)</script>"` stored verbatim; `name:12345` → 400 "Failed to update profile"; `class_level:"Year 3"` accepted; 500-char `department` accepted (SQLite). |
| P5 | After `department→Biology`: `/me/data` = Biology, `/academic-info` = Physics. |
| P6 | `learning_style:""` → 200, `changes:[]`, value unchanged. Poisoned `strong_subjects` + 100k-char subject → 200; victim's `/profile/help/suggestions` → 400; control `/can-help/suggestions` → 200. |
| P7 | `C/C++` add → 201; delete (`%2F`) → 404; list unchanged. `/me/data` has no `skills` key. |
| P8 | `last_active=None`: `POST /profile/me` → 400 "Failed to load profile"; `/me/data` → 200. |
| P9 | `posts_liked`: author (1 reaction received) = 1, the liker = 0; `helpful_count` = 0; `/my-stats` `helpful_received` = 0. |
| P10 | Onboarding `{"monday":["morning"]}`: profile GET → `[]` for monday; after profile POST the onboarding schedule unchanged; 50,000-slot list → 200. |
| P11 | Forced `ImportError` fallback: 200 "Avatar updated successfully", `avatar_url` `/static/upload/avatars/uploads/avatars/<hash>_me.png`, file size **0 bytes**; GIF → 400. |
| P12 | 6000×6000 PNG (119,587 bytes) forwarded unchanged to storage; provider error text returned to the client verbatim. |
| P13 | `/student/skills/popular` unauthenticated → 200; same FCM token saved by two users → 2 rows. |
| P14 | Own username on the public route with private profile → `type:"private"`, `is_own_profile:false`. |
| P15 | Unauthenticated `/profile/me` and `/profile/view/x` → 401 JSON; `/profile/homepage` → 200 HTML. |

Running the tests created git-ignored `__pycache__`/pytest artefacts inside the clone; `git status` shows no tracked changes other than this report.

## Appendix B — Not verified / limitations

- **No browser execution.** XSS (PRF-01), tab races (PRF-19), error-message loss (PRF-12), stale nav avatar (PRF-33) are established from code and API probes only.
- **No Postgres run.** Tests used SQLite; `VARCHAR` length failures (PRF-08), row-locking behaviour and the concurrency findings (PRF-16, PRF-32) were not reproduced.
- **No load testing.** PRF-14/15/17 impact is inferred from code.
- **Deployment topology unknown.** Number of trusted proxy hops, whether `REDIS_URL` is set in every environment, and the production row counts for the "unused" columns were not available.
- **Outside scope, not examined:** whether `search_service`'s `json_array_contains` is case-insensitive for skills (it lower-cases the query; stored skills keep their case); the registration path's own sanitisation; the notifications routes that share the `/profile/` URL prefix.
- **Logout/FCM (PRF-18):** I found no caller of `disablePush` on logout and no `fcm` reference in `auth.py`; I did not exercise a full logout in a browser.

## Appendix C — Method

Read first: `README.md`, `ARCHITECTURE.md`, `API_REFERENCE.md`, `BACKGROUND_JOBS.md`, `SECURITY.md`. Then traced every route in `routes/student/profile.py` against `static/js/profile/*`, `static/js/profile-view/*`, `static/js/core/api.js`, `static/js/push/push.js`, `models.py`, `services/{auth,storage,upload_validation,rate_limit,search,activity,push_notifications,reconciliation,error_tracking}*.py`, and the consuming code in `routes/student/connections/*` and `routes/student/threads/discovery.py`. Reference counts for the data-usage table were traced across `.py`, `.js`, `.html`, tests and migrations rather than a single search.
