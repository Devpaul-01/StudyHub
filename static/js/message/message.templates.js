/**
 * Message System HTML Templates - PRODUCTION
 * Generates HTML for conversations, messages, and UI components
 */

import { getReactionEmoji, getResourceIcon } from './message.constants.js';
import { formatFileSize, formatMessageTime, formatConversationTime } from './message.utils.js';

// ============================================================================
// CONVERSATION LIST TEMPLATES
// ============================================================================

/**
 * Generate conversation list item HTML
 */
export function conversationItemTemplate(conversation) {
  const partner = conversation.partner;
  const lastMessage = conversation.last_message;
  const unreadCount = conversation.unread_count || 0;
  const isOnline = conversation.partner_online || false;

  const lastMessagePreview = lastMessage
    ? `${lastMessage.is_own ? 'You: ' : ''}${lastMessage.body ? lastMessage.body.substring(0, 50) + (lastMessage.body.length > 50 ? '...' : '') : '📎 Attachment'}`
    : 'No messages yet';

  const timeAgo = lastMessage ? formatConversationTime(lastMessage.sent_at) : '';

  return `
    <div class="flex items-center gap-3 px-4 py-3 bg-[var(--bg-card)] hover:bg-[var(--bg-hover)] active:bg-[var(--bg-surface)]
                transition-colors cursor-pointer conversation-item"
         data-action="open-conversation"
         data-partner-id="${partner.id}">

      <!-- Avatar -->
      <div class="relative flex-shrink-0">
        <img src="${partner.avatar || '/static/default-avatar.png'}"
             alt="${escapeHtml(partner.name)}"
             loading="lazy"
             class="w-12 h-12 rounded-full object-cover bg-[var(--bg-surface)]">
        <!-- Online dot -->
        <span class="absolute bottom-0 right-0 w-3 h-3 rounded-full border-2 border-[var(--bg-card)]
                     ${isOnline ? 'bg-[var(--success)]' : 'bg-[var(--text-tertiary)]'}"></span>
      </div>

      <!-- Info -->
      <div class="flex-1 min-w-0">
        <div class="flex items-center justify-between gap-2">
          <span class="text-sm font-semibold text-[var(--text-primary)] truncate">${escapeHtml(partner.name)}</span>
          ${timeAgo ? `<span class="text-xs text-[var(--text-meta)] flex-shrink-0">${timeAgo}</span>` : ''}
        </div>
        <div class="flex items-center justify-between gap-2 mt-0.5">
          <p class="text-xs text-[var(--text-secondary)] truncate ${unreadCount > 0 ? 'font-semibold text-[var(--text-primary)]' : ''}">
            ${escapeHtml(lastMessagePreview)}
          </p>
          ${unreadCount > 0 ? `
            <span class="flex-shrink-0 min-w-[1.25rem] h-5 px-1.5 bg-[var(--accent)] text-white
                         text-xs font-bold rounded-full flex items-center justify-center">
              ${unreadCount > 99 ? '99+' : unreadCount}
            </span>
          ` : ''}
        </div>
      </div>
    </div>
  `;
}

/**
 * Empty state for conversation list
 */
export function emptyConversationsTemplate() {
  return `
    <div class="empty-state">
      <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
      </svg>
      <h3>No messages yet</h3>
      <p>Start a conversation with your connections</p>
    </div>
  `;
}

/**
 * Loading skeleton for conversation list
 */
export function conversationSkeletonTemplate() {
  return Array(5).fill(0).map(() => `
    <div class="conversation-item skeleton">
      <div class="conversation-avatar skeleton-circle"></div>
      <div class="conversation-info">
        <div class="skeleton-line skeleton-title"></div>
        <div class="skeleton-line skeleton-preview"></div>
      </div>
    </div>
  `).join('');
}

// ============================================================================
// MESSAGE TEMPLATES
// ============================================================================

/**
 * Generate message HTML
 */
 export function messageTemplate(message, currentUserId) {
  const isOwn        = message.sender_id === currentUserId;
  const senderName   = message.sender?.name || 'Unknown';
  const senderAvatar = message.sender?.avatar || '/static/default-avatar.png';
  const timestamp    = formatMessageTime(message.sent_at);

  const statusClass = message.status ? `message-${message.status}` : '';
  const failedClass = message.status === 'failed' ? 'message-failed' : '';

  // Bubble colors (own = accent, partner = white) are owned by message.css
  // via `.message-own .message-bubble` / `.message-partner .message-bubble`.
  // Don't add competing bg/text utility classes here — a compound class
  // selector in CSS always beats a single utility class on the same
  // element, so they'd be silently overridden anyway.
  const metaColorClasses = isOwn ? 'text-white/70' : 'text-[var(--text-tertiary)]';

  return `
    <div class="message-wrapper message ${isOwn ? 'message-own' : 'message-partner'} ${statusClass} ${failedClass}"
         data-message-id="${message.id || ''}"
         data-temp-id="${message.client_temp_id || ''}"
         data-is-own="${isOwn}"
         data-sent-at="${message.sent_at || ''}"
         data-sender-id="${message.sender_id || ''}">

      ${!isOwn ? `
        <div class="message-avatar">
          <img src="${senderAvatar}" alt="${senderName}" loading="lazy">
        </div>
      ` : ''}

      <div class="message-content">
        ${!isOwn ? `<div class="message-sender text-[var(--text-secondary)]">${escapeHtml(senderName)}</div>` : ''}

        <div class="message-bubble">
          ${message.body ? `
            <div class="message-text">${escapeHtml(message.body)}</div>
          ` : ''}

          ${message.resources && message.resources.length > 0 ? `
            <div class="message-resources">
              ${message.resources.map(r => resourceTemplate(r)).join('')}
            </div>
          ` : ''}

          <div class="message-meta ${metaColorClasses}">
            <span class="message-time">${timestamp}</span>
            ${isOwn && message.is_read
              ? '<span class="message-read-indicator">✓✓</span>'
              : ''}
            ${isOwn && message.status === 'sent' && !message.is_read
              ? '<span class="message-sent-indicator">✓</span>'
              : ''}
            ${message.status === 'pending'
              ? '<span class="message-pending-indicator">⏱</span>'
              : ''}
          </div>
        </div>

        ${message.reactions && Object.keys(message.reactions).length > 0 ? `
          <div class="message-reactions">
            ${Object.entries(message.reactions).map(([type, data]) => `
              <span class="reaction-badge flex items-center gap-0.5 text-xs bg-[var(--bg-surface)] border
                           border-[var(--border-light)] rounded-full px-2 py-0.5 shadow-sm cursor-pointer
                           hover:bg-[var(--accent-subtle)] transition-colors"
                    data-action="react-to-message"
                    data-reaction="${type}"
                    data-message-id="${message.id}">
                ${data.emoji} <span class="font-medium text-[var(--text-secondary)]">${data.count}</span>
              </span>
            `).join('')}
          </div>
        ` : ''}

        ${message.status === 'failed' ? `
          <button class="message-retry-btn text-[var(--danger)]"
                  data-action="retry-message"
                  data-temp-id="${message.client_temp_id}">
            Retry
          </button>
        ` : ''}
      </div>
    </div>
  `;
}



/**
 * Generate resource/file attachment HTML
 */
export function resourceTemplate(resource) {
  const icon = getResourceIcon(resource.type);
  const sizeStr = formatFileSize(resource.size);
  
  if (resource.type === 'image') {
    return `
      <div class="message-resource message-image">
        <img src="${resource.url}" 
             alt="${resource.filename}"
             loading="lazy"
             data-action="view-image"
             data-url="${resource.url}">
      </div>
    `;
  }
  
  if (resource.type === 'video') {
    return `
      <div class="message-resource message-video">
        <video controls preload="metadata">
          <source src="${resource.url}" type="video/mp4">
          Your browser does not support video playback.
        </video>
      </div>
    `;
  }
  
  // Document or file
  return `
    <a href="${resource.url}" 
       target="_blank" 
       class="message-resource message-file">
      <span class="file-icon">${icon}</span>
      <div class="file-info">
        <span class="file-name">${escapeHtml(resource.filename)}</span>
        <span class="file-size">${sizeStr}</span>
      </div>
      <svg class="file-download" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
        <polyline points="7 10 12 15 17 10"/>
        <line x1="12" y1="15" x2="12" y2="3"/>
      </svg>
    </a>
  `;
}

/**
 * Empty state for messages
 */
export function emptyMessagesTemplate(partnerName) {
  return `
    <div class="empty-state">
      <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
      </svg>
      <h3>Start chatting with ${escapeHtml(partnerName)}</h3>
      <p>Send a message to begin the conversation</p>
    </div>
  `;
}

/**
 * Loading skeleton for messages
 */
export function messageSkeletonTemplate() {
  return Array(3).fill(0).map((_, i) => {
    const isOwn = i % 2 === 0;
    return `
      <div class="message ${isOwn ? 'message-own' : 'message-partner'} skeleton">
        ${!isOwn ? '<div class="message-avatar skeleton-circle"></div>' : ''}
        <div class="message-content">
          <div class="message-bubble skeleton">
            <div class="skeleton-line"></div>
            <div class="skeleton-line short"></div>
          </div>
        </div>
      </div>
    `;
  }).join('');
}

/**
 * Date separator in message list
 */
export function dateSeparatorTemplate(date) {
  return `
    <div class="message-date-separator">
      <span>${formatMessageDate(date)}</span>
    </div>
  `;
}

// ============================================================================
// REACTION PICKER
// ============================================================================

/**
 * Reaction picker modal content
 */
export function reactionPickerTemplate() {
  const reactions = [
    { type: 'love', emoji: '❤️' },
    { type: 'fire', emoji: '🔥' },
    { type: 'laugh', emoji: '😂' },
    { type: 'wow', emoji: '😮' },
    { type: 'sad', emoji: '😢' },
    { type: 'angry', emoji: '😡' },
    { type: 'thumbs_up', emoji: '👍' },
    { type: 'thumbs_down', emoji: '👎' },
    { type: 'clap', emoji: '👏' },
    { type: 'pray', emoji: '🙏' },
    { type: 'celebrate', emoji: '🎉' },
    { type: 'think', emoji: '🤔' }
  ];
  
  return `
    <div class="reaction-picker">
      ${reactions.map(r => `
        <button class="reaction-option" 
                data-action="select-message-reaction" 
                data-reaction="${r.type}">
          ${r.emoji}
        </button>
      `).join('')}
    </div>
  `;
}

// ============================================================================
// OFFLINE INDICATOR
// ============================================================================

/**
 * Offline indicator banner
 */
export function offlineBannerTemplate() {
  return `
    <div class="offline-banner">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <circle cx="12" cy="12" r="10"></circle>
        <line x1="4.93" y1="4.93" x2="19.07" y2="19.07"></line>
      </svg>
      <span>You're offline. Messages will be sent when you reconnect.</span>
    </div>
  `;
}

/**
 * Reconnecting indicator
 */
export function reconnectingBannerTemplate() {
  return `
    <div class="reconnecting-banner">
      <div class="spinner"></div>
      <span>Reconnecting...</span>
    </div>
  `;
}

// ============================================================================
// FILE UPLOAD PROGRESS
// ============================================================================

/**
 * File upload progress indicator
 */
export function uploadProgressTemplate(filename, progress) {
  return `
    <div class="upload-progress">
      <div class="upload-info">
        <span class="upload-filename">${escapeHtml(filename)}</span>
        <span class="upload-percentage">${progress}%</span>
      </div>
      <div class="upload-progress-bar">
        <div class="upload-progress-fill" style="width: ${progress}%"></div>
      </div>
    </div>
  `;
}

// ============================================================================
// CONVERSATION HEADER
// ============================================================================

/**
 * Conversation header with partner info
 */
export function conversationHeaderTemplate(partner, isOnline) {
  return `
    <button data-action="back-to-conversations"
            class="flex-shrink-0 p-2 -ml-1 rounded-full text-[var(--text-secondary)]
                   hover:bg-[var(--bg-hover)] active:bg-[var(--bg-surface)] transition-colors">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <path d="M19 12H5M12 19l-7-7 7-7"/>
      </svg>
    </button>

    <div class="flex items-center gap-2.5 flex-1 min-w-0 cursor-pointer"
     data-action="open-partner-info">
      <div class="relative flex-shrink-0">
        <img data-action='view-avatar' src="${partner.avatar || '/static/default-avatar.png'}"
             alt="${escapeHtml(partner.name)}"
             class="w-9 h-9 rounded-full object-cover bg-[var(--bg-surface)]">
        <span class="absolute bottom-0 right-0 w-2.5 h-2.5 rounded-full border-2 border-[var(--bg-card)]
                     ${isOnline ? 'bg-[var(--success)]' : 'bg-[var(--text-tertiary)]'}"></span>
      </div>
      <div class="min-w-0">
        <h3 class="text-sm font-semibold text-[var(--text-primary)] truncate leading-tight">${escapeHtml(partner.name)}</h3>
        <span class="partner-status-text text-xs ${isOnline ? 'text-[var(--success)]' : 'text-[var(--text-meta)]'}">
          ${isOnline ? 'Online' : 'Offline'}
        </span>
      </div>
    </div>

    <!-- Video Call Button -->
    <button data-action="video-call"
            class="flex-shrink-0 p-2 rounded-full text-[var(--text-secondary)]
                   hover:bg-[var(--bg-hover)] active:bg-[var(--bg-surface)] transition-colors"
            title="Video call">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="2" y="7" width="15" height="12" rx="2"/>
        <path d="M17 9l5-3v12l-5-3V9z"/>
      </svg>
    </button>

    <!-- Audio Call Button -->
    <button data-action="audio-call"
            class="flex-shrink-0 p-2 rounded-full text-[var(--text-secondary)]
                   hover:bg-[var(--bg-hover)] active:bg-[var(--bg-surface)] transition-colors"
            title="Audio call">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M22 16.92v3a2 2 0 01-2.18 2 19.79 19.79 0 01-8.63-3.07
                 A19.5 19.5 0 013.07 9.81a19.79 19.79 0 01-3.07-8.67
                 A2 2 0 012.18 1h3a2 2 0 012 1.72c.127.96.361 1.903.7 2.81
                 a2 2 0 01-.45 2.11L6.91 8.15a16 16 0 006.94 6.94l1.49-1.49
                 a2 2 0 012.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0122 16.92z"/>
      </svg>
    </button>

    <!-- Conversation Options Button -->
    <button data-action="open-conversation-options"
            class="flex-shrink-0 p-2 -mr-1 rounded-full text-[var(--text-secondary)]
                   hover:bg-[var(--bg-hover)] active:bg-[var(--bg-surface)] transition-colors">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
        <circle cx="12" cy="5" r="1.5"></circle>
        <circle cx="12" cy="12" r="1.5"></circle>
        <circle cx="12" cy="19" r="1.5"></circle>
      </svg>
    </button>
  `;
}

// ============================================================================
// UTILITY FUNCTIONS
// ============================================================================

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

/**
 * Format date for message separator
 */
function formatMessageDate(dateStr) {
  const date = new Date(dateStr);
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  
  if (date.toDateString() === today.toDateString()) {
    return 'Today';
  } else if (date.toDateString() === yesterday.toDateString()) {
    return 'Yesterday';
  } else {
    return date.toLocaleDateString('en-US', { 
      weekday: 'long', 
      month: 'short', 
      day: 'numeric' 
    });
  }
}
// ============================================================================
// PENDING ATTACHMENT PREVIEW (strip shown above the input toolbar)
// ============================================================================

/**
 * Preview chip shown in the pending-attachments-strip while a file is
 * staged (and optionally uploading) before the message is sent.
 */
export function pendingAttachmentPreviewTemplate(attachment) {
  const isImage    = attachment.type === 'image';
  const isUploading = attachment.uploading;

  return `
    <div class="relative flex-shrink-0 group" data-attachment-id="${attachment.id}">

      ${isImage && attachment.localUrl ? `
        <!-- Image thumbnail -->
        <div class="w-16 h-16 rounded-xl overflow-hidden bg-[var(--bg-surface)] border border-[var(--border-light)]">
          <img src="${attachment.localUrl || attachment.url}"
               alt="${escapeHtml(attachment.filename)}"
               class="w-full h-full object-cover">
        </div>
      ` : `
        <!-- File chip -->
        <div class="flex items-center gap-2 px-3 py-2 bg-[var(--bg-surface)] rounded-xl border border-[var(--border-light)]
                    max-w-[160px]">
          <svg class="w-4 h-4 flex-shrink-0 text-[var(--accent)]" fill="none" stroke="currentColor"
               stroke-width="2" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round"
                  d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"/>
          </svg>
          <span class="text-xs text-[var(--text-secondary)] truncate leading-tight">${escapeHtml(attachment.filename)}</span>
        </div>
      `}

      <!-- Upload progress overlay -->
      <div data-upload-id="${attachment.id}"
           class="${isUploading ? 'flex' : 'hidden'} absolute inset-0 rounded-xl
                  bg-black/50 items-center justify-center flex-col gap-0.5">
        <svg class="w-4 h-4 text-white animate-spin" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
          <path class="opacity-75" fill="currentColor"
                d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
        </svg>
        <span class="text-white text-[10px] font-semibold">0%</span>
      </div>

      <!-- Remove button -->
      ${!isUploading ? `
        <button data-action="remove-pending-attachment"
                data-attachment-id="${attachment.id}"
                class="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-[var(--primary-active)] text-white
                       flex items-center justify-center opacity-0 group-hover:opacity-100
                       transition-opacity hover:bg-[var(--danger)] z-10">
          <svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="3" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>
          </svg>
        </button>
      ` : ''}
    </div>
  `;
}

// ============================================================================
// DELETED MESSAGE TEMPLATE
// ============================================================================

/**
 * Soft-deleted message placeholder — replaces the original bubble.
 */
export function deletedMessageTemplate(message, currentUserId) {
  const isOwn = message.sender_id === currentUserId;
  return `
    <div class="message-wrapper message ${isOwn ? 'message-own' : 'message-partner'}"
         data-message-id="${message.id || ''}"
         data-is-own="${isOwn}"
         data-is-deleted="true"
         data-sent-at="${message.sent_at || ''}">

      ${!isOwn ? `
        <div class="message-avatar">
          <img src="/static/default-avatar.png" alt="User" loading="lazy">
        </div>
      ` : ''}

      <div class="message-content">
        <div class="message-bubble opacity-60 italic bg-[var(--bg-surface)]">
          <div class="flex items-center gap-1.5 text-[var(--text-meta)] text-sm">
            <svg class="w-3.5 h-3.5 flex-shrink-0" fill="none" stroke="currentColor"
                 stroke-width="2" viewBox="0 0 24 24">
              <polyline points="3 6 5 6 21 6"/>
              <path d="M19 6l-1 14H6L5 6"/>
              <path d="M10 11v6M14 11v6"/>
            </svg>
            <span>${isOwn ? 'You deleted this message' : 'This message was deleted'}</span>
          </div>
        </div>
      </div>
    </div>
  `;
}
export function blockedByMeNoticeTemplate(partnerName) {
  return `
    <div class="flex flex-col items-center justify-center gap-3 py-4 px-6
                bg-[var(--bg-surface)] border-t border-[var(--border-light)] text-center">
      <div class="flex items-center gap-2 text-[var(--text-secondary)]">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="10"/>
          <line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/>
        </svg>
        <span class="text-sm font-medium text-[var(--text-primary)]">
          You blocked <strong>${escapeHtml(partnerName)}</strong>
        </span>
      </div>
      <p class="text-xs text-[var(--text-meta)]">
        You can't send or receive messages from this person.
      </p>
      <button data-action="unblock-message-user"
              class="px-5 py-2 rounded-xl bg-[var(--accent)] hover:bg-[var(--accent-hover)]
                     active:scale-95 text-white text-sm font-semibold
                     transition-all duration-150">
        Unblock ${escapeHtml(partnerName)}
      </button>
    </div>
  `;
}

/**
 * Footer notice when the partner has blocked the current user
 * No action available — just informational
 */
export function blockedByPartnerNoticeTemplate(partnerName) {
  return `
    <div class="flex flex-col items-center justify-center gap-2 py-4 px-6
                bg-[var(--bg-surface)] border-t border-[var(--border-light)] text-center">
      <div class="flex items-center gap-2 text-[var(--text-secondary)]">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="10"/>
          <line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/>
        </svg>
        <span class="text-sm font-medium text-[var(--text-primary)]">
          You can't message <strong>${escapeHtml(partnerName)}</strong>
        </span>
      </div>
      <p class="text-xs text-[var(--text-meta)]">
        This person is not accepting messages from you.
      </p>
    </div>
  `;
}
