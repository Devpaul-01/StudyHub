/**
 * ============================================================================
 * PROFILE DELEGATION — profile.delegation.js
 * Save as: /static/js/profile/profile.delegation.js
 *
 * Exports:
 *   ProfileHandlers  — spread into UNIFIED_ACTIONS in app.unified.js
 *   initProfile      — called by profile.init.js MutationObserver
 * ============================================================================
 */

import { profileState } from './profile.state.js';
import * as profileApi  from './profile.api.js';
import {
  buildSkeleton,
  buildProfileHeader,
  buildPostsTab,
  buildStatsTab,
  buildConnectionsTab,
  buildReputationTab,
  buildGoalsList,
  buildReputationLevelsModal,
} from './profile.templates.js';
import { closeModal, openModal } from '../feed/feed.utils.js';

// ─────────────────────────────────────────────────────────────────────────────
// PRIVATE DOM HELPERS
// ─────────────────────────────────────────────────────────────────────────────

function $id(id) { return document.getElementById(id); }

function setTabContent(html) {
  const el = $id('profile-tab-content');
  if (el) el.innerHTML = html;
}

function setHeaderContent(html) {
  const el = $id('profile-header-container');
  if (el) el.innerHTML = html;
}

function syncTabButtons(activeTab) {
  document.querySelectorAll('.p-tab-btn').forEach(btn => {
    const isActive = btn.dataset.tab === activeTab;
    btn.classList.toggle('active', isActive);
    btn.style.background = isActive ? 'var(--primary)' : 'var(--bg-tertiary)';
    btn.style.color       = isActive ? '#fff'          : 'var(--text-secondary)';
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// TAB RENDERERS
// ─────────────────────────────────────────────────────────────────────────────

async function renderHeaderIfNeeded() {
  if (profileState.isLoaded('profile')) return;
  setHeaderContent(buildSkeleton(1));
  const data = await profileApi.fetchProfileData();
  if (!data) { setHeaderContent('<div style="padding:1rem;color:var(--text-secondary);">Failed to load profile</div>'); return; }
  profileState.profileData = data;
  profileState.markLoaded('profile');
  setHeaderContent(buildProfileHeader(data));
}

async function renderPostsTab(forceReload = false) {
  if (!profileState.isLoaded('posts') || forceReload) {
    setTabContent(buildSkeleton());
    profileState.posts = await profileApi.fetchMyPosts(profileState.postsFilter);
    profileState.markLoaded('posts');
  }
  setTabContent(buildPostsTab(profileState.posts, profileState.postsFilter));
}

async function renderStatsTab() {
  if (!profileState.isLoaded('stats')) {
    setTabContent(buildSkeleton());
    [profileState.stats, profileState.heatmap] = await Promise.all([
      profileApi.fetchMyStats(),
      profileApi.fetchActivityHeatmap(),
    ]);
    profileState.markLoaded('stats');
  }
  setTabContent(buildStatsTab(profileState.stats, profileState.heatmap));
}

async function renderConnectionsTab() {
  if (!profileState.isLoaded('connections')) {
    setTabContent(buildSkeleton());
    profileState.connections = await profileApi.fetchMyConnections();
    profileState.markLoaded('connections');
  }
  setTabContent(buildConnectionsTab(profileState.connections));
}

async function renderReputationTab() {
  if (!profileState.isLoaded('reputation')) {
    setTabContent(buildSkeleton());
    [profileState.reputation, profileState.repHistory] = await Promise.all([
      profileApi.fetchReputation(),
      profileApi.fetchReputationHistory(),
    ]);
    profileState.markLoaded('reputation');
  }
  setTabContent(buildReputationTab(profileState.reputation, profileState.repHistory));
}

async function renderCurrentTab() {
  const tab = profileState.getCurrentTab();
  if (tab === 'posts')       return renderPostsTab();
  if (tab === 'stats')       return renderStatsTab();
  if (tab === 'connections') return renderConnectionsTab();
  if (tab === 'reputation')  return renderReputationTab();
}

// ─────────────────────────────────────────────────────────────────────────────
// MODAL HELPERS
// ─────────────────────────────────────────────────────────────────────────────

function openProfileModal(id) {
  openModal(id);
}

function closeProfileModal(id) {
  closeModal(id);
}

// Batch 2: builds and opens the "How does reputation work?" modal on
// demand (see the 'show-reputation-levels' handler above for why this
// isn't a static block in feed2.html like the other profile modals).
const REPUTATION_LEVELS_MODAL_ID = 'p-reputation-levels-modal';

function openReputationLevelsModal(data) {
  // Remove any previous instance first, in case the user opened and
  // closed this more than once in the same session — avoids piling up
  // duplicate elements with the same id.
  document.getElementById(REPUTATION_LEVELS_MODAL_ID)?.remove();

  const el = document.createElement('div');
  el.id = REPUTATION_LEVELS_MODAL_ID;
  el.className = 'p-modal-overlay hidden';
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-modal', 'true');
  el.innerHTML = `
    <div class="p-modal-box" style="max-width:420px;">
      <div class="p-modal-header">
        <h3 class="p-modal-title">How Reputation Works</h3>
        <button data-action="profile-close-reputation-levels" class="p-modal-close">✕</button>
      </div>
      ${buildReputationLevelsModal(data)}
    </div>
  `;
  document.body.appendChild(el);
  openProfileModal(REPUTATION_LEVELS_MODAL_ID);
}

function closeReputationLevelsModal() {
  closeProfileModal(REPUTATION_LEVELS_MODAL_ID);
  // Unlike the static modals (which stay in the DOM, hidden, for reuse),
  // this one was created fresh on open — remove it right away so a stale
  // copy of someone's levels data doesn't linger in the DOM. .p-modal-overlay
  // toggles with a plain display:none/flex (no CSS transition — checked
  // static/css/profile/profile.css), so there's no animation to wait out.
  document.getElementById(REPUTATION_LEVELS_MODAL_ID)?.remove();
}

function populateEditModal() {
  const d = profileState.profileData;
  if (!d) return;
  const setVal = (id, val) => { const el = $id(id); if (el) el.value = val || ''; };
  setVal('p-edit-name', d.name);
  setVal('p-edit-bio',  d.bio);
  const goalsEl = $id('p-edit-goals-list');
  if (goalsEl) goalsEl.innerHTML = buildGoalsList(d.learning_goals || []);
}

async function populateAcademicModal() {
  const data = await profileApi.fetchAcademicInfo();
  if (!data) return;
  const setChips = (id, arr) => { const el = $id(id); if (el) el.value = (arr || []).join(', '); };
  setChips('p-academic-subjects',   data.subjects);
  setChips('p-academic-strong',     data.strong_subjects);
  setChips('p-academic-help',       data.help_subjects);
  setChips('p-academic-study-prefs',data.study_preferences);
  const styleEl = $id('p-academic-style');
  if (styleEl) styleEl.value = data.learning_style || '';
}

function parseChips(id) {
  const el = $id(id);
  if (!el) return [];
  return el.value.split(',').map(s => s.trim()).filter(Boolean);
}

// ─────────────────────────────────────────────────────────────────────────────
// PUBLIC: initProfile (called from profile.init.js MutationObserver)
// ─────────────────────────────────────────────────────────────────────────────

export async function initProfile() {
  if (profileState.isInitialized()) return;  // already loaded, don't re-run
  profileState.setInitialized(true);
  console.log('[profile] Initializing...');

  await renderHeaderIfNeeded();

  // Default to posts tab
  syncTabButtons('posts');
  profileState.setCurrentTab('posts');
  await renderPostsTab();

  console.log('[profile] ✅ Initialized');
}

// ─────────────────────────────────────────────────────────────────────────────
// PUBLIC: ProfileHandlers — spread into UNIFIED_ACTIONS in app.unified.js
// ─────────────────────────────────────────────────────────────────────────────

export const ProfileHandlers = {

  // ── Tab switching ──────────────────────────────────────────────────────────

  'profile-tab': async (target) => {
    const tab = target.dataset.tab;
    if (!tab || tab === profileState.getCurrentTab()) return;

    profileState.setCurrentTab(tab);
    syncTabButtons(tab);

    if (tab === 'posts')       await renderPostsTab();
    else if (tab === 'stats')       await renderStatsTab();
    else if (tab === 'connections') await renderConnectionsTab();
    else if (tab === 'reputation')  await renderReputationTab();
  },

  // ── Post filter ───────────────────────────────────────────────────────────

  'profile-filter-posts': async (target) => {
    const filter = target.dataset.filter;
    if (!filter || filter === profileState.postsFilter) return;
    profileState.postsFilter = filter;
    profileState.invalidate('posts');
    await renderPostsTab(true);
  },

  // ── Pin post ──────────────────────────────────────────────────────────────

  'profile-pin-post': async (target) => {
    const postId = target.dataset.postId || target.closest('[data-post-id]')?.dataset.postId;
    if (!postId) return;
    const res = await profileApi.pinPost(postId);
    if (res?.status === 'success') {
      showToast('Post pinned 📌', 'success');
      profileState.invalidate('posts');
      await renderPostsTab(true);
    } else {
      showToast(res?.message || 'Could not pin post', 'error');
    }
  },

  // ── Unpin post ────────────────────────────────────────────────────────────

  'profile-unpin-post': async (target) => {
    const postId = target.dataset.postId || target.closest('[data-post-id]')?.dataset.postId;
    if (!postId) return;
    const res = await profileApi.unpinPost(postId);
    if (res?.status === 'success') {
      showToast('Post unpinned', 'success');
      profileState.invalidate('posts');
      await renderPostsTab(true);
    } else {
      showToast(res?.message || 'Could not unpin post', 'error');
    }
  },

  // ── Delete post ───────────────────────────────────────────────────────────

  'profile-delete-post': async (target) => {
    const postId = target.dataset.postId || target.closest('[data-post-id]')?.dataset.postId;
    if (!postId) return;
    if (!confirm('Delete this post? This cannot be undone.')) return;

    const res = await profileApi.deletePost(postId);
    if (res?.status === 'success') {
      showToast('Post deleted', 'success');
      // Remove from DOM immediately
      const card = document.querySelector(`.profile-post-card[data-post-id="${postId}"]`);
      if (card) card.remove();
      // Update state so re-renders stay consistent
      profileState.posts = profileState.posts.filter(p => String(p.id) !== String(postId));
      // Refresh header stats count
      profileState.invalidate('profile');
      await renderHeaderIfNeeded();
    } else {
      showToast(res?.message || 'Could not delete post', 'error');
    }
  },

  // ── Open modals ───────────────────────────────────────────────────────────

  'profile-open-edit': () => {
    populateEditModal();
    openProfileModal('p-edit-modal');
  },

  'profile-open-avatar': () => {
    openProfileModal('p-avatar-modal');
  },

  'profile-open-academic': () => {
    populateAcademicModal().then(() => openProfileModal('p-academic-modal'));
  },

  // Batch 2: "How does reputation work?" — see the matching entry point
  // link added to buildReputationTab() in profile.templates.js.
  // GET /reputation/levels already existed on the backend with nothing
  // in the UI calling it. There's no existing generic/reusable info
  // modal in this app (every other profile modal — p-edit-modal,
  // p-avatar-modal, p-academic-modal — is a dedicated static block
  // already sitting in feed2.html), and adding a large new static block
  // there for a one-off info modal isn't worth it, so this one is built
  // the same way (same p-modal-* classes) but created on demand and
  // removed on close instead of living in the template permanently.
  'show-reputation-levels': async () => {
    const data = await profileApi.fetchReputationLevels();
    openReputationLevelsModal(data);
  },

  // ── Close modals ──────────────────────────────────────────────────────────

  'profile-close-modal': (target) => {
    const id = target.dataset.modalId;
    if (id) closeProfileModal(id);
  },

  'profile-close-reputation-levels': () => {
    closeReputationLevelsModal();
  },

  // ── Save profile edit ─────────────────────────────────────────────────────

  'profile-save-edit': async (target) => {
    const name = $id('p-edit-name')?.value?.trim();
    const bio  = $id('p-edit-bio')?.value?.trim();
    if (!name) { showToast('Name is required', 'error'); return; }

    target.disabled = true;
    target.textContent = 'Saving…';
    const res = await profileApi.saveProfileEdit({ name, bio });
    target.disabled = false;
    target.textContent = 'Save Changes';

    if (res?.status === 'success') {
      showToast('Profile updated ✅', 'success');
      closeProfileModal('p-edit-modal');
      profileState.invalidate('profile');
      await renderHeaderIfNeeded();
    } else {
      showToast(res?.message || 'Failed to save', 'error');
    }
  },

  // ── Learning goals ────────────────────────────────────────────────────────

  'profile-add-goal': async () => {
    const input = $id('p-edit-goal-input');
    const goal  = input?.value?.trim();
    if (!goal) return;
    const res = await profileApi.addLearningGoal(goal);
    if (res?.status === 'success') {
      if (input) input.value = '';
      const newGoals = res.data?.learning_goals || [];
      if (profileState.profileData) profileState.profileData.learning_goals = newGoals;
      const goalsEl = $id('p-edit-goals-list');
      if (goalsEl) goalsEl.innerHTML = buildGoalsList(newGoals);
    } else {
      showToast(res?.message || 'Could not add goal', 'error');
    }
  },

  'profile-remove-goal': async (target) => {
    const index = parseInt(target.dataset.index, 10);
    if (isNaN(index)) return;
    const res = await profileApi.removeLearningGoal(index);
    if (res?.status === 'success') {
      const newGoals = res.data?.learning_goals || [];
      if (profileState.profileData) profileState.profileData.learning_goals = newGoals;
      const goalsEl = $id('p-edit-goals-list');
      if (goalsEl) goalsEl.innerHTML = buildGoalsList(newGoals);
    } else {
      showToast(res?.message || 'Could not remove goal', 'error');
    }
  },

  // ── Save academic info ────────────────────────────────────────────────────

  'profile-save-academic': async (target) => {
    const payload = {
      subjects          : parseChips('p-academic-subjects'),
      strong_subjects   : parseChips('p-academic-strong'),
      help_subjects     : parseChips('p-academic-help'),
      study_preferences : parseChips('p-academic-study-prefs'),
      learning_style    : $id('p-academic-style')?.value?.trim() || '',
    };
    target.disabled = true;
    target.textContent = 'Saving…';
    const res = await profileApi.saveAcademicInfo(payload);
    target.disabled = false;
    target.textContent = 'Save';
    if (res?.status === 'success') {
      showToast('Academic info saved ✅', 'success');
      closeProfileModal('p-academic-modal');
    } else {
      showToast(res?.message || 'Failed to save', 'error');
    }
  },

  // ── Avatar upload ─────────────────────────────────────────────────────────

  'profile-upload-avatar': async () => {
    const input = $id('p-avatar-file-input');
    if (!input?.files?.length) { showToast('Choose a file first', 'error'); return; }

    const btn = document.querySelector('[data-action="profile-upload-avatar"]');
    if (btn) { btn.disabled = true; btn.textContent = 'Uploading…'; }

    const res = await profileApi.uploadAvatar(input.files[0]);

    if (btn) { btn.disabled = false; btn.textContent = 'Upload'; }

    if (res?.status === 'success') {
      showToast('Avatar updated ✅', 'success');
      closeProfileModal('p-avatar-modal');
      profileState.invalidate('profile');
      await renderHeaderIfNeeded();
    } else {
      showToast(res?.message || 'Upload failed', 'error');
    }
  },

  // ── Remove avatar ─────────────────────────────────────────────────────────

  'profile-remove-avatar': async () => {
    if (!confirm('Remove your profile picture?')) return;
    const res = await profileApi.removeAvatar();
    if (res?.status === 'success') {
      showToast('Avatar removed', 'success');
      closeProfileModal('p-avatar-modal');
      profileState.invalidate('profile');
      await renderHeaderIfNeeded();
    } else {
      showToast(res?.message || 'Failed', 'error');
    }
  },
};
