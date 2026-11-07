/**
 * ============================================================================
 * SEARCH DELEGATION — search.delegation.js
 * Single click listener on the page, matching the data-action pattern
 * used throughout the rest of this app's *.delegation.js files.
 * ============================================================================
 */

import {
  handleSetType, handleToggleFilters, handleApplyFilters,
  handleClearHistory, handleLoadMore, handlePickSuggestion, handlePickRecent,
  openProfile, openPost, openThread,
} from './search.events.js';

const SearchHandlers = {
  'search-set-type': (target) => {
    handleSetType(target.dataset.type);
  },
  'search-toggle-filters': () => {
    handleToggleFilters();
  },
  'search-apply-filters': () => {
    handleApplyFilters();
  },
  'search-clear-history': () => {
    handleClearHistory();
  },
  'search-load-more': () => {
    handleLoadMore();
  },
  'search-pick-suggestion': (target) => {
    handlePickSuggestion(target.dataset.text, target.dataset.type);
  },
  'search-pick-recent': (target) => {
    handlePickRecent(target.dataset.query);
  },
  'search-open-profile': (target) => {
    openProfile(target.dataset.username);
  },
  'search-open-post': (target) => {
    openPost(target.dataset.postId);
  },
  'search-open-thread': (target) => {
    openThread(target.dataset.threadId);
  },
};

export function setupSearchDelegation() {
  document.addEventListener('click', (event) => {
    const target = event.target.closest('[data-action]');
    if (!target) return;
    const action = target.dataset.action;
    const handler = SearchHandlers[action];
    if (handler) {
      event.preventDefault();
      handler(target, event);
    }
  });
}
