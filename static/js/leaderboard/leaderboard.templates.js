/**
 * ============================================================================
 * LEADERBOARD TEMPLATES  —  leaderboard.templates.js
 * Pure functions that return HTML strings.
 * All data comes from backend response objects — no invented fields.
 * ============================================================================
 */

import {
  formatScore, getRankChangeHTML, getStreakBadge,
  getAvatarHTML, getRankBadgeHTML, escapeHtml,
  truncate, getSkeletonList, formatPercentile, getLevelColor,
} from './leaderboard.utils.js';


// ─────────────────────────────────────────────────────────────────────────────
// MESSAGE BUTTON  (shared across any template that displays another user)
// Returns '' for the current user (is_you) — you can't message yourself.
// Wire up by listening for clicks on [data-action="lb-message-user"] and
// reading data-user-id off the button (event delegation recommended).
// ─────────────────────────────────────────────────────────────────────────────

function getMessageBtnHTML(user, isYou, extraClass = '') {
  if (isYou || !user) return '';

  const label = escapeHtml(user.name || user.username || 'this user');

  return `
<button class="lb-message-btn ${extraClass}" type="button" data-action="message-author" data-user-id="${user.id}" aria-label="Message ${label}" title="Message ${label}">
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
  </svg>
</button>`;
}


// ─────────────────────────────────────────────────────────────────────────────
// MY RANK SUMMARY STRIP  (always pinned at top of leaderboard section)
// Source: /leaderboard/me
// ─────────────────────────────────────────────────────────────────────────────

export function createMyRankStrip(data) {
  if (!data) return createMyRankStripSkeleton();

  const { rank, score, level, streaks, stats } = data;
  const rankChange   = getRankChangeHTML(rank.change);
  const streakBadge  = getStreakBadge(streaks.login_streak);
  const helpBadge    = getStreakBadge(streaks.help_streak_current);
  const lvlColor     = getLevelColor(level.current.name);
  const pct          = formatPercentile(rank.percentile);

  // Progress bar width (capped at 100%)
  const progressW    = Math.min(level.progress_pct || 0, 100);

  return `
<div class="lb-my-strip" id="lb-my-strip">
  <!-- Rank & movement -->
  <div class="lb-my-rank-block">
    <div class="lb-my-rank-num">#${rank.global}</div>
    <div class="lb-my-rank-meta">
      ${rankChange}
      <span class="lb-my-percentile">${pct}</span>
    </div>
    ${rank.department ? `<div class="lb-my-dept-rank">Dept #${rank.department}</div>` : ''}
  </div>

  <!-- Score info -->
  <div class="lb-my-score-block">
    <div class="lb-my-score-val">${formatScore(score.period_score)}</div>
    <div class="lb-my-score-label">pts this period</div>
    <div class="lb-my-level" style="color:${lvlColor}">
      <span>${level.current.icon}</span> ${escapeHtml(level.current.name)}
    </div>
  </div>

  <!-- Streaks & level progress -->
  <div class="lb-my-meta-block">
    <div class="lb-my-streaks">
      ${streakBadge}
      ${helpBadge}
    </div>
    <div class="lb-my-progress-wrap" title="Level progress: ${progressW}%">
      <div class="lb-my-progress-bar" style="width:${progressW}%;background:${lvlColor}"></div>
    </div>
    ${level.points_to_next > 0
      ? `<div class="lb-my-pts-next">${level.points_to_next} pts to next level</div>`
      : '<div class="lb-my-pts-next">Max level 👑</div>'}
  </div>

  <!-- Breakdown toggle -->
  <button class="lb-breakdown-btn" data-action="lb-toggle-breakdown" title="See how your score is built">
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
      <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
    </svg>
    Breakdown
  </button>
</div>`;
}

export function createMyRankStripSkeleton() {
  return `
<div class="lb-my-strip lb-my-strip-loading">
  <div class="lb-skel" style="width:60px;height:36px;border-radius:8px;"></div>
  <div class="lb-skel-info" style="flex:1;gap:6px;display:flex;flex-direction:column;">
    <div class="lb-skel" style="width:80px;height:14px;border-radius:4px;"></div>
    <div class="lb-skel" style="width:120px;height:10px;border-radius:4px;"></div>
  </div>
</div>`;
}


// ─────────────────────────────────────────────────────────────────────────────
// TOP-3 PODIUM
// ─────────────────────────────────────────────────────────────────────────────

/** How many leading entries the podium takes over (0 when there are fewer than 3). */
export function podiumSize(entries) {
  return entries && entries.length >= 3 ? 3 : 0;
}

export function createPodium(entries) {
  if (!podiumSize(entries)) return '';

  const [first, second, third] = entries;

  function podiumCard(entry, position) {
    const medals  = { 1: 'lb-pod-gold', 2: 'lb-pod-silver', 3: 'lb-pod-bronze' };
    const heights = { 1: '110px', 2: '80px', 3: '60px' };
    const orders  = { 1: 'order-2', 2: 'order-1', 3: 'order-3' };
    const lvlColor = getLevelColor(entry.reputation?.level?.name);
    const youClass = entry.is_you ? 'lb-pod-you' : '';

    return `
<div class="lb-pod-item ${orders[position]} ${youClass}">
  <div class="lb-pod-avatar-wrap">
    ${getAvatarHTML(entry.user, 52)}
    <span class="lb-pod-rank-badge ${medals[position]}">${position}</span>
  </div>
  <div class="lb-pod-name">${escapeHtml(truncate(entry.user.name || entry.user.username, 14))}</div>
  <div class="lb-pod-score" style="color:${lvlColor}">${formatScore(entry.score)}</div>
  <div class="lb-pod-level">
    <span>${entry.reputation?.level?.icon || ''}</span>
    ${escapeHtml(entry.reputation?.level?.name || '')}
  </div>
  ${getMessageBtnHTML(entry.user, entry.is_you, 'lb-pod-message-btn')}
  <div class="lb-pod-bar" style="height:${heights[position]};background:var(--lb-podium-${position})"></div>
</div>`;
  }

  return `
<div class="lb-podium" aria-label="Top 3 rankings">
  ${podiumCard(second, 2)}
  ${podiumCard(first, 1)}
  ${podiumCard(third, 3)}
</div>`;
}


// ─────────────────────────────────────────────────────────────────────────────
// LEADERBOARD ENTRY ROW
// Source: _build_entry() in leaderboard.py
// ─────────────────────────────────────────────────────────────────────────────

/**
 * @param {object}  entry
 * @param {{inPodium?: boolean}} [opts] inPodium: this row is already shown on the
 *   podium, so hide it from the list. Decided by position, not rank: with tied
 *   scores several rows can share rank 2, and only the first three are on the podium.
 */
export function createEntryRow(entry, { inPodium = false } = {}) {
  const { rank, rank_change, is_you, user, score, reputation, streaks } = entry;

  const youClass    = is_you ? 'lb-row-you' : '';
  const rankChange  = getRankChangeHTML(rank_change);
  const streakBadge = getStreakBadge(streaks?.login_streak);
  const helpBadge   = getStreakBadge(streaks?.help_streak_current);
  const lvlColor    = getLevelColor(reputation?.level?.name);
  const dept        = user.department ? `<span class="lb-row-dept">${escapeHtml(truncate(user.department, 18))}</span>` : '';

  // Hide rows that the podium already shows
  const hideIfTop3  = inPodium ? 'lb-row-in-podium' : '';

  return `
<div class="lb-row ${youClass} ${hideIfTop3}" data-rank="${rank}" data-user-id="${user.id}">
  <!-- Rank -->
  <div class="lb-row-rank">
    ${getRankBadgeHTML(rank)}
    ${rankChange}
  </div>

  <!-- Avatar -->
  <div class="lb-row-avatar">
    ${getAvatarHTML(user, 38)}
  </div>

  <!-- Identity -->
  <div class="lb-row-identity">
    <div class="lb-row-name">
      ${escapeHtml(truncate(user.name || user.username, 22))}
      ${is_you ? '<span class="lb-you-tag">YOU</span>' : ''}
      <span class="lb-row-level-icon" title="${escapeHtml(reputation?.level?.name || '')}"
            style="color:${lvlColor}">${reputation?.level?.icon || ''}</span>
    </div>
    <div class="lb-row-sub">
      ${dept}
      ${streakBadge}
      ${helpBadge}
    </div>
  </div>

  <!-- Score -->
  <div class="lb-row-score" style="--lvl-color:${lvlColor}">
    ${formatScore(score)}
  </div>

  <!-- Actions -->
  <div class="lb-row-actions">
    ${getMessageBtnHTML(user, is_you, 'lb-row-message-btn')}
  </div>
</div>`;
}


// ─────────────────────────────────────────────────────────────────────────────
// NEARBY SECTION  (psychologically most powerful — "you vs direct rivals")
// Source: /leaderboard/nearby or /leaderboard/me → nearby_users
// ─────────────────────────────────────────────────────────────────────────────

export function createNearbySection(entries, yourRank) {
  if (!entries || entries.length === 0) return '';

  const rows = entries.map(entry => {
    const isYou       = entry.is_you;
    const youClass    = isYou ? 'lb-nearby-you' : '';
    const change      = getRankChangeHTML(entry.rank_change);
    const streakBadge = getStreakBadge(entry.streaks?.login_streak);
    const lvlColor    = getLevelColor(entry.reputation?.level?.name);
    const aboveBelow  = !isYou ? (entry.rank < yourRank ? 'lb-nearby-above' : 'lb-nearby-below') : '';

    return `
<div class="lb-nearby-row ${youClass} ${aboveBelow}">
  ${isYou ? '<div class="lb-nearby-you-marker">YOU</div>' : ''}
  <span class="lb-nearby-rank" style="${isYou ? 'font-weight:700' : ''}">#${entry.rank}</span>
  ${getAvatarHTML(entry.user, 32)}
  <div class="lb-nearby-name">
    ${escapeHtml(truncate(entry.user.name || entry.user.username, 18))}
    ${streakBadge}
  </div>
  <div class="lb-nearby-score" style="color:${lvlColor}">
    ${formatScore(entry.score)}
    ${change}
  </div>
  ${!isYou
    ? `<div class="lb-nearby-gap">
         ${entry.rank < yourRank
           ? `<span class="lb-gap-chase">+${Math.abs(entry.score - (entries.find(e => e.is_you)?.score || 0))} ahead</span>`
           : `<span class="lb-gap-lead">+${Math.abs((entries.find(e => e.is_you)?.score || 0) - entry.score)} behind</span>`
         }
       </div>`
    : ''}
  ${getMessageBtnHTML(entry.user, isYou, 'lb-nearby-message-btn')}
</div>`;
  }).join('');

  return `
<div class="lb-nearby-section" id="lb-nearby-section">
  <div class="lb-nearby-header">
    <span class="lb-nearby-title">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>
        <path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>
      </svg>
      Near You
    </span>
    <span class="lb-nearby-rank-label">Your rank: <strong>#${yourRank}</strong></span>
  </div>
  <div class="lb-nearby-list">${rows}</div>
</div>`;
}


// ─────────────────────────────────────────────────────────────────────────────
// RISING STARS ROW
// Source: /leaderboard/rising
// ─────────────────────────────────────────────────────────────────────────────

export function createRisingRow(entry) {
  const { rank, weekly_gain, is_you, user, reputation, streaks } = entry;
  const youClass   = is_you ? 'lb-row-you' : '';
  const lvlColor   = getLevelColor(reputation?.level?.name);
  const streakBadge = getStreakBadge(streaks?.help_streak_current);
  const dept       = user.department
    ? `<span class="lb-row-dept">${escapeHtml(truncate(user.department, 18))}</span>` : '';

  return `
<div class="lb-row lb-row-rising ${youClass}" data-rank="${rank}" data-user-id="${user.id}">
  <div class="lb-row-rank">
    ${getRankBadgeHTML(rank)}
  </div>
  <div class="lb-row-avatar">${getAvatarHTML(user, 38)}</div>
  <div class="lb-row-identity">
    <div class="lb-row-name">
      ${escapeHtml(truncate(user.name || user.username, 22))}
      ${is_you ? '<span class="lb-you-tag">YOU</span>' : ''}
      <span style="color:${lvlColor}">${reputation?.level?.icon || ''}</span>
    </div>
    <div class="lb-row-sub">${dept} ${streakBadge}</div>
  </div>
  <div class="lb-row-score lb-rising-gain" style="--lvl-color:${lvlColor}">
    <span class="lb-gain-arrow">▲</span>${formatScore(weekly_gain)}
    <span class="lb-gain-label">7d</span>
  </div>

  <!-- Actions -->
  <div class="lb-row-actions">
    ${getMessageBtnHTML(user, is_you, 'lb-row-message-btn')}
  </div>
</div>`;
}


// ─────────────────────────────────────────────────────────────────────────────
// SCORE BREAKDOWN PANEL
// Source: /leaderboard/breakdown
// ─────────────────────────────────────────────────────────────────────────────

export function createBreakdownPanel(data) {
  if (!data) return '';

  const { period, total_period_score, by_action, recent_events, streaks, level, scoring_tips } = data;

  const actionRows = Object.entries(by_action || {}).map(([action, info]) => `
    <div class="lb-bk-action-row">
      <span class="lb-bk-action-name">${escapeHtml(action.replace(/_/g, ' '))}</span>
      <span class="lb-bk-action-count">×${info.count}</span>
      <span class="lb-bk-action-pts ${info.total_points >= 0 ? 'lb-bk-pos' : 'lb-bk-neg'}">
        ${info.total_points >= 0 ? '+' : ''}${info.total_points}
      </span>
    </div>`).join('');

  const recentRows = (recent_events || []).slice(0, 5).map(ev => `
    <div class="lb-bk-event">
      <span class="lb-bk-event-action">${escapeHtml(ev.action.replace(/_/g, ' '))}</span>
      <span class="lb-bk-event-pts ${ev.points_change >= 0 ? 'lb-bk-pos' : 'lb-bk-neg'}">
        ${ev.points_change >= 0 ? '+' : ''}${ev.points_change}
      </span>
    </div>`).join('');

  const tips = (scoring_tips || []).map(tip =>
    `<div class="lb-bk-tip">${escapeHtml(tip)}</div>`).join('');

  return `
<div class="lb-breakdown-panel" id="lb-breakdown-panel" aria-label="Score breakdown">
  <div class="lb-bk-header">
    <span>Score Breakdown · <em>${escapeHtml(period)}</em></span>
    <button class="lb-bk-close" data-action="lb-toggle-breakdown" aria-label="Close breakdown">✕</button>
  </div>

  <div class="lb-bk-total">
    <span class="lb-bk-total-num">${formatScore(total_period_score)}</span>
    <span class="lb-bk-total-label">period score</span>
  </div>

  <!-- Streaks -->
  <div class="lb-bk-streaks">
    <div class="lb-bk-streak-item">
      🔥 <strong>${streaks?.login_streak || 0}</strong>-day login streak
    </div>
    <div class="lb-bk-streak-item">
      ⚡ <strong>${streaks?.help_streak_current || 0}</strong>-day help streak
    </div>
  </div>

  <!-- By action -->
  ${actionRows
    ? `<div class="lb-bk-section-title">By Action</div>
       <div class="lb-bk-actions">${actionRows}</div>`
    : '<div class="lb-bk-empty-note">No activity this period yet.</div>'}

  <!-- Recent events -->
  ${recentRows
    ? `<div class="lb-bk-section-title">Recent Events</div>
       <div class="lb-bk-events">${recentRows}</div>`
    : ''}

  <!-- Tips -->
  ${tips
    ? `<div class="lb-bk-section-title">Earn More Points</div>
       <div class="lb-bk-tips">${tips}</div>`
    : ''}
</div>`;
}


// ─────────────────────────────────────────────────────────────────────────────
// STATS BANNER
// Source: /leaderboard/stats
// ─────────────────────────────────────────────────────────────────────────────

export function createStatsBanner(data) {
  if (!data) return '';

  return `
<div class="lb-stats-banner">
  <div class="lb-stat-item">
    <span class="lb-stat-val">${formatScore(data.total_students)}</span>
    <span class="lb-stat-label">Students</span>
  </div>
  <div class="lb-stat-divider"></div>
  <div class="lb-stat-item">
    <span class="lb-stat-val">${formatScore(data.active_this_week)}</span>
    <span class="lb-stat-label">Active This Week</span>
  </div>
  <div class="lb-stat-divider"></div>
  <div class="lb-stat-item">
    <span class="lb-stat-val">${formatScore(data.week_rep_earned)}</span>
    <span class="lb-stat-label">Points Earned</span>
  </div>
  ${data.top_department
    ? `<div class="lb-stat-divider"></div>
       <div class="lb-stat-item">
         <span class="lb-stat-val">${escapeHtml(truncate(data.top_department.name, 14))}</span>
         <span class="lb-stat-label">Top Dept</span>
       </div>`
    : ''}
</div>`;
}


// ─────────────────────────────────────────────────────────────────────────────
// EMPTY STATE
// ─────────────────────────────────────────────────────────────────────────────

export function createEmptyState(view = 'global', department = '') {
  const messages = {
    global:      { icon: '🏆', title: 'No rankings yet', body: 'Be the first to earn points!' },
    connections: { icon: '👥', title: 'No connections yet', body: 'Connect with classmates to see how you compare.' },
    rising:      { icon: '🚀', title: 'No rising stars yet', body: 'Start helping others to appear here.' },
    me:          { icon: '📊', title: 'No data yet', body: 'Start earning points to see your rank card.' },
  };
  let { icon, title, body } = messages[view] || messages.global;

  // Department-specific empty state — shown when filtering by a department
  // that has no qualifying users for the current period.
  if (department && (view === 'global' || view === 'rising')) {
    icon  = '🏫';
    title = 'No users in this department';
    body  = `No one in ${escapeHtml(department)} has activity for this period yet. Try another department or period.`;
  }

  return `
<div class="lb-empty-state">
  <span class="lb-empty-icon">${icon}</span>
  <h3 class="lb-empty-title">${title}</h3>
  <p class="lb-empty-body">${body}</p>
</div>`;
}


// ─────────────────────────────────────────────────────────────────────────────
// ERROR STATE
// ─────────────────────────────────────────────────────────────────────────────

export function createErrorState(message = 'Failed to load leaderboard.') {
  return `
<div class="lb-error-state">
  <span class="lb-error-icon">⚠️</span>
  <p>${escapeHtml(message)}</p>
  <button class="lb-retry-btn" data-action="lb-retry">Try again</button>
</div>`;
}


// ─────────────────────────────────────────────────────────────────────────────
// SKELETON LOADING LIST
// ─────────────────────────────────────────────────────────────────────────────

export function createSkeletonList(count = 8) {
  return `<div class="lb-list lb-list-loading">${getSkeletonList(count)}</div>`;
}


// ─────────────────────────────────────────────────────────────────────────────
// LOAD MORE BUTTON
// ─────────────────────────────────────────────────────────────────────────────

export function createLoadMoreBtn() {
  return `
<div class="lb-load-more-wrap">
  <button class="lb-load-more-btn" data-action="lb-load-more">
    Load more
  </button>
</div>`;
}


// ─────────────────────────────────────────────────────────────────────────────
// MY RANK FULL VIEW  (view = 'me')
// Source: /leaderboard/me — rich data card
// ─────────────────────────────────────────────────────────────────────────────

export function createMyRankView(data) {
  if (!data) return createEmptyState('me');

  const { period, rank, score, level, streaks, stats, nearby_users, weekly_champion } = data;
  const lvlColor   = getLevelColor(level.current.name);
  const progressW  = Math.min(level.progress_pct || 0, 100);

  const championBanner = weekly_champion
    ? `<div class="lb-champion-banner">
         🏆 Weekly Champion · ${escapeHtml(weekly_champion.subject || weekly_champion.type)}
         · ${weekly_champion.help_count} helps
       </div>`
    : '';

  return `
<div class="lb-me-view">

  ${championBanner}

  <!-- Big rank card -->
  <div class="lb-me-rank-card">
    <div class="lb-me-global-rank">#${rank.global}</div>
    <div class="lb-me-rank-meta">
      <div class="lb-me-pct">${formatPercentile(rank.percentile)} globally</div>
      ${rank.department_name
        ? `<div class="lb-me-dept-rank">Dept rank <strong>#${rank.department}</strong> · ${escapeHtml(rank.department_name)}</div>`
        : ''}
      <div class="lb-me-rank-change">${getRankChangeHTML(rank.change)}</div>
    </div>
  </div>

  <!-- Stats grid -->
  <div class="lb-me-stats-grid">
    <div class="lb-me-stat">
      <span class="lb-me-stat-val">${formatScore(score.period_score)}</span>
      <span class="lb-me-stat-lbl">Period pts</span>
    </div>
    <div class="lb-me-stat">
      <span class="lb-me-stat-val">${formatScore(score.weekly_gain)}</span>
      <span class="lb-me-stat-lbl">This week</span>
    </div>
    <div class="lb-me-stat">
      <span class="lb-me-stat-val">${score.active_days_30d}</span>
      <span class="lb-me-stat-lbl">Active days</span>
    </div>
    <div class="lb-me-stat">
      <span class="lb-me-stat-val">${formatScore(stats.total_helps_given)}</span>
      <span class="lb-me-stat-lbl">Helps given</span>
    </div>
  </div>

  <!-- Level progress -->
  <div class="lb-me-level-row">
    <span class="lb-me-level-badge" style="color:${lvlColor}">
      ${level.current.icon} ${escapeHtml(level.current.name)}
    </span>
    <div class="lb-me-prog-outer">
      <div class="lb-me-prog-inner" style="width:${progressW}%;background:${lvlColor}"></div>
    </div>
    ${level.points_to_next > 0
      ? `<span class="lb-me-level-next">${level.points_to_next} pts to next</span>`
      : '<span class="lb-me-level-next">Max level 👑</span>'}
  </div>

  <!-- Streaks -->
  <div class="lb-me-streaks">
    <div class="lb-me-streak-card">
      <span class="lb-me-streak-icon">🔥</span>
      <span class="lb-me-streak-val">${streaks.login_streak}</span>
      <span class="lb-me-streak-lbl">Login streak</span>
    </div>
    <div class="lb-me-streak-card">
      <span class="lb-me-streak-icon">⚡</span>
      <span class="lb-me-streak-val">${streaks.help_streak_current}</span>
      <span class="lb-me-streak-lbl">Help streak</span>
    </div>
    <div class="lb-me-streak-card">
      <span class="lb-me-streak-icon">📈</span>
      <span class="lb-me-streak-val">${streaks.help_streak_longest}</span>
      <span class="lb-me-streak-lbl">Best streak</span>
    </div>
  </div>

  <!-- Nearby users section -->
  ${nearby_users && nearby_users.length
    ? createNearbySection(nearby_users, rank.global)
    : ''}

  <!-- Breakdown CTA -->
  <button class="lb-me-breakdown-btn" data-action="lb-toggle-breakdown">
    📊 View Score Breakdown
  </button>
</div>`;
}
