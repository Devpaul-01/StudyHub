/**
 * ============================================================================
 * FEED STATE MANAGEMENT
 * Updated: Page-based pagination replaced with cursor-based pagination.
 *
 * Key additions:
 *   - pagination[filter].nextCursor  — opaque string passed back to the API
 *   - pagination[filter].hasMore     — whether more pages exist
 *   - renderedPostCount[filter]      — total posts rendered so far (used by
 *                                      the renderer to place widgets correctly
 *                                      when a new batch is appended)
 *   - widgetCounts[filter]           — per-widget occurrence counter per filter
 *                                      (keeps widget cycling consistent across
 *                                      initial render + infinite-scroll batches)
 * ============================================================================
 */

import { FEED_FILTERS } from './feed.constants.js';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function _defaultPagination() {
  return { nextCursor: null, hasMore: true, loading: false };
}

function _defaultFilters() {
  return { all: false, department: false, trending: false, connections: false, unsolved: false };
}

function _defaultCounts() {
  return { all: 0, department: 0, trending: 0, connections: 0, unsolved: 0 };
}

function _defaultWidgetCounts() {
  return { all: {}, department: {}, trending: {}, connections: {}, unsolved: {} };
}

// ---------------------------------------------------------------------------
// FeedState
// ---------------------------------------------------------------------------

class FeedState {
  constructor() {
    // ── Post data by filter ─────────────────────────────────────────────────
    this.posts = {
      all:         [],
      department:  [],
      trending:    [],
      connections: [],
      unsolved:    [],
    };

    // ── Cursor-based pagination state per filter ────────────────────────────
    // nextCursor : string | null  — pass to next API call; null = first page
    // hasMore    : boolean        — false when the API says there are no more posts
    // loading    : boolean        — true while an in-flight request is pending
    this.pagination = {
      all:         _defaultPagination(),
      department:  _defaultPagination(),
      trending:    _defaultPagination(),
      connections: _defaultPagination(),
      unsolved:    _defaultPagination(),
    };

    // ── Widget data ─────────────────────────────────────────────────────────
    this.widgets = {
      suggestedConnections: [],
      popularTags:          {},
      risingStars:          [],
      openThreads:          [],
      studyBuddyMatches:    [],
      canHelp:              [],
      topBadgeEarners:      [],
    };

    this.loadedFilters = _defaultFilters();
    this.widgetsLoaded = false;
    this.currentFilter = FEED_FILTERS.ALL;

    // Batch 2: post_type ('', 'question', 'discussion', 'announcement',
    // 'resource', 'problem') is a second, independent filter the backend
    // already supports as an AND on top of the primary filter tab (see
    // GET /posts/feed's post_type param in
    // routes/student/posts/crud.py) — nothing in the UI ever sent it.
    // This isn't tracked per-combination the way the five primary filters
    // above are (that would mean 5x the cached post arrays and pagination
    // state for a "quick win"); instead, changing it just forces a
    // one-off re-fetch of whichever primary tab is currently active, with
    // post_type added to that request. See feed.events.js's
    // handlePostTypeFilterChange().
    this.postTypeFilter = '';

    // ── Widget interleaving continuity ──────────────────────────────────────
    // renderedPostCount: How many post cards have been inserted for each filter.
    //   Used to compute the correct global index offset when appending a new batch,
    //   so that widget-insertion positions remain consistent across pages.
    this.renderedPostCount = _defaultCounts();

    // widgetCounts: Per-filter occurrence counter for each widget type.
    //   Incremented every time a widget is inserted; passed to createWidget()
    //   so the widget can cycle its content across pages.
    this.widgetCounts = _defaultWidgetCounts();

    // ── UI / interaction state ──────────────────────────────────────────────
    this.selectedPostTags   = [];
    this.selectedThreadTags = [];
    this.selectedForkTags   = [];
    this.replyResources     = [];
    this.postResources      = [];
    this.threadAvatarResource = null;
    this.currentRefinement  = null;
    this.commentModalHistory = [];

    this.longPressTimer          = null;
    this.reactionBtn             = null;
    this.originalTitle           = null;
    this.originalContent         = null;
    this.viewObserver            = null;
    this.currentScrollResources  = [];
  }

  // ── Pagination ─────────────────────────────────────────────────────────────

  getPaginationState(filter) {
    return this.pagination[filter] || _defaultPagination();
  }

  /**
   * Merge a partial pagination update.
   * @param {string} filter
   * @param {{ nextCursor?: string|null, hasMore?: boolean, loading?: boolean }} state
   */
  setPaginationState(filter, state) {
    this.pagination[filter] = { ...this.pagination[filter], ...state };
  }

  setPaginationLoading(filter, loading) {
    this.pagination[filter].loading = loading;
  }

  resetPagination(filter) {
    this.pagination[filter]     = _defaultPagination();
    this.renderedPostCount[filter] = 0;
    this.widgetCounts[filter]      = {};
  }

  // ── Widget interleaving counters ───────────────────────────────────────────

  getRenderedPostCount(filter) {
    return this.renderedPostCount[filter] ?? 0;
  }

  setRenderedPostCount(filter, count) {
    this.renderedPostCount[filter] = count;
  }

  incrementRenderedPostCount(filter, by) {
    this.renderedPostCount[filter] = (this.renderedPostCount[filter] ?? 0) + by;
  }

  getWidgetCounts(filter) {
    return this.widgetCounts[filter] ?? {};
  }

  setWidgetCounts(filter, counts) {
    this.widgetCounts[filter] = { ...counts };
  }

  // ── Posts ──────────────────────────────────────────────────────────────────

  appendPosts(filter, newPosts) {
    this.posts[filter] = [...this.posts[filter], ...newPosts];
  }

  setPosts(filter, posts) {
    this.posts[filter]          = posts;
    this.loadedFilters[filter]  = true;
    this.renderedPostCount[filter] = 0;  // reset on fresh load
    this.widgetCounts[filter]      = {};
  }

  getPosts(filter)   { return this.posts[filter] || []; }
  getCurrentPosts()  { return this.posts; }

  isFilterLoaded(filter) { return this.loadedFilters[filter]; }

  // ── Current filter ─────────────────────────────────────────────────────────

  setCurrentFilter(filter) { this.currentFilter = filter; }
  getCurrentFilter()        { return this.currentFilter; }

  // Batch 2
  setPostTypeFilter(type)  { this.postTypeFilter = type || ''; }
  getPostTypeFilter()      { return this.postTypeFilter; }

  // ── Widgets ────────────────────────────────────────────────────────────────

  setWidgets(widgetData) {
    Object.assign(this.widgets, widgetData);
    this.widgetsLoaded = true;
  }
  getWidgets()      { return this.widgets; }
  areWidgetsLoaded() { return this.widgetsLoaded; }

  // ── Comment modal history ──────────────────────────────────────────────────

  setCommentModalHistory(history) { this.commentModalHistory.push(history); }
  getCommentModalHistory()         { return this.commentModalHistory; }
  clearCommentModalHistory()       { this.commentModalHistory = []; }

  // ── Tags ───────────────────────────────────────────────────────────────────

  addPostTag(tag)    { if (!this.selectedPostTags.includes(tag))   { this.selectedPostTags.push(tag);   return true; } return false; }
  addThreadTag(tag)  { if (!this.selectedThreadTags.includes(tag)) { this.selectedThreadTags.push(tag); return true; } return false; }
  addForkTag(tag)    { if (!this.selectedForkTags.includes(tag))   { this.selectedForkTags.push(tag);   return true; } return false; }

  removePostTag(tag)   { this.selectedPostTags   = this.selectedPostTags.filter(t => t !== tag); }
  removeThreadTag(tag) { this.selectedThreadTags = this.selectedThreadTags.filter(t => t !== tag); }
  removeForkTag(tag)   { this.selectedForkTags   = this.selectedForkTags.filter(t => t !== tag); }

  getPostTags()   { return this.selectedPostTags; }
  getThreadTags() { return this.selectedThreadTags; }
  getForkTags()   { return this.selectedForkTags; }

  clearPostTags()   { this.selectedPostTags   = []; }
  clearThreadTags() { this.selectedThreadTags = []; }
  clearForkTags()   { this.selectedForkTags   = []; }

  // ── Resources ──────────────────────────────────────────────────────────────

  addPostResource(resource)    { this.postResources.push(resource); }
  getPostResources()           { return this.postResources; }
  removePostResource(url)      { this.postResources = this.postResources.filter(r => r.url !== url); }
  clearPostResources()         { this.postResources = []; }

  addReplyResource(resource)   { this.replyResources.push(resource); }
  getReplyResources()          { return this.replyResources; }
  removeReplyResource(url)     { this.replyResources = this.replyResources.filter(r => r.url !== url); }
  clearReplyResources()        { this.replyResources = []; }

  // ── Misc UI state ──────────────────────────────────────────────────────────

  setCurrentRefinement(r) { this.currentRefinement = r; }
  getCurrentRefinement()   { return this.currentRefinement; }
  clearRefinement()        { this.currentRefinement = null; }

  clearLongPressTimer() {
    if (this.longPressTimer) { clearTimeout(this.longPressTimer); this.longPressTimer = null; }
  }

  getThreadResource()     { return this.threadAvatarResource; }
  setThreadAvatar(r)      { this.threadAvatarResource = r; }

  setReactionBtn(btn)    { this.reactionBtn = btn; }
  getReactionBtn()        { return this.reactionBtn; }

  setViewObserver(obs)   { this.viewObserver = obs; }
  getViewObserver()       { return this.viewObserver; }
  disconnectViewObserver() { if (this.viewObserver) this.viewObserver.disconnect(); }

  setOriginalTitle(t)    { this.originalTitle = t; }
  getOriginalTitle()      { return this.originalTitle; }
  setOriginalContent(c)  { this.originalContent = c; }
  getOriginalContent()    { return this.originalContent; }

  setCurrentScrollResources(r) { this.currentScrollResources = r; }
  getCurrentScrollResources()   { return this.currentScrollResources; }

  // ── Full reset ─────────────────────────────────────────────────────────────

  reset() {
    this.posts = {
      all:         [],
      department:  [],
      trending:    [],
      connections: [],
      unsolved:    [],
    };

    this.pagination = {
      all:         _defaultPagination(),
      department:  _defaultPagination(),
      trending:    _defaultPagination(),
      connections: _defaultPagination(),
      unsolved:    _defaultPagination(),
    };

    this.loadedFilters      = _defaultFilters();
    this.renderedPostCount  = _defaultCounts();
    this.widgetCounts       = _defaultWidgetCounts();
    this.widgetsLoaded      = false;
    this.currentFilter      = FEED_FILTERS.ALL;

    this.clearForkTags();
    this.clearReplyResources();
    this.clearRefinement();
    this.clearLongPressTimer();
  }
}

export const feedState = new FeedState();
