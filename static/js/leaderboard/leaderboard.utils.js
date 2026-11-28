/**
 * ============================================================================
 * LEADERBOARD UTILS  —  leaderboard.utils.js
 * Pure, side-effect-free helper functions.
 * Mirrors the notification.utils.js pattern in the existing codebase.
 * ============================================================================
 */

import { escapeHtml } from '../core/escape.js';

/**
 * Format a numeric score for compact display (e.g. 1200 → "1.2k").
 */
export function formatScore(score) {
  if (!score && score !== 0) return '0';
  if (score >= 1000) return (score / 1000).toFixed(1).replace(/\.0$/, '') + 'k';
  return score.toString();
}

/**
 * Render rank-change arrow HTML.
 * rankChange: +N = moved up, -N = moved down, 0 = stable, null = no history.
 */
export function getRankChangeHTML(rankChange) {
  if (rankChange === null || rankChange === undefined) return '';
  if (rankChange > 0) {
    return `<span class="lb-rank-change lb-rank-up" title="Moved up ${rankChange} places">▲${rankChange}</span>`;
  }
  if (rankChange < 0) {
    return `<span class="lb-rank-change lb-rank-down" title="Dropped ${Math.abs(rankChange)} places">▼${Math.abs(rankChange)}</span>`;
  }
  return `<span class="lb-rank-change lb-rank-stable" title="No change">–</span>`;
}

/**
 * Render streak badge HTML. Returns '' for streaks below threshold.
 */
export function getStreakBadge(streak) {
  if (!streak || streak < 3) return '';
  const icon = streak >= 14 ? '🔥' : streak >= 7 ? '⚡' : '✨';
  return `<span class="lb-streak-badge" title="${streak}-day streak">${icon} ${streak}d</span>`;
}

/**
 * Resolve a stored avatar value into a usable URL.
 * Handles full URLs, absolute paths, and bare filenames.
 */
function _resolveAvatarUrl(avatar) {
  return avatar
}

/**
 * Render user avatar — falls back to initials block if no avatar or on error.
 */
export function getAvatarHTML(user, size = 40) {
  const initials  = escapeHtml(((user.name || user.username || '?').charAt(0)).toUpperCase());
  const style     = `width:${size}px;height:${size}px;min-width:${size}px;`;
  const avatarUrl = _resolveAvatarUrl(user.avatar);

  if (avatarUrl) {
    return `<img src="${escapeHtml(avatarUrl)}"
                 alt="${escapeHtml(user.username)}"
                 class="lb-avatar"
                 style="${style}"
                 onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
            <div class="lb-avatar-fallback" style="${style}display:none;">${initials}</div>`;
  }
  return `<div class="lb-avatar-fallback" style="${style}">${initials}</div>`;
}

/**
 * Render rank medal (top 3) or plain rank number.
 */
export function getRankBadgeHTML(rank) {
  if (rank === 1) return '<span class="lb-medal lb-medal-gold" aria-label="1st place">1</span>';
  if (rank === 2) return '<span class="lb-medal lb-medal-silver" aria-label="2nd place">2</span>';
  if (rank === 3) return '<span class="lb-medal lb-medal-bronze" aria-label="3rd place">3</span>';
  return `<span class="lb-rank-num">#${rank}</span>`;
}

/**
 * Map reputation level name to its hex color (matches backend REPUTATION_LEVELS).
 */
export function getLevelColor(levelName) {
  const map = {
    Newbie:      '#6B7280',
    Learner:     '#3B82F6',
    Contributor: '#8B5CF6',
    Expert:      '#F59E0B',
    Master:      '#EF4444',
  };
  return map[levelName] || '#6B7280';
}

/**
 * Escape HTML special chars in any user-generated string.
 */
// One shared implementation (also used by the profile modules); re-exported so
// existing leaderboard imports keep working. It escapes quotes, which matters
// because these values are interpolated into quoted attributes.
export { escapeHtml };

/**
 * Truncate text to max characters, adding ellipsis.
 */
export function truncate(text, max = 22) {
  if (!text) return '';
  return text.length > max ? text.slice(0, max) + '…' : text;
}

/**
 * Render a skeleton loading row.
 */
export function getSkeletonRow() {
  return `
    <div class="lb-skeleton-row">
      <div class="lb-skel lb-skel-rank"></div>
      <div class="lb-skel lb-skel-avatar"></div>
      <div class="lb-skel-info">
        <div class="lb-skel lb-skel-name"></div>
        <div class="lb-skel lb-skel-dept"></div>
      </div>
      <div class="lb-skel lb-skel-score"></div>
    </div>`;
}

/**
 * Render multiple skeleton rows for loading state.
 */
export function getSkeletonList(count = 8) {
  return Array(count).fill(0).map(getSkeletonRow).join('');
}

/**
 * Format a percentile number for display ("Top 5%").
 */
export function formatPercentile(pct) {
  if (pct >= 99) return 'Top 1%';
  if (pct >= 95) return 'Top 5%';
  if (pct >= 90) return 'Top 10%';
  if (pct >= 75) return 'Top 25%';
  if (pct >= 50) return 'Top 50%';
  return `Bottom ${Math.round(100 - pct)}%`;
}
