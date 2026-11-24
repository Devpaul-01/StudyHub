/**
 * ============================================================================
 * LEADERBOARD INIT  —  leaderboard.init.js
 * Bootstraps the leaderboard when the #leaderboard section becomes active.
 * Follows the exact notification.init.js observer pattern.
 * ============================================================================
 */

import { initialLoad, refreshIfStale } from './leaderboard.events.js';
import { setupDeptFilter }       from './leaderboard.delegation.js';
import { leaderboardState }      from './leaderboard.state.js';

let isInitialized = false;

// ============================================================================
// INITIALIZE LEADERBOARD SYSTEM
// ============================================================================

function initializeLeaderboard() {
  // Prevent re-initialization (mirrors notification.init.js guard)
  if (isInitialized) return;
  isInitialized = true;

  console.log('✅ Leaderboard system initializing…');

  // Wire department filter change event (can't go through delegation — change doesn't bubble well)
  setupDeptFilter();

  // Load initial data
  initialLoad();

  console.log('✅ Leaderboard system initialized');
}

// ============================================================================
// OBSERVE #leaderboard FOR active CLASS — INIT ON ACTIVATION
// Mirrors notification.init.js MutationObserver pattern exactly.
// ============================================================================

document.addEventListener('DOMContentLoaded', () => {
  const lbSection = document.getElementById('leaderboard');

  if (!lbSection) {
    console.error('lb: #leaderboard element not found');
    return;
  }

  // If already active on load, init immediately
  if (lbSection.classList.contains('active')) {
    initializeLeaderboard();
  }

  // Watch for the active class being added by navigateTo()
  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (mutation.type === 'attributes' && mutation.attributeName === 'class') {
        if (lbSection.classList.contains('active')) {
          initializeLeaderboard();
          refreshIfStale();   // no-op on first activation and while data is fresh
        }
      }
    }
  });

  observer.observe(lbSection, {
    attributes:      true,
    attributeFilter: ['class'],
  });
});

export { initializeLeaderboard };
