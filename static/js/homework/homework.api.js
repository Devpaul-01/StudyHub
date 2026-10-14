/**
 * ============================================================================
 * HOMEWORK API CALLS
 * All backend communication for homework section
 * ============================================================================
 */

/**
 * Strip null/undefined/empty values from a params object before building
 * a query string — otherwise things like `cursor: null` end up as the
 * literal string "null" in the URL.
 */
function cleanParams(params = {}) {
  return Object.fromEntries(
    Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== '')
  );
}

export const homeworkAPI = {
  /**
   * Get my assignments
   * Accepts { status, subject, difficulty, sort, limit, cursor }
   */
  async getMyAssignments(params = {}) {
    const queryParams = new URLSearchParams(cleanParams(params)).toString();
    const url = `/assignments${queryParams ? '?' + queryParams : ''}`;
    return await api.get(url);
  },
  async getChartData(){
    return await api.get('/homework/stats/charts');
},

  /**
   * Create new assignment
   */
  async createAssignment(data) {
    return await api.post('/assignments', data);
  },

  /**
   * Update assignment
   */
  async updateAssignment(id, data) {
    return await api.put(`/assignments/${id}`, data);
  },

  /**
   * Delete assignment
   */
  async deleteAssignment(id) {
    return await api.delete(`/assignments/${id}`);
  },

  /**
   * Toggle assignment status
   */
  async toggleAssignmentStatus(id, status) {
    return await api.post(`/assignments/${id}/quick-actions`, {action:status });
  },

  /**
   * Share assignment for help
   *
   * Batch 1 fix: POST /assignments/<id>/share-for-help is not a real
   * route — sharing for help is one of the actions handled by the
   * existing quick-actions endpoint (see
   * routes/student/homework_system.py's quick_actions(), which accepts
   * action: "mark_complete" | "start_working" | "share_for_help" |
   * "unshare"), so this now calls that instead of a 404.
   */
  async shareAssignmentForHelp(id) {
    return await api.post(`/assignments/${id}/quick-actions`, { action: 'share_for_help' });
  },

  /**
   * Unshare assignment
   *
   * Batch 1 fix: same issue as shareAssignmentForHelp() above —
   * POST /assignments/<id>/unshare isn't a real route either; "unshare"
   * is also just an action value for quick-actions.
   */
  async unshareAssignment(id) {
    return await api.post(`/assignments/${id}/quick-actions`, { action: 'unshare' });
  },
  

  /**
   * Get connections homework feed
   * Accepts { subject, difficulty, sort, limit, cursor }
   */
  async getConnectionsHomework(params = {}) {
    const queryParams = new URLSearchParams(cleanParams(params)).toString();
    const url = `/homework/feed${queryParams ? '?' + queryParams : ''}`;
    return await api.get(url);
  },

  /**
   * Offer to help with homework
   */
  async offerHelp(assignmentId, message = '') {
    return await api.post(`/homework/${assignmentId}/offer-help`, { message });
  },

  /**
   * Get my help requests (people helping me)
   */
  async getMyHelpRequests(id) {
    const url = `/homework/${id}/helpers`;
    return await api.get(url);
  },

  /**
   * Get homework I'm helping with
   */
  async getHelpingWith(params = {}) {
    const queryParams = new URLSearchParams(cleanParams(params)).toString();
    const url = `/homework/helping-with${queryParams ? '?' + queryParams : ''}`;
    return await api.get(url);
  },

  /**
   * Get submission details
   */
  async getSubmissionDetails(submissionId) {
    return await api.get(`/homework/submission/${submissionId}`);
  },

  /**
   * Submit solution
   */
  async submitSolution(submissionId, data) {
    return await api.post(`/homework/submission/${submissionId}/submit-solution`, data);
  },

  /**
   * Give feedback on solution
   */
  async giveFeedback(submissionId, data) {
    return await api.post(`/homework/submission/${submissionId}/give-feedback`, data);
  },

  /**
   * Upload resource for homework
   */
  async uploadResource(file) {
    const formData = new FormData();
    formData.append('file', file);
    return await api.post('/posts/resource/upload', formData, true);
  },

  /**
   * Quick actions on assignment (start, complete, reopen, etc.)
   */
  async quickAction(assignmentId, action) {
    return await api.post(`/assignments/${assignmentId}/quick-actions`, { action });
  },

  /**
   * Cancel submission (before solution submitted)
   */
  async cancelSubmission(submissionId) {
    return await api.delete(`/homework/submission/${submissionId}/cancel`);
  },

  /**
   * Get homework statistics
   */
  async getStats() {
    return await api.get('/homework/stats');
  },
  async getMyStreak() {
  return await api.get('/homework/my-streak');
},

async getChampions() {
  return await api.get('/homework/champions');
},

async getActivityFeed() {
  return await api.get('/activity/feed');
}
};
