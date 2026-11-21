/**
 * ============================================================================
 * HOMEWORK STATS & DASHBOARD
 * Analytics and statistics for homework activity
 * Uses: GET /homework/stats endpoint
 * ============================================================================
 */

import { homeworkAPI } from './homework.api.js';
import { homeworkState } from './homework.state.js';
import { showHomeworkToast, escapeHtml } from './homework.utils.js';
import { renderStreakStats } from './homework.streak.js';
import { renderChampionsInStats } from './homework.champions.js';


/**
 * Get homework statistics
 */
export async function getHomeworkStats() {
  try {
    const response = await homeworkAPI.getStats();
    
    if (response.status === 'success') {
      return response.data;
    } else {
      throw new Error(response.message || 'Failed to load stats');
    }
  } catch (error) {
    console.error('Error loading homework stats:', error);
    return null;
  }
}

/**
 * Render stats dashboard HTML (inline for tab)
 */
export function renderStatsDashboard(stats) {
  if (!stats) {
    showToast('No data found error', 'info');
    return `
      <div class="hw-stats-error">
        <p>Unable to load statistics</p>
        <button class="hw-btn hw-btn-primary" data-action="reload-stats">
          Retry
        </button>
      </div>
    `;
  }

  // Extract data from the actual backend structure
  const myAssignments = stats.my_assignments || {};
  const helpReceived = stats.help_received || {};
  const helpGiven = stats.help_given || {};
  const subjects = stats.subjects || {};

  // Calculate totals and derived stats
  const myWorkActive = (myAssignments.not_started || 0) + (myAssignments.in_progress || 0);
  const myWorkOverdue = myAssignments.overdue || 0;

  return `
    <div class="hw-dashboard">
      <!-- Header -->
      <div class="hw-dashboard-header">
        <h2 class="hw-dashboard-title">📊 Homework Dashboard</h2>
      </div>

      <!-- Overview Cards -->
      <div class="hw-dashboard-overview">
        <div class="hw-overview-card">
          <div class="hw-overview-icon">📚</div>
          <div class="hw-overview-content">
            <div class="hw-overview-value">${myAssignments.total || 0}</div>
            <div class="hw-overview-label">My Assignments</div>
            <div class="hw-overview-breakdown">
              ${myWorkActive} Active • 
              ${myWorkOverdue} Overdue • 
              ${myAssignments.completed || 0} Done
            </div>
          </div>
        </div>

        <div class="hw-overview-card">
          <div class="hw-overview-icon">🎓</div>
          <div class="hw-overview-content">
            <div class="hw-overview-value">${helpGiven.total || 0}</div>
            <div class="hw-overview-label">Help Given</div>
            <div class="hw-overview-breakdown">
              ${helpGiven.pending || 0} Pending • 
              ${helpGiven.submitted || 0} Submitted • 
              ${helpGiven.completed || 0} Complete
            </div>
          </div>
        </div>

        <div class="hw-overview-card">
          <div class="hw-overview-icon">🤝</div>
          <div class="hw-overview-content">
            <div class="hw-overview-value">${helpReceived.total || 0}</div>
            <div class="hw-overview-label">Help Received</div>
            <div class="hw-overview-breakdown">
              ${helpReceived.pending || 0} Pending • 
              ${helpReceived.submitted || 0} Submitted • 
              ${helpReceived.completed || 0} Complete
            </div>
          </div>
        </div>
      </div>

      <!-- My Assignments Breakdown -->
      <div class="hw-dashboard-section">
        <h3 class="hw-section-title">📋 My Assignments Status</h3>
        <div class="hw-status-grid">
          <div class="hw-status-card hw-status-not-started">
            <div class="hw-status-count">${myAssignments.not_started || 0}</div>
            <div class="hw-status-label">Not Started</div>
          </div>
          <div class="hw-status-card hw-status-in-progress">
            <div class="hw-status-count">${myAssignments.in_progress || 0}</div>
            <div class="hw-status-label">In Progress</div>
          </div>
          <div class="hw-status-card hw-status-completed">
            <div class="hw-status-count">${myAssignments.completed || 0}</div>
            <div class="hw-status-label">Completed</div>
          </div>
          <div class="hw-status-card hw-status-shared">
            <div class="hw-status-count">${myAssignments.shared_for_help || 0}</div>
            <div class="hw-status-label">Shared for Help</div>
          </div>
          ${myWorkOverdue > 0 ? `
          <div class="hw-status-card hw-status-overdue">
            <div class="hw-status-count">${myWorkOverdue}</div>
            <div class="hw-status-label">⚠️ Overdue</div>
          </div>
          ` : ''}
        </div>
      </div>

      <!-- Help Given Breakdown -->
      <div class="hw-dashboard-section">
        <h3 class="hw-section-title">🎓 Help Given Breakdown</h3>
        <div class="hw-help-stats">
          <div class="hw-help-stat-item">
            <span class="hw-help-stat-label">Pending:</span>
            <span class="hw-help-stat-value">${helpGiven.pending || 0}</span>
          </div>
          <div class="hw-help-stat-item">
            <span class="hw-help-stat-label">Submitted:</span>
            <span class="hw-help-stat-value">${helpGiven.submitted || 0}</span>
          </div>
          <div class="hw-help-stat-item">
            <span class="hw-help-stat-label">Reviewed:</span>
            <span class="hw-help-stat-value">${helpGiven.reviewed || 0}</span>
          </div>
          <div class="hw-help-stat-item">
            <span class="hw-help-stat-label">Completed:</span>
            <span class="hw-help-stat-value">${helpGiven.completed || 0}</span>
          </div>
        </div>
      </div>

      <!-- Help Received Breakdown -->
      <div class="hw-dashboard-section">
        <h3 class="hw-section-title">🤝 Help Received Breakdown</h3>
        <div class="hw-help-stats">
          <div class="hw-help-stat-item">
            <span class="hw-help-stat-label">Pending:</span>
            <span class="hw-help-stat-value">${helpReceived.pending || 0}</span>
          </div>
          <div class="hw-help-stat-item">
            <span class="hw-help-stat-label">Submitted:</span>
            <span class="hw-help-stat-value">${helpReceived.submitted || 0}</span>
          </div>
          <div class="hw-help-stat-item">
            <span class="hw-help-stat-label">Reviewed:</span>
            <span class="hw-help-stat-value">${helpReceived.reviewed || 0}</span>
          </div>
          <div class="hw-help-stat-item">
            <span class="hw-help-stat-label">Completed:</span>
            <span class="hw-help-stat-value">${helpReceived.completed || 0}</span>
          </div>
        </div>
      </div>

      <!-- Subjects Breakdown -->
      <div class="hw-dashboard-section">
        <h3 class="hw-section-title">📚 My Subjects</h3>
        <div class="hw-subjects-container">
          <div class="hw-subjects-column">
            <h4 class="hw-subjects-subtitle">My Assignments:</h4>
            <div class="hw-subjects-list">
              ${subjects.my_subjects && subjects.my_subjects.length > 0 
                ? subjects.my_subjects.map(subject => `
                  <div class="hw-subject-tag">${escapeHtml(subject)}</div>
                `).join('') 
                : '<p class="hw-empty-message">No subjects yet</p>'}
            </div>
          </div>
          <div class="hw-subjects-column">
            <h4 class="hw-subjects-subtitle">Helping With:</h4>
            <div class="hw-subjects-list">
              ${subjects.helping_with && subjects.helping_with.length > 0 
                ? subjects.helping_with.map(subject => `
                  <div class="hw-subject-tag hw-subject-tag-helping">${escapeHtml(subject)}</div>
                `).join('') 
                : '<p class="hw-empty-message">Not helping with any subjects yet</p>'}
            </div>
          </div>
        </div>
      </div>

      <!-- Streak Widget Placeholder -->
      <div id="streak-widget-placeholder"></div>

      <!-- Champions Section Placeholder -->
      <div id="champions-section-placeholder"></div>
         <div id="charts-section-placeholder"></div>

      <!-- Quick Actions -->
      <div class="hw-dashboard-actions">
        <button class="hw-btn hw-btn-primary" data-action="open-create-homework-modal">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="12" y1="5" x2="12" y2="19"></line>
            <line x1="5" y1="12" x2="19" y2="12"></line>
          </svg>
          New Assignment
        </button>
        <button class="hw-btn hw-btn-secondary" data-action="switch-homework-tab" data-tab="connections-homework">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
            <circle cx="9" cy="7" r="4"/>
            <path d="M23 21v-2a4 4 0 0 0-3-3.87"/>
            <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
          </svg>
          Browse Homework
        </button>
        <button class="hw-btn hw-btn-secondary" data-action="switch-homework-tab" data-tab="my-homework">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
            <polyline points="14 2 14 8 20 8"/>
          </svg>
          My Assignments
        </button>
      </div>
    </div>
  `;
}

/**
 * Load and render stats in container (called by render.js)
 */
export async function loadStatsTab() {
  showToast('Function called');

  let container = document.getElementById('homework-dashboard');
  const el = document.getElementById('stats-tab-panel');

  if (!container) {
    console.error('Stats container not found');

    container = document.querySelector('.homework-stats-dashboard'); // ✅ FIXED
  }
  

  if (!container) {
    showToast("Container not found");
    return;
  }

  // Show loading
  container.innerHTML = `
    <div class="hw-loading-state">
      <div class="hw-spinner"></div>
      <p>Loading statistics...</p>
    </div>
  `;

  try {
    const response = await homeworkAPI.getStats();
    const stats = response.data;

    homeworkState.setStatsData(stats);

    const dashboardHTML = renderStatsDashboard(stats);
    container.innerHTML = dashboardHTML;

    await loadDynamicStatsComponents();

  } catch (error) {
    showHomeworkToast(error.message || 'Failed to load statistics', 'error');

    console.error('Error loading stats:', error);

    container.innerHTML = `
      <div class="hw-error-state">
        <p>Failed to load statistics</p>
        <button class="hw-btn hw-btn-primary" data-action="reload-stats">
          Retry
        </button>
      </div>
    `;
  }
}

/**
 * ============================================================================
 * CHART RENDERING — ADD THESE TO homework_stats.js
 * ============================================================================
 */


// ─── 1. REPLACE loadDynamicStatsComponents (lines 294–317) ──────────────────

export async function loadDynamicStatsComponents() {
  try {
    // Run all 4 async fetches in parallel
    const [streakResponse, championsResponse, chartResponse] = await Promise.all([
      homeworkAPI.getMyStreak(),
      homeworkAPI.getChampions(),
      homeworkAPI.getChartData()   // new endpoint
    ]);

    // Streak
    if (streakResponse.status === 'success') {
      const streakPlaceholder = document.getElementById('streak-widget-placeholder');
      if (streakPlaceholder && streakResponse.data) {
        streakPlaceholder.outerHTML = renderStreakStats(streakResponse.data);
      }
    }

    // Champions
    if (championsResponse.status === 'success') {
      const championsPlaceholder = document.getElementById('champions-section-placeholder');
      if (championsPlaceholder && championsResponse.data) {
        championsPlaceholder.outerHTML = renderChampionsInStats(championsResponse.data);
      }
    }

    // Charts
    if (chartResponse.status === 'success') {
      const chartsPlaceholder = document.getElementById('charts-section-placeholder');
      if (chartsPlaceholder && chartResponse.data) {
        chartsPlaceholder.outerHTML = renderChartsSection(chartResponse.data);
      }
    }

  } catch (error) {
    console.error('Error loading dynamic stats components:', error);
  }
}



/**
 * Master charts section renderer — called with data from /homework/stats/charts
 */
export function renderChartsSection(data) {
  const { daily_activity, subject_completion, reactions_received, response_time } = data;

  const hasActivity    = daily_activity?.some(d => d.helps_given > 0 || d.assignments_created > 0);
  const hasSubjects    = subject_completion?.length > 0;
  const hasReactions   = reactions_received?.some(r => r.count > 0);
  const hasResponseTime = response_time?.average;

  return `
    <div class="hw-charts-section">

      <!-- Response Time Stat Cards (only if data exists) -->
      ${hasResponseTime ? renderResponseTimeCards(response_time) : ''}

      <!-- 7-Day Activity Bar Chart -->
      <div class="hw-dashboard-section">
        <h3 class="hw-section-title">📅 7-Day Activity</h3>
        ${hasActivity
          ? renderActivityBarChart(daily_activity)
          : '<p class="hw-empty-message">No activity in the last 7 days yet.</p>'
        }
      </div>

      <!-- Two-column row: Subject Completion + Reactions -->
      <div class="hw-charts-row">

        <!-- Subject Completion Bars -->
        <div class="hw-dashboard-section hw-chart-col">
          <h3 class="hw-section-title">📚 Subject Completion Rate</h3>
          ${hasSubjects
            ? renderSubjectCompletionBars(subject_completion)
            : '<p class="hw-empty-message">Help with some assignments to see this.</p>'
          }
        </div>

        <!-- Reactions Donut -->
        <div class="hw-dashboard-section hw-chart-col">
          <h3 class="hw-section-title">💬 Reactions Received</h3>
          ${hasReactions
            ? renderReactionDonut(reactions_received)
            : '<p class="hw-empty-message">No reactions received yet.</p>'
          }
        </div>

      </div>
    </div>
  `;
}

/**
 * Response time stat cards
 */
function renderResponseTimeCards(responseTime) {
  return `
    <div class="hw-response-cards">
      <div class="hw-response-card">
        <div class="hw-response-icon">⚡</div>
        <div class="hw-response-value">${responseTime.average}</div>
        <div class="hw-response-label">Avg Response Time</div>
      </div>
      <div class="hw-response-card hw-response-card-accent">
        <div class="hw-response-icon">🚀</div>
        <div class="hw-response-value">${responseTime.fastest}</div>
        <div class="hw-response-label">Fastest Response</div>
      </div>
      <div class="hw-response-card">
        <div class="hw-response-icon">📊</div>
        <div class="hw-response-value">${responseTime.total_timed}</div>
        <div class="hw-response-label">Timed Responses</div>
      </div>
    </div>
  `;
}

/**
 * 7-day grouped bar chart (helps given + assignments created per day)
 */
function renderActivityBarChart(dailyActivity) {
  const maxVal = Math.max(
    ...dailyActivity.map(d => Math.max(d.helps_given, d.assignments_created)),
    1
  );

  return `
    <div class="hw-bar-chart-wrapper">

      <!-- Legend -->
      <div class="hw-chart-legend">
        <span class="hw-legend-dot hw-legend-helps"></span> Helps Given
        <span class="hw-legend-dot hw-legend-created"></span> Assignments Created
      </div>

      <!-- Chart -->
      <div class="hw-bar-chart">
        ${dailyActivity.map(day => {
          const helpsH   = Math.round((day.helps_given / maxVal) * 100);
          const createdH = Math.round((day.assignments_created / maxVal) * 100);
          return `
            <div class="hw-bar-group">
              <div class="hw-bar-pair">
                <div class="hw-bar-col">
                  <div class="hw-bar-segment hw-bar-helps" style="height: ${helpsH}%">
                    ${day.helps_given > 0 ? `<span class="hw-bar-val">${day.helps_given}</span>` : ''}
                  </div>
                </div>
                <div class="hw-bar-col">
                  <div class="hw-bar-segment hw-bar-created" style="height: ${createdH}%">
                    ${day.assignments_created > 0 ? `<span class="hw-bar-val">${day.assignments_created}</span>` : ''}
                  </div>
                </div>
              </div>
              <div class="hw-bar-day-label">${day.day}</div>
            </div>
          `;
        }).join('')}
      </div>
    </div>
  `;
}

/**
 * Horizontal progress bars for subject completion rates
 */
function renderSubjectCompletionBars(subjects) {
  // Sort by total helps desc, cap at 6
  const display = subjects.slice(0, 6);

  return `
    <div class="hw-subject-bars">
      ${display.map(s => `
        <div class="hw-subject-bar-row">
          <div class="hw-subject-bar-label">
            <span class="hw-subject-bar-name">${escapeHtml(s.subject)}</span>
            <span class="hw-subject-bar-rate">${s.rate}%</span>
          </div>
          <div class="hw-subject-bar-track">
            <div 
              class="hw-subject-bar-fill ${s.rate === 100 ? 'hw-bar-complete' : s.rate >= 50 ? 'hw-bar-good' : 'hw-bar-low'}"
              style="width: ${s.rate}%"
            ></div>
          </div>
          <div class="hw-subject-bar-meta">${s.completed}/${s.total} completed</div>
        </div>
      `).join('')}
    </div>
  `;
}

/**
 * CSS-only donut chart for reactions breakdown
 */
function renderReactionDonut(reactions) {
  const total = reactions.reduce((sum, r) => sum + r.count, 0);
  if (total === 0) return '<p class="hw-empty-message">No reactions yet.</p>';

  // Reaction config
  const config = {
    'Thanks 🙏':     { color: '#a78bfa', short: '🙏' },
    'Lifesaver 🔥':  { color: '#f97316', short: '🔥' },
    'Mind Blown 🧠': { color: '#3b82f6', short: '🧠' },
    'Perfect ⭐':    { color: '#eab308', short: '⭐' },
  };

  // Build conic-gradient segments
  let offset = 0;
  const gradientParts = reactions
    .filter(r => r.count > 0)
    .map(r => {
      const pct = (r.count / total) * 100;
      const conf = config[r.reaction] || { color: '#9ca3af' };
      const part = `${conf.color} ${offset}% ${offset + pct}%`;
      offset += pct;
      return part;
    });

  const gradient = `conic-gradient(${gradientParts.join(', ')})`;

  return `
    <div class="hw-donut-wrapper">
      <div class="hw-donut-chart" style="background: ${gradient}">
        <div class="hw-donut-hole">
          <div class="hw-donut-total">${total}</div>
          <div class="hw-donut-total-label">total</div>
        </div>
      </div>

      <div class="hw-donut-legend">
        ${reactions.filter(r => r.count > 0).map(r => {
          const conf = config[r.reaction] || { color: '#9ca3af', short: '•' };
          const pct  = Math.round((r.count / total) * 100);
          return `
            <div class="hw-donut-legend-item">
              <span class="hw-donut-dot" style="background: ${conf.color}"></span>
              <span class="hw-donut-legend-label">${escapeHtml(r.reaction)}</span>
              <span class="hw-donut-legend-count">${r.count} <span class="hw-donut-pct">(${pct}%)</span></span>
            </div>
          `;
        }).join('')}
      </div>
    </div>
  `;
}


/**
 * Load dynamic components (streak, champions) after stats HTML is rendered
 */


/**
 * Render weekly activity chart
 */
function renderWeeklyActivityChart(weeklyData) {
  if (!weeklyData || weeklyData.length === 0) {
    return '<p class="hw-empty-message">No activity this week</p>';
  }

  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const maxActivity = Math.max(...weeklyData.map(d => d.count), 1);

  return `
    <div class="hw-chart">
      ${weeklyData.map((day, index) => {
        const height = (day.count / maxActivity) * 100;
        return `
          <div class="hw-chart-bar-wrapper">
            <div class="hw-chart-bar" style="height: ${height}%">
              <div class="hw-chart-tooltip">${day.count} ${day.count === 1 ? 'activity' : 'activities'}</div>
            </div>
            <div class="hw-chart-label">${days[index]}</div>
          </div>
        `;
      }).join('')}
    </div>
  `;
}
