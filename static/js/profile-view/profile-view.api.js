/**
 * ============================================================================
 * PROFILE-VIEW API — profile-view.api.js
 * Uses the global `api` client from core/api.js (loaded by base.html —
 * see templates/profile-view.html's extra_js block), same as search.api.js.
 * ============================================================================
 */

export const profileViewAPI = {
  /**
   * GET /profile/<username> — confirmed field-by-field against
   * view_profile() in routes/student/profile.py. Response shape differs
   * by data.type: "public" (full profile) or "private" (a few basic
   * fields plus a privacy notice) — see profile-view.templates.js for
   * how both are rendered.
   */
  async fetchProfile(username) {
    return await api.get(`/profile/${encodeURIComponent(username)}`);
  },

  /** POST /connections/request/<user_id> — already used elsewhere in this app. */
  async sendConnectionRequest(userId) {
    return await api.post(`/connections/request/${userId}`);
  },
};
