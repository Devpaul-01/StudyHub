// Notification State Management
class NotificationState {
  constructor() {
    this.hasMore = true;
    this.loading = false;
    this.lastCursor = null;
    // Batch 2: the active filter chip ('' means "All" — see
    // NOTIFICATION_TYPES in notification.constants.js). loadNotifications()
    // reads this to scope its request, and "Mark all as read" sends it too,
    // so marking read while a filter is active only marks that category.
    this.activeType = '';
  }

  setActiveType(type) {
    this.activeType = type || '';
  }

  getActiveType() {
    return this.activeType;
  }

  updateHasMore(value) {
    this.hasMore = value;
  }

  updateCursor(cursor) {
    this.lastCursor = cursor;
  }

  getLastCursor() {
    return this.lastCursor;
  }

  setLoading(value) {
    this.loading = value;
  }

  isLoading() {
    return this.loading;
  }

  canLoadMore() {
    return this.hasMore && !this.loading;
  }

  reset() {
    this.hasMore = true;
    this.loading = false;
    this.lastCursor = null;
  }
}

// Export singleton instance
export const notificationState = new NotificationState();