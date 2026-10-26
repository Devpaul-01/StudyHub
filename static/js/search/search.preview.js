/**
 * ============================================================================
 * SEARCH PREVIEW — search.preview.js
 *
 * Lightweight, read-only preview modals for a post or thread clicked from
 * a search result. See the comment on openPost()/openThread() in
 * search.events.js for why these exist instead of a "deep link" — there
 * is no single-post or single-thread page anywhere else in this app to
 * link to.
 *
 * These use the global `api` client (see search.api.js's header comment)
 * directly rather than going through searchAPI, since GET /posts/<id>
 * and GET /threads/<id> are post/thread endpoints, not search endpoints.
 * ============================================================================
 */

function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function openPreviewModal(title, bodyHtml) {
  document.getElementById('search-preview-modal')?.remove();
  const el = document.createElement('div');
  el.id = 'search-preview-modal';
  el.style.cssText = `
    position: fixed; inset: 0; z-index: 1300; display: flex;
    align-items: center; justify-content: center; padding: 1rem;
    background: rgba(0,0,0,0.5);
  `;
  el.innerHTML = `
    <div style="background: var(--bg-white,#fff); border-radius: var(--radius-lg,16px);
                max-width: 560px; width: 100%; max-height: 80vh; overflow-y: auto;
                padding: 1.25rem;">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:0.75rem;">
        <h3 style="margin:0;font-size:1.05rem;">${escapeHtml(title)}</h3>
        <button id="search-preview-close" style="background:none;border:none;font-size:1.2rem;cursor:pointer;">✕</button>
      </div>
      ${bodyHtml}
    </div>
  `;
  document.body.appendChild(el);
  document.getElementById('search-preview-close').addEventListener('click', closePreviewModal);
  el.addEventListener('click', (e) => { if (e.target === el) closePreviewModal(); });
}

function closePreviewModal() {
  document.getElementById('search-preview-modal')?.remove();
}

export async function showPostPreview(postId) {
  openPreviewModal('Loading…', '<div style="padding:1rem;text-align:center;">Loading…</div>');
  try {
    const response = await api.get(`/posts/${postId}`);
    const post = response?.data?.post;
    const stats = response?.data?.stats || {};
    const author = response?.data?.author;

    if (!post) {
      openPreviewModal('Post', '<div style="padding:1rem;">Could not load this post.</div>');
      return;
    }

    openPreviewModal(post.title || '(untitled)', `
      <div style="font-size:0.8rem;color:#6c757d;margin-bottom:0.6rem;">
        ${author ? escapeHtml(author.name) : 'Unknown'} • ${escapeHtml(post.post_type || '')}
        ${post.department ? ` • ${escapeHtml(post.department)}` : ''}
        ${post.is_solved ? ' • ✅ Solved' : ''}
      </div>
      <div style="white-space:pre-wrap;font-size:0.9rem;line-height:1.5;margin-bottom:0.8rem;">
        ${escapeHtml(post.text_content || '')}
      </div>
      <div style="font-size:0.8rem;color:#6c757d;">
        💬 ${stats.comments_count ?? 0} comments &nbsp; ❤️ ${stats.reactions_count ?? 0} reactions
        &nbsp; 👁️ ${stats.views ?? 0} views
      </div>
      <p style="font-size:0.75rem;color:#6c757d;margin-top:0.8rem;">
        This is a read-only preview — there is no standalone page for a
        single post in this app yet. To comment or react, find this post
        in your feed.
      </p>
    `);
  } catch (error) {
    console.error('Post preview error:', error);
    openPreviewModal('Post', '<div style="padding:1rem;">Could not load this post.</div>');
  }
}

export async function showThreadPreview(threadId) {
  openPreviewModal('Loading…', '<div style="padding:1rem;text-align:center;">Loading…</div>');
  try {
    const response = await api.get(`/threads/${threadId}`);
    const thread = response?.data?.thread || response?.data;

    if (!thread) {
      openPreviewModal('Thread', '<div style="padding:1rem;">Could not load this thread.</div>');
      return;
    }

    openPreviewModal(thread.title || '(untitled thread)', `
      <div style="font-size:0.8rem;color:#6c757d;margin-bottom:0.6rem;">
        ${thread.department ? `${escapeHtml(thread.department)} • ` : ''}
        👥 ${thread.member_count ?? 0}${thread.max_members ? `/${thread.max_members}` : ''} members
        ${thread.is_open === false ? ' • 🔒 Closed' : ' • 🔓 Open'}
      </div>
      ${thread.description ? `<div style="font-size:0.9rem;line-height:1.5;margin-bottom:0.8rem;">${escapeHtml(thread.description)}</div>` : ''}
      <p style="font-size:0.75rem;color:#6c757d;margin-top:0.8rem;">
        This is a read-only preview. To join or message this thread, find
        it in the Threads section.
      </p>
    `);
  } catch (error) {
    console.error('Thread preview error:', error);
    openPreviewModal('Thread', '<div style="padding:1rem;">Could not load this thread.</div>');
  }
}
