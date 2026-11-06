// ============================================================================
// CONNECTION UTILITIES
// ============================================================================

import { REPUTATION_ICONS, HEALTH_COLORS } from './connection.constants.js';

export function getReputationIcon(level) {
  return REPUTATION_ICONS[level] || '⭐';
}

export function getHealthColor(score) {
  if (score >= 70) return HEALTH_COLORS.HIGH;
  if (score >= 40) return HEALTH_COLORS.MEDIUM;
  return HEALTH_COLORS.LOW;
}

export function formatTimeAgo(dateString) {
  if (!dateString) return 'Never';
  
  const date = new Date(dateString);
  const now = new Date();
  const seconds = Math.floor((now - date) / 1000);

  if (seconds < 60) return 'Just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export function getLoadingSkeleton(count = 3) {
  return Array(count).fill(0).map(() => `
    <div class="skeleton-card">
      <div class="skeleton-header">
        <div class="skeleton-avatar"></div>
        <div class="skeleton-text"></div>
      </div>
      <div class="skeleton-content"></div>
    </div>
  `).join('');
}

export function showEmptyState(type) {
  const messages = {
    connected: 'No connections yet',
    received: 'No connection requests',
    sent: 'No sent requests',
    suggestions: 'No suggestions available',
    online: 'No online connections',
    discovery: 'No users to discover'
  };

  return `
    <div class="empty-state">
      <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>
        <circle cx="8.5" cy="7" r="4"></circle>
        <line x1="20" y1="8" x2="20" y2="14"></line>
        <line x1="23" y1="11" x2="17" y2="11"></line>
      </svg>
      <p>${messages[type] || 'No data available'}</p>
    </div>
  `;
}
