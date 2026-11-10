/**
 * Activity Feed Initialization
 *
 * Mirrors static/js/message/message.init.js's section-activation
 * pattern: #activity-feed is always in the DOM (hidden until the nav
 * item for it is clicked, same as every other section in feed2.html),
 * so a MutationObserver watching for the 'active' class is how this
 * app lazily initializes a section instead of running its setup on
 * every page load regardless of whether the person ever opens it.
 *
 * Before this file existed, activity.feed.js's own
 * initActivityFeedSection ran unconditionally on document
 * DOMContentLoaded — see that function's docstring for why that was
 * wrong (a GET /activity/feed request and a 2-minute polling loop for
 * every single page load, whether or not Activity Feed was ever
 * opened). This file replaces that unconditional call with the same
 * activate-on-'active'-class gating messaging already uses.
 */

import {
  initActivityFeedSection,
  subscribeToGlobalPulse,
  unsubscribeFromGlobalPulse,
  activeScope,
} from './activity.feed.js';

let isInitialized = false;

function activateActivityFeed() {
  if (!isInitialized) {
    isInitialized = true;
    initActivityFeedSection();
  }

  // Join the global pulse room only while this section is actually on
  // screen, and only when the Campus Pulse tab is the one selected —
  // subscription is opt-in server-side specifically so a user who
  // never opens this tab never receives that broadcast traffic (see
  // services/websocket_messages.py's activity_feed:subscribe handler).
  // Re-checked every activation, not just the first one, since the
  // person may have left and come back with a different tab selected
  // than last time.
  if (activeScope === 'global') {
    subscribeToGlobalPulse();
  }
}

function deactivateActivityFeed() {
  // Unlike message.init.js (which has no equivalent teardown — a
  // private per-user room doesn't need unsubscribing from), the global
  // pulse room genuinely should be left when the section isn't visible,
  // so leaving it here is a deliberate difference from that file's
  // pattern, not an oversight.
  unsubscribeFromGlobalPulse();
}

document.addEventListener('DOMContentLoaded', () => {
  const activityFeedSection = document.getElementById('activity-feed');

  if (!activityFeedSection) {
    console.error('#activity-feed element not found');
    return;
  }

  // If already active on load (e.g. deep link), init immediately
  if (activityFeedSection.classList.contains('active')) {
    activateActivityFeed();
  }

  // Watch for the active class being added/removed
  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (mutation.type === 'attributes' && mutation.attributeName === 'class') {
        if (activityFeedSection.classList.contains('active')) {
          activateActivityFeed();
        } else {
          deactivateActivityFeed();
        }
      }
    }
  });

  observer.observe(activityFeedSection, {
    attributes: true,
    attributeFilter: ['class'],
  });
});
