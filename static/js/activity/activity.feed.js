/**
 * ============================================================================
 * HOMEWORK ACTIVITY FEED
 * Real-time feed showing what connections are working on
 * ============================================================================
 */

import { homeworkAPI } from '../homework/homework.api.js';
import { showHomeworkToast } from '../homework/homework.utils.js';
import { socket } from '../core/socket.js';

// Two separate caches, not one shared array — connections-scope
// (My Network tab, from GET /activity/feed + activity_feed_update) and
// global-scope (Campus Pulse tab, from GET /activity/pulse +
// activity_pulse_update) are different data with different payload
// shapes (see normalizePulseItem below); mixing them into one array
// would mean renderActivityFeed(activityCache) shows both tabs' items
// at once regardless of which tab is actually selected.
let connectionsCache = [];
let pulseCache = [];
let lastActivityId = null;
let _wsHandlersRegistered = false;

// Which tab is currently on screen — 'connections' (default, matches
// this feature's original/only behavior before Campus Pulse existed)
// or 'global'. Exported so the tab-switcher (added in a later commit)
// can set it; addActivityToFeed below reads it to decide whether a
// just-arrived WebSocket event should render immediately. An event for
// the tab that isn't on screen is still cached (so switching to it
// later shows it without a re-fetch), just not rendered right now.
export let activeScope = 'connections';
export function setActiveScope(scope) {
  activeScope = scope;
}

function cacheFor(scope) {
  return scope === 'global' ? pulseCache : connectionsCache;
}

/**
 * Load activity feed (connections scope — My Network tab)
 */
export async function loadActivityFeed() {
  try {
    const response = await homeworkAPI.getActivityFeed();
    
    if (response.status === 'success') {
      connectionsCache = response.data.activities || [];
      if (activeScope === 'connections') renderActivityFeed(connectionsCache);

      if (connectionsCache.length > 0) {
        lastActivityId = connectionsCache[0].id;
      }
    }
  } catch (error) {
    console.error('Error loading activity feed:', error);
    if (activeScope === 'connections') renderActivityError();
  }
}

/**
 * Load activity pulse (global scope — Campus Pulse tab). Items from
 * GET /activity/pulse are already in the anonymized shape
 * normalizePulseItem produces for WebSocket pulse events — the route
 * (get_activity_pulse in homework_system.py) runs every row through
 * the same anonymize_for_global this file's normalizer is modeled on —
 * so no extra normalization is needed here, just passed straight to
 * renderActivityFeed.
 */
export async function loadActivityPulse() {
  try {
    const response = await homeworkAPI.getActivityPulse();

    if (response.status === 'success') {
      pulseCache = response.data.activities || [];
      if (activeScope === 'global') renderActivityFeed(pulseCache);
    }
  } catch (error) {
    console.error('Error loading activity pulse:', error);
    if (activeScope === 'global') renderActivityError();
  }
}

/**
 * Render activity feed in container
 */
export function renderActivityFeed(activities) {
  const container = document.getElementById('activity-feed-list');
  if (!container) return;
  
  if (!activities || activities.length === 0) {
    container.innerHTML = renderEmptyState();
    return;
  }
  
  const html = activities.map(activity => renderActivityItem(activity)).join('');
  container.innerHTML = html;
}

/**
 * Render empty state
 */
function renderEmptyState() {
  return `
    <div class="activity-empty">
      <div class="activity-empty-icon">
        <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <circle cx="12" cy="12" r="10"></circle>
          <path d="M12 8v4l3 3"></path>
        </svg>
      </div>
      <h4 class="activity-empty-title">No recent activity</h4>
      <p class="activity-empty-text">Activities from your connections in the last 2 hours will appear here</p>
      <p class="activity-empty-hint">Be the first to get started! 🚀</p>
    </div>
  `;
}

/**
 * Render error state
 */
function renderActivityError() {
  const container = document.getElementById('activity-feed-list');
  if (!container) return;
  
  container.innerHTML = `
    <div class="activity-error">
      <div class="activity-error-icon">⚠️</div>
      <p>Failed to load activity feed</p>
      <button class="hw-btn hw-btn-primary" data-action="reload-activity-feed">
        Try Again
      </button>
    </div>
  `;
}

/**
 * Normalize a GET /activity/pulse item or an activity_pulse_update
 * WebSocket payload into the same { id, type, user, data, time_ago }
 * shape renderActivityItem already expects for connections-scoped
 * activities — so one render path serves both, instead of branching
 * inside renderActivityItem itself.
 *
 * Most global-scope types are anonymized server-side down to
 * department/subject only (see
 * services/activity_service.py::anonymize_for_global) — there is no
 * real user to show for those, so `user` here is a synthetic
 * placeholder (id: null, no avatar) rather than null, so
 * renderActivityItem's existing `user.name`/`user.avatar` reads don't
 * need their own null-checks. user_signed_up is the one allowlisted
 * type that keeps a real user_id/name; it gets a real user object.
 *
 * This is a reasonable starting point, not a final visual design —
 * anonymized items showing a generic "Someone in {department}" row
 * work but may be worth a visually distinct card style later (no
 * avatar to show is a real UI gap, not just a data-shape one).
 */
function normalizePulseItem(item) {
  const hasIdentity = !!item.user_id;
  return {
    id: item.id,
    type: item.type,
    data: item.data || {},
    time_ago: item.time_ago || 'just now',
    user: hasIdentity
      ? { id: item.user_id, name: item.name, avatar: null, is_online: false }
      : { id: null, name: item.department || 'Someone', avatar: null, is_online: false },
  };
}

/**
 * Render single activity item
 */
function renderActivityItem(activity) {
  const { id, type, user, data, time_ago } = activity;
  
  const { icon, message, badge, actionButton } = getActivityDisplay(type, user, data);
  const onlineIndicator = user.is_online ? '<div class="activity-online-dot"></div>' : '';
  
  return `
    <div class="activity-item" data-activity-id="${id}">
      <div class="activity-icon-wrapper">
        <div class="activity-icon">${icon}</div>
      </div>
      
      <div class="activity-content">
        <div class="activity-header">
          <div class="activity-user-info">
            <img src="${user.avatar || '/static/default-avatar.png'}"
                 alt="${user.name}"
                 class="activity-avatar" />
            ${onlineIndicator}
          </div>
          <div class="activity-time">${time_ago}</div>
        </div>
        
        <div class="activity-message">${message}</div>
        
        ${badge ? `<div class="activity-badges">${badge}</div>` : ''}
        ${actionButton ? `<div class="activity-action">${actionButton}</div>` : ''}
      </div>
    </div>
  `;
}

/**
 * Get display info for activity type
 */
/**
 * Minimal HTML escaper for untrusted user-submitted text embedded into
 * activity messages (e.g. message_pinned's data.message_preview, which
 * carries actual pinned message text, not a server-controlled
 * title/name). Re-implemented locally rather than imported, matching
 * how this codebase already handles it elsewhere — every module that
 * needs this (search.templates.js, admin.init.js, thread-view.js,
 * profile-view.templates.js) defines its own copy rather than sharing
 * one from a common utility module.
 */
function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function getActivityDisplay(type, user, data) {
  let icon, message, badge = '', actionButton = '';
  
  switch (type) {
    case 'started_homework':
      // FIX: the old case read data.title, data.difficulty, and
      // data.needs_help — none of those keys exist in the real backend
      // payload (routes/student/homework_system.py::create_assignment
      // sends assignment_id, assignment_title, subject). This rendered
      // "started undefined" with no badge, and the Offer Help button
      // (gated on the always-undefined data.needs_help) never appeared
      // at all. record_live_activity for this type only ever fires when
      // the assignment is_shared_for_help (confirmed at the call site),
      // so every started_homework activity is implicitly asking for
      // help — the button is unconditional here, not gated on a field
      // that was never actually sent.
      icon = '🚀';
      message = `<strong>${user.name}</strong> started <strong>${data.assignment_title}</strong>`;

      if (data.subject) {
        badge = `<span class="activity-badge badge-subject">${data.subject}</span>`;
      }

      actionButton = `
        <button class="activity-btn"
                data-action="offer-help-from-activity"
                data-assignment-id="${data.assignment_id}">
          🤝 Offer Help
        </button>
      `;
      break;
      
    case 'offered_help':
      icon = '🤝';
      message = `<strong>${user.name}</strong> is helping <strong>${data.requester_name}</strong> with ${data.subject}`;
      
      if (data.subject) {
        badge = `<span class="activity-badge badge-subject">${data.subject}</span>`;
      }
      break;
      
    case 'completed_homework':
      icon = '✅';
      message = `<strong>${user.name}</strong> completed <strong>${data.assignment_title}</strong>`;
      
      if (data.helper_count && data.helper_count > 1) {
        badge = `<span class="activity-badge badge-helpers">👥 ${data.helper_count} helpers</span>`;
      }
      
      if (data.subject) {
        badge += `<span class="activity-badge badge-subject">${data.subject}</span>`;
      }
      break;
      
    case 'submitted_solution':
      icon = '📝';
      message = `<strong>${user.name}</strong> submitted solution to <strong>${data.requester_name}</strong>`;
      
      if (data.subject) {
        badge = `<span class="activity-badge badge-subject">${data.subject}</span>`;
      }
      break;
      
    case 'achieved_streak':
      // FIX: backend's record_live_activity call (give_feedback, in
      // routes/student/homework_system.py) sends current_streak /
      // longest_streak — there is no streak_count key anywhere in this
      // payload. The old data.streak_count reference rendered
      // "undefined-day helping streak!" on every real event.
      icon = '🔥';
      message = `<strong>${user.name}</strong> reached a <strong>${data.current_streak}-day</strong> helping streak!`;
      if (data.longest_streak && data.longest_streak > data.current_streak) {
        badge = `<span class="activity-badge badge-achievement">Best: ${data.longest_streak}</span>`;
      } else {
        badge = '<span class="activity-badge badge-achievement">Personal Best!</span>';
      }
      break;

    // ------------------------------------------------------------------
    // Cases below are new — this activity type was never rendered at
    // all before (fell through to the generic "did something" default).
    // Several of these fire with scope="global" and therefore arrive
    // anonymized (see services/activity_service.py::anonymize_for_global):
    // user.name will be "Someone" or a department string, not a real
    // name, for every type except user_signed_up — the payload's own
    // data fields (badge_name, new_level_name, thread_title, etc.) carry
    // the actual content, user.name is woven in only where it still
    // reads sensibly as a placeholder.
    // ------------------------------------------------------------------

    case 'help_request_broadcast':
      icon = '🆘';
      message = `<strong>${escapeHtml(user.name)}</strong> needs help with <strong>${escapeHtml(data.subject) || 'something'}</strong>`;
      actionButton = `
        <button class="activity-btn"
                data-action="volunteer-from-activity"
                data-help-request-id="${data.help_request_id}">
          🙋 Volunteer
        </button>
      `;
      break;

    case 'connection_accepted':
      icon = '🤝';
      message = `<strong>${escapeHtml(user.name)}</strong> connected with <strong>${escapeHtml(data.other_user_name) || 'someone'}</strong>`;
      break;

    case 'user_signed_up':
      icon = '👋';
      message = `<strong>${escapeHtml(user.name)}</strong> just joined StudyHub`;
      if (data.department) {
        badge = `<span class="activity-badge badge-subject">${escapeHtml(data.department)}</span>`;
      }
      if (user.id) {
        actionButton = `
          <button class="activity-btn"
                  data-action="connect-from-activity"
                  data-user-id="${user.id}">
            ➕ Connect
          </button>
        `;
      }
      break;

    case 'answer_marked_solution':
      icon = '✔️';
      message = `<strong>${escapeHtml(user.name)}</strong>'s answer was marked as the solution to <strong>${escapeHtml(data.post_title)}</strong>`;
      actionButton = `
        <button class="activity-btn"
                data-action="view-post-from-activity"
                data-post-id="${data.post_id}">
          View Post
        </button>
      `;
      break;

    case 'badge_earned':
      icon = '🏅';
      message = `<strong>${escapeHtml(user.name)}</strong> earned the <strong>${escapeHtml(data.badge_name)}</strong> badge`;
      badge = '<span class="activity-badge badge-achievement">New Badge</span>';
      break;

    case 'reputation_level_up':
      icon = '⭐';
      message = `<strong>${escapeHtml(user.name)}</strong> leveled up to <strong>${escapeHtml(data.new_level_name)}</strong>`;
      badge = '<span class="activity-badge badge-achievement">Level Up</span>';
      break;

    case 'thread_joined':
      icon = '👥';
      message = `<strong>${escapeHtml(user.name)}</strong> joined <strong>${escapeHtml(data.thread_title)}</strong>`;
      if (data.department) {
        badge = `<span class="activity-badge badge-subject">${escapeHtml(data.department)}</span>`;
      }
      actionButton = `
        <button class="activity-btn"
                data-action="view-thread-from-activity"
                data-thread-id="${data.thread_id}">
          View Thread
        </button>
      `;
      break;

    case 'thread_left':
      icon = '👋';
      message = `<strong>${escapeHtml(user.name)}</strong> left <strong>${escapeHtml(data.thread_title)}</strong>`;
      break;

    case 'thread_created':
      icon = '✨';
      message = `<strong>${escapeHtml(user.name)}</strong> started a new thread: <strong>${escapeHtml(data.thread_title)}</strong>`;
      if (data.department) {
        badge = `<span class="activity-badge badge-subject">${escapeHtml(data.department)}</span>`;
      }
      if (data.is_open) {
        badge += '<span class="activity-badge badge-help">Open to Join</span>';
      }
      actionButton = `
        <button class="activity-btn"
                data-action="view-thread-from-activity"
                data-thread-id="${data.thread_id}">
          View Thread
        </button>
      `;
      break;

    case 'message_pinned':
      icon = '📌';
      message = `A message was pinned in <strong>${escapeHtml(data.thread_title)}</strong>`;
      if (data.message_preview) {
        message += `: "${escapeHtml(data.message_preview)}"`;
      }
      if (data.department) {
        badge = `<span class="activity-badge badge-subject">${escapeHtml(data.department)}</span>`;
      }
      actionButton = `
        <button class="activity-btn"
                data-action="view-thread-from-activity"
                data-thread-id="${data.thread_id}">
          View Thread
        </button>
      `;
      break;

    // post_created is NOT currently wired up anywhere in the backend —
    // confirmed by grepping every record_live_activity(...) call site in
    // routes/student/ and services/, this type is never actually sent.
    // It was listed as "optional" in the design doc's activity-type
    // catalog, meaning a candidate for future wiring, not something
    // already implemented. The case below is a no-op today (nothing
    // will ever reach it) but matches the naming/shape the design doc
    // describes, so it's correct and ready if that backend wiring is
    // added later — same reasoning the backend's own commits used for
    // study_buddy_session_logged before that whole feature was removed.
    case 'post_created':
      icon = '📄';
      message = `<strong>${escapeHtml(user.name)}</strong> created a new post`;
      actionButton = `
        <button class="activity-btn"
                data-action="view-post-from-activity"
                data-post-id="${data.post_id}">
          View Post
        </button>
      `;
      break;

    default:
      icon = '📚';
      message = `<strong>${user.name}</strong> did something`;
  }
  
  return { icon, message, badge, actionButton };
}

/**
 * Add new activity to feed (from WebSocket)
 *
 * @param {object} rawActivity - the raw event payload. Connections-scope
 *   (activity_feed_update) is already in the { id, type, user, data,
 *   time_ago } shape renderActivityItem expects; pulse-scope
 *   (activity_pulse_update) is the anonymized shape and needs
 *   normalizePulseItem first.
 * @param {object} [options]
 * @param {boolean} [options.pulse] - true when rawActivity came from
 *   activity_pulse_update rather than activity_feed_update.
 */
export function addActivityToFeed(rawActivity, options = {}) {
  const activity = options.pulse ? normalizePulseItem(rawActivity) : rawActivity;
  const eventScope = options.pulse ? 'global' : 'connections';
  const cache = cacheFor(eventScope);

  // Add to the matching tab's cache, regardless of which tab is active
  // right now — this is what lets switching tabs later show items that
  // arrived while the person was looking at the other one.
  cache.unshift(activity);

  // Keep only last 50
  if (cache.length > 50) {
    cache.pop();
  }

  // An event for the tab that isn't currently on screen is cached above
  // but shouldn't be rendered into the visible list right now.
  if (eventScope !== activeScope) return;

  // Get container
  const container = document.getElementById('activity-feed-list');
  if (!container) return;
  
  // If feed was empty, replace empty state
  const emptyState = container.querySelector('.activity-empty');
  if (emptyState) {
    renderActivityFeed(cache);
    return;
  }
  
  // Otherwise, prepend new item
  const newItemHTML = renderActivityItem(activity);
  container.insertAdjacentHTML('afterbegin', newItemHTML);
  
  // Animate in
  const newItem = container.querySelector(`[data-activity-id="${activity.id}"]`);
  if (newItem) {
    newItem.style.opacity = '0';
    newItem.style.transform = 'translateY(-20px)';
    
    setTimeout(() => {
      newItem.style.transition = 'all 0.3s ease';
      newItem.style.opacity = '1';
      newItem.style.transform = 'translateY(0)';
    }, 10);
    
    // Add highlight effect
    setTimeout(() => {
      newItem.classList.add('activity-new');
      setTimeout(() => newItem.classList.remove('activity-new'), 2000);
    }, 300);
  }
  
  // Remove oldest if too many visible
  const items = container.querySelectorAll('.activity-item');
  if (items.length > 50) {
    items[items.length - 1].remove();
  }
}

/**
 * Initialize activity feed WebSocket
 *
 * FIX: the old version of this function read window.socket, which is
 * never assigned anywhere in this codebase (confirmed by grepping every
 * .js file for "window.socket =" — zero matches), so it was dead code
 * even when uncommented. The real, working pattern — used by
 * static/js/message/message.websocket.js, the one module in this app
 * that actually has live WebSocket messaging working — is a single
 * shared Socket.IO client exported from static/js/core/socket.js as
 * `socket` (an ES module export, not a window global). That client
 * calls io(...) at module-load time with no autoConnect: false, so the
 * connection itself is already starting as soon as any module imports
 * it; this function only needs to register handlers on it, not connect
 * it.
 *
 * Idempotent (_wsHandlersRegistered guard), same reasoning as
 * MessageWebSocket's own _handlersRegistered flag — safe to call every
 * time the Activity Feed section is activated.
 */
export function initActivityFeedWebSocket() {
  if (_wsHandlersRegistered) return;
  _wsHandlersRegistered = true;

  socket.on('activity_feed_update', (data) => addActivityToFeed(data));
  socket.on('activity_pulse_update', (data) => addActivityToFeed(data, { pulse: true }));
}

/**
 * Join the shared global/campus-pulse broadcast room. Only call this
 * while the Campus Pulse tab is actually visible — subscription is
 * deliberately opt-in server-side (see
 * services/websocket_messages.py's activity_feed:subscribe handler) so
 * a user who never opens that tab never pays for the broadcast traffic.
 */
export function subscribeToGlobalPulse() {
  socket.emit('activity_feed:subscribe');
}

/**
 * Leave the global/campus-pulse broadcast room. Call this when
 * switching away from the Campus Pulse tab or leaving the section.
 */
export function unsubscribeFromGlobalPulse() {
  socket.emit('activity_feed:unsubscribe');
}

/**
 * Auto-refresh feed (backup for WebSocket). Refreshes whichever tab is
 * active, same fix as handleActivityRefresh above — this always called
 * loadActivityFeed() regardless of the active tab before this change.
 */
export function startActivityFeedAutoRefresh() {
  // Refresh every 2 minutes
  setInterval(() => {
    if (activeScope === 'global') {
      loadActivityPulse();
    } else {
      loadActivityFeed();
    }
  }, 120000);
}

/**
 * Pull to refresh handler. Reloads whichever tab is currently active —
 * previously always called loadActivityFeed(), which would silently
 * refresh the wrong tab's data if the person pulled to refresh while on
 * Campus Pulse.
 */
export function handleActivityRefresh() {
  const container = document.getElementById('activity-feed-container');
  if (!container) return;
  
  // Add refreshing class
  container.classList.add('refreshing');

  const reload = activeScope === 'global' ? loadActivityPulse() : loadActivityFeed();

  reload.then(() => {
    // Remove refreshing class after short delay
    setTimeout(() => {
      container.classList.remove('refreshing');
      showHomeworkToast('Activity feed refreshed', 'success');
    }, 500);
  });
}

/**
 * Initialize activity feed section.
 *
 * FIX: previously bound to document's DOMContentLoaded unconditionally
 * (see the bottom of this file, before this change), meaning it ran
 * once on every page load regardless of whether the Activity Feed
 * section was ever opened — including firing a GET /activity/feed
 * request and starting a 2-minute polling loop for users who never
 * visit this section at all. Proper section-activation gating (mirror
 * of static/js/message/message.init.js's MutationObserver-on-`active`-
 * class pattern) is added in a later commit; this function itself is
 * unchanged in what it does, just no longer auto-invoked — see the
 * bottom of this file.
 */
export function initActivityFeedSection() {
  // Load initial data for whichever tab is active (defaults to
  // 'connections' until the tab switcher exists)
  if (activeScope === 'global') {
    loadActivityPulse();
  } else {
    loadActivityFeed();
  }

  initActivityFeedWebSocket();

  // Setup auto-refresh
  startActivityFeedAutoRefresh();
}

/**
 * Cleanup activity feed (when section hidden)
 */
export function cleanupActivityFeed() {
  connectionsCache = [];
  pulseCache = [];
  lastActivityId = null;
}

/**
 * Get activity feed stats for summary. Stats for whichever tab is
 * currently active — previously read a single shared cache that no
 * longer exists now that connections and pulse data are kept separate.
 */
export function getActivityFeedStats() {
  const cache = cacheFor(activeScope);
  const activityTypes = cache.reduce((acc, activity) => {
    acc[activity.type] = (acc[activity.type] || 0) + 1;
    return acc;
  }, {});
  
  return {
    total: cache.length,
    types: activityTypes,
    latest: cache[0] || null
  };
}

// The unconditional DOMContentLoaded -> initActivityFeedSection call that
// used to be here is removed as part of this fix (see
// initActivityFeedSection's docstring above for why it was wrong).
// Proper section-activation gating, so this only runs when the person
// actually opens the Activity Feed section, is added in the very next
// commit on this branch — until that lands, this section has no init
// trigger at all, which is a deliberately small, momentary gap rather
// than this commit trying to also own the section-lifecycle wiring.