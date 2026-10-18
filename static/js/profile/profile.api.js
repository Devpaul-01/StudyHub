/**
 * ============================================================================
 * PROFILE API — profile.api.js
 * Save as: /static/js/profile/profile.api.js
 * Uses the global `api` object loaded by /js/core/api.js (non-module script).
 * Same pattern as feed.api.js — no imports needed for `api` or `showToast`.
 * ============================================================================
 */

// ── Profile overview ─────────────────────────────────────────────────────────

export async function fetchProfileData() {
  try {
    const res = await api.get('/profile/me/data');
    return res?.data || null;
  } catch (e) {
    console.error('[profile.api] fetchProfileData:', e);
    showToast('Failed to load profile', 'error');
    return null;
  }
}

// ── Posts ─────────────────────────────────────────────────────────────────────

export async function fetchMyPosts(type = 'all') {
  try {
    const res = await api.get(`/profile/my-posts?type=${encodeURIComponent(type)}`);
    return res?.data?.posts || [];
  } catch (e) {
    console.error('[profile.api] fetchMyPosts:', e);
    showToast('Failed to load posts', 'error');
    return [];
  }
}

// ── Stats + heatmap ──────────────────────────────────────────────────────────

export async function fetchMyStats() {
  try {
    const res = await api.get('/profile/my-stats');
    return res?.data || null;
  } catch (e) {
    console.error('[profile.api] fetchMyStats:', e);
    return null;
  }
}

export async function fetchActivityHeatmap() {
  try {
    const res = await api.get('/analytics/activity-heatmap?days=90');
    return res?.data || null;
  } catch (e) {
    console.error('[profile.api] fetchActivityHeatmap:', e);
    return null;
  }
}

// ── Connections ───────────────────────────────────────────────────────────────

export async function fetchMyConnections() {
  try {
    const res = await api.get('/connections/list');
    // /connections/list returns { status, data: [...], total }
    return Array.isArray(res?.data) ? res.data : [];
  } catch (e) {
    console.error('[profile.api] fetchMyConnections:', e);
    showToast('Failed to load connections', 'error');
    return [];
  }
}

// ── Reputation ────────────────────────────────────────────────────────────────

export async function fetchReputation() {
  try {
    const res = await api.get('/reputation/me');
    return res?.data || null;
  } catch (e) {
    console.error('[profile.api] fetchReputation:', e);
    return null;
  }
}

export async function fetchReputationHistory() {
  try {
    const res = await api.get('/reputation/history?per_page=50');
    return res?.data || null;
  } catch (e) {
    console.error('[profile.api] fetchReputationHistory:', e);
    return null;
  }
}

// Batch 2: GET /reputation/levels already existed on the backend (public,
// no auth required) with nothing on the frontend calling it — there was
// no "how does reputation work?" screen anywhere, just the progress bar
// toward the next level with no explanation of how points are earned.
export async function fetchReputationLevels() {
  try {
    const res = await api.get('/reputation/levels');
    return res?.data || null;
  } catch (e) {
    console.error('[profile.api] fetchReputationLevels:', e);
    return null;
  }
}

// ── Profile editing ───────────────────────────────────────────────────────────

export async function saveProfileEdit(payload) {
  try {
    const res = await api.patch('/profile/update', payload);
    return res;
  } catch (e) {
    console.error('[profile.api] saveProfileEdit:', e);
    return null;
  }
}

export async function addLearningGoal(goal) {
  try {
    return await api.post('/profile/learning-goals', { goal });
  } catch (e) {
    console.error('[profile.api] addLearningGoal:', e);
    return null;
  }
}

export async function removeLearningGoal(index) {
  try {
    return await api.delete(`/profile/learning-goals/${index}`);
  } catch (e) {
    console.error('[profile.api] removeLearningGoal:', e);
    return null;
  }
}

// ── Avatar ────────────────────────────────────────────────────────────────────

export async function uploadAvatar(file) {
  try {
    const formData = new FormData();
    formData.append('avatar', file);
    // 3rd arg true = multipart — same as uploadResource() in feed.api.js
    return await api.post('/profile/avatar/upload', formData, true);
  } catch (e) {
    console.error('[profile.api] uploadAvatar:', e);
    return null;
  }
}

export async function removeAvatar() {
  try {
    return await api.delete('/profile/avatar');
  } catch (e) {
    console.error('[profile.api] removeAvatar:', e);
    return null;
  }
}

// ── Academic info ─────────────────────────────────────────────────────────────

export async function fetchAcademicInfo() {
  try {
    const res = await api.get('/profile/academic-info');
    return res?.data || null;
  } catch (e) {
    console.error('[profile.api] fetchAcademicInfo:', e);
    return null;
  }
}

export async function saveAcademicInfo(payload) {
  try {
    return await api.put('/profile/academic-info', payload);
  } catch (e) {
    console.error('[profile.api] saveAcademicInfo:', e);
    return null;
  }
}

// ── Post actions (profile context) ───────────────────────────────────────────

export async function pinPost(postId) {
  try {
    return await api.post(`/profile/pin-post/${postId}`);
  } catch (e) {
    console.error('[profile.api] pinPost:', e);
    return null;
  }
}

export async function unpinPost(postId) {
  try {
    return await api.post(`/profile/unpin-post/${postId}`);
  } catch (e) {
    console.error('[profile.api] unpinPost:', e);
    return null;
  }
}

export async function deletePost(postId) {
  try {
    return await api.delete(`/posts/${postId}`);
  } catch (e) {
    console.error('[profile.api] deletePost:', e);
    return null;
  }
}
