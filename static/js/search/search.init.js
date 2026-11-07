/**
 * ============================================================================
 * SEARCH INIT — search.init.js
 *
 * Entry point for the search page (templates/search.html). Reads the
 * page's query string on load — base.html's #global-search bar already
 * navigates to /student/search?q=... on Enter, so this makes that
 * actually show results instead of an empty page. Also supports ?type=,
 * ?tags=, and ?department= for deep links (the ?tags= and ?sort= pattern
 * specifically is what the removed static/js/post/resp.js draft already
 * assumed this page would support, from its tag-cloud links).
 * ============================================================================
 */

import { searchState } from './search.state.js';
import {
  handleSearchInput, handleSetType, runSearch, loadDiscovery,
  populateFilterDropdowns, showRecentSearches,
} from './search.events.js';
import { setupSearchDelegation } from './search.delegation.js';

function $(id) { return document.getElementById(id); }

document.addEventListener('DOMContentLoaded', async () => {
  setupSearchDelegation();
  populateFilterDropdowns(); // fire and forget — not on the critical path

  const input = $('search-page-input');
  if (input) {
    input.addEventListener('input', (e) => handleSearchInput(e.target.value));
    input.addEventListener('focus', () => {
      if (!input.value.trim()) showRecentSearches();
    });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        runSearch();
      }
    });
    // Clicking anywhere outside the search bar / suggestions dropdown
    // closes the dropdown, matching the dropdown-dismiss pattern used
    // elsewhere in this app (e.g. connection action menus).
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.search-bar-wrap')) {
        $('search-suggestions')?.classList.add('hidden');
      }
    });
  }

  // Deep-link params
  const params = new URLSearchParams(window.location.search);
  const q           = params.get('q') || '';
  const type         = params.get('type') || 'all';
  const tags          = params.get('tags');
  const department   = params.get('department');
  const sort          = params.get('sort');

  // Batch 2/3-style ordering note: the query is set in state BEFORE
  // handleSetType() runs, not after — handleSetType() itself decides
  // whether to search, show discovery, or show a "type something" nudge
  // based on whatever query is already in state at that moment, so
  // setting it first avoids a visible flash of the wrong empty-state
  // message before the real results replace it.
  if (department) searchState.setFilter('department', department);
  if (sort)         searchState.setFilter('sort', sort === 'trending' ? 'recent' : sort);

  const effectiveQuery = q || (tags || '');
  const effectiveType  = q ? type : (tags ? 'posts' : type);

  if (effectiveQuery) {
    if (input) input.value = effectiveQuery;
    searchState.setQuery(effectiveQuery);
  }

  if (effectiveType !== 'all') {
    handleSetType(effectiveType); // reads state.query, set above; flips the active tab UI
  } else if (effectiveQuery) {
    runSearch();
  } else {
    loadDiscovery();
  }
});
