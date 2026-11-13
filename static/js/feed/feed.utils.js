/**
 * ============================================================================
 * FEED UTILITIES - COMPLETELY FIXED
 * Helper functions - no state, no DOM manipulation
 * Fixed: Added missing exports (openModal, closeModal), improved modal cleanup
 * ============================================================================
 */

/**
 * Format timestamp to human-readable relative time
 */
 export function ShowNotification(message, type = 'info') {
  // Remove any existing notifications first
  const existingToast = document.querySelector('.notification-toast');
  if (existingToast) {
    existingToast.remove();
  }

  // Create toast container
  const toast = document.createElement('div');
  toast.className = `notification-toast notification-toast-${type}`;
  
  // Create message content
  const messageDiv = document.createElement('div');
  messageDiv.className = 'notification-message';
  messageDiv.textContent = message;
  
  // Create close button
  const closeBtn = document.createElement('button');
  closeBtn.className = 'notification-close';
  closeBtn.setAttribute('data-action', 'close-notification');
  closeBtn.innerHTML = `
    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <line x1="18" y1="6" x2="6" y2="18"></line>
      <line x1="6" y1="6" x2="18" y2="18"></line>
    </svg>
  `;
  
  // Append elements
  toast.appendChild(messageDiv);
  toast.appendChild(closeBtn);
  document.body.appendChild(toast);
  
  // Trigger animation
  setTimeout(() => {
    toast.classList.add('notification-toast-show');
  }, 10);
}
/**
 * Sets up global notification listeners
 * Call this once when your app initializes
 */

export function formatTime(timestamp) {
  const date = new Date(timestamp);
  const now = new Date();
  const diff = Math.floor((now - date) / 1000);
  
  if (diff < 60) return 'Just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return date.toLocaleDateString();
}

/**
 * Debounce function calls
 */
export function debounce(func, wait) {
  let timeout;
  return function executedFunction(...args) {
    const later = () => {
      clearTimeout(timeout);
      func(...args);
    };
    clearTimeout(timeout);
    timeout = setTimeout(later, wait);
  };
}

/**
 * Get loading skeleton HTML
 */
export function getLoadingSkeleton() {
  const item = (hasMedia = false, hasActions = true) => `
    <div class="skeleton-card" style="
      background: var(--bg-primary, #fff);
      border: 1px solid var(--border, #e5e7eb);
      border-radius: 12px;
      padding: 1.25rem;
      margin-bottom: 1rem;
    ">
      <!-- Author row -->
      <div style="display: flex; align-items: center; gap: 0.75rem; margin-bottom: 1rem;">
        <div style="
          width: 44px; height: 44px; border-radius: 50%; flex-shrink: 0;
          background: linear-gradient(90deg, #e8e8e8 25%, #f4f4f4 50%, #e8e8e8 75%);
          background-size: 400% 100%;
          animation: skeletonShimmer 1.5s ease-in-out infinite;
        "></div>
        <div style="flex: 1; display: flex; flex-direction: column; gap: 0.4rem;">
          <div style="
            height: 13px; width: 36%; border-radius: 6px;
            background: linear-gradient(90deg, #e8e8e8 25%, #f4f4f4 50%, #e8e8e8 75%);
            background-size: 400% 100%;
            animation: skeletonShimmer 1.5s ease-in-out infinite;
          "></div>
          <div style="
            height: 11px; width: 22%; border-radius: 6px;
            background: linear-gradient(90deg, #e8e8e8 25%, #f4f4f4 50%, #e8e8e8 75%);
            background-size: 400% 100%;
            animation: skeletonShimmer 1.5s ease-in-out infinite;
          "></div>
        </div>
        <div style="
          width: 28px; height: 28px; border-radius: 6px;
          background: linear-gradient(90deg, #e8e8e8 25%, #f4f4f4 50%, #e8e8e8 75%);
          background-size: 400% 100%;
          animation: skeletonShimmer 1.5s ease-in-out infinite;
        "></div>
      </div>
      <!-- Post type badge -->
      <div style="
        height: 11px; width: 18%; border-radius: 20px; margin-bottom: 0.75rem;
        background: linear-gradient(90deg, #e8e8e8 25%, #f4f4f4 50%, #e8e8e8 75%);
        background-size: 400% 100%;
        animation: skeletonShimmer 1.5s ease-in-out infinite;
      "></div>
      <!-- Title line -->
      <div style="
        height: 17px; width: 65%; border-radius: 6px; margin-bottom: 0.6rem;
        background: linear-gradient(90deg, #e8e8e8 25%, #f4f4f4 50%, #e8e8e8 75%);
        background-size: 400% 100%;
        animation: skeletonShimmer 1.5s ease-in-out infinite;
      "></div>
      <!-- Content lines -->
      <div style="display: flex; flex-direction: column; gap: 0.4rem; margin-bottom: 1rem;">
        <div style="
          height: 12px; width: 100%; border-radius: 4px;
          background: linear-gradient(90deg, #e8e8e8 25%, #f4f4f4 50%, #e8e8e8 75%);
          background-size: 400% 100%;
          animation: skeletonShimmer 1.5s ease-in-out infinite;
        "></div>
        <div style="
          height: 12px; width: 95%; border-radius: 4px;
          background: linear-gradient(90deg, #e8e8e8 25%, #f4f4f4 50%, #e8e8e8 75%);
          background-size: 400% 100%;
          animation: skeletonShimmer 1.5s ease-in-out infinite;
        "></div>
        <div style="
          height: 12px; width: 80%; border-radius: 4px;
          background: linear-gradient(90deg, #e8e8e8 25%, #f4f4f4 50%, #e8e8e8 75%);
          background-size: 400% 100%;
          animation: skeletonShimmer 1.5s ease-in-out infinite;
        "></div>
      </div>
      ${hasMedia ? `
      <!-- Media placeholder -->
      <div style="
        height: 220px; border-radius: 10px; margin-bottom: 1rem;
        background: linear-gradient(90deg, #e8e8e8 25%, #f4f4f4 50%, #e8e8e8 75%);
        background-size: 400% 100%;
        animation: skeletonShimmer 1.5s ease-in-out infinite;
      "></div>
      ` : ''}
      <!-- Tags row -->
      <div style="display: flex; gap: 0.5rem; margin-bottom: 1rem;">
        ${[45, 62, 38].map(w => `
          <div style="
            height: 24px; width: ${w}px; border-radius: 20px;
            background: linear-gradient(90deg, #e8e8e8 25%, #f4f4f4 50%, #e8e8e8 75%);
            background-size: 400% 100%;
            animation: skeletonShimmer 1.5s ease-in-out infinite;
          "></div>
        `).join('')}
      </div>
      <!-- Action row -->
      <div style="display: flex; gap: 1rem; padding-top: 0.75rem; border-top: 1px solid var(--border, #e5e7eb);">
        ${[72, 80, 60].map(w => `
          <div style="
            height: 30px; width: ${w}px; border-radius: 6px;
            background: linear-gradient(90deg, #e8e8e8 25%, #f4f4f4 50%, #e8e8e8 75%);
            background-size: 400% 100%;
            animation: skeletonShimmer 1.5s ease-in-out infinite;
          "></div>
        `).join('')}
      </div>
    </div>
  `;

  return `
    <style>
      @keyframes skeletonShimmer {
        0%   { background-position: 100% 0; }
        100% { background-position: -100% 0; }
      }
    </style>
    ${item(false)}
    ${item(true)}
    ${item(false)}
    ${item(true)}
    ${item(false)}
    ${item(false)}
    ${item(true)}
    ${item(false)}
    ${item(true)}
    ${item(false)}
  `;
}

/**
 * Check if widget has data
 */
export function hasWidgetData(widgetData) {
  if (Array.isArray(widgetData)) return widgetData.length > 0;
  if (typeof widgetData === 'object') return Object.keys(widgetData).length > 0;
  return false;
}

/**
 * Escape HTML to prevent XSS
 */
export function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

/**
 * Download resource helper
 */
export function downloadResource(url, filename) {
  const link = document.createElement('a');
  link.href = url;
  link.download = filename || 'download';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

/**
 * Navigate to profile page
 *
 * Batch 4 fix: both branches here sent the browser to the same wrong
 * URL — `/profile/${username}`, missing the /student blueprint prefix
 * this whole app is registered under, so this always 404'd regardless
 * of which branch ran (the typeof check never actually changed the
 * outcome). Also, `/student/profile/<username>` itself is a JSON API
 * route, not a page — so even with the prefix fixed, this would have
 * shown raw JSON instead of a profile page. Points at the new
 * /student/profile/view/<username> page route instead (see
 * view_profile_page() in routes/student/profile.py for why that's a
 * different path from the JSON API rather than the same one).
 */
export function viewProfile(username) {
  window.location.href = `/student/profile/view/${username}`;
}

/**
 * Share post helper
 */
export async function sharePost(postId) {
  const shareData = {
    title: 'Check out this post on LearnHub',
    url: `${window.location.origin}/posts/${postId}`
  };
  
  if (navigator.share) {
    try {
      await navigator.share(shareData);
      if (typeof showToast === 'function') {
        showToast('Post shared successfully!', 'success');
      }
    } catch (error) {
      if (error.name !== 'AbortError') {
        console.error('Share error:', error);
      }
    }
  } else {
    navigator.clipboard.writeText(shareData.url);
    if (typeof showToast === 'function') {
      showToast('Link copied to clipboard!', 'success');
    }
  }
}

/**
 * Open Learnora assistant
 */
export function openLearnora(postId) {
  if (typeof showToast === 'function') {
    showToast('Learnora feature coming soon!', 'info');
  }
}

/**
 * Search tag helper
 */
export function searchTag(tag) {
  if (typeof showToast === 'function') {
  }
  if (typeof navigateTo === 'function') {
    navigateTo('search');
  }
}

/**
 * Navigate to section helper
 */
export function navigateTo(sectionId, event) {
    if (event) event.preventDefault();

    // ── Messages: full-screen fixed overlay (NOT a .section) ───────────────
    if (sectionId === 'messages') {
        _openMessagesOverlay();
        return;
    }

    // ── Threads: full-screen fixed overlay, same as Messages ───────────────
    if (sectionId === 'threads') {
        _openThreadsOverlay();
        return;
    }

    const implementedSections = [
        'feed', 'profile', 'learnora', 'leaderboard', 'analytics', 'advanced-search', 'homework',
        'notifications', 'activity-feed', 'connections'
    ];

    if (!implementedSections.includes(sectionId)) {
        if (typeof showToast === 'function') {
            showToast(`${sectionId} feature coming soon!`, 'info');
        }
        return;
    }

    // Close messages overlay if it was open
    _closeMessagesOverlay();
    _closeThreadsOverlay();

    // Deactivate all sections then activate the target
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    const targetSection = document.getElementById(sectionId);
    if (targetSection) {
        targetSection.classList.remove('hidden');
        targetSection.classList.add('active');
        currentSection = sectionId;
    }

    // ── Header + bottom-nav visibility ─────────────────────────────────────
    if (sectionId === 'feed') {
  
        el.classList.remove('hidden');
      
        _bottomNav?.classList.remove('hidden');
        _helpBtn?.classList.remove('hidden');
    } else {
      
        _bottomNav?.classList.add('hidden');
        _helpBtn?.classList.add('hidden');
        
    }

    // Highlight correct nav item
    document.querySelectorAll('.nav-item, .bottom-nav-item').forEach(item => {
        item.classList.remove('active');
        if (item.dataset.target === sectionId) {
            item.classList.add('active');
        }
    });
    

    
}



/**
 * Open modal - ADDED EXPORT
 */

export function openModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modalId === 'help-volunteers-modal') {
    const requestId = window._activeHelpRequestId;
    if (requestId) {
      loadCurrentVolunteers(requestId);
    }
  }
  if (modal) {
    modal.classList.add('active');
    modal.classList.remove('hidden');
    document.body.style.overflow = 'hidden';

    // Hide the whole fixed top bar whenever any modal is open.
    // #header-bar is the actual position:fixed element (it owns the
    // background/shadow/z-index); #header is just the inner piece with
    // the logo + create-btn. Hiding only #header left #header-bar behind
    // as an empty fixed strip still pinned to the top of the screen.
    const headerBar = document.getElementById('header-bar');
    if (headerBar) headerBar.classList.add('hidden');
  }
}


/**
 * Close modal - ADDED EXPORT & ENHANCED CLEANUP
 */
export function closeModal(modalId) {
  const headerBar = document.getElementById("header-bar");
  const modal = document.getElementById(modalId);
  if (modal) {
    modal.classList.remove('active');
    modal.classList.add('hidden');
    
    document.body.style.overflow = '';

    // Show the whole fixed top bar again, but only if no OTHER modal/overlay
    // is still open. Covers both conventions used in this codebase:
    //   - standard modals: class="modal active"
    //   - tag-posts-modal: class="posts-modal hidden" (no "active" class)
    const tagModal = document.getElementById("tag-posts-modal");
    const tagModalOpen = tagModal && !tagModal.classList.contains("hidden");
    const anyOtherModalOpen = document.querySelector('.modal.active') || tagModalOpen;
    if (headerBar && !anyOtherModalOpen) headerBar.classList.remove('hidden');

    // Special cleanup for comment modal
    if (modalId === 'post-comments-modal') {
      // Import feedState dynamically to avoid circular dependency
      import('./feed.state.js').then(({ feedState }) => {
        feedState.clearCommentModalHistory();
      });
      
      // Import updatePreviousButton dynamically
      import('./feed.render.js').then(({ updatePreviousButton }) => {
        updatePreviousButton();
      });
      
      const commentInput = document.getElementById('commentInput');
      if (commentInput) {
        commentInput.value = '';
        delete commentInput.dataset.parentId;
        delete commentInput.dataset.postId;
      }
      
      const previewArea = document.getElementById('post-comments-preview-area');
      if (previewArea) {
        previewArea.innerHTML = '';
      }
    }
    
    // Cleanup for thread modal
    if (modalId === 'thread-view-modal') {
      const modalBody = modal.querySelector('#thread-details-content');
      if (modalBody) {
        modalBody.innerHTML = '';
      }
      delete modal.dataset.threadId;
      delete modal.dataset.type;
    }
    
    // Cleanup for refine modal
    if (modalId === 'post-refine-modal' || modalId === 'inline-post-refine-modal') {
      import('./feed.state.js').then(({ feedState }) => {
        feedState.clearRefinement();
      });
    }
    
    // Cleanup for fork modal
    if (modalId === 'post-fork-modal') {
      import('./feed.state.js').then(({ feedState }) => {
        feedState.clearForkTags();
      });
    }
    
    // Generic cleanup for other modals
    if (['post-comments-modal', 'thread-view-modal'].includes(modalId)) {
      const modalBody = modal.querySelector('.modal-body');
      if (modalBody && modalBody.id !== 'comments-container') {
        modalBody.innerHTML = '';
      }
    }
  }
}



/**
 * Toggle post options menu
 */
export function togglePostOptions(postId) {
  const optionsDiv = document.getElementById(`options-${postId}`);
  
  if (!optionsDiv) {
    console.warn(`Options menu not found for post ${postId}`);
    return;
  }
  
  // Close all other option menus
  document.querySelectorAll('.advanced-post-options').forEach(menu => {
    if (menu.id !== `options-${postId}`) {
      menu.classList.add('hidden');
    }
  });
  
  optionsDiv.classList.toggle('hidden');
}

/**
 * Set button loading state - NEW HELPER
 */
export function setButtonLoading(button, isLoading, loadingText = 'Loading...') {
  if (!button) return;
  
  if (isLoading) {
    button.dataset.originalText = button.textContent;
    button.disabled = true;
    button.innerHTML = `
      <span class="spinner-small"></span>
      ${loadingText}
    `;
  } else {
    button.disabled = false;
    button.textContent = button.dataset.originalText || 'Submit';
    delete button.dataset.originalText;
  }
}

/**
 * Create empty state component - NEW HELPER
 */
export function createEmptyState(config = {}) {
  const {
    icon = '📭',
    title = 'Nothing here yet',
    message = 'Be the first to create something!',
    actionText = null,
    actionHandler = null
  } = config;
  
  return `
    <div class="empty-state">
      <div class="empty-icon">${icon}</div>
      <h3 class="empty-title">${title}</h3>
      <p class="empty-message">${message}</p>
      ${actionText ? `
        <button class="btn btn-primary" data-action="${actionHandler}">
          ${actionText}
        </button>
      ` : ''}
    </div>
  `;
}

// Export for global use
if (typeof window !== 'undefined') {
  window.viewProfile = viewProfile;
  window.downloadResource = downloadResource;
  window.openModal = openModal;
  window.closeModal = closeModal;
  window.ShowNotification = ShowNotification;
  window.navigateTo = navigateTo;

}