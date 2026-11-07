/**
 * ============================================================================
 * UNIFIED DELEGATION SYSTEM  —  app.unified.js
 * Single listener on document.body. Smart container detection. All actions here.
 * ─── UPDATED: LeaderboardHandlers added ─────────────────────────────────────
 * ============================================================================
 */

import { downloadResource, closeModal, openModal } from './feed/feed.utils.js';
import { detectContainer, getActiveContainer } from './container-detect.js';

import { HomeworkHandlers }     from '../js/homework/homework.delegation.js';
import { MessageHandlers }      from '../js/message/message.delegation.js';
import { AnalyticsHandlers }    from '../js/analytics/analytics.delegation.js';
import { ConnectionHandlers }   from '../js/connection/connection.delegation.js';
import { NotificationHandlers } from '../js/notification/notification.delegation.js';
import { ProfileHandlers }      from '../js/profile/profile.delegation.js';
import { FeedHandlers }         from '../js/feed/feed.delegation.js';

// ── NEW ─────────────────────────────────────────────────────────────────────
import { LeaderboardHandlers }  from '../js/leaderboard/leaderboard.delegation.js';
// ─────────────────────────────────────────────────────────────────────────────

let lazyModals = null;
let lazyEvents = null;
let lazyUtils  = null;

async function getModals() {
  if (!lazyModals) lazyModals = await import('./feed/feed.modals.js');
  return lazyModals;
}

async function getEvents() {
  if (!lazyEvents) lazyEvents = await import('./feed/feed.events.js');
  return lazyEvents;
}

export async function getUtils() {
  if (!lazyUtils) lazyUtils = await import('./feed/feed.utils.js');
  return lazyUtils;
}

const UNIFIED_ACTIONS = {
  ...FeedHandlers,
  ...AnalyticsHandlers,
  ...HomeworkHandlers,
  ...NotificationHandlers,
  ...MessageHandlers,
  ...ProfileHandlers,

  // ── NEW ──────────────────────────────────────────────────────────────────
  ...LeaderboardHandlers,
  ...ConnectionHandlers
  
  // ─────────────────────────────────────────────────────────────────────────
};


/**
 * UNIFIED EVENT HANDLER
 */
function unifiedEventHandler(event) {
  event.stopPropagation();
  const target = event.target.closest('[data-action]');
  if (!target) return;

  const action  = target.dataset.action;
  const handler = UNIFIED_ACTIONS[action];

  if (!handler) {
    console.debug(`No handler for action: ${action}`);
    return;
  }

  const containerType = detectContainer(target);

  try {
    handler(target, event, containerType);
  } catch (error) {
    console.error(`Error in handler for ${action}:`, error);
  }
}

/**
 * UNIFIED FORM HANDLER
 */
function unifiedFormHandler(event) {
  event.stopPropagation();
  const form = event.target.closest('form[data-action]');
  if (!form) return;

  const action  = form.dataset.action;
  const handler = UNIFIED_ACTIONS[action];

  if (!handler) {
    console.debug(`No handler for form action: ${action}`);
    return;
  }

  const containerType = detectContainer(form);

  try {
    handler(form, event, containerType);
  } catch (error) {
    console.error(`Error in form handler for ${action}:`, error);
  }
}

/**
 * SETUP UNIFIED DELEGATION
 */
export function setupUnifiedDelegation() {
  document.body.removeEventListener('click',  unifiedEventHandler);
  document.body.removeEventListener('submit', unifiedFormHandler);

  document.body.addEventListener('click',  unifiedEventHandler);
  document.body.addEventListener('submit', unifiedFormHandler);

  console.log('✅ Unified delegation system initialized');
}

// Export helpers for other modules
export { detectContainer, getActiveContainer };

if (typeof window !== 'undefined') {
  window.setupUnifiedDelegation = setupUnifiedDelegation;
}
