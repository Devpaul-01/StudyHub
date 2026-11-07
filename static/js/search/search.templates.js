/**
 * ============================================================================
 * SEARCH TEMPLATES — search.templates.js
 *
 * These are deliberately NOT reusing the existing feed post card
 * (createPostCard in static/js/feed/feed.templates.js): that renderer
 * expects a full post object (text_content, a comments array, full
 * reaction/resource data for its interactive buttons) and will render
 * incorrectly or throw on the lighter summary objects
 * GET /search/posts actually returns (excerpt instead of text_content,
 * no comments array, counts only, no attached resources). A search
 * result card is a simpler, read-only summary that links to the full
 * post rather than trying to be fully interactive inline.
 * ============================================================================
 */

function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function timeAgo(iso) {
  if (!iso) return '';
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 2592000) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(iso).toLocaleDateString();
}

// ── User result card ───────────────────────────────────────────────────────
// Fields confirmed against search_users() in services/search_service.py.
export function userResultCard(user) {
  return `
    <div class="search-result-card" data-action="search-open-profile" data-username="${escapeHtml(user.username)}">
      <img class="search-result-avatar" src="${user.avatar || '/static/default-avatar.png'}" alt="${escapeHtml(user.name)}">
      <div class="search-result-body">
        <div class="search-result-title">${escapeHtml(user.name)} <span style="color:var(--text-secondary);font-weight:400;">@${escapeHtml(user.username)}</span></div>
        <div class="search-result-meta">
          ${escapeHtml(user.department || '')}${user.class_level ? ` • ${escapeHtml(user.class_level)}` : ''}
          • ⭐ ${user.reputation ?? 0} (${escapeHtml(user.reputation_level || '')})
          ${user.is_online ? ' • 🟢 Online' : ''}
        </div>
        ${user.bio ? `<div class="search-result-excerpt">${escapeHtml(user.bio)}</div>` : ''}
      </div>
    </div>`;
}

// ── Post result card ──────────────────────────────────────────────────────
// Fields confirmed against search_posts() in services/search_service.py.
export function postResultCard(post) {
  const author = post.author;
  return `
    <div class="search-result-card" data-action="search-open-post" data-post-id="${post.id}">
      <img class="search-result-avatar" src="${author?.avatar || '/static/default-avatar.png'}" alt="${escapeHtml(author?.name || '')}">
      <div class="search-result-body">
        <div class="search-result-title">${escapeHtml(post.title || '(untitled)')}</div>
        <div class="search-result-meta">
          ${escapeHtml(author?.name || 'Unknown')} • ${escapeHtml(post.post_type || '')}
          ${post.department ? ` • ${escapeHtml(post.department)}` : ''}
          • ${timeAgo(post.posted_at)}
          ${post.is_solved ? ' • ✅ Solved' : ''}
        </div>
        ${post.excerpt ? `<div class="search-result-excerpt">${escapeHtml(post.excerpt)}</div>` : ''}
        <div class="search-result-meta" style="margin-top:0.3rem;">
          💬 ${post.comments_count ?? 0} &nbsp; ❤️ ${post.reactions_count ?? 0}
        </div>
      </div>
    </div>`;
}

// ── Thread result card ────────────────────────────────────────────────────
export function threadResultCard(thread) {
  return `
    <div class="search-result-card" data-action="search-open-thread" data-thread-id="${thread.id}">
      <div class="search-result-body" style="width:100%;">
        <div class="search-result-title">${escapeHtml(thread.title || thread.name || '(untitled thread)')}</div>
        <div class="search-result-meta">
          ${thread.department ? `${escapeHtml(thread.department)} • ` : ''}
          👥 ${thread.member_count ?? 0}${thread.max_members ? `/${thread.max_members}` : ''} members
          ${thread.is_open === false ? ' • 🔒 Closed' : ' • 🔓 Open'}
        </div>
        ${thread.description ? `<div class="search-result-excerpt">${escapeHtml(thread.description)}</div>` : ''}
      </div>
    </div>`;
}

export function resultCard(type, item) {
  if (type === 'users') return userResultCard(item);
  if (type === 'posts') return postResultCard(item);
  if (type === 'threads') return threadResultCard(item);
  return '';
}

// ── "All" (global search) results, grouped by section ───────────────────
export function globalResultsSections(results) {
  const sections = [];
  if (results.users?.length) {
    sections.push(`
      <div>
        <div class="search-results-section-title">People</div>
        ${results.users.map(userResultCard).join('')}
      </div>`);
  }
  if (results.posts?.length) {
    sections.push(`
      <div>
        <div class="search-results-section-title">Posts</div>
        ${results.posts.map(postResultCard).join('')}
      </div>`);
  }
  if (results.threads?.length) {
    sections.push(`
      <div>
        <div class="search-results-section-title">Threads</div>
        ${results.threads.map(threadResultCard).join('')}
      </div>`);
  }
  if (!sections.length) {
    return `<div class="search-empty-state">No results found. Try a different search.</div>`;
  }
  return sections.join('');
}

// ── Discovery ("for you") state — shown on an empty query ───────────────
// GET /search/discovery/for-you returns a deliberately lighter shape than
// the main search endpoints (confirmed against personalized_discovery()
// in routes/student/search.py: likes_count not reactions_count, no
// excerpt, no author avatar on posts/threads) — these are separate,
// minimal-field renderers rather than a reuse of the cards above, which
// would render blank avatar/excerpt areas for every item.
function discoveryPostCard(post) {
  return `
    <div class="search-result-card" data-action="search-open-post" data-post-id="${post.id}">
      <div class="search-result-body">
        <div class="search-result-title">${escapeHtml(post.title || '(untitled)')}</div>
        <div class="search-result-meta">
          ${escapeHtml(post.post_type || '')} • ❤️ ${post.likes_count ?? 0}
        </div>
      </div>
    </div>`;
}
function discoveryThreadCard(thread) {
  return `
    <div class="search-result-card" data-action="search-open-thread" data-thread-id="${thread.id}">
      <div class="search-result-body">
        <div class="search-result-title">${escapeHtml(thread.title || thread.name || '(untitled thread)')}</div>
        <div class="search-result-meta">👥 ${thread.member_count ?? 0} members</div>
      </div>
    </div>`;
}
function discoveryUserCard(user) {
  return `
    <div class="search-result-card" data-action="search-open-profile" data-username="${escapeHtml(user.username)}">
      <img class="search-result-avatar" src="${user.avatar || '/static/default-avatar.png'}" alt="${escapeHtml(user.name)}">
      <div class="search-result-body">
        <div class="search-result-title">${escapeHtml(user.name)}</div>
        <div class="search-result-meta">${escapeHtml(user.department || '')}</div>
      </div>
    </div>`;
}

export function discoverySections(data) {
  const sections = [];
  if (data.recommended_users?.length) {
    sections.push(`
      <div>
        <div class="search-results-section-title">People you might know</div>
        ${data.recommended_users.map(discoveryUserCard).join('')}
      </div>`);
  }
  if (data.recommended_posts?.length) {
    sections.push(`
      <div>
        <div class="search-results-section-title">Posts for you</div>
        ${data.recommended_posts.map(discoveryPostCard).join('')}
      </div>`);
  }
  if (data.recommended_threads?.length) {
    sections.push(`
      <div>
        <div class="search-results-section-title">Threads you could join</div>
        ${data.recommended_threads.map(discoveryThreadCard).join('')}
      </div>`);
  }
  if (!sections.length) {
    return `<div class="search-empty-state">Start typing to search people, posts, and threads.</div>`;
  }
  return sections.join('');
}

// ── Suggestions dropdown ──────────────────────────────────────────────────
// GET /search/suggestions returns {type, id, text, display, ...} per
// item — `display` is the human-readable label to show, `text` is what
// to actually put in the search box if the suggestion is chosen.
export function suggestionsList(suggestions) {
  if (!suggestions?.length) return '';
  return suggestions.map(s => `
    <div class="search-suggestion-item" data-action="search-pick-suggestion"
         data-text="${escapeHtml(s.text)}" data-type="${escapeHtml(s.type || '')}"
         data-id="${s.id ?? ''}">
      ${s.type === 'user' ? '👤' : s.type === 'post' ? '📝' : s.type === 'thread' ? '💬' : '🔍'}
      ${escapeHtml(s.display || s.text)}
    </div>`).join('');
}

// ── Recent searches chips ─────────────────────────────────────────────────
export function recentSearchesChips(queries) {
  if (!queries?.length) return `<div class="search-empty-state" style="padding:0.5rem;">No recent searches</div>`;
  return queries.map(q => `
    <span class="search-recent-chip" data-action="search-pick-recent" data-query="${escapeHtml(q)}">${escapeHtml(q)}</span>
  `).join('');
}
