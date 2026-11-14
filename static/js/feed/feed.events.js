/**
 * ============================================================================
 * FEED EVENTS - PRODUCTION READY
 * Event listeners and interaction handlers
 * FIXED: Container scoping, resource scrolling, thread creation
 * ============================================================================
 */

import { feedState } from './feed.state.js';
import { LONG_PRESS_TIME, AVAILABLE_TAGS, MAX_TAGS } from './feed.constants.js';
import * as feedApi from './feed.api.js';
import { closeModal, createEmptyState, getLoadingSkeleton } from './feed.utils.js';
import { initVideoAutoplay, notifySlideChange } from './feed.video_autoplay.js';

import { 
  renderFeed, 
  updateFilterButtons, 
  updateFeedContainerVisibility,
  highlightPost,
  clearAllHighlights,
  updateReactionDisplay,
  showReactionMenu,
  hideReactionMenu,
  updateCommentLikeButton,
  updateCommentHelpfulButton,
  removePostFromDOM,
  removeCommentFromDOM,
  updatePreviousButton,
  renderSelectedForkTags,
  renderPostComments,
  appendCommentToUI
} from './feed.render.js';


import { getReactionType,buildDynamicOptionsMenu, renderThreadDetailsHTML, createPostCard, buildPostResourcesContainer } from './feed.templates.js';

import { openCommentModal, saveForkedPost } from './feed.modals.js';



/**
 * Setup all event listeners
 */
export function setupAllEventListeners() {
  setupThreadTags();
  setupThreadTagsListeners();
  setupPostTagsListeners();
  setupPostTags();
  setupPostMediasListeners();
  setupForkTagsListeners();
  setupThreadListeners();
  setupCarouselScrollSync();
  setupCommentMediaListeners();
  setupGlobalClickListeners();
  
  setupViewTracking();
}
function setupCarouselScrollSync() {
  let scrollTimer = null;

  document.addEventListener('scroll', function(e) {
    const scrollContainer = e.target;

    // Only act on resource scroll containers
    if (!scrollContainer.classList?.contains('resources-scroll-container')) return;

    // Debounce: wait for snap-scroll to settle before reading position
    clearTimeout(scrollTimer);
    scrollTimer = setTimeout(() => {
      const carousel = scrollContainer.closest('.post-resources-carousel');
      if (!carousel) return;
      updateScrollIndicator(carousel, scrollContainer);
    }, 80);

  }, { capture: true, passive: true });
}
export function openResourceViewer(resources, index){
  viewer.openResourceViewer(resources, index);
}

/**
 * Toggle comment settings dropdown
 */
 export function handleToggleCommentSettings(target) {
  const commentCard = target.closest('.comment-card');
  if (!commentCard) return;
  const options = commentCard.querySelector('.advanced-comment-options');
  if (!options) return;

  // Close all other open dropdowns
  document.querySelectorAll('.advanced-comment-options').forEach(d => {
    if (d !== options) d.classList.add('hidden');
  });

  const isHidden = options.classList.contains('hidden');
  options.classList.toggle('hidden');

  if (isHidden) {
    // Position fixed relative to the toggle button
    const btnRect = target.getBoundingClientRect();
    options.style.position = 'fixed';
    options.style.top = `${btnRect.bottom + 3}px`;
    options.style.right = `${window.innerWidth - btnRect.right}px`;
    options.style.left = 'auto';
    options.style.zIndex = '10000';
  }
}


/**
 * ✅ FIXED: Handle post resource horizontal scroll
 */
export function handleScrollPostResource(direction, target) {
  const carousel = target.closest('.post-resources-carousel');
  if (!carousel) return;
  
  const scrollContainer = carousel.querySelector('.resources-scroll-container');
  if (!scrollContainer) return;
  
  const resourceWidth = scrollContainer.querySelector('.post-resource')?.offsetWidth || 0;
  const scrollAmount = resourceWidth + 8; // Include gap
  
  if (direction === 'right') {
    scrollContainer.scrollBy({ left: scrollAmount, behavior: 'smooth' });
  } else if (direction === 'left') {
    scrollContainer.scrollBy({ left: -scrollAmount, behavior: 'smooth' });
  }
  
  // Update indicator
  setTimeout(() => {
    updateScrollIndicator(carousel, scrollContainer);
  }, 150);
}

/**
 * ✅ NEW: Update scroll position indicator
 */

/**
 * ✅ FIXED: Update scroll position indicator
 * Handles both button clicks and manual scrolling
 */
function updateScrollIndicator(carousel, scrollContainer) {
  const indicator = carousel.querySelector('.carousel-indicator');
  if (!indicator) return;
  
  const scrollLeft = scrollContainer.scrollLeft;
  const containerWidth = scrollContainer.offsetWidth; // ✅ Use container width (snap scroll)
  const totalItems = scrollContainer.querySelectorAll('.post-resource').length;
  
  // ✅ More accurate index calculation for snap scroll
  const currentIndex = Math.round(scrollLeft / containerWidth);
  
  // ✅ Clamp to valid range (prevent -1 or overflow)
  const safeIndex = Math.max(0, Math.min(currentIndex, totalItems - 1));
  

  
  // Update indicator text
  indicator.textContent = `${safeIndex + 1}/${totalItems}`;
  
  // Update dots (if they exist)
  const dots = carousel.querySelectorAll('.carousel-dot');
  dots.forEach((dot, i) => {
    dot.classList.toggle('active', i === safeIndex);
  });
  
  // Update button states
  const prevBtn = carousel.querySelector('.carousel-prev');
  const nextBtn = carousel.querySelector('.carousel-next');
  
  if (prevBtn) {
    prevBtn.disabled = safeIndex === 0;
    prevBtn.style.opacity = safeIndex === 0 ? '0' : '1';
  }
  
  if (nextBtn) {
    nextBtn.disabled = safeIndex >= totalItems - 1;
    nextBtn.style.opacity = safeIndex >= totalItems - 1 ? '0' : '1';
  }
  notifySlideChange(carousel, safeIndex); // ←
}
export async function togglePostOptions(postId, event, containerType = 'smart-feed') {
  if (event) event.stopPropagation();
  
  const modal = document.getElementById("advanced-post-options-modal");
  if (!modal) {
    console.error('Advanced options modal not found');
    return;
  }
  
  // Show loading state
  const contentModal = modal.querySelector('.modal-content');
  contentModal.innerHTML = '<div class="loading">Loading options...</div>';
  openModal('advanced-post-options-modal');
  
  // Store context in modal
  modal.dataset.postId = postId;
  modal.dataset.containerType = containerType;
  
  try {
    // ✅ Fetch fresh options data
    const response = await feedApi.getPostOptionsMenu(postId);
    
    if (response.status !== 'success') {
      showToast(response.message, "error");
      contentModal.innerHTML = `<div class="error-state" style="padding:2rem;text-align:center;"><p>${response.message || 'Failed to load options'}</p></div>`;
      return;  // ← was missing; prevented crash on undefined data
    }
    
    const optionsData = response.data;
    
    // ✅ Render fresh options HTML
    contentModal.innerHTML = buildDynamicOptionsMenu(optionsData);
    
  } catch (error) {
    console.error('Failed to load post options:', error);
    contentModal.innerHTML = `
      <div class="error-state">
        <p>Failed to load options</p>
      </div>
    `;
  }
}

/**
 * Show comment resources in modal
 */
export function viewCommentResources(resources,index){
  window.openResourceViewer(resources, index);
}

/**
 * Show single comment resource
 */
export function showCommentResource(url, type) {
  if (!url || !type) return;
  
  const modal = document.getElementById("comment-resource-view-modal");
  if (!modal) {
    console.error('Comment resource modal not found');
    return;
  }
  
  let media;
  if (type === 'image') {
    media = `<img src="${url}" class='comment-resource' alt="Resource">`;
  } else if (type === 'video') {
    media = `<video src="${url}" class='comment-resource' controls></video>`;
  } else {
    media = `<p>Resource type not supported for preview</p>`;
  }
  
  modal.classList.remove('hidden');
  modal.innerHTML = `
    <button data-action='close-modal' data-modal-id='comment-resource-view-modal' class="modal-close">×</button>
    <div class='modal-content'>${media}</div>
  `;
}
/**
 * Ask Learnora about a post and display the answer in the Ask Learnora modal.
 * @param {number|string} postId
 * @param {string} [question] - Custom question; if omitted the backend picks a default.
 */
export async function askLearnora(postId, question = '') {
  const modal = document.getElementById('ask-learnora-modal');
  if (!modal) {
    return;
  }

  modal.dataset.postId = postId;
  openModal('ask-learnora-modal');

  const loadingEl    = document.getElementById('ask-learnora-loading');
  const answerEl     = document.getElementById('ask-learnora-answer');
  const answerTextEl = document.getElementById('ask-learnora-answer-text');
  const errorEl      = document.getElementById('ask-learnora-error');
  const errorMsgEl   = document.getElementById('ask-learnora-error-msg');
  const inputEl      = document.getElementById('ask-learnora-input');
  const createChatBtn = document.getElementById('ask-learnora-create-chat');

  // Reset to loading state
  loadingEl.style.display  = 'flex';
  answerEl.style.display   = 'none';
  errorEl.style.display    = 'none';
  if (createChatBtn) createChatBtn.style.display = 'none';

  // Seed the input with whatever question was used so the user can see/edit it
  if (inputEl && question) inputEl.value = question;

  try {
    const response = await feedApi.askLearnora(postId, question);

    if (!response || response.status !== 'success') {
      throw new Error(response?.message || 'Failed to get an answer from Learnora');
    }

    const answer = response?.data?.answer || '';
    answerTextEl.textContent = answer;
    // Store the answer on the modal so the Create Chat handler can read it
    modal.dataset.lastAnswer   = answer;
    modal.dataset.lastQuestion = response?.data?.question || question;

    loadingEl.style.display  = 'none';
    answerEl.style.display   = 'block';
    if (createChatBtn) createChatBtn.style.display = 'flex';

  } catch (error) {
    console.error('Ask Learnora error:', error);
    errorMsgEl.textContent  = error.message || 'Please try again.';
    loadingEl.style.display = 'none';
    errorEl.style.display   = 'block';
  }
}

/**
 * Create a new Learnora chat seeded with the last post answer,
 * then navigate to /student/learnora with the new conversation open.
 */
export async function handleCreateLearnoraChat() {
  const modal    = document.getElementById('ask-learnora-modal');
  const btn      = document.getElementById('ask-learnora-create-chat');
  if (!modal || !btn) return;

  const postId   = modal.dataset.postId;
  const question = modal.dataset.lastQuestion || '';
  const answer   = modal.dataset.lastAnswer   || '';

  // Build a seed message so the user lands in a context-aware chat
  const seed = question
    ? `Regarding the post I was just reading:\n\nQ: ${question}\n\nA: ${answer}\n\nLet's continue from here.`
    : answer;

  const originalHTML = btn.innerHTML;
  btn.disabled   = true;
  btn.innerHTML  = `
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" class="learnora-spin">
      <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
    </svg>
    Creating chat…`;

  try {
    const data = await feedApi.createLearnoraChat(seed);
    // Navigate to Learnora with the new conversation selected
    window.location.href = `/student/learnora?conversation_id=${data.conversation_id}`;
  } catch (error) {
    console.error('Create Learnora chat error:', error);
    showToast('Failed to create chat. Please try again.', 'error');
    btn.disabled  = false;
    btn.innerHTML = originalHTML;
  }
}

/**
 * Show comment resource links modal
 */
export function showCommentResourceLinks(target) {
  const commentCard = target.closest('.comment-card');
  if (!commentCard) return;
  
  const resourcesData = commentCard.dataset.resources;
  let resources;
  try {
    resources = JSON.parse(resourcesData);
  } catch (e) {
    console.error('Failed to parse resources:', e);
    return;
  }
  
  if (!resources || resources.length === 0) return;
  
  const linkHTML = resources.map(resource => `
    <div class='resource-link-container'>
      <span class="resource-link-name">${resource.filename || 'Resource'}</span>
      <button class="download-btn" data-action='download-resource' data-url='${resource.url}' data-filename='${resource.filename}'>
        Download
      </button>
    </div>
  `).join("");
  
  const modal = document.getElementById("comment-resource-download-modal");
  if (!modal) {
    console.error('Comment resource download modal not found');
    return;
  }
  
  modal.innerHTML = `
    <div class='modal'>
      <button data-action='close-modal' data-modal-id='comment-resource-download-modal' class="modal-close">×</button>
      <div class='modal-content'>${linkHTML}</div>
    </div>
  `;
  modal.classList.remove('hidden');
}

/**
 * Show/hide post resource links
 */
export function showPostResourceLinks(target) {
  const postCard = target.closest('.post-card');
  if (!postCard) return;
  
  const linkModal = postCard.querySelector('.resource-link-modal');
  const toggleIcon = target.querySelector('.toggle-icon');
  const toggleText = target.querySelector('.toggle-text');
  
  if (!linkModal) {
    console.error('Resource link modal not found');
    return;
  }
  
  const isHidden = linkModal.classList.contains('hidden');
  
  if (isHidden) {
    linkModal.classList.remove('hidden');
    if (toggleIcon) toggleIcon.style.transform = 'rotate(180deg)';
    if (toggleText) toggleText.textContent = 'Hide Download Links';
  } else {
    linkModal.classList.add('hidden');
    if (toggleIcon) toggleIcon.style.transform = 'rotate(0deg)';
    if (toggleText) toggleText.textContent = 'Show Download Links';
  }
}
// ===============================
// Upload Limits
// ===============================
const MAX_POST_RESOURCES = 10;
const MAX_COMMENT_RESOURCES = 10;

const FILE_SIZE_LIMITS = {
  image: 5 * 1024 * 1024,   // 5MB
  video: 50 * 1024 * 1024,  // 50MB
  other: 10 * 1024 * 1024   // 10MB
};

function validateFile(file) {
  let maxSize;

  if (file.type.startsWith("image/")) {
    maxSize = FILE_SIZE_LIMITS.image;
  } else if (file.type.startsWith("video/")) {
    maxSize = FILE_SIZE_LIMITS.video;
  } else {
    maxSize = FILE_SIZE_LIMITS.other;
  }

  if (file.size > maxSize) {
    showToast(
      `File "${file.name}" exceeds size limit (${(maxSize / 1024 / 1024)}MB max)`,
      "error"
    );
    return false;
  }

  return true;
}

// --------------------------
// Post Media Upload
// --------------------------
export function setupPostMediasListeners() {
  const previewArea = document.getElementById("post-medias-preview-container");
  if (!previewArea) return console.warn('Post preview area not found');

  const fileInputs = document.querySelectorAll(".input-file");

  fileInputs.forEach(input => {
    const newInput = input.cloneNode(true);
    input.parentNode.replaceChild(newInput, input);

    newInput.addEventListener("change", async function(e) {
      const files = Array.from(e.target.files);
      if (files.length === 0) return;

      for (const file of files) {
        const currentResources = feedState.getPostResources() || [];
        if (currentResources.length >= MAX_POST_RESOURCES) {
          showToast(`Maximum ${MAX_POST_RESOURCES} media files per post`, "error");
          break;
        }

        if (!validateFile(file)) continue;

        const previewDiv = document.createElement("div");
        previewDiv.className = "preview-item";
        previewDiv.style.cssText = "position: relative; display: inline-block; margin: 0.5rem;";

        let media;
        if (file.type.startsWith("image/")) {
          media = document.createElement("img");
          media.src = URL.createObjectURL(file);
          media.style.cssText = "max-width: 150px; max-height: 150px; border-radius: 8px;";
        } else if (file.type.startsWith("video/")) {
          media = document.createElement("video");
          media.src = URL.createObjectURL(file);
          media.controls = true;
          media.style.cssText = "max-width: 150px; max-height: 150px; border-radius: 8px;";
        } else {
          media = document.createElement("div");
          media.className = "file-name";
          media.textContent = file.name;
          media.style.cssText = "padding: 1rem; background: var(--bg-tertiary); border-radius: 8px; font-size: 0.875rem;";
        }

        previewDiv.appendChild(media);

        const loader = document.createElement("div");
        loader.className = "loader";
        loader.style.cssText = "position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); background: rgba(0,0,0,0.7); color: white; padding: 0.5rem; border-radius: 4px; font-size: 0.75rem;";
        loader.textContent = "Uploading...";

        const btn = document.createElement('button');
        btn.className = "cancel-upload";
        btn.textContent = "×";
        btn.style.cssText = "position: absolute; top: 5px; right: 5px; background: rgba(0,0,0,0.7); color: white; border: none; border-radius: 50%; width: 24px; height: 24px; cursor: pointer; display: none;";

        previewDiv.appendChild(loader);
        previewDiv.appendChild(btn);
        previewArea.appendChild(previewDiv);

        try {
          const result = await feedApi.uploadResource(file);
          if (result.status === "success") {
            const resource = { url: result.data.url, type: result.data.type, filename: result.data.filename };
            feedState.addPostResource(resource);
            loader.remove();
            btn.style.display = "block";

            btn.onclick = function() {
              previewDiv.remove();
              feedState.removePostResource(resource.url);
            };
          } else {
            loader.textContent = "Failed";
            loader.style.background = "var(--danger)";
            showToast("Upload failed: " + (result.message || "Unknown error"), "error");
          }
        } catch (error) {
          loader.textContent = "Error";
          loader.style.background = "var(--danger)";
          showToast("Error uploading file", "error");
        }
      }
      e.target.value = "";
    });
  });

  console.log('✅ Post media listeners setup complete');
}

// --------------------------
// Comment Media Upload
// --------------------------
export function setupCommentMediaListeners() {
  const modal = document.getElementById("post-comments-modal");
  if (!modal) return console.warn('Comments modal not found');

  const previewArea = document.getElementById("post-comments-preview-area");
  if (!previewArea) return console.warn('Comment preview area not found');

  const fileInputs = modal.querySelectorAll('input[type="file"]');

  fileInputs.forEach(input => {
    const newInput = input.cloneNode(true);
    input.parentNode.replaceChild(newInput, input);

    newInput.addEventListener("change", async function(e) {
      const files = Array.from(e.target.files);
      if (!files.length) return;

      for (const file of files) {
        const currentResources = feedState.getReplyResources() || [];
        if (currentResources.length >= MAX_COMMENT_RESOURCES) {
          showToast(`Maximum ${MAX_COMMENT_RESOURCES} files per comment`, "error");
          break;
        }

        if (!validateFile(file)) continue;

        const previewDiv = document.createElement("div");
        previewDiv.className = "preview-item";
        previewDiv.style.cssText = "position: relative; display: inline-block; margin: 0.5rem;";

        let media;
        if (file.type.startsWith("image/")) {
          media = document.createElement("img");
          media.src = URL.createObjectURL(file);
          media.style.cssText = "max-width: 150px; max-height: 150px; border-radius: 8px;";
        } else if (file.type.startsWith("video/")) {
          media = document.createElement("video");
          media.src = URL.createObjectURL(file);
          media.controls = true;
          media.style.cssText = "max-width: 150px; max-height: 150px; border-radius: 8px;";
        } else {
          media = document.createElement("div");
          media.className = "file-name";
          media.textContent = file.name;
          media.style.cssText = "padding: 1rem; background: var(--bg-tertiary); border-radius: 8px; font-size: 0.875rem;";
        }

        previewDiv.appendChild(media);

        const loader = document.createElement("div");
        loader.className = "loader";
        loader.style.cssText = "position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); background: rgba(0,0,0,0.7); color: white; padding: 0.5rem; border-radius: 4px; font-size: 0.75rem;";
        loader.textContent = "Uploading...";

        const btn = document.createElement('button');
        btn.className = "cancel-upload";
        btn.textContent = "×";
        btn.style.cssText = "position: absolute; top: 5px; right: 5px; background: rgba(0,0,0,0.7); color: white; border: none; border-radius: 50%; width: 24px; height: 24px; cursor: pointer; display: none;";

        previewDiv.appendChild(loader);
        previewDiv.appendChild(btn);
        previewArea.appendChild(previewDiv);

        try {
          const result = await feedApi.uploadResource(file);
          if (result.status === "success") {
            const resource = { url: result.data.url, type: result.data.type, filename: result.data.filename };
            feedState.addReplyResource(resource);
            loader.remove();
            btn.style.display = "block";

            btn.onclick = function() {
              previewDiv.remove();
              feedState.removeReplyResource(resource.url);
            };
          } else {
            loader.textContent = "Failed";
            loader.style.background = "var(--danger)";
            showToast("Upload failed: " + (result.message || "Unknown error"), "error");
          }
        } catch (error) {
          loader.textContent = "Error";
          loader.style.background = "var(--danger)";
          showToast("Error uploading file", "error");
        }
      }

      e.target.value = "";
    });
  });

  console.log('✅ Comment media listeners setup complete');
}
/**
 * Handle view tag posts
 */
export async function handleViewTagPosts(tag) {
  const modal = document.getElementById('tag-posts-modal');
  const container = document.getElementById('tag-posts-container');
  const title = document.getElementById('tag-modal-title');
  
  if (!modal || !container) {
    showToast('Tag posts view not available', 'error');
    return;
  }
  openModal("tag-posts-modal");
  

  if (title) title.textContent = `Posts tagged #${tag}`;
  
  container.innerHTML = getLoadingSkeleton();
  
  try {
    const { posts } = await feedApi.getPostsByTag(tag);
    
    if (!posts || posts.length === 0) {
      container.innerHTML = createEmptyState({
        icon: '🏷️',
        title: 'No posts found',
        message: `No posts tagged with #${tag}`
      });
      return;
    }
    
    const postsHTML = posts.map(post => createPostCard(post)).join('');
    container.innerHTML = postsHTML;
    

    
  } catch (error) {
    showToast('Failed to load tagged posts: ' + error.message, 'error');
    container.innerHTML = `<div class="error-state"><p>${error.message}</p></div>`;
  }
}

/**
 * Setup post media upload listeners
 */

/**
 * ✅ NEW: Setup resource horizontal drag scroll
 */
export function setupResourceDragScroll() {
  document.addEventListener('mousedown', (e) => {
    const scrollContainer = e.target.closest('.resources-scroll-container');
    if (!scrollContainer) return;
    
    let isDown = true;
    let startX = e.pageX - scrollContainer.offsetLeft;
    let scrollLeft = scrollContainer.scrollLeft;
    
    scrollContainer.style.cursor = 'grabbing';
    
    const onMouseMove = (e) => {
      if (!isDown) return;
      e.preventDefault();
      const x = e.pageX - scrollContainer.offsetLeft;
      const walk = (x - startX) * 2;
      scrollContainer.scrollLeft = scrollLeft - walk;
    };
    
    const onMouseUp = () => {
      isDown = false;
      scrollContainer.style.cursor = 'grab';
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
    };
    
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  });
  
  // Touch support
  document.addEventListener('touchstart', (e) => {
    const scrollContainer = e.target.closest('.resources-scroll-container');
    if (!scrollContainer) return;
    
    let touchStartX = e.touches[0].pageX;
    let scrollLeft = scrollContainer.scrollLeft;
    
    const onTouchMove = (e) => {
      const touchX = e.touches[0].pageX;
      const walk = (touchX - touchStartX) * 2;
      scrollContainer.scrollLeft = scrollLeft - walk;
    };
    
    const onTouchEnd = () => {
      scrollContainer.removeEventListener('touchmove', onTouchMove);
      scrollContainer.removeEventListener('touchend', onTouchEnd);
    };
    
    scrollContainer.addEventListener('touchmove', onTouchMove, { passive: true });
    scrollContainer.addEventListener('touchend', onTouchEnd);
  });
  
  console.log('✅ Resource drag scroll setup complete');
}

/**
 * Setup comment media upload listeners
 */


 
export async function setupThreadListeners() {
  const avatarInput = document.getElementById("thread-avatar-input");
  const threadAvatar = document.getElementById('thread-avatar');
  
  if (!avatarInput || !threadAvatar) {
    console.warn('Thread avatar elements not found');
    return;
  }
  
  avatarInput.addEventListener("change", async function(e) {
    const file = e.target.files[0];
    if (!file) return;
    
    if (!file.type.startsWith('image/')) {
      showToast('Please select an image file', 'error');
      return;
    }
    
    if (file.size > 2 * 1024 * 1024) {
      showToast('Image must be less than 2MB', 'error');
      return;
    }
    
    const previewUrl = URL.createObjectURL(file);
    threadAvatar.src = previewUrl;
    
    try {
      const result = await feedApi.uploadResource(file);
      
      if (result.status === "success") {
        const resource = {
          url: result.data.url,
          type: result.data.type,
          filename: result.data.filename
        };
        
        feedState.setThreadAvatar(resource);
        threadAvatar.src = resource.url;
      } else {
        showToast(result.message || 'Upload failed', 'error');
        threadAvatar.src = '/static/default-thread-avatar.png';
        feedState.setThreadAvatar(null);
      }
    } catch (error) {
      console.error('Thread avatar upload error:', error);
      showToast('Failed to upload avatar: ' + error.message, 'error');
      threadAvatar.src = '/static/default-thread-avatar.png';
      feedState.setThreadAvatar(null);
    }
  });
}

/**
 * Setup thread tags listeners
 */
export function setupThreadTagsListeners() {
  const threadTagsDropdown = document.getElementById("thread-tags-dropdown");
  const threadTagInput = document.getElementById("thread-tag-input");
  
  if (!threadTagInput || !threadTagsDropdown) {
    console.warn("Thread tags elements not found");
    return;
  }

  threadTagInput.addEventListener("input", function(e) {
    const input = e.target.value.toLowerCase();
    
    if (input.length === 0) {
      threadTagsDropdown.classList.add("hidden");
      return;
    }
    
    if (feedState.getThreadTags().length >= MAX_TAGS) {
      threadTagsDropdown.classList.add('hidden');
      return;
    }
    
    const relatedTags = AVAILABLE_TAGS.filter(tag => 
      tag.toLowerCase().includes(input) && !feedState.getThreadTags().includes(tag)
    );
    
    if (relatedTags.length > 0) {
      threadTagsDropdown.innerHTML = relatedTags.slice(0, 10).map(tag => 
        `<div data-value="${tag}" class="tag-option" data-action="add-thread-tag">${tag}</div>`
      ).join('');
      threadTagsDropdown.classList.remove('hidden');
    } else {
      threadTagsDropdown.classList.add('hidden');
    }
  });

  threadTagInput.addEventListener("keydown", function(e) {
    if (e.key === "Enter") {
      e.preventDefault();
      const value = e.target.value.trim();
      if (value && feedState.getThreadTags().length < MAX_TAGS) {
        addThreadTag(value);
        threadTagInput.value = '';
        threadTagsDropdown.classList.add('hidden');
      }
    }
  });
}

export function setupPostTagsListeners() {
  const postTagsDropdown = document.getElementById("post-tags-dropdown");
  const postTagInput = document.getElementById("post-tags-input");
  
  if (!postTagInput || !postTagsDropdown) {
    console.warn("Post tags elements not found");
    return;
  }

  postTagInput.addEventListener("input", function(e) {
    const input = e.target.value.toLowerCase();
    
    if (input.length === 0) {
      postTagsDropdown.classList.add("hidden");
      return;
    }
    
    if (feedState.getPostTags().length >= MAX_TAGS) {
      postTagsDropdown.classList.add('hidden');
      return;
    }
    
    const relatedTags = AVAILABLE_TAGS.filter(tag => 
      tag.toLowerCase().includes(input) && !feedState.getPostTags().includes(tag)
    );
    
    if (relatedTags.length > 0) {
      postTagsDropdown.innerHTML = relatedTags.slice(0, 10).map(tag => 
        `<div data-value="${tag}" class="tag-option" data-action="add-post-tag">${tag}</div>`
      ).join('');
      postTagsDropdown.classList.remove('hidden');
    } else {
      postTagsDropdown.classList.add('hidden');
    }
  });

  postTagInput.addEventListener("keydown", function(e) {
    if (e.key === "Enter") {
      e.preventDefault();
      const value = e.target.value.trim();
      if (value && feedState.getPostTags().length < MAX_TAGS) {
        addPostTag(value);
        postTagInput.value = '';
        postTagsDropdown.classList.add('hidden');
      }
    }
  });
}
/**
 * Add thread tag
 */
export function addThreadTag(tag) {
  if (feedState.getThreadTags().length >= MAX_TAGS) {
    showToast(`Maximum ${MAX_TAGS} tags allowed`, 'warning');
    return;
  }
  feedState.addThreadTag(tag);
  renderThreadTags();
}

/**
 * Remove thread tag
 */
export function removeThreadTag(tag) {
  feedState.removeThreadTag(tag);
  renderThreadTags();
}

/**
 * Render thread tags
 */
function renderThreadTags() {
  const container = document.getElementById('thread-selected-tags');
  if (!container) return;
  
  const tags = feedState.getThreadTags();
  container.innerHTML = tags.map(tag => 
    `<span class="tag-badge">
      ${tag}
      <button type="button" class="tag-remove" data-action="remove-thread-tag" data-value="${tag}">×</button>
    </span>`
  ).join('');
}

/**
 * Add post tag
 */
export function addPostTag(tag) {
  if (feedState.getPostTags().length >= MAX_TAGS) {
    showToast(`Maximum ${MAX_TAGS} tags allowed`, 'warning');
    return;
  }
  feedState.addPostTag(tag);
  renderPostTags();
}

/**
 * Remove post tag
 */
export function removePostTag(tag) {
  feedState.removePostTag(tag);
  renderPostTags();
}

/**
 * Render post tags
 */
export function renderPostTags() {
  const container = document.getElementById("selected-post-tags");
  if (!container) return;
  
  const tags = feedState.getPostTags();
  container.innerHTML = tags.map(tag => 
    `<span class="tag-badge">
      ${tag}
      <button type="button" class="tag-remove" data-action="remove-post-tag" data-value="${tag}">×</button>
    </span>`
  ).join('');
}

export function syncPostUI(type, data, containerType = 'smart-feed') {
  if (!data || !data.post_id) {
    console.error('syncPostUI: Invalid data', data);
    return;
  }
  
  // ✅ Find the EXACT container where the interaction happened
  let container;
  
  if (containerType === 'tag-modal') {
    container = document.getElementById('tag-posts-container');
  } else if (containerType === 'comments-modal') {
    container = document.getElementById('comments-container');
  } else if (containerType === 'bookmarks') {
    container = document.getElementById('bookmarks');
  } else {
    // For smart-feed, ONLY get the ACTIVE feed (visible filter)
    container = document.querySelector('.posts-feed.active');
  }
  
  if (!container) {
    console.error(`Container ${containerType} not found or not active`);
    return;
  }
  
  // ✅ Find post ONLY in this specific container
  const post = container.querySelector(`[data-post-id="${data.post_id}"]`);
  
  if (!post) {
    console.warn(`Post ${data.post_id} not found in active ${containerType} container`);
    return;
  }

  // ✅ Update ONLY this instance (other filters remain stale by design)
  try {
    switch (type) {
      case 'reaction':
        syncPostReaction(post, data);
        break;
      case 'follow':
        syncPostFollow(post, data);
        break;
      case 'unfollow':
        syncPostUnfollow(post, data);
        break;
      case 'mark-solved':
        syncPostSolved(post, true);
        break;
      case 'unmark-solved':
        syncPostSolved(post, false);
        break;
      case 'comment_count':
        syncCommentCount(post, data);
        break;
      case 'bookmark':
        applyBookmarkUI(data.post_id, !!data.bookmarked);
        break;
      case 'delete-post':
        post.remove();
        break;
      default:
        console.warn('Unknown sync type:', type);
    }
  } catch (error) {
    console.error(`syncPostUI error for "${type}":`, error);
  }
}
function syncCommentCount(post, data) {
  const commentBtn = post.querySelector('[data-action="open-comments"]');
  if (!commentBtn) return;
  const count = data.comments_count ?? 0;
  // Update only the <span> inside the button — preserves the SVG icon
  const span = commentBtn.querySelector('span');
  if (span) span.textContent = count;
}
// ✅ Helper functions remain the same


function syncPostReaction(post, data) {
  const reactionBtn = post.querySelector('.reaction-btn');
  if (!reactionBtn) return;

  const reacted = data.reacted;
  const count   = data.count || 0;

  // ✅ Just toggle class + update count span — never touch innerHTML
  reactionBtn.classList.toggle('reacted', reacted);

  const countSpan = reactionBtn.querySelector('.reaction-count');
  if (countSpan) {
    countSpan.textContent = count > 0 ? count : '';
  }
}

function syncPostFollow(post, data) {
  const followBtn = post.querySelector('[data-action="follow-post"]');
  if (followBtn) {
    followBtn.dataset.action = 'unfollow-post';
    followBtn.textContent = '👁️ Unfollow';
  }
}

function syncPostUnfollow(post, data) {
  const unfollowBtn = post.querySelector('[data-action="unfollow-post"]');
  if (unfollowBtn) {
    unfollowBtn.dataset.action = 'follow-post';
    unfollowBtn.textContent = '👁️ Follow';
  }
}

function syncPostSolved(post, isSolved) {
  const header = post.querySelector('.post-header');
  if (!header) return;

  const existingBadge = header.querySelector('.solved-badge');
  
  if (isSolved && !existingBadge) {
    const badge = document.createElement('span');
    badge.className = 'solved-badge';
    badge.textContent = '✓ Solved';
    badge.style.cssText = 'padding: 0.25rem 0.5rem; background: var(--success); color: white; border-radius: 4px; font-size: 0.75rem; margin-left: 0.5rem;';
    
    // Try to add to existing badges container first
    const badges = header.querySelector('.post-header-badges');
    if (badges) {
      badges.appendChild(badge);
    } else {
      // Create badges container
      const authorDiv = header.querySelector('.post-author');
      if (authorDiv) {
        const badgesDiv = document.createElement('div');
        badgesDiv.className = 'post-header-badges';
        badgesDiv.style.cssText = 'display: flex; gap: 0.5rem; margin-top: 0.5rem;';
        badgesDiv.appendChild(badge);
        authorDiv.appendChild(badgesDiv);
      }
    }
  } else if (!isSolved && existingBadge) {
    existingBadge.remove();
  }
}


/**
 * Handle switch to previous comment view
 */
export function handleSwitchComment(event) {
  event.stopPropagation();
  
  const history = feedState.getCommentModalHistory();
  const previousState = history.pop();
  
  if (!previousState) {
    closeModal('post-comments-modal');
    return;
  }
  
  updatePreviousButton();
  
  const commentsModal = document.getElementById("comments-container");
  if (!commentsModal) return;
  
  commentsModal.innerHTML = previousState.html;
  commentsModal.scrollTop = previousState.scrollTop;
}

/**
 * Handle listen to post (text-to-speech)
 */
export function handleListenPost(postId, event, containerType) {
  if (event) event.stopPropagation();
  
  if (window.speechSynthesis.speaking) {
    window.speechSynthesis.cancel();
    return;
  }

  // Find post in correct container
  let container;
  if (containerType === 'tag-modal') {
    container = document.getElementById('tag-posts-container');
  } else {
    container = document.getElementById('posts-container');
  }
  
  if (!container) {
    return;
  }
  
  const post = container.querySelector(`[data-post-id="${postId}"]`);
  if (!post) {
    return;
  }

  const contentEl = post.querySelector(".post-content");
  const titleEl = post.querySelector(".post-title");

  if (!contentEl) {
    return;
  }

  const title = titleEl ? titleEl.textContent : '';
  const content = contentEl.textContent;
  const text = `${title} ${content}`.trim();
  
  if (!text) {
    return;
  }

  const speech = new SpeechSynthesisUtterance(text);
  speech.rate = 1;
  speech.pitch = 1;
  speech.lang = "en-US";
  
  speech.onstart = () => {
  };
  
  speech.onend = () => {
  };
  
  speech.onerror = (error) => {
    console.error("Speech error:", error);
    showToast("Speech failed: " + error.error, "error");
  };
  
  window.speechSynthesis.speak(speech);
}

/**
 * Setup thread tags (separate from listeners)
 */
function setupThreadTags() {
  renderThreadTags();
}

/**
 * Setup post tags (separate from listeners)
 */
function setupPostTags() {
  renderPostTags();
}

/**
 * Handle create post
 */
export async function handleCreatePost(event, triggerTarget) {
  event.preventDefault();
  const memberIds = [];
  
  const postTitle = document.getElementById("post-title")?.value || '';
  const postContent = document.getElementById("post-content")?.value;
  const postTags = feedState.getPostTags();
  const postType = document.getElementById('post-type')?.value || 'discussion';
  const postResources = feedState.getPostResources();
  const threadEnabled = document.getElementById('thread-toggle')?.checked || false;
  const memberId = document.getElementById("create-thread-modal")?.dataset?.postAuthorId;
  if (memberId) {
    memberIds.push(memberId);
  }
  
  if (!postContent || !postContent.trim()) {
    showToast("Please enter post content", "warning");
    return;
  }
  
  let threadTitle = '';
  let threadDescription = '';
  let maxMembers = null;
  let requiresApproval = false;
  
  if (threadEnabled) {
    threadTitle = document.getElementById("thread-title")?.value || '';
    threadDescription = document.getElementById('thread-description')?.value || '';
    const maxMembersInput = document.getElementById("thread-max-members")?.value;
    maxMembers = maxMembersInput ? parseInt(maxMembersInput) : null;
    requiresApproval = document.getElementById("thread-approval-toggle")?.checked || false;
  }
  
  const payload = {
    title: postTitle,
    text_content: postContent,
    post_type: postType,
    tags: postTags,
    resources: postResources,
    thread_enabled: threadEnabled,
    thread_title: threadTitle,
    thread_description: threadDescription,
    max_members: maxMembers,
    menber_ids: memberIds,
    requires_approval: requiresApproval
  };
  
  const submitBtn = (triggerTarget instanceof Element && triggerTarget.tagName === 'BUTTON')
    ? triggerTarget
    : document.querySelector('#create-post-form [data-action="submit-create-post"]');
  
  const originalText = submitBtn ? submitBtn.textContent : '';
  
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = 'Creating...';
  }
  
  try {
    const response = await feedApi.uploadPost(payload);
    
    if (response && response.status === 'success') {
      closeModal("create-post-modal");
      
      const postTitleEl = document.getElementById("post-title");
      const postContentEl = document.getElementById("post-content");
      const postTypeEl = document.getElementById("post-type");
      const threadToggleEl = document.getElementById("thread-toggle");
      const threadTitleEl = document.getElementById("thread-title");
      const threadDescEl = document.getElementById("thread-description");
      const threadMaxEl = document.getElementById("thread-max-members");
      const threadApprovalEl = document.getElementById("thread-approval-toggle");
      if (postTitleEl) postTitleEl.value = '';
      if (postContentEl) postContentEl.value = '';
      if (postTypeEl) postTypeEl.value = 'discussion';
      if (threadToggleEl) threadToggleEl.checked = false;
      if (threadTitleEl) threadTitleEl.value = '';
      if (threadDescEl) threadDescEl.value = '';
      if (threadMaxEl) threadMaxEl.value = '';
      if (threadApprovalEl) threadApprovalEl.checked = false;
      // Hide thread settings panel if visible
      const threadSettings = document.getElementById('create-post-thread-details-modal');
      if (threadSettings) threadSettings.classList.add('hidden');
      feedState.clearPostTags();
      feedState.clearPostResources();
      
      const selectedTags = document.getElementById("selected-post-tags");
      if (selectedTags) selectedTags.innerHTML = "";
      
      const previewArea = document.getElementById("post-medias-preview-container");
      if (previewArea) previewArea.innerHTML = "";
      
      const currentFilter = feedState.getCurrentFilter();
      const { posts } = await feedApi.loadPostsByFilter(currentFilter);
      feedState.setPosts(currentFilter, posts);
      await renderFeed(currentFilter);
    } else {
      showToast(response?.message || "Failed to create post", 'error');
    }
  } catch (error) {
    showToast("Upload post error: " + error.message, 'error');
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = originalText;
    }
  }
}

/**
 * Handle create thread
 */
export async function handleCreateThread(event, target) {

  const threadTitle = document.getElementById("thread-title-input")?.value;
  const threadDescription = document.getElementById("thread-description-input")?.value;
  const threadTags = feedState.getThreadTags();
  const maxMembersInput = document.getElementById("thread-maximum-members")?.value;
  const maxMembers = maxMembersInput ? parseInt(maxMembersInput) : null;
  const requiresApproval = document.getElementById("thread-require-approval")?.checked || false;

  const modal = document.getElementById('create-thread-modal');

  const memberIds = modal?.dataset.memberIds
    ? modal.dataset.memberIds.split(',').map(id => parseInt(id))
    : [];

  const threadAvatar = feedState.getThreadResource();

  if (!threadTitle?.trim()) {
    showToast("Please enter thread title", "warning");
    return;
  }

  if (!threadDescription?.trim()) {
    showToast("Please enter thread description", "warning");
    return;
  }

  const payload = {
    title: threadTitle,
    description: threadDescription,
    tags: threadTags,
    max_members: maxMembers,
    member_ids: memberIds,
    requires_approval: requiresApproval,
    avatar: threadAvatar
  };

  const submitBtn = (target instanceof Element && target.tagName === 'BUTTON')
    ? target
    : document.querySelector('#create-thread-form [data-action="submit-create-thread"]');

  const originalText = submitBtn?.textContent;

  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = 'Creating...';
  }

  try {
    const response = await feedApi.createThread(payload);

    if (response?.status === 'success') {
      closeModal("create-thread-modal");
      showToast("Thread created succesfully");


      document.getElementById("thread-title-input").value = '';
      document.getElementById("thread-description-input").value = '';
      document.getElementById("thread-maximum-members").value = '';
      document.getElementById("thread-require-approval").checked = false;
      document.getElementById("thread-tag-input").value = '';

      feedState.setThreadAvatar(null);
      feedState.clearThreadTags();

      const avatar = document.getElementById('thread-avatar');
      if (avatar) avatar.src = '';

      renderThreadTags();
    } else {
      showToast(response?.message || "Failed to create thread", 'error');
    }

  } catch (error) {
    showToast("Create thread error: " + error.message, 'error');
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = originalText;
    }
  }
}
/**
 * ✅ NEW: Handle create thread from post
 */
export async function handleCreateThreadFromPost(postId, userId, event) {
  if (event) event.stopPropagation();

  try {
    let memberId;

    if (userId) {
      // userId provided directly — skip post lookup
      memberId = userId;
    } else {
      // Fall back to resolving the author from the post
      if (!postId) {
        showToast("No post or user provided", "error");
        return;
      }

      const response = await feedApi.getPostQuickView(postId);
      if (!response) {
        showToast("Failed to load post data", "error");
        return;
      }

      const post = response;
      const authorId = post.author?.id;

      if (!authorId) {
        showToast("Couldn't find the post author", "error");
        return;
      }

      // Pre-fill form with post data
      const titleInput = document.getElementById("thread-title-input");
      const descInput = document.getElementById("thread-description-input");

      if (titleInput) titleInput.value = post.title || "Study thread";
      if (descInput) descInput.value = post.text_content?.substring(0, 200) || "";

      if (post.tags && post.tags.length > 0) {
        feedState.clearThreadTags();
        post.tags.slice(0, MAX_TAGS).forEach(tag => feedState.addThreadTag(tag));
        renderThreadTags();
      }

      memberId = authorId;
    }

    // Open create-thread modal
    const modal = document.getElementById("create-thread-modal");
    if (!modal) return;

    modal.classList.remove("hidden");
    modal.classList.add("active");

    if (postId) modal.dataset.fromPostId = postId;
    modal.dataset.memberIds = String(memberId);

  } catch (error) {
    console.error("Create thread from post error:", error);
    showToast("Failed to create thread: " + error.message, "error");
  }
}

/**
 * Setup reaction listeners
 */
export function setupReactionListeners() {
  const reactionMenu = document.getElementById("reactionMenu");
  
  if (!reactionMenu) {
    console.warn("Reaction menu not found");
    return;
  }
  
  let longPressTimer = null;
  let longPressTarget = null;
  let isLongPress = false;
  
  // ==================== MOUSE EVENTS ====================
  document.addEventListener('mousedown', (e) => {
    const reactionBtn = e.target.closest('[data-action="toggle-reactions"]');
    if (!reactionBtn) return;
    
    e.preventDefault();
    longPressTarget = reactionBtn;
    isLongPress = false;
    feedState.setReactionBtn(reactionBtn);
    
    longPressTimer = setTimeout(() => {
      isLongPress = true;
      showReactionMenu(e);
    }, 500); // ✅ Reduced from 800ms to 500ms
  });
  
  document.addEventListener('mouseup', (e) => {
    if (longPressTimer) {
      clearTimeout(longPressTimer);
      
      // ✅ If it wasn't a long press, trigger default like
      if (!isLongPress && longPressTarget) {
        const postId = longPressTarget.closest('[data-post-id]')?.dataset.postId;
        if (postId) {
          // Determine container type
          let containerType = 'smart-feed';
          const tagModal = document.getElementById('tag-posts-modal');
          if (tagModal && !tagModal.classList.contains('hidden')) {
            containerType = 'tag-modal';
          }
          
          toggleReactions(postId, 'like', null, containerType);
        }
      }
      
      longPressTimer = null;
      longPressTarget = null;
      isLongPress = false;
    }
  });
  
  // ✅ INCREASED THRESHOLD: Allow small movements during long press
  let startX = 0;
  let startY = 0;
  const MOVEMENT_THRESHOLD = 15; // pixels
  
  document.addEventListener('mousemove', (e) => {
    if (longPressTimer) {
      if (!startX && !startY) {
        startX = e.clientX;
        startY = e.clientY;
        return;
      }
      
      const deltaX = Math.abs(e.clientX - startX);
      const deltaY = Math.abs(e.clientY - startY);
      
      // ✅ Only cancel if moved more than threshold
      if (deltaX > MOVEMENT_THRESHOLD || deltaY > MOVEMENT_THRESHOLD) {
        clearTimeout(longPressTimer);
        longPressTimer = null;
        longPressTarget = null;
        isLongPress = false;
        startX = 0;
        startY = 0;
      }
    }
  });
  
  // ==================== TOUCH EVENTS ====================
  document.addEventListener('touchstart', (e) => {
    const reactionBtn = e.target.closest('[data-action="toggle-reactions"]');
    if (!reactionBtn) return;
    
    longPressTarget = reactionBtn;
    isLongPress = false;
    feedState.setReactionBtn(reactionBtn);
    
    const touch = e.touches[0];
    startX = touch.clientX;
    startY = touch.clientY;
    
    longPressTimer = setTimeout(() => {
      isLongPress = true;
      showReactionMenu(touch);
    }, 500);
  }, { passive: true });
  
  document.addEventListener('touchend', (e) => {
    if (longPressTimer) {
      clearTimeout(longPressTimer);
      
      // ✅ If it wasn't a long press, trigger default like
      if (!isLongPress && longPressTarget) {
        const postId = longPressTarget.closest('[data-post-id]')?.dataset.postId;
        if (postId) {
          let containerType = 'smart-feed';
          const tagModal = document.getElementById('tag-posts-modal');
          if (tagModal && !tagModal.classList.contains('hidden')) {
            containerType = 'tag-modal';
          }
          
          toggleReactions(postId, 'like', null, containerType);
        }
      }
      
      longPressTimer = null;
      longPressTarget = null;
      isLongPress = false;
      startX = 0;
      startY = 0;
    }
  }, { passive: true });
  
  document.addEventListener('touchmove', (e) => {
    if (longPressTimer) {
      const touch = e.touches[0];
      const deltaX = Math.abs(touch.clientX - startX);
      const deltaY = Math.abs(touch.clientY - startY);
      
      // ✅ Only cancel if moved more than threshold
      if (deltaX > MOVEMENT_THRESHOLD || deltaY > MOVEMENT_THRESHOLD) {
        clearTimeout(longPressTimer);
        longPressTimer = null;
        longPressTarget = null;
        isLongPress = false;
        startX = 0;
        startY = 0;
      }
    }
  }, { passive: true });
  
  console.log('✅ Reaction listeners setup complete');
}

export function setupViewTracking() {
  feedState.disconnectViewObserver();
  
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const postId = entry.target.dataset.postId;
        
        if (postId) {
          sendView(postId);
          observer.unobserve(entry.target);
        }
      }
    });
  }, {
    threshold: 0.4,
    rootMargin: '0px'
  });
  
  feedState.setViewObserver(observer);
  
  const activeFeed = document.querySelector('.posts-feed.active');
  if (activeFeed) {
    activeFeed.querySelectorAll(".post-card[data-post-id]").forEach(post => {
      observer.observe(post);
    });
  }
}

/**
 * Send view tracking
 */
async function sendView(postId) {
  try {
    await feedApi.trackPostView(postId);
  } catch (error) {
    console.debug("View tracking error:", postId, error);
  }
}

/**
 * Setup fork tags listeners
 */
function setupForkTagsListeners() {
  const forkTagsDropdown = document.getElementById("fork-tags-dropdown");
  const forkTagInput = document.getElementById("fork-tags-input");
  
  if (!forkTagInput || !forkTagsDropdown) {
    console.warn("Fork tags elements not found");
    return;
  }

  forkTagInput.addEventListener("input", function(e) {
    const input = e.target.value.toLowerCase();
    
    if (input.length === 0) {
      forkTagsDropdown.classList.add("hidden");
      return;
    }
    
    if (feedState.getForkTags().length >= MAX_TAGS) {
      forkTagsDropdown.classList.add('hidden');
      return;
    }
    
    const relatedTags = AVAILABLE_TAGS.filter(tag => 
      tag.toLowerCase().includes(input) && !feedState.getForkTags().includes(tag)
    );
    
    if (relatedTags.length > 0) {
      forkTagsDropdown.innerHTML = relatedTags.slice(0, 10).map(tag => 
        `<div class="tag-option" data-action="add-fork-tag" data-value="${tag}">${tag}</div>`
      ).join('');
      forkTagsDropdown.classList.remove('hidden');
    } else {
      forkTagsDropdown.classList.add('hidden');
    }
  });

  forkTagInput.addEventListener("keydown", function(e) {
    if (e.key === "Enter") {
      e.preventDefault();
      const value = e.target.value.trim();
      if (value && feedState.getForkTags().length < MAX_TAGS) {
        if (typeof window.addForkTag === 'function') {
          window.addForkTag(value);
        }
      }
    }
  });
}

function setupGlobalClickListeners() {
  document.addEventListener('click', function(e) {
    const reactionMenu = document.getElementById("reactionMenu");
    if (reactionMenu && 
        !reactionMenu.contains(e.target) && 
        !e.target.closest('.reaction-btn')) {
      hideReactionMenu();
    }
    
    if (!e.target.closest('.post-options-btn') && !e.target.closest('.advanced-post-options')) {
      document.querySelectorAll('.advanced-post-options').forEach(menu => {
        menu.classList.add('hidden');
      });
    }
    
    const advancedModal = document.getElementById("advanced-post-options-modal");
    if (advancedModal && e.target === advancedModal) {
      advancedModal.classList.add('hidden');
    }
  });
}

/**
 * ✅ FIXED: Handle follow post with container scoping
 */
export async function handleFollowPost(postId, event, containerType = 'smart-feed') {
  if (event) event.stopPropagation();
  
  try {
    const response = await feedApi.followPost(postId);
    
    if (response.status === "success") {
      const data = { post_id: postId };
      syncPostUI('follow', data, containerType);
    } else {
      showToast(response?.message || "Failed to follow post", 'error');
    }
  } catch (error) {
    showToast("Error: " + error.message, 'error');
  }
}

/**
 * ✅ FIXED: Handle unfollow post with container scoping
 */
export async function handleUnfollowPost(postId, event, containerType = 'smart-feed') {
  if (event) event.stopPropagation();
  
  try {
    const response = await feedApi.unfollowPost(postId);
    
    if (response.status === "success") {
      const data = { post_id: postId };
      syncPostUI('unfollow', data, containerType);
    } else {
      showToast(response?.message || "Failed to unfollow post", 'error');
    }
  } catch (error) {
    showToast("Error: " + error.message, 'error');
  }
}

/**
 * ✅ FIXED: Handle delete post with container scoping
 */
export async function handleDeletePost(postId, event, containerType = 'smart-feed') {
  if (event) event.stopPropagation();
  
  if (!confirm('Are you sure you want to delete this post?')) {
    return;
  }
  
  try {
    const response = await feedApi.deletePost(postId);
    
    if (response.status === "success") {
      const data = { post_id: postId };
      syncPostUI('delete-post', data, containerType);
    } else {
      showToast(response?.message || "Failed to delete post", 'error');
    }
  } catch (error) {
    showToast("Error deleting post: " + error.message, 'error');
  }
}

/**
 * Handle delete comment
 */
export async function handleDeleteComment(commentId, event) {
  if (event) event.stopPropagation();
  
  if (!confirm('Are you sure you want to delete this comment?')) {
    return;
  }
  
  try {
    const response = await feedApi.deleteComment(commentId);
    
    if (response.status === "success") {
      removeCommentFromDOM(commentId);
    } else {
      showToast(response?.message || "Failed to delete comment", 'error');
    }
  } catch (error) {
    showToast("Error deleting comment: " + error.message, 'error');
  }
}

/**
 * Handle open report modal
 */
export function handleOpenReportModal(postId, event) {
  if (event) event.stopPropagation();
  
  const reportModal = document.getElementById("post-report-modal");
  if (!reportModal) {
    return;
  }
  
  reportModal.classList.remove("hidden");
  reportModal.classList.add("active");
  reportModal.dataset.postId = postId;
}

/**
 * Handle report post
 */
export async function handleReportPost(event) {
  event.preventDefault();
  event.stopPropagation();
  
  const reportModal = document.getElementById("post-report-modal");
  const postId = reportModal?.dataset.postId;
  
  if (!postId) {
    return;
  }
  
  
  const reasonInput = reportModal.querySelector('input[name="reason"]:checked');
  
  if (!reasonInput) {
    showToast("Please select a reason", "warning");
    return;
  }
  
  const reason = reasonInput.value;
  
  try {
    const response = await feedApi.reportPost(postId, reason);
    
    if (response.status === "success") {
      showToast("Post reported. Our admin will review and take action", 'info');
      closeModal("post-report-modal");
      delete reportModal.dataset.postId;
    } else {
      showToast(response.message || "Failed to report post", 'error');
    }
  } catch (error) {
    showToast("Report post error: " + error.message, 'error');
  }
}

/**
 * Handle toggle comment like
 */
export async function handleToggleCommentLike(commentId, event) {
  if (event) event.stopPropagation();
  
  try {
    const response = await feedApi.toggleCommentLike(commentId);
    
    if (response?.status === 'success') {
      const liked = response.data.liked;
      const count = response.data.count;
      
      updateCommentLikeButton(commentId, liked, count);
    } else {
      showToast(response?.message || "Failed to like comment", 'error');
    }
  } catch (error) {
    showToast('Error: ' + error.message, 'error');
  }
}

/**
 * Handle toggle comment helpful
 * Fixed: in-flight guard prevents 3rd-click race condition
 */
export async function handleToggleCommentHelpful(commentId, event) {
  if (event) event.stopPropagation();

  // ── In-flight guard ──────────────────────────────────────────────────────
  // Using a data attribute on the button so rapid clicks are ignored
  const commentCard = document.querySelector(`[data-comment-id="${commentId}"]`);
  if (!commentCard) return;
  const helpfulBtn = commentCard.querySelector('[data-action="toggle-comment-helpful"]');
  if (!helpfulBtn) return;

  if (helpfulBtn.dataset.inflight === 'true') return;  // already processing
  helpfulBtn.dataset.inflight = 'true';
  helpfulBtn.style.opacity = '0.6';

  try {
    const response = await feedApi.toggleCommentHelpful(commentId);

    if (response && response.status === "success") {
      const count = response.data?.count ?? 0;
      // Explicitly use === true so we're never fooled by a string "false"
      const is_helpful = response.data?.is_helpful === true;
      updateCommentHelpfulButton(commentId, is_helpful, count);
    } else {
      showToast(response?.message || "Failed to mark helpful", "error");
    }
  } catch (error) {
    console.error("Error toggling helpful:", error);
    showToast("Error: " + error.message, "error");
  } finally {
    // Always release the guard so the button works again
    helpfulBtn.dataset.inflight = 'false';
    helpfulBtn.style.opacity = '';
  }
}

/**
 * Handle view thread
 */
export async function handleViewThread(threadId, type, event) {
  if (event) event.stopPropagation();
  
  try {
    const result = await feedApi.getThreadDetails(threadId, type);
    
    const threadModal = document.getElementById("thread-view-modal");
    if (!threadModal) {
      return;
    }
        threadModal.classList.remove('hidden');
    threadModal.classList.add('active');
    document.body.style.overflow = 'hidden';
    
    threadModal.dataset.threadId = threadId;
    threadModal.dataset.type = type;
    
    const contentEl = threadModal.querySelector("#thread-details-content");
    if (!contentEl) {
      return;
    }
    
    if (!result || !result.thread) {
      contentEl.innerHTML = `<div class="empty-state">
        <h1>No data found for this thread</h1>
      </div>`;
      return;
    }
    
    contentEl.innerHTML = renderThreadDetailsHTML(result.thread);
  } catch (error) {
    console.error("View thread error:", error);
    showToast("Error loading thread: " + error.message, "error");
  }
}

export async function handleJoinThread(event) {
  if (event) event.stopPropagation();
  
  const threadModal = document.getElementById("thread-view-modal");
  const btn = document.getElementById("join-thread-btn");
  
  if (!btn || !threadModal) return;
  
  const threadId = threadModal.dataset.threadId;
  const type = threadModal.dataset.type;
  
  if (!threadId) {
    return;
  }
  
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Joining...";
  
  try {
    const response = await feedApi.joinThread(threadId, type);
    
    if (response && response.status === "success") {
      btn.textContent = "✓ Joined";
      btn.classList.add("success");
      showToast("Successfully joined thread!", "success");
      
      setTimeout(() => {
        btn.style.display = "none";
        delete threadModal.dataset.threadId;
        delete threadModal.dataset.type;
        closeModal('thread-view-modal');
      }, 2000);
    } else {
      btn.disabled = false;
      btn.textContent = originalText;
      showToast(response?.message || "Failed to join", 'error');
    }
  } catch (error) {
    console.error("Join thread error:", error);
    showToast("Join thread error: " + error.message, 'error');
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

/**
 * Handle connect request
 */
export async function handleConnectRequest(userId, event) {
  if (event) event.stopPropagation();
  
  try {
    const response = await feedApi.sendConnectionRequest(userId);
    
    if (response && response.status === 'success') {
      showToast('Connection request sent!', 'success');
      
      const btn = event.target;
      if (btn) {
        btn.textContent = "Pending";
        btn.disabled = true;
      }
    } else {
      showToast(response?.message || 'Failed to send request', 'error');
    }
  } catch (error) {
    showToast('Error sending request: ' + error.message, 'error');
  }
}

/**
 * ✅ FIXED: Handle mark post as solved with container scoping
 */
export async function handleMarkSolved(postId, event, containerType = 'smart-feed') {
  if (event) event.stopPropagation();
  
  const btn = event.target;
  const originalText = btn.textContent;
  btn.textContent = "Marking...";
  btn.disabled = true;
  
  try {
    const response = await feedApi.markPostSolved(postId);
    
    if (response && response.status === "success") {
      btn.textContent = "✓ Marked Solved";
      btn.classList.add("success");
      
      setTimeout(() => {
        btn.dataset.action = 'unmark-solved';
        btn.textContent = "❌ Mark Unsolved";
        btn.disabled = false;
        btn.classList.remove("success");
      }, 2000);
      
      const data = { post_id: postId };
      syncPostUI('mark-solved', data, containerType);
    } else {
      btn.disabled = false;
      btn.textContent = originalText;
      showToast(response?.message || "Failed to mark as solved", 'error');
    }
  } catch (error) {
    console.error("Mark solved error:", error);
    showToast(error.message, 'error');
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

/**
 * ✅ FIXED: Handle unmark post as solved with container scoping
 */
export async function handleUnmarkSolved(postId, event, containerType = 'smart-feed') {
  if (event) event.stopPropagation();
  
  const btn = event.target;
  const originalText = btn.textContent;
  btn.textContent = "Unmarking...";
  btn.disabled = true;
  
  try {
    const response = await feedApi.markPostUnsolved(postId);
    
    if (response && response.status === "success") {
      btn.textContent = "✓ Unmarked Solved";
      btn.classList.add("success");
      
      setTimeout(() => {
        btn.dataset.action = 'mark-solved';
        btn.textContent = "Mark Solved";
        btn.disabled = false;
        btn.classList.remove("success");
      }, 2000);
      
      const data = { post_id: postId };
      syncPostUI('unmark-solved', data, containerType);
    } else {
      btn.disabled = false;
      btn.textContent = originalText;
      showToast(response?.message || "Failed to mark unsolved", 'error');
    }
  } catch (error) {
    console.error("Unmark solved error:", error);
    showToast(error.message, 'error');
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

/**
 * ✅ REWRITTEN: Toggle like/reaction on a post
 * Works with the new SVG heart button — never touches innerHTML.
 * Just toggles the `.reacted` class and updates the `.reaction-count` span.
 */
 
export async function toggleReactions(postId, newType = 'like', event, containerType = 'smart-feed') {
  if (event) event.stopPropagation();

  // ── Locate the button ──────────────────────────────────────────────────
  let container;
  if (containerType === 'tag-modal') {
    container = document.getElementById('tag-posts-container');
  }
  else if(containerType === "profile"){
    container = document.getElementById("profile");
  
  } else if (containerType === 'bookmarks') {
    container = document.getElementById('bookmarks');
  } else {
    container = document.getElementById('posts-container') || document.body;
  }

  const postCard = container
    ? container.querySelector(`[data-post-id="${postId}"]`)
    : document.getElementById(`post-${postId}`);

  // Fallback — try the standard id
  const card = postCard || document.getElementById(`post-${postId}`);
  if (!card) { console.warn(`Post card ${postId} not found`); return; }

  const reactionBtn = card.querySelector('.reaction-btn');
  if (!reactionBtn) return;

  // ── In-flight guard ────────────────────────────────────────────────────
  if (reactionBtn.dataset.inflight === 'true') return;
  reactionBtn.dataset.inflight = 'true';

  // ── Read current state from DOM ────────────────────────────────────────
  const wasReacted = reactionBtn.classList.contains('reacted');
  const countSpan  = reactionBtn.querySelector('.reaction-count');
  const prevCount  = parseInt(countSpan?.textContent || '0', 10) || 0;

  // ── Optimistic update ──────────────────────────────────────────────────
  if (wasReacted) {
    reactionBtn.classList.remove('reacted');
    if (countSpan) countSpan.textContent = prevCount > 1 ? prevCount - 1 : '';
  } else {
    reactionBtn.classList.add('reacted');
    if (countSpan) countSpan.textContent = prevCount + 1;
  }

  try {
    const response = await feedApi.toggleReactions(postId, newType);

    if (!response || response.status !== 'success') {
      throw new Error(response?.message || 'Request failed');
    }

    const backendCount   = response.data?.count   ?? 0;
    const backendReacted = typeof response.data?.reacted === 'boolean'
    ? response.data.reacted
    : !wasReacted; // trust the optimistic state if API omits the field

    // Settle to backend truth
    reactionBtn.classList.toggle('reacted', backendReacted);
    if (countSpan) {
      countSpan.textContent = backendCount > 0 ? backendCount : '';
    }

    // Sync the other feed container (tag-modal ↔ main feed)
    syncPostUI('reaction', {
      post_id: postId,
      reacted: backendReacted,
      count:   backendCount,
      emoji:   '❤️'
    }, containerType);

  } catch (error) {
    // Roll back optimistic update
    reactionBtn.classList.toggle('reacted', wasReacted);
    if (countSpan) countSpan.textContent = prevCount > 0 ? prevCount : '';

    console.error('Reaction error:', error);
    showToast('Failed to react: ' + error.message, 'error');
  } finally {
    reactionBtn.dataset.inflight = 'false';
  }
}

/**
 * Toggle bookmark on a post card. Works in every container (feed, tag modal,
 * profile, bookmarks section) because it locates the button from the click
 * target, not from a container id.
 */
export async function toggleBookmark(postId, btn, containerType = 'smart-feed') {
  if (!btn || btn.dataset.inflight === 'true') return;
  btn.dataset.inflight = 'true';

  const was = btn.classList.contains('bookmarked');
  applyBookmarkUI(postId, !was);                       // optimistic, all copies

  try {
    const res = await feedApi.setBookmark(postId, !was);
    if (!res || res.status !== 'success') throw new Error(res?.message || 'Request failed');

    const now = !!res.data?.bookmarked;                // settle to server truth
    applyBookmarkUI(postId, now);
    feedState.patchPostEverywhere(postId, {
      user_interactions: { bookmarked: now },
      bookmark_count: res.data?.bookmark_count,
    });
    // Let the Bookmarks section add/remove the row (decoupled via an event).
    document.dispatchEvent(new CustomEvent('bookmark:changed', {
      detail: { postId: Number(postId), bookmarked: now, count: res.data?.bookmark_count },
    }));
    showToast(now ? 'Saved to bookmarks' : 'Removed from bookmarks', 'success', 1800);
  } catch (error) {
    applyBookmarkUI(postId, was);                      // rollback
    console.error('Bookmark error:', error);
    showToast('Could not update bookmark', 'error');
  } finally {
    btn.dataset.inflight = 'false';
  }
}

/** Update EVERY rendered copy of this post (feed, tag modal, profile, bookmarks). */
export function applyBookmarkUI(postId, bookmarked) {
  document.querySelectorAll(`.post-card[data-post-id="${postId}"] .bookmark-btn`).forEach((b) => {
    b.classList.toggle('bookmarked', bookmarked);
    b.setAttribute('aria-pressed', String(bookmarked));
    b.setAttribute('aria-label', bookmarked ? 'Remove bookmark' : 'Bookmark post');
    const label = b.querySelector('.bookmark-label');
    if (label) label.textContent = bookmarked ? 'Saved' : 'Save';
  });
}

/**
 * Filter feed by type
 */
export async function filterFeed(filterType) {
  updateFilterButtons(filterType);
  updateFeedContainerVisibility(filterType);
  
  if (!feedState.isFilterLoaded(filterType)) {
    const { posts } = await feedApi.loadPostsByFilter(filterType);
    feedState.setPosts(filterType, posts);
  }
  
  feedState.setCurrentFilter(filterType);
  await renderFeed(filterType);
  
  setupAllDelegation();
}

export async function handleMarkSolution(postId, commentId, event) {
  if (event) event.stopPropagation();
  
  const commentCard = document.querySelector(`[data-comment-id="${commentId}"]`);
  if (!commentCard) return;
  
  const btn = commentCard.querySelector('[data-action="mark-solution"]');
  if (!btn) return;
  
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Marking...";

  try {
    // Dynamic import to avoid circular dependency
    const feedApi = await import('./feed.api.js');
    const res = await feedApi.markCommentAsSolution(postId, commentId);
    
    if (!res || res.status !== "success") {
      showToast(res?.message || 'Failed to mark solution', 'error');
      btn.disabled = false;
      btn.textContent = originalText;
      return;
    }

    // Remove other solution badges
    document.querySelectorAll(`.comment-card[data-post-id="${postId}"] .solution-badge`)
      .forEach(badge => badge.remove());

    // Update all solution buttons
    const cards = document.querySelectorAll(`.comment-card[data-post-id="${postId}"]`);
    cards.forEach(card => {
      const solutionBtn = card.querySelector('[data-action="mark-solution"], [data-action="unmark-solution"]');
      if (solutionBtn) {
        solutionBtn.textContent = "Mark Solution";
        solutionBtn.dataset.action = "mark-solution";
        solutionBtn.disabled = false;
      }
    });

    // Add solution badge to this comment
    const header = commentCard.querySelector(".comment-header");
    if (header) {
      const badge = document.createElement("span");
      badge.className = "solution-badge";
      badge.textContent = "✓ Solution";
      badge.style.cssText = 'padding: 0.25rem 0.5rem; background: var(--success); color: white; border-radius: 4px; font-size: 0.75rem;';
      header.appendChild(badge);
    }

    // Update this button to unmark
    btn.textContent = "Unmark Solution";
    btn.dataset.action = "unmark-solution";
    btn.disabled = false;
    
    // Sync post UI
    const data = {post_id: postId};
    syncPostUI('mark-solved', data, 'smart-feed');

  } catch (error) {
    console.error(error);
    if (typeof showToast === 'function') {
      showToast(error.message || "Failed to mark solution", "error");
    }
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

export async function handleUnmarkSolution(postId, commentId, event) {
  if (event) event.stopPropagation();
  
  const commentCard = document.querySelector(`[data-comment-id="${commentId}"]`);
  if (!commentCard) return;
  
  const btn = commentCard.querySelector('[data-action="unmark-solution"]');
  if (!btn) return;
  
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Unmarking...";

  try {
    const feedApi = await import('./feed.api.js');
    const res = await feedApi.unmarkCommentAsSolution(postId, commentId);
    
    if (!res || res.status !== "success") {
      showToast(res?.message || 'Failed to unmark solution', 'error');
      btn.disabled = false;
      btn.textContent = originalText;
      return;
    }

    // Remove solution badge
    const header = commentCard.querySelector(".comment-header");
    header?.querySelector(".solution-badge")?.remove();

    // Update all solution buttons
    const cards = document.querySelectorAll(`.comment-card[data-post-id="${postId}"]`);
    cards.forEach(card => {
      const solutionBtn = card.querySelector('[data-action="mark-solution"], [data-action="unmark-solution"]');
      if (solutionBtn) {
        solutionBtn.textContent = "Mark Solution";
        solutionBtn.dataset.action = "mark-solution";
        solutionBtn.disabled = false;
      }
    });
    
    const data = {post_id: postId};
    syncPostUI('unmark-solved', data, 'smart-feed');

  } catch (error) {
    console.error(error);
    if (typeof showToast === 'function') {
      showToast(error.message || "Failed to unmark solution", "error");
    }
    btn.disabled = false;
    btn.textContent = originalText;
  }
}


// Global exports for backward compatibility
if (typeof window !== 'undefined') {
  window.filterFeed = filterFeed;
}
if (typeof window !== 'undefined') {
  window.toggleReactions = toggleReactions;
}
/*
export {
  handleFollowPost,
  handleUnfollowPost,
  handleDeletePost,
  handleMarkSolved,
  handleUnmarkSolved,
  handleListenPost,
  handleToggleCommentLike,
  handleToggleCommentHelpful,
  handleToggleCommentSettings,
  handleDeleteComment,
  handleMarkSolution,
  handleUnmarkSolution,
  handleViewThread,
  handleJoinThread,
  handleConnectRequest,
  handleScrollPostResource,
  showCommentResource,
  showCommentResources,
  showCommentResourceLinks,
  showPostResourceLinks,
  handleViewTagPosts,
  handleCreateThreadFromPost,
  handleOpenReportModal,
  handleReportPost,
  toggleReactions
  // ... all other existing exports
};
*/
window.askLearnora             = askLearnora;
window.handleCreateLearnoraChat = handleCreateLearnoraChat;