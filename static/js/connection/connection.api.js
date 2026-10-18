// ============================================================================
// CONNECTION API (Fixed)
// ============================================================================

import { CONNECTION_ENDPOINTS } from './connection.constants.js';

export const ConnectionAPI = {
  
  // Load all connection data at once
  async loadAllConnectionData() {
    try {
      const [received, sent, suggestions, connected, discovery] = await Promise.all([
        api.get(CONNECTION_ENDPOINTS.RECEIVED),
        api.get(CONNECTION_ENDPOINTS.SENT),
        api.get(CONNECTION_ENDPOINTS.SUGGESTIONS),
        api.get(CONNECTION_ENDPOINTS.CONNECTED),
        api.get(CONNECTION_ENDPOINTS.DISCOVERY)
      ]);

      return {
        received: received?.data || [],
        sent: sent?.data || [],
        suggestions: suggestions?.data || {},
        connected: connected?.data || [],
        discovery: discovery?.data.discoveries || []
      };
    } catch (error) {
      console.error('Load all connection data error:', error);
      throw error;
    }
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
  
  
  async getSessionDetails(sessionId) {
  try {
    const response = await api.get(`/study-session/${sessionId}/details`);
    return response;
  } catch(error) {
    console.error("Error getting session details:", error);
    throw error;
  }
},

async rescheduleSession(sessionData, sessionId) {
  try {
    const response = await api.post(`/study-session/${sessionId}/reschedule`, sessionData);
    return response;
  } catch(error) {
    console.error("Error rescheduling session:", error);
    showToast(`Error saving session changes ${error.message}`, 'error');
    
  }
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

  async getBlockedUsers() {
    const response = await api.get(CONNECTION_ENDPOINTS.BLOCKED_LIST);
    return response?.data || [];
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
  
  // ============================================================================
  // STUDY SESSIONS API (FIXED & COMPLETE)
  // ============================================================================
  
  async getStudySessions(userId, filter = 'all') {
    try {
      const response = await api.get(`/study-session/all?partner_id=${userId}&status=${filter}`);
      return response;
    } catch (error) {
      console.error('Get study sessions error:', error);
      throw error;
    }
  },
  
  async createStudySession(sessionData) {
    try {
      const response = await api.post('/study-session/request', sessionData);
      return response;
    } catch (error) {
      console.error('Create study session error:', error);
      throw error;
    }
  },
  
  async confirmStudySession(sessionId, confirmedTime) {
    try {
      const response = await api.post(`/study-session/${sessionId}/confirm`, {
        confirmed_time: confirmedTime
      });
      return response;
    } catch (error) {
      console.error('Confirm study session error:', error);
      throw error;
    }
  },
  
  async cancelStudySession(sessionId, reason) {
    try {
      const response = await api.post(`/study-session/${sessionId}/cancel`, {reason:reason});
      return response;
    } catch (error) {
      console.error('Cancel study session error:', error);
      throw error;
    }
  },
  async declineStudySession(sessionId, reason, isWithdrawal=false) {
    try {
      const response = await api.post(`/study-session/${sessionId}/decline`, {is_withdrawal: isWithdrawal, reason:reason});
      return response;
    } catch (error) {
      console.error('Decline study session error:', error);
      throw error;
    }
  },
  async getStudySession(sessionId) {
    try {
      const response = await api.get(`/study-session/${sessionId}/details`);
      return response;
    } catch (error) {
      console.error('Get study session error:', error);
      throw error;
    }
  },
  async sendSessionChanges(content,sessionId) {
    try {
      const response = await api.post(`/study-session/${sessionId}/reschedule`, content);
      return response;
    } catch (error) {
      console.error('Save study session error:', error);
      throw error;
    }
  },
  async uploadResource(file) {
    try{
      const formData = new FormData();
      formData.append("file", file);
      return await api.post("/posts/resource/upload", formData, true);
    }
    catch(error){
      showToast('Error encounterd uploading fils', 'error');
    }
 },
  

  // Connection notes
  async getConnectionNotes(connectionId) {
    const response = await api.get(`/connections/${connectionId}/notes`);
    return response?.data.notes || null;
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
