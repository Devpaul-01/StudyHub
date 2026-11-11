/**
 * Activity Feed — action button delegation handlers
 *
 * Follows the same pattern as every other feature's *.delegation.js
 * (see connection.delegation.js for the clearest example): a plain
 * object keyed by action-name string, each value a
 * (target, event) => {...} function, spread into app.unified.js's
 * UNIFIED_ACTIONS.
 *
 * NOTE on connection.events.js::sendConnectionRequest: that function
 * exists and does call ConnectionAPI.sendConnectionRequest
 * underneath, but it's tightly coupled to the Connections section's
 * own DOM (it looks for and removes a closest('.connection-card') /
 * closest('.user-card'), and refreshes connectionState's current tab)
 * — none of which exists on this page's Activity Feed section. Calling
 * it from here would silently no-op those DOM lookups at best, or
 * throw (the User.closest('.user-card') line references an undefined
 * `target` variable, not `button`, which would throw if that branch is
 * reached) at worst. 'connect-from-activity' below calls
 * ConnectionAPI.sendConnectionRequest directly instead and manages the
 * button's own state itself, deliberately not reusing that wrapper.
 */

import { ConnectionAPI } from '../connection/connection.api.js';
import { showHomeworkToast } from '../homework/homework.utils.js';
import {
  activeScope,
  setActiveScope,
  loadActivityFeed,
  loadActivityPulse,
  subscribeToGlobalPulse,
  unsubscribeFromGlobalPulse,
} from './activity.feed.js';

export const ActivityHandlers = {
  /**
   * Tab switcher — toggles between the My Network (connections) and
   * Campus Pulse (global) feeds. data-activity-scope on the clicked
   * button is 'connections' or 'global', matching feed2.html's markup.
   */
  'switch-activity-scope': (target, event) => {
    const scope = target.dataset.activityScope;
    if (!scope || scope === activeScope) return;

    const tabsContainer = target.closest('.activity-tabs');
    if (tabsContainer) {
      tabsContainer.querySelectorAll('.activity-tab').forEach((tab) => {
        tab.classList.toggle('active', tab === target);
      });
    }

    // Leave/join the broadcast room as the tab changes — only Campus
    // Pulse needs the shared activity_pulse:global room; My Network's
    // activity_feed_update events arrive on the per-user room every
    // socket already joins on connect, no subscribe needed.
    if (activeScope === 'global') {
      unsubscribeFromGlobalPulse();
    }

    setActiveScope(scope);

    if (scope === 'global') {
      subscribeToGlobalPulse();
      loadActivityPulse();
    } else {
      loadActivityFeed();
    }
  },

  /**
   * Connect button — shown on user_signed_up cards, the one global-scope
   * activity type that keeps a real user_id (see
   * services/activity_service.py::GLOBAL_SCOPE_IDENTITY_ALLOWED).
   */
  'connect-from-activity': async (target, event) => {
    const userId = target.dataset.userId;
    if (!userId) return;

    target.disabled = true;
    const originalText = target.textContent;
    target.textContent = 'Sending...';

    try {
      const response = await ConnectionAPI.sendConnectionRequest(parseInt(userId, 10));
      if (response.status === 'success') {
        target.textContent = response.data?.is_instant ? '✓ Connected' : '✓ Requested';
      } else {
        target.disabled = false;
        target.textContent = originalText;
        showHomeworkToast('Could not send connection request', 'error');
      }
    } catch (error) {
      console.error('connect-from-activity error:', error);
      target.disabled = false;
      target.textContent = originalText;
      showHomeworkToast('Could not send connection request', 'error');
    }
  },

  /**
   * Volunteer button — shown on help_request_broadcast cards.
   * ConnectionAPI.volunteerForHelp has no feature-coupled wrapper
   * anywhere in the codebase (confirmed by grep), so calling it
   * directly here matches how it's used everywhere else it's called.
   */
  'volunteer-from-activity': async (target, event) => {
    const requestId = target.dataset.helpRequestId;
    if (!requestId) return;

    target.disabled = true;
    const originalText = target.textContent;
    target.textContent = 'Volunteering...';

    try {
      const response = await ConnectionAPI.volunteerForHelp(parseInt(requestId, 10));
      if (response.status === 'success') {
        target.textContent = '✓ Volunteered';
      } else {
        target.disabled = false;
        target.textContent = originalText;
        showHomeworkToast('Could not volunteer right now', 'error');
      }
    } catch (error) {
      console.error('volunteer-from-activity error:', error);
      target.disabled = false;
      target.textContent = originalText;
      showHomeworkToast('Could not volunteer right now', 'error');
    }
  },

  /**
   * View Thread — used by thread_joined, thread_created, and
   * message_pinned cards. Navigates to the real standalone thread
   * detail page. Deliberately NOT thread.render.js's showThreadView:
   * that function only works from inside the in-app Threads section
   * (toggles #thread-list-panel/#thread-chat-panel, neither of which
   * exists in this page's DOM) and the person has said the in-app
   * Threads feature lives on a different HTML page entirely — routing
   * through it from here would silently do nothing.
   */
  'view-thread-from-activity': (target, event) => {
    const threadId = target.dataset.threadId;
    if (!threadId) return;
    window.location.href = `/student/threads/view/${encodeURIComponent(threadId)}`;
  },

  /**
   * View Post — used by answer_marked_solution and post_created cards.
   * Same reasoning as view-thread-from-activity: a real standalone page
   * exists now, navigate to it directly rather than through any
   * in-feed post modal.
   */
  'view-post-from-activity': (target, event) => {
    const postId = target.dataset.postId;
    if (!postId) return;
    window.location.href = `/student/posts/view/${encodeURIComponent(postId)}`;
  },
};
