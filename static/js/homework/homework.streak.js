/**
 * ============================================================================
 * HOMEWORK STREAK TRACKING
 * Display and manage user's help streak
 * ============================================================================
 */

import { homeworkAPI } from './homework.api.js';
import { homeworkState } from './homework.state.js';
import { showHomeworkToast } from './homework.utils.js';

/**
 * Load user's streak data
 */
export async function loadStreakData() {
  try {
    const response = await homeworkAPI.getMyStreak();
    
    if (response.status === 'success') {
      homeworkState.setStreakData(response.data);
      updateStreakWidget(response.data);
      
      // Show warning if streak at risk
      if (response.data.streak_at_risk && response.data.current_streak >= 3) {
        showStreakWarning(response.data.current_streak);
      }
    }
  } catch (error) {
    console.error('Error loading streak:', error);
  }
}

/**
 * Update streak widget display
 */
export function updateStreakWidget(streakData) {
  const widget = document.getElementById('streak-widget');
  if (!widget) return;
  
  const { current_streak, longest_streak, streak_at_risk, helped_today } = streakData;
  
  let statusClass = '';
  let statusHTML = '';
  
  if (current_streak === 0) {
    // No streak yet
    statusClass = 'streak-none';
    statusHTML = `
      <div class="streak-message streak-start">
        <div class="streak-message-icon">💡</div>
        <div class="streak-message-text">Help someone to start your streak!</div>
      </div>
    `;
  } else if (streak_at_risk && !helped_today) {
    // Streak at risk
    statusClass = 'streak-at-risk';
    statusHTML = `
      <div class="streak-message streak-warning">
        <div class="streak-message-icon">⚠️</div>
        <div class="streak-message-text">Help someone today to keep your streak alive!</div>
      </div>
    `;
  } else if (helped_today) {
    // Streak safe for today
    statusClass = 'streak-safe';
    statusHTML = `
      <div class="streak-message streak-success">
        <div class="streak-message-icon">✅</div>
        <div class="streak-message-text">Streak saved for today!</div>
      </div>
    `;
  }
  
  widget.innerHTML = `
    <div class="streak-container ${statusClass}">
      <div class="streak-main">
        <div class="streak-flame-container">
          <div class="streak-flame">${current_streak > 0 ? '🔥' : '💤'}</div>
          ${current_streak > 7 ? '<div class="streak-sparkle">✨</div>' : ''}
        </div>
        <div class="streak-info">
          <div class="streak-current">${current_streak}</div>
          <div class="streak-label">day${current_streak !== 1 ? 's' : ''} streak</div>
          ${longest_streak > current_streak ? 
            `<div class="streak-record">Best: ${longest_streak} days 🏆</div>` : 
            current_streak > 0 ? '<div class="streak-record">New record! 🎉</div>' : ''
          }
        </div>
      </div>
      ${statusHTML}
    </div>
  `;
}

/**
 * Show streak warning notification
 */
function showStreakWarning(streakCount) {
  const existingWarning = document.querySelector('.streak-warning-toast');
  if (existingWarning) return; // Don't spam warnings
  
  const warning = document.createElement('div');
  warning.className = 'streak-warning-toast';
  warning.innerHTML = `
    <div class="streak-warning-content">
      <div class="streak-warning-icon">⚠️</div>
      <div class="streak-warning-text">
        <strong>Streak at risk!</strong>
        <p>Help someone today to keep your ${streakCount}-day streak</p>
      </div>
      <button class="streak-warning-close" data-action="close-streak-warning">×</button>
    </div>
  `;
  
  document.body.appendChild(warning);
  
  // Auto-remove after 10 seconds
  setTimeout(() => {
    warning.classList.add('fade-out');
    setTimeout(() => warning.remove(), 300);
  }, 10000);
}

/**
 * Close streak warning
 */
export function closeStreakWarning() {
  const warning = document.querySelector('.streak-warning-toast');
  if (warning) {
    warning.classList.add('fade-out');
    setTimeout(() => warning.remove(), 300);
  }
}

/**
 * Show streak celebration animation
 */
export function showStreakCelebration(streakDays, isNewRecord = false) {
  const celebration = document.createElement('div');
  celebration.className = 'streak-celebration';
  
  const title = isNewRecord ? '🎉 New Record!' : '🔥 Streak Updated!';
  const emoji = streakDays >= 30 ? '🔥🔥🔥' : streakDays >= 14 ? '🔥🔥' : '🔥';
  
  celebration.innerHTML = `
    <div class="celebration-content">
      <div class="celebration-emoji">${emoji}</div>
      <h2 class="celebration-title">${title}</h2>
      <p class="celebration-days">${streakDays} days!</p>
      ${isNewRecord ? '<p class="celebration-subtitle">Your best streak yet!</p>' : ''}
    </div>
  `;
  
  document.body.appendChild(celebration);
  
  // Add animation class
  setTimeout(() => celebration.classList.add('show'), 10);
  
  // Remove after 3 seconds
  setTimeout(() => {
    celebration.classList.remove('show');
    celebration.classList.add('fade-out');
    setTimeout(() => celebration.remove(), 500);
  }, 3000);
}

/**
 * Render streak stats for dashboard
 */
export function renderStreakStats(streakData) {
  if (!streakData || streakData.current_streak === 0) {
    return `
      <div class="streak-stats-empty">
        <div class="streak-stats-icon">💤</div>
        <p>Start your helping streak today!</p>
      </div>
    `;
  }
  
  const { current_streak, longest_streak, helped_today } = streakData;
  const progress = longest_streak > 0 ? (current_streak / longest_streak) * 100 : 100;
  
  return `
    <div class="streak-stats">
      <div class="streak-stats-header">
        <h4>🔥 Your Streak</h4>
        ${helped_today ? '<span class="streak-badge-safe">Safe today</span>' : '<span class="streak-badge-risk">At risk</span>'}
      </div>
      
      <div class="streak-stats-main">
        <div class="streak-stats-current">
          <div class="streak-number">${current_streak}</div>
          <div class="streak-label">days</div>
        </div>
        
        <div class="streak-stats-progress">
          <div class="progress-bar">
            <div class="progress-fill" style="width: ${progress}%"></div>
          </div>
          <div class="progress-label">
            ${current_streak === longest_streak ? 
              'New record!' : 
              `${longest_streak - current_streak} to beat your record`
            }
          </div>
        </div>
      </div>
      
      <div class="streak-stats-footer">
        <div class="streak-stat-item">
          <span class="stat-label">Best:</span>
          <span class="stat-value">${longest_streak} days 🏆</span>
        </div>
      </div>
    </div>
  `;
}

/**
 * Get motivational message based on streak
 */
export function getStreakMotivation(streakCount) {
  if (streakCount === 0) {
    return "Help someone to start your streak! 🚀";
  } else if (streakCount < 3) {
    return "Great start! Keep it going! 💪";
  } else if (streakCount < 7) {
    return "You're on fire! 🔥";
  } else if (streakCount < 14) {
    return "Amazing dedication! 🌟";
  } else if (streakCount < 30) {
    return "Incredible streak! You're unstoppable! ⚡";
  } else {
    return "LEGENDARY STREAK! 👑";
  }
}
