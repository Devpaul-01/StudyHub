/**
 * ============================================================================
 * FEED EVENT DELEGATION - CORRECTED
 * ✅ ONLY handles actions that happen INSIDE feed containers
 * ✅ Modal actions handled by app.global.js
 * ============================================================================
 */

import { feedState } from './feed.state.js';
import * as feedApi from './feed.api.js';

import { 
  viewProfile, 
  sharePost, 
  openLearnora, 
  searchTag,
  downloadResource,
  closeModal
} from './feed.utils.js';

import { detectContainer } from '../app.unified.js';

import {
  renderPostComments,
  appendCommentToUI,
  updatePreviousButton
} from './feed.render.js';
import { openResourceViewer, initResourceViewer } from '../resource.viewer.js';

// Lazy-load to avoid circular dependencies
let lazyModals = null;
async function getModals() {
  if (!lazyModals) {
    lazyModals = await import('./feed.modals.js');
  }
  return lazyModals;
}

let lazyEvents = null;
async function getEvents() {
  if (!lazyEvents) {
    lazyEvents = await import('./feed.events.js');
  }
  return lazyEvents;
}

let lazyUtils = null;
async function getUtils() {
  if (!lazyUtils) {
    lazyUtils = await import('./feed.utils.js');
  }
  return lazyUtils;
}

/**
 * ✅ Determine container type from DOM element
 */


/**
 * ✅ CORRECTED: Feed-only handlers
 * Actions that happen DIRECTLY on posts in feed (not in modals)
 */
export const FeedHandlers = {
  
  'toggle-mobile-sidebar': (target, event) => {
    toggleMobileSidebar();
  },

  'message-author': (target, event) => {
    navigateTo('messages', event);
    
    const userId = target.dataset.userId;
    if (!userId) {
      showToast("Invalid user");
      return;
    }
    const el = document.querySelector(
  `.conversation-item[data-partner-id="${userId}"]`);
    if (el) {
      el.click();
    } else {
      showToast("Conversation not found");
    }
    closeMobileSidebar();
  },
  // ==================== PROFILE ====================

  'view-profile': async (target, event, containerType) => {
    const username = target.dataset.username || target.closest('[data-username]')?.dataset.username;
    if (!username) return;
    
    const utils = await getUtils();
    utils.viewProfile(username);
  },
  'navigate-to': (target, event) => {
    const type = target.dataset.target;
    if (!type) return;
    window.navigateTo(type, event);
  },


// ⭐ ADD THIS NEW HANDLER
'close-section': (target, event) => {
  const sectionId = target.dataset.sectionId;
  if (!sectionId) return;
  
  const section = document.querySelector(`section#${sectionId}`);
  if (section) {
    section.classList.remove("active");

    // Optional: Also remove active from any modal overlays inside
    const modal = section.querySelector('.modal-overlay');
    if (modal) {
      modal.classList.remove('active');
    }
  }
},
"ask-learnora": async (target, event) => {
  const events = await getEvents();
  const postId = target.dataset.postId;
  if (!postId){
    showToast("An error occurred, please try again later", "error");
    return;
  }
  events.askLearnora(postId);
},


  // ==================== POST INTERACTIONS ====================
  'toggle-post-options': async (target, event, containerType) => {
    event.stopPropagation();
    const postId = target.closest('[data-post-id]')?.dataset.postId;
    if (!postId) return;
    
    const events = await getEvents();
    events.togglePostOptions(postId, event, containerType);
  },
  
  'toggle-reactions': async (target, event, containerType) => {
    // Skip if from reaction menu (handled by select-reaction)
    if (target.closest('#reactionMenu')) return;
    
    event.stopPropagation();
    const postId = target.closest('[data-post-id]')?.dataset.postId;
    const reactionType = target.dataset.reaction || 'like';
    if (!postId) return;
    
    const events = await getEvents();
    events.toggleReactions(postId, reactionType, event, containerType);
  },
  
  'select-reaction': async (target, event, containerType) => {
    const reactionType = target.dataset.reaction;
    if (!reactionType) return;

    const reactionBtn = feedState.getReactionBtn();
    if (!reactionBtn) return;

    const postCard = reactionBtn.closest(".post-card");
    const postId = postCard?.dataset.postId;
    if (!postId) return;
    
    // Detect container from the stored reaction button
    const btnContainerType = detectContainer(reactionBtn);
    
    const events = await getEvents();
    events.toggleReactions(postId, reactionType, null, btnContainerType);

    const reactionMenu = document.getElementById("reactionMenu");
    if (reactionMenu) reactionMenu.classList.add("hidden");
  },
  
  'open-comments': async (target, event, containerType) => {
    const postId = target.closest('[data-post-id]')?.dataset.postId;
    if (!postId) return;
    
    const modals = await getModals();
    modals.openCommentModal(postId, event);
  },

  'follow-post': async (target, event, containerType) => {
    const postId = target.dataset.postId || target.closest('[data-post-id]')?.dataset.postId;
    if (!postId) return;
    
    // If from modal, get stored containerType
    if (containerType === 'global') {
      const modal = document.getElementById('advanced-post-options-modal');
      containerType = modal?.dataset.containerType || 'smart-feed';
    }
    
    const events = await getEvents();
    events.handleFollowPost(postId, event, containerType);
    
    if (target.closest('#advanced-post-options-modal')) {
      closeModal('advanced-post-options-modal');
    }
  },
  
  'unfollow-post': async (target, event, containerType) => {
    const postId = target.dataset.postId || target.closest('[data-post-id]')?.dataset.postId;
    if (!postId) return;
    
    if (containerType === 'global') {
      const modal = document.getElementById('advanced-post-options-modal');
      containerType = modal?.dataset.containerType || 'smart-feed';
    }
    
    const events = await getEvents();
    events.handleUnfollowPost(postId, event, containerType);
    
    if (target.closest('#advanced-post-options-modal')) {
      closeModal('advanced-post-options-modal');
    }
  },
  
  'delete-post': async (target, event, containerType) => {
    const postId = target.dataset.postId || target.closest('[data-post-id]')?.dataset.postId;
    if (!postId) return;
    
    if (containerType === 'global') {
      const modal = document.getElementById('advanced-post-options-modal');
      containerType = modal?.dataset.containerType || 'smart-feed';
    }
    
    const events = await getEvents();
    events.handleDeletePost(postId, event, containerType);
    
    if (target.closest('#advanced-post-options-modal')) {
      closeModal('advanced-post-options-modal');
    }
  },
  
  'report-post': async (target, event, containerType) => {
    const postId = target.dataset.postId || target.closest('[data-post-id]')?.dataset.postId;
    if (!postId) return;
    
    const events = await getEvents();
    events.handleOpenReportModal(postId, event);
    
    if (target.closest('#advanced-post-options-modal')) {
      closeModal('advanced-post-options-modal');
    }
  },
  
  'fork-post': async (target, event, containerType) => {
    const postId = target.dataset.postId || target.closest('[data-post-id]')?.dataset.postId;
    if (!postId) return;
    
    const modals = await getModals();
    modals.openForkModal(postId);
    
    if (target.closest('#advanced-post-options-modal')) {
      closeModal('advanced-post-options-modal');
    }
  },
  
  'refine-post': async (target, event, containerType) => {
    const postId = target.dataset.postId || target.closest('[data-post-id]')?.dataset.postId;
    if (!postId) return;
    
    const modals = await getModals();
    modals.refinePost(postId);
    
    if (target.closest('#advanced-post-options-modal')) {
      closeModal('advanced-post-options-modal');
    }
  },
  
  'form-thread-with-author': async (target, event, containerType) => {
    const postId = target.dataset.postId || target.closest('[data-post-id]')?.dataset.postId;
    const userId = target.dataset.userId;

    if (!postId && !userId) return;

    // Close any open context modal before opening the thread modal
    const advancedModal = target.closest('#advanced-post-options-modal');
    if (advancedModal) closeModal('advanced-post-options-modal');

    const events = await getEvents();
    events.handleCreateThreadFromPost(postId, userId, event);
  },
  'share-post': async (target, event, containerType) => {
    const postId = target.dataset.postId || target.closest('[data-post-id]')?.dataset.postId;
    if (!postId) return;
    
    const utils = await getUtils();
    utils.sharePost(postId);
    
    if (target.closest('#advanced-post-options-modal')) {
      closeModal('advanced-post-options-modal');
    }
  },
  
  'open-learnora': async (target, event, containerType) => {
    const postId = target.dataset.postId || target.closest('[data-post-id]')?.dataset.postId;
    if (!postId) return;
    
    const utils = await getUtils();
    utils.openLearnora(postId);
    
    if (target.closest('#advanced-post-options-modal')) {
      closeModal('advanced-post-options-modal');
    }
  },
  
  
  'listen-post': async (target, event, containerType) => {
    const postId = target.dataset.postId || target.closest('[data-post-id]')?.dataset.postId;
    if (!postId) return;
    
    if (containerType === 'global') {
      const modal = document.getElementById('advanced-post-options-modal');
      containerType = modal?.dataset.containerType || 'smart-feed';
    }
    
    const events = await getEvents();
    events.handleListenPost(postId, event, containerType);
    
    if (target.closest('#advanced-post-options-modal')) {
      closeModal('advanced-post-options-modal');
    }
  },
  'view-comment-resource': (target, event) => {
    const resources = target.dataset.resources;
    const index = target.dataset.index;
    events.openResourceViewer(resources, index);
  },
  
  'mark-solved': async (target, event, containerType) => {
    const postId = target.dataset.postId || target.closest('[data-post-id]')?.dataset.postId;
    if (!postId) return;
    
    if (containerType === 'global') {
      const modal = document.getElementById('advanced-post-options-modal');
      containerType = modal?.dataset.containerType || 'smart-feed';
    }
    
    const events = await getEvents();
    events.handleMarkSolved(postId, event, containerType);
    
    if (target.closest('#advanced-post-options-modal')) {
      closeModal('advanced-post-options-modal');
    }
  },
  
  
  'unmark-solved': async (target, event, containerType) => {
    const postId = target.dataset.postId || target.closest('[data-post-id]')?.dataset.postId;
    if (!postId) return;
    
    if (containerType === 'global') {
      const modal = document.getElementById('advanced-post-options-modal');
      containerType = modal?.dataset.containerType || 'smart-feed';
    }
    
    const events = await getEvents();
    events.handleUnmarkSolved(postId, event, containerType);
    
    if (target.closest('#advanced-post-options-modal')) {
      closeModal('advanced-post-options-modal');
    }
  },
  
  'create-thread-from-post': async (target, event, containerType) => {
    event.stopPropagation();
    const postId = target.dataset.postId || target.closest('[data-post-id]')?.dataset.postId;
    if (!postId) return;
    
    const events = await getEvents();
    events.handleCreateThreadFromPost(postId, event);
    
    if (target.closest('#advanced-post-options-modal')) {
      closeModal('advanced-post-options-modal');
    }
  },
  
  'view-thread': async (target, event, containerType) => {
    const threadId = target.dataset.threadId;
    const type = target.dataset.threadType || 'post';
    if (!threadId) return;
    
    const events = await getEvents();
    events.handleViewThread(threadId, type, event);
    
    if (target.closest('#advanced-post-options-modal')) {
      closeModal('advanced-post-options-modal');
    }
  },
  
  
  'view-user-overview': async (target, event, containerType) => {
    const userId = target.dataset.authorId;
    if (!userId) return;
    
    const modals = await getModals();
    modals.startAuthorOverviewStream(userId);
    
    if (target.closest('#advanced-post-options-modal')) {
      closeModal('advanced-post-options-modal');
    }
  },
  
  'connect-request': async (target, event, containerType) => {
    const userId = target.dataset.userId;
    if (!userId) return;
    
    const events = await getEvents();
    events.handleConnectRequest(userId, event);
    
    if (target.closest('#advanced-post-options-modal')) {
      closeModal('advanced-post-options-modal');
    }
  },
  
  // ==================== POST RESOURCES ====================
  'scroll-post-resource': async (target, event, containerType) => {
    event.stopPropagation();
    const direction = target.dataset.direction;
    
    const events = await getEvents();
    events.handleScrollPostResource(direction, target);
  },
  
  'view-post-resource-links': async (target, event, containerType) => {
    event.stopPropagation();
    const events = await getEvents();
    events.showPostResourceLinks(target);
  },
  
  'download-resource': (target, event, containerType) => {
    const url = target.dataset.url;
    const filename = target.dataset.filename;
    downloadResource(url, filename);
  },
  
  // ==================== TAGS ====================
  'view-tag-posts': async (target, event, containerType) => {
    const tag = target.dataset.tag;
    event.stopPropagation();
    
    const events = await getEvents();
    events.handleViewTagPosts(tag);
  },
  
  'search-tag': async (target, event, containerType) => {
    const tag = target.dataset.tag;
    if (!tag) return;
    
    const utils = await getUtils();
    utils.searchTag(tag);
  },
  
  // ==================== COMMENTS ====================
  'toggle-comment-like': async (target, event, containerType) => {
    const commentId = target.closest('[data-comment-id]')?.dataset.commentId;
    if (!commentId) return;
    
    const events = await getEvents();
    events.handleToggleCommentLike(commentId, event);
  },
  
  'toggle-comment-helpful': async (target, event, containerType) => {
    const commentId = target.closest('[data-comment-id]')?.dataset.commentId;
    if (!commentId) return;
    
    const events = await getEvents();
    events.handleToggleCommentHelpful(commentId, event);
  },
  'copy-comment-link':(target, event) => {
    const modal = target.closest('advanced-comment-options');
    const commentId = modal.dataset.commentId;
    const commentUrl = `${window.location.origin}/comment/${commentId}`;
    try {
      navigator.clipboard.writeText(commentUrl);
      showToast('Comment link copied!', 'success');
    } catch (error) {
      showToast('Failed to copy link', 'error');
      }
  },
  
  'toggle-comment-settings': async (target, event, containerType) => {
    event.stopPropagation();
    const events = await getEvents();
    events.handleToggleCommentSettings(target);
  },
  
  'delete-comment': async (target, event, containerType) => {
    const commentId = target.closest('[data-comment-id]')?.dataset.commentId;
    if (!commentId) return;
    
    const events = await getEvents();
    events.handleDeleteComment(commentId, event);
  },
  
  'mark-solution': async (target, event, containerType) => {
    const commentId = target.dataset.commentId;
    const postId = target.dataset.postId;
    if (!commentId || !postId) return;
    
    const events = await getEvents();
    events.handleMarkSolution(postId, commentId, event);
  },
  
  'unmark-solution': async (target, event, containerType) => {
    const commentId = target.dataset.commentId;
    const postId = target.dataset.postId;
    if (!commentId || !postId) return;
    
    const events = await getEvents();
    events.handleUnmarkSolution(postId, commentId, event);
  },
  
  'open-reply': async (target, event, containerType) => {
    const commentId = target.dataset.commentId;
    const postId = target.dataset.postId;
    const username = target.dataset.username;
    if (!commentId || !postId || !username) return;
    
    const modals = await getModals();
    modals.openReplyModal(username, commentId, postId);
  },
  
  'view-comment-resource': async (target, event, containerType) => {
    event.stopPropagation();
    const url = target.dataset.url;
    const type = target.dataset.resourceType;
    
    const events = await getEvents();
    events.showCommentResource(url, type);
  },
  
  'view-comment-resources': async (target, event, containerType) => {
    event.stopPropagation();
    const resources = target.dataset.resources;
    
    const events = await getEvents();
    events.showCommentResources(resources);
  },
  
  'view-comment-resource-links': async (target, event, containerType) => {
    event.stopPropagation();
    const events = await getEvents();
    events.showCommentResourceLinks(target);
  },
  
  'post-comment': async (target, event, containerType) => {
    event.preventDefault();
    event.stopPropagation();
    const modals = await getModals();
    modals.postComment(event);
  },
  
  // ==================== MODALS ====================
  'open-modal': (target, event, containerType) => {
    const modalId = target.dataset.modalId;
    const closeModalId = target.dataset.closeModal;
    if (closeModalId) closeModal(closeModalId);
    if (modalId) openModal(modalId);
  },
  
  'close-modal': (target, event, containerType) => {
    const modalId = target.dataset.modalId;
    if (modalId) closeModal(modalId);
  },
  
  // ==================== CREATE POST ====================
  'submit-create-post': async (target, event, containerType) => {
    event.preventDefault();
    const events = await getEvents();
    events.handleCreatePost(event, target);
  },
  
  'add-post-tag': async (target, event, containerType) => {
    const tag = target.dataset.value;
    if (!tag) return;
    
    const modals = await getModals();
    modals.addPostTag(tag);
  },
  
  'remove-post-tag': async (target, event, containerType) => {
    const tag = target.dataset.value;
    if (!tag) return;
    
    const modals = await getModals();
    modals.removePostTag(tag);
  },
  
  'toggle-thread-settings': (target, event, containerType) => {
    const modal = document.getElementById('create-post-thread-details-modal');
    if (modal) modal.classList.toggle('hidden');
  },
  
  'trigger-file-input': (target, event, containerType) => {
    const inputId = target.dataset.inputId;
    const modalId = target.dataset.closeModal;
    
    const input = document.getElementById(inputId);
    if (input) input.click();
    
    if (modalId) closeModal(modalId);
  },

  
  // ==================== CREATE THREAD ====================
  'submit-create-thread': async (target, event, containerType) => {
    event.preventDefault();
    const events = await getEvents();
    events.handleCreateThread(event, target);
  },
  
  'add-thread-tag': async (target, event, containerType) => {
    const tag = target.dataset.value;
    if (!tag) return;
    
    const modals = await getModals();
    modals.addThreadTag(tag);
  },
  
  'remove-thread-tag': async (target, event, containerType) => {
    const tag = target.dataset.value;
    if (!tag) return;
    
    const modals = await getModals();
    modals.removeThreadTag(tag);
  },
  
  // ==================== FORK POST ====================
  'submit-fork-post': async (target, event, containerType) => {
    event.preventDefault();
    const modals = await getModals();
    modals.saveForkedPost(event);
  },
  
  'add-fork-tag': async (target, event, containerType) => {
    const tag = target.dataset.tag || target.dataset.value;
    if (!tag) return;
    
    const modals = await getModals();
    modals.addForkTag(tag);
  },
  
  'remove-fork-tag': async (target, event, containerType) => {
    const tag = target.dataset.tag || target.dataset.value;
    if (!tag) return;
    
    const modals = await getModals();
    modals.removeForkTag(tag);
  },
  
  // ==================== THREAD ====================
  'join-thread': async (target, event, containerType) => {
    event.stopPropagation();
    const events = await getEvents();
    events.handleJoinThread(event);
  },
  
  // ==================== REPORT ====================
  'submit-report-post': async (target, event, containerType) => {
    event.preventDefault();
    const events = await getEvents();
    events.handleReportPost(event);
  },
  
  // ==================== REFINEMENT ====================
  'start-refinement': async (target, event, containerType) => {
    event.stopPropagation();
    const modal = document.getElementById('post-refine-modal');
    const postId = modal?.dataset.id;
    if (!postId) return;
    
    const modals = await getModals();
    modals.startRefinement(postId);
  },
  
  'apply-refinement': async (target, event, containerType) => {
    event.stopPropagation();
    const modal = document.getElementById('post-refine-modal');
    const postId = modal?.dataset.id;
    if (!postId) return;
    
    const modals = await getModals();
    modals.applyRefinement(postId);
  },
  
  'refine-before-post': async (target, event, containerType) => {
    const context = target.dataset.context || 'create';
    const modals = await getModals();
    modals.refineBeforePost(context, event);
  },
  
  'apply-inline-refinement': async (target, event, containerType) => {
    event.stopPropagation();
    const modals = await getModals();
    modals.applyInlineRefinement();
  },
  
  // ==================== NAVIGATION ====================
  'filter-feed': (target, event, containerType) => {
    const filter = target.dataset.filter;
    if (filter && typeof window.filterFeed === 'function') {
      window.filterFeed(filter);
    }
  },

  // Batch 2: see the chip row added to templates/post/feed2.html
  // right under .feed-filters, and handlePostTypeFilterChange() in
  // feed.events.js.
  'filter-post-type': (target, event, containerType) => {
    const postType = target.dataset.postType || '';
    if (typeof window.filterPostType === 'function') {
      window.filterPostType(postType);
    }
  },
  
  'logout': (target, event, containerType) => {
    if (typeof logout === 'function') {
      logout(event);
    }
  }
};

/**
 * ✅ Delegated event handler
 */


