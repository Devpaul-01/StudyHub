"""
services/ai_provider_service.py

Multi-provider AI access layer. Moved out of learnora.py per Document 1
§2.4 — this is the module every blueprint that needs an AI call now
imports from, instead of reaching into learnora.py's internals.

Owns:
  - MultiProviderManager (provider loading, rotation, cooldown/blacklist)
  - StudyAssistant (per-conversation streaming assistant)
  - _call_provider_sync (non-streaming, used by background/WebSocket callers)
  - call_ai_response (NEW — the retry/rotation consolidation described in
    Document 1 §2.4: drains a full non-streaming response, handling
    provider rotation and retry internally, so callers don't each
    hand-roll their own loop)
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
GROQ_VISION_MODELS = {
    "meta-llama/llama-4-scout-17b-16e-instruct",
}

CEREBRAS_MODELS = [
    "gpt-oss-120b"      # current production model on Cerebras (June 2026)
]

GROQ_MODELS = [
    "openai/gpt-oss-120b",                    # Best reasoning & complex tasks
    "meta-llama/llama-4-scout-17b-16e-instruct", # Vision + multimodal + strong general chat
    "qwen/qwen3-32b",                         # Fast, smart, cost-efficient middle tier
]

MISTRAL_MODELS = [
    "mistral-large-2512",     # Best reasoning & quality
    "mistral-medium-latest",  # Balanced quality/cost
    "ministral-3b-2512",      # Ultra-fast & cheap fallback
]

# OpenRouter vision models — these support image input (multimodal).
OPENROUTER_VISION_MODELS = {
    "meta-llama/llama-4-scout",
}
OPENROUTER_MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free", # Default
    "google/gemma-4-31b-it:free",            # Vision tasks
    "nousresearch/hermes-3-405b:free",       # Escalation
]


# Non-chat model filter: skip these during dynamic model discovery.
NON_CHAT_PATTERN = re.compile(
    r"whisper|embed|guard|tts|moderation|transcribe|ocr|safeguard|vision-only",
    re.IGNORECASE,
)

# Provider order — mirrors multiProvider.js PROVIDER_ORDER
PROVIDER_ORDER = ["cerebras", "groq", "mistral", "openrouter"]


# ===========================================================
# MULTI-PROVIDER API KEY MANAGER
# ===========================================================

class MultiProviderManager:
    """Manage multiple API providers and rotate between them"""

    # How many failures within the sliding window triggers a provider-level blacklist
    PROVIDER_FAILURE_THRESHOLD = 3
    # Sliding window for counting failures (seconds)
    PROVIDER_FAILURE_WINDOW = 300   # 5 minutes
    # How long a blacklisted provider stays locked out (seconds)
    PROVIDER_BLACKLIST_DURATION = 1800  # 30 minutes

    def __init__(self):
        self.providers = self._load_providers()
        self.current_provider_index = 0

        # Per-key cooldown  {provider_name: datetime}  — unchanged
        self.failed_providers: dict = {}
        self.cooldown_period = 3600   # 1-hour per-key cooldown

        # Per-provider-type failure tracking
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
                self._fetch_and_apply_models(pid, provider_slot)

        t = threading.Thread(target=_discover, daemon=True)
        t.start()

    def _fetch_and_apply_models(self, provider_id: str, representative_slot: dict):
        """
        Fetch /v1/models for a provider and update all matching provider slots
        with the ranked model list.
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

        # Determine vision-capable models in the discovered set
        vision_models = representative_slot.get("_vision_models", set())
        primary_model  = ranked[0]
        primary_vision = next((m for m in ranked if m in vision_models), None)

        # Apply to every slot belonging to this provider
        for p in self.providers:
            if p.get("_provider_id") == provider_id:
                p["text_model"]            = primary_model
                p["text_model_fallbacks"]  = ranked
                p["vision_model"]          = primary_vision
                p["supports_vision"]       = primary_vision is not None
                p["vision_model_fallbacks"] = [m for m in ranked if m in vision_models]

    # ----------------------------------------------------------
    # Provider-type blacklist helpers
    # ----------------------------------------------------------

    def _record_provider_type_failure(self, provider_type: str):
        """
        Record a failure for the given provider type and blacklist the entire
        type if it has exceeded PROVIDER_FAILURE_THRESHOLD failures within
        PROVIDER_FAILURE_WINDOW seconds.
        """
        now = datetime.datetime.utcnow()
        window_start = now - datetime.timedelta(seconds=self.PROVIDER_FAILURE_WINDOW)

        # Prune old failures outside the sliding window
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
            if provider_type not in self._blacklisted_types:
                logger.error(
                    f"🚫 Provider type '{provider_type}' has failed {failure_count} times "
                    f"— blacklisting ALL {provider_type} keys for "
                    f"{self.PROVIDER_BLACKLIST_DURATION // 60} min"
                )
            self._blacklisted_types[provider_type] = now

    def _is_provider_type_blacklisted(self, provider_type: str) -> bool:
        """Return True if the provider type is currently blacklisted."""
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

    def get_working_provider(self, needs_vision=False):
        """Get next working provider, skipping blacklisted provider types and failed keys."""
        if not self.providers:
            logger.error("❌ No API providers configured!")
            return None

        # Clear expired per-key cooldowns
        now = datetime.datetime.utcnow()
        self.failed_providers = {
            name: fail_time for name, fail_time in self.failed_providers.items()
            if (now - fail_time).total_seconds() < self.cooldown_period
        }

        attempts = 0
        while attempts < len(self.providers):
            provider = self.providers[self.current_provider_index]
            provider_type = provider.get("_provider_id", provider["name"])

            # Skip entire provider type if blacklisted
            if self._is_provider_type_blacklisted(provider_type):
                logger.info(f"⏭️ Skipping {provider['name']} — provider type '{provider_type}' is blacklisted")
                self.rotate()
                attempts += 1
                continue

            # Skip individual failed key
            if provider["name"] in self.failed_providers:
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
        """
        self.failed_providers[provider_name] = datetime.datetime.utcnow()
        logger.warning(f"⚠️ Provider key '{provider_name}' failed: {error_message}")

        # Find the provider type for this key and record the type-level failure
        for p in self.providers:
            if p["name"] == provider_name:
                provider_type = p.get("_provider_id", provider_name)
                self._record_provider_type_failure(provider_type)
                break

    def rotate(self):
        """Move to next provider"""
        self.current_provider_index = (self.current_provider_index + 1) % len(self.providers)

    def get_stats(self):
        """Get provider statistics"""
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
                "key_failed": p["name"] in self.failed_providers,
                "type_blacklisted": self._is_provider_type_blacklisted(provider_type),
                "type_failure_count": len(self._provider_type_failures.get(provider_type, [])),
            })

        blacklisted_info = {}
        for pt, ts in self._blacklisted_types.items():
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
                if p["name"] not in self.failed_providers
                and not self._is_provider_type_blacklisted(p.get("_provider_id", p["name"]))
            ),
            "failed_keys": list(self.failed_providers.keys()),
            "blacklisted_provider_types": blacklisted_info,
            "current_provider": self.providers[self.current_provider_index]["name"] if self.providers else None,
            "providers": provider_details,
        }


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
        """
        MAX_MODEL_RETRIES = max(len(CEREBRAS_MODELS), len(GROQ_MODELS), len(MISTRAL_MODELS), len(OPENROUTER_MODELS))
        self._provider_exhausted = False

        for model_attempt in range(MAX_MODEL_RETRIES):
            yield from self._do_stream(messages)
            # _do_stream sets self._model_error_occurred if a model error occurred
            if not getattr(self, "_model_error_occurred", False):
                return  # clean exit

            # Try next fallback model
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
        """
        self._model_error_occurred = False

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

        except requests.exceptions.Timeout:
            logger.error("⏱️ Request timeout")
            yield f"data: {json.dumps({'error': 'Request timed out', 'timeout': True})}\n\n"
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            body = e.response.text[:500] if e.response is not None else ""
            logger.error(f"❌ HTTP error {status} from {self.provider['name']}: {body}")
            if status == 404:
                self._model_error_occurred = True
                return
            yield f"data: {json.dumps({'error': f'HTTP {status}', 'http_error': True})}\n\n"
        except Exception as e:
            logger.error(f"❌ Stream error: {str(e)}", exc_info=True)
            yield f"data: {json.dumps({'error': str(e)})}\n\n"


def _call_provider_sync(
    messages: list,
    provider: dict,
    type: str = "",
    max_tokens: int | None = None,
) -> str | None:
    """
    Non-streaming provider call for use in background threads.

    Used by the Thread WebSocket system (meeting notes, action AI calls)
    and now also as the internal engine for call_ai_response() below.
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
        "model":      provider["text_model"],
        "messages":   messages,
        "stream":     False,
        "max_tokens": max_tokens
    }

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
            return None

        return clean_ai_response(raw)

    except requests.exceptions.Timeout:
        logger.error(f"AI sync call ({type or 'default'}): timeout")
        provider_manager.mark_provider_failed(provider["name"], "timeout")
        return None

    except requests.exceptions.HTTPError as e:
        logger.error(f"AI sync call ({type or 'default'}) HTTP error: {e}")
        provider_manager.mark_provider_failed(provider["name"], str(e))
        return None

    except Exception as e:
        logger.error(f"AI sync call ({type or 'default'}) error: {e}", exc_info=True)
        provider_manager.mark_provider_failed(provider["name"], str(e))
        return None


# ===========================================================
# call_ai_response — Document 1 §2.4 consolidation
#
# Before this migration, at least four call sites (posts.py::
# ask_learnora_about_post, connections.py::get_connection_overview's
# non-streaming fallback, study_sessions.py::ai_ask_in_session, and
# threads.py's meeting-notes/action AI calls) each hand-rolled their own
# "call provider, on failure rotate, retry N times" loop — with subtly
# different retry counts/timeouts per call site. This function is the
# single implementation all of them should call instead.
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

    provider = provider_manager.get_working_provider(needs_vision=needs_vision)

    for attempt in range(max_retries + 1):
        if not provider:
            diagnostics["errors"].append("no working provider available")
            break

        diagnostics["attempts"] += 1
        diagnostics["provider"] = provider["name"]

        result = _call_provider_sync(messages, provider, type=call_type, max_tokens=max_tokens)

        if result is not None:
            return result, diagnostics

        # _call_provider_sync already called provider_manager.mark_provider_failed
        # internally on any failure path, so just rotate and try the next one.
        diagnostics["errors"].append(f"provider {provider['name']} failed")
        provider_manager.rotate()
        provider = provider_manager.get_working_provider(needs_vision=needs_vision)

    return None, diagnostics
