import { createPostCard } from '../feed/feed.templates.js';
import { escapeHtml, formatTime } from '../feed/feed.utils.js';

const TYPES = [
  { key: '',             label: 'All' },
  { key: 'question',     label: '❓ Questions' },
  { key: 'discussion',   label: '💬 Discussions' },
  { key: 'resource',     label: '📚 Resources' },
  { key: 'announcement', label: '📢 Announcements' },
  { key: 'problem',      label: '🧩 Problems' },
];

export const FOLDER_ICONS  = ['📁', '📚', '🧪', '📝', '💡', '🎯', '⭐', '🔥'];
export const FOLDER_COLORS = ['#6B7280', '#3B82F6', '#10B981', '#F59E0B', '#EF4444', '#8B5CF6', '#EC4899'];

export function buildTypePills(active) {
  return TYPES.map(t => `
    <button type="button" class="p-filter-pill${t.key === active ? ' active' : ''}"
            data-action="bm-type" data-type="${t.key}">${t.label}</button>`).join('');
}

export function buildFolderRail(folders, activeId, totalAll) {
  const row = (id, icon, name, count, color, editable) => `
    <div class="bm-folder-row${(id ?? null) === (activeId ?? null) ? ' active' : ''}">
      <button type="button" class="bm-folder-item" data-action="bm-folder"
              data-folder-id="${id ?? ''}" ${color ? `style="--bm-folder-color:${escapeHtml(color)}"` : ''}>
        <span class="bm-folder-icon">${escapeHtml(icon)}</span>
        <span class="bm-folder-name">${escapeHtml(name)}</span>
        <span class="bm-folder-count">${count}</span>
      </button>
      ${editable ? `<button type="button" class="bm-folder-more" data-action="bm-edit-folder"
                            data-folder-id="${id}" aria-label="Edit folder ${escapeHtml(name)}">⋯</button>` : ''}
    </div>`;
  return row(null, '🔖', 'All bookmarks', totalAll, null, false)
       + folders.map(f => row(f.id, f.icon, f.name, f.count, f.color, !f.is_default)).join('');
}

export function buildBookmarkMeta(entry) {
  const tags = (entry.tags || []).map(t => `<span class="bm-tag">#${escapeHtml(t)}</span>`).join('');
  return `
    <div class="bm-meta">
      <div class="bm-meta-row">
        <button type="button" class="bm-folder-chip" data-action="bm-folder"
                data-folder-id="${entry.folder_id ?? ''}">📁 ${escapeHtml(entry.folder || 'Saved')}</button>
        ${tags}
        <span class="bm-time">Saved ${escapeHtml(formatTime(entry.bookmarked_at))}</span>
        <span class="bm-actions">
          <button type="button" class="bm-link" data-action="bm-edit">Edit note / folder</button>
          <button type="button" class="bm-link bm-link-danger" data-action="bm-remove">Remove</button>
        </span>
      </div>
      ${entry.notes ? `<p class="bm-note">📝 ${escapeHtml(entry.notes)}</p>` : ''}
    </div>`;
}

export function buildBookmarkItem(entry, { selectMode, selected }) {
  return `
    <article class="bm-item${selected ? ' is-selected' : ''}" data-bookmark-id="${entry.bookmark_id}">
      ${selectMode ? `<button type="button" class="bm-check" data-action="bm-toggle-select-item"
                              aria-pressed="${selected}" aria-label="Select this post"></button>` : ''}
      ${createPostCard(entry.post)}
      ${buildBookmarkMeta(entry)}
    </article>`;
}

export function buildSkeleton(n = 3) {
  return Array.from({ length: n }, () => `
    <div class="bm-skeleton"><div class="bm-sk-line w40"></div><div class="bm-sk-line w90"></div>
    <div class="bm-sk-line w75"></div></div>`).join('');
}

export function buildEmpty({ filtered, hasAnyBookmarks }) {
  if (filtered) return `
    <div class="bm-empty"><div class="bm-empty-icon">🔍</div>
      <h3>No matches</h3><p>Nothing in this view matches your filters.</p>
      <button type="button" class="bm-btn" data-action="bm-reset-filters">Clear filters</button></div>`;
  return `
    <div class="bm-empty"><div class="bm-empty-icon">🔖</div>
      <h3>${hasAnyBookmarks ? 'This folder is empty' : 'No bookmarks yet'}</h3>
      <p>Tap <strong>Save</strong> on any post in your feed to keep it here for later.</p>
      <button type="button" class="bm-btn bm-btn-primary" data-action="navigate-to" data-target="feed">Browse the feed</button></div>`;
}

export function buildError(message) {
  return `
    <div class="bm-empty"><div class="bm-empty-icon">⚠️</div>
      <h3>Couldn't load bookmarks</h3><p>${escapeHtml(message || 'Please try again.')}</p>
      <button type="button" class="bm-btn bm-btn-primary" data-action="bm-retry">Retry</button></div>`;
}

// ── modal bodies ──────────────────────────────────────────────────────────

export function buildEditModal(entry, folders) {
  const opts = folders.map(f =>
    `<option value="${f.id}" ${f.id === entry.folder_id ? 'selected' : ''}>${escapeHtml(f.icon)} ${escapeHtml(f.name)}</option>`).join('');
  return `
    <h3 class="bm-modal-title">Edit bookmark</h3>
    <label class="bm-label" for="bm-edit-folder-select">Folder</label>
    <select id="bm-edit-folder-select" class="bm-select">${opts}</select>
    <label class="bm-label" for="bm-edit-notes">Private note <span class="bm-hint">(only you see this)</span></label>
    <textarea id="bm-edit-notes" class="bm-textarea" maxlength="1000" rows="4">${escapeHtml(entry.notes || '')}</textarea>
    <label class="bm-label" for="bm-edit-tags">Tags <span class="bm-hint">(comma separated, max 10)</span></label>
    <input id="bm-edit-tags" class="bm-input" maxlength="300" value="${escapeHtml((entry.tags || []).join(', '))}">
    <div class="bm-modal-actions">
      <button type="button" class="bm-btn" data-action="bm-close-modal">Cancel</button>
      <button type="button" class="bm-btn bm-btn-primary" data-action="bm-save-edit"
              data-bookmark-id="${entry.bookmark_id}">Save</button>
    </div>`;
}

export function buildFolderModal(folder) {         // folder === null -> create
  const icon = folder?.icon || FOLDER_ICONS[0];
  const color = folder?.color || FOLDER_COLORS[0];
  return `
    <h3 class="bm-modal-title">${folder ? 'Edit folder' : 'New folder'}</h3>
    <label class="bm-label" for="bm-folder-name">Name</label>
    <input id="bm-folder-name" class="bm-input" maxlength="100" value="${escapeHtml(folder?.name || '')}" autocomplete="off">
    <div class="bm-label">Icon</div>
    <div class="bm-swatches" id="bm-folder-icons">
      ${FOLDER_ICONS.map(i => `<button type="button" class="bm-swatch${i === icon ? ' on' : ''}"
         data-action="bm-pick-icon" data-value="${i}">${i}</button>`).join('')}
    </div>
    <div class="bm-label">Color</div>
    <div class="bm-swatches" id="bm-folder-colors">
      ${FOLDER_COLORS.map(c => `<button type="button" class="bm-swatch bm-swatch-color${c === color ? ' on' : ''}"
         data-action="bm-pick-color" data-value="${c}" style="background:${c}" aria-label="${c}"></button>`).join('')}
    </div>
    <div class="bm-modal-actions">
      ${folder ? `<button type="button" class="bm-btn bm-btn-danger bm-push-left" data-action="bm-delete-folder"
                          data-folder-id="${folder.id}">Delete…</button>` : ''}
      <button type="button" class="bm-btn" data-action="bm-close-modal">Cancel</button>
      <button type="button" class="bm-btn bm-btn-primary" data-action="bm-save-folder"
              data-folder-id="${folder?.id ?? ''}">${folder ? 'Save' : 'Create'}</button>
    </div>`;
}

export function buildDeleteFolderModal(folder) {
  return `
    <h3 class="bm-modal-title">Delete “${escapeHtml(folder.name)}”?</h3>
    <p class="bm-hint">This folder has ${folder.count} bookmark${folder.count === 1 ? '' : 's'}.</p>
    <label class="bm-radio"><input type="radio" name="bm-del-mode" value="move" checked>
      Keep the bookmarks — move them to “Saved”</label>
    <label class="bm-radio"><input type="radio" name="bm-del-mode" value="delete">
      Delete the folder <em>and</em> its bookmarks</label>
    <div class="bm-modal-actions">
      <button type="button" class="bm-btn" data-action="bm-close-modal">Cancel</button>
      <button type="button" class="bm-btn bm-btn-danger" data-action="bm-confirm-delete-folder"
              data-folder-id="${folder.id}">Delete</button>
    </div>`;
}

export function buildMoveModal(count, folders) {
  const opts = folders.map(f => `<option value="${f.id}">${escapeHtml(f.icon)} ${escapeHtml(f.name)}</option>`).join('');
  return `
    <h3 class="bm-modal-title">Move ${count} bookmark${count === 1 ? '' : 's'}</h3>
    <label class="bm-label" for="bm-move-folder">To folder</label>
    <select id="bm-move-folder" class="bm-select">${opts}</select>
    <div class="bm-modal-actions">
      <button type="button" class="bm-btn" data-action="bm-close-modal">Cancel</button>
      <button type="button" class="bm-btn bm-btn-primary" data-action="bm-confirm-move">Move</button>
    </div>`;
}

export function buildConfirmModal(title, body, confirmAction, confirmLabel) {
  return `
    <h3 class="bm-modal-title">${escapeHtml(title)}</h3>
    <p class="bm-hint">${escapeHtml(body)}</p>
    <div class="bm-modal-actions">
      <button type="button" class="bm-btn" data-action="bm-close-modal">Cancel</button>
      <button type="button" class="bm-btn bm-btn-danger" data-action="${confirmAction}">${escapeHtml(confirmLabel)}</button>
    </div>`;
}
