"""
services/ai_provider_service.py

Multi-provider AI access layer. Moved out of learnora.py per Document 1
§2.4 — this is the module every blueprint that needs an AI call now
imports from, instead of reaching into learnora.py's internals.

Owns:
  - ProviderCallError, classify_provider_error (AUDIT horizontal-scaling
    completion pass — direct port of the reference multiProvider.js
    project's utils/providerErrors.js taxonomy: KEY_FAULT /
    PROVIDER_TRANSIENT / BAD_MODEL / NON_RETRYABLE)
  - MultiProviderManager (provider loading, rotation, cooldown/blacklist —
    cooldown/blacklist/rotation-index/model-discovery-cache state is now
    Redis-backed, shared cross-instance, with a kill switch —
    AI_PROVIDER_REDIS_STATE_ENABLED — mirroring multiProvider.js's own
    MULTIPROVIDER_REDIS_STATE_ENABLED)
  - StudyAssistant (per-conversation streaming assistant; stream_response
    now classifies pre-stream/header-stage failures via
    classify_provider_error the same way call_ai_response does)
  - _call_provider_sync (non-streaming, ORIGINAL name/contract preserved
    exactly — used by websocket_threads.py's Thread-WebSocket meeting-
    notes/action-AI background callers) and _call_provider_sync_raising
    (the new classified engine underneath it, used directly by
    call_ai_response's queue-walking loop)
  - call_ai_response (the retry/rotation consolidation described in
    Document 1 §2.4 — now rebuilt around a flattened provider×model queue,
    _build_call_queue, matching multiProvider.js's buildProviderQueue/
    callWithFallback structure)
  - clean_ai_response, generate_conversation_title
  - Model priority lists

Per Document 2 §2's layering rule, this module has zero Flask dependency:
no `from flask import ...`, no `request`/`session`/`g`. Anything HTTP-layer
(routes, request parsing, response serialization) stays in
routes/student/learnora/*.

Import-time network call fix (Document 1 §5): the previous version of this
code (in learnora.py) called self._warm_model_discovery() from
MultiProviderManager.__init__, meaning a background thread hitting live
provider APIs was started the moment the module was imported — this broke
unit-testing/importing the module in CI without network access. That call
is now REMOVED from __init__. app.py's create_app() must call
`provider_manager.warm_model_discovery()` explicitly once, after blueprint
registration, as its own visible startup step.
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
# MODEL PRIORITY LISTS
#
# These serve two purposes:
#   1. Static fallback when dynamic discovery is unavailable.
#   2. Ranking template for discovered models (known models appear first
#      in priority order; unknown new models are appended as extras).
#
# Mistral exception: uses provider-managed alias IDs that are always
# current — no dynamic discovery needed, no manual updates required.
# ===========================================================

# Groq vision models — these support image input (multimodal).
# ============================================================
# GROQ
# ============================================================

GROQ_VISION_MODELS = {
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
}

GROQ_MODELS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3-32b",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
]


# ============================================================
# CEREBRAS
# ============================================================

CEREBRAS_MODELS = [
    "gpt-oss-120b",
]


# ============================================================
# MISTRAL
# ============================================================

MISTRAL_VISION_MODELS = {
    "mistral-medium-latest",
    "mistral-large-latest",
    "mistral-small-latest",
    "ministral-14b-latest",
    "ministral-8b-latest",
    "ministral-3b-latest",
}

MISTRAL_MODELS = [
    "mistral-medium-latest",
    "mistral-large-latest",
    "mistral-small-latest",
    "ministral-14b-latest",
    "ministral-8b-latest",
    "ministral-3b-latest",
]


# ============================================================
# OPENROUTER
# ============================================================

OPENROUTER_VISION_MODELS = {
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "nvidia/nemotron-3-nano-omni:free",
}

OPENROUTER_MODELS = [
    "openai/gpt-oss-120b:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "nvidia/nemotron-3-nano-omni:free",
    "nvidia/nemotron-nano-9b-v2:free",
]

OPENROUTER_FREE_ROUTER = "openrouter/free"


# Non-chat model filter: skip these during dynamic model discovery.
NON_CHAT_PATTERN = re.compile(
    r"whisper|embed|guard|tts|moderation|transcribe|ocr|safeguard|vision-only",
    re.IGNORECASE,
)

# Provider order — mirrors multiProvider.js PROVIDER_ORDER
PROVIDER_ORDER = ["cerebras", "groq", "mistral", "openrouter"]


# ===========================================================
# PROVIDER ERROR CLASSIFICATION
#
# Direct Python port of utils/providerErrors.js from the reference
# multiProvider.js project (supplied, read in full). Replaces the old
# per-call-site exception handling (_call_provider_sync's bare
# Timeout/HTTPError/Exception split, StudyAssistant._do_stream's
# _is_model_error string-matching) — every one of those old paths
# treated a failure identically regardless of WHY it failed, which is
# exactly the problem this taxonomy fixes: a 500 from a provider-wide
# outage used to cool the specific key exactly like a genuinely bad
# API key would, wasting that key's availability for an hour over a
# failure that had nothing to do with it.
#
# The four categories and their handling (see call_ai_response's fallback
# loop and StudyAssistant.stream_response below) mirror the reference
# file's classifyProviderError docstring exactly:
#
#   KEY_FAULT          — cool the specific key, try next key/provider.
#                        Attributable to the key itself (bad credentials,
#                        this key specifically hit its rate limit, or this
#                        key/account is out of quota — 402 Payment Required
#                        included, since that's an account-billing state,
#                        not a provider-wide outage).
#   PROVIDER_TRANSIENT — do NOT cool the key, try next key/provider.
#                        Provider-wide or network condition unrelated to
#                        this key's validity — penalizing the key would
#                        be incorrect and would waste its availability.
#   BAD_MODEL          — do NOT cool the key, skip this model specifically
#                        (not just this key), evict the model from the
#                        Redis-backed discovery cache so other requests/
#                        instances stop hitting it until the next natural
#                        refresh.
#   NON_RETRYABLE      — do not retry, raise immediately. Most likely a
#                        genuine application bug in how the request was
#                        built, not routine provider flakiness.
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


_KEY_FAULT_STATUSES = {401, 402, 403, 429}
_PROVIDER_TRANSIENT_STATUSES = {500, 502, 503, 504}
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
        re.search(r"not found|does not exist|invalid|decommission|unknown|unsupported", text)
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
# MULTI-PROVIDER API KEY MANAGER
# ===========================================================

class MultiProviderManager:
    """
    Manage multiple API providers and rotate between them.

    HORIZONTAL SCALING (AUDIT §5/§6 — MultiProviderManager runtime state):
    Previously, self.failed_providers / self._provider_type_failures /
    self._blacklisted_types / self.current_provider_index were plain
    instance attributes on this module-level singleton — each application
    instance had an independent view of which provider keys were failing
    or blacklisted. The audit flagged this exactly as multiProvider.js's
    own header comment (this codebase's sibling Node.js project, supplied
    as a reference pattern, NOT a file to run directly here — StudyHub is
    Flask/Python) already solved via Redis: "an instance that saw a key
    fail had no way to tell any other instance, so other instances kept
    sending traffic to a known-bad key."

    This class now mirrors that pattern using services/cache_service.py's
    real, already-proven get(key) / set(key, value, ttl_seconds=N) /
    delete(key) primitives (confirmed via their existing use across
    leaderboard_service.py, study_buddy.py, Threads_discovery.py,
    connection_service.py, post_service.py in this same codebase) — NOT
    multiProvider.js's own Node-specific redis.js helper names, which
    don't exist in this Python codebase.

    KILL SWITCH: AI_PROVIDER_REDIS_STATE_ENABLED (default enabled — set to
    the literal string 'false' to disable). Mirrors multiProvider.js's own
    MULTIPROVIDER_REDIS_STATE_ENABLED kill switch and its stated reasoning
    verbatim: this manager is the single highest-traffic file for every AI
    feature in the app, so a single environment-variable toggle that
    reverts to the previously-working, well-understood in-memory behavior,
    with no code deploy required, is the highest-value risk mitigation
    available for a change this central. When disabled, every method below
    falls back to the original plain-dict, per-process behavior — that
    code path is kept, not deleted, specifically so this switch works.

    FAIL-OPEN: every new Redis call here follows the same philosophy
    cache_service.py's own callers already rely on throughout this
    codebase — a Redis hiccup degrades precision (an instance may briefly
    retry a key another instance already knows is bad), it never raises
    out and breaks an AI call. Any cache_service.get/set that raises is
    caught and treated as a cache miss / no-op.

    NOT changed: self.providers (the pool itself) and _load_providers()
    stay exactly as they were — this is configuration read from
    os.getenv(...) at construction time, not runtime state. Every
    correctly-configured instance has identical environment variables, so
    there's nothing to synchronize there; multiProvider.js's own header
    comment makes this identical distinction for its own pool construction
    ("DELIBERATELY left local / per-process, unchanged").
    """

    # How many failures within the sliding window triggers a provider-level blacklist
    PROVIDER_FAILURE_THRESHOLD = 3
    # Sliding window for counting failures (seconds)
    PROVIDER_FAILURE_WINDOW = 300   # 5 minutes
    # How long a blacklisted provider stays locked out (seconds)
    PROVIDER_BLACKLIST_DURATION = 1800  # 30 minutes

    # Redis key prefixes — mirrors this codebase's existing "sh:{version}:"
    # convention (see cache_service.py's own callers, e.g. leaderboard_service.py's
    # "sh:1:lb:..." keys).
    _REDIS_COOLDOWN_PREFIX    = "sh:1:ai:cooldown:"       # per-key cooldown
    _REDIS_TYPE_FAILS_PREFIX  = "sh:1:ai:typefails:"      # provider-type failure timestamps
    _REDIS_BLACKLIST_PREFIX   = "sh:1:ai:blacklist:"      # provider-type blacklist flag
    _REDIS_ROTATION_KEY       = "sh:1:ai:rotation_index"  # shared round-robin index
    _REDIS_MODELS_PREFIX      = "sh:1:ai:models:"         # discovered model list cache
    _ROTATION_TTL_SECONDS     = 6 * 60 * 60  # rotation index itself isn't inherently
                                              # time-bound, but a TTL means a stale key
                                              # from a since-reconfigured deployment
                                              # (fewer providers than before) can't
                                              # wedge forever — self-heals within 6h.
    MODEL_CACHE_TTL_SECONDS   = 6 * 60 * 60  # matches multiProvider.js's MODEL_CACHE_TTL_S

    def __init__(self):
        self.providers = self._load_providers()
        self.current_provider_index = 0

        # Per-key cooldown  {provider_name: datetime} — used as the
        # in-memory fallback ONLY (kill switch off, or a Redis call
        # itself fails at runtime). The Redis-backed path never reads
        # this dict; see is_redis_state_enabled()/_is_key_cooling_redis
        # below.
        self.failed_providers: dict = {}
        self.cooldown_period = 3600   # 1-hour per-key cooldown

        # Per-provider-type failure tracking — in-memory fallback only,
        # same as above.
        # {provider_type: [datetime, ...]}  — rolling list of failure timestamps
        self._provider_type_failures: dict = {}
        # {provider_type: datetime}  — when the type was blacklisted
        self._blacklisted_types: dict = {}

        # NOTE (Document 1 §5 fix): _warm_model_discovery() is deliberately
        # NOT called here anymore. Starting a background thread that makes
        # live network calls as a side effect of __init__ (which itself ran
        # as a side effect of merely importing this module, since
        # `provider_manager = MultiProviderManager()` is instantiated at
        # module level below) made the module unsafe to import in a
        # test/CI context without network access. Model discovery is now an
        # explicit startup step — call `provider_manager.warm_model_discovery()`
        # once from app.py's create_app(), after blueprints are registered.

    # ----------------------------------------------------------
    # HORIZONTAL SCALING — kill switch + Redis helpers
    # ----------------------------------------------------------

    @staticmethod
    def is_redis_state_enabled() -> bool:
        """Mirrors multiProvider.js's isRedisStateEnabled() — default
        enabled, disabled only when the env var is the literal string
        'false'."""
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
        """Fail-open write: any exception is swallowed — a write that
        doesn't land just means the in-memory fallback path (if the kill
        switch is later flipped) or the next successful write is the
        source of truth instead."""
        try:
            from services import cache_service
            cache_service.set(key, value, ttl_seconds=ttl_seconds)
        except Exception as e:
            logger.warning(f"[AI_PROVIDER_REDIS] set({key}) failed (non-fatal): {e}")

    @staticmethod
    def _redis_delete(key):
        try:
            from services import cache_service
            cache_service.delete(key)
        except Exception as e:
            logger.warning(f"[AI_PROVIDER_REDIS] delete({key}) failed (non-fatal): {e}")

    # ----------------------------------------------------------
    # Provider loading
    # ----------------------------------------------------------

    def _load_providers(self):
        """Load Cerebras, Groq, Mistral, and OpenRouter providers from environment variables.
        Each provider supports multiple keys (e.g. CEREBRAS_API_KEY_1 … _5).
        Falls back to the no-suffix var (CEREBRAS_API_KEY) when only one key exists.
        Provider order mirrors multiProvider.js: cerebras → groq → mistral → openrouter.
        """
        providers = []

        PROVIDER_DEFS = [
            {
                "id":         "cerebras",
                "type":       "cerebras",
                "base_url":   "https://api.cerebras.ai/v1",
                "env_prefix": "CEREBRAS_API_KEY",
                "max_keys":   10,
                "models":     CEREBRAS_MODELS,
                # Cerebras currently has no vision-capable models on public endpoints.
                "vision_models": set(),
            },
            {
                "id":         "groq",
                "type":       "groq",
                "base_url":   "https://api.groq.com/openai/v1",
                "env_prefix": "GROQ_API_KEY",
                "max_keys":   10,
                "models":     GROQ_MODELS,
                # llama-4-scout supports multimodal image input on Groq.
                "vision_models": GROQ_VISION_MODELS,
            },
            {
                "id":         "mistral",
                "type":       "mistral",
                "base_url":   "https://api.mistral.ai/v1",
                "env_prefix": "MISTRAL_API_KEY",
                "max_keys":   5,
                "models":     MISTRAL_MODELS,
                "vision_models": set(),
            },
            {
                "id":         "openrouter",
                "type":       "openrouter",
                "base_url":   "https://openrouter.ai/api/v1",
                "env_prefix": "OPENROUTER_API_KEY",
                "max_keys":   5,
                "models":     OPENROUTER_MODELS,
                # llama-4-scout supports multimodal image input on OpenRouter.
                "vision_models": OPENROUTER_VISION_MODELS,
            },
        ]

        for defn in PROVIDER_DEFS:
            keys = []
            for i in range(1, defn["max_keys"] + 1):
                key = os.getenv(f"{defn['env_prefix']}_{i}")
                if key and key.strip():
                    keys.append((i, key.strip()))

            # Single-key fallback (no suffix)
            if not keys:
                key = os.getenv(defn["env_prefix"])
                if key and key.strip():
                    keys.append((0, key.strip()))

            if not keys:
                continue

            logger.info(f"🔧 {defn['id']}: {len(keys)} key(s) loaded")

            primary_model   = defn["models"][0]
            vision_models   = defn.get("vision_models", set())
            # Primary vision model: first model in models list that supports vision,
            # or None if none of the listed models support vision.
            primary_vision  = next((m for m in defn["models"] if m in vision_models), None)
            supports_vision = primary_vision is not None

            for key_index, api_key in keys:
                providers.append({
                    "name":                   f"{defn['id']}_{key_index}",
                    "api_key":                api_key,
                    "base_url":               defn["base_url"],
                    "text_model":             primary_model,
                    "vision_model":           primary_vision,
                    "supports_vision":        supports_vision,
                    "type":                   defn["type"],
                    "text_model_fallbacks":   defn["models"],
                    "vision_model_fallbacks": [m for m in defn["models"] if m in vision_models],
                    # Store reference to provider definition for dynamic updates
                    "_provider_id":           defn["id"],
                    "_vision_models":         vision_models,
                })

        logger.info(f"🔧 Loaded {len(providers)} provider slot(s) across {PROVIDER_ORDER}")
        return providers

    # ----------------------------------------------------------
    # Dynamic model discovery (background)
    # ----------------------------------------------------------

    def warm_model_discovery(self):
        """
        PUBLIC entry point for kicking off background model discovery.

        Document 1 §5: this used to be a private method (_warm_model_discovery)
        called automatically from __init__. It's now public and must be called
        explicitly — app.py's create_app() calls this once, after all
        blueprints are registered, as a visible startup step rather than an
        import side effect.
        """
        self._warm_model_discovery()

    def _warm_model_discovery(self):
        """
        Spawn a background thread to discover available models from each
        provider's /v1/models endpoint. Updates each provider slot's model
        lists in-place once discovery completes.

        Mistral and OpenRouter are skipped — Mistral uses provider-managed
        aliases that are already self-updating; OpenRouter uses a static list.

        HORIZONTAL SCALING: before fetching, checks the shared Redis cache
        (populated by any instance within the last MODEL_CACHE_TTL_SECONDS)
        first. On a hit, applies the cached ranked list directly with no
        HTTP call at all — closing the gap the audit names explicitly:
        "two instances could independently discover slightly different
        model lists from a transiently-inconsistent provider response,
        meaning two requests to the same feature, routed to different
        instances, could silently use different underlying models." On a
        miss, this instance fetches (as before) and writes the result to
        the shared cache so other instances hit the cache instead of also
        calling the provider's /v1/models endpoint redundantly.

        Not lock-guarded (see module-level note in the audit-completion
        summary): this codebase's real services/distributed_lock.py was
        never supplied to verify its exact call shape, so rather than
        guess at an unproven interface, this uses the same get/set
        primitives as everything else here. Worst case on a cache miss
        that many instances hit near-simultaneously is a handful of
        redundant /v1/models calls in the same few-hundred-millisecond
        window — a minor efficiency gap, not a correctness one (unlike
        the never-shared-at-all behavior this replaces).
        """
        # Group provider slots by provider_id to avoid redundant API calls
        seen: dict = {}
        for p in self.providers:
            pid = p.get("_provider_id")
            if pid and pid not in seen:
                seen[pid] = p

        def _discover():
            for pid, provider_slot in seen.items():
                if pid in ("mistral", "openrouter"):
                    continue  # mistral aliases are self-updating; openrouter uses static list

                if self.is_redis_state_enabled():
                    cached = self._redis_get(f"{self._REDIS_MODELS_PREFIX}{pid}")
                    if cached:
                        self._apply_ranked_models(pid, cached)
                        logger.info(f"✅ {pid}: applied {len(cached)} model(s) from shared Redis cache — no HTTP call")
                        continue

                self._fetch_and_apply_models(pid, provider_slot)

        t = threading.Thread(target=_discover, daemon=True)
        t.start()

    def _apply_ranked_models(self, provider_id: str, ranked: list):
        """Apply an already-ranked model list to every slot belonging to
        provider_id — shared by both the Redis-cache-hit path above and
        _fetch_and_apply_models' own fresh-fetch path below, so there's
        one implementation of "what applying a ranked list means," not two
        copies that could drift."""
        representative = next((p for p in self.providers if p.get("_provider_id") == provider_id), None)
        vision_models = representative.get("_vision_models", set()) if representative else set()
        primary_model  = ranked[0]
        primary_vision = next((m for m in ranked if m in vision_models), None)

        for p in self.providers:
            if p.get("_provider_id") == provider_id:
                p["text_model"]             = primary_model
                p["text_model_fallbacks"]   = ranked
                p["vision_model"]           = primary_vision
                p["supports_vision"]        = primary_vision is not None
                p["vision_model_fallbacks"] = [m for m in ranked if m in vision_models]

    def _fetch_and_apply_models(self, provider_id: str, representative_slot: dict):
        """
        Fetch /v1/models for a provider and update all matching provider slots
        with the ranked model list. Also writes the ranked list to the
        shared Redis cache (kill-switch permitting) so other instances can
        apply it without their own HTTP call — see _warm_model_discovery's
        docstring above.
        """
        base_url   = representative_slot["base_url"]
        api_key    = representative_slot["api_key"]
        priority   = CEREBRAS_MODELS if provider_id == "cerebras" else GROQ_MODELS

        try:
            resp = requests.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=8,
            )
            resp.raise_for_status()
            data   = resp.json()
            all_ids = [m.get("id", "") for m in data.get("data", []) if m.get("id")]

            # Rank: known-priority models first (in order), then unknown chat models.
            known   = [m for m in priority if m in all_ids]
            unknown = [m for m in all_ids if m not in priority and not NON_CHAT_PATTERN.search(m)]
            ranked  = known + sorted(unknown)

            if not ranked:
                logger.warning(f"⚠️  {provider_id}: discovery returned 0 usable models — keeping static list")
                return

            logger.info(f"✅ {provider_id}: discovered {len(ranked)} chat model(s) — updating provider slots")

        except Exception as e:
            logger.warning(f"⚠️  {provider_id}: model discovery failed ({e}) — keeping static list")
            return

        self._apply_ranked_models(provider_id, ranked)

        if self.is_redis_state_enabled():
            self._redis_set(f"{self._REDIS_MODELS_PREFIX}{provider_id}", ranked, self.MODEL_CACHE_TTL_SECONDS)

    def evict_model(self, provider_id: str, model_id: str):
        """
        Direct port of evictModelFromRedisCache. Called when
        classify_provider_error returns BAD_MODEL — removes the one dead/
        renamed/decommissioned model from BOTH the shared Redis-cached
        list and every in-process provider slot for that provider, so
        this instance AND every other instance stop offering that model
        until the next natural MODEL_CACHE_TTL_SECONDS refresh, without
        cooling the key that happened to be used when the bad model was
        discovered (the key itself did nothing wrong).

        DEVIATION FROM THE REFERENCE, flagged explicitly rather than
        silently approximated: the JS original preserves the cache
        entry's REMAINING TTL when rewriting it (via getRawClient().ttl(),
        so eviction doesn't reset the natural refresh clock). StudyHub's
        real cache_service.py (confirmed via direct read) exposes no TTL-
        introspection or raw-client escape hatch — every other caller in
        this codebase goes through get/set/delete only, and reaching past
        that into extensions.redis_client directly would be inventing
        capability no other file in this codebase uses. This re-sets with
        a full MODEL_CACHE_TTL_SECONDS instead, which means an eviction
        modestly extends how long the corrected list stays cached — a
        minor, honestly-labeled simplification, not a silent behavior
        difference.
        """
        if not self.is_redis_state_enabled():
            # In-memory-only fallback: just filter the current in-process
            # list — no cross-instance cache to update.
            for p in self.providers:
                if p.get("_provider_id") == provider_id:
                    p["text_model_fallbacks"] = [m for m in p.get("text_model_fallbacks", []) if m != model_id]
                    p["vision_model_fallbacks"] = [m for m in p.get("vision_model_fallbacks", []) if m != model_id]
            return

        cache_key = f"{self._REDIS_MODELS_PREFIX}{provider_id}"
        current = self._redis_get(cache_key)
        if not current:
            return  # nothing cached to evict from
        updated = [m for m in current if m != model_id]
        if len(updated) == len(current):
            return  # model wasn't in the cached list — nothing to evict
        self._redis_set(cache_key, updated, self.MODEL_CACHE_TTL_SECONDS)
        if updated:
            self._apply_ranked_models(provider_id, updated)
        logger.warning(f"🚫 Evicted bad model '{model_id}' from {provider_id}'s cached model list")

    # ----------------------------------------------------------
    # Provider-type blacklist helpers
    # ----------------------------------------------------------

    def _record_provider_type_failure(self, provider_type: str):
        """
        Record a failure for the given provider type and blacklist the entire
        type if it has exceeded PROVIDER_FAILURE_THRESHOLD failures within
        PROVIDER_FAILURE_WINDOW seconds.

        HORIZONTAL SCALING: when Redis state is enabled, the sliding-window
        timestamp list is read/pruned/written through the shared cache
        instead of the process-local self._provider_type_failures dict, so
        a failure recorded by one instance is visible to every other
        instance's very next get_working_provider() call — closing the
        exact gap the audit names: "an instance that saw a key fail had no
        way to tell any other instance."
        """
        now = datetime.datetime.utcnow()
        window_start = now - datetime.timedelta(seconds=self.PROVIDER_FAILURE_WINDOW)

        if self.is_redis_state_enabled():
            redis_key = f"{self._REDIS_TYPE_FAILS_PREFIX}{provider_type}"
            raw = self._redis_get(redis_key)
            timestamps = []
            if raw:
                for ts_str in raw:
                    try:
                        t = datetime.datetime.fromisoformat(ts_str)
                        if t >= window_start:
                            timestamps.append(t)
                    except (ValueError, TypeError):
                        continue
            timestamps.append(now)
            self._redis_set(
                redis_key,
                [t.isoformat() for t in timestamps],
                self.PROVIDER_FAILURE_WINDOW,
            )
        else:
            # In-memory fallback (kill switch off)
            timestamps = self._provider_type_failures.get(provider_type, [])
            timestamps = [t for t in timestamps if t >= window_start]
            timestamps.append(now)
            self._provider_type_failures[provider_type] = timestamps

        failure_count = len(timestamps)
        logger.info(
            f"📊 Provider type '{provider_type}' failure count in last "
            f"{self.PROVIDER_FAILURE_WINDOW}s: {failure_count}/{self.PROVIDER_FAILURE_THRESHOLD}"
        )

        if failure_count >= self.PROVIDER_FAILURE_THRESHOLD:
            already_blacklisted = (
                self._redis_get(f"{self._REDIS_BLACKLIST_PREFIX}{provider_type}") is not None
                if self.is_redis_state_enabled()
                else provider_type in self._blacklisted_types
            )
            if not already_blacklisted:
                logger.error(
                    f"🚫 Provider type '{provider_type}' has failed {failure_count} times "
                    f"— blacklisting ALL {provider_type} keys for "
                    f"{self.PROVIDER_BLACKLIST_DURATION // 60} min"
                )
            if self.is_redis_state_enabled():
                self._redis_set(
                    f"{self._REDIS_BLACKLIST_PREFIX}{provider_type}",
                    now.isoformat(),
                    self.PROVIDER_BLACKLIST_DURATION,
                )
            else:
                self._blacklisted_types[provider_type] = now

    def _is_provider_type_blacklisted(self, provider_type: str) -> bool:
        """
        Return True if the provider type is currently blacklisted.

        HORIZONTAL SCALING: reads the shared Redis flag when enabled. The
        Redis key carries its own TTL (PROVIDER_BLACKLIST_DURATION), so
        expiry is handled by Redis itself rather than the manual elapsed-
        time check the in-memory fallback still does below — a stale key
        simply stops existing once its TTL lapses, and cache_service.get
        returning None is indistinguishable from "never blacklisted,"
        which is exactly the semantics this method needs.
        """
        if self.is_redis_state_enabled():
            blacklisted_at = self._redis_get(f"{self._REDIS_BLACKLIST_PREFIX}{provider_type}")
            return blacklisted_at is not None

        blacklisted_at = self._blacklisted_types.get(provider_type)
        if not blacklisted_at:
            return False
        elapsed = (datetime.datetime.utcnow() - blacklisted_at).total_seconds()
        if elapsed >= self.PROVIDER_BLACKLIST_DURATION:
            # Blacklist expired — clear it and reset failure counters
            del self._blacklisted_types[provider_type]
            self._provider_type_failures.pop(provider_type, None)
            logger.info(f"✅ Provider type '{provider_type}' blacklist expired — re-enabling")
            return False
        return True

    # ----------------------------------------------------------
    # Runtime helpers
    # ----------------------------------------------------------

    def _is_key_cooling(self, provider_name: str) -> bool:
        """
        HORIZONTAL SCALING: per-key cooldown check. When Redis state is
        enabled, this is the single source of truth (a key marked failed
        by ANY instance is visible to every instance's next
        get_working_provider() call) — replaces the old
        "self.failed_providers, filtered by elapsed time on every call"
        approach with a Redis key whose own TTL (cooldown_period) is the
        expiry mechanism, mirroring _is_provider_type_blacklisted's
        identical reasoning above.
        """
        if self.is_redis_state_enabled():
            return self._redis_get(f"{self._REDIS_COOLDOWN_PREFIX}{provider_name}") is not None

        now = datetime.datetime.utcnow()
        self.failed_providers = {
            name: fail_time for name, fail_time in self.failed_providers.items()
            if (now - fail_time).total_seconds() < self.cooldown_period
        }
        return provider_name in self.failed_providers

    def get_working_provider(self, needs_vision=False):
        """Get next working provider, skipping blacklisted provider types and failed keys."""
        if not self.providers:
            logger.error("❌ No API providers configured!")
            return None

        attempts = 0
        while attempts < len(self.providers):
            provider = self.providers[self._get_rotation_index()]
            provider_type = provider.get("_provider_id", provider["name"])

            # Skip entire provider type if blacklisted
            if self._is_provider_type_blacklisted(provider_type):
                logger.info(f"⏭️ Skipping {provider['name']} — provider type '{provider_type}' is blacklisted")
                self.rotate()
                attempts += 1
                continue

            # Skip individual failed key
            if self._is_key_cooling(provider["name"]):
                self.rotate()
                attempts += 1
                continue

            if needs_vision and not provider["supports_vision"]:
                logger.info(f"⏭️ Skipping {provider['name']} (no vision support)")
                self.rotate()
                attempts += 1
                continue

            logger.info(f"✅ Using provider: {provider['name']}")
            return provider

        logger.error("❌ All providers are in cooldown or blacklisted!")
        return None

    def mark_provider_failed(self, provider_name: str, error_message: str = ""):
        """
        Mark a specific provider key as failed (per-key cooldown) AND record
        a failure against its provider type so repeated failures trigger a
        full type-level blacklist.

        HORIZONTAL SCALING: the cooldown write goes to the shared Redis key
        (kill-switch permitting) instead of only this process's local dict,
        so every other instance's next _is_key_cooling() check for this
        exact key sees the failure immediately — this is the specific gap
        the audit names: "other instances kept sending traffic to a
        known-bad key."
        """
        now = datetime.datetime.utcnow()
        if self.is_redis_state_enabled():
            self._redis_set(f"{self._REDIS_COOLDOWN_PREFIX}{provider_name}", now.isoformat(), self.cooldown_period)
        else:
            self.failed_providers[provider_name] = now
        logger.warning(f"⚠️ Provider key '{provider_name}' failed: {error_message}")

        # Find the provider type for this key and record the type-level failure
        for p in self.providers:
            if p["name"] == provider_name:
                provider_type = p.get("_provider_id", provider_name)
                self._record_provider_type_failure(provider_type)
                break

    def _get_rotation_index(self) -> int:
        """
        HORIZONTAL SCALING: shared round-robin index. When Redis state is
        enabled, reads the shared counter instead of this instance's own
        current_provider_index, so every instance round-robins against the
        same shared position rather than each independently starting from
        0 — mirrors multiProvider.js's own reasoning that per-instance
        rotation state is a real (if lower-severity) correctness gap, not
        just the blacklist/cooldown state.

        Modulo is applied here (not stored) so a since-changed provider
        count (a redeploy that adds/removes a key) can never produce an
        out-of-range index — same defensive shape rotate() already used
        before this change.
        """
        if self.is_redis_state_enabled():
            raw = self._redis_get(self._REDIS_ROTATION_KEY)
            idx = int(raw) if raw is not None else 0
        else:
            idx = self.current_provider_index
        return idx % len(self.providers) if self.providers else 0

    def rotate(self):
        """Move to next provider."""
        if self.is_redis_state_enabled():
            current = self._get_rotation_index()
            next_idx = (current + 1) % len(self.providers) if self.providers else 0
            self._redis_set(self._REDIS_ROTATION_KEY, next_idx, self._ROTATION_TTL_SECONDS)
        else:
            self.current_provider_index = (self.current_provider_index + 1) % len(self.providers)

    def get_stats(self):
        """Get provider statistics."""
        provider_details = []
        for p in self.providers:
            provider_type = p.get("_provider_id", p["name"])
            provider_details.append({
                "name": p["name"],
                "provider_type": provider_type,
                "text_model": p.get("text_model"),
                "supports_vision": p.get("supports_vision", False),
                "vision_model": p.get("vision_model"),
                "available_models": len(p.get("text_model_fallbacks", [])),
                "key_failed": self._is_key_cooling(p["name"]),
                "type_blacklisted": self._is_provider_type_blacklisted(provider_type),
                "type_failure_count": self._get_type_failure_count(provider_type),
            })

        blacklisted_info = {}
        seen_types = {p.get("_provider_id", p["name"]) for p in self.providers}
        for pt in seen_types:
            if not self._is_provider_type_blacklisted(pt):
                continue
            if self.is_redis_state_enabled():
                ts_raw = self._redis_get(f"{self._REDIS_BLACKLIST_PREFIX}{pt}")
                ts = datetime.datetime.fromisoformat(ts_raw) if ts_raw else datetime.datetime.utcnow()
            else:
                ts = self._blacklisted_types.get(pt, datetime.datetime.utcnow())
            elapsed = (datetime.datetime.utcnow() - ts).total_seconds()
            remaining = max(0, self.PROVIDER_BLACKLIST_DURATION - elapsed)
            blacklisted_info[pt] = {
                "blacklisted_at": ts.isoformat(),
                "remaining_seconds": int(remaining),
            }

        return {
            "total_providers": len(self.providers),
            "active_providers": sum(
                1 for p in self.providers
                if not self._is_key_cooling(p["name"])
                and not self._is_provider_type_blacklisted(p.get("_provider_id", p["name"]))
            ),
            "failed_keys": [p["name"] for p in self.providers if self._is_key_cooling(p["name"])],
            "blacklisted_provider_types": blacklisted_info,
            "current_provider": self.providers[self._get_rotation_index()]["name"] if self.providers else None,
            "providers": provider_details,
            "state_source": "redis" if self.is_redis_state_enabled() else "in-memory",
        }

    def _get_type_failure_count(self, provider_type: str) -> int:
        """Shared by get_stats — reads whichever backing store is active,
        same split as every other accessor above."""
        if self.is_redis_state_enabled():
            raw = self._redis_get(f"{self._REDIS_TYPE_FAILS_PREFIX}{provider_type}")
            return len(raw) if raw else 0
        return len(self._provider_type_failures.get(provider_type, []))




# Initialize provider manager (module-level singleton — every caller across
# the app shares this one instance, which is what makes provider
# failure/blacklist state consistent within a single process).
#
# NOTE: this line runs at IMPORT time (as it did before), but it no longer
# triggers a network call — MultiProviderManager.__init__ only loads env
# vars and sets up in-memory dicts. warm_model_discovery() must be called
# separately and explicitly (see app.py).
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
            payload = {
                "model": provider["text_model"],
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
        providers (Cerebras in particular) run strict schema validation on
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

    def build_messages(self, user_input, extracted_data, mode, post_content=None):
        """
        Build the full message array for the API call.

        Vision handling:
          - If self.vision_active is True: images are embedded as base64 data URIs
            inside image_url content parts (standard OpenAI vision format).
          - If self.vision_active is False: image items are replaced with a plain
            text notice so non-vision models don't crash or silently ignore them.
        """
        messages = []

        messages.append({"role": "system", "content": self.base_system})
        messages.append({"role": "system", "content": self.get_mode_prompt(mode)})

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
        MAX_MODEL_RETRIES = max(len(CEREBRAS_MODELS), len(GROQ_MODELS), len(MISTRAL_MODELS), len(OPENROUTER_MODELS))
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
                # CEREBRAS_MODELS/etc. against the same cooled key before
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
                # Provider-wide/network issue — don't cool the key, but
                # also don't keep hammering the SAME provider with a
                # different model; a 503 is not model-specific, so
                # advance_to_fallback_model would just repeat the same
                # failure under a different model name. Let the caller
                # rotate to a different provider instead.
                logger.warning(
                    f"⚠️ {self.provider['name']} hit a PROVIDER_TRANSIENT error mid-stream — "
                    "switching providers rather than retrying it"
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
        }

        logger.info(f"🚀 Streaming — provider: {self.provider['name']}, model: {self.model}")
        endpoint_url = f"{self.provider['base_url']}/chat/completions"

        try:
            response = requests.post(
                endpoint_url,
                headers=headers,
                json=data,
                stream=True,
                timeout=60
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
    Direct structural port of multiProvider.js's buildProviderQueue.

    Flattens every healthy provider slot's full model fallback list into
    one ordered queue of {provider, model} attempts, instead of the old
    nested "pick one provider, retry its whole model list internally, on
    exhaustion rotate to the next provider" two-level loop. This is the
    architectural shift the reference file's own queue-then-walk design
    represents — building the whole fallback plan up front means the
    classified-error handling below (mirroring callWithFallback's four-
    branch catch) can cleanly fall through to the next queue entry
    regardless of whether that entry is a different model on the SAME
    provider or the next provider entirely, matching the reference file's
    exact walk order (provider order, then model order within a provider,
    per multiProvider.js's own queue construction).

    Reuses provider_manager's already-Redis-backed health checks
    (_is_key_cooling / _is_provider_type_blacklisted) rather than
    duplicating that logic — a provider/key that's cooling or blacklisted
    on ANY instance is correctly excluded here too, same cross-instance
    guarantee as get_working_provider already has.
    """
    queue = []
    for provider in provider_manager.providers:
        provider_type = provider.get("_provider_id", provider["name"])
        if provider_manager._is_provider_type_blacklisted(provider_type):
            continue
        if provider_manager._is_key_cooling(provider["name"]):
            continue
        if needs_vision and not provider.get("supports_vision"):
            continue

        models = provider.get("vision_model_fallbacks") if (needs_vision and provider.get("supports_vision")) \
            else provider.get("text_model_fallbacks", [])
        if not models:
            continue

        for model in models:
            queue.append({"provider": provider, "model": model, "provider_id": provider_type})

    return queue


def _call_provider_sync_raising(
    messages: list,
    provider: dict,
    type: str = "",
    max_tokens: int | None = None,
    model: str | None = None,
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

    payload = {
        "model":      model or provider["text_model"],
        "messages":   messages,
        "stream":     False,
        "max_tokens": max_tokens
    }

    provider_id = provider.get("_provider_id", provider["name"])

    try:
        response = requests.post(
            f"{provider['base_url']}/chat/completions",
            headers=headers,
            json=payload,
            timeout=30
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
# classified fallback queue (AUDIT horizontal-scaling completion pass —
# ported from multiProvider.js's callWithFallback, using the real
# classify_provider_error taxonomy from providerErrors.js, both supplied
# and read in full rather than approximated).
#
# Before this migration, at least four call sites (posts.py::
# ask_learnora_about_post, connections.py::get_connection_overview's
# non-streaming fallback, study_sessions.py::ai_ask_in_session, and
# threads.py's meeting-notes/action AI calls) each hand-rolled their own
# "call provider, on failure rotate, retry N times" loop — with subtly
# different retry counts/timeouts per call site. This function is the
# single implementation all of them should call instead.
#
# What changed in THIS pass: the internal retry mechanism. Previously,
# every failure was treated identically (rotate, retry, up to
# max_retries+1 total attempts across DIFFERENT PROVIDERS only — never
# tried a second model on the same provider). Now walks a flattened
# provider×model queue (_build_call_queue) and reacts differently per
# classify_provider_error's four categories:
#   KEY_FAULT          -> cool the key, advance to next queue entry
#   PROVIDER_TRANSIENT -> do NOT cool the key, advance to next queue entry
#   BAD_MODEL          -> do NOT cool the key, evict just this model,
#                         advance to next queue entry
#   NON_RETRYABLE       -> stop immediately, don't burn through the rest
#                         of the queue on a request that will never
#                         succeed anywhere
#
# EXTERNAL CONTRACT UNCHANGED: still returns (text, diagnostics) with
# diagnostics = {"attempts", "provider", "errors"} — every one of the
# six existing callers (Connections_discovery.py, Threads_discovery.py,
# connections_crud.py, membership.py, messaging.py, threads_crud.py, plus
# study_sessions.py's own StudyAssistant-based streaming path) keeps
# working unchanged. max_retries is now interpreted as "maximum queue
# entries to attempt" rather than "maximum provider rotations" — for a
# request that fails identically everywhere, this means MORE distinct
# attempts than before (every model on every provider, not just one
# model per provider), which is strictly more resilient, not less.
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
            result = _call_provider_sync_raising(messages, provider, type=call_type, max_tokens=max_tokens, model=model)
            return result, diagnostics

        except ProviderCallError as e:
            category = classify_provider_error(e)
            diagnostics["errors"].append(f"provider {provider['name']} model {model} failed ({category}): {e}")
            logger.warning(f"[call_ai_response] {provider['name']}/{model} failed, category={category}: {e}")

            if category == "KEY_FAULT":
                provider_manager.mark_provider_failed(provider["name"], str(e))
            elif category == "BAD_MODEL":
                provider_manager.evict_model(entry["provider_id"], model)
            elif category == "PROVIDER_TRANSIENT":
                pass  # deliberately do NOT cool the key — see classify_provider_error's docstring
            else:  # NON_RETRYABLE
                logger.error(f"[call_ai_response] NON_RETRYABLE error, aborting fallback chain: {e}")
                break

            # Attempts remaining were already capped by max_attempts above;
            # simply continue to the next queue entry for KEY_FAULT/
            # BAD_MODEL/PROVIDER_TRANSIENT.
            continue

    return None, diagnostics
