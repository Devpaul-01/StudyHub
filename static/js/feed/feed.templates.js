/**
 * ============================================================================
 * FEED TEMPLATES - PRODUCTION READY
 * FIXED: Horizontal snap scroll for resources, one visible at a time
 * ============================================================================
 */
import { POST_TYPE_ICONS, REACTION_TYPES, CAN_SOLVE_TYPES, MAX_DISPLAY_RESOURCES, MAX_COMMENT_PREVIEW_RESOURCES } from './feed.constants.js';
import { formatTime } from './feed.utils.js';

/**
 * Get post type icon
 */
function getPostTypeIcon(type) {
  return POST_TYPE_ICONS[type] || POST_TYPE_ICONS.discussion;
}

/**
 * Get reaction emoji
 */
export function getReactionType(type) {
  return REACTION_TYPES[type] || REACTION_TYPES.like;
}

/**
 * ✅ COMPLETELY REWRITTEN: Build post resources with horizontal snap scroll
 */
/**
 * ============================================================================
 * INSTAGRAM-STYLE RESOURCE RENDERING SYSTEM
 * Complete implementation for posts, comments, and viewer
 * ============================================================================
 */

// ========== 1. POST RESOURCES (Feed Card) ==========
/**
 * ✅ NEW: Instagram-style horizontal snap scroll for post resources
 */
/**
 * Returns icon SVG + accent colour for a given file extension.
 * Used by the document card inside buildPostResourcesContainer.
 */
function _getDocMeta(ext) {
  switch (ext) {
    case 'pdf':
      return {
        bg: 'rgba(239,68,68,0.12)',
        color: '#ef4444',
        svg: `<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                <polyline points="14 2 14 8 20 8"/>
                <line x1="9" y1="13" x2="15" y2="13"/>
                <line x1="9" y1="17" x2="15" y2="17"/>
                <line x1="9" y1="9" x2="11" y2="9"/>
              </svg>`
      };
    case 'doc': case 'docx':
      return {
        bg: 'rgba(59,130,246,0.12)',
        color: '#3b82f6',
        svg: `<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#3b82f6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                <polyline points="14 2 14 8 20 8"/>
                <line x1="8" y1="13" x2="16" y2="13"/>
                <line x1="8" y1="17" x2="16" y2="17"/>
              </svg>`
      };
    case 'xls': case 'xlsx': case 'csv':
      return {
        bg: 'rgba(16,185,129,0.12)',
        color: '#10b981',
        svg: `<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                <polyline points="14 2 14 8 20 8"/>
                <rect x="8" y="12" width="3" height="5"/>
                <rect x="13" y="10" width="3" height="7"/>
              </svg>`
      };
    case 'ppt': case 'pptx':
      return {
        bg: 'rgba(245,158,11,0.12)',
        color: '#f59e0b',
        svg: `<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                <polyline points="14 2 14 8 20 8"/>
                <circle cx="11" cy="14" r="3"/>
              </svg>`
      };
    case 'zip': case 'rar': case '7z':
      return {
        bg: 'rgba(139,92,246,0.12)',
        color: '#8b5cf6',
        svg: `<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#8b5cf6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
                <line x1="12" y1="8" x2="12" y2="8.01"/>
                <line x1="12" y1="12" x2="12" y2="16"/>
              </svg>`
      };
    default:
      return {
        bg: 'rgba(100,116,139,0.12)',
        color: '#64748b',
        svg: `<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#64748b" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                <polyline points="14 2 14 8 20 8"/>
              </svg>`
      };
  }
}

/**
 * ============================================================================
 * PATCHED: buildPostResourcesContainer
 * ============================================================================
 * Drop-in replacement for the existing function in feed_templates.js.
 *
 * CHANGES vs original:
 *   • Image + video .post-resource elements now carry:
 *       data-action="view-post-resource"
 *       data-index="<N>"
 *       data-resources="<JSON>"   ← full array, so the viewer can navigate
 *   • Cursor is set to pointer on clickable items
 *   • Documents are unchanged (they open/download inline as before)
 *
 * NOTHING else in the function changed — carousel markup, dots, nav buttons,
 * document cards are all identical to the original.
 * ============================================================================
 */

export function buildPostResourcesContainer(resources, postId) {
  if (!resources || resources.length === 0) return '';

  const length = resources.length;

  // Pre-serialise once; each item gets the full array so the viewer knows
  // how many items there are and can navigate between them.
  const resourcesJSON = JSON.stringify(resources).replace(/'/g, '&#39;');

  const resourceItems = resources.map((resource, index) => {

    // ── IMAGE ────────────────────────────────────────────────────────────────
    if (resource.type === 'image') {
      return `
        <div class="post-resource"
             data-type="image"
             data-index="${index}"
            
             data-resources='${resourcesJSON}'
             style="cursor:pointer;"
             role="button"
             aria-label="View image ${index + 1} of ${length}">
          <img src="${resource.url}"
               alt="${resource.filename || 'Image'}"
               class="post-resource-media"
               loading="lazy"
               draggable="false">
        </div>
      `;
    }

    // ── VIDEO ────────────────────────────────────────────────────────────────
    if (resource.type === 'video') {
      return `
        <div class="post-resource post-resource--video"
             data-type="video"
             data-index="${index}"
             data-action="view-post-resourc"
             data-resources='${resourcesJSON}'
             style="cursor:pointer;"
             role="button"
             aria-label="View video ${index + 1} of ${length}">
          <video class="post-resource-media post-resource-media--video"
                 src="${resource.url}"
                 ${resource.thumbnail ? `poster="${resource.thumbnail}"` : ''}
                 playsinline
                 preload="metadata">
          </video>
          <div class="video-play-overlay" aria-hidden="true">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="white">
              <path d="M8 5v14l11-7z"/>
            </svg>
          </div>
        </div>
      `;
      // NOTE: controls + onplay/onpause removed intentionally — the video is
      // now a click-target that opens the fullscreen viewer. Playback happens
      // inside the viewer where it has the full stage.
    }

    // ── DOCUMENT (unchanged) ─────────────────────────────────────────────────
    const ext = (resource.filename || '').split('.').pop().toLowerCase();
    const doc = _getDocMeta(ext);   // ← your existing helper in feed_templates.js
    return `
      <div class="post-resource document-resource" data-type="document" data-index="${index}">
        <div class="document-preview">
          <div class="document-icon" style="background:${doc.bg};">
            ${doc.svg}
          </div>
          <span class="document-name">${resource.filename || 'Document'}</span>
          ${ext ? `<span class="document-ext-badge" style="color:${doc.color};border-color:${doc.color};">${ext.toUpperCase()}</span>` : ''}
          
          
        </div>
      </div>
    `;

  }).join('');

  return `
    <div class="post-resources-carousel"
         data-post-id="${postId}"
         data-current-index="0"
         data-total="${length}">

      <div class="resources-scroll-container" data-post-id="${postId}">
        ${resourceItems}
      </div>

      ${length > 1 ? `
        <div class="carousel-dots">
          ${Array.from({length}, (_, i) => `
            <span class="carousel-dot ${i === 0 ? 'active' : ''}" data-index="${i}"></span>
          `).join('')}
        </div>
        <div class="carousel-indicator">1/${length}</div>
        <button class="carousel-nav carousel-prev"
                data-action="scroll-post-resource"
                data-direction="left"
                disabled>‹</button>
        <button class="carousel-nav carousel-next"
                data-action="scroll-post-resource"
                data-direction="right">›</button>
      ` : ''}
    </div>
  `;
}

/* ─── HOW TO WIRE INTO YOUR DELEGATED EVENT HANDLER ───────────────────────────

In whichever file handles your data-action clicks (e.g. feed.events.js), add:

  import { openResourceViewer, initResourceViewer } from './post_resource_viewer.js';

  // In your DOMContentLoaded / init:
  initResourceViewer();

  // In your delegated click handler switch/if block:
  case 'view-post-resource': {
    const trigger = e.target.closest('[data-action="view-post-resource"]');
    if (trigger) openResourceViewer(trigger);
    break;
  }

That's it. No other changes needed.
─────────────────────────────────────────────────────────────────────────────── */


export function buildCommentResourceHTML(resources, commentId, postId) {
  if (!resources || resources.length === 0) return '';

  const resourcesHTML = resources.map((resource, index) => {
    if (resource.type === "image") {
      return `
        <div class="comment-resource media-resource" 
             data-type="image"
             data-resources='${JSON.stringify(resources).replace(/"/g, '&quot;')}'
             data-action="view-comment-resource"
             data-url="${resource.url}"
             data-resource-type="image"
             data-comment-id="${commentId}"
             data-index="${index}">
          <img src="${resource.url}" 
               alt="${resource.filename || 'Image'}"
               class="comment-resource-img"
               loading="lazy">
        </div>
      `;
    } else if (resource.type === "video") {
      return `
        <div class="comment-resource media-resource" 
             data-type="video"
             data-action="view-comment-resource"
             data-url="${resource.url}"
             data-resources='${JSON.stringify(resources).replace(/"/g, '&quot;')}'
             data-resource-type="video"
             data-comment-id="${commentId}"
             data-index="${index}">
          <video src="${resource.url}" 
                 class="comment-resource-video"
                 playsinline
                 preload="metadata">
          </video>
          <div class="comment-video-overlay">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="white">
              <path d="M8 5v14l11-7z"/>
            </svg>
          </div>
        </div>
      `;
    } else {
      return `
        <div class="comment-resource document-resource" data-type="document">
          <div class="comment-document-preview">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
              <polyline points="14 2 14 8 20 8"></polyline>
            </svg>
            <span class="comment-document-name">${resource.filename || 'Document'}</span>
            <button class="comment-download-btn" 
                    data-action="download-resource"
                    data-resources='${JSON.stringify(resources).replace(/"/g, '&quot;')}'
                    data-url="${resource.url}"
                    data-filename="${resource.filename}">
              Download
            </button>
          </div>
        </div>
      `;
    }
  }).join('');

  return `
    <div class="comment-resources-carousel" data-comment-id="${commentId}">
      <div class="comment-resources-scroll">
        ${resourcesHTML}
      </div>
    </div>
  `;
}



export function buildResourceLinks(resources) {
  if (!resources || resources.length === 0) return '';
  
  const linkHTML = resources.map(resource => `
    <div class='resource-link-container' data-url='${resource.url}' style="padding: 0.75rem; background: var(--bg-secondary); border-radius: 8px; display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.5rem;">
      <span class="resource-link-name" style="flex: 1;">${resource.filename || 'Resource'}</span>
      <button class="download-btn" 
              data-action='download-resource' 
              data-url='${resource.url}' 
              data-filename='${resource.filename}' 
              aria-label="Download"
              style="padding: 0.5rem; background: var(--primary); color: white; border: none; border-radius: 4px; cursor: pointer;">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path d="M12 3V14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          <path d="M7 10L12 15L17 10" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          <path d="M5 21H19" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
      </button>
    </div>
  `).join("");
  return linkHTML;
}

/**
 * Build comments preview HTML (for feed)
 */
export function buildCommentsPreviewHTML(comments, postId) {
  if (!comments || comments.length === 0) return '';
  
  const commentCards = comments.map(comment => `
    <div class="comment-preview" 
         data-action="open-comments"
         data-post-id="${postId}"
         style="padding: 0.75rem; background: var(--bg-secondary); border-radius: 8px; margin-top: 0.5rem; cursor: pointer;">
      <div style="display: flex; gap: 0.75rem;">
        <img src="${comment.avatar || '/static/default-avatar.png'}" 
             alt="${comment.name}" 
             class="comment-avatar" 
             onerror="this.src='/static/default-avatar.png'"
             style="width: 32px; height: 32px; border-radius: 50%;">
        <div style="flex: 1;">
          <div class="comment-preview-author" style="font-weight: 500; font-size: 0.875rem;">${comment.name || 'Anonymous'}</div>
          <div class="comment-preview-text" style="font-size: 0.875rem; color: var(--text-secondary); margin-top: 0.25rem;">${comment.text_content}</div>
        </div>
        <div class="comment-preview-stats" style="display: flex; gap: 0.5rem; align-items: flex-start;">
          ${comment.likes_count > 0 ? `<span style="font-size: 0.75rem;">👍 ${comment.likes_count}</span>` : ''}
          ${comment.is_solution ? '<span class="solution-indicator" style="font-size: 0.75rem; color: var(--success);">✓</span>' : ''}
        </div>
      </div>
    </div>
  `).join('');
  
  return `<div class="comments-preview-container">${commentCards}</div>`;
}

export function createRepliesCard(replies, parentId, context = 'modal') {
  if (!replies || replies.length === 0) return '';

  const replyCards = replies.map(comment => {
    const author = comment.author || {};
    // ✅ FIX: Use proper unique ID for replies (different from parent comments)
    const uniqueId = `comment-card-${context}-${comment.id}`;
    const resourcesHTML = buildCommentResourceHTML(
      comment.resources || [],
      comment.id,
      comment.post_id
    );

    return `
      <div 
        data-resources='${JSON.stringify(comment.resources || {}).replace(/'/g, "&apos;")}'
        data-post-id="${comment.post_id}"
        data-comment-id="${comment.id}"
        data-depth="${comment.depth_level}"
        class="comment-card reply-comment"
        data-parent-id="${parentId}"
        id="${uniqueId}"
        style="margin-left: 2rem; margin-top: 0.75rem; padding: 0.75rem; background: var(--bg-secondary); border-radius: 8px; border-left: 2px solid var(--primary);"
      >
        <div class="comment-header" style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.5rem;">
          <img 
            src="${author.avatar || '/static/default-avatar.png'}"
            data-action="view-profile"
            data-username="${author.username || ''}"
            alt="${author.name || 'User'}"
            class="avatar"
            onerror="this.src='/static/default-avatar.png'"
            style="width: 32px; height: 32px; border-radius: 50%; cursor: pointer;"
          >

          <div class="comment-author" style="flex: 1;">
            <div 
              data-action="view-profile"
              data-username="${author.username || ''}"
              class="comment-author-name"
              style="font-weight: 500; font-size: 0.875rem; cursor: pointer;"
            >
              ${author.name || 'Anonymous'}
            </div>
            <div class="comment-time" style="font-size: 0.75rem; color: var(--text-secondary);">${formatTime(comment.posted_at)}</div>
          </div>

          ${comment.is_solution ? '<span class="solution-badge" style="padding: 0.25rem 0.5rem; background: var(--success); color: white; border-radius: 4px; font-size: 0.75rem;">✓ Solution</span>' : ''}
        </div>

        <div class="comment-content" style="font-size: 0.9rem; line-height: 1.5;">${comment.text_content}</div>

        ${resourcesHTML}

        <div class="comment-actions" style="display: flex; gap: 0.5rem; margin-top: 0.5rem; flex-wrap: wrap;">

          <!-- Heart like button (matches createCommentCard) -->
          <button
            class="comment-action-btn ${comment.user_interactions?.has_liked ? 'liked' : ''}"
            data-action="toggle-comment-like"
            data-comment-id="${comment.id}"
            aria-label="Like reply">
            <span class="action-icon">
              <svg class="heart-outline" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
              </svg>
              <svg class="heart-fill" width="16" height="16" viewBox="0 0 24 24" fill="currentColor" stroke="none">
                <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
              </svg>
            </span>
            <span>${comment.likes_count > 0 ? comment.likes_count : 'Like'}</span>
          </button>

          <!-- Helpful bulb button (matches createCommentCard) -->
          <button
            class="comment-action-btn ${comment.user_interactions?.has_marked_helpful ? 'helpful' : ''}"
            data-action="toggle-comment-helpful"
            data-comment-id="${comment.id}"
            aria-label="Mark reply helpful">
            <span class="action-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M9 18h6"/>
                <path d="M10 22h4"/>
                <path d="M15.09 14c.18-.98.65-1.74 1.41-2.5A4.65 4.65 0 0 0 18 8 6 6 0 0 0 6 8c0 1 .23 2.23 1.5 3.5A4.61 4.61 0 0 1 8.91 14"/>
              </svg>
            </span>
            <span>${comment.helpful_count > 0 ? comment.helpful_count : 'Helpful'}</span>
          </button>

          ${
            !comment.is_you &&
            comment.user_interactions?.is_author &&
            !comment.post_is_solved &&
            !comment.is_solution
              ? `
            <button
              class="comment-action-btn solution"
              data-action="mark-solution"
              data-comment-id="${comment.id}"
              data-post-id="${comment.post_id}"
              aria-label="Mark as solution">
              <span class="action-icon">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/>
                  <polyline points="22 4 12 14.01 9 11.01"/>
                </svg>
              </span>
              <span>Mark Solution</span>
            </button>`
              : ''
          }

          ${
            comment.is_you
              ? `
            <button
              class="comment-action-btn"
              data-action="toggle-comment-settings"
              data-comment-id="${comment.id}"
              aria-label="Comment settings">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="1"/>
                <circle cx="12" cy="5" r="1"/>
                <circle cx="12" cy="19" r="1"/>
              </svg>
            </button>`
              : ''
          }
        </div>

        ${
          comment.is_you
            ? `
          <div class="advanced-comment-options hidden">
            <button
              class="comment-option-item danger"
              data-action="delete-comment"
              data-comment-id="${comment.id}">
              <div class="comment-option-icon">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <polyline points="3 6 5 6 21 6"/>
                  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                </svg>
              </div>
              <div class="comment-option-content">
                <div class="comment-option-title">Delete Reply</div>
                <div class="comment-option-description">Permanently remove</div>
              </div>
            </button>
          </div>`
            : ''
        }
      </div>
    `;
  }).join('');

  return replyCards 
  ? `<div class="replies-container" style="display:flex;flex-direction:column;width:100%;margin-top:0.5rem;">${replyCards}</div>`
  : '';
}

/**
 * ✅ NEW: Build dynamic options menu from fresh data
 * Uses EXACT same styling as original buildPostOptionsMenu
 */
export function buildDynamicOptionsMenu(data) {
  const { post_id, is_author, post_type, is_solved, interactions, author, thread, permissions } = data;
  
  const canSolve = permissions.can_solve;
  
  return `
    <div class="post-options-grid" style="display: flex; flex-direction: column; gap: 0;">
      
      <!-- Report Post -->
      <div 
        class="post-option-item" 
        data-post-id="${post_id}" 
        data-action="report-post"
        style="display: flex; align-items: center; gap: 1rem; padding: 1rem; cursor: pointer; transition: background 0.2s; border-bottom: 1px solid var(--border);"
        onmouseover="this.style.background='var(--bg-secondary)'" 
        onmouseout="this.style.background='transparent'">
        <div class="option-icon" style="width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; background: rgba(239, 68, 68, 0.1); border-radius: 8px; flex-shrink: 0;">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2">
            <path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"></path>
            <line x1="4" y1="22" x2="4" y2="15"></line>
          </svg>
        </div>
        <div class="option-content" style="flex: 1;">
          <div class="option-title" style="font-weight: 600; font-size: 0.9rem; margin-bottom: 0.25rem;">Report Post</div>
          <div class="option-description" style="font-size: 0.8rem; color: var(--text-secondary);">Flag inappropriate content</div>
        </div>
      </div>
      
      <!-- Fork Post -->
      <div 
        class="post-option-item" 
        data-post-id="${post_id}" 
        data-action="fork-post"
        style="display: flex; align-items: center; gap: 1rem; padding: 1rem; cursor: pointer; transition: background 0.2s; border-bottom: 1px solid var(--border);"
        onmouseover="this.style.background='var(--bg-secondary)'" 
        onmouseout="this.style.background='transparent'">
        <div class="option-icon" style="width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; background: rgba(59, 130, 246, 0.1); border-radius: 8px; flex-shrink: 0;">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#3b82f6" stroke-width="2">
            <circle cx="12" cy="18" r="3"></circle>
            <circle cx="6" cy="6" r="3"></circle>
            <circle cx="18" cy="6" r="3"></circle>
            <path d="M18 9v1a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V9"></path>
            <path d="M12 12v3"></path>
          </svg>
        </div>
        <div class="option-content" style="flex: 1;">
          <div class="option-title" style="font-weight: 600; font-size: 0.9rem; margin-bottom: 0.25rem;">Repost</div>
          <div class="option-description" style="font-size: 0.8rem; color: var(--text-secondary);">Create your own version</div>
        </div>
      </div>
      
      <!-- Ask Learnora -->
      <div 
        class="post-option-item" 
        data-post-id="${post_id}" 
        data-action='ask-learnora'
        style="display: flex; align-items: center; gap: 1rem; padding: 1rem; cursor: pointer; transition: background 0.2s; border-bottom: 1px solid var(--border);"
        onmouseover="this.style.background='var(--bg-secondary)'" 
        onmouseout="this.style.background='transparent'">
        <div class="option-icon" style="width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; background: rgba(168, 85, 247, 0.1); border-radius: 8px; flex-shrink: 0;">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#a855f7" stroke-width="2">
            <path d="M12 2L15.09 8.26L22 9.27L17 14.14L18.18 21.02L12 17.77L5.82 21.02L7 14.14L2 9.27L8.91 8.26L12 2Z"/>
          </svg>
        </div>
        <div class="option-content" style="flex: 1;">
          <div class="option-title" style="font-weight: 600; font-size: 0.9rem; margin-bottom: 0.25rem;">Ask Learnora</div>
          <div class="option-description" style="font-size: 0.8rem; color: var(--text-secondary);">Get AI help with this post</div>
        </div>
      </div>
      
      ${!is_author && thread && !thread.is_member ? `
      <!-- Join Thread -->
      <div 
        class="post-option-item" 
        data-post-id="${post_id}" 
        data-action="view-thread" 
        data-thread-id="${thread.thread_id}" 
        data-thread-type="post"
        style="display: flex; align-items: center; gap: 1rem; padding: 1rem; cursor: pointer; transition: background 0.2s; border-bottom: 1px solid var(--border);"
        onmouseover="this.style.background='var(--bg-secondary)'" 
        onmouseout="this.style.background='transparent'">
        <div class="option-icon" style="width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; background: rgba(249, 115, 22, 0.1); border-radius: 8px; flex-shrink: 0;">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#f97316" stroke-width="2">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
          </svg>
        </div>
        <div class="option-content" style="flex: 1;">
          <div class="option-title" style="font-weight: 600; font-size: 0.9rem; margin-bottom: 0.25rem;">Join Thread</div>
          <div class="option-description" style="font-size: 0.8rem; color: var(--text-secondary);">${thread.request_status === 'pending' ? 'Request Pending' : 'Participate in discussion'}</div>
        </div>
      </div>
      ` : ''}
      
      <!-- ✅ DYNAMIC: Follow/Unfollow based on current state -->
      ${interactions.followed ? `
      <!-- Unfollow Post -->
      <div 
        class="post-option-item" 
        data-post-id="${post_id}" 
        data-action="unfollow-post"
        
        style="display: flex; align-items: center; gap: 1rem; padding: 1rem; cursor: pointer; transition: background 0.2s; border-bottom: 1px solid var(--border);"
        onmouseover="this.style.background='var(--bg-secondary)'" 
        onmouseout="this.style.background='transparent'">
        <div class="option-icon" style="width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; background: rgba(148, 163, 184, 0.1); border-radius: 8px; flex-shrink: 0;">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" stroke-width="2">
            <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
            <circle cx="12" cy="12" r="3"></circle>
            <line x1="18" y1="6" x2="6" y2="18"></line>
          </svg>
        </div>
        <div class="option-content" style="flex: 1;">
          <div class="option-title" style="font-weight: 600; font-size: 0.9rem; margin-bottom: 0.25rem;">Unfollow Post</div>
          <div class="option-description" style="font-size: 0.8rem; color: var(--text-secondary);">Stop receiving updates</div>
        </div>
      </div>
      ` : `
      <!-- Follow Post -->
      <div 
        class="post-option-item" 
        data-post-id="${post_id}" 
        data-action="follow-post"
        
        style="display: flex; align-items: center; gap: 1rem; padding: 1rem; cursor: pointer; transition: background 0.2s; border-bottom: 1px solid var(--border);"
        onmouseover="this.style.background='var(--bg-secondary)'" 
        onmouseout="this.style.background='transparent'">
        <div class="option-icon" style="width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; background: rgba(59, 130, 246, 0.1); border-radius: 8px; flex-shrink: 0;">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#3b82f6" stroke-width="2">
            <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
            <circle cx="12" cy="12" r="3"></circle>
          </svg>
        </div>
        <div class="option-content" style="flex: 1;">
          <div class="option-title" style="font-weight: 600; font-size: 0.9rem; margin-bottom: 0.25rem;">Follow Post</div>
          <div class="option-description" style="font-size: 0.8rem; color: var(--text-secondary);">Get notified of updates</div>
        </div>
      </div>
      `}
      
      
      ${!is_author && author && author.connection_status == 'accepted' ? `
<!-- Message Author -->
<div 
  class="post-option-item" 
  data-post-id="${post_id}" 
  data-user-id="${author.id}" 
  data-action="message-author"
  
  style="display: flex; align-items: center; gap: 1rem; padding: 1rem; cursor: pointer; transition: background 0.2s; border-bottom: 1px solid var(--border);"
  onmouseover="this.style.background='var(--bg-secondary)'" 
  onmouseout="this.style.background='transparent'">
  
  <div class="option-icon" 
       style="width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; background: rgba(99, 102, 241, 0.1); border-radius: 8px; flex-shrink: 0;">
       
    <!-- Modern message/chat icon -->
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#6366f1" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"></path>
    </svg>
    
  </div>
  
  <div class="option-content" style="flex: 1;">
    <div class="option-title" 
         style="font-weight: 600; font-size: 0.9rem; margin-bottom: 0.25rem;">
      Message Author
    </div>
    
    <div class="option-description" 
         style="font-size: 0.8rem; color: var(--text-secondary);">
      Start a private conversation
    </div>
  </div>
  
</div>
` : ''}
      
      ${!is_author && author && !author.connection ? `
      <!-- Connect Request -->
      <div 
        class="post-option-item" 
        data-post-id="${post_id}" 
        data-user-id='${author.id}' 
        data-action="open-connection-request"
        
        style="display: flex; align-items: center; gap: 1rem; padding: 1rem; cursor: pointer; transition: background 0.2s; border-bottom: 1px solid var(--border);"
        onmouseover="this.style.background='var(--bg-secondary)'" 
        onmouseout="this.style.background='transparent'">
        <div class="option-icon" style="width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; background: rgba(34, 197, 94, 0.1); border-radius: 8px; flex-shrink: 0;">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="2">
            <path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>
            <circle cx="8.5" cy="7" r="4"></circle>
            <line x1="20" y1="8" x2="20" y2="14"></line>
            <line x1="23" y1="11" x2="17" y2="11"></line>
          </svg>
        </div>
        <div class="option-content" style="flex: 1;">
          <div class="option-title" style="font-weight: 600; font-size: 0.9rem; margin-bottom: 0.25rem;">Connect</div>
          <div class="option-description" style="font-size: 0.8rem; color: var(--text-secondary);">Send connection request</div>
        </div>
      </div>
      ` : ''}
      
      ${!is_author ? `
      <!-- Form Thread -->
      <div 
        class="post-option-item" 
        data-post-id='${post_id}' 
        data-action="form-thread-with-author"
    
        style="display: flex; align-items: center; gap: 1rem; padding: 1rem; cursor: pointer; transition: background 0.2s; border-bottom: 1px solid var(--border);"
        onmouseover="this.style.background='var(--bg-secondary)'" 
        onmouseout="this.style.background='transparent'">
        <div class="option-icon" style="width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; background: rgba(249, 115, 22, 0.1); border-radius: 8px; flex-shrink: 0;">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#f97316" stroke-width="2">
            <circle cx="12" cy="12" r="10"></circle>
            <line x1="12" y1="8" x2="12" y2="16"></line>
            <line x1="8" y1="12" x2="16" y2="12"></line>
          </svg>
        </div>
        <div class="option-content" style="flex: 1;">
          <div class="option-title" style="font-weight: 600; font-size: 0.9rem; margin-bottom: 0.25rem;">Form Thread</div>
          <div class="option-description" style="font-size: 0.8rem; color: var(--text-secondary);">Start a study group</div>
        </div>
      </div>
      ` : ''}
      
      
      ${is_author ? `
      <!-- Delete Post -->
      <div 
        class="post-option-item" 
        data-post-id="${post_id}" 
        data-action="delete-post"
        
        style="display: flex; align-items: center; gap: 1rem; padding: 1rem; cursor: pointer; transition: background 0.2s; border-bottom: 1px solid var(--border);"
        onmouseover="this.style.background='rgba(239, 68, 68, 0.05)'" 
        onmouseout="this.style.background='transparent'">
        <div class="option-icon" style="width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; background: rgba(239, 68, 68, 0.1); border-radius: 8px; flex-shrink: 0;">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2">
            <polyline points="3 6 5 6 21 6"></polyline>
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
          </svg>
        </div>
        <div class="option-content" style="flex: 1;">
          <div class="option-title" style="font-weight: 600; font-size: 0.9rem; margin-bottom: 0.25rem; color: var(--danger);">Delete Post</div>
          <div class="option-description" style="font-size: 0.8rem; color: var(--text-secondary);">Permanently remove</div>
        </div>
      </div>
      ` : ''}
      
      <!-- ✅ DYNAMIC: Mark Solved/Unsolved based on current state -->
      ${canSolve ? (
        is_solved ? `
        <!-- Mark Unsolved -->
        <div 
          class="post-option-item" 
          data-post-id="${post_id}" 
          data-action="unmark-solved"
          
          style="display: flex; align-items: center; gap: 1rem; padding: 1rem; cursor: pointer; transition: background 0.2s; border-bottom: 1px solid var(--border);"
          onmouseover="this.style.background='var(--bg-secondary)'" 
          onmouseout="this.style.background='transparent'">
          <div class="option-icon" style="width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; background: rgba(239, 68, 68, 0.1); border-radius: 8px; flex-shrink: 0;">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2">
              <circle cx="12" cy="12" r="10"></circle>
              <line x1="15" y1="9" x2="9" y2="15"></line>
              <line x1="9" y1="9" x2="15" y2="15"></line>
            </svg>
          </div>
          <div class="option-content" style="flex: 1;">
            <div class="option-title" style="font-weight: 600; font-size: 0.9rem; margin-bottom: 0.25rem;">Mark Unsolved</div>
            <div class="option-description" style="font-size: 0.8rem; color: var(--text-secondary);">Reopen this question</div>
          </div>
        </div>
        ` : `
        <!-- Mark Solved -->
        <div 
          class="post-option-item" 
          data-post-id="${post_id}" 
          data-action="mark-solved"
          
          style="display: flex; align-items: center; gap: 1rem; padding: 1rem; cursor: pointer; transition: background 0.2s; border-bottom: 1px solid var(--border);"
          onmouseover="this.style.background='var(--bg-secondary)'" 
          onmouseout="this.style.background='transparent'">
          <div class="option-icon" style="width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; background: rgba(34, 197, 94, 0.1); border-radius: 8px; flex-shrink: 0;">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="2">
              <polyline points="20 6 9 17 4 12"></polyline>
            </svg>
          </div>
          <div class="option-content" style="flex: 1;">
            <div class="option-title" style="font-weight: 600; font-size: 0.9rem; margin-bottom: 0.25rem;">Mark Solved</div>
            <div class="option-description" style="font-size: 0.8rem; color: var(--text-secondary);">Mark as resolved</div>
          </div>
        </div>
        `
      ) : ''}
  
      
      <!-- Listen (Audio) -->
      <div 
        class="post-option-item" 
        data-post-id="${post_id}" 
        data-action="listen-post"
        
        style="display: flex; align-items: center; gap: 1rem; padding: 1rem; cursor: pointer; transition: background 0.2s;"
        onmouseover="this.style.background='var(--bg-secondary)'" 
        onmouseout="this.style.background='transparent'">
        <div class="option-icon" style="width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; background: rgba(59, 130, 246, 0.1); border-radius: 8px; flex-shrink: 0;">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#3b82f6" stroke-width="2">
            <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon>
            <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"></path>
          </svg>
        </div>
        <div class="option-content" style="flex: 1;">
          <div class="option-title" style="font-weight: 600; font-size: 0.9rem; margin-bottom: 0.25rem;">Listen (Audio)</div>
          <div class="option-description" style="font-size: 0.8rem; color: var(--text-secondary);">Text-to-speech playback</div>
        </div>
      </div>
      
    </div>
  `;
}

export function createPostCard(post) {
  const tags = post.tags?.map(tag => `
    <span class="tag" data-action="view-tag-posts" data-tag="${tag}" style="display: inline-block; padding: 0.25rem 0.75rem; background: var(--bg-tertiary); border-radius: 9999px; font-size: 0.875rem; margin-right: 0.5rem; cursor: pointer;">#${tag}</span>
  `).join('') || '';
  
  const postTypeIcon = getPostTypeIcon(post.post_type);
  const resourceLinkHTML = post.resources ? buildResourceLinks(post.resources) : '';
  const canSolveType = CAN_SOLVE_TYPES.includes(post.post_type);
  const length = post.resources?.length || 0;
  
  const resourcesHTML = post.resources?.length > 0 
    ? buildPostResourcesContainer(post.resources, post.id)
    : '';
  
  const commentsPreviewHTML = buildCommentsPreviewHTML(post.comments, post.id);
  
  return `
    <div data-resource-length="${length}" 
         data-resources='${JSON.stringify(post.resources || []).replace(/'/g, "&apos;").replace(/"/g, "&quot;")}' 
         id="post-${post.id}" 
         data-post-id="${post.id}" 
         class="post-card"
         style="background: var(--bg-primary); border: 1px solid var(--border); border-radius: 12px; padding: 1.5rem; margin-bottom: 1rem; position: relative;">
      
      <div class="post-header" style="display: flex; align-items: flex-start; gap: 1rem;">
        <img data-action='view-avatar' 
             data-username="${post.author.username}"
             src="${post.author?.avatar || '/static/default-avatar.png'}" 
            
             class="avatar" 
             onerror="this.src='/static/default-avatar.png'"
             style="width: 48px; height: 48px; border-radius: 50%; cursor: pointer;">
        
        <div class="post-author" style="flex: 1;">
         <div class="post-author-info" style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.25rem;">
            <div data-action="view-profile" 
                 data-username="${post.author.username}"
                 class="post-author-name"
                 style="font-weight: 600; cursor: pointer;">
              ${post.author?.name || 'Anonymous'}
            </div>
          </div>
          <div class="post-time" style="font-size: 0.875rem; color: var(--text-secondary);">${formatTime(post.posted_at)}</div>
          
          ${post.is_solved || post.thread_enabled ? `
            <div class="post-header-badges" style="display: flex; gap: 0.5rem; margin-top: 0.5rem;">
              ${post.is_solved ? '<span class="solved-badge" style="padding: 0.25rem 0.5rem; background: var(--success); color: white; border-radius: 4px; font-size: 0.75rem;">✓ Solved</span>' : ''}
              ${post.thread_enabled ? '<span class="thread-badge" style="padding: 0.25rem 0.5rem; background: var(--info); color: white; border-radius: 4px; font-size: 0.75rem;">🧵 Thread</span>' : ''}
            </div>
          ` : ''}
        </div>
        
        <button data-action="toggle-post-options"
                class="post-options-btn" 
                id="options-btn-${post.id}"
                style="padding: 0.5rem; background: var(--bg-tertiary); border: none; border-radius: 4px; cursor: pointer; font-size: 1.25rem; position: relative;">
          ⋯
        </button>
      </div>
      
  
      
      <div class="post-type-indicator" style="display: flex; align-items: center; gap: 0.5rem; margin-top: 1rem;">
        <span style="display: flex; align-items: center;">${postTypeIcon}</span>
        <span class="post-type-label" style="text-transform: capitalize; font-size: 0.875rem; color: var(--text-secondary);">${post.post_type}</span>
      </div>
      
      ${post.title ? `<div class="post-title" style="font-size: 1.25rem; font-weight: 600; margin-top: 1rem;">${post.title}</div>` : ''}
      <div class="post-content" style="margin-top: 0.75rem; line-height: 1.6;">${post.excerpt || post.text_content || ''}</div>
      
      ${resourcesHTML}
      
      ${post.resources?.length > 0 ? `
        <button class="btn-toggle-details" data-action="view-post-resource-links">
  <span class="download-icon">
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
      <polyline points="7 10 12 15 17 10"></polyline>
      <line x1="12" y1="15" x2="12" y2="3"></line>
    </svg>
  </span>
  <span class="toggle-text">Show Download Links</span>
  <svg class="toggle-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
    <polyline points="6 9 12 15 18 9"></polyline>
  </svg>
</button>
        <div class='resource-link-modal hidden' style="margin-top: 0.5rem; padding: 1rem; background: var(--bg-secondary); border-radius: 8px;">${resourceLinkHTML}</div>
      ` : ''}
        
      ${tags ? `<div class="post-tags" style="margin-top: 1rem;">${tags}</div>` : ''}
      ${commentsPreviewHTML}
      
      <div class="post-stats">
  <!-- Reaction Button with Icon -->
  <button class="stat-btn reaction-btn ${post.user_interactions?.user_reacted ? 'reacted' : ''}" 
          data-action="toggle-reactions"
          data-post-id="${post.id}"
          aria-label="React to post">
          <span class="reaction-icon">
  <svg class="heart-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
  </svg>
</span>
    
    <span class="reaction-count">${post.reactions_count > 0 ? post.reactions_count : ''}</span>
  </button>
  
  <!-- Comment Button -->
  <button class="stat-btn" data-action="open-comments">
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
    </svg>
    <span>${post.comments_count || 0}</span>
  </button>
  
  <!-- Share Button (NEW) -->
  <button class="share-btn" data-action="share-post">
    <span class="share-icon">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="18" cy="5" r="3"></circle>
        <circle cx="6" cy="12" r="3"></circle>
        <circle cx="18" cy="19" r="3"></circle>
        <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line>
        <line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line>
      </svg>
    </span>
    <span>Share</span>
  </button>
</div>
    </div>
  `;
}

export function createCommentCard(comment, context = 'modal') {


  const author = comment.author || {};
  const uniqueId = `comment-card-${context}-${comment.id}`;
  const resourcesHTML = buildCommentResourceHTML(
    comment.resources || [],
    comment.id,
    comment.post_id
  );
  const replies = comment.replies || [];
  const repliesHTML = createRepliesCard(replies, comment.id, context);
  
  return `
    <div data-resources='${JSON.stringify(comment.resources || []).replace(/'/g, "&apos;")}' 
         data-post-id="${comment.post_id}"
         data-comment-id="${comment.id}"
         data-depth="${comment.depth_level}" 
         class="comment-card" 
         id="${uniqueId}"
         style="padding: 1rem; background: var(--bg-primary); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 1rem; position: relative;">
      
      <!-- Comment Header -->
      <div class="comment-header" style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.5rem;">
        <img data-action='view-avatar' src="${author.avatar || '/static/default-avatar.png'}" 
             data-action="view-profile"
             data-username="${author.username || ''}"
             alt="${author.name || 'User'}"
             class="avatar"
             onerror="this.src='/static/default-avatar.png'"
             style="width: 40px; height: 40px; border-radius: 50%; cursor: pointer;">
        
        <div class="comment-author" style="flex: 1;">
          <div data-action="view-profile" 
               data-username="${author.username || ''}"
               class="comment-author-name"
               style="font-weight: 500; cursor: pointer;">${author.name || 'Anonymous'}</div>
          <div class="comment-time" style="font-size: 0.875rem; color: var(--text-secondary);">${formatTime(comment.posted_at)}</div>
        </div>

        ${comment.is_solution ? '<span class="solution-badge" style="padding: 0.25rem 0.5rem; background: var(--success); color: white; border-radius: 4px; font-size: 0.75rem;">✓ Solution</span>' : ''}
      </div>

      <!-- Comment Content -->
      <div class="comment-content" style="font-size: 0.9rem; line-height: 1.5; margin-bottom: 0.5rem;">${comment.text_content}</div>

      <!-- Comment Resources -->
      ${resourcesHTML}

      <!-- Comment Actions (Refined with Icons) -->
      <div class="comment-actions" style="display: flex; gap: 0.5rem; margin-top: 0.75rem; flex-wrap: wrap;">
        
        <!-- Like Button -->
        <button class="comment-action-btn ${comment.user_interactions?.has_liked ? 'liked' : ''}" 
                data-action="toggle-comment-like"
                data-comment-id="${comment.id}"
                aria-label="Like comment">
          <span class="action-icon">
            <!-- Outlined heart — base layer -->
            <svg class="heart-outline" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
            </svg>
            <!-- Filled heart — revealed by CSS when .liked is present -->
            <svg class="heart-fill" width="16" height="16" viewBox="0 0 24 24" fill="currentColor" stroke="none">
              <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
            </svg>
          </span>
          <span>${comment.likes_count > 0 ? comment.likes_count : 'Like'}</span>
        </button>

        <!-- Helpful Button -->
        <button class="comment-action-btn ${comment.user_interactions?.has_marked_helpful ? 'helpful' : ''}" 
                data-action="toggle-comment-helpful"
                data-comment-id="${comment.id}">
          <span class="action-icon">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M9 18h6"></path>
              <path d="M10 22h4"></path>
              <path d="M15.09 14c.18-.98.65-1.74 1.41-2.5A4.65 4.65 0 0 0 18 8 6 6 0 0 0 6 8c0 1 .23 2.23 1.5 3.5A4.61 4.61 0 0 1 8.91 14"></path>
            </svg>
          </span>
          <span>${comment.helpful_count > 0 ? comment.helpful_count : 'Helpful'}</span>
        </button>

        <!-- Mark Solution Button (for post authors only) -->
        ${!comment.is_you && comment.is_author && !comment.post_is_solved && !comment.is_solution ? `
          <button class="comment-action-btn solution" 
                  data-action="mark-solution"
                  data-comment-id="${comment.id}"
                  data-post-id="${comment.post_id}">
            <span class="action-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
                <polyline points="22 4 12 14.01 9 11.01"></polyline>
              </svg>
            </span>
            <span>Mark Solution</span>
          </button>
        ` : ''}

        <!-- Reply Button (only if not max depth) -->
        ${comment.depth_level < 1 ? `
          <button class="comment-action-btn" 
                  data-action="open-reply"
                  data-username="${author.username || ''}"
                  data-comment-id="${comment.id}"
                  data-post-id="${comment.post_id}">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
            </svg>
            <span>Reply</span>
          </button>
        ` : `
          <span class="disabled-text" style="font-size: 0.75rem; color: var(--text-secondary);">Max depth reached</span>
        `}

        <!-- Settings Button (for comment owner) -->
        ${comment.is_you ? `
          <button class="comment-action-btn" 
                  data-action="toggle-comment-settings"
                  data-comment-id="${comment.id}">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="1"></circle>
              <circle cx="12" cy="5" r="1"></circle>
              <circle cx="12" cy="19" r="1"></circle>
            </svg>
          </button>
        ` : ''}
      </div>

      <!-- Inline Advanced Options Dropdown (Hidden by default) -->
      ${comment.is_you ? `
        <div class="advanced-comment-options hidden">
          
          ${comment.resources && comment.resources.length > 0 ? `
          <!-- View Resources Option -->
          <button class="comment-option-item" 
                  data-action="view-comment-resource-links">
            <div class="comment-option-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                <polyline points="7 10 12 15 17 10"></polyline>
                <line x1="12" y1="15" x2="12" y2="3"></line>
              </svg>
            </div>
            <div class="comment-option-content">
              <div class="comment-option-title">Download Resources</div>
              <div class="comment-option-description">View all attachments</div>
            </div>
          </button>
          ` : ''}

          <!-- Delete Option -->
          <button class="comment-option-item danger" 
                  data-action="delete-comment"
                  data-comment-id="${comment.id}">
            <div class="comment-option-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="3 6 5 6 21 6"></polyline>
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
              </svg>
            </div>
            <div class="comment-option-content">
              <div class="comment-option-title">Delete Comment</div>
              <div class="comment-option-description">Permanently remove</div>
            </div>
          </button>
        </div>
      ` : ''}

      <!-- Replies -->
      ${repliesHTML}
    </div>
  `;
}

/**
 * ✅ UPDATED: Create local comment card (for newly posted comments)
 */
export function createLocalCommentCard(comment, context = 'modal') {
  const author = comment.author || {};
  const uniqueId = `comment-card-${context}-${comment.id}`;
  const resourcesHTML = buildCommentResourceHTML(
    comment.resources || [],
    comment.id,
    comment.post_id
  );
  const repliesHTML = createRepliesCard(comment.replies || [], comment.id, context);
  
  return `
    <div data-resources='${JSON.stringify(comment.resources || []).replace(/'/g, "&apos;")}' 
         data-post-id="${comment.post_id}"
         data-comment-id="${comment.id}"
         data-depth="${comment.depth_level}" 
         class="comment-card" 
         id="${uniqueId}"
         style="padding: 1rem; background: var(--bg-primary); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 1rem; position: relative;">
      
      <div class="comment-header" style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.5rem;">
        <img data-action='view-avatar' src="${author.avatar || '/static/default-avatar.png'}" 
             data-action="view-profile"
             data-username="${author.username || ''}"
             alt="${author.name || 'User'}"
             class="avatar"
             onerror="this.src='/static/default-avatar.png'"
             style="width: 40px; height: 40px; border-radius: 50%; cursor: pointer;">
        
        <div class="comment-author" style="flex: 1;">
          <div data-action="view-profile"
               data-username="${author.username || ''}"
               class="comment-author-name"
               style="font-weight: 500; cursor: pointer;">${author.name || 'Anonymous'}</div>
          <div class="comment-time" style="font-size: 0.875rem; color: var(--text-secondary);">${formatTime(comment.posted_at)}</div>
        </div>

        ${comment.is_solution ? '<span class="solution-badge" style="padding: 0.25rem 0.5rem; background: var(--success); color: white; border-radius: 4px; font-size: 0.75rem;">✓ Solution</span>' : ''}
      </div>

      <div class="comment-content" style="font-size: 0.9rem; line-height: 1.5; margin-bottom: 0.5rem;">${comment.text_content}</div>

      ${resourcesHTML}

      <div class="comment-actions" style="display: flex; gap: 0.5rem; margin-top: 0.75rem; flex-wrap: wrap;">
        
        <button class="comment-action-btn ${comment.user_interactions?.has_liked ? 'liked' : ''}" 
                data-action="toggle-comment-like"
                data-comment-id="${comment.id}"
                aria-label="Like comment">
          <span class="action-icon">
            <svg class="heart-outline" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
            </svg>
            <svg class="heart-fill" width="16" height="16" viewBox="0 0 24 24" fill="currentColor" stroke="none">
              <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
            </svg>
          </span>
          <span>${comment.likes_count > 0 ? comment.likes_count : 'Like'}</span>
        </button>

        <button class="comment-action-btn ${comment.user_interactions?.has_marked_helpful ? 'helpful' : ''}" 
                data-action="toggle-comment-helpful"
                data-comment-id="${comment.id}">
          <span class="action-icon">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M9 18h6"></path>
              <path d="M10 22h4"></path>
              <path d="M15.09 14c.18-.98.65-1.74 1.41-2.5A4.65 4.65 0 0 0 18 8 6 6 0 0 0 6 8c0 1 .23 2.23 1.5 3.5A4.61 4.61 0 0 1 8.91 14"></path>
            </svg>
          </span>
          <span>${comment.helpful_count > 0 ? comment.helpful_count : 'Helpful'}</span>
        </button>

        ${comment.is_you ? `
          <button class="comment-action-btn" 
                  data-action="toggle-comment-settings"
                  data-comment-id="${comment.id}">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="1"></circle>
              <circle cx="12" cy="5" r="1"></circle>
              <circle cx="12" cy="19" r="1"></circle>
            </svg>
          </button>
        ` : ''}
      </div>

      ${comment.is_you ? `
        <div class="advanced-comment-options hidden">
          <button class="comment-option-item" 
                  data-action="copy-comment-link"
                  data-comment-id="${comment.id}">
            <div class="comment-option-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path>
                <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path>
              </svg>
            </div>
            <div class="comment-option-content">
              <div class="comment-option-title">Copy Link</div>
              <div class="comment-option-description">Share this comment</div>
            </div>
          </button>

          <button class="comment-option-item danger" 
                  data-action="delete-comment"
                  data-comment-id="${comment.id}">
            <div class="comment-option-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="3 6 5 6 21 6"></polyline>
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
              </svg>
            </div>
            <div class="comment-option-content">
              <div class="comment-option-title">Delete Comment</div>
              <div class="comment-option-description">Permanently remove</div>
            </div>
          </button>
        </div>
      ` : ''}

      ${repliesHTML}
    </div>
  `;
}

/**
 * Render thread details HTML
 */
export function renderThreadDetailsHTML(thread) {
  return `
    <div class="thread-header" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem;">
      <h3 class="thread-title" style="font-size: 1.25rem; font-weight: 600;">${thread.title}</h3>
      <span style="padding: 0.25rem 0.75rem; background: ${thread.requires_approval ? 'var(--warning)' : 'var(--success)'}; color: white; border-radius: 9999px; font-size: 0.75rem;">
        ${thread.requires_approval ? "🔒 Private" : "🌎 Public"}
      </span>
    </div>

    <p style="margin-bottom: 1rem; color: var(--text-secondary);">${thread.description || 'No description'}</p>

    ${thread.tags && thread.tags.length > 0 ? `
    <div style="margin-bottom: 1rem;">
      <strong>Tags:</strong> 
      ${thread.tags.map(tag => `<span class="tag" style="display: inline-block; padding: 0.25rem 0.75rem; background: var(--bg-tertiary); border-radius: 9999px; font-size: 0.875rem; margin-right: 0.5rem;">#${tag}</span>`).join('')}
    </div>
    ` : ''}

    <p style="margin-bottom: 0.5rem;"><strong>Department:</strong> ${thread.department || "None"}</p>
    <p style="margin-bottom: 1rem;"><strong>Members:</strong> ${thread.total_users} / ${thread.max_members || '∞'}</p>
    <p style="margin-bottom: 1rem;"><strong>Last Activity:</strong> ${new Date(thread.last_activity).toLocaleString()}</p>

    ${thread.members_data && thread.members_data.length > 0 ? `
    <h4 style="margin-bottom: 0.75rem;">Members Preview:</h4>
    <div class="member-list" style="display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem;">
      ${thread.members_data.slice(0, 5).map(member => `
        <div class="member" style="text-align: center;">
          <img data-action='view-avatar' src="${member.avatar || '/static/default-avatar.png'}" style="width: 48px; height: 48px; border-radius: 50%; margin-bottom: 0.25rem;">
          <div class="member-name" style="font-size: 0.75rem; font-weight: 500;">${member.name.substring(0, 10)}</div>
          <div class="member-reputation-level" style="font-size: 0.625rem; color: var(--text-secondary);">${member.reputation_level || ''}</div>
        </div>
      `).join('')}
      ${thread.members_data.length > 5 ? `<div style="font-size: 0.875rem; color: var(--text-secondary); align-self: center;">+${thread.members_data.length - 5} more</div>` : ""}
    </div>
    ` : ''}

    <div style="display: flex; gap: 0.75rem;">
      <button id="join-thread-btn" 
              data-action="join-thread"
              class="btn btn-primary" 
              style="flex: 1; padding: 0.75rem; background: var(--primary); color: white; border: none; border-radius: 8px; cursor: pointer; font-weight: 500;">
        Join Thread
      </button>
      <button data-action='close-modal' 
              data-modal-id='thread-view-modal' 
              class="btn btn-secondary"
              style="padding: 0.75rem 1.5rem; background: var(--bg-secondary); border: none; border-radius: 8px; cursor: pointer;">
        Cancel
      </button>
    </div>
  `;
}