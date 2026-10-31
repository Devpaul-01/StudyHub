/**
 * ============================================================================
 * NOTIFICATION HANDLERS FOR UNIFIED DELEGATION
 * Exports click handlers to be merged into app.unified.js
 * ============================================================================
 */

import {
  handleDeleteNotification, toggleNotificationSettings,
  handleMarkAllRead, handleClearReadNotifications, handleFilterNotifications,
} from './notification.events.js';
import { notificationAPI } from './notification.api.js';
import { updateBadgeCount } from './notification.utils.js';

/**
 * All notification click handlers
 * These will be spread into UNIFIED_ACTIONS in app.unified.js
 */
export const NotificationHandlers = {
  /**
   * View/navigate to notification link
   */
  'view-notification': async (target, event, containerType) => {
    const notificationId = target.dataset.notificationId;
    const link = target.dataset.link;
    const isRead = target.classList.contains('read');
    
    // Mark as read if unread
    if (!isRead && notificationId) {
      try {
        const response = await notificationAPI.markAsRead(notificationId);
        
        if (response.status === 'success') {
          // Update UI to show as read
          target.classList.add('read');
          
          // Update badge count (decrease by 1)
          const badgeElement = document.querySelector('[data-action="navigate-to"][data-target="notifications"] .notification-badge');
          if (badgeElement) {
            const currentCount = parseInt(badgeElement.textContent) || 0;
            const newCount = Math.max(0, currentCount - 1);
            if (newCount > 0) {
              badgeElement.textContent = newCount;
              badgeElement.style.display = 'flex';
            } else {
              badgeElement.style.display = 'none';
            }
          }
        }
      } catch (error) {
        console.error('Error marking notification as read:', error);
        // Continue with navigation even if mark-as-read fails
      }
    }
    
    // Navigate to link if present
    if (link && link !== '' && link !== 'null' && link !== 'undefined') {
      window.location.href = link;
    }
  },
  
  /**
   * Toggle notification settings modal
   */
  'toggle-notification-options': (target, event, containerType) => {
    event.stopPropagation();
    toggleNotificationSettings();
  },
  
  /**
   * Delete a notification
   */
  'delete-notification': (target, event, containerType) => {
    event.stopPropagation();
    const id = target.dataset.notificationId;
    if (id) {
      handleDeleteNotification(id, event);
    }
  },

  // Batch 2 additions below ------------------------------------------------

  /**
   * Mark every (optionally filtered) notification as read.
   */
  'mark-all-notifications-read': (target, event) => {
    event.stopPropagation();
    toggleNotificationSettings(); // close the options dropdown
    handleMarkAllRead();
  },

  /**
   * Delete every already-read (optionally filtered) notification.
   */
  'clear-read-notifications': (target, event) => {
    event.stopPropagation();
    toggleNotificationSettings(); // close the options dropdown
    handleClearReadNotifications();
  },

  /**
   * Switch the active notification type filter chip.
   */
  'filter-notifications': (target, event) => {
    event.stopPropagation();
    handleFilterNotifications(target.dataset.type || '');
  },
};
