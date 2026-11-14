/**
 * ============================================================================
 * FEED RENDERING
 * Updated: appendPostsToFeed() now interleaves widgets correctly by using
 *          the global post-count offset stored in feedState, so widget
 *          positions remain consistent across initial render + scroll batches.
 * ============================================================================
 */

import { feedState } from './feed.state.js';
import { WIDGET_ORDER } from './feed.constants.js';
import { getLoadingSkeleton, hasWidgetData } from './feed.utils.js';
import { createPostCard, createCommentCard, createLocalCommentCard, renderThreadDetailsHTML } from './feed.templates.js';
import { createWidget } from './feed.widgets.js';

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/**
 * Build HTML for a slice of posts, interleaving widgets based on the
 * *global* post index (startIndex + local index) so that widget positions
 * are consistent whether this is the initial render or a later appended batch.
 *
 * Returns both the HTML string and the updated widgetCounts map so the caller
 * can persist them in state for the next batch.
 *
 * @param {Array}  posts        - Array of post objects to render
 * @param {number} startIndex   - Global index of posts[0] in the full list
 * @param {Object} widgetCounts - Current per-widget occurrence counters { widgetId: count }
 * @param {Object} widgets      - All widget data from feedState
 * @returns {{ html: string, widgetCounts: Object }}
 */
function buildPostsHTML(posts, startIndex, widgetCounts, widgets) {
  const counts = { ...widgetCounts };
  const items  = [];

  posts.forEach((post, localIndex) => {
    const globalIndex = startIndex + localIndex;

    items.push(createPostCard(post));

    // Check each widget slot — use (globalIndex + 1) so the first post is
    // position 1, matching the original interleaveFeed() behaviour.
    WIDGET_ORDER.forEach(({ id, every }) => {
      if ((globalIndex + 1) % every === 0 && hasWidgetData(widgets[id])) {
        counts[id] = (counts[id] || 0) + 1;
        items.push(createWidget(id, counts[id], widgets));
      }
    });
  });

  return { html: items.join(''), widgetCounts: counts };
}

/**
 * Build or re-build the sentinel element for a filter container.
 */
function createSentinel(filterType) {
  const sentinel       = document.createElement('div');
  sentinel.id          = `feed-sentinel-${filterType}`;
  sentinel.className   = 'feed-sentinel';
  sentinel.style.cssText = 'height: 1px; width: 100%;';
  return sentinel;
}

// ---------------------------------------------------------------------------
// Public rendering API
// ---------------------------------------------------------------------------

/**
 * Full render of a filter's feed container (replaces all existing content).
 * Resets the interleaving counters in state before rendering.
 */
export async function renderFeed(filterType) {
  const containerId = `feed-${filterType}`;
  const container   = document.getElementById(containerId);

  if (!container) {
    console.error(`Container ${containerId} not found`);
    return;
  }

  container.innerHTML = getLoadingSkeleton();

  const posts = feedState.getPosts(filterType);

  if (!posts || posts.length === 0) {
    container.innerHTML = `
      <div class="empty-state" style="padding: 3rem 1rem; text-align: center; color: var(--text-secondary);">
        <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin: 0 auto 1rem; opacity: 0.3;">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
          <polyline points="14 2 14 8 20 8"></polyline>
        </svg>
        <p>No posts found</p>
      </div>
    `;
    return;
  }

  // Reset counters — this is a clean render, not an append
  feedState.setRenderedPostCount(filterType, 0);
  feedState.setWidgetCounts(filterType, {});

  const widgets           = feedState.getWidgets();
  const { html, widgetCounts } = buildPostsHTML(posts, 0, {}, widgets);

  // Persist the counts and post count for future appended batches
  feedState.setWidgetCounts(filterType, widgetCounts);
  feedState.setRenderedPostCount(filterType, posts.length);

  container.innerHTML = html;

  // Attach sentinel if more posts exist
  const { hasMore } = feedState.getPaginationState(filterType);
  if (hasMore) {
    container.appendChild(createSentinel(filterType));
  }
}

/**
 * Append a new batch of posts to an existing feed container (infinite scroll).
 * Widgets are interleaved using the correct global index offset so they appear
 * at the same cadence as if everything were rendered in one shot.
 *
 * @param {string} filterType
 * @param {Array}  posts - New post objects to append
 */
export async function appendPostsToFeed(filterType, posts) {
  const containerId = `feed-${filterType}`;
  const container   = document.getElementById(containerId);

  if (!container) {
    console.error(`Container ${containerId} not found`);
    return;
  }

  // Remove the old sentinel before inserting new content
  const oldSentinel = document.getElementById(`feed-sentinel-${filterType}`);
  if (oldSentinel) oldSentinel.remove();

  // Retrieve current interleaving state
  const startIndex   = feedState.getRenderedPostCount(filterType);
  const widgetCounts = feedState.getWidgetCounts(filterType);
  const widgets      = feedState.getWidgets();

  const { html, widgetCounts: updatedCounts } = buildPostsHTML(
    posts,
    startIndex,
    widgetCounts,
    widgets
  );

  // Persist updated counts for the *next* batch
  feedState.setWidgetCounts(filterType, updatedCounts);
  feedState.incrementRenderedPostCount(filterType, posts.length);

  container.insertAdjacentHTML('beforeend', html);

  // Re-attach sentinel if more posts remain
  const { hasMore } = feedState.getPaginationState(filterType);
  if (hasMore) {
    container.appendChild(createSentinel(filterType));
  }
}

// ---------------------------------------------------------------------------
// Comment rendering
// ---------------------------------------------------------------------------

export function renderPostComments(comments) {
  return comments.map(comment => createCommentCard(comment)).join('');
}

export function appendCommentToUI(comment, parentId) {
  const commentsContainer = document.getElementById('comments-container');
  if (!commentsContainer) return;

  const commentHTML = createLocalCommentCard(comment, 'modal');

  if (parentId) {
    const parentCard = document.getElementById(`comment-card-modal-${parentId}`);
    if (parentCard) {
      let repliesContainer = parentCard.querySelector('.replies-container');
      if (!repliesContainer) {
        repliesContainer = document.createElement('div');
        repliesContainer.className = 'replies-container';
        parentCard.appendChild(repliesContainer);
      }
      repliesContainer.insertAdjacentHTML('beforeend', commentHTML);
    } else {
      commentsContainer.insertAdjacentHTML('afterbegin', commentHTML);
    }
  } else {
    commentsContainer.insertAdjacentHTML('afterbegin', commentHTML);
  }
}

// ---------------------------------------------------------------------------
// Thread rendering
// ---------------------------------------------------------------------------

export function renderThreadDetails(thread) {
  const modal = document.getElementById('thread-view-modal');
  if (!modal) return;
  modal.classList.remove('hidden');

  const modalBody = modal.querySelector('#thread-details-content');
  if (modalBody) {
    modalBody.innerHTML = renderThreadDetailsHTML(thread);
  }
}

// ---------------------------------------------------------------------------
// Tag pill rendering
// ---------------------------------------------------------------------------

export function renderSelectedForkTags() {
  const container = document.getElementById('selected-fork-tags');
  if (!container) return;
  container.innerHTML = feedState.getForkTags().map(tag =>
    `<span data-value="${tag}" class="tag-badge">
      ${tag}
      <button type="button" class="tag-remove" data-action="remove-fork-tag" data-value="${tag}">×</button>
    </span>`
  ).join('');
}

export function renderSelectedPostTags() {
  const container = document.getElementById('selected-post-tags');
  if (!container) return;
  container.innerHTML = feedState.getPostTags().map(tag =>
    `<span data-value="${tag}" class="tag-badge">
      ${tag}
      <button type="button" class="tag-remove" data-action="remove-post-tag" data-value="${tag}">×</button>
    </span>`
  ).join('');
}

export function renderSelectedThreadTags() {
  const container = document.getElementById('thread-selected-tags');
  if (!container) return;
  container.innerHTML = feedState.getThreadTags().map(tag =>
    `<span data-value="${tag}" class="tag-badge">
      ${tag}
      <button type="button" class="tag-remove" data-action="remove-thread-tag" data-value="${tag}">×</button>
    </span>`
  ).join('');
}

// ---------------------------------------------------------------------------
// Filter UI helpers
// ---------------------------------------------------------------------------

export function updateFilterButtons(filterType) {
  document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.filter === filterType);
  });
}

export function updateFeedContainerVisibility(filterType) {
  document.querySelectorAll('.posts-feed').forEach(c => c.classList.remove('active'));
  document.getElementById(`feed-${filterType}`)?.classList.add('active');
}

// ---------------------------------------------------------------------------
// Reaction / comment button helpers
// ---------------------------------------------------------------------------

export function updateReactionDisplay(postId, emoji, count, isReacted) {
  const post        = document.getElementById(`post-${postId}`);
  if (!post) return;
  const reactionBtn = post.querySelector('.reaction-btn');
  if (!reactionBtn) return;

  reactionBtn.classList.toggle('reacted', isReacted);

  const countSpan = reactionBtn.querySelector('.reaction-count');
  if (countSpan) countSpan.textContent = count > 0 ? count : '';

  reactionBtn.style.transform = 'scale(1.1)';
  setTimeout(() => { reactionBtn.style.transform = ''; }, 200);
}

export function showReactionMenu(event) {
  const reactionMenu = document.getElementById('reactionMenu');
  if (!reactionMenu) return;

  reactionMenu.classList.remove('hidden');
  const target    = event.target.closest('.reaction-btn') || event.target;
  const rect      = target.getBoundingClientRect();
  const menuWidth = 320;
  const menuHeight = 60;

  let left = rect.left + (rect.width / 2) - (menuWidth / 2);
  let top  = rect.top - menuHeight - 10;

  if (left < 10) left = 10;
  if (left + menuWidth > window.innerWidth - 10) left = window.innerWidth - menuWidth - 10;
  if (top  < 10) top = rect.bottom + 10;

  reactionMenu.style.left = `${left}px`;
  reactionMenu.style.top  = `${top}px`;
}

export function hideReactionMenu() {
  document.getElementById('reactionMenu')?.classList.add('hidden');
}

export function updateCommentLikeButton(commentId, isLiked, count = null) {
  const commentCard = document.querySelector(`[data-comment-id="${commentId}"]`);
  if (!commentCard) { console.warn(`Comment ${commentId} not found`); return; }

  const likeBtn = commentCard.querySelector('[data-action="toggle-comment-like"]');
  if (!likeBtn)  { console.warn(`Like button not found for comment ${commentId}`); return; }

  likeBtn.classList.toggle('liked', isLiked);

  const textSpan = likeBtn.querySelector('span:last-child');
  if (textSpan) {
    if (count !== null) {
      textSpan.textContent = count > 0 ? count : 'Like';
    } else {
      const match        = textSpan.textContent.trim().match(/(\d+)/);
      let   currentCount = match ? parseInt(match[1]) : 0;
      if (isLiked  && !likeBtn.dataset.previouslyLiked) currentCount++;
      if (!isLiked &&  likeBtn.dataset.previouslyLiked) currentCount = Math.max(0, currentCount - 1);
      textSpan.textContent = currentCount > 0 ? currentCount : 'Like';
    }
  }

  const icon = likeBtn.querySelector('.action-icon');
  if (icon) { icon.style.transform = 'scale(1.2)'; setTimeout(() => { icon.style.transform = ''; }, 200); }

  likeBtn.dataset.previouslyLiked = isLiked;
}

export function updateCommentHelpfulButton(commentId, isHelpful, count = null) {
  const commentCard = document.querySelector(`[data-comment-id="${commentId}"]`);
  if (!commentCard) { console.warn(`Comment ${commentId} not found`); return; }

  const helpfulBtn = commentCard.querySelector('[data-action="toggle-comment-helpful"]');
  if (!helpfulBtn) { console.warn(`Helpful button not found for comment ${commentId}`); return; }

  // Explicitly coerce to boolean so dataset round-trips don't corrupt the value
  const wasHelpful = helpfulBtn.dataset.previouslyHelpful === 'true';

  helpfulBtn.classList.toggle('helpful', isHelpful);

  const textSpan = helpfulBtn.querySelector('span:last-child');
  if (textSpan) {
    if (count !== null) {
      textSpan.textContent = count > 0 ? count : 'Helpful';
    } else {
      const match        = textSpan.textContent.trim().match(/(\d+)/);
      let   currentCount = match ? parseInt(match[1]) : 0;
      if ( isHelpful && !wasHelpful) currentCount++;
      if (!isHelpful &&  wasHelpful) currentCount = Math.max(0, currentCount - 1);
      textSpan.textContent = currentCount > 0 ? currentCount : 'Helpful';
    }
  }

  const icon = helpfulBtn.querySelector('.action-icon');
  if (icon) { icon.style.transform = 'scale(1.2) rotate(5deg)'; setTimeout(() => { icon.style.transform = ''; }, 200); }

  // Store as explicit string 'true'/'false' — never just assign the boolean
  // because dataset always converts to string, making `false` → truthy "false"
  helpfulBtn.dataset.previouslyHelpful = isHelpful ? 'true' : 'false';
}

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------

export function removePostFromDOM(postId) {
  document.getElementById(`post-${postId}`)?.remove();
}

export function removeCommentFromDOM(commentId) {
  document.getElementById(`comment-card-modal-${commentId}`)?.remove();
}

export function updatePreviousButton() {
  const states = feedState.getCommentModalHistory();
  const btn    = document.getElementById('previous-comment');
  if (!btn) { console.warn('Previous comment button not found'); return; }
  btn.classList.toggle('hidden', states.length === 0);
}

// ---------------------------------------------------------------------------
// Global exports (consumed by inline HTML handlers)
// ---------------------------------------------------------------------------

if (typeof window !== 'undefined') {
  window.updatePreviousButton          = updatePreviousButton;
  window.updateCommentLikeButton       = updateCommentLikeButton;
  window.updateCommentHelpfulButton    = updateCommentHelpfulButton;
}
