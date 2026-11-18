"""
services/ai_provider_service.py

AI access layer. Moved out of learnora.py per Document 1 §2.4 — this is
the module every blueprint that needs an AI call now imports from, instead
of reaching into learnora.py's internals.

GATEWAY MIGRATION: this module no longer talks to Gemini/Groq/Cohere/
Cloudflare/Mistral/OpenRouter directly with per-provider API keys. Every
call now goes through one self-hosted llm-gateway instance
(https://github.com/Devpaul-01/llm-gateway) over its OpenAI-compatible
`POST {LLM_GATEWAY_BASE_URL}/v1/chat/completions` endpoint, authenticated
with a single project-scoped gateway key (`LLM_GATEWAY_API_KEY`). The
gateway itself now owns everything that used to live here: provider/key
rotation, circuit breaking, cooldowns, and model discovery, across
whichever real providers are configured on that gateway project. This
module's job shrinks to: build an OpenAI-shaped request (optionally with
a `fallback_models` chain for the gateway to walk server-side), send it to
the one gateway endpoint, and classify the one HTTP response that comes
back.

Owns:
  - ProviderCallError, classify_provider_error — same four-category
    taxonomy as before (KEY_FAULT / PROVIDER_TRANSIENT / BAD_MODEL /
    NON_RETRYABLE), re-mapped against the GATEWAY's own error surface
    (see apierror.go / providers/errors.go in llm-gateway) rather than
    each vendor's raw API — see the status-set comments below for exactly
    what changed and why.
  - MultiProviderManager — now a thin client wrapper around the one
    gateway endpoint (class name kept for backward compatibility with
    existing imports/tests). Owns the short client-side cooldown that
    keeps this process from hammering a gateway that's currently down,
    Redis-backed and cross-instance via the same AI_PROVIDER_REDIS_STATE_ENABLED
    kill switch as before.
  - StudyAssistant (per-conversation streaming assistant; stream_response
    classifies pre-stream/header-stage failures via classify_provider_error
    the same way call_ai_response does)
  - _call_provider_sync (non-streaming, ORIGINAL name/contract preserved
    exactly — used by websocket_threads.py's Thread-WebSocket meeting-
    notes/action-AI background callers) and _call_provider_sync_raising
    (the classified engine underneath it, used directly by
    call_ai_response)
  - call_ai_response — same external contract (text, diagnostics) as
    before; internally now a single gateway call per attempt, since the
    gateway already walks its own fallback_models chain server-side.
  - clean_ai_response, generate_conversation_title
  - Model catalog (GATEWAY_TEXT_MODELS / GATEWAY_VISION_MODELS) — the
    `fallback_models` chain sent to the gateway on every request.

Per Document 2 §2's layering rule, this module has zero Flask dependency:
no `from flask import ...`, no `request`/`session`/`g`. Anything HTTP-layer
(routes, request parsing, response serialization) stays in
routes/student/learnora/*.

Import-time network call fix (Document 1 §5, preserved through the gateway
migration): MultiProviderManager.__init__ performs no network I/O — it
only reads env vars and sets up in-memory/Redis-backed state, so importing
this module in a test/CI context without network access still works.
app.py's create_app() still calls `provider_manager.warm_model_discovery()`
once, after blueprint registration; against the gateway this is now a
cheap, best-effort reachability check (see its docstring) rather than a
per-provider /v1/models crawl, but the call site and method name are kept
so app.py needs no changes.
"""

import os
import re
import json
import datetime
import logging
import threading

import requests

logger = logging.getLogger(__name__)


# ===========================================================
# MODEL CATALOG
#
# llm-gateway resolves a model by bare name, by an explicit "provider"
# field, or by the unambiguous "provider:model" form (see its README's
# Providers section and internal/providers/resolve.go's parseModelRef).
# Every entry below is written in the explicit "provider:model" form so
# routing never depends on the gateway's own model->provider inference
# table staying in sync with this list.
#
# The gateway's built-in provider registry (internal/providers/registry.go)
# supports: groq, mistral, openrouter, gemini, openai, deepseek, together,
# fireworks, cerebras, xai, deepinfra, sambanova. Cohere and Cloudflare
# Workers AI — both present in the old direct-call provider list — are
# NOT in that registry, so they have no equivalent here; if either is ever
# added to llm-gateway's registry, add its models to the catalog below the
# same way the other four are.
#
# PRIMARY_MODEL is the first entry the gateway is asked to use; the rest
# of each list is sent as that request's `fallback_models`, so the gateway
# walks the whole chain server-side in one HTTP call — this module no
# longer loops over providers/models itself (see _build_call_queue).
#
# Override either list from the environment with a comma-separated
# "provider:model" list — LLM_GATEWAY_TEXT_MODELS / LLM_GATEWAY_VISION_MODELS
# — without a redeploy; see _load_gateway_provider() below.
# ===========================================================

# ============================================================
# GEMINI — via the gateway's "gemini" provider id.
# ============================================================
GEMINI_MODELS = [
    "gemini:gemini-3.7-flash",
    "gemini:gemini-3.5-flash",
    "gemini:gemini-3.1-flash-lite",
]
GEMINI_VISION_MODELS = set(GEMINI_MODELS)  # all three are multimodal

# ============================================================
# GROQ — via the gateway's "groq" provider id. No vision-capable models
# in the current Groq lineup (left out of the vision chain entirely).
# ============================================================
GROQ_MODELS = [
    "groq:openai/gpt-oss-120b",
    "groq:llama-3.3-70b-versatile",
    "groq:llama-3.1-8b-instant",
    "groq:moonshotai/kimi-k2-instruct-0905",
]
GROQ_VISION_MODELS: set = set()

# ============================================================
# MISTRAL — via the gateway's "mistral" provider id. Trimmed to the one
# model confirmed working on the current plan — mistral-medium-latest
# (resolves to Mistral Medium 3.5, which is multimodal).
# ============================================================
MISTRAL_MODELS = [
    "mistral:mistral-medium-latest",
]
MISTRAL_VISION_MODELS = set(MISTRAL_MODELS)

# ============================================================
# OPENROUTER — via the gateway's "openrouter" provider id, pointed at the
# Free Models Router (openrouter/free) rather than a hand-picked list of
# specific ":free" model IDs, which goes stale as OpenRouter's free-tier
# catalog turns over. openrouter/free auto-selects from whatever free
# models are live right now and already filters for what the request
# needs (vision, tool calling, structured outputs), so it doubles as the
# vision entry too.
# ============================================================
OPENROUTER_FREE_ROUTER = "openrouter:openrouter/free"
OPENROUTER_MODELS = [OPENROUTER_FREE_ROUTER]
OPENROUTER_VISION_MODELS = {OPENROUTER_FREE_ROUTER}

# Provider order — the order these four catalogs are concatenated into
# the gateway text/vision fallback chains below. Kept as a constant for
# logging/display parity with the pre-gateway version.
PROVIDER_ORDER = ["gemini", "groq", "mistral", "openrouter"]

# Full aggregated catalogs (provider order above, model order within each
# provider) — this IS the fallback_models chain sent to the gateway,
# unless overridden by LLM_GATEWAY_TEXT_MODELS / LLM_GATEWAY_VISION_MODELS.
GATEWAY_TEXT_MODELS = GEMINI_MODELS + GROQ_MODELS + MISTRAL_MODELS + OPENROUTER_MODELS
GATEWAY_VISION_MODELS = [
    m for m in GATEWAY_TEXT_MODELS
    if m in GEMINI_VISION_MODELS | GROQ_VISION_MODELS | MISTRAL_VISION_MODELS | OPENROUTER_VISION_MODELS
]


# ===========================================================
# GATEWAY ERROR CLASSIFICATION
#
# Same four-category taxonomy as the pre-gateway version (KEY_FAULT /
# PROVIDER_TRANSIENT / BAD_MODEL / NON_RETRYABLE), but the status-set
# membership below is re-derived against llm-gateway's OWN error surface
# (internal/httpserver/apierror.go + internal/providers/errors.go), not
# each vendor's raw API, because there is now exactly one upstream and
# exactly one key (LLM_GATEWAY_API_KEY):
#
#   KEY_FAULT          — NOT used for anything the gateway itself returns
#                        anymore. The old meaning ("cool this one key,
#                        try the next key/provider") doesn't apply when
#                        there's only one gateway key: "cooling" it would
#                        just block every subsequent call for the cooldown
#                        window instead of rotating to a working
#                        alternative. 401/403 (invalid/revoked gateway
#                        key) is a hard misconfiguration — see
#                        NON_RETRYABLE below — not a per-key fault to
#                        rotate away from.
#   PROVIDER_TRANSIENT — do NOT cool the key; the gateway's own admission
#                        control (429 rate_limit_exceeded /
#                        too_many_concurrent_requests /
#                        daily_token_budget_exceeded) and its upstream
#                        failures once EVERY configured provider/credential
#                        is exhausted (502 upstream_error, 503
#                        service_unavailable, 504) land here. These are
#                        worth a short retry later — see
#                        MultiProviderManager's cooldown, which exists to
#                        protect a struggling gateway, not to pick a
#                        different key.
#   BAD_MODEL          — a model this code asked for doesn't exist on any
#                        configured provider. Evicted from this process's
#                        in-memory fallback list (see evict_model) so the
#                        same dead model name isn't retried every request
#                        until the next deploy.
#   NON_RETRYABLE      — malformed/rejected request (400), the gateway
#                        key itself is missing/invalid/revoked (401/403),
#                        or the request body was too large (413). Retrying
#                        the same request against the same single gateway
#                        would fail identically every time.
# ===========================================================

class ProviderCallError(Exception):
    """
    Direct port of multiProvider.js's ProviderCallError. Carries the real
    structured failure data (status code, provider id, network error code,
    parsed error body) that classify_provider_error operates on — never a
    re-parsed message string, for the identical reasons the reference
    file's own header comment gives.
    """
    def __init__(self, message, *, status=None, provider_id=None, network_error_code=None, parsed_body=None):
        super().__init__(message)
        self.status = status                        # real numeric HTTP status, or None for network-level failures
        self.provider_id = provider_id               # which provider produced this error
        self.network_error_code = network_error_code # e.g. 'ECONNREFUSED'-equivalent, populated only for network-level failures
        self.parsed_body = parsed_body                # the provider's parsed JSON error body, if it parsed successfully
        self.raw_message = message                    # kept for logging ONLY — never used for classification


# See the "GATEWAY ERROR CLASSIFICATION" comment above for why these
# status sets differ from the pre-gateway, per-vendor version.
_KEY_FAULT_STATUSES: set = set()  # no gateway status maps to "rotate to a different key" anymore
_PROVIDER_TRANSIENT_STATUSES = {429, 500, 502, 503, 504}
# Python's requests library raises ConnectionError/Timeout as exception
# TYPES, not as a networkErrorCode string the way Node's fetch does via
# err.cause.code — see _classify_network_exception below, which maps
# requests' exception hierarchy onto this same set of category names
# instead of a populated network_error_code string. network_error_code
# stays on ProviderCallError for parity with the reference shape, and is
# populated on a best-effort basis where a real code is available (e.g.
# from a lower-level socket error), but is not the primary network-failure
# detection path in this Python port the way it is in the JS original.
_NETWORK_MESSAGE_FALLBACK_RE = re.compile(
    r"connection refused|timed? ?out|name or service not known|connection reset|econnrefused|etimedout|enotfound|econnreset",
    re.IGNORECASE,
)


def _is_bad_model_signal(parsed_body) -> bool:
    """
    Direct port of isBadModelSignal — best-effort detection of a "model
    not found / invalid / decommissioned" signal inside a provider's own
    structured JSON error body, checked against the SPECIFIC fields a
    provider actually uses for this purpose (error.type / error.code /
    error.message), not a flattened message string.
    """
    if not parsed_body or not isinstance(parsed_body, dict):
        return False
    error_obj = parsed_body.get("error")
    if not error_obj or not isinstance(error_obj, dict):
        return False
    text = f"{error_obj.get('type') or ''} {error_obj.get('code') or ''} {error_obj.get('message') or ''}".lower()
    return bool(re.search(r"model", text)) and bool(
        re.search(r"not[\s_-]?found|does[\s_-]?not[\s_-]?exist|invalid|decommission|unknown|unsupported", text)
    )


def classify_provider_error(err) -> str:
    """
    Classify a ProviderCallError into one of four category strings:
    'KEY_FAULT' | 'PROVIDER_TRANSIENT' | 'BAD_MODEL' | 'NON_RETRYABLE'.

    Direct port of classifyProviderError — same status-set membership
    checks, same network-fallback ordering, same BAD_MODEL gate (status
    == 400 AND the structured body signal), same conservative default
    (anything not explicitly matched is NON_RETRYABLE rather than
    guessed-retryable).
    """
    if not isinstance(err, ProviderCallError):
        # Defensive fallback for any error that reaches this function
        # without having gone through the ProviderCallError wrapping path
        # — should not happen in normal operation, but classification
        # must never itself raise. Treat conservatively as non-retryable
        # rather than guessing at a category.
        return "NON_RETRYABLE"

    if err.status is not None and err.status in _KEY_FAULT_STATUSES:
        return "KEY_FAULT"

    if err.status is not None and err.status in _PROVIDER_TRANSIENT_STATUSES:
        return "PROVIDER_TRANSIENT"

    if err.status is None:
        if err.network_error_code and err.network_error_code in {"ECONNREFUSED", "ETIMEDOUT", "ENOTFOUND", "ECONNRESET"}:
            return "PROVIDER_TRANSIENT"
        if not err.network_error_code and _NETWORK_MESSAGE_FALLBACK_RE.search(err.raw_message or ""):
            return "PROVIDER_TRANSIENT"

    if err.status == 400 and _is_bad_model_signal(err.parsed_body):
        return "BAD_MODEL"

    return "NON_RETRYABLE"


def _wrap_request_exception(exc, provider_id: str) -> "ProviderCallError":
    """
    Convert a raised requests.exceptions.* (or any other exception) into a
    ProviderCallError with real status/network_error_code/parsed_body
    populated wherever the exception actually carries that information —
    mirrors multiProvider.js's callProvider/streamProvider try/catch
    wrapping around fetch(), which is the ONE place in the reference file
    that constructs a ProviderCallError from a raw failure.

    requests.exceptions.HTTPError carries a real .response (status code,
    body text) since it's raised by response.raise_for_status() AFTER a
    response was received — this is the Python equivalent of the
    reference file's "res.ok was false" branch. Everything else
    (ConnectionError, Timeout, and any other exception) is the equivalent
    of the reference file's network-level catch branch (the fetch() call
    itself threw, before any response existed) — status stays None.
    """
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        body_text = exc.response.text[:2000] if exc.response.text else ""
        parsed_body = None
        try:
            parsed_body = exc.response.json()
        except (ValueError, TypeError):
            pass  # not JSON — parsed_body stays None, classified conservatively (matches reference)
        return ProviderCallError(
            f"HTTP {status}: {body_text}", status=status, provider_id=provider_id, parsed_body=parsed_body
        )

    # Network-level failure — no response ever came back. requests doesn't
    # expose a Node-style single error-code string, so network_error_code
    # is left None here and classify_provider_error's raw_message regex
    # fallback (mirroring the reference file's own "narrow, last-resort
    # fallback used ONLY when a network failure doesn't populate a
    # structured code" path) does the classification instead.
    return ProviderCallError(str(exc), status=None, provider_id=provider_id, network_error_code=None)


# ===========================================================
# GATEWAY CLIENT MANAGER
# ===========================================================

def _parse_model_list_env(var_name: str) -> list:
    """Parse a comma-separated LLM_GATEWAY_*_MODELS env override (a list
    of explicit "provider:model" strings) into a Python list, dropping
    blanks/whitespace. Returns [] when the env var is unset/empty, so
    callers can `_parse_model_list_env(...) or <built-in default>`."""
    raw = os.environ.get(var_name, "")
    return [m.strip() for m in raw.split(",") if m.strip()]


class MultiProviderManager:
    """
    Thin client around a single llm-gateway instance (class name kept for
    backward compatibility with existing imports/tests — it no longer
    manages "multiple providers" itself; the gateway does that now).

    GATEWAY MIGRATION: every vendor-specific key, base URL, and the whole
    per-key cooldown / provider-type blacklist / background model-discovery
    machinery that used to live here has moved server-side, into the
    llm-gateway project (https://github.com/Devpaul-01/llm-gateway) — see
    its ARCHITECTURE.md for the failover engine, circuit breaker, and
    encrypted credential storage that now do this job. This class's only
    remaining state is:

      - self.providers: a list with zero or one dict, built once from env
        at construction (see _load_gateway_provider) — zero when
        LLM_GATEWAY_BASE_URL/LLM_GATEWAY_API_KEY aren't set, exactly like
        the old "no provider configured" case.
      - a short, Redis-backed (cross-instance), kill-switch-respecting
        cooldown — see mark_provider_failed/_is_key_cooling — that exists
        purely so this process doesn't hammer a gateway that just failed,
        NOT to pick a different key (there is only one).

    KILL SWITCH: AI_PROVIDER_REDIS_STATE_ENABLED (default enabled — set to
    the literal string 'false' to fall back to a plain in-process cooldown
    with no cross-instance sharing). Same switch/semantics as before the
    gateway migration.
    """

    DEFAULT_COOLDOWN_SECONDS = 20  # short: see class docstring above

    _REDIS_COOLDOWN_KEY = "sh:1:ai:gateway:cooldown"

    def __init__(self):
        self.cooldown_period = int(os.environ.get("LLM_GATEWAY_COOLDOWN_SECONDS", self.DEFAULT_COOLDOWN_SECONDS))
        self._cooled_at = None  # in-memory fallback only (kill switch off)
        self.providers = self._load_gateway_provider()

    @staticmethod
    def is_redis_state_enabled() -> bool:
        """Mirrors the pre-gateway kill switch — default enabled, disabled
        only when the env var is the literal string 'false'."""
        return os.environ.get("AI_PROVIDER_REDIS_STATE_ENABLED", "true").lower() != "false"

    @staticmethod
    def _redis_get(key):
        """Fail-open read: any exception is treated as a cache miss."""
        try:
            from services import cache_service
            return cache_service.get(key)
        except Exception as e:
            logger.warning(f"[AI_PROVIDER_REDIS] get({key}) failed (non-fatal): {e}")
            return None

    @staticmethod
    def _redis_set(key, value, ttl_seconds):
        """Fail-open write: any exception is swallowed."""
        try:
            from services import cache_service
            cache_service.set(key, value, ttl_seconds=ttl_seconds)
        except Exception as e:
            logger.warning(f"[AI_PROVIDER_REDIS] set({key}) failed (non-fatal): {e}")

    # ----------------------------------------------------------
    # Gateway provider loading
    # ----------------------------------------------------------

    def _load_gateway_provider(self) -> list:
        """
        Build the single provider slot that represents the llm-gateway
        endpoint, entirely from environment variables:

          LLM_GATEWAY_BASE_URL    (required) e.g. https://gateway.example.com
                                   — no trailing slash needed, and no "/v1"
                                   suffix; that's appended here, matching
                                   the gateway's own
                                   `POST {base}/v1/chat/completions` route.
          LLM_GATEWAY_API_KEY     (required) the project's gateway key —
                                   create one with
                                   `POST /admin/gateway-keys {"project_id": "..."}`
                                   against the gateway's admin API (see its
                                   README's Quickstart). This is the ONLY
                                   credential StudyHub needs; it is sent as
                                   `Authorization: Bearer <key>` exactly
                                   like every OpenAI-compatible call this
                                   file already made before the migration.
          LLM_GATEWAY_PROJECT_ID  (optional) the gateway project id this
                                   key belongs to. The gateway derives the
                                   project from the key itself on every
                                   `/v1/chat/completions` call
                                   (internal/httpserver/auth.go) — this is
                                   NOT sent on chat requests and does not
                                   affect routing. It's read here purely
                                   for logging/diagnostics (get_stats(),
                                   startup logs) and so this codebase has
                                   the id on hand for any future
                                   `GET /admin/metrics?project_id=...` or
                                   `GET /public/metrics` integration,
                                   without needing another round trip to
                                   discover it.
          LLM_GATEWAY_TEXT_MODELS / LLM_GATEWAY_VISION_MODELS (optional)
                                   comma-separated "provider:model" override
                                   for the built-in GATEWAY_TEXT_MODELS /
                                   GATEWAY_VISION_MODELS catalogs defined
                                   near the top of this file — lets ops
                                   change which models/fallback order are
                                   requested from the gateway without a
                                   code deploy.

        Returns a list with zero or one provider dict — zero when the
        gateway isn't configured, so get_working_provider() reports "no
        provider available" exactly like the old zero-keys-configured case.
        """
        base_url = os.environ.get("LLM_GATEWAY_BASE_URL", "").strip().rstrip("/")
        api_key = os.environ.get("LLM_GATEWAY_API_KEY", "").strip()
        project_id = os.environ.get("LLM_GATEWAY_PROJECT_ID", "").strip() or None

        if not base_url or not api_key:
            logger.error(
                "❌ LLM_GATEWAY_BASE_URL and/or LLM_GATEWAY_API_KEY are not set — "
                "no AI provider configured. Point these at a running llm-gateway "
                "instance (https://github.com/Devpaul-01/llm-gateway) and a "
                "project gateway key created via its /admin/gateway-keys API."
            )
            return []

        text_models = _parse_model_list_env("LLM_GATEWAY_TEXT_MODELS") or list(GATEWAY_TEXT_MODELS)
        vision_models = _parse_model_list_env("LLM_GATEWAY_VISION_MODELS") or list(GATEWAY_VISION_MODELS)

        logger.info(
            f"🔧 llm-gateway configured: {base_url} "
            f"(project={project_id or 'derived server-side from the gateway key'}) — "
            f"{len(text_models)} text model(s), {len(vision_models)} vision model(s) in the fallback chain"
        )

        return [{
            "name": "llm-gateway",
            "api_key": api_key,
            "base_url": f"{base_url}/v1",
            "text_model": text_models[0] if text_models else None,
            "vision_model": vision_models[0] if vision_models else None,
            "supports_vision": bool(vision_models),
            "type": "gateway",
            "text_model_fallbacks": text_models,
            "vision_model_fallbacks": vision_models,
            "_provider_id": "gateway",
            "project_id": project_id,
        }]

    def warm_model_discovery(self):
        """
        PUBLIC entry point kept for app.py's create_app(), which calls this
        once at startup, after blueprint registration, as its own visible
        startup step (see this module's header docstring).

        Against a gateway there is no per-provider /v1/models crawl left to
        do here — the gateway owns model validity/discovery for whichever
        real providers it's configured with. This is now a cheap,
        synchronous, best-effort log of the resolved configuration (no
        background thread, no network call) rather than the old
        multi-provider /v1/models fetch.
        """
        if not self.providers:
            logger.warning("⚠️ warm_model_discovery(): no gateway configured — set LLM_GATEWAY_BASE_URL / LLM_GATEWAY_API_KEY")
            return
        provider = self.providers[0]
        logger.info(
            f"✅ llm-gateway ready at {provider['base_url']} — "
            f"primary text model {provider['text_model']!r} "
            f"({len(provider['text_model_fallbacks'])} in fallback chain), "
            f"vision {'enabled' if provider['supports_vision'] else 'disabled'}"
        )

    # ----------------------------------------------------------
    # Cooldown — short client-side circuit breaker (see class docstring)
    # ----------------------------------------------------------

    def _is_key_cooling(self, provider_name: str) -> bool:
        """
        HORIZONTAL SCALING: when Redis state is enabled, a cooldown set by
        ANY instance (after a failed gateway call) is visible to every
        instance's next get_working_provider() call, same cross-instance
        guarantee as before the gateway migration — just over a single
        shared key instead of one key per vendor credential.
        """
        if self.is_redis_state_enabled():
            return self._redis_get(self._REDIS_COOLDOWN_KEY) is not None
        if not self._cooled_at:
            return False
        return (datetime.datetime.utcnow() - self._cooled_at).total_seconds() < self.cooldown_period

    def mark_provider_failed(self, provider_name: str, error_message: str = ""):
        """
        Record that the single gateway call just failed, and start a short
        cooldown (LLM_GATEWAY_COOLDOWN_SECONDS, default 20s) during which
        get_working_provider() returns None instead of immediately
        re-sending the identical request to a gateway that's currently
        struggling. There is no second key/provider to rotate to at this
        layer anymore — the gateway already tried its own full
        fallback_models chain, across every provider/credential it has,
        before this failure was ever raised back to StudyHub.
        """
        now = datetime.datetime.utcnow()
        if self.is_redis_state_enabled():
            self._redis_set(self._REDIS_COOLDOWN_KEY, now.isoformat(), self.cooldown_period)
        else:
            self._cooled_at = now
        logger.warning(f"⚠️ llm-gateway call failed, cooling down for {self.cooldown_period}s: {error_message}")

    def rotate(self):
        """
        No-op: there is exactly one provider slot (the gateway) now.
        Kept so every existing caller that does
        `mark_provider_failed(...); rotate(); get_working_provider(...)`
        between a failed attempt and its next one keeps working with no
        changes to those call sites.
        """
        return

    def get_working_provider(self, needs_vision: bool = False):
        """
        Return the gateway provider slot, or None when:
          - no gateway is configured (LLM_GATEWAY_BASE_URL/API_KEY unset);
          - the gateway is in its short post-failure cooldown; or
          - needs_vision=True and no vision model is configured for it.

        Every existing "get provider → try → on failure: mark_provider_failed()
        + rotate() + get_working_provider() again" loop in the route layer
        now naturally terminates after one real attempt: the gateway
        already walked its own fallback_models chain server-side before
        replying, so an immediate second call within the cooldown window
        wouldn't find anything the first call didn't already try.
        """
        if not self.providers:
            logger.error("❌ No AI provider configured — set LLM_GATEWAY_BASE_URL and LLM_GATEWAY_API_KEY")
            return None

        provider = self.providers[0]

        if self._is_key_cooling(provider["name"]):
            logger.info("⏭️ llm-gateway is cooling down after a recent failure")
            return None

        if needs_vision and not provider["supports_vision"]:
            logger.warning("⏭️ Vision requested but no vision model is configured in the gateway fallback chain")
            return None

        logger.info(f"✅ Using provider: {provider['name']}")
        return provider

    def evict_model(self, provider_id: str, model_id: str):
        """
        Remove a dead/renamed/decommissioned model from this process's
        in-memory fallback chain(s) for the remainder of its lifetime.

        Process-local only — unlike the pre-gateway version there's no
        shared Redis-backed discovery cache to evict from anymore, because
        there's no live per-vendor /v1/models crawl feeding one: the
        catalog is the small, hand-maintained GATEWAY_TEXT_MODELS /
        GATEWAY_VISION_MODELS list (or its LLM_GATEWAY_*_MODELS env
        override) defined near the top of this file. A model evicted here
        reappears on the next deploy/restart unless also removed from that
        catalog or override — if a model is dead for good, fix it there.
        """
        evicted = False
        for p in self.providers:
            before = len(p.get("text_model_fallbacks", [])) + len(p.get("vision_model_fallbacks", []))
            p["text_model_fallbacks"] = [m for m in p.get("text_model_fallbacks", []) if m != model_id]
            p["vision_model_fallbacks"] = [m for m in p.get("vision_model_fallbacks", []) if m != model_id]
            after = len(p["text_model_fallbacks"]) + len(p["vision_model_fallbacks"])
            evicted = evicted or (after != before)

            if p.get("text_model") == model_id:
                p["text_model"] = p["text_model_fallbacks"][0] if p["text_model_fallbacks"] else None
            if p.get("vision_model") == model_id:
                p["vision_model"] = p["vision_model_fallbacks"][0] if p["vision_model_fallbacks"] else None
                p["supports_vision"] = bool(p["vision_model"])

        if evicted:
            logger.warning(f"🚫 Evicted bad model '{model_id}' from the gateway fallback chain for this process")

    def get_stats(self):
        """Stats surface for routes/admin.py — same method name as before,
        reshaped for the single-gateway model (no per-key/blacklist detail
        left to report; that bookkeeping now lives in the gateway's own
        /admin/metrics and Gateway Console)."""
        if not self.providers:
            return {
                "configured": False,
                "state_source": "redis" if self.is_redis_state_enabled() else "in-memory",
            }
        provider = self.providers[0]
        return {
            "configured": True,
            "base_url": provider["base_url"],
            "project_id": provider.get("project_id") or "unknown — derived server-side from the gateway key",
            "text_model": provider["text_model"],
            "vision_model": provider["vision_model"],
            "supports_vision": provider["supports_vision"],
            "text_model_fallback_count": len(provider["text_model_fallbacks"]),
            "vision_model_fallback_count": len(provider["vision_model_fallbacks"]),
            "cooling_down": self._is_key_cooling(provider["name"]),
            "cooldown_period_seconds": self.cooldown_period,
            "state_source": "redis" if self.is_redis_state_enabled() else "in-memory",
        }


# Initialize provider manager (module-level singleton — every caller across
# the app shares this one instance, which is what makes the gateway
# cooldown state consistent within a single process).
#
# NOTE: this line runs at IMPORT time, but it performs no network I/O —
# MultiProviderManager.__init__ only reads env vars and sets up in-memory
# state. warm_model_discovery() must still be called separately and
# explicitly (see app.py).
provider_manager = MultiProviderManager()


# ===========================================================
# RESPONSE CLEANING
# ===========================================================

# Patterns that some reasoning/chat models emit at the start of responses
# even when not asked to — strip these before showing text to the user.
_REASONING_PREFIX_RE = re.compile(
    r"^(<think>.*?</think>|<reasoning>.*?</reasoning>|<scratchpad>.*?</scratchpad>)\s*",
    re.DOTALL | re.IGNORECASE,
)

# Stray SSE/data protocol artefacts that can leak into streamed content
_SSE_ARTIFACT_RE = re.compile(r"^data:\s*", re.MULTILINE)

# Some models wrap the whole reply in triple back-ticks with no language tag
_BARE_CODE_FENCE_RE = re.compile(r"^```\s*\n(.*?)\n```\s*$", re.DOTALL)


def clean_ai_response(text: str) -> str:
    """
    Sanitise a raw AI response before it is stored or sent to the client.

    Cleaning steps (in order):
      1. Strip leading/trailing whitespace.
      2. Remove internal reasoning/scratchpad blocks that some models emit
         (e.g. <think>…</think> from DeepSeek-style models).
      3. Remove stray SSE protocol prefixes ("data: ") that can bleed through
         when a streamed chunk is accidentally captured verbatim.
      4. Unwrap a response that is *entirely* a bare triple-back-tick block
         with no language tag (the model mistakenly wrapping prose in fences).
      5. Collapse three-or-more consecutive blank lines to two (keeps intentional
         whitespace but removes runaway vertical padding).
      6. Final strip.

    The function is intentionally conservative — it does NOT strip markdown
    formatting (bold, headers, code blocks with language tags) because those
    are meaningful to the frontend renderer.
    """
    if not text:
        return ""

    # 1. Initial strip
    text = text.strip()

    # 2. Remove leading reasoning/scratchpad blocks
    text = _REASONING_PREFIX_RE.sub("", text).strip()

    # 3. Remove stray SSE prefixes
    text = _SSE_ARTIFACT_RE.sub("", text).strip()

    # 4. Unwrap bare code fences wrapping the entire response (prose mistake)
    bare_match = _BARE_CODE_FENCE_RE.match(text)
    if bare_match:
        inner = bare_match.group(1).strip()
        # Only unwrap if the inner text looks like prose (no newline-separated
        # code lines that start with keywords), to avoid stripping real code.
        first_line = inner.splitlines()[0] if inner else ""
        looks_like_code = re.match(
            r"^\s*(def |class |import |from |#include|function |var |const |let |public |private )",
            first_line,
        )
        if not looks_like_code:
            text = inner

    # 5. Collapse excessive blank lines (3+ → 2)
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 6. Final strip
    return text.strip()


def generate_conversation_title(first_message, provider=None):
    """
    Generate a short, descriptive conversation title using AI.
    Falls back to message truncation if AI call fails or no provider available.
    """
    if provider:
        try:
            headers = {"Content-Type": "application/json"}

            if provider.get("api_key"):
                headers["Authorization"] = f"Bearer {provider['api_key']}"

            endpoint = f"{provider['base_url']}/chat/completions"
            fallback_models = _fallback_models_for(provider, provider["text_model"])
            payload = {
                "model": provider["text_model"],
                **({"fallback_models": fallback_models} if fallback_models else {}),
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Generate a concise, descriptive title (3–6 words) for a study conversation "
                            "based on the user's first message. "
                            "Return ONLY the title text — no quotes, no punctuation, no explanation."
                        )
                    },
                    {
                        "role": "user",
                        "content": first_message[:500]
                    }
                ],
                "max_tokens": 60,
                "stream": False
            }

            response = requests.post(endpoint, headers=headers, json=payload, timeout=10)

            if response.status_code == 200:
                result = response.json()
                try:
                    message = result["choices"][0]["message"]
                    # Reasoning models (e.g. gpt-oss-120b) can return the visible
                    # answer under "content", with internal reasoning tokens under
                    # "reasoning_content". If max_tokens is too low, "content" can
                    # come back missing entirely while reasoning_content is set —
                    # that's what was causing the bare 'content' KeyError.
                    title = (message.get("content") or message.get("reasoning_content") or "")
                    title = clean_ai_response(title).strip('"\'')
                except (KeyError, IndexError, TypeError):
                    logger.warning(f"⚠️ Unexpected title response shape: {result}")
                    title = ""

                if title and len(title) <= 100:
                    logger.info(f"✅ AI-generated title: '{title}'")
                    return title
            else:
                logger.warning(f"⚠️ Title generation got HTTP {response.status_code}: {response.text[:300]}")

        except Exception as e:
            logger.warning(f"⚠️ AI title generation failed, using fallback: {str(e)}")

    # Fallback: truncate the first message cleanly
    clean = ' '.join(first_message.split())
    return clean if len(clean) <= 60 else clean[:57] + '...'


# ===========================================================
# STUDY ASSISTANT - MULTI-PROVIDER VERSION
# ===========================================================

class StudyAssistant:
    def __init__(self, provider, conversation_messages=None):
        self.provider = provider
        self.conversation_history = conversation_messages or []
        self.model = None           # set by select_model()
        self.base_system = (
            "You are Learnora, an intelligent study assistant. "
            "Provide clear, accurate, and helpful responses."
        )

    def should_summarize(self):
        return len(self.conversation_history) > 10

    @staticmethod
    def _preview_text(content) -> str:
        """Best-effort short text preview of a message's content, whether it's
        a plain string or a list of content parts (file/image attachments)."""
        if isinstance(content, str):
            return content[:100]
        if isinstance(content, list):
            text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
            return " ".join(text_parts)[:100] if text_parts else "[attachment]"
        return str(content)[:100]

    def summarize_conversation(self):
        if len(self.conversation_history) <= 10:
            return self.conversation_history

        old_messages = self.conversation_history[:-5]
        recent_messages = self.conversation_history[-5:]

        summary_text = "Previous conversation summary:\n"
        for msg in old_messages:
            preview = self._preview_text(msg.get("content"))
            if msg["role"] == "user":
                summary_text += f"- User asked: {preview}...\n"
            elif msg["role"] == "assistant":
                summary_text += f"- Assistant answered: {preview}...\n"

        summarized = [{"role": "system", "content": summary_text}]
        summarized.extend(recent_messages)
        return summarized

    def get_working_messages(self):
        """
        Return the conversation history slice to send to the provider,
        sanitized down to {role, content} only.

        IMPORTANT: messages stored in conversation.messages (the DB column)
        carry extra bookkeeping fields — timestamp, attachments, is_continue,
        model, provider, is_complete, error — for our own app's use. Several
        providers (Mistral in particular) run strict schema validation on
        the chat completions body and will reject the *entire request* with
        a 400 if a message object contains any property outside role/content.
        This is why the first message in a conversation (empty history) works
        fine, but every message after it fails — the moment there's stored
        history to replay, those extra keys go along for the ride.
        """
        raw = self.summarize_conversation() if self.should_summarize() else self.conversation_history[-10:]
        return [
            {"role": m.get("role", "user"), "content": m.get("content", "")}
            for m in raw
            if m.get("role") and m.get("content") is not None
        ]

    def select_model(self, has_images: bool):
        """
        Pick the best model for this request.

        Vision flow:
          1. If images present AND provider supports vision → use vision_model
          2. If images present but provider has no vision support → fall back to
             text_model and set a flag so build_messages() strips image parts
        """
        if has_images and self.provider.get("supports_vision"):
            self.model = self.provider["vision_model"]
            self.vision_active = True
            logger.info(f"🤖 Vision model selected: {self.model}")
        else:
            self.model = self.provider["text_model"]
            self.vision_active = False
            if has_images:
                logger.warning(
                    f"⚠️ Provider {self.provider['name']} does not support vision — "
                    "images will be described as text-only placeholders."
                )
            else:
                logger.info(f"🤖 Text model selected: {self.model}")

    def build_messages(self, user_input, extracted_data, mode, post_content=None, continuation_text=None):
        """
        Build the full message array for the API call.

        Vision handling:
          - If self.vision_active is True: images are embedded as base64 data URIs
            inside image_url content parts (standard OpenAI vision format).
          - If self.vision_active is False: image items are replaced with a plain
            text notice so non-vision models don't crash or silently ignore them.

        continuation_text (Learnora Chat Audit — Issue 13): the stored
        AIConversation.last_incomplete_message text, passed only when
        this request is resuming a token-limit-truncated response.
        Previously "Continue" sent nothing but a bare "continue" user
        turn and relied entirely on the model inferring intent from
        conversation history — last_incomplete_message was written but
        never read back anywhere. When provided, an explicit system
        instruction is added telling the model exactly what it already
        said and to continue from there without repeating itself.
        """
        messages = []

        messages.append({"role": "system", "content": self.base_system})
        messages.append({"role": "system", "content": self.get_mode_prompt(mode)})

        if continuation_text:
            messages.append({
                "role": "system",
                "content": (
                    "The previous response was cut off before it finished. "
                    "Continue writing from exactly where it left off — do not "
                    "repeat, restate, or summarize what was already said. "
                    "The truncated response so far was:\n\n"
                    f"{continuation_text}"
                )
            })

        context_messages = self.get_working_messages()
        messages.extend(context_messages)

        user_content_parts = []

        if user_input:
            user_content_parts.append({
                "type": "text",
                "text": f"**Question:** {user_input}"
            })

        if post_content:
            user_content_parts.append({
                "type": "text",
                "text": (
                    f"\n\n**Referenced Post:**\n"
                    f"Title: {post_content['title']}\n\n"
                    f"Content: {post_content['content']}"
                )
            })

        logger.info(f"📎 Building message with {len(extracted_data)} file(s), vision_active={getattr(self, 'vision_active', False)}")

        for item in extracted_data:
            if item["type"] == "image":
                if getattr(self, "vision_active", False):
                    # Send as base64 data URI — primary vision path
                    user_content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": item["content"]}
                    })
                    logger.info(f"🖼️ Added base64 image: {item['filename']}")
                else:
                    # Non-vision model fallback — send a text placeholder
                    user_content_parts.append({
                        "type": "text",
                        "text": (
                            f"\n\n**[Attached Image: {item['filename']}]**\n"
                            "_(Image content cannot be displayed — the current model does not support vision. "
                            "Please describe what you need help with regarding this image.)_"
                        )
                    })
                    logger.info(f"📝 Image replaced with text placeholder: {item['filename']}")
            else:
                content_preview = item["content"][:5000]
                if len(item["content"]) > 5000:
                    content_preview += "\n\n[... content truncated ...]"

                user_content_parts.append({
                    "type": "text",
                    "text": (
                        f"\n\n**Attached {item['type'].upper()} File:** `{item['filename']}`\n"
                        f"```\n{content_preview}\n```"
                    )
                })
                logger.info(f"📄 Added file: {item['filename']}")

        # Only use the OpenAI-style array-of-parts content format when we're
        # actually embedding a real image (vision). Several providers —
        # Mistral in particular ("Extra inputs are not permitted" is a known
        # Mistral validation error) and apparently Groq's non-vision models
        # too — validate the message schema strictly and reject the
        # multimodal array format unless a vision model is actually in use.
        # The old rule ("array only if more than 1 part") broke the moment a
        # file or referenced post was attached, since that always produces
        # 2+ parts. Collapsing everything to a single string is the safest,
        # most portable choice whenever there's no image to embed.
        has_image_part = any(p["type"] == "image_url" for p in user_content_parts)

        if has_image_part:
            messages.append({"role": "user", "content": user_content_parts})
        else:
            combined_text = "\n".join(p["text"] for p in user_content_parts if p["type"] == "text")
            messages.append({"role": "user", "content": combined_text})

        logger.info(f"✅ Built message with {len(messages)} parts")
        return messages

    def get_mode_prompt(self, mode):
        mode_prompts = {
            "deep_think": (
                "Provide extremely thorough explanations. Break down complex concepts "
                "into simple steps. Use examples and analogies."
            ),
            "fast_response": (
                "Provide concise, direct answers. Be brief but accurate."
            ),
            "programming": (
                "You are an expert programming tutor. Review code carefully, explain logic, "
                "identify bugs, suggest improvements, and provide working examples."
            ),
            "research": (
                "Act as a research assistant. Provide well-researched information."
            ),
            "summarize": (
                "Summarize the provided content concisely. Extract key points."
            ),
            "explain": (
                "Explain concepts as if teaching a student. Use simple language."
            )
        }
        return mode_prompts.get(mode, "Respond helpfully and clearly.")

    def _is_model_error(self, error_msg: str) -> bool:
        """
        Return True if the error looks like a missing/invalid model,
        meaning we should retry with the next fallback model rather than
        marking the whole provider as failed.
        """
        lower = error_msg.lower()
        model_error_signals = [
            "model not found",
            "no endpoints found",
            "invalid model",
            "model does not exist",
            "unknown model",
            "model_not_found",
            "404",
        ]
        return any(sig in lower for sig in model_error_signals)

    def advance_to_fallback_model(self, has_images: bool) -> bool:
        """
        Try the next model in the provider's fallback chain.
        Returns True if a new model was selected, False if the chain is exhausted.
        """
        key = "vision_model_fallbacks" if has_images and getattr(self, "vision_active", False) \
              else "text_model_fallbacks"
        fallbacks: list = self.provider.get(key, [])

        current = self.model
        try:
            idx = fallbacks.index(current)
            next_models = fallbacks[idx + 1:]
        except ValueError:
            next_models = fallbacks  # current model wasn't in list, try all

        for next_model in next_models:
            self.model = next_model
            logger.info(f"🔄 Model fallback: {current} → {next_model}")
            return True

        logger.warning(f"⚠️ Model fallback chain exhausted for {self.provider['name']}")
        return False

    def stream_response(self, messages, has_images: bool = False):
        """
        Stream AI response with error handling.
        Detects model-not-found errors and automatically retries with the
        next model in the provider's fallback chain.

        Sets self._provider_exhausted = True when all models in this provider
        have been tried and failed, so generate() knows to switch providers.

        CLASSIFICATION (AUDIT horizontal-scaling completion pass): _do_stream
        now sets self._last_error_category (one of classify_provider_error's
        four category strings) alongside the existing _model_error_occurred
        flag, for HEADER-STAGE failures only (network failure, non-2xx
        status, timeout — i.e. anything with a real HTTP status or
        exception available to classify). This loop still only branches on
        _model_error_occurred (external SSE contract and _provider_exhausted
        semantics are UNCHANGED — study_sessions.py's ai_ask_in_session is
        the sole external consumer of _provider_exhausted and needs no
        changes), but a KEY_FAULT-classified header-stage failure ALSO
        cools the specific key now (previously: every header-stage HTTP/
        timeout failure yielded an SSE {'error': ...} chunk with no
        cooldown at all — a genuinely bad key streamed the exact same
        error chunk forever with no signal ever reaching MultiProviderManager).

        Deliberately NOT classified: the mid-stream `if 'error' in chunk`
        branch inside _do_stream, where a provider's response already
        succeeded at the HTTP level (200, headers already sent) and is
        reporting an error INSIDE an already-flowing SSE body. There is no
        HTTP status at that point to classify against — classify_provider_error
        operates on status/networkErrorCode/parsedBody, none of which exist
        for an error embedded in a chunk of an already-200 stream. That
        branch keeps its existing rate-limit/generic split unchanged.
        """
        # Client-side safety net only now — the gateway already walks the
        # `fallback_models` chain attached to every stream request (see
        # _do_stream) before a failure is ever reported back here, so this
        # loop should rarely do more than one pass. Bounded by the longest
        # single-provider catalog so a pathological repeated BAD_MODEL
        # signal still can't loop forever.
        MAX_MODEL_RETRIES = max(len(GEMINI_MODELS), len(GROQ_MODELS), len(MISTRAL_MODELS), len(OPENROUTER_MODELS))
        self._provider_exhausted = False

        for model_attempt in range(MAX_MODEL_RETRIES):
            yield from self._do_stream(messages)

            # AUDIT: header-stage failures now cool the key when
            # classified as KEY_FAULT — see _do_stream's docstring for
            # exactly which failures set _last_error_category and why
            # this is scoped to header-stage only.
            category = getattr(self, "_last_error_category", None)
            self._last_error_category = None

            if category == "KEY_FAULT":
                # Cooling the key here is not enough on its own — this
                # provider's key is now dead for the cooldown window, so
                # trying its OTHER models (advance_to_fallback_model,
                # below) would just burn every remaining model in
                # GEMINI_MODELS/etc. against the same cooled key before
                # ever giving the caller a chance to pick a different
                # provider. Bug this fixes: a 402 (payment required, now
                # classified KEY_FAULT — see classify_provider_error)
                # used to fall through to the generic "not a model error"
                # branch below, which does nothing for KEY_FAULT, so the
                # loop kept re-streaming from the SAME already-failed key
                # up to MAX_MODEL_RETRIES times.
                provider_manager.mark_provider_failed(self.provider["name"], "streaming KEY_FAULT")
                logger.warning(
                    f"⚠️ {self.provider['name']} hit a KEY_FAULT mid-stream — "
                    "cooling key and switching providers rather than retrying it"
                )
                self._provider_exhausted = True
                return

            if category == "PROVIDER_TRANSIENT":
                # Single-gateway design: a 429/502/503/504 here means the
                # gateway's own admission control or its upstream failover
                # was exhausted — there's no different provider at THIS
                # layer to switch to, so (unlike the pre-gateway version)
                # this now ALSO starts the short cooldown, same reasoning
                # as the KEY_FAULT branch above: don't keep hammering a
                # gateway that just told us it's struggling.
                provider_manager.mark_provider_failed(self.provider["name"], "streaming PROVIDER_TRANSIENT")
                logger.warning(
                    f"⚠️ {self.provider['name']} hit a PROVIDER_TRANSIENT error mid-stream — "
                    "cooling down rather than retrying it immediately"
                )
                self._provider_exhausted = True
                return

            if category == "BAD_MODEL":
                provider_manager.evict_model(self.provider.get("_provider_id", self.provider["name"]), self.model)

            elif category == "NON_RETRYABLE":
                # Genuinely not retryable anywhere for this request — don't
                # cycle models on this provider, but also don't treat it as
                # a clean exit (see below): signal the caller so it doesn't
                # silently reuse this same provider for the same request.
                logger.error(
                    f"❌ {self.provider['name']} hit a NON_RETRYABLE error mid-stream — aborting fallback chain"
                )
                self._provider_exhausted = True
                return

            # _do_stream sets self._model_error_occurred if a model error occurred
            if not getattr(self, "_model_error_occurred", False):
                return  # clean exit

            # Try next fallback model (BAD_MODEL, or a mid-stream model-not-
            # found signal with no header-stage category at all)
            advanced = self.advance_to_fallback_model(has_images)
            if not advanced:
                # All models in this provider exhausted — signal caller to switch providers
                logger.warning(f"⚠️ All models exhausted for provider {self.provider['name']} — needs provider switch")
                self._provider_exhausted = True
                return

            self._model_error_occurred = False
            logger.info(f"🔁 Retrying stream with model: {self.model}")
            yield f"data: {json.dumps({'type': 'model_retry', 'new_model': self.model})}\n\n"

    def _do_stream(self, messages):
        """
        Internal: perform one streaming request and yield SSE chunks.
        Sets self._model_error_occurred = True if a model-not-found error
        is detected so stream_response() knows to retry.

        AUDIT: also sets self._last_error_category to one of
        classify_provider_error's four category strings for HEADER-STAGE
        failures (the 404 branch, the Timeout/HTTPError/generic Exception
        except blocks) — see stream_response's docstring above for exactly
        what this does and doesn't cover. Every SSE chunk this function
        yields is UNCHANGED in shape from before this pass — classification
        is layered on as an additional side-channel signal
        (self._last_error_category), never a change to what's sent over
        the wire to the frontend.
        """
        self._model_error_occurred = False
        self._last_error_category = None

        headers = {"Content-Type": "application/json"}

        if self.provider["api_key"]:
            headers["Authorization"] = f"Bearer {self.provider['api_key']}"

        data = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            # Server-side failover — see _fallback_models_for's docstring.
            # The gateway tries each of these in order before this stream
            # is ever reported as failed.
            **({"fallback_models": fb} if (fb := _fallback_models_for(
                self.provider, self.model, getattr(self, "vision_active", False)
            )) else {}),
        }

        logger.info(f"🚀 Streaming — provider: {self.provider['name']}, model: {self.model}")
        endpoint_url = f"{self.provider['base_url']}/chat/completions"
        stream_timeout_seconds = int(os.environ.get("LLM_GATEWAY_STREAM_TIMEOUT_SECONDS", "60"))

        try:
            response = requests.post(
                endpoint_url,
                headers=headers,
                json=data,
                stream=True,
                timeout=stream_timeout_seconds
            )

            # Catch model-not-found at the HTTP level (some providers return 404)
            if response.status_code == 404:
                error_body = response.text[:200]
                logger.warning(f"⚠️ 404 for model {self.model}: {error_body}")
                self._model_error_occurred = True
                parsed_body = None
                try:
                    parsed_body = response.json()
                except (ValueError, TypeError):
                    pass
                classify_err = ProviderCallError(
                    error_body, status=404,
                    provider_id=self.provider.get("_provider_id", self.provider["name"]),
                    parsed_body=parsed_body,
                )
                self._last_error_category = classify_provider_error(classify_err)
                return

            response.raise_for_status()

            response_complete = False

            for line in response.iter_lines():
                if line:
                    line = line.decode('utf-8')

                    if line.startswith(':'):
                        continue

                    if line.startswith('data: '):
                        line = line[6:]

                    if line == '[DONE]':
                        response_complete = True
                        yield "data: [DONE]\n\n"
                        break

                    try:
                        chunk = json.loads(line)

                        if 'error' in chunk:
                            error_msg = chunk['error'].get('message', str(chunk['error']))
                            logger.error(f"❌ API error in stream: {error_msg}")

                            # Model-not-found error → trigger model fallback
                            # NOT classified via classify_provider_error — see
                            # this function's docstring: no HTTP status exists
                            # for an error embedded mid-stream after a 200.
                            if self._is_model_error(error_msg):
                                logger.warning(f"⚠️ Model error detected: {error_msg}")
                                self._model_error_occurred = True
                                return

                            if 'rate limit' in error_msg.lower() or 'quota' in error_msg.lower():
                                yield f"data: {json.dumps({'rate_limit': True, 'error': error_msg})}\n\n"
                            else:
                                yield f"data: {json.dumps({'error': error_msg})}\n\n"
                            break

                        content = chunk.get('choices', [{}])[0].get('delta', {}).get('content', '')

                        if content:
                            yield f"data: {json.dumps({'content': content})}\n\n"

                        finish_reason = chunk.get('choices', [{}])[0].get('finish_reason')

                        if finish_reason == 'length':
                            yield f"data: {json.dumps({'incomplete': True, 'reason': 'token_limit'})}\n\n"
                            response_complete = False
                            break
                        elif finish_reason == 'stop':
                            response_complete = True
                        elif finish_reason == 'content_filter':
                            yield f"data: {json.dumps({'error': 'Response filtered'})}\n\n"
                            break

                    except json.JSONDecodeError:
                        continue

            yield f"data: {json.dumps({'complete': response_complete})}\n\n"
            logger.info(f"✅ Stream complete: {response_complete}")

        except requests.exceptions.Timeout as e:
            logger.error("⏱️ Request timeout")
            classify_err = ProviderCallError(
                "timeout", status=None, provider_id=self.provider.get("_provider_id", self.provider["name"])
            )
            self._last_error_category = classify_provider_error(classify_err)
            yield f"data: {json.dumps({'error': 'Request timed out', 'timeout': True})}\n\n"
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            body = e.response.text[:500] if e.response is not None else ""
            logger.error(f"❌ HTTP error {status} from {self.provider['name']}: {body}")
            classify_err = _wrap_request_exception(e, self.provider.get("_provider_id", self.provider["name"]))
            self._last_error_category = classify_provider_error(classify_err)
            if status == 404:
                self._model_error_occurred = True
                return
            yield f"data: {json.dumps({'error': f'HTTP {status}', 'http_error': True})}\n\n"
        except Exception as e:
            logger.error(f"❌ Stream error: {str(e)}", exc_info=True)
            yield f"data: {json.dumps({'error': str(e)})}\n\n"


def _build_call_queue(needs_vision: bool = False) -> list[dict]:
    """
    GATEWAY MIGRATION: this used to flatten every healthy provider slot's
    full model list into a long queue of {provider, model} attempts that
    call_ai_response walked one HTTP call at a time, because this process
    was the thing doing provider/model fallback.

    That fallback now happens server-side, inside ONE gateway call: the
    queue entry's "model" is the primary model, and
    _call_provider_sync_raising/StudyAssistant attach the REST of the
    chain as that request's `fallback_models` (see GATEWAY_TEXT_MODELS /
    GATEWAY_VISION_MODELS and the payload-building code below), so the
    gateway walks every model/provider/credential it has before ever
    reporting a failure back here. The queue therefore has at most one
    entry — a second local attempt after that one fails would just be
    re-asking the same gateway to retry a chain it already exhausted.

    External shape unchanged: still a list of {"provider", "model",
    "provider_id"} dicts, so call_ai_response's loop (and its diagnostics
    dict) needs no changes.
    """
    queue = []
    for provider in provider_manager.providers:
        provider_type = provider.get("_provider_id", provider["name"])
        if provider_manager._is_key_cooling(provider["name"]):
            continue
        if needs_vision and not provider.get("supports_vision"):
            continue

        model = provider.get("vision_model") if needs_vision else provider.get("text_model")
        if not model:
            continue

        queue.append({"provider": provider, "model": model, "provider_id": provider_type})

    return queue


def _fallback_models_for(provider: dict, model: str, needs_vision: bool = False) -> list:
    """
    The `fallback_models` chain to send alongside `model` on a gateway
    request — every OTHER model in the relevant catalog (vision or text),
    in order, so the gateway tries them in sequence server-side if `model`
    fails (see llm-gateway's README "Automatic failover" and
    chatCompletionRequest.FallbackModels in internal/httpserver/chat.go).
    `model` itself is excluded so it isn't sent twice.
    """
    chain = provider.get("vision_model_fallbacks") if needs_vision else provider.get("text_model_fallbacks")
    return [m for m in (chain or []) if m != model]


def _call_provider_sync_raising(
    messages: list,
    provider: dict,
    type: str = "",
    max_tokens: int | None = None,
    model: str | None = None,
    needs_vision: bool = False,
) -> str | None:
    """
    Non-streaming provider call for use in background threads.

    Used by the Thread WebSocket system (meeting notes, action AI calls)
    and now also as the internal engine for call_ai_response() below.

    `model` (new, optional): when supplied, overrides provider["text_model"]
    for this one call — this is what lets call_ai_response's queue-walking
    loop try a SPECIFIC model from the flattened queue rather than always
    the provider's single primary model. Defaults to provider["text_model"]
    when omitted, so every pre-existing caller (the Thread WebSocket
    meeting-notes/action-AI call sites, which call this directly without
    going through call_ai_response's queue) is completely unaffected.

    CLASSIFICATION CHANGE: previously every failure (timeout, any HTTP
    status, any other exception) unconditionally called
    provider_manager.mark_provider_failed — cooling the specific key
    regardless of whether the failure was actually attributable to that
    key. Failures are now wrapped into a ProviderCallError and raised
    (not swallowed into a bare `return None`) so call_ai_response's queue
    loop can classify and react per multiProvider.js's four-category
    taxonomy instead. Direct callers of THIS function (the Thread
    WebSocket meeting-notes/action-AI sites) that don't go through
    call_ai_response still get the exact same external behavior as
    before — see the thin backward-compatible wrapper immediately below
    this function.
    """
    # Some call types need more headroom than a normal chat reply.
    # Meeting notes summarize a whole conversation into structured JSON,
    # so they need a much higher ceiling.
    DEFAULT_MAX_TOKENS = {
        "meeting_notes": 2000,
    }
    if max_tokens is None:
        max_tokens = DEFAULT_MAX_TOKENS.get(type, 500)

    headers = {"Content-Type": "application/json"}
    if provider["api_key"]:
        headers["Authorization"] = f"Bearer {provider['api_key']}"

    chosen_model = model or provider["text_model"]
    payload = {
        "model":           chosen_model,
        "messages":        messages,
        "stream":          False,
        "max_tokens":      max_tokens,
        # Server-side failover: the gateway walks this chain itself before
        # reporting a failure back to us — see _fallback_models_for's
        # docstring. Only populated when there's something left to fall
        # back to (an explicit single `model` from call_ai_response's
        # one-entry queue already covers the common case; direct callers
        # that pass no `model` get the full chain behind their primary).
        **({"fallback_models": fb} if (fb := _fallback_models_for(provider, chosen_model, needs_vision)) else {}),
    }

    provider_id = provider.get("_provider_id", provider["name"])

    timeout_seconds = int(os.environ.get("LLM_GATEWAY_TIMEOUT_SECONDS", "30"))

    try:
        response = requests.post(
            f"{provider['base_url']}/chat/completions",
            headers=headers,
            json=payload,
            timeout=timeout_seconds
        )
        response.raise_for_status()
        data = response.json()

        message = data["choices"][0]["message"]

        # Reasoning models (DeepSeek-reasoner, QwQ, etc.) sometimes return
        # the usable text in "reasoning_content" rather than "content",
        # or leave "content" empty. Prefer content when it's non-empty,
        # fall back to reasoning_content otherwise.
        content = (message.get("content") or "").strip()
        reasoning_content = (message.get("reasoning_content") or "").strip()
        raw = content or reasoning_content

        if not raw:
            logger.warning(
                f"AI sync call ({type or 'default'}): empty content "
                f"and reasoning_content from provider {provider.get('name')}"
            )
            raise ProviderCallError(
                "empty content and reasoning_content", status=None, provider_id=provider_id
            )

        return clean_ai_response(raw)

    except requests.exceptions.Timeout as e:
        logger.error(f"AI sync call ({type or 'default'}): timeout")
        raise ProviderCallError(f"timeout: {e}", status=None, provider_id=provider_id) from e

    except requests.exceptions.HTTPError as e:
        logger.error(f"AI sync call ({type or 'default'}) HTTP error: {e}")
        raise _wrap_request_exception(e, provider_id) from e

    except ProviderCallError:
        raise  # the "empty content" raise above — already correctly shaped, don't re-wrap

    except Exception as e:
        logger.error(f"AI sync call ({type or 'default'}) error: {e}", exc_info=True)
        raise ProviderCallError(str(e), status=None, provider_id=provider_id) from e


def _call_provider_sync(
    messages: list,
    provider: dict,
    type: str = "",
    max_tokens: int | None = None,
) -> str | None:
    """
    Non-streaming provider call for use in background threads.

    IMPORTANT — this name/signature is preserved EXACTLY as it existed
    before this pass, on purpose: websocket_threads.py's
    _call_learnora_for_thread / _call_learnora_action import this exact
    name (`from learnora import provider_manager, _call_provider_sync`,
    confirmed via direct grep across the supplied files) and treat its
    return value as `ai_text`, immediately doing `if not ai_text: return`
    — i.e. they depend on the ORIGINAL "returns None on any failure, no
    exception propagates" contract. Renaming or changing this function's
    behavior would have silently broken two real, currently-working call
    sites; instead, this is now a thin compatibility wrapper around the
    new classified engine (_call_provider_sync_raising, below), while
    call_ai_response's new queue-walking loop calls the raising version
    directly to get real per-category handling. Two names, one
    implementation underneath — not two copies of the request logic to
    keep in sync.

    This is a deliberate compatibility seam, not an oversight: rewriting
    those Thread-WebSocket call sites to build their own classified queue
    was out of scope for this pass (they're single-shot background-thread
    calls with their own already-correct "no provider available -> return
    early" handling, not a retry loop this change needs to touch). If
    those call sites should also get full queue-based fallback, that's
    worth its own follow-up rather than folding in unrequested here.
    """
    try:
        return _call_provider_sync_raising(messages, provider, type=type, max_tokens=max_tokens)
    except ProviderCallError as e:
        provider_manager.mark_provider_failed(provider["name"], str(e))
        return None


# ===========================================================
# call_ai_response — Document 1 §2.4 consolidation, now rebuilt around a
# classified single gateway call.
#
# At least four call sites (posts.py::ask_learnora_about_post,
# connections.py::get_connection_overview's non-streaming fallback,
# study_sessions.py::ai_ask_in_session, and threads.py's meeting-notes/
# action AI calls) each hand-rolled their own "call provider, on failure
# rotate, retry N times" loop — with subtly different retry counts/
# timeouts per call site. This function is the single implementation all
# of them should call instead.
#
# GATEWAY MIGRATION: _build_call_queue now returns at most ONE entry (the
# single gateway provider), because the gateway itself already walks the
# model's `fallback_models` chain — across every model, provider, and
# credential the gateway project owns — inside that one HTTP call before
# ever reporting a failure back here (see llm-gateway's "Automatic
# failover"). There is no second queue entry left to "advance to" the way
# the old per-provider×model queue had. classify_provider_error's four
# categories now only decide how this ONE attempt's failure affects the
# NEXT call_ai_response invocation, via provider_manager's cooldown:
#   KEY_FAULT          -> cool down (dead in practice — no gateway status
#                         maps to KEY_FAULT anymore; kept for defensive
#                         completeness, see classify_provider_error)
#   PROVIDER_TRANSIENT -> cool down. Unlike the pre-gateway version, this
#                         DOES start the short client-side cooldown now —
#                         there's no other key to rotate to, so protecting
#                         a struggling/exhausted gateway from being
#                         hammered on every subsequent request is the
#                         right reaction here, not "don't cool."
#   BAD_MODEL          -> evict just this model from the local fallback
#                         catalog; do NOT cool the gateway itself (the
#                         gateway is healthy — this one model name isn't).
#   NON_RETRYABLE      -> stop immediately, no cooldown (it's a request/
#                         config problem, not a gateway health problem).
#
# EXTERNAL CONTRACT UNCHANGED: still returns (text, diagnostics) with
# diagnostics = {"attempts", "provider", "errors"} — every one of the
# six existing callers (Connections_discovery.py, Threads_discovery.py,
# connections_crud.py, membership.py, messaging.py, threads_crud.py, plus
# study_sessions.py's own StudyAssistant-based streaming path) keeps
# working unchanged. max_retries is now effectively capped at 1 real
# attempt (there's only one queue entry), but the parameter and its
# plumbing are left in place rather than ripped out, in case a future
# gateway project is added as a genuinely separate second entry.
# ===========================================================

def call_ai_response(
    messages: list[dict],
    needs_vision: bool = False,
    max_retries: int = 2,
    *,
    call_type: str = "",
    max_tokens: int | None = None,
) -> tuple[str | None, dict]:
    """
    Get one complete (non-streaming) AI response, handling provider
    selection, rotation on failure, and retry internally.

    Returns (text, diagnostic_info):
      - text: the cleaned response string, or None if every attempt failed.
      - diagnostic_info: dict with at least {"attempts": int, "provider": str | None,
        "errors": list[str]} — useful for logging at the call site without
        that call site needing to track retry bookkeeping itself.

    This is for callers that want one complete string back. Callers that
    want the token-by-token streaming experience (learnora.py's own
    /api/chat endpoint, connections.py's SSE overview path) should keep
    using StudyAssistant.stream_response() directly — this function
    doesn't replace that path.
    """
    diagnostics = {"attempts": 0, "provider": None, "errors": []}

    queue = _build_call_queue(needs_vision=needs_vision)

    if not queue:
        diagnostics["errors"].append("no working provider available")
        return None, diagnostics

    max_attempts = max(1, max_retries + 1)

    for entry in queue[:max_attempts] if len(queue) > max_attempts else queue:
        provider = entry["provider"]
        model = entry["model"]

        diagnostics["attempts"] += 1
        diagnostics["provider"] = provider["name"]

        try:
            result = _call_provider_sync_raising(
                messages, provider, type=call_type, max_tokens=max_tokens, model=model, needs_vision=needs_vision
            )
            return result, diagnostics

        except ProviderCallError as e:
            category = classify_provider_error(e)
            diagnostics["errors"].append(f"provider {provider['name']} model {model} failed ({category}): {e}")
            logger.warning(f"[call_ai_response] {provider['name']}/{model} failed, category={category}: {e}")

            if category in ("KEY_FAULT", "PROVIDER_TRANSIENT"):
                # Single-gateway design: there's no other key/provider to
                # rotate to, so the reaction to a gateway-health failure is
                # a short cooldown, not a rotation — see the docstring
                # above for why this differs from the pre-gateway version.
                provider_manager.mark_provider_failed(provider["name"], str(e))
            elif category == "BAD_MODEL":
                provider_manager.evict_model(entry["provider_id"], model)
            else:  # NON_RETRYABLE
                logger.error(f"[call_ai_response] NON_RETRYABLE error, aborting fallback chain: {e}")
                break

            # Queue has at most one entry now (see _build_call_queue), so
            # this loop will not find another entry to try — it exists so
            # BAD_MODEL/cooldown bookkeeping above still runs before the
            # function returns its final (None, diagnostics).
            continue

    return None, diagnostics
