// Notification Utility Functions

/**
 * Returns `link` only if it is a same-origin, app-relative path ("/foo?x=1#y");
 * otherwise ''. Rejects absolute and protocol-relative URLs, "javascript:" and
 * other schemes, backslash tricks and control characters. Notification links
 * are server-generated, but they are stored data: anything that ends up in an
 * href/location must be re-validated on the client as well (audit NOTIF-01).
 */
export function safeNotificationLink(link) {
  if (typeof link !== 'string') return '';
  const value = link.trim();
  if (!value.startsWith('/') || value.startsWith('//')) return '';
  // eslint-disable-next-line no-control-regex
  if (/[\\\u0000-\u001f\u007f\s]/.test(value)) return '';
  return value;
}

export function formatHudTime(isoDate) {
  const date = new Date(isoDate);
  const now = new Date();
  const diffMs = now - date;
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return "Just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit"
  });
}

// Every unread badge is marked with data-notification-badge. The SPA nav item
// had no badge element at all and the code looked for one hard-coded id that
// existed only in an unused template, so the badge never showed (audit
// NOTIF-03). The current count is held here so +1 / -1 never has to be parsed
// back out of the DOM.
let badgeCount = 0;

export function getBadgeCount() {
  return badgeCount;
}

export function updateBadgeCount(count) {
  badgeCount = Math.max(0, Number(count) || 0);
  if (typeof document === 'undefined') return;
  document.querySelectorAll('[data-notification-badge]').forEach((badge) => {
    badge.textContent = badgeCount > 0 ? String(badgeCount) : '';
    badge.style.display = badgeCount > 0 ? 'flex' : 'none';
  });
}

export function adjustBadgeCount(delta) {
  updateBadgeCount(badgeCount + delta);
}

export function showEmptyNotifications() {
  return `
    <div style="text-align: center; padding: 3rem 1.5rem; color: var(--text-secondary);">
      <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="margin: 0 auto 1rem; opacity: 0.3;">
        <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"></path>
        <path d="M13.73 21a2 2 0 0 1-3.46 0"></path>
      </svg>
      <h3 style="margin-bottom: 0.5rem;">No notifications yet</h3>
      <p>When you get notifications, they'll show up here</p>
    </div>
  `;
}

export function getLoadingSkeletonState() {
  return `
    <div class="notification-skeleton">
      ${Array(3).fill(0).map(() => `
        <div class="skeleton-notification" style="padding: 1rem; border-bottom: 1px solid var(--border);">
          <div class="skeleton-line" style="width: 70%; height: 16px; background: var(--bg-secondary); border-radius: 4px; margin-bottom: 0.5rem;"></div>
          <div class="skeleton-line" style="width: 90%; height: 14px; background: var(--bg-secondary); border-radius: 4px; margin-bottom: 0.5rem;"></div>
          <div class="skeleton-line" style="width: 40%; height: 12px; background: var(--bg-secondary); border-radius: 4px;"></div>
        </div>
      `).join('')}
    </div>
  `;
}