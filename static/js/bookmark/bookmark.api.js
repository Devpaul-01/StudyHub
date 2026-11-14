/** Bookmark API — uses the global `api` from /js/core/api.js (non-module). */

export async function fetchBookmarks({ folderId, type, q, sort, cursor, limit = 12 } = {}) {
  const p = new URLSearchParams();
  if (folderId) p.set('folder_id', folderId);
  if (type)     p.set('type', type);
  if (q)        p.set('q', q);
  if (sort && sort !== 'recent') p.set('sort', sort);
  if (cursor)   p.set('cursor', cursor);
  p.set('limit', limit);
  const res = await api.get(`/posts/bookmarked?${p.toString()}`);
  return res.data;   // {bookmarks, next_cursor, has_more, total?, folders?}
}

export async function fetchFolders() {
  const res = await api.get('/posts/bookmarks/folders');
  return res.data.folders;
}
export const createFolder  = (body)       => api.post('/posts/bookmarks/folders', body);
export const updateFolder  = (id, body)   => api.patch(`/posts/bookmarks/folders/${id}`, body);
export const deleteFolder  = (id, mode)   => api.delete(`/posts/bookmarks/folders/${id}?mode=${mode}`);

export const updateBookmark = (bookmarkId, patch) => api.patch(`/posts/bookmarks/${bookmarkId}`, patch);
export const moveBookmarks  = (postIds, folderId) => api.post('/posts/bookmarks/move', { post_ids: postIds, folder_id: folderId });
export const removeBookmark = (postId)    => api.delete(`/posts/${postId}/bookmark`);
export const restoreBookmark = (postId, o) => api.put(`/posts/${postId}/bookmark`, o);
export const bulkRemove     = (postIds)   => api.post('/posts/bookmarks/bulk-remove', { post_ids: postIds });
