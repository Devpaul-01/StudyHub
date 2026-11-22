/**
 * ============================================================================
 * HOMEWORK UTILITY FUNCTIONS
 * Helper functions for homework section
 * ============================================================================
 */

/**
 * Escape a value for safe interpolation into HTML text or a quoted attribute.
 * Every server-supplied string rendered through innerHTML MUST go through this
 * (titles, subjects, descriptions, names, solution/feedback text, filenames).
 * null/undefined become ''. Numbers/booleans are stringified.
 */
export function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
    .replace(/`/g, '&#96;');
}

/**
 * Return the URL only if it is safe to put in href/src/iframe: http(s) or a
 * same-site absolute path. Anything else (javascript:, data:, vbscript:, //host
 * protocol-relative tricks, control characters) yields ''.
 * The result is NOT escaped - wrap it with escapeHtml() when interpolating.
 */
export function safeUrl(url) {
  if (typeof url !== 'string') return '';
  const trimmed = url.trim();
  // Reject control characters / whitespace smuggling inside the scheme.
  if (/[\u0000-\u001f\u007f]/.test(trimmed)) return '';
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  if (/^\/(?!\/)/.test(trimmed)) return trimmed;
  return '';
}

/**
 * Format time until due
 */
export function formatTimeUntilDue(hoursUntilDue) {
  if (hoursUntilDue < 0) {
    const hoursOverdue = Math.abs(hoursUntilDue);
    if (hoursOverdue < 24) {
      return `${Math.floor(hoursOverdue)}h overdue`;
    } else {
      const daysOverdue = Math.floor(hoursOverdue / 24);
      return `${daysOverdue}d overdue`;
    }
  } else if (hoursUntilDue < 1) {
    return `${Math.floor(hoursUntilDue * 60)}m left`;
  } else if (hoursUntilDue < 24) {
    return `${Math.floor(hoursUntilDue)}h left`;
  } else {
    const daysLeft = Math.floor(hoursUntilDue / 24);
    return `${daysLeft}d left`;
  }
}

/**
 * Get urgency color class
 */
export function getUrgencyColorClass(urgencyLevel) {
  const colorMap = {
    'critical': 'hw-urgency-critical',
    'high': 'hw-urgency-high',
    'medium': 'hw-urgency-medium',
    'low': 'hw-urgency-low'
  };
  return colorMap[urgencyLevel] || 'hw-urgency-medium';
}

/**
 * Get difficulty badge class
 */
export function getDifficultyBadgeClass(difficulty) {
  const classMap = {
    'easy': 'hw-difficulty-easy',
    'medium': 'hw-difficulty-medium',
    'hard': 'hw-difficulty-hard'
  };
  return classMap[difficulty] || 'hw-difficulty-medium';
}

/**
 * Get status badge class
 */
export function getStatusBadgeClass(status) {
  const classMap = {
    'not_started': 'hw-status-not-started',
    'in_progress': 'hw-status-in-progress',
    'completed': 'hw-status-completed',
    'pending': 'hw-status-pending',
    'submitted': 'hw-status-submitted',
    'reviewed': 'hw-status-reviewed'
  };
  return classMap[status] || 'hw-status-not-started';
}

/**
 * Get status display text
 */
export function getStatusDisplayText(status) {
  const textMap = {
    'not_started': 'Not Started',
    'in_progress': 'In Progress',
    'completed': 'Completed',
    'pending': 'Pending',
    'submitted': 'Submitted',
    'reviewed': 'Reviewed'
  };
  return textMap[status] || status;
}

/**
 * Get difficulty emoji
 */
export function getDifficultyEmoji(difficulty) {
  const emojiMap = {
    'easy': '🟢',
    'medium': '🟡',
    'hard': '🔴'
  };
  return emojiMap[difficulty] || '🟡';
}

/**
 * Format date
 */
export function formatDate(dateString) {
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = date - now;
  const diffHours = diffMs / (1000 * 60 * 60);
  
  if (diffHours < -24) {
    // Past date
    return date.toLocaleDateString('en-US', { 
      month: 'short', 
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    });
  } else if (diffHours < 0) {
    // Today but past
    return date.toLocaleTimeString('en-US', { 
      hour: '2-digit', 
      minute: '2-digit' 
    });
  } else if (diffHours < 24) {
    // Today
    return 'Today ' + date.toLocaleTimeString('en-US', { 
      hour: '2-digit', 
      minute: '2-digit' 
    });
  } else if (diffHours < 48) {
    // Tomorrow
    return 'Tomorrow ' + date.toLocaleTimeString('en-US', { 
      hour: '2-digit', 
      minute: '2-digit' 
    });
  } else {
    // Future
    return date.toLocaleDateString('en-US', { 
      month: 'short', 
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    });
  }
}

/**
 * Get file type icon
 */
export function getFileTypeIcon(type) {
  const iconMap = {
    'pdf': '📄',
    'document': '📄',
    'image': '🖼️',
    'video': '🎥',
    'audio': '🎵'
  };
  return iconMap[type] || '📎';
}

/**
 * Get file type from filename
 */
export function getFileType(filename) {
  const ext = filename.split('.').pop().toLowerCase();
  
  const imageExts = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg'];
  const videoExts = ['mp4', 'webm', 'ogg', 'mov', 'avi'];
  const documentExts = ['pdf', 'doc', 'docx', 'txt', 'rtf'];
  const audioExts = ['mp3', 'wav', 'ogg', 'aac'];
  
  if (imageExts.includes(ext)) return 'image';
  if (videoExts.includes(ext)) return 'video';
  if (documentExts.includes(ext)) return 'document';
  if (audioExts.includes(ext)) return 'audio';
  
  return 'file';
}

/**
 * Render user online status
 */
export function renderUserStatus(activeDetails) {
  if (!activeDetails) {
    return '<span class="hw-user-status-offline">Offline</span>';
  }
  
  if (activeDetails.in_study_session) {
    return `
      <span class="hw-user-status-study">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
          <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
          <polyline points="7.5 4.21 12 6.81 16.5 4.21"/>
          <polyline points="7.5 19.79 7.5 14.6 3 12"/>
          <polyline points="21 12 16.5 14.6 16.5 19.79"/>
          <polyline points="3.27 6.96 12 12.01 20.73 6.96"/>
          <line x1="12" y1="22.08" x2="12" y2="12"/>
        </svg>
        Studying
      </span>
    `;
  }
  
  if (activeDetails.is_online) {
    return `
      <span class="hw-user-status-online">
        <span class="hw-status-dot"></span>
        Online
      </span>
    `;
  }
  
  if (activeDetails.last_active) {
    return `<span class="hw-user-status-offline">${escapeHtml(activeDetails.last_active)}</span>`;
  }
  
  return '<span class="hw-user-status-offline">Offline</span>';
}

/**
 * Truncate text
 */
export function truncateText(text, maxLength = 100) {
  if (!text) return '';
  if (text.length <= maxLength) return text;
  return text.substring(0, maxLength) + '...';
}

/**
 * Show toast notification
 */
export function showHomeworkToast(message, type = 'info') {
  if (typeof showToast === 'function') {
    showToast(message, type);
  } else {
    console.log(`[${type.toUpperCase()}] ${message}`);
  }
}

/**
 * Convert a value from <input type="datetime-local"> (wall-clock time in the
 * user's timezone, no offset) into an unambiguous UTC ISO-8601 instant that
 * the backend stores verbatim. Returns '' for empty/invalid input.
 */
export function localInputToUtcIso(value) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toISOString();
}

/**
 * Convert a server timestamp into a datetime-local input value in the user's
 * timezone. The API emits naive UTC strings ("2026-12-01T10:00:00", no 'Z'),
 * which JS would otherwise parse as *local* time and shift by the UTC offset.
 */
export function serverUtcToLocalInput(value) {
  if (!value) return '';
  const raw = String(value);
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/i.test(raw);
  const date = new Date(hasZone ? raw : `${raw}Z`);
  if (Number.isNaN(date.getTime())) return '';
  const pad = (n) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
         `T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
