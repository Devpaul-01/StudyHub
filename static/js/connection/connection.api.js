// ============================================================================
// CONNECTION API (Fixed)
// ============================================================================

import { CONNECTION_ENDPOINTS } from './connection.constants.js';

export const ConnectionAPI = {
  
  // Fetch every page of a paginated list endpoint (?limit/?cursor).
  // Audit CON-15: the list endpoints now paginate and report has_more /
  // next_cursor; the UI still shows full lists, so follow the cursor
  // (bounded, so a bug can never loop forever).
  async fetchAllPages(endpoint, maxPages = 10) {
    const sep = endpoint.includes('?') ? '&' : '?';
    let cursor = null;
    let items = [];
    for (let page = 0; page < maxPages; page++) {
      const response = await api.get(cursor ? `${endpoint}${sep}cursor=${encodeURIComponent(cursor)}` : endpoint);
      items = items.concat(response?.data || []);
      if (!response?.has_more || !response?.next_cursor) break;
      cursor = response.next_cursor;
    }
    return { data: items };
  },

  // Load all connection data at once
  async loadAllConnectionData() {
    // allSettled: one failing/rate-limited section must not blank the whole
    // page; only fail if EVERYTHING failed.
    const results = await Promise.allSettled([
      this.fetchAllPages(CONNECTION_ENDPOINTS.RECEIVED),
      this.fetchAllPages(CONNECTION_ENDPOINTS.SENT),
      api.get(CONNECTION_ENDPOINTS.SUGGESTIONS),
      this.fetchAllPages(CONNECTION_ENDPOINTS.CONNECTED),
      api.get(CONNECTION_ENDPOINTS.DISCOVERY)
    ]);

    results.forEach((r, i) => {
      if (r.status === 'rejected') console.error(`Load connection data (section ${i}) failed:`, r.reason);
    });
    if (results.every(r => r.status === 'rejected')) {
      throw results[0].reason;
    }

    const value = (i) => (results[i].status === 'fulfilled' ? results[i].value : null);
    const [received, sent, suggestions, connected, discovery] = [0, 1, 2, 3, 4].map(value);

    return {
      received: received?.data || [],
      sent: sent?.data || [],
      suggestions: suggestions?.data || {},
      connected: connected?.data || [],
      discovery: discovery?.data?.discoveries || []
    };
  },
  async broadcastHelpRequest(subject, message = '') {
    const response = await api.post('/connections/help/broadcast', { subject, message });
    return response;
  },

  async volunteerForHelp(requestId) {
    const response = await api.post(`/connections/help/${requestId}/volunteer`);
    return response;
  },

  async getHelpVolunteers(requestId) {
    const response = await api.get(`/connections/help/${requestId}/volunteers`);
    return response?.data || null;
  },
  // Load badge counts
  async loadConnectionBadges() {
    try {
      const [received, sent] = await Promise.all([
        api.get(CONNECTION_ENDPOINTS.UNSEEN_RECEIVED),
        api.get(CONNECTION_ENDPOINTS.UNSEEN_SENT)
      ]);

      return {
        received: received?.data?.count || 0,
        sent: sent?.data?.count || 0
      };
    } catch (error) {
      console.error('Load badges error:', error);
      return { received: 0, sent: 0 };
    }
  },

  // Mark connections as seen
  async markReceivedSeen() {
    try {
      const response = await api.post(CONNECTION_ENDPOINTS.MARK_RECEIVED_SEEN);
      return response;
    } catch (error) {
      console.error('Mark received seen error:', error);
      throw error;
    }
  },

  async markSentSeen() {
    try {
      const response = await api.post(CONNECTION_ENDPOINTS.MARK_SENT_SEEN);
      return response;
    } catch (error) {
      console.error('Mark sent seen error:', error);
      throw error;
    }
  },

  // Connection actions
  async sendConnectionRequest(userId, message='') {
    const response = await api.post(`${CONNECTION_ENDPOINTS.CONNECT}/${userId}`, {message});
    return response;
  },

  async acceptRequest(connectionId) {
    const response = await api.post(`${CONNECTION_ENDPOINTS.ACCEPT}/${connectionId}`);
    return response;
  },

  async rejectRequest(connectionId) {
    const response = await api.post(`${CONNECTION_ENDPOINTS.REJECT}/${connectionId}`);
    return response;
  },

  async cancelRequest(connectionId) {
    const response = await api.delete(`${CONNECTION_ENDPOINTS.CANCEL}/${connectionId}`);
    return response;
  },

  // Block/Unblock
  async blockUser(userId) {
    const response = await api.post(`${CONNECTION_ENDPOINTS.BLOCK}/${userId}`);
    return response;
  },

  async unblockUser(userId) {
    const response = await api.post(`${CONNECTION_ENDPOINTS.UNBLOCK}/${userId}`);
    return response;
  },

  // Batch 2: "unfriend" — see the comment on CONNECTION_ENDPOINTS.REMOVE.
  async removeConnection(userId) {
    const response = await api.delete(`${CONNECTION_ENDPOINTS.REMOVE}/${userId}`);
    return response;
  },

  // Batch 3: lightweight online count, for a tab badge — avoids pulling
  // the whole online-connections list just to show a number.
  async getOnlineCount(timeWindow) {
    const qs = timeWindow ? `?time_window=${timeWindow}` : '';
    const response = await api.get(`${CONNECTION_ENDPOINTS.ONLINE_COUNT}${qs}`);
    return response;
  },

  // Batch 3: online connections filtered to the user's own department.
  async getOnlineByDepartment(timeWindow) {
    const qs = timeWindow ? `?time_window=${timeWindow}` : '';
    const response = await api.get(`${CONNECTION_ENDPOINTS.ONLINE_DEPARTMENT}${qs}`);
    return response;
  },

  // Batch 3: connections who are online, can help with `subject`, or
  // whose study schedule says they're free right now — scored and
  // sorted by the backend. `subject` is optional.
  async getAvailableNow(subject) {
    const qs = subject ? `?subject=${encodeURIComponent(subject)}` : '';
    const response = await api.get(`${CONNECTION_ENDPOINTS.AVAILABLE_NOW}${qs}`);
    return response;
  },

  // Batch 3: the full connection-health/shared-threads/mutuals/messaging
  // detail card. Deliberately does not surface the response's
  // study_history section — the study-sessions feature it covered has
  // been removed from main.
  async getConnectionDetails(connectionId) {
    const response = await api.get(`${CONNECTION_ENDPOINTS.DETAILS}/${connectionId}/details`);
    return response;
  },

  async getBlockedUsers() {
    const response = await api.get(CONNECTION_ENDPOINTS.BLOCKED_LIST);
    // Audit CON-13: the endpoint returns {blocked_users: [...], total}, not an
    // array - returning response.data made the modal always fail to render.
    return response?.data?.blocked_users || [];
  },

  // Search
  async searchConnections(query) {
    const response = await api.get(`${CONNECTION_ENDPOINTS.SEARCH}?search=${encodeURIComponent(query)}`);
    return response?.data || [];
  },

  // Mutuals
  async getMutualConnections(userId) {
    const response = await api.get(`${CONNECTION_ENDPOINTS.MUTUALS}/${userId}`);
    return response;
  },

  // Online connections
  async getOnlineConnections() {
    const response = await api.get(CONNECTION_ENDPOINTS.ONLINE);
    return response?.data || [];
  },

  // User overview (AI streaming)
  getOverviewStream(userId) {
    return fetch(`${CONNECTION_ENDPOINTS.OVERVIEW}/${userId}`, {
      headers: {
        'Authorization': `Bearer ${localStorage.getItem('token')}`
      }
    });
  },
  
  // Connection notes
  async getConnectionNotes(connectionId) {
    const response = await api.get(`/connections/${connectionId}/notes`);
    return response?.data?.notes ?? null;
  },

  async createConnectionNote(connectionId, notes) {
    const response = await api.post(`/connections/${connectionId}/notes/update`, { notes });
    return response;
  },

  // Settings
  async toggleConnectionNotification(enabled) {
    const response = await api.post('/connections/settings', {
      enable_sound: enabled
    });
    return response;
  },

  // Form thread from connection
  async formThread(payload) {
    const response = await api.post('/threads/create', payload);
    return response;
  }
};
