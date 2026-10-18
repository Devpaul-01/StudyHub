/**
 * ============================================================================
 * NOTIFICATION INITIALIZATION (REFINED)
 * Simplified version without cursor pagination and infinite scroll
 * ============================================================================
 */

import {
  loadNotifications, handleToggleNotification, handleToggleNotificationSound,
  toggleNotificationSettings,
} from './notification.events.js';
import { notificationState } from './notification.state.js';
import { createNotificationCard } from './notification.templates.js';
import { updateBadgeCount } from './notification.utils.js';
import { socket } from '../core/socket.js';

// Pull-to-refresh variables
let startY      = null;
let currentY    = null;
let pulling     = false;
let refreshing  = false;

// ============================================================================
// INITIALIZE NOTIFICATION SYSTEM
// ============================================================================

let isInitialized = false;

function initializeNotifications() {
  // Prevent re-initialization
  if (isInitialized) return;
  isInitialized = true;

  // Load all notifications at once
  loadNotifications();

  // Setup change event listeners (toggles)
  setupNotificationToggles();

  // Setup pull-to-refresh
  setupPullToRefresh();

  // Setup click-outside to close settings
  setupClickOutsideHandler();

  console.log('✅ Notification system initialized');
}

// ============================================================================
// TOGGLE LISTENERS
// ============================================================================

function setupNotificationToggles() {
  const enableNotifToggle = document.getElementById('toggle-notification');
  if (enableNotifToggle) {
    enableNotifToggle.addEventListener('change', (e) => {
      handleToggleNotification(e.target.checked);
    });
  }

  const enableSoundToggle = document.getElementById('toggle-notification-sound');
  if (enableSoundToggle) {
    enableSoundToggle.addEventListener('change', (e) => {
      handleToggleNotificationSound(e.target.checked);
    });
  }
}

// ============================================================================
// CLICK-OUTSIDE HANDLER
// ============================================================================

function setupClickOutsideHandler() {
  /*
  document.addEventListener('click', (e) => {
    const settingsModal = document.getElementById('notification-settings-modal');
    const toggleButton = e.target.closest('[data-action="toggle-notification-options"]');

    if (settingsModal && !settingsModal.contains(e.target) && !toggleButton) {
      settingsModal.classList.add('hidden');
    }
  });
  */
}

// ============================================================================
// PULL-TO-REFRESH
// ============================================================================

function setupPullToRefresh() {
  const indicator = document.getElementById('notification-pull-indicator');
  const container = document.getElementById('notifications-list-container');

  if (!indicator || !container) {
    console.error('Pull-to-refresh elements not found');
    return;
  }

  // ── Shared refresh action ──────────────────────────────────────────────────
  async function triggerRefresh() {
    if (refreshing) return;
    refreshing = true;

    indicator.innerHTML = `
      <svg class="refresh-spinner" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="animation: spin 1s linear infinite;">
        <polyline points="1 4 1 10 7 10"></polyline>
        <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"></path>
      </svg>
      <span>Refreshing...</span>
    `;
    indicator.style.transform = 'translateX(-50%) translateY(10px)';
    indicator.style.opacity   = '1';

    try {
      container.innerHTML = '';
      await loadNotifications();

      indicator.innerHTML = `
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="20 6 9 17 4 12"></polyline>
        </svg>
        <span>Updated!</span>
      `;

      setTimeout(() => _resetIndicator(indicator), 500);
    } catch (error) {
      console.error('Refresh error:', error);
      _resetIndicator(indicator);
    } finally {
      refreshing  = false;
      startY      = null;
      currentY    = null;
    }
  }

  // ── Touch events ───────────────────────────────────────────────────────────
  container.addEventListener('touchstart', (e) => {
    if (container.scrollTop !== 0) return;
    startY  = e.touches[0].clientY;
    pulling = true;
  }, { passive: true });

  container.addEventListener('touchmove', (e) => {
    if (refreshing || !pulling || startY === null) return;

    currentY = e.touches[0].clientY;
    const pullDistance = currentY - startY;

    if (pullDistance > 10 && container.scrollTop === 0) {
      indicator.classList.remove('hidden');
      indicator.classList.add('visible', 'pulling');
      indicator.style.transform = `translateX(-50%) translateY(${Math.min(pullDistance / 2, 50)}px)`;
      indicator.style.opacity   = Math.min(pullDistance / 100, 1);

      indicator.innerHTML = pullDistance > 120
        ? `<svg class="refresh-spinner" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
             <polyline points="1 4 1 10 7 10"></polyline>
             <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"></path>
           </svg>
           <span>Release to refresh</span>`
        : `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
             <line x1="12" y1="5" x2="12" y2="19"></line>
             <polyline points="19 12 12 19 5 12"></polyline>
           </svg>
           <span>Pull to refresh</span>`;
    } else {
      _resetIndicator(indicator);
    }
  }, { passive: false });

  container.addEventListener('touchend', async () => {
    if (!pulling) return;

    const pullDistance = currentY !== null ? currentY - startY : 0;
    pulling = false;

    if (pullDistance > 120 && container.scrollTop === 0) {
      await triggerRefresh();
    } else {
      _resetIndicator(indicator);
      startY   = null;
      currentY = null;
    }
  });

  // ── Mouse events (desktop testing) ─────────────────────────────────────────
  let mouseDown   = false;
  let mouseStartY = null;

  container.addEventListener('mousedown', (e) => {
    if (container.scrollTop !== 0) return;
    mouseDown   = true;
    mouseStartY = e.clientY;
  });

  container.addEventListener('mousemove', (e) => {
    if (!mouseDown || refreshing || mouseStartY === null) return;

    const pullDistance = e.clientY - mouseStartY;

    if (pullDistance > 10 && container.scrollTop === 0) {
      indicator.classList.remove('hidden');
      indicator.classList.add('visible');
      indicator.style.transform = `translateX(-50%) translateY(${Math.min(pullDistance / 2, 50)}px)`;
      indicator.style.opacity   = Math.min(pullDistance / 100, 1);
      indicator.textContent     = pullDistance > 120 ? '↓ Release to refresh' : '↓ Pull to refresh';
    }
  });

  container.addEventListener('mouseup', async (e) => {
    if (!mouseDown) return;

    const pullDistance = e.clientY - mouseStartY;
    mouseDown = false;

    if (pullDistance > 160 && container.scrollTop === 0) {
      await triggerRefresh();
    } else {
      _resetIndicator(indicator);
    }

    mouseStartY = null;
  });
}

// ── Indicator reset helper ─────────────────────────────────────────────────
function _resetIndicator(indicator) {
  indicator.classList.remove('visible', 'pulling');
  indicator.classList.add('hidden');
  indicator.style.transform = 'translateX(-50%) translateY(-50px)';
  indicator.style.opacity   = '0';
}

// ============================================================================
// OBSERVE #notifications FOR active CLASS — INIT ON ACTIVATION
// ============================================================================

document.addEventListener('DOMContentLoaded', () => {
  const notificationsSection = document.getElementById('notifications');

  if (!notificationsSection) {
    console.error('#notifications element not found');
    return;
  }

  // If already active on load, init immediately
  if (notificationsSection.classList.contains('active')) {
    initializeNotifications();
  }

  // Watch for the active class being added
  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (mutation.type === 'attributes' && mutation.attributeName === 'class') {
        if (notificationsSection.classList.contains('active')) {
          initializeNotifications();
        }
      }
    }
  });

  observer.observe(notificationsSection, {
    attributes: true,
    attributeFilter: ['class'],
  });
});

export { initializeNotifications };

// ============================================================================
// BATCH 2: LIVE NOTIFICATIONS (new_notification WebSocket event)
// ============================================================================
//
// The backend already emits 'new_notification' the moment a notification
// is created (see services/notification_service.py::_broadcast) with a
// payload shape — {id, title, body, type, link, related_type, related_id,
// created_at} — that already matches what createNotificationCard() reads;
// nothing on the frontend was listening for it, so the nav badge and the
// notification list only ever updated on a manual page load, a full
// pull-to-refresh, or (now) after one of the Batch 2 bulk actions above.
// This listener is registered unconditionally at module load (not inside
// initializeNotifications(), which only runs once the #notifications
// section becomes active), since the badge in the nav bar needs to update
// live no matter which section the user is currently looking at.
socket.on('new_notification', (payload) => {
  if (!payload || !payload.id) return;

  // Bump the badge immediately — this is the one piece that matters even
  // if the user never opens the notifications section this session.
  const badge = document.getElementById('notification-badge');
  const currentCount = badge ? (parseInt(badge.textContent, 10) || 0) : 0;
  updateBadgeCount(currentCount + 1);

  // If the notifications list is already open, prepend the new card live
  // rather than waiting for the next reload — but respect whatever filter
  // chip is currently active, so a filtered view doesn't suddenly show a
  // notification from a different category.
  const activeType = notificationState.getActiveType();
  if (activeType && activeType !== payload.type) {
    // Still matches _build_type_filter()'s category grouping (e.g.
    // 'badges' covers both 'badge_earned' and 'reputation_level_up'), not
    // just an exact string match, so this needs the same groupings the
    // backend uses rather than a plain equality check.
    const TYPE_GROUPS = {
      badges:      ['badge_earned', 'reputation_level_up'],
      engagements: ['comment', 'like', 'helpful', 'solution_accepted'],
      connections: ['connection_request', 'connection_accepted'],
      messages:    ['message'],
      mentions:    ['mention'],
    };
    const inGroup = TYPE_GROUPS[activeType]
      ? TYPE_GROUPS[activeType].includes(payload.type)
      : (activeType === 'threads' ? String(payload.type || '').startsWith('thread_') : payload.type === activeType);
    if (!inGroup) return;
  }

  const section = document.getElementById('notifications');
  const container = document.getElementById('notifications-list-container');
  if (!section || !container || !section.classList.contains('active')) return;

  // Clear the empty-state placeholder if it's showing. Both the
  // server-rendered empty state (.notifications-empty-state, in
  // feed2.html) and the JS-rendered one (showEmptyNotifications(), which
  // has no distinguishing class) have no [data-notification-id] card
  // inside them, which a real notification list always does — checking
  // for that is more reliable here than matching either markup shape.
  if (container.children.length > 0 && !container.querySelector('[data-notification-id]')) {
    container.innerHTML = '';
  }

  const tempDiv = document.createElement('div');
  tempDiv.innerHTML = createNotificationCard({ ...payload, is_read: false });
  const card = tempDiv.firstElementChild;
  if (card) {
    container.insertBefore(card, container.firstChild);
  }
});
