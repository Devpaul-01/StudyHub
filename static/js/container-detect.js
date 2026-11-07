/**
 * ============================================================================
 * CONTAINER DETECTION — container-detect.js
 * ============================================================================
 * Extracted out of app.unified.js so it can be imported by
 * feed/feed.delegation.js without creating a circular import
 * (feed.delegation.js is itself imported BY app.unified.js to build
 * FeedHandlers — app.unified.js importing feed.delegation.js importing
 * app.unified.js back for just these two functions was a real
 * module-graph cycle that broke `type="module"` script evaluation in the
 * browser: app.unified.js's own DOMContentLoaded-independent top-level
 * code — including the `window.setupUnifiedDelegation = setupUnifiedDelegation`
 * assignment at the bottom of the file — never ran, which meant
 * setupUnifiedDelegation() was never callable, the single delegated
 * click listener on document.body was never attached, and every
 * data-action="..." button on the page (nav items, homework tabs, post
 * actions, etc.) silently did nothing. Moving these two pure, dependency-free
 * DOM-lookup functions to their own file breaks the cycle entirely: both
 * app.unified.js and feed.delegation.js now import from here instead of
 * from each other.
 */

/**
 * Smart container detection.
 * Returns: 'tag-modal' | 'comments-modal' | 'smart-feed' | 'profile' | 'leaderboard' | 'global'
 */
export function detectContainer(element) {
  const tagModal = document.getElementById('tag-posts-container');
  if (tagModal && tagModal.contains(element)) return 'tag-modal';

  const commentsContainer = document.getElementById('comments-container');
  if (commentsContainer && commentsContainer.contains(element)) return 'comments-modal';

  const profileSection = document.getElementById('profile');
  if (profileSection && profileSection.classList.contains('active') && profileSection.contains(element)) {
    return 'profile';
  }

  const lbSection = document.getElementById('leaderboard');
  if (lbSection && lbSection.contains(element)) return 'leaderboard';

  const postsContainer = document.getElementById('posts-container');
  if (postsContainer && postsContainer.contains(element)) return 'smart-feed';

  return 'global';
}

/**
 * Get active feed container for syncing.
 */
export function getActiveContainer(containerType) {
  if (containerType === 'tag-modal')      return document.getElementById('tag-posts-container');
  if (containerType === 'comments-modal') return document.getElementById('comments-container');
  if (containerType === 'smart-feed')     return document.querySelector('.posts-feed.active');
  if (containerType === 'profile')        return document.getElementById('profile');
  if (containerType === 'leaderboard')    return document.getElementById('leaderboard');
  return null;
}
