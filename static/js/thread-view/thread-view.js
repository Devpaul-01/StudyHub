/**
 * ============================================================================
 * THREAD VIEW — thread-view.js
 * Dedicated single-thread detail page. Self-contained — talks directly to
 * the real thread endpoints (GET /threads/<id>, POST /threads/<id>/join,
 * POST /threads/<id>/leave) via the shared `api` client from core/api.js
 * (loaded globally by shell_base.html). This is the actual "apply to
 * thread" flow the search page previously had no way to reach (the old
 * search.preview.js's thread preview was read-only, by design, with no
 * join button at all).
 * ============================================================================
 */

function $(id) { return document.getElementById(id); }

function escapeHtml(text) {
  if (text == null) return '';
  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return String(text).replace(/[&<>"']/g, m => map[m]);
}

function formatTime(timestamp) {
  const date = new Date(timestamp);
  const now = new Date();
  const diff = Math.floor((now - date) / 1000);
  if (diff < 60) return 'Just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return date.toLocaleDateString();
}

function getThreadId() {
  // /student/threads/view/<id>
  const match = window.location.pathname.match(/\/threads\/view\/(\d+)/);
  return match ? match[1] : null;
}

let state = { thread: null, userStatus: null };

async function loadThread() {
  const threadId = getThreadId();
  const content = $('threadv-content');
  if (!threadId) {
    content.innerHTML = `<div class="threadv-error">No thread specified.</div>`;
    return;
  }

  try {
    const res = await api.get(`/threads/${threadId}`);
    const data = res?.data;
    if (!data?.id && !data?.thread_data && !data) {
      content.innerHTML = `<div class="threadv-error">Thread not found.</div>`;
      return;
    }
    // Backend returns thread fields merged with user_status/members at the
    // top level of `data` (see get_thread() in
    // routes/student/threads/discovery.py) rather than nested under a
    // `thread` key — read directly off `data`.
    state.thread = data;
    state.userStatus = data.user_status || {};
    render();
  } catch (error) {
    console.error('[thread-view] Failed to load thread:', error);
    content.innerHTML = `<div class="threadv-error">Something went wrong loading this thread. Please try again.</div>`;
  }
}

function render() {
  const t = state.thread;
  const status = state.userStatus;
  const content = $('threadv-content');

  const tagsHtml = (t.tags || []).map(tag =>
    `<span class="threadv-tag">#${escapeHtml(tag)}</span>`
  ).join('');

  const statusBadges = `
    <span class="threadv-badge ${t.is_open ? 'threadv-badge-open' : 'threadv-badge-closed'}">
      ${t.is_open ? 'Open' : 'Closed'}
    </span>
    ${t.is_full ? `<span class="threadv-badge threadv-badge-full">Full</span>` : ''}
    ${t.department ? `<span class="threadv-badge threadv-badge-dept">${escapeHtml(t.department)}</span>` : ''}
  `;

  let actionsHtml = '';
  if (status.is_creator) {
    actionsHtml = `<span class="threadv-pending-note">You created this thread.</span>`;
  } else if (status.is_member) {
    actionsHtml = `<button class="threadv-btn threadv-btn-danger" id="threadv-leave-btn">Leave thread</button>`;
  } else if (status.has_pending_request) {
    actionsHtml = `<span class="threadv-pending-note">Your join request is pending approval.</span>`;
  } else if (status.can_join) {
    actionsHtml = `<button class="threadv-btn threadv-btn-primary" id="threadv-join-toggle-btn">Apply to join</button>`;
  } else if (!t.is_open) {
    actionsHtml = `<span class="threadv-pending-note">This thread is closed to new members.</span>`;
  } else if (t.is_full) {
    actionsHtml = `<span class="threadv-pending-note">This thread is full.</span>`;
  } else {
    actionsHtml = `<span class="threadv-pending-note">You can't join this thread.</span>`;
  }

  const relatedPostHtml = t.post ? `
    <div class="threadv-section">
      <div class="threadv-section-title">Related post</div>
      <a class="threadv-related-post" href="/student/posts/view/${t.post.id}">
        ${escapeHtml(t.post.title || 'View post')} <span style="opacity:.6">(${escapeHtml(t.post.post_type || '')})</span>
      </a>
    </div>
  ` : '';

  const membersHtml = status.is_member && Array.isArray(t.members) ? `
    <div class="threadv-section">
      <div class="threadv-section-title">Members (${t.members.length})</div>
      ${t.members.map(m => `
        <div class="threadv-member-row">
          <img class="threadv-member-avatar" src="${escapeHtml(m.avatar || '/static/default-avatar.png')}"
               onerror="this.src='/static/default-avatar.png'" alt="">
          <div>
            <div class="threadv-member-name" data-action="open-member" data-username="${escapeHtml(m.username)}">${escapeHtml(m.name)}</div>
            <div class="threadv-member-role">${escapeHtml(m.role)}</div>
          </div>
        </div>
      `).join('')}
    </div>
  ` : '';

  content.innerHTML = `
    <a href="javascript:history.length > 1 ? history.back() : (window.location.href='/student/search')" class="threadv-back">&larr; Back</a>
    <div class="threadv-card">
      <div class="threadv-header">
        <img class="threadv-avatar" src="${escapeHtml(t.avatar || '/static/default-avatar.png')}"
             onerror="this.src='/static/default-avatar.png'" alt="">
        <div>
          <h1 class="threadv-title">${escapeHtml(t.title)}</h1>
          <div class="threadv-sub">by ${escapeHtml(t.creator?.name || 'Unknown')} · ${formatTime(t.created_at)}</div>
          <div class="threadv-status-row">${statusBadges}</div>
        </div>
      </div>

      ${t.description ? `<div class="threadv-desc">${escapeHtml(t.description)}</div>` : ''}
      ${tagsHtml ? `<div class="threadv-tags">${tagsHtml}</div>` : ''}

      <div class="threadv-stats">
        <div class="threadv-stat">
          <div class="threadv-stat-value">${t.member_count ?? 0}/${t.max_members ?? '—'}</div>
          <div class="threadv-stat-label">Members</div>
        </div>
      </div>

      <div class="threadv-actions">${actionsHtml}</div>

      <div class="threadv-join-form" id="threadv-join-form">
        <textarea class="threadv-join-textarea" id="threadv-join-message"
                  placeholder="Add a short message to your request (optional)…"></textarea>
        <div class="threadv-join-form-actions">
          <button class="threadv-btn threadv-btn-primary" id="threadv-join-submit-btn">Send request</button>
          <button class="threadv-btn" id="threadv-join-cancel-btn">Cancel</button>
        </div>
      </div>
    </div>

    ${relatedPostHtml}
    ${membersHtml}
  `;

  $('threadv-join-toggle-btn')?.addEventListener('click', () => {
    $('threadv-join-form')?.classList.add('is-open');
  });
  $('threadv-join-cancel-btn')?.addEventListener('click', () => {
    $('threadv-join-form')?.classList.remove('is-open');
  });
  $('threadv-join-submit-btn')?.addEventListener('click', handleJoinRequest);
  $('threadv-leave-btn')?.addEventListener('click', handleLeaveThread);
  content.querySelectorAll('[data-action="open-member"]').forEach(el => {
    el.addEventListener('click', () => {
      const username = el.dataset.username;
      if (username) window.location.href = `/student/profile/view/${encodeURIComponent(username)}`;
    });
  });
}

async function handleJoinRequest() {
  const threadId = getThreadId();
  const btn = $('threadv-join-submit-btn');
  const message = $('threadv-join-message')?.value.trim() || '';
  if (!threadId || !btn) return;

  btn.disabled = true;
  try {
    await api.post(`/threads/${threadId}/join`, { message });
    showToast('Join request sent.', 'success');
    await loadThread();
  } catch (error) {
    console.error('[thread-view] Join request failed:', error);
    const msg = error?.message || 'Could not send join request.';
    showToast(msg, 'error');
    btn.disabled = false;
  }
}

async function handleLeaveThread() {
  const threadId = getThreadId();
  const btn = $('threadv-leave-btn');
  if (!threadId || !btn) return;
  if (!confirm('Leave this thread?')) return;

  btn.disabled = true;
  try {
    await api.post(`/threads/${threadId}/leave`, {});
    showToast('You left the thread.', 'success');
    await loadThread();
  } catch (error) {
    console.error('[thread-view] Leave thread failed:', error);
    showToast('Could not leave thread.', 'error');
    btn.disabled = false;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  loadThread();
});
