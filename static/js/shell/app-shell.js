/**
 * ============================================================================
 * APP SHELL — app-shell.js
 * Shared navbar behavior for standalone pages (search, profile-view,
 * post-view, thread-view). Replaces the inline <script> block in
 * templates/base.html, which is not used by these pages — see the
 * header comment in static/css/shell/app-shell.css for why.
 *
 * Fixes carried over from auditing base.html's inline script:
 *  - loadNotificationCounts() referenced document.getElementById(
 *    'messages-badge') which doesn't exist anywhere in that markup,
 *    throwing "Cannot read properties of null (reading 'style')" on
 *    every single page load (confirmed in a real browser). Dropped —
 *    only #msg-count (which does exist) is updated here.
 *  - The dropdown linked to /student/leaderboard, /student/analytics,
 *    /student/connections, /student/notifications and
 *    /student/ai-assistant, none of which are real page routes (all
 *    confirmed 404 against the running app) — those only exist as
 *    sections inside the main app shell (templates/post/feed2.html).
 *    Fixed to link at /student/profile/homepage?section=<id>, which
 *    feed.init.js now reads on load to jump straight to that section
 *    (see the matching fix there).
 * ============================================================================
 */

function $(id) { return document.getElementById(id); }

async function initShellAuth() {
  try {
    const user = await api.verifyAuth();
    if (user) {
      updateShellUserInfo(user);
      loadShellNotificationCounts();
    } else {
      redirectIfNeeded();
    }
  } catch (error) {
    console.error('[app-shell] Auth error:', error);
    redirectIfNeeded();
  }
}

function updateShellUserInfo(user) {
  const nameEl = $('shell-user-name');
  const avatarEl = $('shell-user-avatar');
  if (nameEl) nameEl.textContent = '@' + user.username;
  if (avatarEl && user.avatar) avatarEl.src = user.avatar;
}

function redirectIfNeeded() {
  // Every page that loads this shell is itself an authenticated page
  // (search, profile-view, post-view, thread-view all sit behind
  // @token_required), so a failed auth check always means "go log in" —
  // no need for the path-prefix allowlist base.html used, since there's
  // no public page here that loads this script.
  window.location.href = '/student/login';
}

async function loadShellNotificationCounts() {
  try {
    const notifRes = await api.get('/profile/notifications/all');
    const notifCount = notifRes.data?.unread_count ?? 0;
    const notifBadge = $('shell-notif-count');
    if (notifBadge) {
      notifBadge.textContent = notifCount;
      notifBadge.style.display = notifCount > 0 ? 'flex' : 'none';
    }

    const msgRes = await api.get('/messages/conversations');
    const msgCount = msgRes.data?.total_unread ?? 0;
    const msgBadge = $('shell-msg-count');
    if (msgBadge) {
      msgBadge.textContent = msgCount;
      msgBadge.style.display = msgCount > 0 ? 'flex' : 'none';
    }
  } catch (error) {
    console.error('[app-shell] Failed to load notification counts:', error);
  }
}

function setupShellListeners() {
  const dropdownBtn = $('shell-profile-dropdown-btn');
  const dropdownMenu = $('shell-profile-dropdown-menu');
  if (dropdownBtn && dropdownMenu) {
    dropdownBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      dropdownMenu.classList.toggle('hidden');
    });
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.shell-profile-dropdown')) {
        dropdownMenu.classList.add('hidden');
      }
    });
  }

  const searchInput = $('shell-search-input');
  if (searchInput) {
    searchInput.addEventListener('keypress', (e) => {
      if (e.key === 'Enter') {
        const query = searchInput.value.trim();
        window.location.href = query
          ? `/student/search?q=${encodeURIComponent(query)}`
          : '/student/search';
      }
    });
  }

  const logoutBtn = $('shell-logout-btn');
  if (logoutBtn) {
    logoutBtn.addEventListener('click', () => {
      if (confirm('Are you sure you want to logout?')) {
        localStorage.removeItem('studyhub_token');
        window.location.href = '/student/login';
      }
    });
  }
}

document.addEventListener('DOMContentLoaded', () => {
  setupShellListeners();
  initShellAuth();
});
