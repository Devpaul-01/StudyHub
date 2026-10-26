/**
 * ============================================================================
 * SEARCH EVENTS — search.events.js
 * ============================================================================
 */

import { searchAPI } from './search.api.js';
import { searchState } from './search.state.js';
import {
  resultCard, globalResultsSections, discoverySections,
  suggestionsList, recentSearchesChips,
} from './search.templates.js';

function $(id) { return document.getElementById(id); }

// ── Debounce helper for the suggestions-as-you-type box ──────────────────
let suggestDebounce = null;

export function handleSearchInput(value) {
  searchState.setQuery(value);
  clearTimeout(suggestDebounce);

  if (!value || value.trim().length < 2) {
    hideSuggestions();
    if (!value) showRecentSearches();
    return;
  }
  hideRecentSearches();

  suggestDebounce = setTimeout(async () => {
    try {
      const response = await searchAPI.suggestions(value.trim(), searchState.type === 'all' ? 'all' : searchState.type);
      const suggestions = response?.data?.suggestions || [];
      searchState.setSuggestions(suggestions);
      renderSuggestions(suggestions);
    } catch (error) {
      console.error('Suggestions error:', error);
    }
  }, 300);
}

function renderSuggestions(suggestions) {
  const el = $('search-suggestions');
  if (!el) return;
  if (!suggestions.length) {
    el.classList.add('hidden');
    return;
  }
  el.innerHTML = suggestionsList(suggestions);
  el.classList.remove('hidden');
}

function hideSuggestions() {
  $('search-suggestions')?.classList.add('hidden');
}

// ── Recent searches ────────────────────────────────────────────────────────

export async function showRecentSearches() {
  const el = $('search-recent');
  if (!el) return;
  try {
    const response = await searchAPI.getHistory();
    const queries = response?.data?.recent_searches || [];
    searchState.setRecentSearches(queries);
    $('search-recent-list').innerHTML = recentSearchesChips(queries);
    el.classList.remove('hidden');
  } catch (error) {
    console.error('Recent searches error:', error);
  }
}

function hideRecentSearches() {
  $('search-recent')?.classList.add('hidden');
}

export async function handleClearHistory() {
  try {
    await searchAPI.clearHistory();
    searchState.setRecentSearches([]);
    $('search-recent-list').innerHTML = recentSearchesChips([]);
  } catch (error) {
    console.error('Clear history error:', error);
  }
}

// ── Type switching ────────────────────────────────────────────────────────

export function handleSetType(type) {
  searchState.setType(type);

  document.querySelectorAll('.search-type-tab').forEach(tab => {
    tab.classList.toggle('active', tab.dataset.type === type);
  });

  const postTypeGroup = $('search-filter-post-type-group');
  const classLevelGroup = $('search-filter-class-level-group');
  if (postTypeGroup)   postTypeGroup.style.display   = (type === 'posts' || type === 'all') ? '' : 'none';
  if (classLevelGroup) classLevelGroup.style.display = (type === 'users' || type === 'all') ? '' : 'none';

  if (searchState.query.trim()) {
    runSearch();
  } else if (type !== 'all') {
    // Discovery state is only meaningful for "All"; switching to a
    // specific type with no query yet just clears results rather than
    // guessing what the user wants to browse.
    $('search-results').innerHTML = `<div class="search-empty-state">Type something to search ${type}.</div>`;
  } else {
    loadDiscovery();
  }
}

// ── Filters ────────────────────────────────────────────────────────────────

export async function populateFilterDropdowns() {
  try {
    const [deptRes, classRes] = await Promise.all([
      searchAPI.departments(),
      searchAPI.classLevels(),
    ]);
    const departments = deptRes?.data?.departments || [];
    const classLevels  = classRes?.data?.class_levels || [];

    const deptSelect = $('search-filter-department');
    if (deptSelect) {
      deptSelect.innerHTML = '<option value="">Any</option>' +
        departments.map(d => `<option value="${d.name}">${d.name} (${d.student_count})</option>`).join('');
    }
    const classSelect = $('search-filter-class-level');
    if (classSelect) {
      classSelect.innerHTML = '<option value="">Any</option>' +
        classLevels.map(c => `<option value="${c.name}">${c.name} (${c.student_count})</option>`).join('');
    }
  } catch (error) {
    console.error('Populate filters error:', error);
  }
}

export function handleToggleFilters() {
  $('search-filters-drawer')?.classList.toggle('hidden');
}

export function handleApplyFilters() {
  searchState.setFilter('department', $('search-filter-department')?.value || '');
  searchState.setFilter('classLevel', $('search-filter-class-level')?.value || '');
  searchState.setFilter('postType',   $('search-filter-post-type')?.value || '');
  searchState.setFilter('sort',        $('search-filter-sort')?.value || 'relevance');
  searchState.setPage(1);
  runSearch();
}

// ── Core search execution ─────────────────────────────────────────────────

export async function runSearch(opts = {}) {
  const query = searchState.query.trim();
  if (!query) {
    loadDiscovery();
    return;
  }

  hideSuggestions();
  hideRecentSearches();

  const resultsEl = $('search-results');
  if (!opts.append && resultsEl) {
    resultsEl.innerHTML = `<div class="search-empty-state">Searching…</div>`;
  }
  searchState.setLoading(true);

  try {
    if (searchState.type === 'all') {
      const response = await searchAPI.global(query);
      const results = response?.data?.results || { users: [], posts: [], threads: [] };
      searchState.setResults(results);
      if (resultsEl) resultsEl.innerHTML = globalResultsSections(results);
    } else {
      const filters = searchState.getFilters();
      const page = opts.append ? searchState.page + 1 : 1;
      let response;

      if (searchState.type === 'users') {
        response = await searchAPI.users({
          q: query, department: filters.department, classLevel: filters.classLevel,
          sort: filters.sort, page,
        });
      } else if (searchState.type === 'posts') {
        response = await searchAPI.posts({
          q: query, postType: filters.postType, department: filters.department,
          sort: filters.sort, page,
        });
      } else if (searchState.type === 'threads') {
        response = await searchAPI.threads({ q: query, department: filters.department, page });
      }

      const data = response?.data || {};
      const items = data[searchState.type] || [];
      searchState.setResults(data, opts.append);
      searchState.setPage(page);
      searchState.setHasMore(!!data.pagination?.has_next);

      renderTypedResults(opts.append);
    }

    // Only save a genuinely new search to history, not every keystroke or
    // "load more" page.
    if (!opts.append && !opts.fromHistory) {
      searchAPI.saveToHistory(query).catch(() => {}); // fire-and-forget
    }
  } catch (error) {
    console.error('Search error:', error);
    if (resultsEl && !opts.append) {
      resultsEl.innerHTML = `<div class="search-empty-state">Something went wrong. Please try again.</div>`;
    }
  } finally {
    searchState.setLoading(false);
  }
}

function renderTypedResults(append) {
  const resultsEl = $('search-results');
  if (!resultsEl) return;

  const items = searchState.results?.[searchState.type] || [];
  const cardsHtml = items.map(item => resultCard(searchState.type, item)).join('');
  const body = cardsHtml || `<div class="search-empty-state">No results found. Try a different search.</div>`;
  const loadMoreBtn = searchState.hasMore
    ? `<button class="search-load-more" data-action="search-load-more">Load more</button>` : '';

  if (append) {
    const existingBtn = resultsEl.querySelector('.search-load-more');
    existingBtn?.remove();
    resultsEl.insertAdjacentHTML('beforeend', cardsHtml + loadMoreBtn);
  } else {
    resultsEl.innerHTML = body + loadMoreBtn;
  }
}

export function handleLoadMore() {
  runSearch({ append: true });
}

export async function loadDiscovery() {
  const resultsEl = $('search-results');
  if (resultsEl) resultsEl.innerHTML = `<div class="search-empty-state">Loading…</div>`;
  try {
    const response = await searchAPI.forYou();
    if (resultsEl) resultsEl.innerHTML = discoverySections(response?.data || {});
  } catch (error) {
    console.error('Discovery error:', error);
    if (resultsEl) resultsEl.innerHTML = `<div class="search-empty-state">Type something to search people, posts, and threads.</div>`;
  }
}

// ── Picking a suggestion or a recent search ───────────────────────────────

export function handlePickSuggestion(text, type) {
  const input = $('search-page-input');
  if (input) input.value = text;
  searchState.setQuery(text);
  hideSuggestions();
  if (type && type !== 'all' && ['users', 'posts', 'threads'].includes(type)) {
    handleSetType(type);
  } else {
    runSearch();
  }
}

export function handlePickRecent(query) {
  const input = $('search-page-input');
  if (input) input.value = query;
  searchState.setQuery(query);
  runSearch({ fromHistory: true });
}

// ── Navigation out of the search page ─────────────────────────────────────
//
// There is no single-post or single-thread detail PAGE anywhere in this
// app to link to — confirmed by checking: the main SPA (feed2.html)
// shows posts entirely inline within the feed itself (reactions/comments
// expand in place), with no dedicated "open this one post" route or JS
// function anywhere in static/js/feed/, and no post notification link
// anywhere in the backend follows any URL convention that would resolve
// to one either. Rather than link to a URL that doesn't actually show
// the post (which would be a broken/misleading "deep link"), search
// results for posts and threads open a lightweight read-only preview
// modal right here on the search page, built from the real
// GET /posts/<id> and GET /threads/<id> endpoints — both of which
// already existed and already return everything a preview needs.

export function openProfile(username) {
  window.location.href = `/student/profile/${encodeURIComponent(username)}`;
}

export async function openPost(postId) {
  const { showPostPreview } = await import('./search.preview.js');
  await showPostPreview(postId);
}

export async function openThread(threadId) {
  const { showThreadPreview } = await import('./search.preview.js');
  await showThreadPreview(threadId);
}
