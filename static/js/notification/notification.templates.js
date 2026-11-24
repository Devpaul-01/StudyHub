import { formatHudTime, safeNotificationLink } from './notification.utils.js';
import { escapeHtml } from '../core/escape.js';

/**
 * Builds one notification card.
 *
 * title/body contain other users' display names and post/thread titles, so every
 * server-provided value is escaped here (audit NOTIF-01). The id is coerced to a
 * number and the link is restricted to same-origin paths before use.
 */
export function createNotificationCard(notification) {
  const id = Number(notification.id) || 0;
  const link = safeNotificationLink(notification.link);

  return `
    <div data-action="view-notification"
         data-link="${escapeHtml(link)}"
         class="hud-notification ${notification.is_read ? 'read' : ''}"
         data-notification-id="${id}">

      <button class="cancel-btn"
              data-action="delete-notification"
              data-notification-id="${id}">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <line x1="18" y1="6" x2="6" y2="18"></line>
          <line x1="6" y1="6" x2="18" y2="18"></line>
        </svg>
      </button>

      <div class="hud-title">${escapeHtml(notification.title)}</div>
      <div class="hud-body">${escapeHtml(notification.body)}</div>

      <div class="hud-time">
        ${formatHudTime(notification.created_at)}
      </div>
    </div>
  `;
}
