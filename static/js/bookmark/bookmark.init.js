import { initBookmarks, onReactivated } from './bookmark.delegation.js';
import { bookmarkState } from './bookmark.state.js';

document.addEventListener('DOMContentLoaded', () => {
  const section = document.getElementById('bookmarks');
  if (!section) { console.error('[bookmark.init] section#bookmarks not found'); return; }

  const start = () => {
    if (!bookmarkState.isInitialized()) {
      initBookmarks().catch((err) => {
        console.error('[bookmark.init] initBookmarks failed:', err);
        bookmarkState.setInitialized(false);          // allow retry on next activation
      });
    } else {
      onReactivated();
    }
  };

  new MutationObserver((mutations) => {
    for (const m of mutations) {
      if (m.type === 'attributes' && m.attributeName === 'class' && section.classList.contains('active')) {
        start();
      }
    }
  }).observe(section, { attributes: true });

  // Deep link (?section=bookmarks) can activate the section before this module runs.
  if (section.classList.contains('active')) start();
});
