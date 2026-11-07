/**
 * ============================================================================
 * SEARCH API — search.api.js
 *
 * Batch 4: the old static/js/search/ directory contained four files that
 * were never functional — syntax errors (missing `async`, a broken
 * import path), a typo'd endpoint (/search/sugguestions), a missing `&`
 * in a query string, and DOM targets (#advanced-search-modal,
 * #global-search-bar) that don't exist anywhere in the app. Nothing
 * loaded any of them. This is a full rewrite, built for the new
 * dedicated /student/search page (templates/search.html) rather than
 * the in-SPA-modal approach those old drafts assumed.
 *
 * Uses the global `api` client from core/api.js (loaded by base.html
 * before this module runs — see search.html's extra_js block).
 *
 * Every endpoint here already existed on the backend
 * (routes/student/search.py) before Batch 4 — nothing in the frontend
 * called any of them. Field names and response shapes below were
 * confirmed directly against routes/student/search.py and
 * services/search_service.py, not assumed from a prior summary of them.
 * ============================================================================
 */

export const searchAPI = {
  /**
   * GET /search/global — top results across users/posts/threads at once.
   * Response: { data: { query, results: {users, posts, threads}, counts } }
   * NOTE: results live under `results`, not `all` — the service function
   * internally calls that key `all`, but the route renames it before
   * sending the response.
   */
  async global(query, limit = 5) {
    const params = new URLSearchParams({ q: query, limit });
    return await api.get(`/search/global?${params}`);
  },

  /**
   * GET /search/users
   * Response: { data: { users: [...], pagination: {...}, filters_applied } }
   */
  async users({ q = '', department, classLevel, skills, reputationMin, sort, page = 1, perPage = 20 } = {}) {
    const params = new URLSearchParams({ q, page, per_page: perPage });
    if (department)    params.set('department', department);
    if (classLevel)    params.set('class_level', classLevel);
    if (skills?.length) params.set('skills', skills.join(','));
    if (reputationMin) params.set('reputation_min', reputationMin);
    if (sort)           params.set('sort', sort);
    return await api.get(`/search/users?${params}`);
  },

  /** GET /search/users/top-contributors */
  async topContributors({ department, period, limit = 10 } = {}) {
    const params = new URLSearchParams({ limit });
    if (department) params.set('department', department);
    if (period)      params.set('period', period);
    return await api.get(`/search/users/top-contributors?${params}`);
  },

  /**
   * GET /search/posts
   * Response: { data: { posts: [...], pagination: {...}, filters_applied } }
   */
  async posts({ q = '', postType, department, tags, solved, dateFrom, dateTo, sort, page = 1, perPage = 20 } = {}) {
    const params = new URLSearchParams({ q, page, per_page: perPage });
    if (postType)      params.set('post_type', postType);
    if (department)    params.set('department', department);
    if (tags?.length)  params.set('tags', tags.join(','));
    if (solved !== undefined && solved !== null) params.set('solved', solved);
    if (dateFrom)       params.set('date_from', dateFrom);
    if (dateTo)         params.set('date_to', dateTo);
    if (sort)            params.set('sort', sort);
    return await api.get(`/search/posts?${params}`);
  },

  /** GET /search/posts/unanswered */
  async unansweredPosts({ department, page = 1, perPage = 20 } = {}) {
    const params = new URLSearchParams({ page, per_page: perPage });
    if (department) params.set('department', department);
    return await api.get(`/search/posts/unanswered?${params}`);
  },

  /** GET /search/posts/trending */
  async trendingPosts({ department, limit = 10 } = {}) {
    const params = new URLSearchParams({ limit });
    if (department) params.set('department', department);
    return await api.get(`/search/posts/trending?${params}`);
  },

  /**
   * GET /search/threads
   * Response: { data: { threads: [...], pagination: {...} } } — summaries
   * only (no full member lists — see GET /threads/<id>/members for that).
   */
  async threads({ q = '', department, isOpen, hasSpace, page = 1, perPage = 20 } = {}) {
    const params = new URLSearchParams({ q, page, per_page: perPage });
    if (department) params.set('department', department);
    if (isOpen !== undefined && isOpen !== null) params.set('is_open', isOpen);
    if (hasSpace !== undefined && hasSpace !== null) params.set('has_space', hasSpace);
    return await api.get(`/search/threads?${params}`);
  },

  /** GET /search/threads/open — response: { data: { open_threads: [...], total } } */
  async openThreads({ department, limit = 20 } = {}) {
    const params = new URLSearchParams({ limit });
    if (department) params.set('department', department);
    return await api.get(`/search/threads/open?${params}`);
  },

  /**
   * GET /search/suggestions — autocomplete. Response:
   * { data: { query, suggestions: [{type, id, text, display, ...}] } }
   */
  async suggestions(query, type = 'all') {
    const params = new URLSearchParams({ q: query, type });
    return await api.get(`/search/suggestions?${params}`);
  },

  /** GET /search/tags/popular — response: { data: { popular_tags: [...], detailed: [...] } } */
  async popularTags(limit = 30) {
    return await api.get(`/search/tags/popular?limit=${limit}`);
  },

  /** GET /search/filters/departments — response: { data: { departments: [{name, student_count}] } } */
  async departments() {
    return await api.get('/search/filters/departments');
  },

  /** GET /search/filters/class-levels — response: { data: { class_levels: [{name, student_count}] } } */
  async classLevels() {
    return await api.get('/search/filters/class-levels');
  },

  /**
   * GET /search/discovery/for-you — personalized, empty-query landing
   * state. Response: { data: { recommended_posts, recommended_threads,
   * recommended_users, based_on } } — deliberately lighter fields than
   * the main search endpoints (no excerpt, no avatar on posts/threads;
   * see search.templates.js's renderDiscoverySection for why it has its
   * own, separate card renderer rather than reusing the main one).
   */
  async forYou() {
    return await api.get('/search/discovery/for-you');
  },

  /** GET /search/history — response: { data: { recent_searches: [string, ...] } } */
  async getHistory() {
    return await api.get('/search/history');
  },

  /** POST /search/history — body: { query } */
  async saveToHistory(query) {
    return await api.post('/search/history', { query });
  },

  /** DELETE /search/history */
  async clearHistory() {
    return await api.delete('/search/history');
  },
};
