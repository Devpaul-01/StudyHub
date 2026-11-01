/**
 * ============================================================================
 * PROFILE TEMPLATES — profile.templates.js
 * Save as: /static/js/profile/profile.templates.js
 * Pure HTML generation — no side effects, no API calls.
 * ============================================================================
 */

import { formatTime } from '../feed/feed.utils.js';
import { buildPostResourcesContainer, buildResourceLinks } from '../feed/feed.templates.js';

// ─────────────────────────────────────────────────────────────────────────────
// PRIVATE HELPERS
// ─────────────────────────────────────────────────────────────────────────────

const POST_TYPE_META = {
  question    : { label: 'Question',     icon: '❓', color: '#3b82f6' },
  problem     : { label: 'Problem',      icon: '🧩', color: '#8b5cf6' },
  resource    : { label: 'Resource',     icon: '📚', color: '#10b981' },
  discussion  : { label: 'Discussion',   icon: '💬', color: '#f59e0b' },
  announcement: { label: 'Announcement', icon: '📢', color: '#ef4444' },
};

function getTypeMeta(type) {
  return POST_TYPE_META[type] || { label: type || 'Post', icon: '📄', color: '#6b7280' };
}

function buildAvatar(url, name, size = 60) {
  const initials = (name || '?')
    .split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();
  if (url) {
    return `<img src="${url}" alt="${name || ''}"
              style="width:${size}px;height:${size}px;border-radius:50%;object-fit:cover;display:block;">`;
  }
  const fs = Math.round(size * 0.36);
  return `<div style="width:${size}px;height:${size}px;border-radius:50%;background:var(--primary);
                      color:#fff;display:flex;align-items:center;justify-content:center;
                      font-weight:700;font-size:${fs}px;flex-shrink:0;">${initials}</div>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// SKELETON (shown while loading)
// ─────────────────────────────────────────────────────────────────────────────

export function buildSkeleton(rows = 3) {
  const items = Array.from({ length: rows }, () =>
    `<div style="background:var(--bg-tertiary);border-radius:12px;height:85px;margin-bottom:0.75rem;"></div>`
  ).join('');
  return `<div class="profile-skeleton">${items}</div>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// PROFILE HEADER
// ─────────────────────────────────────────────────────────────────────────────

export function buildProfileHeader(data) {
  if (!data) return '';
  const stats  = data.stats || {};
  const streak = data.login_streak || 0;
  const goals  = (data.learning_goals || []).slice(0, 3);

  function stat(val, label) {
    return `<div style="text-align:center;flex:1;">
      <div style="font-size:1.15rem;font-weight:700;color:var(--text-primary);">${val ?? 0}</div>
      <div style="font-size:0.68rem;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.04em;">${label}</div>
    </div>`;
  }

  return `
    <div class="profile-header-card">
      <!-- Avatar + info row -->
      <div style="display:flex;gap:1rem;align-items:flex-start;">

        <!-- Avatar -->
        <div style="position:relative;flex-shrink:0;">
          ${buildAvatar(data.avatar, data.name, 72)}
          <button data-action="profile-open-avatar" title="Change photo"
            style="position:absolute;bottom:0;right:-2px;width:22px;height:22px;border-radius:50%;
                   background:var(--primary);border:2px solid var(--bg-secondary);cursor:pointer;
                   display:flex;align-items:center;justify-content:center;padding:0;">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.5">
              <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
              <circle cx="12" cy="13" r="4"/>
            </svg>
          </button>
        </div>

        <!-- Name / username / bio -->
        <div style="flex:1;min-width:0;">
          <div style="display:flex;align-items:center;gap:0.45rem;flex-wrap:wrap;margin-bottom:0.1rem;">
            <span style="font-size:1.05rem;font-weight:700;color:var(--text-primary);">${data.name || ''}</span>
            ${streak >= 3 ? `<span style="font-size:0.8rem;" title="${streak}-day streak">🔥${streak}</span>` : ''}
          </div>
          <div style="font-size:0.75rem;color:var(--text-secondary);margin-bottom:0.35rem;">@${data.username || ''}</div>
          ${data.bio
            ? `<p style="margin:0 0 0.4rem;font-size:0.82rem;color:var(--text-primary);line-height:1.5;word-break:break-word;">${data.bio}</p>`
            : ''}
          <div style="display:flex;gap:0.35rem;flex-wrap:wrap;">
            ${data.department ? `<span class="p-chip">🏫 ${data.department}</span>` : ''}
            ${data.class_level ? `<span class="p-chip">📖 ${data.class_level}</span>` : ''}
            <span class="p-chip">⭐ ${data.reputation_level || 'Newbie'}</span>
          </div>
        </div>

        <!-- Edit button -->
        <button data-action="profile-open-edit"
          style="padding:0.4rem 0.8rem;background:var(--primary);color:#fff;border:none;
                 border-radius:8px;cursor:pointer;font-size:0.78rem;font-weight:600;
                 white-space:nowrap;flex-shrink:0;align-self:flex-start;">
          ✏️ Edit
        </button>
      </div>

      <!-- Stats row -->
      <div style="display:flex;margin-top:1rem;padding-top:1rem;border-top:1px solid var(--border);">
        ${stat(stats.total_posts,       'Posts')}
        ${stat(stats.connections_count, 'Connections')}
        ${stat(data.reputation,         'Reputation')}
        ${stat(stats.total_helpful,     'Helpful')}
      </div>

      <!-- Learning goals -->
      ${goals.length ? `
        <div style="display:flex;gap:0.35rem;flex-wrap:wrap;align-items:center;margin-top:0.75rem;">
          <span style="font-size:0.68rem;font-weight:700;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.04em;">Goals:</span>
          ${goals.map(g => `<span class="p-chip p-chip--accent">${g}</span>`).join('')}
        </div>` : ''}
    </div>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// PROFILE POST CARD
// NOTE: data-action="open-comments" on the title reuses the existing
// UNIFIED_ACTIONS['open-comments'] handler — no new handler needed.
// ─────────────────────────────────────────────────────────────────────────────

export function buildProfilePostCard(post) {
  const meta      = getTypeMeta(post.post_type);
  const tags      = (post.tags || []).slice(0, 3);
  const length    = post.resources?.length || 0;
  const resources = post.resources || [];

  // ── Resources carousel (mirrors feed's buildPostResourcesContainer signature) ──
  const resourcesHTML = length > 0
    ? buildPostResourcesContainer(resources, post.id)
    : '';

  // ── Download-links toggle (mirrors feed) ──
  const resourceLinksHTML = length > 0
    ? buildResourceLinks(resources)
    : '';

  // ── Tags ──
  const tagsHTML = tags.length
    ? `<div class="post-tags" style="margin-top:1rem;">
         ${tags.map(t => `<span class="tag p-chip" data-action="view-tag-posts" data-tag="${t}" style="cursor:pointer;">#${t}</span>`).join('')}
       </div>`
    : '';

  return `
    <div data-resource-length="${length}"
         data-resources='${JSON.stringify(resources).replace(/'/g, "&apos;").replace(/"/g, "&quot;")}'
         id="post-${post.id}"
         data-post-id="${post.id}"
         class="profile-post-card post-card"
         style="background:var(--bg-primary);border:1px solid var(--border);border-radius:12px;
                padding:1.5rem;margin-bottom:1rem;position:relative;">

      <!-- ── Post header: avatar + author info + pin button (mirrors feed layout) ── -->
      <div class="post-header" style="display:flex;align-items:flex-start;gap:1rem;">
        <img data-action="view-avatar"
             data-username="${post.author?.username || ''}"
             src="${post.author?.avatar || '/static/default-avatar.png'}"
             class="avatar"
             onerror="this.src='/static/default-avatar.png'"
             style="width:48px;height:48px;border-radius:50%;cursor:pointer;">

        <div class="post-author" style="flex:1;">
          <div class="post-author-info" style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.25rem;">
            <div data-action="view-profile"
                 data-username="${post.author?.username || ''}"
                 class="post-author-name"
                 style="font-weight:600;cursor:pointer;">
              ${post.author?.name || 'Anonymous'}
            </div>
          </div>
          <div class="post-time" style="font-size:0.875rem;color:var(--text-secondary);">${formatTime(post.posted_at)}</div>

          ${post.is_solved || post.is_pinned ? `
            <div class="post-header-badges" style="display:flex;gap:0.5rem;margin-top:0.5rem;">
              ${post.is_solved ? `<span class="solved-badge" style="padding:0.25rem 0.5rem;background:var(--success);color:#fff;border-radius:4px;font-size:0.75rem;">✓ Solved</span>` : ''}
              ${post.is_pinned ? `<span style="padding:0.25rem 0.5rem;background:var(--warning);color:#fff;border-radius:4px;font-size:0.75rem;">📌 Pinned</span>` : ''}
            </div>` : ''}
        </div>

        <button data-action="toggle-post-options"
                class="post-options-btn"
                id="options-btn-${post.id}"
                style="padding:0.5rem;background:var(--bg-tertiary);border:none;border-radius:4px;cursor:pointer;font-size:1.25rem;position:relative;">
          ⋯
        </button>
      </div>

      <!-- ── Post-type indicator (mirrors feed — rendered below header) ── -->
      <div class="post-type-indicator" style="display:flex;align-items:center;gap:0.5rem;margin-top:1rem;">
        <span style="display:flex;align-items:center;">${meta.icon}</span>
        <span class="post-type-label"
              style="text-transform:capitalize;font-size:0.875rem;color:var(--text-secondary);">
          ${meta.label}
        </span>
      </div>

      <!-- ── Title (mirrors feed — plain div, opens comments on click) ── -->
      ${post.title
        ? `<div class="post-title"
                data-action="open-comments"
                data-post-id="${post.id}"
                style="font-size:1.25rem;font-weight:600;margin-top:1rem;cursor:pointer;
                       line-height:1.4;word-break:break-word;">
             ${post.title}
           </div>`
        : ''}

      <!-- ── Body ── -->
      <div class="post-content" style="margin-top:0.75rem;line-height:1.6;">
        ${post.text_content || ''}
      </div>

      <!-- ── Resources carousel ── -->
      ${resourcesHTML}

      <!-- ── Download-links toggle ── -->
      ${length > 0 ? `
        <button class="btn-toggle-details" data-action="view-post-resource-links" data-post-id="${post.id}">
          <span class="download-icon">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
              <polyline points="7 10 12 15 17 10"/>
              <line x1="12" y1="15" x2="12" y2="3"/>
            </svg>
          </span>
          <span class="toggle-text">Show Download Links</span>
          <svg class="toggle-icon" width="16" height="16" viewBox="0 0 24 24"
               fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="6 9 12 15 18 9"/>
          </svg>
        </button>
        <div class="resource-link-modal hidden"
             style="margin-top:0.5rem;padding:1rem;background:var(--bg-secondary);border-radius:8px;">
          ${resourceLinksHTML}
        </div>` : ''}

      <!-- ── Tags ── -->
      ${tagsHTML}

      <!-- ── Stats bar — mirrors feed's .post-stats exactly ── -->
      <div class="post-stats">

        <!-- Reaction / like button -->
        <button class="stat-btn reaction-btn ${post.user_reacted ? 'reacted' : ''}"
                data-action="toggle-reactions"
                data-post-id="${post.id}"
                aria-label="React to post">
          <span class="reaction-icon">
            <svg class="heart-icon" width="18" height="18" viewBox="0 0 24 24"
                 fill="none" stroke="currentColor" stroke-width="2"
                 stroke-linecap="round" stroke-linejoin="round">
              <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
            </svg>
          </span>
          <span class="reaction-count">${post.likes_count > 0 ? post.likes_count : ''}</span>
        </button>

        <!-- Comments button -->
        <button class="stat-btn"
                data-action="open-comments"
                data-post-id="${post.id}">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
          </svg>
          <span>${post.comments_count || 0}</span>
        </button>

        <!-- Share button -->
        <button class="share-btn" data-action="share-post" data-post-id="${post.id}">
          <span class="share-icon">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="18" cy="5" r="3"/>
              <circle cx="6" cy="12" r="3"/>
              <circle cx="18" cy="19" r="3"/>
              <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/>
              <line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/>
            </svg>
          </span>
          <span>Share</span>
        </button>

      </div>
    </div>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// POSTS TAB
// ─────────────────────────────────────────────────────────────────────────────

export function buildPostsTab(posts, activeFilter) {
  const FILTERS = [
    { key: 'all',         label: 'All' },
    { key: 'pinned',      label: '📌 Pinned' },
    { key: 'questions',   label: '❓ Questions' },
    { key: 'resources',   label: '📚 Resources' },
    { key: 'discussions', label: '💬 Discussions' },
  ];

  const pills = FILTERS.map(f => `
    <button data-action="profile-filter-posts" data-filter="${f.key}"
      class="p-filter-pill${f.key === activeFilter ? ' active' : ''}">${f.label}</button>`
  ).join('');

  const list = posts.length
    ? posts.map(buildProfilePostCard).join('')
    : `<div class="p-empty"><div style="font-size:2.2rem;margin-bottom:0.5rem;">📭</div><div>No posts here yet</div></div>`;

  return `
    <div>
      <div class="p-filter-bar">${pills}</div>
      <div id="profile-posts-list" style="position:relative;">${list}</div>
    </div>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// GITHUB-STYLE HEATMAP
// ─────────────────────────────────────────────────────────────────────────────

export function buildHeatmap(heatmapData) {
  if (!heatmapData?.heatmap?.length) {
    return `<div class="p-empty">No activity data yet</div>`;
  }

  const days    = heatmapData.heatmap;
  const summary = heatmapData.summary || {};

  // 5 green levels (0 = none … 4 = max)
  const LEVEL_COLORS = [
    'var(--bg-tertiary)', // 0  — no activity
    '#bbf7d0',            // 1
    '#4ade80',            // 2
    '#16a34a',            // 3
    '#14532d',            // 4
  ];
  const MONTH_NAMES = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const DAY_LABELS  = ['S','M','T','W','T','F','S'];
  const CELL = 12;
  const GAP  = 2;

  // Build grid of weeks
  const grid = [];
  let week = Array(7).fill(null);
  days.forEach((day, i) => {
    const dow = new Date(day.date).getDay();
    week[dow] = day;
    if (dow === 6 || i === days.length - 1) {
      grid.push([...week]);
      week = Array(7).fill(null);
    }
  });

  // Month label positions (first week of each month)
  const monthLabels = [];
  let lastMonth = -1;
  grid.forEach((wk, wi) => {
    const first = wk.find(d => d !== null);
    if (first) {
      const m = new Date(first.date).getMonth();
      if (m !== lastMonth) { monthLabels.push({ wi, label: MONTH_NAMES[m] }); lastMonth = m; }
    }
  });

  const monthRow = grid.map((_, wi) => {
    const found = monthLabels.find(m => m.wi === wi);
    return `<div style="width:${CELL}px;font-size:0.58rem;color:var(--text-secondary);white-space:nowrap;overflow:hidden;">${found ? found.label : ''}</div>`;
  }).join('');

  const columns = grid.map(wk =>
    `<div style="display:flex;flex-direction:column;gap:${GAP}px;">
      ${wk.map(day => {
        if (!day) return `<div style="width:${CELL}px;height:${CELL}px;border-radius:2px;background:var(--bg-tertiary);opacity:0.3;"></div>`;
        const color = LEVEL_COLORS[day.level] || LEVEL_COLORS[0];
        return `<div title="${day.date}: score ${day.score} (${day.posts}p · ${day.comments}c · ${day.helpful}h)"
                     style="width:${CELL}px;height:${CELL}px;border-radius:2px;background:${color};cursor:default;"></div>`;
      }).join('')}
    </div>`
  ).join('');

  const legend = LEVEL_COLORS.map(c =>
    `<div style="width:${CELL}px;height:${CELL}px;border-radius:2px;background:${c};"></div>`
  ).join('');

  return `
    <div>
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:0.6rem;flex-wrap:wrap;gap:0.4rem;">
        <span style="font-size:0.82rem;font-weight:700;color:var(--text-primary);">Activity · last 90 days</span>
        <div style="display:flex;gap:0.85rem;font-size:0.73rem;color:var(--text-secondary);">
          <span>🔥 ${summary.current_streak ?? 0} day streak</span>
          <span>✅ ${summary.active_days ?? 0} active days</span>
        </div>
      </div>
      <!-- Month labels -->
      <div style="display:flex;gap:${GAP}px;margin-bottom:2px;padding-left:${CELL + GAP + 4}px;overflow-x:auto;">
        ${monthRow}
      </div>
      <!-- Day labels + cells -->
      <div style="display:flex;gap:${GAP}px;overflow-x:auto;padding-bottom:0.2rem;">
        <div style="display:flex;flex-direction:column;gap:${GAP}px;margin-right:4px;flex-shrink:0;">
          ${DAY_LABELS.map(l =>
            `<div style="width:${CELL}px;height:${CELL}px;font-size:0.56rem;color:var(--text-secondary);
                         display:flex;align-items:center;justify-content:center;">${l}</div>`
          ).join('')}
        </div>
        ${columns}
      </div>
      <!-- Legend -->
      <div style="display:flex;align-items:center;gap:0.3rem;margin-top:0.45rem;justify-content:flex-end;font-size:0.68rem;color:var(--text-secondary);">
        Less ${legend} More
      </div>
    </div>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// STATS TAB
// ─────────────────────────────────────────────────────────────────────────────

export function buildStatsTab(stats, heatmapData) {
  if (!stats) return `<div class="p-empty">Could not load stats</div>`;

  function card(icon, val, label, sub = '') {
    return `<div class="p-stat-card">
      <div style="font-size:1.2rem;">${icon}</div>
      <div style="font-size:1.25rem;font-weight:700;color:var(--text-primary);margin:0.2rem 0;">${val ?? 0}</div>
      <div style="font-size:0.72rem;font-weight:600;color:var(--text-secondary);">${label}</div>
      ${sub ? `<div style="font-size:0.66rem;color:var(--text-secondary);margin-top:0.1rem;">${sub}</div>` : ''}
    </div>`;
  }

  const e = stats.engagement || {};
  const h = stats.help       || {};
  const t = stats.threads    || {};
  const p = stats.posts      || {};

  return `
    <div style="display:flex;flex-direction:column;gap:1.1rem;">

      <!-- Heatmap -->
      <div class="p-card">${buildHeatmap(heatmapData)}</div>

      <!-- Engagement -->
      <div class="p-section-title">Engagement</div>
      <div class="p-stat-grid">
        ${card('👍', e.reactions_received, 'Reactions Received')}
        ${card('💡', e.helpful_received,   'Marked Helpful')}
        ${card('💬', e.total_comments,     'Comments Made')}
        ${card('✅', e.questions_solved,   'Solved')}
        ${card('🧠', e.questions_answered, 'Answered')}
        ${card('📚', p.resources_shared,   'Resources Shared')}
      </div>

      <!-- Help -->
      <div class="p-section-title">Help Activity</div>
      <div class="p-stat-grid">
        ${card('🤝', h.total_helps_given,   'Helps Given')}
        ${card('🙏', h.total_helps_received,'Helps Received')}
        ${card('🔥', h.streak_current,      'Help Streak', `Longest: ${h.streak_longest ?? 0}`)}
      </div>

      <!-- Threads -->
      <div class="p-section-title">Threads</div>
      <div class="p-stat-grid">
        ${card('🧵', t.joined,  'Joined')}
        ${card('➕', t.created, 'Created')}
      </div>

    </div>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// CONNECTIONS TAB
// ─────────────────────────────────────────────────────────────────────────────

export function buildConnectionsTab(connections) {
  if (!connections || !connections.length) {
    return `<div class="p-empty">
      <div style="font-size:2.2rem;margin-bottom:0.5rem;">🤝</div>
      <div style="font-weight:600;margin-bottom:0.2rem;">No connections yet</div>
      <div style="font-size:0.8rem;color:var(--text-secondary);">Connect with classmates to see them here</div>
    </div>`;
  }

  const cards = connections.map(c => {
    const u = c.user || c;
    return `
      <div class="p-connection-card">
        <div style="position:relative;flex-shrink:0;">
          ${buildAvatar(u.avatar, u.name, 40)}
          <span style="position:absolute;bottom:0;right:0;width:9px;height:9px;border-radius:50%;
                       border:2px solid var(--bg-secondary);background:${u.is_online ? '#22c55e' : '#6b7280'};"></span>
        </div>
        <div style="flex:1;min-width:0;overflow:hidden;">
          <div style="font-weight:600;font-size:0.84rem;color:var(--text-primary);
                      white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${u.name || ''}</div>
          <div style="font-size:0.71rem;color:var(--text-secondary);">@${u.username || ''}</div>
          ${u.department ? `<div style="font-size:0.68rem;color:var(--text-secondary);">🏫 ${u.department}</div>` : ''}
        </div>
        <div style="display:flex;flex-direction:column;align-items:flex-end;gap:0.35rem;flex-shrink:0;">
          <div style="font-size:0.75rem;font-weight:700;color:var(--primary);">⭐ ${u.reputation || 0}</div>
          <div style="font-size:0.65rem;color:var(--text-secondary);">${u.reputation_level || ''}</div>
          <button
            data-action="message-author"
            data-user-id="${u.id}"
            style="display:flex;align-items:center;gap:0.25rem;padding:0.2rem 0.55rem;
                   font-size:0.68rem;font-weight:600;color:var(--primary);
                   background:transparent;border:1.5px solid var(--primary);
                   border-radius:999px;cursor:pointer;transition:background 0.15s,color 0.15s;"
            onmouseover="this.style.background='var(--primary)';this.style.color='#fff';"
            onmouseout="this.style.background='transparent';this.style.color='var(--primary)';">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>
            </svg>
            Message
          </button>
        </div>
      </div>`;
  }).join('');

  return `
    <div>
      <div style="font-size:0.78rem;color:var(--text-secondary);margin-bottom:0.65rem;">
        ${connections.length} connection${connections.length !== 1 ? 's' : ''}
      </div>
      <div style="display:flex;flex-direction:column;gap:0.5rem;">${cards}</div>
    </div>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// REPUTATION TAB
// ─────────────────────────────────────────────────────────────────────────────

export function buildReputationTab(repData, historyData) {
  if (!repData) return `<div class="p-empty">Could not load reputation data</div>`;

  const rep     = repData.reputation;
  const level   = rep.level;
  const next    = rep.next_level;
  const rank    = rep.rank;
  const history = historyData?.history || [];
  const summary = historyData?.summary || {};
  const progress = next ? (next.progress_percentage || 0) : 100;

  const ACTION_LABELS = {
    post_created   : 'Created a post',
    comment_created: 'Added a comment',
    post_liked     : 'Post received a like',
    post_helpful   : 'Post marked helpful',
    comment_helpful: 'Comment marked helpful',
    question_solved: 'Solved a question',
    streak_bonus   : 'Login streak bonus',
    helped_user    : 'Helped a user',
    first_post     : 'First post bonus',
  };

  const historyRows = history.slice(0, 30).map(r => {
    const isPos = r.points_change > 0;
    return `
      <div style="display:flex;align-items:center;gap:0.65rem;padding:0.55rem 0;border-bottom:1px solid var(--border);">
        <span style="font-size:0.8rem;font-weight:700;color:${isPos ? 'var(--success)' : 'var(--danger)'};
                     min-width:38px;text-align:right;">${isPos ? '+' : ''}${r.points_change}</span>
        <div style="flex:1;min-width:0;">
          <div style="font-size:0.78rem;color:var(--text-primary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
            ${ACTION_LABELS[r.action] || r.action}
          </div>
          <div style="font-size:0.68rem;color:var(--text-secondary);">
            ${new Date(r.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
          </div>
        </div>
        <span style="font-size:0.68rem;color:var(--text-secondary);flex-shrink:0;">→ ${r.reputation_after}</span>
      </div>`;
  }).join('');

  return `
    <div style="display:flex;flex-direction:column;gap:1.1rem;">

      <!-- Level card -->
      <div class="p-card">
        <div style="display:flex;align-items:center;gap:0.8rem;margin-bottom:0.9rem;">
          <div style="font-size:2rem;">${level.icon || '⭐'}</div>
          <div style="flex:1;">
            <div style="font-size:0.98rem;font-weight:700;color:var(--text-primary);">${level.name}</div>
            <div style="font-size:0.75rem;color:var(--text-secondary);">${rep.points} pts</div>
          </div>
          <div style="text-align:right;">
            <div style="font-size:0.7rem;color:var(--text-secondary);">Global Rank</div>
            <div style="font-size:1rem;font-weight:700;color:var(--primary);">#${rank.global}</div>
            <div style="font-size:0.65rem;color:var(--text-secondary);">Top ${100 - Math.floor(rank.percentile)}%</div>
          </div>
        </div>
        <!-- Batch 2: entry point for the new "How reputation works" modal
             — GET /reputation/levels already existed with nothing in the
             UI surfacing it, so there was no way to see the full level
             table or what actions earn points without already knowing. -->
        <button data-action="show-reputation-levels"
                style="margin-bottom:0.7rem;background:none;border:none;padding:0;cursor:pointer;
                       font-size:0.72rem;color:var(--primary);font-weight:600;">
          How does reputation work? →
        </button>
        ${next ? `
          <div style="font-size:0.72rem;color:var(--text-secondary);display:flex;justify-content:space-between;margin-bottom:0.3rem;">
            <span>Progress to ${next.icon || ''} ${next.name}</span>
            <span>${next.points_needed} pts to go</span>
          </div>
          <div style="background:var(--bg-tertiary);border-radius:99px;height:6px;overflow:hidden;">
            <div style="height:100%;width:${progress}%;background:var(--primary);border-radius:99px;transition:width 0.5s ease;"></div>
          </div>
        ` : `<div style="font-size:0.82rem;color:var(--success);font-weight:600;">🎉 Max level reached!</div>`}
      </div>

      <!-- Summary boxes -->
      ${summary.total_gained !== undefined ? `
        <div class="p-stat-grid" style="grid-template-columns:repeat(3,1fr);">
          <div class="p-stat-card">
            <div style="font-size:1rem;font-weight:700;color:var(--success);">+${summary.total_gained || 0}</div>
            <div style="font-size:0.68rem;color:var(--text-secondary);">Total Gained</div>
          </div>
          <div class="p-stat-card">
            <div style="font-size:1rem;font-weight:700;color:var(--danger);">-${summary.total_lost || 0}</div>
            <div style="font-size:0.68rem;color:var(--text-secondary);">Total Lost</div>
          </div>
          <div class="p-stat-card">
            <div style="font-size:1rem;font-weight:700;color:var(--primary);">${summary.net_change || 0}</div>
            <div style="font-size:0.68rem;color:var(--text-secondary);">Net</div>
          </div>
        </div>` : ''}

      <!-- History -->
      <div class="p-card">
        <div style="font-size:0.82rem;font-weight:700;color:var(--text-primary);margin-bottom:0.5rem;">Recent History</div>
        ${historyRows || `<div class="p-empty">No history yet</div>`}
      </div>
    </div>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// REPUTATION LEVELS MODAL (Batch 2)
// ─────────────────────────────────────────────────────────────────────────────

export function buildReputationLevelsModal(data) {
  if (!data) return `<div class="p-empty">Could not load reputation info</div>`;

  const levels  = data.levels  || [];
  const actions = data.actions || [];

  const levelRows = levels.map(l => `
    <div style="display:flex;align-items:center;gap:0.7rem;padding:0.6rem 0;border-bottom:1px solid var(--border);">
      <div style="font-size:1.4rem;">${l.icon || '⭐'}</div>
      <div style="flex:1;">
        <div style="font-size:0.85rem;font-weight:700;color:${l.color || 'var(--text-primary)'};">${l.name}</div>
        <div style="font-size:0.7rem;color:var(--text-secondary);">
          ${l.min}${l.max >= 999999 ? '+' : ` – ${l.max}`} points
        </div>
      </div>
    </div>`).join('');

  const actionRows = actions
    .slice()
    .sort((a, b) => b.points - a.points)
    .map(a => {
      const isPos = a.points > 0;
      return `
      <div style="display:flex;align-items:center;gap:0.65rem;padding:0.5rem 0;border-bottom:1px solid var(--border);">
        <span style="font-size:0.78rem;font-weight:700;color:${isPos ? 'var(--success)' : 'var(--danger)'};
                     min-width:38px;text-align:right;">${isPos ? '+' : ''}${a.points}</span>
        <div style="flex:1;font-size:0.78rem;color:var(--text-primary);">${a.description}</div>
      </div>`;
    }).join('');

  return `
    <div style="display:flex;flex-direction:column;gap:1.1rem;max-height:70vh;overflow-y:auto;">
      <div class="p-card">
        <div style="font-size:0.82rem;font-weight:700;color:var(--text-primary);margin-bottom:0.3rem;">Levels</div>
        ${levelRows || `<div class="p-empty">No levels defined</div>`}
      </div>
      <div class="p-card">
        <div style="font-size:0.82rem;font-weight:700;color:var(--text-primary);margin-bottom:0.3rem;">How you earn (and lose) points</div>
        ${actionRows || `<div class="p-empty">No actions defined</div>`}
      </div>
    </div>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// GOAL LIST (used inside Edit Modal)
// ─────────────────────────────────────────────────────────────────────────────

export function buildGoalsList(goals) {
  if (!goals || !goals.length) {
    return `<div style="font-size:0.78rem;color:var(--text-secondary);padding:0.25rem 0;">No goals yet</div>`;
  }
  return goals.map((g, i) => `
    <div style="display:flex;align-items:center;gap:0.5rem;padding:0.3rem 0;border-bottom:1px solid var(--border);">
      <span style="flex:1;font-size:0.82rem;color:var(--text-primary);">${g}</span>
      <button data-action="profile-remove-goal" data-index="${i}"
        style="padding:0.15rem 0.45rem;background:rgba(239,68,68,0.1);border:none;
               border-radius:6px;cursor:pointer;font-size:0.72rem;color:#ef4444;flex-shrink:0;">✕</button>
    </div>`
  ).join('');
}

// Batch 3: same pattern as buildGoalsList above, rendered as wrapping
// chips rather than a vertical list, since skills (unlike goals) are
// short tags more than sentences.
export function buildSkillsList(skills) {
  if (!skills || !skills.length) {
    return `<div style="font-size:0.78rem;color:var(--text-secondary);padding:0.25rem 0;">No skills added yet</div>`;
  }
  return `<div style="display:flex;flex-wrap:wrap;gap:0.4rem;">` +
    skills.map(s => `
      <span style="display:inline-flex;align-items:center;gap:0.35rem;padding:0.25rem 0.5rem;
                   background:var(--bg-tertiary);border:1px solid var(--border);border-radius:999px;
                   font-size:0.78rem;color:var(--text-primary);">
        ${s}
        <button data-action="profile-remove-skill" data-skill="${s.replace(/"/g, '&quot;')}"
          style="padding:0;background:none;border:none;cursor:pointer;font-size:0.7rem;
                 color:var(--text-secondary);line-height:1;">✕</button>
      </span>`
    ).join('') +
    `</div>`;
}

// Batch 3: study-schedule grid, one row per day, one toggle chip per time
// slot. Built by JS instead of 28 hardcoded checkboxes in feed2.html (the
// way templates/onboard.html does it) — see populateScheduleModal() in
// profile.delegation.js for how selections are read back out of this.
const SCHEDULE_DAYS  = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];
const SCHEDULE_SLOTS = ['morning', 'afternoon', 'evening', 'night'];

export function buildScheduleGrid(schedule) {
  schedule = schedule || {};
  return SCHEDULE_DAYS.map(day => {
    const selected = new Set(schedule[day] || []);
    return `
      <div style="display:flex;align-items:center;gap:0.5rem;padding:0.4rem 0;border-bottom:1px solid var(--border);">
        <span style="width:72px;flex-shrink:0;font-size:0.78rem;font-weight:600;
                     color:var(--text-primary);text-transform:capitalize;">${day}</span>
        <div style="display:flex;flex-wrap:wrap;gap:0.35rem;flex:1;">
          ${SCHEDULE_SLOTS.map(slot => `
            <button type="button" data-action="profile-toggle-schedule-slot"
                    data-day="${day}" data-slot="${slot}"
                    class="${selected.has(slot) ? 'active' : ''}"
                    style="padding:0.3rem 0.6rem;border-radius:999px;font-size:0.72rem;
                           cursor:pointer;text-transform:capitalize;
                           border:1px solid ${selected.has(slot) ? 'var(--accent,#0057FF)' : 'var(--border)'};
                           background:${selected.has(slot) ? 'var(--accent,#0057FF)' : 'transparent'};
                           color:${selected.has(slot) ? '#fff' : 'var(--text-secondary)'};">
              ${slot}
            </button>`).join('')}
        </div>
      </div>`;
  }).join('');
}

// ─────────────────────────────────────────────────────────────────────────────
// BATCH 3: Badges tab
// ─────────────────────────────────────────────────────────────────────────────
// Backs GET /badges/my-badges, /badges/progress and /badges/available,
// all of which already existed with no tab in the UI calling any of
// them — the only existing badges feature was the unrelated "top
// earners" sidebar widget.

const RARITY_COLORS = {
  common:    '#9ca3af',
  uncommon:  '#22c55e',
  rare:      '#3b82f6',
  epic:      '#a855f7',
  legendary: '#f59e0b',
};

function _rarityColor(rarity) {
  return RARITY_COLORS[(rarity || '').toLowerCase()] || 'var(--text-secondary)';
}

export function buildBadgesTab(myBadges, progress) {
  if (!myBadges) return `<div class="p-empty">Could not load badges</div>`;

  const earned = myBadges.badges || [];
  const featured = myBadges.featured || [];
  const progressList = progress?.progress || [];

  const earnedGrid = earned.length ? `
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(90px,1fr));gap:0.6rem;">
      ${earned.map(b => `
        <button data-action="profile-open-badge-details" data-badge-id="${b.id}"
                style="display:flex;flex-direction:column;align-items:center;gap:0.3rem;
                       padding:0.6rem 0.3rem;background:var(--p-card-bg,var(--bg-secondary));
                       border:1px solid ${b.is_featured ? _rarityColor(b.rarity) : 'var(--border)'};
                       border-radius:10px;cursor:pointer;font-family:inherit;">
          <div style="font-size:1.6rem;">${b.icon || '🏅'}</div>
          <div style="font-size:0.68rem;font-weight:600;color:var(--text-primary);text-align:center;
                      line-height:1.2;">${b.name}</div>
          ${b.is_featured ? `<div style="font-size:0.6rem;color:${_rarityColor(b.rarity)};">★ Featured</div>` : ''}
        </button>
      `).join('')}
    </div>` : `<div class="p-empty">No badges earned yet — keep participating!</div>`;

  const progressRows = progressList.length ? progressList.slice(0, 8).map(p => `
    <div data-action="profile-open-badge-details" data-badge-id="${p.badge.id}"
         style="padding:0.5rem 0;border-bottom:1px solid var(--border);cursor:pointer;">
      <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.3rem;">
        <span style="font-size:1.1rem;opacity:0.5;">${p.badge.icon || '🏅'}</span>
        <span style="flex:1;font-size:0.82rem;font-weight:600;color:var(--text-primary);">${p.badge.name}</span>
        <span style="font-size:0.72rem;color:var(--text-secondary);">
          ${p.progress.message || `${p.progress.current}/${p.progress.required} ${p.progress.type}`}
        </span>
      </div>
      <div style="height:5px;border-radius:3px;background:var(--border);overflow:hidden;">
        <div style="height:100%;width:${Math.min(100, p.progress.percentage || 0)}%;
                    background:${_rarityColor(p.badge.rarity)};"></div>
      </div>
    </div>
  `).join('') : `<div class="p-empty">No badges in progress</div>`;

  return `
    <div style="display:flex;flex-direction:column;gap:1.1rem;">
      <div class="p-card">
        <div style="font-size:0.82rem;font-weight:700;color:var(--text-primary);margin-bottom:0.6rem;">
          Earned (${earned.length}) ${featured.length ? `— ${featured.length}/3 featured` : ''}
        </div>
        ${earnedGrid}
      </div>
      <div class="p-card">
        <div style="font-size:0.82rem;font-weight:700;color:var(--text-primary);margin-bottom:0.3rem;">
          In Progress
        </div>
        ${progressRows}
      </div>
      <button data-action="profile-browse-all-badges"
              style="background:none;border:none;padding:0;cursor:pointer;
                     font-size:0.78rem;color:var(--primary);font-weight:600;text-align:left;">
        Browse all badges →
      </button>
    </div>`;
}

export function buildBadgeDetailsModal(data) {
  if (!data) return `<div class="p-empty">Could not load badge details</div>`;
  const b = data.badge;
  const color = _rarityColor(b.rarity);

  return `
    <div style="display:flex;flex-direction:column;gap:0.9rem;">
      <div style="text-align:center;">
        <div style="font-size:3rem;">${b.icon || '🏅'}</div>
        <div style="font-size:1.05rem;font-weight:700;color:var(--text-primary);margin-top:0.3rem;">${b.name}</div>
        <div style="font-size:0.78rem;color:${color};text-transform:capitalize;font-weight:600;">${b.rarity}</div>
        <div style="font-size:0.8rem;color:var(--text-secondary);margin-top:0.4rem;">${b.description}</div>
      </div>

      ${data.has_earned ? `
        <div class="p-card" style="text-align:center;">
          <div style="font-size:0.78rem;color:var(--text-secondary);">
            Earned ${data.earned_at ? new Date(data.earned_at).toLocaleDateString() : ''}
          </div>
          <button data-action="profile-toggle-feature-badge" data-badge-id="${b.id}"
                  style="margin-top:0.5rem;padding:0.4rem 1rem;border-radius:999px;border:1px solid ${color};
                         background:transparent;color:${color};cursor:pointer;font-size:0.78rem;font-weight:600;">
            ${data.is_featured ? '★ Unfeature' : '☆ Feature on profile'}
          </button>
        </div>
      ` : data.progress ? `
        <div class="p-card">
          <div style="font-size:0.78rem;color:var(--text-secondary);margin-bottom:0.3rem;">
            ${data.progress.message || `${data.progress.current} / ${data.progress.required} ${data.progress.type}`}
          </div>
          <div style="height:6px;border-radius:3px;background:var(--border);overflow:hidden;">
            <div style="height:100%;width:${Math.min(100, data.progress.percentage || 0)}%;background:${color};"></div>
          </div>
        </div>
      ` : ''}

      ${data.recent_earners && data.recent_earners.length ? `
        <div class="p-card">
          <div style="font-size:0.78rem;font-weight:700;margin-bottom:0.4rem;">Recently earned by</div>
          <div style="display:flex;gap:0.4rem;flex-wrap:wrap;">
            ${data.recent_earners.map(e => `
              <img src="${e.avatar || '/static/default-avatar.png'}" alt="${e.name}" title="${e.name}"
                   style="width:28px;height:28px;border-radius:50%;object-fit:cover;">
            `).join('')}
          </div>
        </div>
      ` : ''}
      <div style="font-size:0.7rem;color:var(--text-secondary);text-align:center;">
        Awarded to ${b.awarded_count} student(s) total
      </div>
    </div>`;
}

export function buildAllBadgesModal(data) {
  if (!data) return `<div class="p-empty">Could not load badges</div>`;
  const badges = data.badges || [];
  return `
    <div style="display:flex;flex-direction:column;gap:0.4rem;max-height:65vh;overflow-y:auto;">
      ${badges.map(b => `
        <button data-action="profile-open-badge-details" data-badge-id="${b.id}"
                style="display:flex;align-items:center;gap:0.6rem;padding:0.5rem;
                       background:none;border:none;border-bottom:1px solid var(--border);
                       cursor:pointer;text-align:left;font-family:inherit;
                       opacity:${b.has_earned ? '1' : '0.5'};">
          <span style="font-size:1.4rem;">${b.icon || '🏅'}</span>
          <div style="flex:1;">
            <div style="font-size:0.84rem;font-weight:600;color:var(--text-primary);">${b.name}</div>
            <div style="font-size:0.72rem;color:var(--text-secondary);">${b.description}</div>
          </div>
          ${b.has_earned ? '<span style="font-size:0.9rem;">✅</span>' : ''}
        </button>
      `).join('')}
    </div>`;
}
