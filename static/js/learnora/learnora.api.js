/**
 * ============================================================================
 * LEARNORA API LAYER
 * All HTTP interactions for the Learnora AI chat feature.
 *
 * Non-streaming calls delegate to the global `api` object (same pattern as
 * notification.api.js). Streaming chat uses raw fetch() because SSE over POST
 * cannot use the browser's EventSource API.
 * ============================================================================
 */

// ---------------------------------------------------------------------------
// Auth helper — mirrors whatever the global api.js does internally
// ---------------------------------------------------------------------------

function _getFetchOptions(extraHeaders = {}) {
  const headers = { ...extraHeaders };

  // Strategy 1: inspect the global api object for a stored token
  if (window.api) {
    const t = window.api._token ?? window.api.token ?? window.api._authToken ?? null;
    if (t) headers['Authorization'] = `Bearer ${t}`;
  }

  // Strategy 2: check common cookie names used by Flask-JWT / Flask-Login
  if (!headers['Authorization']) {
    const match = document.cookie.match(
      /(?:^|;\s*)(?:access_token|token|jwt|auth_token)=([^;]+)/
    );
    if (match) headers['Authorization'] = `Bearer ${match[1]}`;
  }

  return {
    credentials: 'include', // always include cookies as session fallback
    headers,
  };
}

// ---------------------------------------------------------------------------
// API object
// ---------------------------------------------------------------------------

export const learnoraAPI = {
  /** Create a new blank conversation */
  createConversation() {
    return api.post('/learnora/api/conversation/new');
  },

  /** Fetch all active conversations for the current user */
  getConversations() {
    return api.get('/learnora/api/conversation/list');
  },

  /**
   * Fetch a conversation with its paginated messages.
   * page=1 → most recent N messages.
   */
  getConversation(id, page = 1, perPage = 50) {
    return api.get(
      `/learnora/api/conversations/${id}?page=${page}&per_page=${perPage}`
    );
  },

  /** Soft-delete (archive) a conversation */
  deleteConversation(id) {
    return api.delete(`/learnora/api/conversation/${id}`);
  },

  /** Clear all messages from a conversation */
  clearConversation(id) {
    return api.post(`/learnora/api/conversation/${id}/clear`);
  },

  /** Manually override the conversation title */
  updateTitle(id, title) {
    // Use api.put if available; fall back to raw fetch (PUT is not in all api.js builds)
    if (typeof api.put === 'function') {
      return api.put(`/learnora/api/conversation/${id}/title`, { title });
    }
    return fetch(`/student/learnora/api/conversation/${id}/title`, {
      method: 'PUT',
      ..._getFetchOptions({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ title }),
    }).then((r) => r.json());
  },

  /** Re-generate the AI title from the first user message */
  resetTitle(id) {
    return api.post('/learnora/api/chat/reset-title', { conversation_id: id });
  },

  /** Fetch provider stats and daily quota for the current user */
  getStats() {
    return api.get('/learnora/api/stats');
  },

  /**
   * Send a chat message — returns a raw fetch Response for SSE streaming.
   * The caller is responsible for reading the ReadableStream.
   *
   * @param {FormData} formData  Must include conversation_id, message, mode, optional files
   * @returns {Promise<Response>}
   */
  /**
 * Stream chat response with SSE (Server-Sent Events)
 * 
 * ✅ Uses fetch directly (not api.post)
 * ✅ credentials: 'include' sends cookies automatically
 * ✅ No manual token handling (httponly cookie is sent by browser)
 * ✅ No Content-Type header (browser sets multipart boundary)
 * ✅ CSRF token is NOT needed for this endpoint (exempted)
 */
async streamChat(formData) {
  try {
    const response = await fetch('/student/learnora/api/chat', {
      method: 'POST',
      credentials: 'include',  // ✅ Sends httponly cookies automatically
      body: formData,          // ✅ Browser sets Content-Type with boundary
    });

    if (!response.ok) {
      let errMsg = `HTTP ${response.status}`;
      try {
        const body = await response.json();
        errMsg = body.error || body.message || errMsg;
      } catch (_) {
        // Ignore parse error
      }
      throw new Error(errMsg);
    }

    return response;
  } catch (error) {
    console.error('[streamChat] Error:', error);
    throw error;
  }
}
}
