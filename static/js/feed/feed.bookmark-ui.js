/**
 * Dependency-free DOM helper shared by the feed and the Bookmarks section.
 * Update EVERY rendered copy of a post's bookmark button (feed, tag modal,
 * profile, bookmarks section).
 */
export function applyBookmarkUI(postId, bookmarked) {
  document.querySelectorAll(`.post-card[data-post-id="${postId}"] .bookmark-btn`).forEach((b) => {
    b.classList.toggle('bookmarked', bookmarked);
    b.setAttribute('aria-pressed', String(bookmarked));
    b.setAttribute('aria-label', bookmarked ? 'Remove bookmark' : 'Bookmark post');
    const label = b.querySelector('.bookmark-label');
    if (label) label.textContent = bookmarked ? 'Saved' : 'Save';
  });
}
