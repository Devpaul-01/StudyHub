/**
 * ============================================================================
 * HOMEWORK ACTIVITY FEED
 * Real-time feed showing what connections are working on
 * ============================================================================
 */

import { homeworkAPI } from '../homework/homework.api.js';
import { showHomeworkToast } from '../homework/homework.utils.js';

let activityCache = [];
let lastActivityId = null;

/**
 * Load activity feed
 */
export async function loadActivityFeed() {
  try {
    const response = await homeworkAPI.getActivityFeed();
    
    if (response.status === 'success') {
      activityCache = response.data.activities || [];
      renderActivityFeed(activityCache);
      
      if (activityCache.length > 0) {
        lastActivityId = activityCache[0].id;
      }
    }
  } catch (error) {
    console.error('Error loading activity feed:', error);
    renderActivityError();
  }
}

/**
 * Render activity feed in container
 */
export function renderActivityFeed(activities) {
  const container = document.getElementById('activity-feed-list');
  if (!container) return;
  
  if (!activities || activities.length === 0) {
    container.innerHTML = renderEmptyState();
    return;
  }
  
  const html = activities.map(activity => renderActivityItem(activity)).join('');
  container.innerHTML = html;
}

/**
 * Render empty state
 */
function renderEmptyState() {
  return `
    <div class="activity-empty">
      <div class="activity-empty-icon">
        <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <circle cx="12" cy="12" r="10"></circle>
          <path d="M12 8v4l3 3"></path>
        </svg>
      </div>
      <h4 class="activity-empty-title">No recent activity</h4>
      <p class="activity-empty-text">Activities from your connections in the last 2 hours will appear here</p>
      <p class="activity-empty-hint">Be the first to get started! 🚀</p>
    </div>
  `;
}

/**
 * Render error state
 */
function renderActivityError() {
  const container = document.getElementById('activity-feed-list');
  if (!container) return;
  
  container.innerHTML = `
    <div class="activity-error">
      <div class="activity-error-icon">⚠️</div>
      <p>Failed to load activity feed</p>
      <button class="hw-btn hw-btn-primary" data-action="reload-activity-feed">
        Try Again
      </button>
    </div>
  `;
}

/**
 * Render single activity item
 */
function renderActivityItem(activity) {
  const { id, type, user, data, time_ago } = activity;
  
  const { icon, message, badge, actionButton } = getActivityDisplay(type, user, data);
  const onlineIndicator = user.is_online ? '<div class="activity-online-dot"></div>' : '';
  
  return `
    <div class="activity-item" data-activity-id="${id}">
      <div class="activity-icon-wrapper">
        <div class="activity-icon">${icon}</div>
      </div>
      
      <div class="activity-content">
        <div class="activity-header">
          <div class="activity-user-info">
            <img src="${user.avatar || '/static/images/default-avatar.png'}" 
                 alt="${user.name}" 
                 class="activity-avatar" />
            ${onlineIndicator}
          </div>
          <div class="activity-time">${time_ago}</div>
        </div>
        
        <div class="activity-message">${message}</div>
        
        ${badge ? `<div class="activity-badges">${badge}</div>` : ''}
        ${actionButton ? `<div class="activity-action">${actionButton}</div>` : ''}
      </div>
    </div>
  `;
}

/**
 * Get display info for activity type
 */
function getActivityDisplay(type, user, data) {
  let icon, message, badge = '', actionButton = '';
  
  switch (type) {
    case 'started_homework':
      icon = data.difficulty === 'hard' ? '🔥' : '🚀';
      message = `<strong>${user.name}</strong> started <strong>${data.title}</strong>`;
      
      if (data.difficulty) {
        badge = `<span class="activity-badge badge-${data.difficulty}">${data.difficulty}</span>`;
      }
      
      if (data.needs_help) {
        badge += '<span class="activity-badge badge-help">Needs Help</span>';
        actionButton = `
          <button class="activity-btn" 
                  data-action="offer-help-from-activity" 
                  data-user-id="${user.id}"
                  data-assignment-title="${data.title}">
            🤝 Offer Help
          </button>
        `;
      }
      break;
      
    case 'offered_help':
      icon = '🤝';
      message = `<strong>${user.name}</strong> is helping <strong>${data.requester_name}</strong> with ${data.subject}`;
      
      if (data.subject) {
        badge = `<span class="activity-badge badge-subject">${data.subject}</span>`;
      }
      break;
      
    case 'completed_homework':
      icon = '✅';
      message = `<strong>${user.name}</strong> completed <strong>${data.assignment_title}</strong>`;
      
      if (data.helper_count && data.helper_count > 1) {
        badge = `<span class="activity-badge badge-helpers">👥 ${data.helper_count} helpers</span>`;
      }
      
      if (data.subject) {
        badge += `<span class="activity-badge badge-subject">${data.subject}</span>`;
      }
      break;
      
    case 'submitted_solution':
      icon = '📝';
      message = `<strong>${user.name}</strong> submitted solution to <strong>${data.requester_name}</strong>`;
      
      if (data.subject) {
        badge = `<span class="activity-badge badge-subject">${data.subject}</span>`;
      }
      break;
      
    case 'achieved_streak':
      icon = '🔥';
      message = `<strong>${user.name}</strong> reached a <strong>${data.streak_count}-day</strong> helping streak!`;
      badge = '<span class="activity-badge badge-achievement">Achievement</span>';
      break;
      
    default:
      icon = '📚';
      message = `<strong>${user.name}</strong> did something`;
  }
  
  return { icon, message, badge, actionButton };
}

/**
 * Add new activity to feed (from WebSocket)
 */
export function addActivityToFeed(activity) {
  // Add to cache
  activityCache.unshift(activity);
  
  // Keep only last 50
  if (activityCache.length > 50) {
    activityCache.pop();
  }
  
  // Get container
  const container = document.getElementById('activity-feed-list');
  if (!container) return;
  
  // If feed was empty, replace empty state
  const emptyState = container.querySelector('.activity-empty');
  if (emptyState) {
    renderActivityFeed(activityCache);
    return;
  }
  
  // Otherwise, prepend new item
  const newItemHTML = renderActivityItem(activity);
  container.insertAdjacentHTML('afterbegin', newItemHTML);
  
  // Animate in
  const newItem = container.querySelector(`[data-activity-id="${activity.id}"]`);
  if (newItem) {
    newItem.style.opacity = '0';
    newItem.style.transform = 'translateY(-20px)';
    
    setTimeout(() => {
      newItem.style.transition = 'all 0.3s ease';
      newItem.style.opacity = '1';
      newItem.style.transform = 'translateY(0)';
    }, 10);
    
    // Add highlight effect
    setTimeout(() => {
      newItem.classList.add('activity-new');
      setTimeout(() => newItem.classList.remove('activity-new'), 2000);
    }, 300);
  }
  
  // Remove oldest if too many visible
  const items = container.querySelectorAll('.activity-item');
  if (items.length > 50) {
    items[items.length - 1].remove();
  }
}

/**
 * Initialize activity feed WebSocket
 */
 /*
export function initActivityFeedWebSocket() {
  if (!window.socket) {
    console.warn('Socket not initialized for activity feed');
    return;
  }
  
  console.log('✅ Activity feed WebSocket initialized');
  
  // Listen for new activities
  window.socket.on('new_activity', (data) => {
    console.log('📡 New activity received:', data);
    addActivityToFeed(data);
  });
}
*/

/**
 * Auto-refresh feed (backup for WebSocket)
 */
export function startActivityFeedAutoRefresh() {
  // Refresh every 2 minutes
  setInterval(() => {
    loadActivityFeed();
  }, 120000);
}

/**
 * Pull to refresh handler
 */
export function handleActivityRefresh() {
  const container = document.getElementById('activity-feed-container');
  if (!container) return;
  
  // Add refreshing class
  container.classList.add('refreshing');
  
  // Reload feed
  loadActivityFeed().then(() => {
    // Remove refreshing class after short delay
    setTimeout(() => {
      container.classList.remove('refreshing');
      showHomeworkToast('Activity feed refreshed', 'success');
    }, 500);
  });
}

/**
 * Initialize activity feed section
 */
function initActivityFeedSection() {
  // Load initial data
  loadActivityFeed();
  
  // Setup WebSocket

  
  // Setup auto-refresh
  startActivityFeedAutoRefresh();
  
  console.log('✅ Activity feed initialized');
}

/**
 * Cleanup activity feed (when section hidden)
 */
export function cleanupActivityFeed() {
  // Clear cache
  activityCache = [];
  lastActivityId = null;
}

/**
 * Get activity feed stats for summary
 */
export function getActivityFeedStats() {
  const activityTypes = activityCache.reduce((acc, activity) => {
    acc[activity.type] = (acc[activity.type] || 0) + 1;
    return acc;
  }, {});
  
  return {
    total: activityCache.length,
    types: activityTypes,
    latest: activityCache[0] || null
  };
}
document.addEventListener("DOMContentLoaded", initActivityFeedSection);