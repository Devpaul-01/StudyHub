/**
 * ============================================================================
 * POST VIEW — post-view.js
 * Dedicated single-post detail page. Self-contained: talks to the same
 * backend endpoints the main feed uses (GET/POST on /posts/<id>, /react,
 * /comments, /bookmark, /mark-solved) via the shared `api` client from
 * core/api.js (loaded globally by shell_base.html), but does NOT depend
 * on feed.state.js / app.unified.js / the feed2.html SPA globals — this
 * page is reachable standalone from search results and from another
 * user's profile, so it can't assume any of that machinery exists.
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

function getPostId() {
  // /student/posts/view/<id>
  const match = window.location.pathname.match(/\/posts\/view\/(\d+)/);
  return match ? match[1] : null;
}

let state = {
  post: null,
  stats: null,
  author: null,
  userInteraction: null,
  permissions: null,
  comments: [],
  commentResources: [], // staged attachments for the comment composer: [{url, type, filename}]
};

const MAX_COMMENT_RESOURCES = 10;
const FILE_SIZE_LIMITS = {
  image: 5 * 1024 * 1024,   // 5MB
  video: 50 * 1024 * 1024,  // 50MB
  other: 10 * 1024 * 1024,  // 10MB
};

function validateCommentFile(file) {
  const maxSize = file.type.startsWith('image/') ? FILE_SIZE_LIMITS.image
    : file.type.startsWith('video/') ? FILE_SIZE_LIMITS.video
    : FILE_SIZE_LIMITS.other;
  if (file.size > maxSize) {
    showToast(`File "${file.name}" exceeds size limit (${maxSize / 1024 / 1024}MB max)`, 'error');
    return false;
  }
  return true;
}

async function loadPost() {
  const postId = getPostId();
  const content = $('postv-content');
  if (!postId) {
    content.innerHTML = `<div class="postv-error">No post specified.</div>`;
    return;
  }

  try {
    const res = await api.get(`/posts/${postId}`);
    const data = res?.data;
    if (!data?.post) {
      content.innerHTML = `<div class="postv-error">Post not found.</div>`;
      return;
    }
    state.post = data.post;
    state.stats = data.stats;
    state.author = data.author;
    state.userInteraction = data.user_interaction;
    state.permissions = data.permissions;

    render();
    loadComments(postId);
  } catch (error) {
    console.error('[post-view] Failed to load post:', error);
    content.innerHTML = `<div class="postv-error">Something went wrong loading this post. Please try again.</div>`;
  }
}

function render() {
  const { post, stats, author, userInteraction, permissions } = state;
  const content = $('postv-content');

  const tagsHtml = (post.tags || []).map(t =>
    `<span class="postv-tag">#${escapeHtml(t)}</span>`
  ).join('');

  const resourcesHtml = (post.resources || []).map(r => {
    const url = typeof r === 'string' ? r : r.url;
    const filename = typeof r === 'string' ? 'Resource' : (r.filename || 'Resource');
    if (!url) return '';
    return `<a class="postv-resource-link" href="${escapeHtml(url)}" target="_blank" rel="noopener">
      📎 ${escapeHtml(filename)}
    </a>`;
  }).join('');

  const canSolveType = ['question', 'problem'].includes(post.post_type);

  content.innerHTML = `
    <a href="javascript:history.length > 1 ? history.back() : (window.location.href='/student/search')" class="postv-back">&larr; Back</a>
    <div class="postv-card">
      <div class="postv-header">
        <img class="postv-avatar" data-action="open-author"
             src="${escapeHtml(author?.avatar || '/static/default-avatar.png')}"
             onerror="this.src='/static/default-avatar.png'" alt="">
        <div class="postv-author-block">
          <div class="postv-author-name" data-action="open-author">${escapeHtml(author?.name || 'Anonymous')}</div>
          <div class="postv-meta">@${escapeHtml(author?.username || 'unknown')} · ${formatTime(post.posted_at)}</div>
          <div class="postv-badges">
            <span class="postv-badge postv-badge-type">${escapeHtml(post.post_type)}</span>
            ${post.is_solved ? `<span class="postv-badge postv-badge-solved">✓ Solved</span>` : ''}
          </div>
        </div>
      </div>

      ${post.title ? `<div class="postv-title">${escapeHtml(post.title)}</div>` : ''}
      <div class="postv-text">${escapeHtml(post.text_content || '')}</div>

      ${tagsHtml ? `<div class="postv-tags">${tagsHtml}</div>` : ''}
      ${resourcesHtml ? `<div class="postv-resources">${resourcesHtml}</div>` : ''}

      <div class="postv-actions">
        <button class="postv-action-btn ${userInteraction.reaction ? 'is-active' : ''}" id="postv-react-btn">
          ❤️ <span id="postv-react-count">${stats.reactions_count || 0}</span>
        </button>
        <button class="postv-action-btn" id="postv-comment-focus-btn">
          💬 <span>${stats.comments_count || 0}</span>
        </button>
        <button class="postv-action-btn ${userInteraction.bookmarked ? 'is-active' : ''}" id="postv-bookmark-btn">
          🔖 ${userInteraction.bookmarked ? 'Saved' : 'Save'}
        </button>
        ${permissions.can_mark_solved && canSolveType ? `
          <button class="postv-action-btn postv-solved-btn" id="postv-solved-btn">
            ${post.is_solved ? 'Unmark solved' : 'Mark solved'}
          </button>
        ` : ''}
      </div>
    </div>

    <div class="postv-comments-section">
      <div class="postv-comments-title">Comments</div>
      <div class="postv-comment-form">
        <textarea class="postv-comment-input" id="postv-comment-input"
                  placeholder="Write a comment…" ${permissions.can_comment ? '' : 'disabled'}></textarea>
        <div class="postv-comment-toolbar">
          <label class="postv-upload-btn" title="Attach image">
            <input type="file" id="postv-comment-image-input" accept="image/*" hidden ${permissions.can_comment ? '' : 'disabled'}>
            🖼️
          </label>
          <label class="postv-upload-btn" title="Attach video">
            <input type="file" id="postv-comment-video-input" accept="video/*" hidden ${permissions.can_comment ? '' : 'disabled'}>
            🎬
          </label>
          <label class="postv-upload-btn" title="Attach document">
            <input type="file" id="postv-comment-doc-input" accept=".pdf,.doc,.docx,.txt" hidden ${permissions.can_comment ? '' : 'disabled'}>
            📎
          </label>
          <button class="postv-comment-submit" id="postv-comment-submit" ${permissions.can_comment ? '' : 'disabled'}>Post</button>
        </div>
        <div id="postv-comment-preview-area" class="postv-comment-preview-area"></div>
      </div>
      <div id="postv-comments-list"><div class="postv-empty-comments">Loading comments…</div></div>
    </div>
  `;

  $('postv-react-btn')?.addEventListener('click', handleReact);
  $('postv-bookmark-btn')?.addEventListener('click', handleBookmark);
  $('postv-solved-btn')?.addEventListener('click', handleToggleSolved);
  $('postv-comment-focus-btn')?.addEventListener('click', () => $('postv-comment-input')?.focus());
  $('postv-comment-submit')?.addEventListener('click', handleSubmitComment);
  $('postv-comment-image-input')?.addEventListener('change', handleCommentFileSelect);
  $('postv-comment-video-input')?.addEventListener('change', handleCommentFileSelect);
  $('postv-comment-doc-input')?.addEventListener('change', handleCommentFileSelect);
  content.querySelectorAll('[data-action="open-author"]').forEach(el => {
    el.addEventListener('click', () => {
      if (author?.username) {
        window.location.href = `/student/profile/view/${encodeURIComponent(author.username)}`;
      }
    });
  });
}

async function handleReact() {
  const postId = getPostId();
  const btn = $('postv-react-btn');
  if (!postId || !btn) return;
  btn.disabled = true;
  try {
    const res = await api.post(`/posts/${postId}/react`, {});
    const liked = res?.data?.liked;
    const count = res?.data?.count ?? 0;
    btn.classList.toggle('is-active', !!liked);
    $('postv-react-count').textContent = count;
    state.userInteraction.reaction = liked ? 'like' : null;
  } catch (error) {
    console.error('[post-view] React failed:', error);
    showToast('Could not react to this post.', 'error');
  } finally {
    btn.disabled = false;
  }
}

async function handleBookmark() {
  const postId = getPostId();
  const btn = $('postv-bookmark-btn');
  if (!postId || !btn) return;
  btn.disabled = true;
  try {
    const res = await api.post(`/posts/${postId}/bookmark`, {});
    const bookmarked = res?.data?.bookmarked ?? !state.userInteraction.bookmarked;
    state.userInteraction.bookmarked = bookmarked;
    btn.classList.toggle('is-active', bookmarked);
    btn.innerHTML = bookmarked ? '🔖 Saved' : '🔖 Save';
    showToast(bookmarked ? 'Post saved.' : 'Removed from saved.', 'success');
  } catch (error) {
    console.error('[post-view] Bookmark failed:', error);
    showToast('Could not update bookmark.', 'error');
  } finally {
    btn.disabled = false;
  }
}

async function handleToggleSolved() {
  const postId = getPostId();
  const btn = $('postv-solved-btn');
  if (!postId || !btn) return;
  const endpoint = state.post.is_solved ? 'unmark-solved' : 'mark-solved';
  btn.disabled = true;
  try {
    await api.post(`/posts/${postId}/${endpoint}`, {});
    state.post.is_solved = !state.post.is_solved;
    render();
    loadComments(postId);
    showToast(state.post.is_solved ? 'Marked as solved.' : 'Unmarked as solved.', 'success');
  } catch (error) {
    console.error('[post-view] Toggle solved failed:', error);
    showToast('Could not update solved status.', 'error');
    btn.disabled = false;
  }
}

async function loadComments(postId) {
  const list = $('postv-comments-list');
  try {
    const res = await api.get(`/posts/${postId}/comments`);
    state.comments = res?.data?.comments || [];
    renderComments();
  } catch (error) {
    console.error('[post-view] Failed to load comments:', error);
    if (list) list.innerHTML = `<div class="postv-empty-comments">Could not load comments.</div>`;
  }
}

function commentResourcesHtml(resources, commentId) {
  if (!resources || !resources.length) return '';
  const items = resources.map(r => {
    const filename = escapeHtml(r.filename || 'Resource');
    const url = escapeHtml(r.url || '');
    if (r.type === 'image') {
      return `<a class="postv-comment-resource postv-comment-resource-image" href="${url}" target="_blank" rel="noopener">
        <img src="${url}" alt="${filename}" loading="lazy">
      </a>`;
    }
    if (r.type === 'video') {
      return `<div class="postv-comment-resource postv-comment-resource-video">
        <video src="${url}" playsinline preload="metadata" controls></video>
      </div>`;
    }
    return `<a class="postv-comment-resource postv-comment-resource-doc" href="${url}" target="_blank" rel="noopener" download="${filename}">
      📄 <span class="postv-comment-resource-name">${filename}</span>
    </a>`;
  }).join('');
  return `<div class="postv-comment-resources" data-comment-id="${commentId}">${items}</div>`;
}

function commentHtml(c, postId) {
  const liked = c.user_interactions?.has_liked;
  const helpful = c.user_interactions?.has_marked_helpful;
  const repliesHtml = (c.replies || []).map(r => commentHtml(r, postId)).join('');
  return `
    <div class="postv-comment" data-comment-id="${c.id}">
      <div class="postv-comment-header">
        <img class="postv-comment-avatar" src="${escapeHtml(c.author?.avatar || '/static/default-avatar.png')}"
             onerror="this.src='/static/default-avatar.png'" alt="">
        <span class="postv-comment-author">${escapeHtml(c.author?.name || 'Anonymous')}</span>
        <span class="postv-comment-time">${formatTime(c.posted_at)}</span>
        ${c.is_solution ? `<span class="postv-comment-solution">✓ Solution</span>` : ''}
      </div>
      <div class="postv-comment-body">${escapeHtml(c.text_content)}</div>
      ${commentResourcesHtml(c.resources, c.id)}
      <div class="postv-comment-footer">
        <button class="postv-comment-action ${liked ? 'is-active' : ''}" data-action="like-comment" data-comment-id="${c.id}">
          👍 ${c.likes_count || 0}
        </button>
        <button class="postv-comment-action ${helpful ? 'is-active' : ''}" data-action="helpful-comment" data-comment-id="${c.id}">
          Helpful ${c.helpful_count ? `(${c.helpful_count})` : ''}
        </button>
        ${c.can_reply ? `<button class="postv-comment-action" data-action="reply-comment" data-comment-id="${c.id}" data-username="${escapeHtml(c.author?.username || '')}">Reply</button>` : ''}
        ${state.permissions?.can_mark_solved && !c.is_solution ? `<button class="postv-comment-action" data-action="mark-solution" data-comment-id="${c.id}">Mark as solution</button>` : ''}
        ${state.permissions?.can_mark_solved && c.is_solution ? `<button class="postv-comment-action" data-action="unmark-solution" data-comment-id="${c.id}">Unmark as solution</button>` : ''}
      </div>
      ${repliesHtml ? `<div class="postv-replies">${repliesHtml}</div>` : ''}
    </div>
  `;
}

function renderComments() {
  const list = $('postv-comments-list');
  if (!list) return;
  if (!state.comments.length) {
    list.innerHTML = `<div class="postv-empty-comments">No comments yet. Be the first to comment.</div>`;
    return;
  }
  const postId = getPostId();
  list.innerHTML = state.comments.map(c => commentHtml(c, postId)).join('');

  list.querySelectorAll('[data-action="like-comment"]').forEach(btn => {
    btn.addEventListener('click', () => handleLikeComment(btn.dataset.commentId, btn));
  });
  list.querySelectorAll('[data-action="helpful-comment"]').forEach(btn => {
    btn.addEventListener('click', () => handleHelpfulComment(btn.dataset.commentId, btn));
  });
  list.querySelectorAll('[data-action="reply-comment"]').forEach(btn => {
    btn.addEventListener('click', () => startReply(btn.dataset.commentId, btn.dataset.username));
  });
  list.querySelectorAll('[data-action="mark-solution"]').forEach(btn => {
    btn.addEventListener('click', () => handleMarkSolution(btn.dataset.commentId));
  });
  list.querySelectorAll('[data-action="unmark-solution"]').forEach(btn => {
    btn.addEventListener('click', () => handleUnmarkSolution(btn.dataset.commentId));
  });
}

async function handleLikeComment(commentId, btn) {
  btn.disabled = true;
  try {
    await api.post(`/comments/${commentId}/like`, {});
    loadComments(getPostId());
  } catch (error) {
    console.error('[post-view] Comment like failed:', error);
    showToast('Could not like comment.', 'error');
    btn.disabled = false;
  }
}

async function handleHelpfulComment(commentId, btn) {
  btn.disabled = true;
  try {
    await api.post(`/comments/${commentId}/mark-helpful`, {});
    loadComments(getPostId());
  } catch (error) {
    console.error('[post-view] Mark helpful failed:', error);
    showToast('Could not mark comment as helpful.', 'error');
    btn.disabled = false;
  }
}

async function handleMarkSolution(commentId) {
  try {
    await api.post(`/posts/${getPostId()}/mark-solution`, { comment_id: commentId });
    showToast('Marked as solution.', 'success');
    loadComments(getPostId());
  } catch (error) {
    console.error('[post-view] Mark solution failed:', error);
    showToast('Could not mark as solution.', 'error');
  }
}

async function handleUnmarkSolution(commentId) {
  try {
    await api.post(`/posts/${getPostId()}/unmark-solution`, { comment_id: commentId });
    showToast('Unmarked as solution.', 'success');
    loadComments(getPostId());
  } catch (error) {
    console.error('[post-view] Unmark solution failed:', error);
    showToast('Could not unmark as solution.', 'error');
  }
}

let replyTarget = null;

function startReply(commentId, username) {
  replyTarget = { commentId, username };
  const input = $('postv-comment-input');
  if (input) {
    input.value = `@${username} `;
    input.focus();
  }
}

async function handleCommentFileSelect(e) {
  const files = Array.from(e.target.files || []);
  e.target.value = '';
  if (!files.length) return;

  const previewArea = $('postv-comment-preview-area');
  if (!previewArea) return;

  for (const file of files) {
    if (state.commentResources.length >= MAX_COMMENT_RESOURCES) {
      showToast(`Maximum ${MAX_COMMENT_RESOURCES} files per comment`, 'error');
      break;
    }
    if (!validateCommentFile(file)) continue;

    const item = document.createElement('div');
    item.className = 'postv-preview-item';

    let media;
    if (file.type.startsWith('image/')) {
      media = document.createElement('img');
      media.src = URL.createObjectURL(file);
      media.className = 'postv-preview-media';
    } else if (file.type.startsWith('video/')) {
      media = document.createElement('video');
      media.src = URL.createObjectURL(file);
      media.controls = true;
      media.className = 'postv-preview-media';
    } else {
      media = document.createElement('div');
      media.className = 'postv-preview-file';
      media.textContent = file.name;
    }
    item.appendChild(media);

    const loader = document.createElement('div');
    loader.className = 'postv-preview-loader';
    loader.textContent = 'Uploading…';
    item.appendChild(loader);

    const removeBtn = document.createElement('button');
    removeBtn.className = 'postv-preview-remove';
    removeBtn.textContent = '×';
    removeBtn.style.display = 'none';
    item.appendChild(removeBtn);

    previewArea.appendChild(item);

    try {
      const res = await api.post('/posts/resource/upload', (() => {
        const fd = new FormData();
        fd.append('file', file);
        return fd;
      })(), true);
      if (res?.status === 'success' || res?.data?.url) {
        const resource = { url: res.data.url, type: res.data.type, filename: res.data.filename };
        state.commentResources.push(resource);
        loader.remove();
        removeBtn.style.display = '';
        removeBtn.addEventListener('click', () => {
          item.remove();
          state.commentResources = state.commentResources.filter(r => r.url !== resource.url);
        });
      } else {
        loader.textContent = 'Failed';
        loader.classList.add('is-error');
        showToast('Upload failed: ' + (res?.message || 'Unknown error'), 'error');
      }
    } catch (error) {
      console.error('[post-view] Comment attachment upload failed:', error);
      loader.textContent = 'Error';
      loader.classList.add('is-error');
      showToast('Could not upload file.', 'error');
    }
  }
}

async function handleSubmitComment() {
  const postId = getPostId();
  const input = $('postv-comment-input');
  const btn = $('postv-comment-submit');
  const text = input?.value.trim();
  if (!text && !state.commentResources.length) return;

  btn.disabled = true;
  try {
    await api.post('/comments/create', {
      post_id: Number(postId),
      text_content: text || '',
      parent_id: replyTarget ? Number(replyTarget.commentId) : null,
      resources: state.commentResources,
    });
    input.value = '';
    replyTarget = null;
    state.commentResources = [];
    const previewArea = $('postv-comment-preview-area');
    if (previewArea) previewArea.innerHTML = '';
    showToast('Comment posted.', 'success');
    loadComments(postId);
    // Refresh the comment count shown in the action bar.
    const res = await api.get(`/posts/${postId}`);
    if (res?.data?.stats) {
      state.stats = res.data.stats;
      const commentBtn = $('postv-comment-focus-btn');
      if (commentBtn) commentBtn.innerHTML = `💬 <span>${state.stats.comments_count}</span>`;
    }
  } catch (error) {
    console.error('[post-view] Post comment failed:', error);
    showToast('Could not post comment.', 'error');
  } finally {
    btn.disabled = false;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  loadPost();
});
