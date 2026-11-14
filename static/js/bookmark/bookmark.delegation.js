import * as bmApi from './bookmark.api.js';
import { bookmarkState as state } from './bookmark.state.js';
import {
  buildTypePills, buildFolderRail, buildBookmarkItem, buildBookmarkMeta, buildSkeleton, buildEmpty,
  buildError, buildEditModal, buildFolderModal, buildDeleteFolderModal, buildMoveModal, buildConfirmModal,
} from './bookmark.templates.js';
import { applyBookmarkUI } from '../feed/feed.events.js';
import * as feedApi from '../feed/feed.api.js';

const $ = (id) => document.getElementById(id);
const STALE_AFTER_MS = 60_000;

// ───────────────────────── rendering ─────────────────────────

function isFiltered() {
  const f = state.filters;
  return !!(f.type || f.q);
}

function renderSummary() {
  const el = $('bm-summary');
  if (!el) return;
  if (state.loading && !state.items.length) { el.textContent = ''; return; }
  const n = state.total ?? state.items.length;
  el.textContent = `${n} saved post${n === 1 ? '' : 's'}` + (isFiltered() ? ' match your filters' : '');
}

function renderRail() {
  const el = $('bm-folders');
  if (el) el.innerHTML = buildFolderRail(state.folders, state.filters.folderId, state.totalAll());
}

function renderPills() {
  const el = $('bm-type-pills');
  if (el) el.innerHTML = buildTypePills(state.filters.type);
}

function itemHTML(entry) {
  return buildBookmarkItem(entry, {
    selectMode: state.selectMode, selected: state.selected.has(entry.bookmark_id),
  });
}

function renderList() {
  const list = $('bm-list');
  if (!list) return;
  if (!state.items.length) {
    list.innerHTML = buildEmpty({
      filtered: isFiltered(),
      hasAnyBookmarks: state.totalAll() > 0,
    });
  } else {
    list.innerHTML = state.items.map(itemHTML).join('');
  }
  renderSummary();
  renderBulkBar();
}

function appendItems(entries) {
  $('bm-list')?.insertAdjacentHTML('beforeend', entries.map(itemHTML).join(''));
  renderSummary();
}

function renderBulkBar() {
  const bar = $('bm-bulkbar');
  const btn = $('bm-select-btn');
  if (btn) btn.textContent = state.selectMode ? 'Done' : 'Select';
  if (!bar) return;
  const n = state.selected.size;
  bar.hidden = !(state.selectMode && n > 0);
  const c = $('bm-bulk-count');
  if (c) c.textContent = `${n} selected`;
}

function setMore(visible) {
  const el = $('bm-more');
  if (el) el.hidden = !visible;
}

// ───────────────────────── loading ─────────────────────────

function params(cursor = null) {
  return { ...state.filters, cursor };
}

async function loadFirstPage({ silent = false } = {}) {
  const seq = ++state.seq;
  state.loading = true;
  state.cursor = null;
  if (!silent) { $('bm-list').innerHTML = buildSkeleton(); renderSummary(); }
  try {
    const data = await bmApi.fetchBookmarks(params());
    if (seq !== state.seq) return;                       // a newer request superseded this one
    state.items = data.bookmarks || [];
    state.cursor = data.next_cursor;
    state.hasMore = !!data.has_more;
    state.total = data.total ?? state.items.length;
    if (data.folders) state.folders = data.folders;
    state.loadedAt = Date.now();
    state.stale = false;
    renderRail(); renderList();
  } catch (e) {
    if (seq !== state.seq) return;
    console.error('[bookmarks] load failed', e);
    $('bm-list').innerHTML = buildError(e?.message);
  } finally {
    if (seq === state.seq) state.loading = false;
  }
}

export async function loadMore() {
  if (state.loading || !state.hasMore || !state.cursor) return;
  const seq = ++state.seq;
  state.loading = true;
  setMore(true);
  try {
    const data = await bmApi.fetchBookmarks(params(state.cursor));
    if (seq !== state.seq) return;
    state.items.push(...(data.bookmarks || []));
    state.cursor = data.next_cursor;
    state.hasMore = !!data.has_more;
    appendItems(data.bookmarks || []);
  } catch (e) {
    if (seq === state.seq) showToast('Could not load more bookmarks', 'error');
  } finally {
    if (seq === state.seq) state.loading = false;
    setMore(false);
  }
}

async function refreshFolders() {
  try { state.folders = await bmApi.fetchFolders(); renderRail(); } catch (e) { /* non-fatal */ }
}

// ───────────────────────── modal helper ─────────────────────────

let _modalEl = null;
let _lastFocus = null;

function openModal(html) {
  closeModal();
  _lastFocus = document.activeElement;
  _modalEl = document.createElement('div');
  _modalEl.className = 'bm-modal-backdrop';
  _modalEl.innerHTML = `<div class="bm-modal" role="dialog" aria-modal="true">${html}</div>`;
  // Backdrop click closes (handled here, NOT via data-action, so clicks inside the dialog are unaffected)
  _modalEl.addEventListener('mousedown', (e) => { if (e.target === _modalEl) closeModal(); });
  _modalEl.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeModal();
    if (e.key === 'Enter' && e.target.tagName === 'INPUT') {
      _modalEl.querySelector('.bm-btn-primary')?.click();
    }
  });
  document.body.appendChild(_modalEl);
  _modalEl.querySelector('input,textarea,select,button')?.focus();
}

function closeModal() {
  if (!_modalEl) return;
  _modalEl.remove();
  _modalEl = null;
  _lastFocus?.focus?.();
}

const modalValue = (id) => _modalEl?.querySelector(`#${id}`)?.value ?? '';

// ───────────────────────── removal + undo ─────────────────────────

let _undoTimer = null;

function showSnackbar(text, actionLabel, onAction) {
  const bar = $('bm-snackbar');
  if (!bar) return;
  clearTimeout(_undoTimer);
  bar.innerHTML = '';
  const span = document.createElement('span');
  span.textContent = text;
  bar.appendChild(span);
  if (actionLabel) {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'bm-link'; b.textContent = actionLabel;
    b.addEventListener('click', () => { bar.hidden = true; onAction(); });
    bar.appendChild(b);
  }
  bar.hidden = false;
  _undoTimer = setTimeout(() => { bar.hidden = true; }, 6000);
}

/** Remove a row from the view + state (server already updated). */
function removeFromView(entry, { undo = true } = {}) {
  const el = document.querySelector(`.bm-item[data-bookmark-id="${entry.bookmark_id}"]`);
  const finish = () => {
    el?.remove();
    state.removeByBookmark(entry.bookmark_id);
    state.selected.delete(entry.bookmark_id);
    if (state.total != null) state.total = Math.max(0, state.total - 1);
    const f = state.folderById(entry.folder_id);
    if (f) f.count = Math.max(0, (f.count || 1) - 1);
    renderRail(); renderSummary(); renderBulkBar();
    if (!state.items.length) {
      state.hasMore ? loadMore() : renderList();
    }
  };
  if (el) { el.classList.add('is-removing'); setTimeout(finish, 220); } else { finish(); }

  if (undo) {
    showSnackbar('Bookmark removed', 'Undo', async () => {
      try {
        await bmApi.restoreBookmark(entry.post.id,
          { folder_id: entry.folder_id, notes: entry.notes, tags: entry.tags });
        applyBookmarkUI(entry.post.id, true);
        showToast('Bookmark restored', 'success', 1800);
        loadFirstPage({ silent: true });
        refreshFolders();
      } catch (e) { showToast('Could not restore bookmark', 'error'); }
    });
  }
}

// ───────────────────────── init ─────────────────────────

export async function initBookmarks() {
  state.setInitialized(true);
  renderPills();
  renderRail();
  bindControls();
  bindInfiniteScroll();
  document.addEventListener('bookmark:changed', onBookmarkChanged);
  await loadFirstPage();
}

/** Called by bookmark.init.js each time the section becomes active again. */
export function onReactivated() {
  if (state.stale || Date.now() - state.loadedAt > STALE_AFTER_MS) loadFirstPage({ silent: true });
}

function onBookmarkChanged(e) {
  const { postId, bookmarked } = e.detail || {};
  state.stale = true;                      // feed/other views changed something
  const entry = state.findByPost(postId);
  if (!bookmarked && entry) removeFromView(entry);   // un-bookmarked from the card button in this section
}

function bindControls() {
  const search = $('bm-search');
  const clear = $('bm-search-clear');
  let t = null;
  search?.addEventListener('input', () => {
    clear.hidden = !search.value;
    clearTimeout(t);
    t = setTimeout(() => {
      const q = search.value.trim();
      if (q === state.filters.q) return;
      state.filters.q = q;
      loadFirstPage();
    }, 300);
  });
  $('bm-sort')?.addEventListener('change', (e) => {
    state.filters.sort = e.target.value;
    loadFirstPage();
  });
}

let _io = null;
function bindInfiniteScroll() {
  const sentinel = $('bm-sentinel');
  if (!sentinel || !('IntersectionObserver' in window)) return;
  _io?.disconnect();
  _io = new IntersectionObserver((entries) => {
    if (entries.some(e => e.isIntersecting) && $('bookmarks')?.classList.contains('active')) loadMore();
  }, { rootMargin: '400px 0px' });
  _io.observe(sentinel);
}

// ───────────────────────── handlers ─────────────────────────

const itemOf = (target) => {
  const el = target.closest('.bm-item');
  return el ? state.find(el.dataset.bookmarkId) : null;
};

export const BookmarkHandlers = {

  // ── filters ──
  'bm-folder': (target) => {
    const id = target.dataset.folderId ? Number(target.dataset.folderId) : null;
    if (id === state.filters.folderId) return;
    state.filters.folderId = id;
    loadFirstPage(); renderRail();
  },
  'bm-type': (target) => {
    state.filters.type = target.dataset.type || '';
    renderPills(); loadFirstPage();
  },
  'bm-clear-search': () => {
    const s = $('bm-search'); if (s) s.value = '';
    $('bm-search-clear').hidden = true;
    state.filters.q = ''; loadFirstPage();
  },
  'bm-reset-filters': () => {
    state.filters = { folderId: null, type: '', q: '', sort: state.filters.sort };
    const s = $('bm-search'); if (s) s.value = '';
    renderPills(); renderRail(); loadFirstPage();
  },
  'bm-retry': () => loadFirstPage(),

  // ── single bookmark ──
  'bm-remove': async (target) => {
    const entry = itemOf(target); if (!entry) return;
    try {
      await feedApi.setBookmark(entry.post.id, false);
      applyBookmarkUI(entry.post.id, false);
      removeFromView(entry);
    } catch (e) { showToast('Could not remove bookmark', 'error'); }
  },
  'bm-edit': async (target) => {
    const entry = itemOf(target); if (!entry) return;
    if (!state.folders.length) await refreshFolders();
    openModal(buildEditModal(entry, state.folders));
  },
  'bm-save-edit': async (target) => {
    const entry = state.find(target.dataset.bookmarkId); if (!entry) return;
    const tags = modalValue('bm-edit-tags').split(',').map(s => s.trim()).filter(Boolean).slice(0, 10);
    const patch = {
      notes: modalValue('bm-edit-notes'),
      tags,
      folder_id: Number(modalValue('bm-edit-folder-select')),
    };
    target.disabled = true;
    try {
      const res = await bmApi.updateBookmark(entry.bookmark_id, patch);
      const d = res.data;
      const movedOut = state.filters.folderId !== null && d.folder_id !== state.filters.folderId;
      Object.assign(entry, { notes: d.notes, tags: d.tags, folder_id: d.folder_id, folder: d.folder });
      closeModal();
      if (movedOut) {
        removeFromView(entry, { undo: false });   // moved away from the folder being viewed
        showToast(`Moved to ${d.folder}`, 'success', 1800);
      } else {
        const el = document.querySelector(`.bm-item[data-bookmark-id="${entry.bookmark_id}"] .bm-meta`);
        if (el) el.outerHTML = buildBookmarkMeta(entry);
        showToast('Bookmark updated', 'success', 1800);
      }
      refreshFolders();
    } catch (e) {
      target.disabled = false;
      showToast(e?.message || 'Could not save changes', 'error');
    }
  },

  // ── folders ──
  'bm-new-folder': () => openModal(buildFolderModal(null)),
  'bm-edit-folder': (target) => {
    const f = state.folderById(target.dataset.folderId);
    if (f) openModal(buildFolderModal(f));
  },
  'bm-pick-icon':  (t) => { t.parentElement.querySelectorAll('.bm-swatch').forEach(b => b.classList.toggle('on', b === t)); },
  'bm-pick-color': (t) => { t.parentElement.querySelectorAll('.bm-swatch').forEach(b => b.classList.toggle('on', b === t)); },
  'bm-save-folder': async (target) => {
    const id = target.dataset.folderId ? Number(target.dataset.folderId) : null;
    const body = {
      name: modalValue('bm-folder-name').trim(),
      icon: _modalEl.querySelector('#bm-folder-icons .on')?.dataset.value,
      color: _modalEl.querySelector('#bm-folder-colors .on')?.dataset.value,
    };
    if (!body.name) { showToast('Give the folder a name', 'error'); return; }
    target.disabled = true;
    try {
      if (id) await bmApi.updateFolder(id, body); else await bmApi.createFolder(body);
      closeModal();
      await refreshFolders();
      showToast(id ? 'Folder updated' : 'Folder created', 'success', 1800);
    } catch (e) {
      target.disabled = false;
      showToast(e?.message || 'Could not save folder', 'error');   // e.g. 409 duplicate name
    }
  },
  'bm-delete-folder': (target) => {
    const f = state.folderById(target.dataset.folderId);
    if (f) openModal(buildDeleteFolderModal(f));
  },
  'bm-confirm-delete-folder': async (target) => {
    const id = Number(target.dataset.folderId);
    const mode = _modalEl.querySelector('input[name="bm-del-mode"]:checked')?.value || 'move';
    target.disabled = true;
    try {
      await bmApi.deleteFolder(id, mode);
      closeModal();
      if (state.filters.folderId === id) state.filters.folderId = null;
      await refreshFolders();
      await loadFirstPage();
      showToast('Folder deleted', 'success', 1800);
    } catch (e) {
      target.disabled = false;
      showToast(e?.message || 'Could not delete folder', 'error');
    }
  },
  'bm-close-modal': () => closeModal(),

  // ── multi-select ──
  'bm-toggle-select': () => {
    state.selectMode = !state.selectMode;
    if (!state.selectMode) state.selected.clear();
    renderList();
  },
  'bm-toggle-select-item': (target) => {
    const entry = itemOf(target); if (!entry) return;
    state.toggleSelected(entry.bookmark_id);
    const item = target.closest('.bm-item');
    item.classList.toggle('is-selected', state.selected.has(entry.bookmark_id));
    target.setAttribute('aria-pressed', String(state.selected.has(entry.bookmark_id)));
    renderBulkBar();
  },
  'bm-select-all': () => {
    state.items.forEach(i => state.selected.add(i.bookmark_id));
    renderList();
  },
  'bm-bulk-cancel': () => { state.selected.clear(); state.selectMode = false; renderList(); },
  'bm-bulk-move': async () => {
    if (!state.folders.length) await refreshFolders();
    openModal(buildMoveModal(state.selected.size, state.folders));
  },
  'bm-confirm-move': async (target) => {
    const folderId = Number(modalValue('bm-move-folder'));
    const postIds = [...state.selected].map(id => state.find(id)?.post.id).filter(Boolean);
    target.disabled = true;
    try {
      for (let i = 0; i < postIds.length; i += 50) {                // server cap: 50 / request
        await bmApi.moveBookmarks(postIds.slice(i, i + 50), folderId);
      }
      closeModal();
      state.selected.clear(); state.selectMode = false;
      await Promise.all([refreshFolders(), loadFirstPage({ silent: true })]);
      showToast('Bookmarks moved', 'success', 1800);
    } catch (e) {
      target.disabled = false;
      showToast(e?.message || 'Could not move bookmarks', 'error');
    }
  },
  'bm-bulk-remove': () => {
    const n = state.selected.size;
    openModal(buildConfirmModal(`Remove ${n} bookmark${n === 1 ? '' : 's'}?`,
      'The posts themselves are not deleted.', 'bm-confirm-bulk-remove', 'Remove'));
  },
  'bm-confirm-bulk-remove': async (target) => {
    const postIds = [...state.selected].map(id => state.find(id)?.post.id).filter(Boolean);
    target.disabled = true;
    try {
      for (let i = 0; i < postIds.length; i += 50) await bmApi.bulkRemove(postIds.slice(i, i + 50));
      postIds.forEach(id => applyBookmarkUI(id, false));
      closeModal();
      state.selected.clear(); state.selectMode = false;
      await Promise.all([refreshFolders(), loadFirstPage({ silent: true })]);
      showToast('Bookmarks removed', 'success', 1800);
    } catch (e) {
      target.disabled = false;
      showToast('Could not remove bookmarks', 'error');
    }
  },
};
