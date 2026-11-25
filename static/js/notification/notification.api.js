// Refined Notification API Calls
// Fetches all notifications at once instead of cursor-based pagination

export const notificationAPI = {
  /**
   * Get all notifications at once
   * No cursor implementation - fetches everything in one call
   */
  async getNotifications(type) {
    // Batch 2: accept an optional type filter (see NOTIFICATION_TYPES in
    // notification.constants.js) — '/profile/notifications/all' already
    // supported a ?type= query param on the backend, nothing on the
    // frontend passed one.
    const qs = type ? `?type=${encodeURIComponent(type)}` : '';
    const response = await api.get(`/profile/notifications/all${qs}`);
    return response;
  },

  async toggleEnableNotification(data) {
    const response = await api.post("/profile/notifications/settings", data);
    return response;
  },

  async toggleEnableNotificationSound(data) {
    const response = await api.post("/profile/notifications/settings", data);
    return response;
  },

  async deleteNotification(id) {
    const response = await api.delete(`/profile/notifications/${id}`);
    return response;
  },

  async markAsRead(id) {
    const response = await api.post(`/profile/notifications/${id}/mark-read`);
    return response;
  },

  /**
   * Batch 2: mark every notification read, optionally scoped to one type
   * (see NOTIFICATION_TYPES in notification.constants.js for valid values).
   */
  async markAllRead(type) {
    const response = await api.post('/profile/notifications/mark-all-read', type ? { type } : {});
    return response;
  },

  /**
   * Batch 2: delete notifications in bulk. With no args, deletes
   * everything; pass { readOnly: true } to only delete already-read
   * notifications ("Clear read"), and/or { type } to scope to one
   * category.
   */
  async deleteAll({ type, readOnly } = {}) {
    const params = new URLSearchParams();
    if (type) params.set('type', type);
    if (readOnly) params.set('read_only', 'true');
    const qs = params.toString();
    const response = await api.delete(`/profile/notifications/delete-all${qs ? `?${qs}` : ''}`);
    return response;
  },

  // Count only (audit NOTIF-07): don't download the list to read one number.
  async getUnreadCount() {
    const response = await api.get('/profile/notifications/unread-count');
    return { count: response.data?.unread_count || 0 };
  }
};
