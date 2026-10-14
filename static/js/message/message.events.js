/**
 * Message System Event Handlers — PRODUCTION
 * All business logic for user interactions.
 * Uses messageState.getCurrentUserId() — never window globals.
 */

import * as messageState from './message.state.js';
import * as messageApi   from './message.api.js';
import { messageWS }     from './message.websocket.js';
import * as modals       from './message.modals.js';
import * as render       from './message.render.js';
import {
  generateMessageId,
  isValidMessageBody,
  isValidFileType,
  isValidFileSize,
  getFileCategory,
  getDraft,
  saveDraft,
  clearDraft,
  canDeleteForEveryone,
  debounce,
  generateFileId,
} from './message.utils.js';
import { TYPING_DEBOUNCE } from './message.constants.js';

const _toast = () => window.showToast ?? globalThis.showToast ?? (() => {});
const toast  = (msg, type) => {
  const fn = window.showToast || globalThis.showToast;
  if (fn) fn(msg, type);
};

// ============================================================================
// CONVERSATION ACTIONS
// ============================================================================

export async function handleOpenConversation(target) {
  const partnerId = parseInt(target.dataset.partnerId, 10);
  if (!partnerId) return;

  try {
    messageState.setCurrentConversation(partnerId);
    messageState.setLoadingMessages(true);

    modals.showConversationView();

    // ── Fetch history — response now includes block status ─────────────────
    const result = await messageApi.fetchMessageHistory(partnerId);

    // result is { messages, is_blocked_by_me, blocked_by_partner }
    const messages          = result.messages          ?? result; // graceful fallback
    const isBlockedByMe     = result.is_blocked_by_me  ?? false;
    const blockedByPartner  = result.blocked_by_partner ?? false;

    messageState.setMessages(messages);

    // ── Sync block status into state immediately ───────────────────────────
    // updateConversation now also keeps currentConversation in sync (fixed above)
    messageState.updateConversation(partnerId, {
      is_blocked_by_me:   isBlockedByMe,
      blocked_by_partner: blockedByPartner,
    });

    // ── Render messages ────────────────────────────────────────────────────
    render.renderMessageList();

    // ── Mark unread as read (only if not blocked) ──────────────────────────
    if (!isBlockedByMe && !blockedByPartner) {
      const currentUserId = messageState.getCurrentUserId();
      const unreadIds = messages
        .filter(m => !m.is_read && m.receiver_id === currentUserId)
        .map(m => m.id);

      if (unreadIds.length > 0) {
        messageWS.markMessagesRead(unreadIds);
        messageState.clearUnreadCount(partnerId);
        render.updateConversationItem(partnerId);
      }
    }

    // ── Restore draft ──────────────────────────────────────────────────────
    const draft = getDraft(partnerId);
    const input = document.getElementById('message-input');
    if (input && draft) {
      input.value = draft;
      render.updateSendMicToggle();
    }

    // ── Apply blocked UI (re-renders footer based on fresh state) ──────────
    _applyBlockedUI();

    // ── Attach long-press listeners ────────────────────────────────────────
    const { attachMessageLongPress } = await import('./message.longpress.js');
    const listEl = document.getElementById('messages-list');
    if (listEl) attachMessageLongPress(listEl);

  } catch (error) {
    console.error('Failed to open conversation:', error);
    toast('Failed to load conversation', 'error');
  } finally {
    messageState.setLoadingMessages(false);
  }
}

export function handleCloseConversation() {
  const partnerId = messageState.getCurrentPartnerId();

  if (partnerId) {
    const input = document.getElementById('message-input');
    if (input?.value.trim()) {
      saveDraft(partnerId, input.value);
    } else {
      clearDraft(partnerId);
    }
  }

  // Detach long-press listeners
  import('./message.longpress.js').then(({ detachMessageLongPress }) => {
    detachMessageLongPress();
  });

  messageState.clearPendingAttachments();
  render.renderPendingAttachments();
  render.updateSendMicToggle();

  modals.showConversationListView();
}

// ============================================================================
// SEND MESSAGE  (text + staged attachments)
// ============================================================================

export async function handleSendMessage() {
  const isUploading = messageState.getPendingAttachments().some(a => a.uploading);
  if (isUploading) {
    toast('Please wait for uploads to finish', 'warning');
    return;
  }
  const input  = document.getElementById('message-input');
  const partnerId = messageState.getCurrentPartnerId();
  if (!input || !partnerId){
    return;
  }

  // Blocked check
  if (messageState.isCurrentPartnerBlockedByMe() || messageState.isBlockedByCurrentPartner()) {
    toast('Cannot send message — user is blocked', 'warning');
    return;
  }

  const body        = input.value.trim();
  const attachments = messageState.getPendingAttachments();

  if (!body && attachments.length === 0) return;
  if (body && !isValidMessageBody(body)) {
    toast('Message is too long (max 5,000 characters)', 'error');
    return;
  }

  const currentUser   = messageState.getCurrentUser();
  const currentUserId = messageState.getCurrentUserId();
  const clientTempId  = generateMessageId();

  // Build resource list from staged attachments
  const resources = attachments.map(a => ({
    id:       a.id,
    url:      a.url,
    type:     a.type,
    filename: a.filename,
    size:     a.size,
    duration_seconds: a.duration_seconds ?? undefined,
  }));

  // Optimistic message
  const optimistic = {
    id:             null,
    client_temp_id: clientTempId,
    sender_id:      currentUserId,
    receiver_id:    partnerId,
    body:           body || '',
    resources,
    sent_at:        new Date().toISOString(),
    is_read:        false,
    is_deleted:     false,
    status:         'pending',
    sender:         currentUser,
  };

  messageState.addOptimisticMessage(optimistic);
  render.renderNewMessage(optimistic);

  input.value = '';
  messageState.clearPendingAttachments();
  render.renderPendingAttachments();

  clearDraft(partnerId);

  messageState.setSendingMessage(true);
  messageWS.sendMessage(partnerId, body || '', resources, clientTempId);
}

// ============================================================================
// TYPING INDICATOR
// ============================================================================

const _sendTypingStop = debounce((partnerId) => {
  messageWS.sendTyping(partnerId, false);
}, TYPING_DEBOUNCE * 4);

export function handleMessageInput(event) {
  const partnerId = messageState.getCurrentPartnerId();
  if (!partnerId) return;

  const value = event.target.value;
  value.trim() ? saveDraft(partnerId, value) : clearDraft(partnerId);

  if (value.trim()) {
    messageWS.sendTyping(partnerId, true);
    _sendTypingStop(partnerId);
  }

  // Toggle send ↔ mic button
  render.updateSendMicToggle();
}

// ============================================================================
// FILE ATTACHMENT
// ============================================================================

export function handleAttachFile() {
  const partnerId = messageState.getCurrentPartnerId();
  if (!partnerId) return;

  if (messageState.isCurrentPartnerBlockedByMe() || messageState.isBlockedByCurrentPartner()) {
    toast('Cannot send attachments — user is blocked', 'warning');
    return;
  }

  const input  = document.createElement('input');
  input.type   = 'file';
  input.accept = 'image/*,video/*,audio/*,.pdf,.doc,.docx,.txt,.ppt,.pptx,.xls,.xlsx';
  input.multiple = true;

  input.onchange = async (e) => {
    const files = Array.from(e.target.files);
    if (!files.length) return;

    for (const file of files) {
      if (!isValidFileType(file)) {
        toast(`Invalid file type: ${file.name}`, 'error');
        continue;
      }
      if (!isValidFileSize(file, 50)) {
        toast(`File too large (max 50 MB): ${file.name}`, 'error');
        continue;
      }
      await _stageAttachment(file);
    }
  };

  input.click();
}

async function _stageAttachment(file) {
  const fileId    = generateFileId();
  const category  = getFileCategory(file);
  const localUrl  = file.type.startsWith('image/') ? URL.createObjectURL(file) : null;

  // Optimistically add to pending strip
  const pending = {
    id:       fileId,
    type:     category,
    filename: file.name,
    size:     file.size,
    localUrl,
    url:      localUrl || '', // will be replaced by server URL
    _file:    file,
    uploading: true,
  };
  messageState.addPendingAttachment(pending);
  render.renderPendingAttachments();
  

  try {
    messageState.setUploadingFile(true);
    const resource = await messageApi.uploadResource(file, (progress) => {
      render.updateAttachmentProgress(fileId, progress);
    });

    // Replace placeholder with real data
    messageState.removePendingAttachment(fileId);
    messageState.addPendingAttachment({
      ...pending,
      id:       resource.id || fileId,
      url:      resource.url,
      uploading: false,
    });
    render.renderPendingAttachments();
    messageState.setUploadingFile(false);
  } catch (err) {
    console.error('Failed to upload attachment:', err);
    messageState.removePendingAttachment(fileId);
    render.renderPendingAttachments();
    render.updateSendMicToggle();
    messageState.setUploadingFile(false);
    toast(`Failed to upload ${file.name}`, 'error');
    if (localUrl) URL.revokeObjectURL(localUrl);
  }
}

export function handleRemovePendingAttachment(target) {
  const id = target.dataset.attachmentId;
  if (!id) return;
  messageState.removePendingAttachment(id);
  render.renderPendingAttachments();
  render.updateSendMicToggle();
}

// ============================================================================
// MESSAGE OPTIONS  (opened by long-press via message.modals)
// ============================================================================

export function handleDeleteMessageForMe() {
  const messageId = messageState.messageState.messageOptionsMessageId;
  if (!messageId) return;

  messageState.softDeleteMessage(messageId);
  render.markMessageAsDeleted(messageId);
  modals.closeMessageOptionsModal();
  messageWS.deleteMessage(messageId, false);
}

export function handleDeleteMessageForEveryone() {
  const messageId = messageState.messageState.messageOptionsMessageId;
  if (!messageId) return;

  const message = messageState.getMessages().find(m => m.id === messageId);
  if (!message) return;

  if (!canDeleteForEveryone(message.sent_at)) {
    toast('Can only delete within 5 minutes of sending', 'warning');
    return;
  }

  // Optimistic UI update immediately for snappy feedback
  messageState.softDeleteMessage(messageId);
  render.markMessageAsDeleted(messageId);
  modals.closeMessageOptionsModal();

  // Emit via WebSocket — server will persist and notify the partner
  messageWS.deleteMessage(messageId, true);
}

export async function handleCopyMessage() {
  const messageId = messageState.messageState.messageOptionsMessageId;
  if (!messageId) return;

  const message = messageState.getMessages().find(m => m.id === messageId);
  if (!message?.body) return;

  try {
    await navigator.clipboard.writeText(message.body);
    toast('Copied to clipboard', 'success');
  } catch {
    toast('Failed to copy text', 'error');
  }
  modals.closeMessageOptionsModal();
}

export async function handleReportMessage() {
  const messageId = messageState.messageState.messageOptionsMessageId;
  if (!messageId) return;
  modals.closeMessageOptionsModal();
  modals.openReportMessageModal(messageId);
}

export async function handleSubmitReport(target) {
  const modal     = document.getElementById('msg-report-modal');
  const messageId = parseInt(modal?.dataset.messageId, 10);
  const reason    = modal?.querySelector('#report-reason')?.value;
  const desc      = modal?.querySelector('#report-description')?.value || '';

  if (!messageId || !reason) {
    toast('Please select a reason', 'warning');
    return;
  }

  try {
    const response = await messageApi.reportMessage(messageId, reason, desc);
    if(!response.status === 'success'){
      toast(response.message, 'error');
      return;
    }
    toast('Report submitted — thank you', 'success');
    modals.closeReportMessageModal();
  } catch {
  
  }
}

// ============================================================================
// REACTIONS
// ============================================================================

export function handleReactToMessage(target) {
  const messageId = parseInt(target.dataset.messageId, 10);
  if (!messageId) return;
  modals.openReactionPicker(messageId, target);
}

export async function handleSelectReaction(target) {
  const reactionType = target.dataset.reaction;
  const messageId    = messageState.messageState.reactionPickerMessageId;
  if (!reactionType || !messageId) return;

  try {
    messageState.addOptimisticReaction(messageId, reactionType);
    // Reactions are a WebSocket-only operation — there is no matching
    // REST route on the backend (only the socket handlers
    // 'add_message_reaction' / 'remove_message_reaction' exist). The old
    // call to messageApi.addReaction() hit a route that doesn't exist and
    // always 404'd, so reaction picks silently failed and the optimistic
    // reaction was reverted every time. See CHANGES.md, Batch 1, item 1.
    messageWS.addReaction(messageId, reactionType);
    modals.closeReactionPicker();
  } catch (err) {
    console.error('Failed to add reaction:', err);
    messageState.revertOptimisticReaction(messageId);
    toast('Failed to add reaction', 'error');
  }
}

// ============================================================================
// CONVERSATION OPTIONS
// ============================================================================

export function handleOpenConversationOptions() {
  modals.openConversationOptionsModal();
}

export async function handleClearChat() {
  const partnerId = messageState.getCurrentPartnerId();
  if (!partnerId) return;

  modals.closeConversationOptionsModal();
  modals.openConfirmModal({
    title:   'Clear conversation?',
    message: 'All messages will be removed from your view. This cannot be undone.',
    confirmLabel: 'Clear',
    confirmClass: 'bg-red-600 hover:bg-red-700',
    onConfirm: async () => {
      try {
        await messageApi.clearChat(partnerId);
        messageState.setMessages([]);
        render.renderMessageList();
      } catch {
        toast('Failed to clear chat', 'error');
      }
    },
  });
}
export async function handleBlockUser() {
  const partnerId = messageState.getCurrentPartnerId();
  const partner   = messageState.getCurrentPartner();
  if (!partnerId || !partner) return;

  modals.closeConversationOptionsModal();
  modals.openConfirmModal({
    title:   `Block ${partner.name}?`,
    message: 'They won\'t be able to message you and you won\'t be able to message them.',
    confirmLabel: 'Block',
    confirmClass: 'bg-red-600 hover:bg-red-700',
    onConfirm: async () => {
      try {
        await messageApi.blockUser(partnerId);
      } catch {
        // Block may still have succeeded — continue with UI update
      }
      // Always update UI regardless — backend block is optimistic
      messageState.updateConversation(partnerId, { is_blocked_by_me: true });
      render.updateConversationItem(partnerId);
      toast(`${partner.name} blocked`, 'success');
      // Close the conversation and return to list
      handleCloseConversation();
    },
  });
}


export async function handleUnblockUser() {
  const partnerId = messageState.getCurrentPartnerId();
  const partner   = messageState.getCurrentPartner();
  if (!partnerId || !partner) return;

  try {
    await messageApi.unblockUser(partnerId);
    messageState.updateConversation(partnerId, { is_blocked_by_me: false });
    render.restoreMessageInput();
    render.updateConversationItem(partnerId);
  } catch {
    toast('Failed to unblock user', 'error');
  }
}

// ============================================================================
// PARTNER INFO
// ============================================================================

export function handleOpenPartnerInfo() {
  modals.openPartnerInfoModal();
}

// ============================================================================
// IMAGE VIEWER
// ============================================================================

export function handleViewImage(target) {
  const url      = target.dataset.url || target.src;
  const filename = target.dataset.filename || target.alt || 'Image';
  render.openImageViewer(url, filename);
}

// ============================================================================
// MESSAGE RETRY
// ============================================================================

export async function handleRetryMessage(target) {
  const clientTempId = target.dataset.tempId;
  if (!clientTempId) return;

  const failedMessage = messageState.getFailedMessages().find(
    m => m.client_temp_id === clientTempId
  );
  if (!failedMessage) return;

  try {
    messageState.removeFromRetryQueue(clientTempId);
    const partnerId = messageState.getCurrentPartnerId();
    messageWS.sendMessage(
      partnerId,
      failedMessage.body,
      failedMessage.resources || [],
      clientTempId
    );

    const el = document.querySelector(`[data-temp-id="${clientTempId}"]`);
    if (el) {
      el.classList.remove('message-failed');
      el.querySelector('.message-retry-btn')?.remove();
    }
  } catch {
    toast('Failed to retry message', 'error');
  }
}

// ============================================================================
// PRIVATE HELPERS
// ============================================================================

function _applyBlockedUI() {
  const isBlockedByMe    = messageState.isCurrentPartnerBlockedByMe();
  const blockedByPartner = messageState.isBlockedByCurrentPartner();

  if (isBlockedByMe) {
    render.renderBlockedNotice('blocked_by_me');
  } else if (blockedByPartner) {
    render.renderBlockedNotice('blocked_by_partner');
  } else {
    // Not blocked — make sure input footer is in its normal state
    render.restoreMessageInput();
  }
}
