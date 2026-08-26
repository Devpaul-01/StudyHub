"""
Learnora AI Chat System - PRODUCTION VERSION
Features: Account rotation, multiple providers, full frontend support

--------------------------------------------------------------------------
Document 1 §2.4 refactor note:
MultiProviderManager, StudyAssistant, _call_provider_sync, clean_ai_response,
generate_conversation_title, and the model priority lists live in
services/ai_provider_service.py — that module is the canonical home for
all of this, since posts.py, connections.py, study_sessions.py, and
threads.py all depend on it too (not just this file).

They are re-imported below and re-exported at the same names, so any
existing `from learnora import provider_manager` (or similar) elsewhere in
the codebase keeps working unchanged during the migration. Once every
caller is updated to `from services.ai_provider_service import ...`
directly, these re-exports can be deleted — see Document 5's rollout notes
on this pattern (same approach used for connection_service's blocking
functions).

Document 1 §2.4 (Phase 2 file split): FileHandler moved out to
routes/student/learnora/file_handler.py (genuinely learnora-specific,
upload-only). This file now owns: upload_user_files, and the learnora_bp
Flask routes — the routes-only file for this blueprint, matching the
target tree's `learnora/__init__.py  # routes only` entry.
--------------------------------------------------------------------------

PROVIDERS (in fallback order — highest free TPM first):
  1. Cerebras   (~60K TPM free)  — gpt-oss-120b (production) + dynamic discovery
  2. Groq       (~30K TPM free)  — llama-4-scout (vision!) + llama-3.3-70b + dynamic discovery
  3. Mistral    (500K TPM free*) — mistral-small-latest / mistral-medium-latest (provider aliases)
  4. OpenRouter (pay-per-use)    — meta-llama/llama-4-scout (vision!) + static model list

See services/ai_provider_service.py for the model-selection strategy and
env var docs — unchanged from before, just relocated.
"""

import os
import io
import json
import base64
import mimetypes
import threading
import datetime
import logging

from werkzeug.utils import secure_filename
from werkzeug.datastructures import FileStorage

from flask import (
    request, render_template, jsonify, Response,
    stream_with_context, current_app, Blueprint,
)

from extensions import db
from models import AIConversation, AIUsageQuota, Post, User
from errors import ValidationError
from routes.student.helpers import (
    token_required, success_response, error_response
)

# Document 1 §2.4: FileHandler moved to its own file within this package.
from routes.student.learnora.file_handler import FileHandler

# Learnora Chat Audit — Issue 10: same content-validation functions
# upload_post_attachment() (below in this file) already uses, reused
# here for chat attachments. Imported at module level for the chat()
# route's use; upload_post_attachment() keeps its own existing local
# import unchanged.
from services.upload_validation_service import (
    validate_and_normalize_image, validate_document_mime,
)

# ── Moved to services/ai_provider_service.py (Document 1 §2.4) ────────────
# Re-exported here at the same names for backward compatibility with any
# existing `from learnora import ...` callers elsewhere in the codebase.
from services.ai_provider_service import (
    provider_manager,
    MultiProviderManager,
    StudyAssistant,
    call_ai_response,
    _call_provider_sync,
    clean_ai_response,
    generate_conversation_title,
    CEREBRAS_MODELS,
    GROQ_MODELS,
    MISTRAL_MODELS,
    OPENROUTER_MODELS,
    GROQ_VISION_MODELS,
    OPENROUTER_VISION_MODELS,
    PROVIDER_ORDER,
)
# Phase 5b (Document 4 §1): AI_EXPENSIVE on the routes that call an AI
# provider (chat, new-conversation seeding/title-gen, reset-title) — these
# cost real money per call, per the ticket's explicit table entry for this
# file. Lighter, non-AI routes (list/get/delete/clear/title-PUT/upload/
# stats) get WRITE_HEAVY or BURST_OK instead.
from services.rate_limit_service import limiter, RateLimitTier, user_or_ip_key

learnora_bp = Blueprint('learnora', __name__, url_prefix='/learnora')


# Setup logging
# NOTE: without a basicConfig call, the root logger defaults to WARNING level,
# which silently drops every logger.info(...) call below (you'd only ever see
# .warning()/.error() lines in the terminal). If your app's entrypoint (app.py)
# already calls logging.basicConfig(...) elsewhere, this is redundant but
# harmless (basicConfig is a no-op if handlers already exist).
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

# Learnora Chat Audit — Issue 7: single source of truth for the default
# daily AI-message quota, so the "no quota row yet" fallback in
# get_stats() can't drift from the value actually used when a quota row
# is first created in chat() (previously two different hardcoded
# numbers — 10 and 50 — for what should be the same thing).
DEFAULT_DAILY_MESSAGE_LIMIT = 50

# Learnora Chat Audit — Issue 8: matches the frontend's own cap
# (learnora.events.js's addPendingFiles() rejects a 6th file
# client-side); enforced here too so a direct API call can't attach an
# unbounded number of files.
MAX_CHAT_ATTACHMENTS = 5


def upload_user_files(files, user_id):
    """
    Upload user files to Cloudinary and return metadata.
    Uses cloudinary_storage.upload_file directly to avoid the buggy
    upload_ai_file wrapper in storage.py.
    """
    from services.storage import cloudinary_storage, FilenameService

    uploaded_files = []

    logger.info(f"📤 Uploading {len(files)} files for user {user_id}")

    for file_key in files:
        file = files[file_key]

        if not file or not file.filename:
            continue

        safe_filename = secure_filename(file.filename)
        fname_lower = safe_filename.lower()

        # Determine category and Cloudinary resource type
        if fname_lower.endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif')):
            file_type = 'image'
            resource_type = 'image'
        elif fname_lower.endswith(('.py', '.js', '.java', '.ts', '.cpp', '.txt', '.html', '.css')):
            file_type = 'code'
            resource_type = 'raw'
        elif fname_lower.endswith(('.pdf', '.doc', '.docx', '.csv')):
            file_type = 'document'
            resource_type = 'raw'
        else:
            file_type = 'unknown'
            resource_type = 'auto'

        # Generate a unique filename via FilenameService
        _, _, generated_filename = FilenameService.get_ai_temp_path(user_id, safe_filename)
        folder_path = f"ai-uploads/user_{user_id}"

        # Read size before upload
        file.seek(0, 2)
        file_size = file.tell()
        file.seek(0)

        # Upload to Cloudinary
        result = cloudinary_storage.upload_file(file, folder_path, generated_filename, resource_type)

        if result["success"]:
            mime_type = mimetypes.guess_type(safe_filename)[0] or 'application/octet-stream'
            uploaded_files.append({
                "filename": file.filename,
                "url": result["url"],
                "public_id": result.get("public_id"),
                "size": file_size,
                "mime_type": mime_type,
                "type": file_type
            })
            logger.info(f"✅ Uploaded {file.filename} to Cloudinary")
        else:
            logger.error(f"❌ Failed to upload {file.filename}: {result['error']}")

    return uploaded_files


# ===========================================================
# FLASK ROUTES
# ===========================================================


# -----------------------------------------------------------
# MAIN CHAT
# -----------------------------------------------------------

@learnora_bp.route("/", methods=["GET"])
@token_required
def learnora_page(current_user):
    return render_template('learnora/learnora.html')


@learnora_bp.route("/api/chat", methods=["POST"])
@limiter.limit(RateLimitTier.AI_EXPENSIVE, key_func=user_or_ip_key)
@token_required
def chat(current_user):
    """
    Main chat endpoint with multi-provider streaming support.

    Form fields:
        conversation_id (int, required): Target conversation
        message (str, required): User's message
        mode (str, optional): Response mode — fast_response | deep_think | programming | research | summarize | explain
        post_id (int, optional): Related post ID for context
        is_continue (str, optional): "true" if continuing an incomplete response
        files (multipart, optional): Attachments (images, docs, code)
    """
    try:
        # ── Resolve conversation ─────────────────────────────
        conversation_id = request.form.get("conversation_id")

        if not conversation_id:
            return jsonify({"status": "error", "message": "conversation_id is required"}), 400

        # Learnora Chat Audit — Issue 11: validate as an int before it
        # reaches a query, matching routes that take conversation_id from
        # the URL (Flask's <int:...> converter validates those
        # automatically). A non-numeric value here previously fell
        # through to the DB driver and surfaced as an unhandled 500.
        try:
            conversation_id = int(conversation_id)
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "conversation_id must be a valid integer"}), 400

        conversation = AIConversation.query.filter_by(
            id=conversation_id,
            user_id=current_user.id
        ).first()

        if not conversation:
            return jsonify({"status": "error", "message": "Conversation not found"}), 404

        if conversation.is_archived:
            return jsonify({"status": "error", "message": "This conversation has been archived"}), 410

        # ── Hard message cap ─────────────────────────────────
        if conversation.total_messages >= 500:
            return jsonify({"status": "error", "message": "Message limit reached (500 messages per conversation)"}), 429

        # ── Daily quota ──────────────────────────────────────
        quota = AIUsageQuota.query.filter_by(user_id=current_user.id).first()
        if not quota:
            quota = AIUsageQuota(user_id=current_user.id, daily_messages_limit=DEFAULT_DAILY_MESSAGE_LIMIT)
            db.session.add(quota)
            db.session.commit()

        today = datetime.date.today()
        if quota.last_reset_date != today:
            quota.daily_messages_used = 0
            quota.last_reset_date = today
            db.session.commit()

        if quota.daily_messages_used >= quota.daily_messages_limit:
            return jsonify({
                "status": "error",
                "message": f"Daily limit reached ({quota.daily_messages_limit} messages). Try again tomorrow."
            }), 429

        # ── Parse request fields ─────────────────────────────
        # Learnora Chat Audit — Issue 12: same 2000-char cap already used
        # for initial_message in create_conversation() — this field
        # previously had no length limit at all.
        user_message = request.form.get("message", "").strip()[:2000]
        mode = request.form.get("mode", "fast_response")
        post_id = request.form.get("post_id")
        is_continue = request.form.get("is_continue", "false").lower() == "true"

        # Learnora Chat Audit — Issue 13: last_incomplete_message was
        # previously written on every truncated response but never read
        # back anywhere — "Continue" just sent a bare "continue" user
        # turn. Captured here (off the conversation object already
        # fetched above, no extra query) and passed into build_messages()
        # below so the model gets an explicit continuation instruction
        # instead of having to infer intent from conversation history
        # alone. Only used when there's actually stored text to continue
        # from, so a stray is_continue=true with nothing to continue is
        # a harmless no-op.
        continuation_text = conversation.last_incomplete_message if is_continue else None

        # If no message text was sent, this may be a request to generate the
        # AI's first reply to a message that was already seeded when the
        # conversation was created (POST /api/conversation/new with
        # initial_message). In that case, reuse the last stored user turn
        # instead of requiring the student to retype it.
        seeded_reuse = False
        if not user_message:
            existing_messages = conversation.messages or []
            if existing_messages and existing_messages[-1].get("role") == "user":
                last_content = existing_messages[-1].get("content", "")
                if isinstance(last_content, list):
                    last_content = " ".join(
                        p.get("text", "") for p in last_content if isinstance(p, dict)
                    )
                user_message = (last_content or "").strip()
                seeded_reuse = bool(user_message)

        if not user_message:
            return jsonify({"status": "error", "message": "Message cannot be empty"}), 400

        logger.info(
            f"💬 Chat request: user={current_user.id}, conv={conversation_id}, "
            f"mode={mode}, is_continue={is_continue}"
        )

        # ── Start Cloudinary upload in background ────────────
        files = request.files

        # Learnora Chat Audit — Issue 8: the frontend caps attachments at
        # 5 (learnora.events.js's addPendingFiles), but nothing
        # server-side enforced that — a direct API call could attach an
        # unbounded number of files.
        if len(files) > MAX_CHAT_ATTACHMENTS:
            return jsonify({
                "status": "error",
                "message": f"Too many attachments ({len(files)}). Maximum is {MAX_CHAT_ATTACHMENTS} files per message."
            }), 400

        # Learnora Chat Audit — Issue 3: previously the background
        # Cloudinary-upload thread and the main-thread FileHandler both
        # read the SAME request.files FileStorage objects concurrently —
        # each doing its own seek()/read() with no locking, a real race
        # condition that could corrupt either consumer's read. Each
        # uploaded file's bytes are now read into memory exactly once,
        # up front on the main thread (request.files streams aren't
        # guaranteed usable outside this request context anyway once we
        # branch into a background thread), and each consumer below gets
        # its own independent FileStorage snapshot wrapping a fresh
        # BytesIO copy of those bytes — no shared mutable stream, no
        # cross-thread contention.
        #
        # Learnora Chat Audit — Issue 10: chat attachments were
        # previously trusted by filename extension alone, unlike
        # upload_post_attachment() (below in this same file), which
        # validates actual file content. Content — not just the claimed
        # extension — now gets the same treatment here: images are
        # re-encoded from decoded pixel data (rejects an
        # extension-spoofed non-image by construction, e.g. an SVG/JS
        # polyglot saved as .jpg), and documents are checked against
        # their real magic-number signature. Code files (.py/.js/...)
        # have no reliable magic number to check — same as text/plain in
        # upload_validation_service.py's own signature table — so they
        # remain extension-based, matching that module's own documented
        # limitation rather than inventing a check that doesn't exist.
        _CHAT_DOC_MIME_MAP = {
            "pdf":  "application/pdf",
            "doc":  "application/msword",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "txt":  "text/plain",
            "csv":  "text/csv",
        }

        upload_snapshot = {}
        handler_snapshot = {}
        handler = FileHandler()
        for file_key in files:
            f = files[file_key]
            f.seek(0)
            fname_lower = (f.filename or "").lower()
            ftype = handler.detect_type(fname_lower)

            if ftype == "image":
                try:
                    normalized = validate_and_normalize_image(f)
                except ValidationError as e:
                    return jsonify({"status": "error", "message": f"{f.filename}: {e}"}), 400
                raw_bytes = normalized.getvalue()
            elif ftype == "document":
                expected_mime = _CHAT_DOC_MIME_MAP.get(fname_lower.rsplit(".", 1)[-1])
                f.seek(0)
                raw_bytes = f.read()
                if expected_mime:
                    f.seek(0)
                    try:
                        validate_document_mime(f, {expected_mime})
                    except ValidationError as e:
                        return jsonify({"status": "error", "message": f"{f.filename}: {e}"}), 400
            else:
                f.seek(0)
                raw_bytes = f.read()

            upload_snapshot[file_key] = FileStorage(
                stream=io.BytesIO(raw_bytes),
                filename=f.filename,
                content_type=f.content_type,
            )
            handler_snapshot[file_key] = FileStorage(
                stream=io.BytesIO(raw_bytes),
                filename=f.filename,
                content_type=f.content_type,
            )

        _upload_result = {}

        def _do_cloudinary_upload():
            _upload_result["data"] = upload_user_files(upload_snapshot, current_user.id)

        upload_thread = threading.Thread(target=_do_cloudinary_upload, daemon=True)
        upload_thread.start()

        # ── Process files for AI context (local, no network) ─
        file_result = handler.process_files(handler_snapshot)
        logger.info(f"📊 File processing: {file_result['info']}")

        # ── Optional post context ────────────────────────────
        post_content = None
        if post_id:
            post = Post.query.get(post_id)
            if post:
                # Learnora Chat Audit — Issue 12: cap referenced post
                # content the same way file-attachment previews already
                # are (5000 chars) — this was previously included in
                # full with no limit, unlike every other piece of
                # content folded into the prompt.
                post_body = post.text_content or ""
                post_content = {
                    "title": post.title,
                    "content": post_body[:5000]
                }

        # ── Pick a working provider ──────────────────────────
        provider = provider_manager.get_working_provider(needs_vision=file_result["has_images"])

        if not provider:
            return jsonify({
                "status": "error",
                "message": "All AI providers are currently unavailable. Please try again later.",
                "stats": provider_manager.get_stats()
            }), 503

        # ── Build assistant + messages ────────────────────────
        # When reusing a seeded message, exclude it from history — it's the
        # question being asked right now, not prior context, so it should
        # only appear once in the built message list (not duplicated).
        if seeded_reuse:
            conversation_messages_copy = list(conversation.messages[:-1]) if conversation.messages else []
        else:
            conversation_messages_copy = list(conversation.messages) if conversation.messages else []

        assistant = StudyAssistant(provider, conversation_messages_copy)
        assistant.select_model(file_result["has_images"])

        messages = assistant.build_messages(
            user_message,
            file_result["texts"],
            mode,
            post_content,
            continuation_text
        )

        # ── Join upload thread, then persist user message ─────
        upload_thread.join(timeout=30)
        uploaded_file_metadata = _upload_result.get("data", [])

        db.session.refresh(conversation)

        if seeded_reuse:
            # Already stored at creation time — just bump timing, don't
            # duplicate it in conversation.messages.
            conversation.last_message_at = datetime.datetime.utcnow()
        else:
            user_msg = {
                "role": "user",
                "content": user_message,
                "timestamp": datetime.datetime.utcnow().isoformat(),
                "attachments": uploaded_file_metadata,
                "is_continue": is_continue
            }
            conversation.messages.append(user_msg)
            conversation.total_messages += 1
            conversation.last_message_at = datetime.datetime.utcnow()

        is_first_message = (conversation.total_messages == 1)

        quota.daily_messages_used += 1
        quota.last_message_time = datetime.datetime.utcnow()

        db.session.commit()
        db.session.expunge(conversation)
        db.session.expunge(quota)

        # ── Generate title in background (first message only) ─
        # Skip if this was a seeded message — create_conversation() already
        # kicked off title generation for it, no need to do it twice.
        if is_first_message and not seeded_reuse:
            _app       = current_app._get_current_object()
            _conv_id   = int(conversation_id)
            _first_msg = user_message
            _provider  = provider

            def _do_title():
                try:
                    title = generate_conversation_title(_first_msg, _provider)
                    with _app.app_context():
                        conv = AIConversation.query.get(_conv_id)
                        if conv:
                            conv.title = title
                            db.session.commit()
                            logger.info(f"✅ Background title saved for conv {_conv_id}: '{title}'")
                except Exception as _e:
                    logger.warning(f"⚠️ Background title generation failed: {_e}")

            threading.Thread(target=_do_title, daemon=True).start()

        # ── Streaming response with provider rotation ─────────
        def generate():
            nonlocal provider
            full_response = ""
            response_complete = True
            error_occurred = False
            error_message = None
            retries = 0
            max_retries = 3

            yield f"data: {json.dumps({'type': 'start', 'model': assistant.model, 'provider': provider['name']})}\n\n"

            while retries < max_retries:
                error_in_stream = False

                for chunk in assistant.stream_response(messages, has_images=file_result["has_images"]):
                    yield chunk

                    if chunk.startswith("data: "):
                        try:
                            data = json.loads(chunk[6:])

                            if 'content' in data:
                                full_response += data['content']
                            elif 'incomplete' in data:
                                response_complete = False
                            elif 'complete' in data:
                                response_complete = data['complete']
                            elif data.get('type') == 'model_retry':
                                # Model fallback already handled inside stream_response
                                pass
                            elif 'error' in data:
                                error_message = data['error']
                                error_occurred = True

                                if data.get('rate_limit') or data.get('timeout') or data.get('http_error'):
                                    error_in_stream = True
                                    provider_manager.mark_provider_failed(provider['name'], error_message)
                                    provider_manager.rotate()

                                    next_provider = provider_manager.get_working_provider(
                                        needs_vision=file_result["has_images"]
                                    )

                                    if next_provider and retries < max_retries - 1:
                                        logger.info(f"🔄 Switching provider to {next_provider['name']}...")
                                        provider = next_provider
                                        assistant.provider = next_provider
                                        assistant.select_model(file_result["has_images"])

                                        yield f"data: {json.dumps({'type': 'provider_switch', 'new_provider': provider['name']})}\n\n"

                                        retries += 1
                                        error_occurred = False
                                        error_message = None
                                        break
                                    else:
                                        # Learnora Chat Audit — Issue 4: retries
                                        # must be incremented here too — this is
                                        # a failed attempt (no fallback provider
                                        # available, or the retry budget is
                                        # already spent). Previously this branch
                                        # left retries unchanged while also
                                        # setting error_in_stream=True, which
                                        # skips the loop's only other exit check
                                        # below — the while loop would then
                                        # re-enter and keep retrying the same
                                        # already-failing provider with no bound
                                        # and no backoff.
                                        retries += 1
                                        response_complete = False
                                        break
                                else:
                                    response_complete = False
                                    break
                        except Exception:
                            pass

                if not error_in_stream:
                    # Also check if stream_response exhausted all models in the provider
                    # (model-not-found failures that never produced a stream error chunk) —
                    # OR gave up on this provider entirely for a KEY_FAULT / PROVIDER_TRANSIENT /
                    # NON_RETRYABLE header-stage failure. StudyAssistant.stream_response now sets
                    # _provider_exhausted for all of these (previously only a fully-exhausted
                    # model list set it), so this branch covers e.g. a 402 payment-required
                    # error that never even got here as a mid-stream 'error' chunk.
                    if getattr(assistant, '_provider_exhausted', False) and not full_response:
                        exhaustion_reason = error_message or "provider exhausted (see server logs for classified error category)"
                        logger.warning(
                            f"⚠️ Provider {provider['name']} exhausted — forcing provider switch "
                            f"(reason: {exhaustion_reason})"
                        )
                        provider_manager.mark_provider_failed(provider['name'], exhaustion_reason)
                        provider_manager.rotate()

                        next_provider = provider_manager.get_working_provider(
                            needs_vision=file_result["has_images"]
                        )

                        if next_provider and retries < max_retries - 1:
                            logger.info(f"🔄 Switching provider to {next_provider['name']} after model exhaustion...")
                            provider = next_provider
                            assistant.provider = next_provider
                            assistant.select_model(file_result["has_images"])
                            assistant._provider_exhausted = False

                            yield f"data: {json.dumps({'type': 'provider_switch', 'new_provider': provider['name']})}\n\n"

                            retries += 1
                            error_occurred = False
                            error_message = None
                        else:
                            response_complete = False
                            break
                    else:
                        break

            # ── Persist assistant response ────────────────────
            try:
                with db.session.begin_nested():
                    conv = db.session.query(AIConversation).get(conversation_id)

                    cleaned_response = clean_ai_response(full_response) if full_response else ""

                    assistant_msg = {
                        "role": "assistant",
                        "content": cleaned_response if cleaned_response else "[Error: No response]",
                        "model": assistant.model,
                        "provider": provider['name'],
                        "timestamp": datetime.datetime.utcnow().isoformat(),
                        "is_complete": response_complete,
                        "error": error_message
                    }

                    conv.messages.append(assistant_msg)
                    conv.total_messages += 1
                    conv.tokens_used += handler.total_tokens + len(cleaned_response) // 4
                    conv.is_last_message_complete = response_complete

                    if not response_complete:
                        conv.last_incomplete_message = cleaned_response
                    else:
                        conv.last_incomplete_message = None

                    if error_occurred:
                        conv.error_count += 1

                    db.session.commit()

            except Exception as e:
                logger.error(f"❌ Error saving assistant response: {str(e)}", exc_info=True)

            done_payload = {
                'type': 'done',
                'tokens': handler.total_tokens,
                'complete': response_complete,
                'can_continue': not response_complete and not error_occurred,
                'provider': provider['name']
            }
            yield f"data: {json.dumps(done_payload)}\n\n"

        return Response(
            stream_with_context(generate()),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no'
            }
        )

    except Exception as e:
        logger.error(f"❌ Chat error: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


# -----------------------------------------------------------
# CONVERSATION MANAGEMENT
# -----------------------------------------------------------

@learnora_bp.route("/api/conversation/new", methods=["POST"])
@limiter.limit(RateLimitTier.AI_EXPENSIVE, key_func=user_or_ip_key)
@token_required
def create_conversation(current_user):
    """
    Create a new AI conversation session.

    JSON body:
        initial_message (str, optional): The topic/question the student typed
            before the conversation existed. It is stored immediately as the
            conversation's first user message (so the AI has it as context
            right away) and used to seed a title (instant truncated fallback,
            upgraded to an AI-generated one in the background).

            No AI reply is generated yet at this point — this endpoint is a
            plain JSON response, not a stream. To actually get the AI's
            response to it, call POST /api/chat with this same
            conversation_id and an EMPTY "message" field; the endpoint will
            detect the unanswered seeded message and respond to it instead
            of requiring the text to be retyped.

    Returns:
        conversation_id to be used in subsequent /api/chat calls.
    """
    try:
        data = request.get_json(silent=True) or {}
        initial_message = (data.get("initial_message") or "").strip()[:2000]

        conversation = AIConversation(
            user_id=current_user.id,
            messages=[],
            total_messages=0,
            tokens_used=0
        )
        db.session.add(conversation)
        db.session.commit()

        if initial_message:
            # Seed it as a real first message, exactly like /api/chat would
            # store it, minus attachments (none exist at creation time).
            seed_msg = {
                "role": "user",
                "content": initial_message,
                "timestamp": datetime.datetime.utcnow().isoformat(),
                "attachments": [],
                "is_continue": False
            }
            conversation.messages.append(seed_msg)
            conversation.total_messages = 1
            conversation.last_message_at = datetime.datetime.utcnow()

            # Instant, synchronous fallback title — so the UI has something
            # sensible immediately instead of "New Conversation".
            clean = ' '.join(initial_message.split())
            conversation.title = clean if len(clean) <= 60 else clean[:57] + '...'
            db.session.commit()

            # Upgrade to an AI-generated title in the background, same
            # pattern used for the first message in /api/chat.
            _app     = current_app._get_current_object()
            _conv_id = conversation.id
            _msg     = initial_message

            def _do_initial_title():
                try:
                    provider = provider_manager.get_working_provider(needs_vision=False)
                    title = generate_conversation_title(_msg, provider)
                    with _app.app_context():
                        conv = AIConversation.query.get(_conv_id)
                        if conv:
                            conv.title = title
                            db.session.commit()
                            logger.info(f"✅ Initial title generated for conv {_conv_id}: '{title}'")
                except Exception as e:
                    logger.warning(f"⚠️ Initial title generation failed: {e}")

            threading.Thread(target=_do_initial_title, daemon=True).start()

        return jsonify({
            "status": "success",
            "data": {
                "conversation_id": conversation.id,
                "title": conversation.title,
                "initial_message": initial_message or None,
                "created_at": conversation.created_at.isoformat()
            }
        }), 201

    except Exception as e:
        logger.error(f"Error creating conversation: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@learnora_bp.route("/api/conversation/list", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
@token_required
def get_conversations(current_user):
    """
    Fetch the user's active (non-archived) conversation list.

    Query params:
        limit (int, optional): Max results, default 50, max 100
    """
    try:
        limit = min(request.args.get("limit", 50, type=int), 100)

        conversations = AIConversation.query.filter_by(
            user_id=current_user.id,
            is_archived=False
        ).order_by(AIConversation.last_message_at.desc()).limit(limit).all()

        conversation_data = [
            {
                "conversation_id": conv.id,
                "title": conv.title,
                "total_messages": conv.total_messages,
                "tokens_used": conv.tokens_used,
                "is_last_message_complete": conv.is_last_message_complete,
                "last_message_at": conv.last_message_at.isoformat() if conv.last_message_at else None,
                "created_at": conv.created_at.isoformat() if conv.created_at else None
            }
            for conv in conversations
        ]

        return jsonify({
            "status": "success",
            "data": conversation_data,
            "count": len(conversation_data)
        })

    except Exception as e:
        logger.error(f"Error loading conversations: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@learnora_bp.route("/api/conversations/<int:conversation_id>", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
@token_required
def get_conversation(current_user, conversation_id):
    """
    Fetch a conversation with paginated messages.

    Query params:
        page     (int, optional): Page number, default 1. Paginates from most recent.
        per_page (int, optional): Messages per page, default 20, max 100.
    """
    try:
        conversation = AIConversation.query.filter_by(
            id=conversation_id,
            user_id=current_user.id
        ).first()

        if not conversation:
            return jsonify({"status": "error", "message": "Conversation not found"}), 404

        page = request.args.get("page", 1, type=int)
        per_page = min(request.args.get("per_page", 20, type=int), 100)

        all_messages = conversation.messages or []
        total_count = len(all_messages)

        end_idx = max(0, total_count - (page - 1) * per_page)
        start_idx = max(0, end_idx - per_page)
        paginated_messages = all_messages[start_idx:end_idx]

        return jsonify({
            "status": "success",
            "data": {
                "id": conversation.id,
                "title": conversation.title,
                "messages": paginated_messages,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total_messages": total_count,
                    "has_more": start_idx > 0
                },
                "total_messages": conversation.total_messages,
                "tokens_used": conversation.tokens_used,
                "is_last_message_complete": conversation.is_last_message_complete,
                "last_message_at": conversation.last_message_at.isoformat() if conversation.last_message_at else None,
                "created_at": conversation.created_at.isoformat()
            }
        })

    except Exception as e:
        logger.error(f"Error loading conversation: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@learnora_bp.route("/api/conversation/<int:conversation_id>", methods=["DELETE"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def delete_conversation(current_user, conversation_id):
    """
    Archive (soft-delete) a conversation.
    Archived conversations no longer appear in the list and cannot receive new messages.
    """
    try:
        conversation = AIConversation.query.filter_by(
            id=conversation_id,
            user_id=current_user.id
        ).first()

        if not conversation:
            return jsonify({"status": "error", "message": "Conversation not found"}), 404

        if conversation.is_archived:
            return jsonify({"status": "error", "message": "Conversation is already archived"}), 409

        conversation.is_archived = True
        db.session.commit()

        logger.info(f"🗂️ Conversation {conversation_id} archived by user {current_user.id}")

        return jsonify({
            "status": "success",
            "message": "Conversation archived successfully"
        })

    except Exception as e:
        logger.error(f"❌ Error archiving conversation {conversation_id}: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@learnora_bp.route("/api/conversation/<int:conversation_id>/clear", methods=["POST"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def clear_conversation(current_user, conversation_id):
    """
    Clear all messages from a conversation while keeping the conversation record.
    """
    try:
        conversation = AIConversation.query.filter_by(
            id=conversation_id,
            user_id=current_user.id
        ).first()

        if not conversation:
            return jsonify({"status": "error", "message": "Conversation not found"}), 404

        conversation.messages = []
        conversation.total_messages = 0
        conversation.tokens_used = 0
        conversation.title = "New Conversation"
        conversation.last_message_at = None
        conversation.last_incomplete_message = None
        conversation.is_last_message_complete = True
        conversation.error_count = 0
        db.session.commit()

        logger.info(f"🧹 Conversation {conversation_id} cleared by user {current_user.id}")

        return jsonify({
            "status": "success",
            "message": "Conversation history cleared"
        })

    except Exception as e:
        logger.error(f"❌ Error clearing conversation {conversation_id}: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


# -----------------------------------------------------------
# TITLE MANAGEMENT
# -----------------------------------------------------------

@learnora_bp.route("/api/conversation/<int:conversation_id>/title", methods=["PUT"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def update_conversation_title(current_user, conversation_id):
    """
    Manually override the conversation title.

    JSON body:
        title (str, required): New title, max 200 characters
    """
    try:
        data = request.get_json() or {}
        new_title = data.get("title", "").strip()

        if not new_title:
            return jsonify({"status": "error", "message": "title field is required"}), 400

        if len(new_title) > 200:
            return jsonify({"status": "error", "message": "Title too long (max 200 characters)"}), 400

        conversation = AIConversation.query.filter_by(
            id=conversation_id,
            user_id=current_user.id
        ).first()

        if not conversation:
            return jsonify({"status": "error", "message": "Conversation not found"}), 404

        conversation.title = new_title
        db.session.commit()

        return jsonify({
            "status": "success",
            "data": {
                "conversation_id": conversation_id,
                "title": new_title
            }
        })

    except Exception as e:
        logger.error(f"❌ Error updating title for conversation {conversation_id}: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@learnora_bp.route("/api/chat/reset-title", methods=["POST"])
@limiter.limit(RateLimitTier.AI_EXPENSIVE, key_func=user_or_ip_key)
@token_required
def reset_conversation_title(current_user):
    """
    Regenerate the AI-generated title for a conversation.
    Finds the first user message and re-runs the title generation prompt.

    JSON body:
        conversation_id (int, required): Target conversation
    """
    try:
        data = request.get_json(silent=True) or {}
        conversation_id = data.get("conversation_id")

        if not conversation_id:
            return jsonify({"status": "error", "message": "conversation_id is required"}), 400

        # Learnora Chat Audit — Issue 11: same fix as chat() — validate
        # as an int before it reaches a query instead of letting a
        # malformed value fall through to an unhandled DB-level error.
        try:
            conversation_id = int(conversation_id)
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "conversation_id must be a valid integer"}), 400

        conversation = AIConversation.query.filter_by(
            id=conversation_id,
            user_id=current_user.id
        ).first()

        if not conversation:
            return jsonify({"status": "error", "message": "Conversation not found"}), 404

        if not conversation.messages:
            return jsonify({"status": "error", "message": "Conversation has no messages to generate a title from"}), 400

        first_user_message = next(
            (msg.get("content", "") for msg in conversation.messages if msg.get("role") == "user"),
            None
        )

        if not first_user_message:
            return jsonify({"status": "error", "message": "No user messages found in conversation"}), 400

        provider = provider_manager.get_working_provider(needs_vision=False)

        new_title = generate_conversation_title(first_user_message, provider)

        conversation.title = new_title
        db.session.commit()

        logger.info(f"🔄 Title reset for conversation {conversation_id}: '{new_title}'")

        return jsonify({
            "status": "success",
            "data": {
                "conversation_id": conversation_id,
                "title": new_title
            }
        })

    except Exception as e:
        logger.error(f"❌ Error resetting title: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


# -----------------------------------------------------------
# FILE UPLOADS
# -----------------------------------------------------------

@learnora_bp.route("/api/upload/attachment", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def upload_post_attachment(current_user):
    """
    Upload a post attachment (image or document) to Cloudinary.
    Intended for use before creating a post — returns the URL to embed.

    Form fields:
        file (multipart, required): The file to upload

    Allowed types:
        Images   — jpg, jpeg, png, webp, gif  (max 10MB)
        Documents — pdf, doc, docx, txt, csv  (max 10MB)

    Returns:
        url, public_id, filename, size, mime_type, file_category
    """
    try:
        from services.storage import cloudinary_storage, FilenameService

        if 'file' not in request.files:
            return jsonify({"status": "error", "message": "No file provided"}), 400

        file = request.files['file']

        if not file or not file.filename:
            return jsonify({"status": "error", "message": "File has no name"}), 400

        safe_filename = secure_filename(file.filename)
        fname_lower = safe_filename.lower()

        ALLOWED = {
            'image':    ('.jpg', '.jpeg', '.png', '.webp', '.gif'),
            'document': ('.pdf', '.doc', '.docx', '.txt', '.csv'),
        }

        file_category = None
        resource_type = None
        for category, exts in ALLOWED.items():
            if any(fname_lower.endswith(ext) for ext in exts):
                file_category = category
                resource_type = 'image' if category == 'image' else 'raw'
                break

        if not file_category:
            allowed_list = ", ".join(ext for exts in ALLOWED.values() for ext in exts)
            return jsonify({
                "status": "error",
                "message": f"Unsupported file type. Allowed: {allowed_list}"
            }), 415

        file.seek(0, 2)
        file_size = file.tell()
        file.seek(0)

        MAX_SIZE = 10 * 1024 * 1024
        if file_size > MAX_SIZE:
            return jsonify({
                "status": "error",
                "message": f"File too large ({file_size // 1024}KB). Maximum is 10MB."
            }), 413

        if file_size == 0:
            return jsonify({"status": "error", "message": "File is empty"}), 400

        # Document 3 §3: the extension check above (ALLOWED dict) is cheap
        # early rejection only. Images get re-encoded from decoded pixel
        # data (structurally rules out SVG/polyglot content mislabeled
        # with an image extension); documents get their real magic-number
        # signature checked against the extension they claim.
        from services.upload_validation_service import (
            validate_and_normalize_image, validate_document_mime,
        )
        upload_target = file
        if file_category == "image":
            try:
                upload_target = validate_and_normalize_image(file)
            except ValidationError as e:
                return jsonify({"status": "error", "message": str(e)}), 400
        elif file_category == "document":
            doc_mime_map = {
                "pdf":  "application/pdf",
                "doc":  "application/msword",
                "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "txt":  "text/plain",
                "csv":  "text/csv",
            }
            expected_mime = doc_mime_map.get(fname_lower.rsplit(".", 1)[-1])
            if expected_mime:
                try:
                    validate_document_mime(file, {expected_mime})
                except ValidationError as e:
                    return jsonify({"status": "error", "message": str(e)}), 400

        folder, generated_filename = FilenameService.get_post_file_path(
            current_user.id,
            safe_filename,
            file_category
        )

        result = cloudinary_storage.upload_file(upload_target, folder, generated_filename, resource_type)

        if not result["success"]:
            logger.error(f"❌ Cloudinary upload failed for user {current_user.id}: {result['error']}")
            return jsonify({
                "status": "error",
                "message": f"Upload failed: {result['error']}"
            }), 502

        mime_type = mimetypes.guess_type(safe_filename)[0] or "application/octet-stream"

        logger.info(
            f"✅ Attachment uploaded by user {current_user.id}: "
            f"{safe_filename} → {result['url']}"
        )

        return jsonify({
            "status": "success",
            "data": {
                "url": result["url"],
                "public_id": result.get("public_id"),
                "filename": file.filename,
                "size": file_size,
                "mime_type": mime_type,
                "file_category": file_category
            }
        }), 201

    except Exception as e:
        logger.error(f"❌ Attachment upload error: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


# -----------------------------------------------------------
# STATS & DIAGNOSTICS
# -----------------------------------------------------------

@learnora_bp.route("/api/stats", methods=["GET"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def get_stats(current_user):
    """
    Fetch provider stats and the current user's daily quota usage.
    """
    try:
        quota = AIUsageQuota.query.filter_by(user_id=current_user.id).first()

        # Learnora Chat Audit — Issue 7: use the same shared constant as
        # chat()'s quota-creation fallback, instead of a second,
        # disagreeing hardcoded number (was 10 here vs. 50 there).
        daily_limit = quota.daily_messages_limit if quota else DEFAULT_DAILY_MESSAGE_LIMIT
        daily_used = quota.daily_messages_used if quota else 0

        # Learnora Chat Audit — Issue 9: provider_manager.get_stats()
        # returns real provider names, per-key cooldown/blacklist
        # status, model lists, and Redis-vs-memory state — meaningful
        # internal operational detail that was previously returned to
        # any authenticated student. Regular users only ever needed
        # their own quota (confirmed: the frontend never reads
        # provider_stats). Admin/system accounts still get the full
        # diagnostic payload for actual operational use.
        response_data = {
            "user_quota": {
                "daily_used": daily_used,
                "daily_limit": daily_limit,
                "remaining": max(0, daily_limit - daily_used),
                "reset_date": quota.last_reset_date.isoformat() if quota and quota.last_reset_date else None
            }
        }

        if current_user.role in ("admin", "system"):
            response_data["provider_stats"] = provider_manager.get_stats()

        return jsonify({
            "status": "success",
            "data": response_data
        })
    except Exception as e:
        logger.error(f"Error getting stats: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

