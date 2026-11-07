/**
 * ============================================================================
 * PROFILE-VIEW TEMPLATES — profile-view.templates.js
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

// Connection-status → action button, matching the states the backend
// actually returns (confirmed against view_profile() in
// routes/student/profile.py: none / pending_sent / pending_received /
// connected / blocked).
function connectionActionButton(status, isOwnProfile) {
  if (isOwnProfile) return '';
  switch (status) {
    case 'connected':
      return `<button class="pv-btn" data-action="pv-message">Message</button>`;
    case 'pending_sent':
      return `<button class="pv-btn" disabled>Request Sent</button>`;
    case 'pending_received':
      return `<button class="pv-btn pv-btn-primary" data-action="pv-go-to-connections">Respond to Request</button>`;
    case 'blocked':
      return ''; // nothing actionable to offer here
    default:
      return `<button class="pv-btn pv-btn-primary" data-action="pv-connect">Connect</button>`;
  }
}

export function renderPrivateProfile(data, isOwnProfile) {
  const u = data.user;
  return `
    <div class="pv-header">
      <img class="pv-avatar" src="${u.avatar || '/static/default-avatar.png'}" alt="${escapeHtml(u.name)}">
      <div>
        <h1 class="pv-name">${escapeHtml(u.name)}</h1>
        <div class="pv-username">@${escapeHtml(u.username)}</div>
        <div class="pv-meta">${escapeHtml(u.department || '')}${u.class_level ? ` • ${escapeHtml(u.class_level)}` : ''}</div>
        <div class="pv-actions">${connectionActionButton(data.connection_status, isOwnProfile)}</div>
      </div>
    </div>
    <div class="pv-private-notice">
      🔒 ${escapeHtml(data.privacy?.message || 'This profile is private.')}
    </div>
    ${u.pinned_posts?.length ? `
      <div class="pv-section">
        <div class="pv-section-title">Pinned</div>
        ${u.pinned_posts.map(p => `
          <div class="pv-post-row" data-action="pv-open-post" data-post-id="${p.id}">
            ${escapeHtml(p.title)}
            <div class="pv-post-meta">${escapeHtml(p.post_type || '')} • ${timeAgo(p.posted_at)}</div>
          </div>`).join('')}
      </div>` : ''}
  `;
}

export function renderPublicProfile(data, isOwnProfile) {
  const u      = data.user;
  const stats  = data.stats;
  const badges = data.badges || [];
  // The backend's recent_posts is already ordered pinned-first (see
  // view_profile()'s query: .order_by(Post.is_pinned.desc(), ...)), so
  // this doesn't need a separate pinned-posts section the way the
  // private branch above does — the backend's public response doesn't
  // actually include a separate pinned_posts field at all (confirmed:
  // only the private branch's response has one), so recent_posts is the
  // single source of truth here rather than something to merge with it.
  const posts   = data.recent_posts || [];
  const threads = data.active_threads || [];
  const skills  = data.skills || [];
  const goals   = data.learning_goals || [];

  return `
    <div class="pv-header">
      <img class="pv-avatar" src="${u.avatar || '/static/default-avatar.png'}" alt="${escapeHtml(u.name)}">
      <div>
        <h1 class="pv-name">${escapeHtml(u.name)}</h1>
        <div class="pv-username">@${escapeHtml(u.username)}</div>
        <div class="pv-meta">
          ${escapeHtml(u.department || '')}${u.class_level ? ` • ${escapeHtml(u.class_level)}` : ''}
          • ⭐ ${u.reputation ?? 0} (${escapeHtml(u.reputation_level || '')})
          ${u.active_status && u.last_active ? ` • Active ${timeAgo(u.last_active)}` : ''}
        </div>
        ${u.bio ? `<div class="pv-bio">${escapeHtml(u.bio)}</div>` : ''}
        <div class="pv-actions">${connectionActionButton(data.connection_status, isOwnProfile)}</div>
      </div>
    </div>

    <div class="pv-stat-grid">
      <div class="pv-stat"><div class="pv-stat-value">${stats.total_posts}</div><div class="pv-stat-label">Posts</div></div>
      <div class="pv-stat"><div class="pv-stat-value">${stats.total_comments}</div><div class="pv-stat-label">Comments</div></div>
      <div class="pv-stat"><div class="pv-stat-value">${stats.total_threads}</div><div class="pv-stat-label">Threads</div></div>
      <div class="pv-stat"><div class="pv-stat-value">${stats.total_helpful}</div><div class="pv-stat-label">Helpful</div></div>
    </div>

    ${badges.length ? `
      <div class="pv-section">
        <div class="pv-section-title">Badges</div>
        <div class="pv-badge-grid">
          ${badges.map(b => `
            <div class="pv-badge" title="${escapeHtml(b.description)}">
              <div class="pv-badge-icon">${b.icon || '🏅'}</div>
              <div class="pv-badge-name">${escapeHtml(b.name)}</div>
            </div>`).join('')}
        </div>
      </div>` : ''}

    ${skills.length ? `
      <div class="pv-section">
        <div class="pv-section-title">Skills</div>
        ${skills.map(s => `<span class="pv-chip">${escapeHtml(s)}</span>`).join('')}
      </div>` : ''}

    ${goals.length ? `
      <div class="pv-section">
        <div class="pv-section-title">Learning Goals</div>
        ${goals.map(g => `<div class="pv-empty" style="color:var(--text-primary);">• ${escapeHtml(g)}</div>`).join('')}
      </div>` : ''}

    <div class="pv-section">
      <div class="pv-section-title">Recent Posts</div>
      ${posts.length ? posts.map(p => `
        <div class="pv-post-row" data-action="pv-open-post" data-post-id="${p.id}">
          ${p.is_pinned ? '📌 ' : ''}${escapeHtml(p.title)}
          <div class="pv-post-meta">
            ${escapeHtml(p.post_type || '')} • 💬 ${p.comments_count ?? 0} • ❤️ ${p.likes_count ?? 0}
            ${p.is_solved ? ' • ✅ Solved' : ''} • ${timeAgo(p.posted_at)}
          </div>
        </div>`).join('') : `<div class="pv-empty">No posts yet</div>`}
    </div>

    <div class="pv-section">
      <div class="pv-section-title">Active Threads</div>
      ${threads.length ? threads.map(t => `
        <div class="pv-thread-row" data-action="pv-open-thread" data-thread-id="${t.id}">
          ${escapeHtml(t.title)}${t.is_creator ? ' 👑' : ''}
          <div class="pv-thread-meta">👥 ${t.member_count} members • active ${timeAgo(t.last_activity)}</div>
        </div>`).join('') : `<div class="pv-empty">Not in any threads yet</div>`}
    </div>
  `;
}
