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
  buildSkillsList,
  buildScheduleGrid,
  buildBadgesTab,
  buildBadgeDetailsModal,
  buildAllBadgesModal,
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
    // Batch 3: rankHistory fetched alongside the two existing calls here.
    [profileState.reputation, profileState.repHistory, profileState.rankHistory] = await Promise.all([
      profileApi.fetchReputation(),
      profileApi.fetchReputationHistory(),
      profileApi.fetchRankHistory(),
    ]);
    profileState.markLoaded('reputation');
  }
  setTabContent(buildReputationTab(profileState.reputation, profileState.repHistory, profileState.rankHistory));
}

// Batch 3
async function renderBadgesTab() {
  if (!profileState.isLoaded('badges')) {
    setTabContent(buildSkeleton());
    [profileState.myBadges, profileState.badgeProgress] = await Promise.all([
      profileApi.fetchMyBadges(),
      profileApi.fetchBadgeProgress(),
    ]);
    profileState.markLoaded('badges');
  }
  setTabContent(buildBadgesTab(profileState.myBadges, profileState.badgeProgress));
}

async function renderCurrentTab() {
  const tab = profileState.getCurrentTab();
  if (tab === 'posts')       return renderPostsTab();
  if (tab === 'stats')       return renderStatsTab();
  if (tab === 'connections') return renderConnectionsTab();
  if (tab === 'reputation')  return renderReputationTab();
  if (tab === 'badges')     return renderBadgesTab();
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

// Batch 3: badge details and "browse all badges" — both built and
// inserted into the page on demand (same p-modal-* classes other modals
// use) and removed on close, rather than two more large static blocks in
// feed2.html for what are essentially one-off info screens. Each uses
// its own fixed id and is replaced (not stacked) on repeated opens.
function _openDynamicProfileModal(id, title, bodyHtml) {
  document.getElementById(id)?.remove();
  const el = document.createElement('div');
  el.id = id;
  el.className = 'p-modal-overlay hidden';
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-modal', 'true');
  el.innerHTML = `
    <div class="p-modal-box" style="max-width:420px;">
      <div class="p-modal-header">
        <h3 class="p-modal-title">${title}</h3>
        <button data-action="profile-close-modal" data-modal-id="${id}" class="p-modal-close">✕</button>
      </div>
      ${bodyHtml}
    </div>
  `;
  document.body.appendChild(el);
  openProfileModal(id);
}

const BADGE_DETAILS_MODAL_ID = 'p-badge-details-modal';
const ALL_BADGES_MODAL_ID    = 'p-all-badges-modal';

function openBadgeDetailsModal(data) {
  _openDynamicProfileModal(BADGE_DETAILS_MODAL_ID, 'Badge Details', buildBadgeDetailsModal(data));
}
function closeBadgeDetailsModal() {
  closeProfileModal(BADGE_DETAILS_MODAL_ID);
  document.getElementById(BADGE_DETAILS_MODAL_ID)?.remove();
}
function openAllBadgesModal(data) {
  _openDynamicProfileModal(ALL_BADGES_MODAL_ID, 'All Badges', buildAllBadgesModal(data));
}

function populateEditModal() {
  const d = profileState.profileData;
  if (!d) return;
  const setVal = (id, val) => { const el = $id(id); if (el) el.value = val || ''; };
  setVal('p-edit-name', d.name);
  setVal('p-edit-bio',  d.bio);
  const goalsEl = $id('p-edit-goals-list');
  if (goalsEl) goalsEl.innerHTML = buildGoalsList(d.learning_goals || []);
  // Batch 3: skills is already part of the same profile payload (see
  // "skills" in the /profile/me response, routes/student/profile.py) —
  // no extra request needed to populate this.
  const skillsEl = $id('p-edit-skills-list');
  if (skillsEl) skillsEl.innerHTML = buildSkillsList(d.skills || []);
  populateSkillSuggestions();
}

// Batch 3: populates the <datalist> backing the skill input's
// autocomplete from GET /skills/popular. Fire-and-forget — if it fails,
// the input still works as a plain text field, just without suggestions.
async function populateSkillSuggestions() {
  const el = $id('p-edit-skill-suggestions');
  if (!el) return;
  const popular = await profileApi.fetchPopularSkills();
  el.innerHTML = popular.map(s => `<option value="${String(s).replace(/"/g, '&quot;')}"></option>`).join('');
}

// Batch 3: Privacy settings modal — populated from GET
// /profile/visibility-settings each time it's opened, so it always
// reflects the current saved state rather than a stale local copy.
async function populatePrivacyModal() {
  const data = await profileApi.fetchVisibilitySettings();
  if (!data) return;
  const setChecked = (id, val) => { const el = $id(id); if (el) el.checked = !!val; };
  setChecked('p-privacy-profile-private',     data.set_profile_private);
  setChecked('p-privacy-show-active',         data.show_active_status);
  setChecked('p-privacy-weekly-notification', data.send_weekly_notification);
}

// Batch 3: Study schedule modal — same "always re-fetch on open" approach
// as privacy above.
async function populateScheduleModal() {
  const schedule = await profileApi.fetchStudySchedule();
  const gridEl = $id('p-schedule-grid');
  if (gridEl) gridEl.innerHTML = buildScheduleGrid(schedule || {});
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
    else if (tab === 'badges')      await renderBadgesTab();
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

  // Batch 3
  'profile-open-privacy': () => {
    populatePrivacyModal().then(() => openProfileModal('p-privacy-modal'));
  },

  'profile-open-schedule': () => {
    populateScheduleModal().then(() => openProfileModal('p-schedule-modal'));
  },

  // Batch 3: badge details — dynamically built/removed modal, same
  // approach as the privacy/schedule modals above but with no static
  // shell in feed2.html at all (see openBadgeDetailsModal() below for
  // why — same reasoning as the reputation-levels-style modals).
  'profile-open-badge-details': async (target) => {
    const badgeId = target.closest('[data-badge-id]')?.dataset.badgeId;
    if (!badgeId) return;
    const data = await profileApi.fetchBadgeDetails(badgeId);
    openBadgeDetailsModal(data);
  },

  'profile-toggle-feature-badge': async (target) => {
    const badgeId = target.dataset.badgeId;
    if (!badgeId) return;
    const res = await profileApi.toggleFeatureBadge(badgeId);
    if (res?.status === 'success') {
      showToast(res.message || 'Updated', 'success');
      profileState.invalidate('badges'); // featured state changed — refetch next visit
      closeBadgeDetailsModal();
      // If the badges tab is currently open, refresh it immediately
      // rather than waiting for the next tab switch.
      if (profileState.getCurrentTab() === 'badges') await renderBadgesTab();
    } else {
      showToast(res?.message || 'Could not update badge', 'error');
    }
  },

  'profile-browse-all-badges': async () => {
    const data = await profileApi.fetchAvailableBadges();
    openAllBadgesModal(data);
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

  // ── Batch 3: Skills ────────────────────────────────────────────────────────

  'profile-add-skill': async () => {
    const input = $id('p-edit-skill-input');
    const skill = input?.value?.trim();
    if (!skill) return;
    const res = await profileApi.addSkill(skill);
    if (res?.status === 'success') {
      if (input) input.value = '';
      const newSkills = res.data?.skills || [];
      if (profileState.profileData) profileState.profileData.skills = newSkills;
      const skillsEl = $id('p-edit-skills-list');
      if (skillsEl) skillsEl.innerHTML = buildSkillsList(newSkills);
    } else {
      showToast(res?.message || 'Could not add skill', 'error');
    }
  },

  'profile-remove-skill': async (target) => {
    const skill = target.dataset.skill;
    if (!skill) return;
    const res = await profileApi.removeSkill(skill);
    if (res?.status === 'success') {
      const newSkills = res.data?.skills || [];
      if (profileState.profileData) profileState.profileData.skills = newSkills;
      const skillsEl = $id('p-edit-skills-list');
      if (skillsEl) skillsEl.innerHTML = buildSkillsList(newSkills);
    } else {
      showToast(res?.message || 'Could not remove skill', 'error');
    }
  },

  // ── Batch 3: Privacy settings ─────────────────────────────────────────────

  'profile-save-privacy': async () => {
    const settings = {
      set_profile_private:      !!$id('p-privacy-profile-private')?.checked,
      show_active_status:       !!$id('p-privacy-show-active')?.checked,
      send_weekly_notification: !!$id('p-privacy-weekly-notification')?.checked,
    };
    const res = await profileApi.saveVisibilitySettings(settings);
    if (res?.status === 'success') {
      showToast('Privacy settings saved', 'success');
      closeProfileModal('p-privacy-modal');
    } else {
      showToast(res?.message || 'Could not save privacy settings', 'error');
    }
  },

  // ── Batch 3: Study schedule ───────────────────────────────────────────────

  'profile-toggle-schedule-slot': (target) => {
    // Pure UI toggle — read back by profile-save-schedule below. No
    // request per click; one save sends the whole schedule at once, the
    // same way the academic-info modal batches its fields into one save.
    target.classList.toggle('active');
    const active = target.classList.contains('active');
    target.style.background   = active ? 'var(--accent,#0057FF)' : 'transparent';
    target.style.borderColor  = active ? 'var(--accent,#0057FF)' : 'var(--border)';
    target.style.color        = active ? '#fff' : 'var(--text-secondary)';
  },

  'profile-save-schedule': async () => {
    const schedule = {};
    document.querySelectorAll('#p-schedule-grid [data-action="profile-toggle-schedule-slot"].active')
      .forEach(btn => {
        const day = btn.dataset.day;
        if (!schedule[day]) schedule[day] = [];
        schedule[day].push(btn.dataset.slot);
      });
    const res = await profileApi.saveStudySchedule(schedule);
    if (res?.status === 'success') {
      showToast('Study schedule saved', 'success');
      closeProfileModal('p-schedule-modal');
    } else {
      showToast(res?.message || 'Could not save study schedule', 'error');
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
