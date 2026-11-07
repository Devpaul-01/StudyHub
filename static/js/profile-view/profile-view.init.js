/**
 * ============================================================================
 * PROFILE-VIEW INIT — profile-view.init.js
 * Entry point for the public profile page (templates/profile-view.html).
 * ============================================================================
 */

import { profileViewAPI } from './profile-view.api.js';
import { renderPublicProfile, renderPrivateProfile } from './profile-view.templates.js';

function $(id) { return document.getElementById(id); }

function usernameFromPath() {
  // Path is /student/profile/view/<username>
  const parts = window.location.pathname.split('/').filter(Boolean);
  return decodeURIComponent(parts[parts.length - 1] || '');
}

async function loadProfile() {
  const username = usernameFromPath();
  const contentEl = $('pv-content');
  if (!username) {
    contentEl.innerHTML = `<div class="pv-error">No username given.</div>`;
    return;
  }

  try {
    const response = await profileViewAPI.fetchProfile(username);
    if (response?.status !== 'success') {
      contentEl.innerHTML = `<div class="pv-error">${response?.message || 'Could not load this profile.'}</div>`;
      return;
    }

    const data = response.data;

    // Batch 4: the backend's is_own_profile is hardcoded false on both
    // response branches (confirmed — view_profile() in
    // routes/student/profile.py never sets it any other way, so viewing
    // your own username through this route would otherwise always say
    // "not your own profile" even though it correctly shows your data).
    // Determined independently here instead, by comparing the profile's
    // own user id against the currently logged-in user — which this
    // page already has to fetch anyway for the nav bar (api.verifyAuth(),
    // called by base.html's own inline script before this module runs).
    const currentUser = await api.verifyAuth().catch(() => null);
    const isOwnProfile = !!(currentUser && data.user && currentUser.id === data.user.id);

    if (data.type === 'private') {
      contentEl.innerHTML = renderPrivateProfile(data, isOwnProfile);
    } else {
      contentEl.innerHTML = renderPublicProfile(data, isOwnProfile);
    }

    contentEl.dataset.userId = data.user.id;
  } catch (error) {
    console.error('Profile view error:', error);
    contentEl.innerHTML = `<div class="pv-error">Could not load this profile. Please try again.</div>`;
  }
}

function setupDelegation() {
  document.addEventListener('click', async (event) => {
    const target = event.target.closest('[data-action]');
    if (!target) return;
    const action = target.dataset.action;
    const userId = parseInt($('pv-content')?.dataset.userId, 10);

    if (action === 'pv-connect' && userId) {
      event.preventDefault();
      target.disabled = true;
      target.textContent = 'Sending…';
      try {
        const res = await profileViewAPI.sendConnectionRequest(userId);
        if (res?.status === 'success') {
          target.textContent = res.data?.connection_status === 'pending'
            ? 'Request Sent' : 'Connected';
        } else {
          target.disabled = false;
          target.textContent = 'Connect';
          alert(res?.message || 'Could not send connection request');
        }
      } catch (error) {
        console.error('Connect error:', error);
        target.disabled = false;
        target.textContent = 'Connect';
      }
    } else if (action === 'pv-message' && userId) {
      event.preventDefault();
      // No standalone single-conversation page exists in this app
      // (messaging lives inside the main SPA's #messages section) — same
      // situation as search.preview.js's post/thread links, so this
      // sends the user to the SPA rather than claiming a deep link into
      // one specific conversation that doesn't exist.
      window.location.href = '/student/profile/homepage';
    } else if (action === 'pv-go-to-connections') {
      event.preventDefault();
      window.location.href = '/student/profile/homepage';
    } else if (action === 'pv-open-post') {
      // Batch 4: posts shown on a profile used to be static, unclickable
      // text (confirmed in the old renderPublicProfile/renderPrivateProfile
      // output — no data-action, no handler). Now navigates to the
      // dedicated post-detail page, same destination search results use.
      const postId = target.dataset.postId;
      if (postId) window.location.href = `/student/posts/view/${postId}`;
    } else if (action === 'pv-open-thread') {
      const threadId = target.dataset.threadId;
      if (threadId) window.location.href = `/student/threads/view/${threadId}`;
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  setupDelegation();
  loadProfile();
});
