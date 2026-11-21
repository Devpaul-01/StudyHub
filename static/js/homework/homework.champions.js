/**
 * ============================================================================
 * WEEKLY CHAMPIONS
 * Display and track weekly subject champions
 * ============================================================================
 */

import { homeworkAPI } from './homework.api.js';
import { showHomeworkToast, escapeHtml, safeUrl } from './homework.utils.js';

/**
 * Load current week's champions
 */
export async function loadChampions() {
  try {
    const response = await homeworkAPI.getChampions();
    
    if (response.status === 'success') {
      return response.data;
    }
    return null;
  } catch (error) {
    console.error('Error loading champions:', error);
    return null;
  }
}

/**
 * Render champions banner for homework section
 */
export function renderChampionsBanner(championsData) {
  if (!championsData || !championsData.champions) {
    return '';
  }
  
  const { champions, your_progress } = championsData;
  const hasChampions = champions.subject_champions.length > 0 || 
                       champions.most_helpful || 
                       champions.fastest_helper;
  
  if (!hasChampions) {
    return `
      <div class="champions-banner champions-empty">
        <div class="champions-empty-icon">🏆</div>
        <p>No champions yet this week</p>
        <p class="champions-empty-hint">Be the first to help others!</p>
      </div>
    `;
  }
  
  return `
    <div class="champions-banner">
      <div class="champions-header">
        <h3 class="champions-title">🏆 This Week's Champions</h3>
        <div class="champions-week">${formatWeekRange(championsData.week_start, championsData.week_end)}</div>
      </div>
      
      <div class="champions-grid">
        ${renderMostHelpful(champions.most_helpful)}
        ${renderSubjectChampions(champions.subject_champions)}
        ${renderFastestHelper(champions.fastest_helper)}
      </div>
      
      ${renderYourProgress(your_progress, champions)}
    </div>
  `;
}

/**
 * Render most helpful overall champion
 */
function renderMostHelpful(champion) {
  if (!champion) return '';
  
  const crownClass = champion.is_you ? 'champion-card-you' : '';
  
  return `
    <div class="champion-card champion-card-featured ${crownClass}">
      <div class="champion-badge">👑</div>
      <div class="champion-avatar-wrapper">
        <img src="${escapeHtml(safeUrl(champion.user.avatar) || '/static/default-avatar.png')}" 
             alt="${escapeHtml(champion.user.name)}" 
             class="champion-avatar" />
        ${champion.is_you ? '<div class="champion-you-badge">You!</div>' : ''}
      </div>
      <div class="champion-name">${escapeHtml(champion.user.name)}</div>
      <div class="champion-title">Most Helpful</div>
      <div class="champion-count">${champion.help_count} help${champion.help_count !== 1 ? 's' : ''}</div>
    </div>
  `;
}

/**
 * Render subject champions (show top 3-5)
 */
function renderSubjectChampions(champions) {
  if (!champions || champions.length === 0) return '';
  
  // Show max 4 subject champions
  const displayChampions = champions.slice(0, 4);
  
  return displayChampions.map(champion => {
    const crownClass = champion.is_you ? 'champion-card-you' : '';
    const subjectEmoji = getSubjectEmoji(champion.subject);
    
    return `
      <div class="champion-card ${crownClass}">
        <div class="champion-subject-badge">${subjectEmoji}</div>
        <div class="champion-avatar-wrapper">
          <img src="${escapeHtml(safeUrl(champion.user.avatar) || '/static/default-avatar.png')}" 
               alt="${escapeHtml(champion.user.name)}" 
               class="champion-avatar" />
          ${champion.is_you ? '<div class="champion-you-badge">You!</div>' : ''}
        </div>
        <div class="champion-name">${escapeHtml(champion.user.name)}</div>
        <div class="champion-title">${escapeHtml(champion.subject)}</div>
        <div class="champion-count">${champion.help_count} help${champion.help_count !== 1 ? 's' : ''}</div>
      </div>
    `;
  }).join('');
}

/**
 * Render fastest helper champion
 */
function renderFastestHelper(champion) {
  if (!champion) return '';
  
  const crownClass = champion.is_you ? 'champion-card-you' : '';
  
  return `
    <div class="champion-card ${crownClass}">
      <div class="champion-badge">⚡</div>
      <div class="champion-avatar-wrapper">
        <img src="${escapeHtml(safeUrl(champion.user.avatar) || '/static/default-avatar.png')}" 
             alt="${escapeHtml(champion.user.name)}" 
             class="champion-avatar" />
        ${champion.is_you ? '<div class="champion-you-badge">You!</div>' : ''}
      </div>
      <div class="champion-name">${escapeHtml(champion.user.name)}</div>
      <div class="champion-title">Fastest Helper</div>
      <div class="champion-count">${champion.help_count} response${champion.help_count !== 1 ? 's' : ''}</div>
    </div>
  `;
}

/**
 * Render user's progress toward championship
 */
function renderYourProgress(progress, champions) {
  if (!progress || progress.total_helps === 0) return '';
  
  // Find closest subject champion to beat
  const closestChampion = findClosestChampion(progress, champions);
  
  return `
    <div class="your-progress">
      <div class="progress-header">
        <strong>Your Progress This Week:</strong>
        <span class="progress-total">${progress.total_helps} help${progress.total_helps !== 1 ? 's' : ''}</span>
      </div>
      
      ${closestChampion ? `
        <div class="progress-hint">
          <span class="hint-icon">💡</span>
          <span class="hint-text">
            ${closestChampion.gap} more help${closestChampion.gap !== 1 ? 's' : ''} in ${escapeHtml(closestChampion.subject)} 
            to become champion!
          </span>
        </div>
      ` : ''}
      
      <div class="progress-breakdown">
        ${Object.entries(progress.by_subject).map(([subject, count]) => `
          <div class="progress-subject">
            <span class="progress-subject-name">${getSubjectEmoji(subject)} ${escapeHtml(subject)}</span>
            <span class="progress-subject-count">${count}</span>
          </div>
        `).join('')}
      </div>
    </div>
  `;
}

/**
 * Find closest champion that user could beat
 */
function findClosestChampion(progress, champions) {
  let closest = null;
  let smallestGap = Infinity;
  
  for (const [subject, count] of Object.entries(progress.by_subject)) {
    const subjectChampion = champions.subject_champions.find(c => c.subject === subject);
    
    if (subjectChampion && !subjectChampion.is_you) {
      const gap = subjectChampion.help_count - count;
      
      // Only show if within reach (5 helps or less)
      if (gap > 0 && gap <= 5 && gap < smallestGap) {
        smallestGap = gap;
        closest = {
          subject,
          gap,
          champion: subjectChampion
        };
      }
    }
  }
  
  return closest;
}

/**
 * Get emoji for subject
 */
function getSubjectEmoji(subject) {
  const emojiMap = {
    'Mathematics': '📐',
    'Math': '📐',
    'Physics': '⚛️',
    'Chemistry': '🧪',
    'Biology': '🧬',
    'Programming': '💻',
    'Computer Science': '💻',
    'CS': '💻',
    'English': '📚',
    'History': '📜',
    'Geography': '🌍',
    'Economics': '💰',
    'Psychology': '🧠',
    'Engineering': '⚙️',
    'Art': '🎨',
    'Music': '🎵',
    'General': '📖'
  };
  
  return emojiMap[subject] || '📚';
}

/**
 * Format week range
 */
function formatWeekRange(startDate, endDate) {
  const start = new Date(startDate);
  const end = new Date(endDate);
  
  const options = { month: 'short', day: 'numeric' };
  const startStr = start.toLocaleDateString('en-US', options);
  const endStr = end.toLocaleDateString('en-US', options);
  
  return `${startStr} - ${endStr}`;
}

/**
 * Render champions for stats dashboard
 */
export function renderChampionsInStats(championsData) {
  if (!championsData) {
    return '<div class="champions-loading">Loading champions...</div>';
  }
  
  const { champions, your_progress } = championsData;
  
  return `
    <div class="hw-dashboard-section">
      <h3 class="hw-section-title">🏆 Weekly Champions</h3>
      ${renderChampionsBanner(championsData)}
    </div>
  `;
}

/**
 * Listen for champion notifications
 */
export function initChampionsWebSocket() {
  if (!window.socket) return;
  
  window.socket.on('became_champion', (data) => {
    console.log('Became champion:', data);
    
    // Show celebration
    showChampionCelebration(data.champion_type, data.subject);
    
    // Reload champions
    loadChampions();
  });
}

/**
 * Show champion celebration animation
 */
function showChampionCelebration(championType, subject) {
  const celebration = document.createElement('div');
  celebration.className = 'champion-celebration';
  
  let title = '🏆 You\'re a Champion!';
  let subtitle = championType;
  
  if (subject) {
    title = `🏆 ${subject} Champion!`;
    subtitle = 'You helped the most people this week!';
  }
  
  celebration.innerHTML = `
    <div class="celebration-content">
      <div class="celebration-crown">👑</div>
      <h2 class="celebration-title">${escapeHtml(title)}</h2>
      <p class="celebration-subtitle">${escapeHtml(subtitle)}</p>
    </div>
  `;
  
  document.body.appendChild(celebration);
  
  setTimeout(() => celebration.classList.add('show'), 10);
  
  setTimeout(() => {
    celebration.classList.remove('show');
    celebration.classList.add('fade-out');
    setTimeout(() => celebration.remove(), 500);
  }, 4000);
  
  // Also show toast
  showHomeworkToast(title, 'success');
}
