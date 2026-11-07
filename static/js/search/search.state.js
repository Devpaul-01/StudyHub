/**
 * ============================================================================
 * SEARCH STATE — search.state.js
 * ============================================================================
 */

class SearchState {
  constructor() {
    this.query          = '';
    this.type            = 'all'; // 'all' | 'users' | 'posts' | 'threads'
    this.filters = {
      department:  '',
      classLevel:  '',
      postType:    '',
      sort:        'relevance',
      tags:        [],
    };
    this.results          = null;  // last results payload for the current type
    this.loading          = false;
    this.page              = 1;
    this.hasMore           = false;
    this.recentSearches   = [];
    this.suggestions       = [];
  }

  setQuery(q)            { this.query = q || ''; }
  setType(type)           { this.type = type || 'all'; this.page = 1; this.results = null; }
  setFilter(key, value)   { this.filters[key] = value; }
  getFilters()             { return { ...this.filters }; }

  setResults(results, append = false) {
    if (append && this.results) {
      // Only the items list grows; pagination/meta always reflect the
      // latest page fetched.
      const key = this._itemsKeyForType(this.type);
      this.results = {
        ...results,
        [key]: [...(this.results[key] || []), ...(results[key] || [])],
      };
    } else {
      this.results = results;
    }
  }

  _itemsKeyForType(type) {
    return { users: 'users', posts: 'posts', threads: 'threads' }[type] || 'users';
  }

  setLoading(v)   { this.loading = v; }
  setPage(p)       { this.page = p; }
  setHasMore(v)    { this.hasMore = v; }
  setRecentSearches(list) { this.recentSearches = list || []; }
  setSuggestions(list)     { this.suggestions = list || []; }

  reset() {
    this.query = '';
    this.results = null;
    this.page = 1;
    this.hasMore = false;
  }
}

export const searchState = new SearchState();
