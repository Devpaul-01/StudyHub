/**
 * ============================================================================
 * FEED INITIALIZATION
 * Updated: Infinite scroll now uses cursor-based pagination.
 *          - Reads nextCursor from feedState
 *          - Passes it to loadPostsByFilter(filter, cursor)
 *          - Stores the returned nextCursor back in state
 * ============================================================================
 */

import { feedState } from './feed.state.js';
import { PULL_TO_REFRESH_THRESHOLD } from './feed.constants.js';
import * as feedApi from './feed.api.js';
import { setupUnifiedDelegation } from '../app.unified.js';
import { initVideoAutoplay } from './feed.video_autoplay.js';
import { initResourceViewer } from '../resource.viewer.js';

import {
  renderFeed,
  updateFilterButtons,
  updateFeedContainerVisibility,
  appendPostsToFeed,
} from './feed.render.js';
import { setupAllEventListeners } from './feed.events.js';
import { getLoadingSkeleton, navigateTo } from './feed.utils.js';

// ---------------------------------------------------------------------------
// Logging helper — prefixes every message so it's easy to filter in DevTools
// ---------------------------------------------------------------------------

const LOG_PREFIX = '[FeedInit]';

function log(msg, ...args)  { console.log(`${LOG_PREFIX} ${msg}`, ...args); }
function warn(msg, ...args) { console.warn(`${LOG_PREFIX} ⚠️  ${msg}`, ...args); }
function err(msg, ...args)  { console.error(`${LOG_PREFIX} ❌ ${msg}`, ...args); }
function group(label)       { console.group(`${LOG_PREFIX} ${label}`); }
function groupEnd()         { console.groupEnd(); }

// ---------------------------------------------------------------------------
// IntersectionObserver registry (one observer per filter)
// ---------------------------------------------------------------------------

const infiniteScrollObservers = new Map();

function cleanupObserver(filter) {
  const observer = infiniteScrollObservers.get(filter);
  if (observer) {
    observer.disconnect();
    infiniteScrollObservers.delete(filter);
    log(`Observer disconnected for filter: "${filter}"`);
  }
}

// ---------------------------------------------------------------------------
// Infinite scroll
// ---------------------------------------------------------------------------

function setupInfiniteScroll() {
  const currentFilter = feedState.getCurrentFilter();
  log(`Setting up infinite scroll for filter: "${currentFilter}"`);
  cleanupObserver(currentFilter);

  const sentinel = document.getElementById(`feed-sentinel-${currentFilter}`);
  if (!sentinel) {
    warn(`Sentinel element #feed-sentinel-${currentFilter} not found — infinite scroll disabled for this filter`);
    return;
  }

  const observer = new IntersectionObserver(
    async (entries) => {
      if (!entries[0].isIntersecting) return;

      const filter          = feedState.getCurrentFilter();
      const paginationState = feedState.getPaginationState(filter);

      log(`Sentinel intersected for filter: "${filter}"`, {
        loading:    paginationState.loading,
        hasMore:    paginationState.hasMore,
        nextCursor: paginationState.nextCursor,
      });

      if (paginationState.loading) {
        log(`Skipping — already loading for "${filter}"`);
        return;
      }
      if (!paginationState.hasMore) {
        log(`Skipping — no more posts for "${filter}"`);
        return;
      }

      log(`📥 Fetching next page for "${filter}" (cursor: ${paginationState.nextCursor})`);
      feedState.setPaginationLoading(filter, true);

      try {
        const response = await feedApi.loadPostsByFilter(filter, paginationState.nextCursor);
        log(`API response for "${filter}":`, {
          postCount:  response?.posts?.length,
          nextCursor: response?.nextCursor,
          hasMore:    response?.hasMore,
        });

        const { posts, nextCursor, hasMore } = response;

        if (!Array.isArray(posts)) {
          throw new Error(`loadPostsByFilter returned non-array posts: ${JSON.stringify(posts)}`);
        }

        if (posts.length > 0) {
          feedState.appendPosts(filter, posts);
          await appendPostsToFeed(filter, posts);
          feedState.setPaginationState(filter, { nextCursor, hasMore, loading: false });
          log(`✅ Appended ${posts.length} posts. Has more: ${hasMore}`);

          if (!hasMore) {
            log(`End of feed reached for "${filter}" — removing sentinel`);
            sentinel.remove();
            cleanupObserver(filter);
          }
        } else {
          warn(`Empty batch returned for "${filter}" — treating as end of feed`);
          feedState.setPaginationState(filter, { nextCursor: null, hasMore: false, loading: false });
          sentinel.remove();
          cleanupObserver(filter);
        }
      } catch (error) {
        err('Infinite scroll fetch failed:', error);
        feedState.setPaginationLoading(filter, false);
        if (typeof showToast === 'function') showToast('Failed to load more posts', 'error');
      }
    },
    {
      root:       null,
      rootMargin: '200px',
      threshold:  0,
    }
  );

  observer.observe(sentinel);
  infiniteScrollObservers.set(currentFilter, observer);
  log(`✅ Infinite scroll observer attached to sentinel for "${currentFilter}"`);
}

// ---------------------------------------------------------------------------
// Initial data load
// ---------------------------------------------------------------------------

async function loadInitialData() {
  group('loadInitialData');
  log('Calling feedApi.loadInitialFeedData()...');

  try {
    const feedData = await feedApi.loadInitialFeedData();

    log('Raw feedData returned from API:', feedData);

    // ── Validate the shape we depend on ────────────────────────────────────
    if (!feedData) {
      throw new Error('feedApi.loadInitialFeedData() returned null/undefined');
    }
    if (!feedData.all) {
      throw new Error(`feedData.all is missing. Keys present: ${Object.keys(feedData).join(', ')}`);
    }
    if (!feedData.cursors || !('all' in feedData.cursors)) {
      throw new Error(`feedData.cursors.all is missing. cursors value: ${JSON.stringify(feedData.cursors)}`);
    }
    if (!Array.isArray(feedData.all)) {
      throw new Error(`feedData.all is not an array — got: ${typeof feedData.all}`);
    }
    // ───────────────────────────────────────────────────────────────────────

    log(`feedData.all has ${feedData.all.length} post(s)`);

    feedState.setPosts('all', feedData.all);
    log('feedState.setPosts("all") called');

    const { nextCursor, hasMore } = feedData.cursors.all;
    log('Pagination state:', { nextCursor, hasMore });
    feedState.setPaginationState('all', { nextCursor, hasMore, loading: false });

    // Load widgets in parallel — non-blocking
    if (!feedState.areWidgetsLoaded()) {
      log('Kicking off non-blocking widget load...');
      feedApi.loadWidgetData()
        .then(widgets => {
          log('Widgets loaded:', widgets);
          feedState.setWidgets(widgets);
        })
        .catch(e => warn('Widget load failed (non-critical):', e));
    } else {
      log('Widgets already loaded — skipping');
    }
  } catch (error) {
    err('loadInitialData failed:', error);
    throw error;   // re-throw so initFeed/DOMContentLoaded can catch it
  } finally {
    groupEnd();
  }
}

// ---------------------------------------------------------------------------
// Filter switching
// ---------------------------------------------------------------------------

async function filterFeed(type) {
  group(`filterFeed("${type}")`);
  const oldFilter = feedState.getCurrentFilter();
  log(`Switching from "${oldFilter}" → "${type}"`);

  if (oldFilter !== type) cleanupObserver(oldFilter);

  updateFilterButtons(type);
  updateFeedContainerVisibility(type);

  // If already loaded, render from cache
  if (feedState.isFilterLoaded(type)) {
    log(`"${type}" already cached — rendering from state`);
    feedState.setCurrentFilter(type);
    await renderFeed(type);
    setupInfiniteScroll();
    groupEnd();
    return;
  }

  // First visit — show skeleton then fetch
  const container = document.getElementById(`feed-${type}`);
  if (container) {
    container.innerHTML = getLoadingSkeleton();
    log(`Skeleton injected into #feed-${type}`);
  } else {
    warn(`Container #feed-${type} not found in DOM`);
  }

  try {
    log(`Fetching posts for filter "${type}"...`);
    const response = await feedApi.loadPostsByFilter(type);
    log(`API response for filter "${type}":`, {
      postCount:  response?.posts?.length,
      nextCursor: response?.nextCursor,
      hasMore:    response?.hasMore,
    });

    if (!Array.isArray(response?.posts)) {
      throw new Error(`loadPostsByFilter("${type}") returned non-array posts: ${JSON.stringify(response)}`);
    }

    const { posts, nextCursor, hasMore } = response;
    feedState.setPosts(type, posts);
    feedState.setPaginationState(type, { nextCursor, hasMore, loading: false });
    log(`State updated for "${type}": ${posts.length} posts`);
  } catch (error) {
    err(`Failed to load filter "${type}":`, error);
    if (typeof showToast === 'function') showToast(`Failed to load ${type} feed`, 'error');
    // Restore previous tab
    updateFilterButtons(oldFilter);
    updateFeedContainerVisibility(oldFilter);
    feedState.setCurrentFilter(oldFilter);
    groupEnd();
    return;
  }

  feedState.setCurrentFilter(type);
  log(`Rendering feed for "${type}"...`);
  await renderFeed(type);
  setupInfiniteScroll();
  groupEnd();
}

// ---------------------------------------------------------------------------
// Pull-to-refresh
// ---------------------------------------------------------------------------

class PullToRefresh {
  constructor() {
    this.startY      = 0;
    this.currentY    = 0;
    this.pulling     = false;
    this.threshold   = PULL_TO_REFRESH_THRESHOLD;
    this.refreshing  = false;
    this.element     = document.getElementById('pullToRefresh');
    this.contentArea = document.querySelector('.content-area');

    if (!this.element)     warn('PullToRefresh: #pullToRefresh element not found');
    if (!this.contentArea) warn('PullToRefresh: .content-area element not found');

    this.init();
  }

  init() {
    if (!this.element || !this.contentArea) {
      warn('PullToRefresh skipped — required elements missing');
      return;
    }
    this.contentArea.addEventListener('touchstart', this.onTouchStart.bind(this), { passive: true });
    this.contentArea.addEventListener('touchmove',  this.onTouchMove.bind(this),  { passive: false });
    this.contentArea.addEventListener('touchend',   this.onTouchEnd.bind(this),   { passive: true });
    this.contentArea.addEventListener('mousedown',  this.onMouseDown.bind(this));
    this.contentArea.addEventListener('mousemove',  this.onMouseMove.bind(this));
    this.contentArea.addEventListener('mouseup',    this.onMouseUp.bind(this));
    log('PullToRefresh listeners attached');
  }

  onTouchStart(e) {
    if (this.refreshing || this.contentArea.scrollTop > 0) return;
    this.startY  = e.touches[0].clientY;
    this.pulling = true;
  }

  onTouchMove(e) {
    if (!this.pulling || this.refreshing) return;
    this.currentY = e.touches[0].clientY;
    const diff    = this.currentY - this.startY;
    if (diff > 0 && this.contentArea.scrollTop === 0) {
      e.preventDefault();
      const pullDistance = Math.min(diff * 0.6, this.threshold * 1.5);
      this.element.style.transform = `translateY(${pullDistance - 80}px)`;
      this.element.classList.toggle('pulling', pullDistance >= this.threshold * 1.2);
    }
  }

  onTouchEnd() {
    if (!this.pulling || this.refreshing) return;
    if (this.currentY - this.startY >= this.threshold * 1.25) this.refresh();
    else this.reset();
    this.pulling = false;
  }

  onMouseDown(e) {
    if (this.refreshing || this.contentArea.scrollTop > 0) return;
    this.startY  = e.clientY;
    this.pulling = true;
  }

  onMouseMove(e) {
    if (!this.pulling || this.refreshing) return;
    this.currentY = e.clientY;
    const diff    = this.currentY - this.startY;
    if (diff > 0 && this.contentArea.scrollTop === 0) {
      e.preventDefault();
      const pullDistance = Math.min(diff, this.threshold * 1.5);
      this.element.style.transform = `translateY(${pullDistance - 80}px)`;
      this.element.classList.toggle('pulling', pullDistance >= this.threshold);
    }
  }

  onMouseUp() {
    if (!this.pulling || this.refreshing) return;
    if (this.currentY - this.startY >= this.threshold * 1.25) this.refresh();
    else this.reset();
    this.pulling = false;
  }

  async refresh() {
    log('Pull-to-refresh triggered');
    this.refreshing = true;
    this.element.classList.add('refreshing');
    this.element.style.transform = 'translateY(0)';

    try {
      log('Tearing down all observers...');
      infiniteScrollObservers.forEach(obs => obs.disconnect());
      infiniteScrollObservers.clear();

      log('Resetting feedState...');
      feedState.reset();

      await loadInitialData();

      const currentFilter = feedState.getCurrentFilter();
      log(`Current filter after reset: "${currentFilter}"`);

      if (currentFilter !== 'all') {
        log(`Re-fetching posts for active non-"all" filter: "${currentFilter}"`);
        const { posts, nextCursor, hasMore } = await feedApi.loadPostsByFilter(currentFilter);
        feedState.setPosts(currentFilter, posts);
        feedState.setPaginationState(currentFilter, { nextCursor, hasMore, loading: false });
        log(`Re-fetched ${posts.length} posts for "${currentFilter}"`);
      }

      log('Re-rendering feed...');
      await renderFeed(feedState.getCurrentFilter());
      setupAllEventListeners();
      setupInfiniteScroll();
      log('✅ Pull-to-refresh complete');
    } catch (error) {
      err('Pull-to-refresh failed:', error);
      if (typeof showToast === 'function') showToast('Failed to refresh feed', 'error');
    }

    setTimeout(() => { this.reset(); this.refreshing = false; }, 500);
  }

  reset() {
    this.element.classList.remove('pulling', 'refreshing');
    this.element.style.transform = 'translateY(-100%)';
  }
}

// ---------------------------------------------------------------------------
// Feed init entry point
// ---------------------------------------------------------------------------

async function initFeed() {
  group('initFeed');
  log('Starting...');

  // ── Guard: #feed-all must exist before we can do anything ────────────────
  const container = document.getElementById('feed-all');
  if (!container) {
    err('#feed-all container not found in DOM — cannot initialise feed. Check your HTML.');
    groupEnd();
    throw new Error('#feed-all not found');
  }
  log('#feed-all container found');
  container.innerHTML = getLoadingSkeleton();
  log('Skeleton injected into #feed-all');

  try {
    log('Calling loadInitialData()...');
    await loadInitialData();

    // Verify posts landed in state before attempting render
    const posts = feedState.getPosts('all');
    log(`Posts in state for "all" after loadInitialData: ${Array.isArray(posts) ? posts.length : 'NOT AN ARRAY — ' + JSON.stringify(posts)}`);

    if (!Array.isArray(posts) || posts.length === 0) {
      warn('No posts in state for "all" — renderFeed will likely produce an empty feed');
    }

    log('Calling renderFeed("all")...');
    await renderFeed('all');
    log('✅ renderFeed("all") completed');
  } catch (error) {
    err('initFeed failed:', error);
    if (container) container.innerHTML = `<p class="feed-error">Failed to load posts. Please refresh.<br><small>${error.message}</small></p>`;
    if (typeof showToast === 'function') showToast('Failed to load feed: ' + error.message, 'error');
    throw error;   // propagate so DOMContentLoaded reports it too
  } finally {
    groupEnd();
  }
}

// ---------------------------------------------------------------------------
// DOMContentLoaded bootstrap
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', async function () {
  group('=== LearnHub Feed Initialization ===');
  log('DOMContentLoaded fired');

  try {
    // ── Pre-flight checks ──────────────────────────────────────────────────
    log('Running pre-flight checks...');

    if (typeof api === 'undefined') {
      throw new Error('Global `api` helper is not defined — ensure api.js loads before feed_init.js');
    }
    log('✅ api helper present');

    if (typeof showToast === 'undefined') {
      warn('showToast not defined — falling back to alert()');
      window.showToast = (msg) => alert(msg);
    } else {
      log('✅ showToast present');
    }

    // Verify feedApi methods we depend on exist
    const requiredApiMethods = ['loadInitialFeedData', 'loadPostsByFilter', 'loadWidgetData'];
    for (const method of requiredApiMethods) {
      if (typeof feedApi[method] !== 'function') {
        throw new Error(`feedApi.${method} is not a function — check feed.api.js exports`);
      }
      log(`✅ feedApi.${method} present`);
    }

    // Verify feedState methods we depend on exist
    const requiredStateMethods = ['getPosts', 'setPosts', 'getCurrentFilter', 'setCurrentFilter',
                                  'getPaginationState', 'setPaginationState', 'setPaginationLoading',
                                  'appendPosts', 'isFilterLoaded', 'areWidgetsLoaded',
                                  'setWidgets', 'reset'];
    for (const method of requiredStateMethods) {
      if (typeof feedState[method] !== 'function') {
        warn(`feedState.${method} is not a function — may cause errors later`);
      }
    }

    // ── Core initialization ────────────────────────────────────────────────
    log('Calling initFeed()...');
    await initFeed();
    log('✅ initFeed() done');

    log('Calling setupAllEventListeners()...');
    setupAllEventListeners();
    log('✅ setupAllEventListeners() done');

    log('Calling setupUnifiedDelegation()...');
    setupUnifiedDelegation();
    log('✅ setupUnifiedDelegation() done');

    log('Calling setupInfiniteScroll()...');
    setupInfiniteScroll();
    log('✅ setupInfiniteScroll() done');

    log('Calling initVideoAutoplay()...');
    initVideoAutoplay();
    log('✅ initVideoAutoplay() done');

    log('Calling initResourceViewer()...');
    initResourceViewer();
    log('✅ initResourceViewer() done');

    log('Creating PullToRefresh instance...');
    window.pullToRefresh = new PullToRefresh();
    log('✅ PullToRefresh ready');

    log('=== ✅ All systems initialized ===');

    // Batch 1 fix: GET /student/profile/me used to render a standalone
    // template (profile/profile.html) that didn't exist, so hitting that
    // URL directly 500'd. feed2.html is the single-page app shell for the
    // whole site (this very file), and it already has a fully working
    // "my profile" view — the #profile section navigateTo() switches to.
    // Rather than build a second, separate profile page that would drift
    // out of sync with this one, /profile/me now serves this same shell
    // (see routes/student/profile.py), and this one line opens it
    // straight to the profile tab when that's the URL that was loaded —
    // every other page gets the normal feed view, unaffected.
    if (window.location.pathname === '/student/profile/me') {
      navigateTo('profile');
    }
  } catch (error) {
    err('=== INITIALIZATION FAILED ===', error);
    alert('Failed to load feed: ' + error.message);
  } finally {
    groupEnd();
  }
});

// ---------------------------------------------------------------------------
// Expose to HTML onclick handlers
// ---------------------------------------------------------------------------

window.filterFeed          = filterFeed;
window.setupInfiniteScroll = setupInfiniteScroll;
