/** Bookmarks section state. Same shape/idea as profile.state.js. */
class BookmarkState {
  constructor() {
    this._initialized = false;
    this.resetList();
    this.folders = [];
  }

  resetList() {
    this.items = [];            // [{bookmark_id, folder_id, folder, notes, tags, bookmarked_at, post}]
    this.cursor = null;
    this.hasMore = false;
    this.total = null;          // total for the CURRENT filter (first page only)
    this.loading = false;
    this.seq = 0;               // request sequence: ignore stale responses
    this.loadedAt = 0;
    this.stale = false;         // set when something changed elsewhere
    this.selectMode = false;
    this.selected = new Set();  // bookmark_ids
  }

  filters = { folderId: null, type: '', q: '', sort: 'recent' };

  setInitialized(v) { this._initialized = v; }
  isInitialized()   { return this._initialized; }

  find(bookmarkId)       { return this.items.find(i => i.bookmark_id === Number(bookmarkId)); }
  findByPost(postId)     { return this.items.find(i => i.post.id === Number(postId)); }
  removeByBookmark(id)   { this.items = this.items.filter(i => i.bookmark_id !== Number(id)); }
  folderById(id)         { return this.folders.find(f => f.id === Number(id)); }
  totalAll()             { return this.folders.reduce((n, f) => n + (f.count || 0), 0); }

  toggleSelected(id) {
    id = Number(id);
    this.selected.has(id) ? this.selected.delete(id) : this.selected.add(id);
  }
}

export const bookmarkState = new BookmarkState();
