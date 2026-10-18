// ============================================================================
// CONNECTION EVENT HANDLERS
// ============================================================================

import { connectionContainer } from './connection.constants.js';
import { ConnectionAPI } from './connection.api.js';
import { connectionState } from './connection.state.js';
import { createSearchResultCard, createBlockedUserCard, createMutualConnectionCard } from './connection.templates.js';
import { getLoadingSkeleton, showEmptyState } from './connection.utils.js';
import { renderConnectionTab } from './connection.render.js';
import { loadConnectionTab } from './connection.init.js';

export function ConnectionEventListeners(){
  CreateStudySessionUploadListeners();
  RescheduleStudySessionUploadListeners();
}
// ============================================================================
// STUDY SESSIONS EVENT HANDLERS (FIXED)
// ============================================================================


// ============================================================================
// OPEN CREATE SESSION MODAL
// ============================================================================

/**
 * Sets up upload listeners for creating study sessions
 * Handles file uploads with preview, progress indication, and removal
 */
 export function createResourcePreviews(resources, previewArea, removable = true) {
  if (!previewArea) {
    console.warn('Preview area not provided');
    return;
  }

  if (!Array.isArray(resources) || resources.length === 0) {
    console.log('No resources to preview');
    return;
  }

  resources.forEach((resource) => {
    if (!resource || !resource.url) {
      console.warn('Invalid resource object:', resource);
      return;
    }

    // Create preview container
    const previewDiv = document.createElement("div");
    previewDiv.className = "resource-preview-container";
    previewDiv.style.cssText = `
      position: relative;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      margin: 0.5rem;
      width: 150px;
      height: 150px;
      border-radius: 8px;
      overflow: hidden;
      background: var(--bg-tertiary, #f5f5f5);
    `;
    previewDiv.dataset.resourceUrl = resource.url;

    // Create media element based on resource type
    let media;
    const resourceType = resource.type || resource.resource_type || '';
    const filename = resource.filename || resource.name || 'Resource';

    if (resourceType.startsWith("image/") || resourceType === 'image') {
      media = document.createElement("img");
      media.className = "resource-preview-image";
      media.src = resource.url;
      media.style.cssText = `
        width: 100%;
        height: 100%;
        object-fit: cover;
      `;
      media.alt = filename;
      media.onerror = function() {
        const fallback = createFallbackElement(filename);
        media.replaceWith(fallback);
      };
    } else if (resourceType.startsWith("video/") || resourceType === 'video') {
      media = document.createElement("video");
      media.className = "resource-preview-video";
      media.src = resource.url;
      media.controls = true;
      media.style.cssText = `
        width: 100%;
        height: 100%;
        object-fit: cover;
      `;
      media.onerror = function() {
        const fallback = createFallbackElement(filename);
        media.replaceWith(fallback);
      };
    } else {
      // For documents and other files
      media = createDocumentPreview(filename);
    }

    previewDiv.appendChild(media);

    // Create remove button
    const removeBtn = document.createElement('button');
    removeBtn.className = "resource-preview-remove-btn";
    removeBtn.textContent = "×";
    removeBtn.title = removable ? "Remove resource" : "Cannot remove this resource";
    removeBtn.style.cssText = `
      position: absolute;
      top: 8px;
      right: 8px;
      background: rgba(0, 0, 0, 0.7);
      color: white;
      border: none;
      border-radius: 50%;
      width: 28px;
      height: 28px;
      cursor: ${removable ? 'pointer' : 'not-allowed'};
      font-size: 1.25rem;
      line-height: 1;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background 0.2s ease;
      padding: 0;
    `;
    
    // Handle resource removal
    removeBtn.onclick = function() {
      if (removable) {
        previewDiv.remove();
        connectionState.removeRescheduleSessionResource(resource.url);
      }
    };

    // Hover effect for remove button
    if (removable) {
      removeBtn.onmouseenter = function() {
        this.style.background = 'rgba(255, 0, 0, 0.8)';
      };
      removeBtn.onmouseleave = function() {
        this.style.background = 'rgba(0, 0, 0, 0.7)';
      };
    }

    previewDiv.appendChild(removeBtn);
    previewArea.appendChild(previewDiv);
  });

  console.log(`Created ${resources.length} resource preview(s)`);
}

// Helper function to create document preview
function createDocumentPreview(filename) {
  const media = document.createElement("div");
  media.className = "resource-preview-document";
  
  // Get file extension and icon
  const extension = filename.split('.').pop().toLowerCase();
  const icon = getFileIcon(extension);
  
  media.innerHTML = `
    <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; padding: 1rem; height: 100%; width: 100%;">
      <span class="resource-preview-icon" style="font-size: 2.5rem; margin-bottom: 0.5rem;">${icon}</span>
      <span class="resource-preview-filename" style="font-size: 0.75rem; word-break: break-word; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; max-width: 100%;">${filename}</span>
    </div>
  `;
  media.style.cssText = `
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
  `;
  
  return media;
}

// Helper function to create fallback element
function createFallbackElement(filename) {
  const extension = filename.split('.').pop().toLowerCase();
  const icon = getFileIcon(extension);
  
  const fallback = document.createElement("div");
  fallback.className = "resource-preview-fallback";
  fallback.innerHTML = `
    <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; padding: 1rem; height: 100%; width: 100%;">
      <span class="resource-preview-icon" style="font-size: 2.5rem; margin-bottom: 0.5rem;">${icon}</span>
      <span class="resource-preview-filename" style="font-size: 0.75rem; word-break: break-word; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; max-width: 100%;">${filename}</span>
    </div>
  `;
  fallback.style.cssText = `
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
  `;
  
  return fallback;
}

// Helper function to get file icon
function getFileIcon(extension) {
  const iconMap = {
    'pdf': '📕',
    'doc': '📘',
    'docx': '📘',
    'xls': '📊',
    'xlsx': '📊',
    'ppt': '📙',
    'pptx': '📙',
    'zip': '📦',
    'rar': '📦',
    'txt': '📝'
  };
  
  return iconMap[extension] || '📄';
}
 


export function CreateStudySessionUploadListeners() {
  // Get DOM elements
  const previewArea = document.getElementById("create-session-resource-preview-container");
  const input = document.getElementById("create-session-upload-input");
  
  // Validate DOM elements exist
  if (!previewArea) {
    console.error('❌ Preview area not found: #create-session-resource-preview-container');
    return;
  }
  
  if (!input) {
    console.error('❌ Upload input not found: #create-session-upload-input');
    return;
  }
  
  console.log('✅ CreateStudySessionUploadListeners: DOM elements found');
  
  // Clone and replace input to remove any existing listeners
  const newInput = input.cloneNode(true);
  input.parentNode.replaceChild(newInput, input);
  
  // Add change event listener
  newInput.addEventListener("change", async function(e) {
    const files = Array.from(e.target.files);
    
    if (files.length === 0) {
      console.log('No files selected');
      return;
    }
    
    console.log(`📁 Processing ${files.length} file(s)`);
    
    for (const file of files) {
      if (!file) {
        console.warn('Skipping null/undefined file');
        continue;
      }
      
      console.log(`📤 Uploading: ${file.name} (${file.type}, ${(file.size / 1024).toFixed(2)} KB)`);
      
      // Create preview container
      const previewDiv = document.createElement("div");
      previewDiv.className = "preview-item";
      previewDiv.style.cssText = `
        position: relative;
        display: inline-block;
        margin: 0.5rem;
        border: 2px solid var(--border);
        border-radius: 8px;
        padding: 0.5rem;
        background: var(--bg-primary);
      `;
      
      // Store blob URL for cleanup
      let blobURL = null;
      
      // Create media element based on file type
      let media;
      if (file.type.startsWith("image/")) {
        media = document.createElement("img");
        blobURL = URL.createObjectURL(file);
        media.src = blobURL;
        media.style.cssText = `
          max-width: 150px;
          max-height: 150px;
          border-radius: 8px;
          display: block;
        `;
        media.alt = file.name;
        
      } else if (file.type.startsWith("video/")) {
        media = document.createElement("video");
        blobURL = URL.createObjectURL(file);
        media.src = blobURL;
        media.controls = true;
        media.style.cssText = `
          max-width: 150px;
          max-height: 150px;
          border-radius: 8px;
          display: block;
        `;
        
      } else {
        // For non-media files, show filename with icon
        media = document.createElement("div");
        media.className = "file-name";
        media.innerHTML = `
          <div style="text-align: center;">
            <div style="font-size: 2rem; margin-bottom: 0.5rem;">📄</div>
            <div style="font-size: 0.875rem; word-break: break-all; max-width: 150px;">${file.name}</div>
            <div style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.25rem;">
              ${(file.size / 1024).toFixed(2)} KB
            </div>
          </div>
        `;
        media.style.cssText = `
          padding: 1rem;
          background: var(--bg-tertiary);
          border-radius: 8px;
          min-width: 150px;
        `;
      }
      
      previewDiv.appendChild(media);
      
      // Create upload progress loader
      const loader = document.createElement("div");
      loader.className = "upload-loader";
      loader.style.cssText = `
        position: absolute;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        background: rgba(0, 0, 0, 0.8);
        color: white;
        padding: 0.75rem 1rem;
        border-radius: 6px;
        font-size: 0.875rem;
        font-weight: 500;
        z-index: 10;
        display: flex;
        align-items: center;
        gap: 0.5rem;
      `;
      loader.innerHTML = `
        <div class="spinner" style="
          width: 16px;
          height: 16px;
          border: 2px solid rgba(255,255,255,0.3);
          border-top-color: white;
          border-radius: 50%;
          animation: spin 0.8s linear infinite;
        "></div>
        <span>Uploading...</span>
      `;
      
      // Create remove button (hidden until upload completes)
      const removeBtn = document.createElement('button');
      removeBtn.className = "remove-resource-btn";
      removeBtn.innerHTML = "×";
      removeBtn.style.cssText = `
        position: absolute;
        top: -8px;
        right: -8px;
        background: var(--danger);
        color: white;
        border: 2px solid white;
        border-radius: 50%;
        width: 28px;
        height: 28px;
        cursor: pointer;
        display: none;
        font-size: 20px;
        line-height: 1;
        font-weight: bold;
        z-index: 20;
        transition: all 0.2s;
      `;
      removeBtn.title = "Remove file";
      
      removeBtn.onmouseenter = function() {
        this.style.transform = "scale(1.1)";
        this.style.background = "#dc2626";
      };
      
      removeBtn.onmouseleave = function() {
        this.style.transform = "scale(1)";
        this.style.background = "var(--danger)";
      };
      
      previewDiv.appendChild(loader);
      previewDiv.appendChild(removeBtn);
      previewArea.appendChild(previewDiv);
      
      // Store resource data for removal
      let uploadedResource = null;
      
      try {
        // Validate ConnectionAPI exists
        if (typeof ConnectionAPI === 'undefined' || !ConnectionAPI.uploadResource) {
          throw new Error('ConnectionAPI.uploadResource is not available');
        }
        
        console.log(`⏳ Uploading ${file.name}...`);
        
        // Upload file to server
        const result = await ConnectionAPI.uploadResource(file);
        
        console.log('Upload result:', result);
        
        if (result.status === "success" && result.data) {
          const resource = {
            url: result.data.url,
            type: result.data.type,
            filename: result.data.filename
          };
          
          uploadedResource = resource;
          
          console.log(`✅ Upload successful: ${resource.filename}`);
          
          // Validate connectionState exists
          if (typeof connectionState === 'undefined' || !connectionState.addCreateSessionResource) {
            console.error('❌ connectionState.addCreateSessionResource is not available');
            throw new Error('Cannot save resource - connectionState not available');
          }
          
          // Add resource to state
          connectionState.addCreateSessionResource(resource);
          console.log('Resource added to state:', resource);
          
          // Remove loader and show remove button
          loader.remove();
          removeBtn.style.display = "block";
          
          // Update preview border to show success
          previewDiv.style.borderColor = "var(--success)";
          
          // Show success toast
          
        } else {
          throw new Error(result.message || "Upload failed with unknown error");
        }
        
      } catch (error) {
        console.error("❌ Upload error:", error);
        
        // Update UI to show error
        loader.innerHTML = `
          <span style="color: #fca5a5;">⚠️ Upload failed</span>
        `;
        loader.style.background = "rgba(220, 38, 38, 0.9)";
        
        previewDiv.style.borderColor = "var(--danger)";
        
        showToast(`Failed to upload ${file.name}: ${error.message}`, "error");
        
        // Show remove button even on error
        setTimeout(() => {
          loader.remove();
          removeBtn.style.display = "block";
        }, 2000);
      }
      
      // Setup remove button click handler (outside try-catch to always work)
      removeBtn.onclick = function() {
        console.log(`🗑️ Removing resource: ${file.name}`);
        
        // Remove from state if it was successfully uploaded
        if (uploadedResource && typeof connectionState !== 'undefined' && connectionState.removeCreateSessionResource) {
          connectionState.removeCreateSessionResource(uploadedResource.url);
          console.log('Resource removed from state');
        }
        
        // Clean up blob URL
        if (blobURL) {
          URL.revokeObjectURL(blobURL);
          console.log('Blob URL revoked');
        }
        
        // Remove DOM element
        previewDiv.remove();
        
      };
    }
    
    // Reset input to allow re-uploading the same file
    e.target.value = "";
  });
  
  // Add CSS animation for spinner
  if (!document.getElementById('upload-spinner-animation')) {
    const style = document.createElement('style');
    style.id = 'upload-spinner-animation';
    style.textContent = `
      @keyframes spin {
        from { transform: rotate(0deg); }
        to { transform: rotate(360deg); }
      }
    `;
    document.head.appendChild(style);
  }
  
  console.log('✅ Create Session upload listeners setup complete');
}

// Export for debugging
if (typeof window !== 'undefined') {
  window.debugCreateSessionUpload = function() {
    console.log('=== Debug Info ===');
    console.log('Preview area exists:', !!document.getElementById("create-session-resource-preview-container"));
    console.log('Input exists:', !!document.getElementById("create-session-upload-input"));
    console.log('ConnectionAPI exists:', typeof ConnectionAPI !== 'undefined');
    console.log('connectionState exists:', typeof connectionState !== 'undefined');
    
    if (typeof connectionState !== 'undefined') {
      console.log('createSessionResources:', connectionState.createSessionResources);
    }
  };
  
  console.log('💡 Run window.debugCreateSessionUpload() to check setup');
}


/**
 * Sets up upload listeners for rescheduling study sessions
 * Handles file uploads with preview, progress indication, and removal
 */
export function RescheduleStudySessionUploadListeners() {
  const previewArea = document.getElementById("reschedule-session-resource-preview-container");
  if (!previewArea) {
    return;
  }
  
  const input = document.getElementById("reschedule-session-upload-input");
  if (!input) {
    console.warn('Reschedule session upload input not found');
    return;
  }
  
  // Clone and replace input to remove any existing listeners
  const newInput = input.cloneNode(true);
  input.parentNode.replaceChild(newInput, input);
  
  newInput.addEventListener("change", async function(e) {
    const files = Array.from(e.target.files);
    
    if (files.length === 0) return;
    
    for (const file of files) {
      if (!file) continue;
      
      // Create preview container
      const previewDiv = document.createElement("div");
      previewDiv.className = "preview-item";
      previewDiv.style.cssText = "position: relative; display: inline-block; margin: 0.5rem;";
      
      // Create media element based on file type
      let media;
      if (file.type.startsWith("image/")) {
        media = document.createElement("img");
        media.src = URL.createObjectURL(file);
        media.style.cssText = "max-width: 150px; max-height: 150px; border-radius: 8px;";
        media.alt = file.name;
      } else if (file.type.startsWith("video/")) {
        media = document.createElement("video");
        media.src = URL.createObjectURL(file);
        media.controls = true;
        media.style.cssText = "max-width: 150px; max-height: 150px; border-radius: 8px;";
      } else {
        // For non-media files, show filename
        media = document.createElement("div");
        media.className = "file-name";
        media.textContent = file.name;
        media.style.cssText = "padding: 1rem; background: var(--bg-tertiary); border-radius: 8px; font-size: 0.875rem;";
      }
      
      previewDiv.appendChild(media);
      
      // Create upload progress loader
      const loader = document.createElement("div");
      loader.className = "loader";
      loader.style.cssText = "position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); background: rgba(0,0,0,0.7); color: white; padding: 0.5rem; border-radius: 4px; font-size: 0.75rem;";
      loader.textContent = "Uploading...";
      
      // Create remove button (hidden until upload completes)
      const removeBtn = document.createElement('button');
      removeBtn.className = "cancel-upload";
      removeBtn.textContent = "×";
      removeBtn.style.cssText = "position: absolute; top: 5px; right: 5px; background: rgba(0,0,0,0.7); color: white; border: none; border-radius: 50%; width: 24px; height: 24px; cursor: pointer; display: none;";
      
      previewDiv.appendChild(loader);
      previewDiv.appendChild(removeBtn);
      previewArea.appendChild(previewDiv);
    
      try {
        // Upload file to server
        const result = await ConnectionAPI.uploadResource(file);
        
        if (result.status === "success") {
          const resource = {
            url: result.data.url,
            type: result.data.type,
            filename: result.data.filename
          };
          
          // Add resource to state
          const type = connectionState.getAmRequester? 'requester_resources': 'receiver_resources';
          connectionState.addRescheduleSessionResource(resource, type);
          
          // Remove loader and show remove button
          loader.remove();
          removeBtn.style.display = "block";
          
          // Handle resource removal
          removeBtn.onclick = function() {
            previewDiv.remove();
            connectionState.removeRescheduleSessionResource(resource.url);
            
            // Revoke object URL to free memory
            if (media.src && media.src.startsWith('blob:')) {
              URL.revokeObjectURL(media.src);
            }
          };
        } else {
          // Handle upload failure
          loader.textContent = "Failed";
          loader.style.background = "var(--danger)";
          showToast("Upload failed: " + (result.message || "Unknown error"), "error");
          removeBtn.style.display = "block";
          removeBtn.onclick = function() {
            previewDiv.remove();
      
            
            // Revoke object URL to free memory
            if (media.src && media.src.startsWith('blob:')) {
              URL.revokeObjectURL(media.src);
            }
          };
        }
      } catch (error) {
        console.error("Upload error:", error);
        loader.textContent = "Error";
        loader.style.background = "var(--danger)";
        removeBtn.style.display = "block";
        showToast("Error uploading file", "error");
        removeBtn.onclick = function() {
            previewDiv.remove();
    
            
            
            // Revoke object URL to free memory
            if (media.src && media.src.startsWith('blob:')) {
              URL.revokeObjectURL(media.src);
            }
          };
      }
    }
    
    // Reset input to allow re-uploading the same file
    e.target.value = "";
  });
  
  console.log('Reschedule Session upload listeners setup complete');
}

function populateRescheduleModal(sessionData) {
  // ✅ FIX: Set am_requester state
  connectionState.setAmRequester(sessionData.am_requester);
  
  // ✅ FIX: Correct selectors with dots
  const previewArea1 = document.querySelector(".reschedule-session-resource-container.receiver");
  const previewArea2 = document.querySelector(".reschedule-session-resource-container.requester");
  previewArea1.innerHTML = '';
  previewArea2.innerHTML = '';
  
  const amRequester = sessionData.am_requester;
  
  // ✅ FIX: Use correct variable names
  if(sessionData.receiver_resources && sessionData.receiver_resources.length > 0){
    connectionState.addRescheduleSessionResource(sessionData.receiver_resources, 'receiver_resources');
  }
  
  if(sessionData.requester_resources && sessionData.requester_resources.length > 0){
    connectionState.addRescheduleSessionResource(sessionData.requester_resources, 'requester_resources');
  }
  
  // ✅ FIX: Only render if containers exist
  if(previewArea2) {
    createResourcePreviews(sessionData.requester_resources || [], previewArea2, amRequester);
  }
  
  if(previewArea1) {
    createResourcePreviews(sessionData.receiver_resources || [], previewArea1, !amRequester); // ✅ Invert for receiver
  }
  
  // Set session ID
  document.getElementById("reschedule-study-session-modal").dataset.sessionId = parseInt(sessionData.id);
  
  // Set session ID
  
  // Populate session info summary
  document.getElementById('reschedule-session-title').textContent = sessionData.title;
  document.getElementById('reschedule-session-subject').textContent = `📚 ${sessionData.subject || 'No subject'}`;
  
  if (sessionData.partner) {
    document.getElementById('reschedule-session-partner').textContent = 
      `With: ${sessionData.partner.name} (@${sessionData.partner.username})`;
  }
  
  // Show current confirmed time if exists
  const currentTimeContainer = document.getElementById('reschedule-current-time-container');
  const currentTimeElement = document.getElementById('reschedule-current-time');
  
  if (sessionData.confirmed_time) {
    const confirmedDate = new Date(sessionData.confirmed_time);
    currentTimeElement.textContent = confirmedDate.toLocaleString('en-US', {
      weekday: 'long',
      year: 'numeric',
      month: 'long',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      hour12: true
    });
    currentTimeContainer.style.display = 'block';
  } else {
    currentTimeContainer.style.display = 'none';
  }
  
  // Populate form fields
  document.getElementById('reschedule-title').value = sessionData.title || '';
  document.getElementById('reschedule-subject').value = sessionData.subject || '';
  document.getElementById('reschedule-description').value = sessionData.description || '';
  document.getElementById('reschedule-duration').value = sessionData.duration_minutes || 60;
  document.getElementById('reschedule-notes').value = sessionData.requester_notes || '';
  
  // Populate proposed times
  const timesContainer = document.getElementById('reschedule-proposed-times-container');
  timesContainer.innerHTML = '';
  
  if (sessionData.proposed_times && sessionData.proposed_times.length > 0) {
    sessionData.proposed_times.forEach((time, index) => {
      const date = new Date(time);
      const formattedDate = date.toISOString().slice(0, 16); // Format for datetime-local input
      addRescheduleTimeSlot(formattedDate);
    });
  } else {
    // Add one empty time slot if none exist
    addRescheduleTimeSlot();
  }
  
  // Show modal
  openModal('reschedule-study-session-modal');
}

// Helper function to add time slot
export function addRescheduleTimeSlot(value = '') {
  const container = document.getElementById('reschedule-proposed-times-container');
  const index = container.children.length;
  
  const timeSlot = document.createElement('div');
  timeSlot.className = 'reschedule-time-slot';
  timeSlot.innerHTML = `
    <input 
      type="datetime-local" 
      class="reschedule-time-input" 
      data-field="proposed_time"
      data-index="${index}"
      value="${value}"
      required
    />
    <button 
      type="button" 
      class="reschedule-remove-time-btn" 
      data-action="remove-reschedule-time-slot"
      data-index="${index}">
      ❌
    </button>
  `;
  
  container.appendChild(timeSlot);
}

export async function submitRescheduleSession() {
  const modal = document.getElementById("reschedule-study-session-modal");
  const button = modal.querySelector('[data-action="submit-reschedule-study-session"]');
  
  if (!button) {
    return;
  }

  const originalText = button.textContent;
  const sessionData = getRescheduleFormData();
  
  if (!sessionData) {
    return;
  }

  try {
    button.disabled = true;
    button.textContent = "Rescheduling...";
    
    const response = await ConnectionAPI.rescheduleSession(sessionData, sessionData.session_id);
    
    if (response.status !== 'success') {
      showToast(response.message, 'error');
      return;
    }
    
    closeModal('reschedule-study-session-modal');
    resetRescheduleSessionForm();
    connectionState.rescheduleSessionResources = {
      requester_resources: [],
      receiver_resources: []
    };
    
    
  } catch (error) {
    console.error('Error rescheduling session:', error);
    showToast(`Error saving changes, ${error.message}`, 'error');
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}
export function resetRescheduleSessionForm() {
  // Clear text inputs
  document.getElementById('reschedule-title').value = '';
  document.getElementById('reschedule-subject').value = '';
  document.getElementById('reschedule-description').value = '';
  document.getElementById('reschedule-notes').value = '';
  
  // Reset duration to default
  document.getElementById('reschedule-duration').value = '60';
  
  // Clear session info display
  document.getElementById('reschedule-session-title').textContent = '';
  document.getElementById('reschedule-session-subject').textContent = '';
  document.getElementById('reschedule-session-partner').textContent = '';
  document.getElementById('reschedule-current-time').textContent = '';
  
  // Clear hidden session ID
  document.getElementById('reschedule-study-session-modal').dataset.sessionId = '';
  
  // Clear all proposed time slots
  const timesContainer = document.getElementById('reschedule-proposed-times-container');
  timesContainer.innerHTML = '';
  
}


// Helper function to get form data
function getRescheduleFormData() {
  
  try {
    const sessionId = document.getElementById("reschedule-study-session-modal").dataset.sessionId;
    const title = document.getElementById('reschedule-title').value.trim();
    const subject = document.getElementById('reschedule-subject').value.trim();
    const description = document.getElementById('reschedule-description').value.trim();
    const durationMinutes = parseInt(document.getElementById('reschedule-duration').value);
    const requesterNotes = document.getElementById('reschedule-notes').value.trim();
    const resources = connectionState.getRescheduleSessionResources();
    
    // Get all proposed times
    const timeInputs = document.querySelectorAll('.reschedule-time-input');
    const proposedTimes = Array.from(timeInputs)
      .map(input => input.value)
      .filter(value => value) // Remove empty values
      .map(value => new Date(value).toISOString());
    
    const data = {
      session_id: parseInt(sessionId),
      title: title,
      subject: subject,
      receiver_resources: resources['receiver_resources'], // Fixed: use string key
      requester_resources: resources['requester_resources'], // Fixed: use string key
      description: description,
      duration_minutes: durationMinutes,
      requester_notes: requesterNotes,
      proposed_times: proposedTimes
    };
    
    
    return data;
    
  } catch (error) {
    showToast(`Error getting form data: ${error.message}`, 'error');
    console.error('Error in getRescheduleFormData:', error);
    return null;
  }
}


/**
 * Enhanced Study Session Resource Preview Function
 * Shows resources in a larger, more visible modal
 */
export function showStudySessionResourcePreview(url, name, type) {
  const modal = document.getElementById('study-session-resource-preview-modal');
  const title = document.getElementById('study-session-resource-preview-title');
  const container = document.getElementById('study-session-resource-preview-container');
  
  // Set download URL
  const downloadBtn = modal.querySelector("[data-action='download-resource']");
  if (downloadBtn) {
    downloadBtn.dataset.url = url;
  }
  
  // Set title
  title.textContent = name;
  
  // Clear previous content
  container.innerHTML = '';
  
  // Add loading indicator
  container.innerHTML = '<div class="study-session-resource-loading">Loading resource...</div>';
  
  // Generate preview based on type
  setTimeout(() => {
    container.innerHTML = '';
    
    if (type === 'image') {
      const img = document.createElement('img');
      img.src = url;
      img.alt = name;
      img.className = 'study-session-resource-preview-image';
      img.style.cssText = `
        max-width: 100%;
        max-height: 75vh;
        width: auto;
        height: auto;
        object-fit: contain;
        display: block;
        margin: 0 auto;
      `;
      
      // Handle load error
      img.onerror = () => {
        container.innerHTML = `
          <div style="text-align: center; color: #6b7280; padding: 40px;">
            <p style="font-size: 18px; margin-bottom: 10px;">⚠️ Failed to load image</p>
            <p style="font-size: 14px;">The image could not be displayed.</p>
          </div>
        `;
      };
      
      container.appendChild(img);
      
    } else if (type === 'video') {
      const video = document.createElement('video');
      video.controls = true;
      video.className = 'study-session-resource-preview-video';
      video.style.cssText = `
        max-width: 100%;
        max-height: 75vh;
        width: auto;
        height: auto;
        display: block;
        margin: 0 auto;
      `;
      
      const source = document.createElement('source');
      source.src = url;
      source.type = 'video/mp4';
      video.appendChild(source);
      
      // Handle load error
      video.onerror = () => {
        container.innerHTML = `
          <div style="text-align: center; color: #6b7280; padding: 40px;">
            <p style="font-size: 18px; margin-bottom: 10px;">⚠️ Failed to load video</p>
            <p style="font-size: 14px;">Your browser may not support this video format.</p>
          </div>
        `;
      };
      
      container.appendChild(video);
      
    } else if (type === 'pdf' || type === 'document') {
      const iframe = document.createElement('iframe');
      iframe.src = url;
      iframe.className = 'study-session-resource-preview-iframe';
      iframe.style.cssText = `
        width: 100%;
        height: 75vh;
        min-height: 500px;
        border: none;
        display: block;
      `;
      
      // Handle load error
      iframe.onerror = () => {
        container.innerHTML = `
          <div style="text-align: center; color: #6b7280; padding: 40px;">
            <p style="font-size: 18px; margin-bottom: 10px;">⚠️ Failed to load document</p>
            <p style="font-size: 14px;">The document could not be displayed in preview.</p>
            <a href="${url}" target="_blank" style="color: #3b82f6; text-decoration: underline;">
              Click here to open in new tab
            </a>
          </div>
        `;
      };
      
      container.appendChild(iframe);
      
    } else {
      // Unknown type - provide download link
      container.innerHTML = `
        <div style="text-align: center; color: #6b7280; padding: 40px;">
          <p style="font-size: 18px; margin-bottom: 10px;">📄 Preview not available</p>
          <p style="font-size: 14px; margin-bottom: 20px;">This file type cannot be previewed.</p>
          <a href="${url}" download="${name}" 
             style="display: inline-block; padding: 10px 20px; background: #3b82f6; color: white; 
                    text-decoration: none; border-radius: 6px; font-weight: 500;">
            Download File
          </a>
        </div>
      `;
    }
  }, 100);
  
  // Open the modal
  openModal('study-session-resource-preview-modal');
}

/**
 * Alternative function with fullscreen toggle
 */
 /*
export function showStudySessionResourcePreviewEnhanced(url, name, type) {
  const modal = document.getElementById('study-session-resource-preview-modal');
  const title = document.getElementById('study-session-resource-preview-title');
  const container = document.getElementById('study-session-resource-preview-container');
  
  // Set download URL
  const downloadBtn = modal.querySelector("[data-action='download-resource']");
  if (downloadBtn) {
    downloadBtn.dataset.url = url;
  }
  
  // Set title
  title.textContent = name;
  
  // Clear previous content
  container.innerHTML = '';
  
  // Add fullscreen toggle button to header (optional)
  const header = modal.querySelector('.study-session-modal-header');
  let fullscreenBtn = header.querySelector('.btn-fullscreen-toggle');
  
  if (!fullscreenBtn) {
    fullscreenBtn = document.createElement('button');
    fullscreenBtn.className = 'btn-fullscreen-toggle';
    fullscreenBtn.innerHTML = '⛶';
    fullscreenBtn.style.cssText = `
      background: none;
      border: none;
      font-size: 20px;
      cursor: pointer;
      padding: 6px 10px;
      margin-left: 10px;
      border-radius: 6px;
      transition: background 0.2s;
    `;
    fullscreenBtn.title = 'Toggle fullscreen';
    
    fullscreenBtn.addEventListener('click', () => {
      container.classList.toggle('fullscreen-mode');
      fullscreenBtn.innerHTML = container.classList.contains('fullscreen-mode') ? '⛶' : '⛶';
    });
    
    const closeBtn = header.querySelector('.study-session-modal-close-btn');
    header.insertBefore(fullscreenBtn, closeBtn);
  }
  
  // Generate preview (same as above)
  showStudySessionResourcePreview(url, name, type);
}
*/

export function closeStudySessionResourcePreview() {
  const modal = document.getElementById('study-session-resource-preview-modal');
  const container = document.getElementById('study-session-resource-preview-container');
  
  // Stop any playing videos
  const videos = container.querySelectorAll('video');
  videos.forEach(video => video.pause());
  
  closeModal('study-session-resource-preview-modal');
  container.innerHTML = '';
}
export function openStudySessionModal(userId) {
  connectionState.setCurrentSessionPartner(userId);
  openModal('create-study-session-modal');
  
  // Reset form
  resetCreateSessionForm();
  
  // Add first time slot with current date/time
  const container = document.getElementById('timeSlotsContainer');
  if (container) {
    const now = new Date();
    now.setHours(now.getHours() + 1); // Default to 1 hour from now
    const dateTimeString = now.toISOString().slice(0, 16);
    
    container.innerHTML = `
      <div class="time-slot">
        <input type="datetime-local" class="form-input time-slot-input" value="${dateTimeString}">
        <button class="btn-remove-time" data-action="remove-time-slot" style="visibility: hidden;">Remove</button>
      </div>
    `;
  }
}

// ============================================================================
// ADD TIME SLOT
// ============================================================================
export function addTimeSlot() {
  const container = document.getElementById('timeSlotsContainer');
  if (!container) return;
  
  const slots = container.querySelectorAll('.time-slot');
  if (slots.length >= 5) {
    showToast('Maximum 5 time slots allowed', 'warning');
    return;
  }
  
  const newSlot = document.createElement('div');
  newSlot.className = 'time-slot';
  newSlot.innerHTML = `
    <input type="datetime-local" class="form-input time-slot-input">
    <button class="btn-remove-time" data-action="remove-time-slot">Remove</button>
  `;
  
  container.appendChild(newSlot);
  
  // Show remove button on all slots if more than one
  updateRemoveButtons();
}

// ============================================================================
// REMOVE TIME SLOT
// ============================================================================
export function removeTimeSlot(target) {
  const slot = target.closest('.time-slot');
  if (!slot) return;
  
  const container = document.getElementById('timeSlotsContainer');
  const slots = container.querySelectorAll('.time-slot');
  
  if (slots.length <= 1) {
    showToast('At least one time slot required', 'warning');
    return;
  }
  
  slot.remove();
  updateRemoveButtons();
}

// ============================================================================
// UPDATE REMOVE BUTTONS VISIBILITY
// ============================================================================
function updateRemoveButtons() {
  const container = document.getElementById('timeSlotsContainer');
  if (!container) return;
  
  const slots = container.querySelectorAll('.time-slot');
  const removeButtons = container.querySelectorAll('.btn-remove-time');
  
  removeButtons.forEach(btn => {
    btn.style.visibility = slots.length > 1 ? 'visible' : 'hidden';
  });
}



// ============================================================================
// CREATE STUDY SESSION
// ============================================================================
export async function handleCreateStudySession() {
  try {
    // Get form values
    const title = document.getElementById('sessionTitle')?.value.trim();
    const subject = document.getElementById('sessionSubject')?.value.trim();
    const description = document.getElementById('sessionDescription')?.value.trim();
    const duration = parseInt(document.getElementById('sessionDuration')?.value);
    const notes = document.getElementById('sessionNotes')?.value.trim();
    
    // Validation
    if (!title) {
      showToast('Please enter a session title', 'error');
      return;
    }
    
    // Get all time slots
    const timeInputs = document.querySelectorAll('.time-slot-input');
    const proposedTimes = Array.from(timeInputs)
      .map(input => input.value)
      .filter(value => value !== '');
    
    if (proposedTimes.length === 0) {
      showToast('Please propose at least one time slot', 'error');
      return;
    }
    
    // Convert to ISO format
    const proposedTimesISO = proposedTimes.map(t => new Date(t).toISOString());
    const resources = ConnectionState.getCreateSessionResources();
    
    const partnerId = connectionState.getCurrentSessionPartner();
    if (!partnerId) {
      showToast('Invalid partner selection', 'error');
      return;
    }
    
    // Prepare session data
    const sessionData = {
      receiver_id: partnerId,
      title: title,
      subject: subject,
      resources:resources,
      description: description,
      duration_minutes: duration,
      proposed_times: proposedTimesISO,
      requester_notes: notes
    };
    
    // Show loading
    const submitBtn = document.getElementById('createSessionBtn');
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = 'Creating...';
    }
    
    // Create session
    const response = await ConnectionAPI.createStudySession(sessionData);
    
    if (response.status === 'success') {
      closeModal('create-study-session-modal');
      resetCreateSessionForm();
    } else {
      showToast(response.message || 'Failed to create study session', 'error');
    }
    
  } catch (error) {
    console.error('Create study session error:', error);
    showToast('Failed to create study session', 'error');
  } finally {
    const submitBtn = document.getElementById('createSessionBtn');
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Send Request';
    }
  }
}

function resetCreateSessionForm() {
  document.getElementById('sessionTitle').value = '';
  document.getElementById('sessionSubject').value = '';
  document.getElementById('sessionDescription').value = '';
  document.getElementById('sessionDuration').value = '60';
  document.getElementById('sessionNotes').value = '';
  document.getElementById("create-session-resource-preview-container").innerHTML = '';
  connectionState.createSessionResources = [];
  
  const container = document.getElementById('timeSlotsContainer');
  if (container) {
    container.innerHTML = `
      <div class="time-slot">
        <input type="datetime-local" class="form-input time-slot-input">
        <button class="btn-remove-time" data-action="remove-time-slot" style="visibility: hidden;">Remove</button>
      </div>
    `;
  }
}

// ============================================================================
// VIEW STUDY SESSIONS
// ============================================================================
export async function withdrawStudySession(sessionId) {
  try {
    const response = await ConnectionAPI.declineStudySession(sessionId, reason, true);
    
    if (response.status !== 'success') {
      showToast(response.message, 'error');
      return;
    }
    
    const userId = connectionState.getCurrentSessionPartner();
    showStudySessions(userId);
  } catch (error) {
    console.error('Withdraw study session error:', error);
    showToast("Error encountered while withdrawing from study session"+ error, 'error');
  }
}
export async function showStudySessions(userId) {
  try {
    openModal('viewSessionsModal');
    connectionState.setCurrentSessionPartner(userId);
    
    const modal = document.getElementById('viewSessionsModal');
    const sessionsList = document.getElementById('sessionsList');
    
    if (!sessionsList) {
      console.error('Sessions list container not found');
      return;
    }
    
    // Show loading
    sessionsList.innerHTML = '<div class="loader">Loading sessions...</div>';
    
    // Get active filter
    const activeFilter = modal.querySelector('.filter-chip.active');
    const filter = activeFilter?.dataset.filter || 'all';
    
    // Fetch sessions
    const response = await ConnectionAPI.getStudySessions(userId, filter);
    
    if (!response.data || !response.data.sessions || response.data.sessions.length === 0) {
      sessionsList.innerHTML = `
        <div class="empty-state">
          <h3>No study sessions found</h3>
          <p>Schedule a new study session to get started</p>
        </div>
      `;
      return;
    }
    
    // Render sessions
    sessionsList.innerHTML = response.data.sessions
      .map(session => createStudySessionCard(session))
      .join('');
      
  } catch (error) {
    console.error('Show study sessions error:', error);
    const sessionsList = document.getElementById('sessionsList');
    if (sessionsList) {
      sessionsList.innerHTML = `
        <div class="empty-state">
          <h3>Error loading sessions</h3>
          <p>Please try again later</p>
        </div>
      `;
    }
    showToast('Failed to load study sessions', 'error');
  }
}
function createStudySessionCard(session) {
  const statusClass = {
    'pending': 'status-pending',
    'confirmed': 'status-confirmed',
    'rescheduled': 'status-rescheduled',
    'completed': 'status-completed',
    'cancelled': 'status-cancelled',
    'declined': 'status-declined'
  }[session.status] || '';
  
  const statusIcon = {
    'pending': '⏳',
    'confirmed': '✅',
    'rescheduled': '🔄',
    'completed': '✔️',
    'cancelled': '❌',
    'declined': '🚫'
  }[session.status] || '❓';
  
  // Format proposed times
  const proposedTimesHTML = session.proposed_times && session.proposed_times.length > 0
    ? session.proposed_times.length === 1
        ? // Single time - no radio button
          (() => {
            const date = new Date(session.proposed_times[0]);
            return `<div class="proposed-time">${date.toLocaleString('en-US', {
              weekday: 'short',
              month: 'short',
              day: 'numeric',
              hour: 'numeric',
              minute: '2-digit',
              hour12: true
            })}</div>`;
          })()
        : // Multiple times - add radio buttons
          session.proposed_times
            .map((time, index) => {
              const date = new Date(time);
              return `
                <div class="proposed-time">
                  <label>
                    <input type="radio" name="selected-time-${session.id}" value="${time}" data-index="${index}">
                    ${date.toLocaleString('en-US', {
                      weekday: 'short',
                      month: 'short',
                      day: 'numeric',
                      hour: 'numeric',
                      minute: '2-digit',
                      hour12: true
                    })}
                  </label>
                </div>`;
            })
            .join('')
    : '<div class="no-times">No times proposed</div>';
  
  // Format resources
  const renderResources = (resources, label) => {
  if (!resources || resources.length === 0) return '';
  
  // Get resource icon and type
  const getResourceIcon = (type) => {
    const icons = {
      'image': '🖼️',
      'video': '🎥',
      'document': '📄',
      'pdf': '📕',
      'default': '📎'
    };
    return icons[type?.toLowerCase()] || icons.default;
  };
  
  // Determine if resource can be previewed
  const canPreview = (type) => {
    return ['image', 'video', 'pdf'].includes(type?.toLowerCase());
  };
  
  // Handle both string URLs and resource objects
  const resourceItems = resources.map((resource, index) => {
    let url, name, type;
    
    if (typeof resource === 'string') {
      // Simple URL string
      url = resource;
      name = 'View Resource';
      type = 'default';
    } else {
      // Resource object
      url = resource.url;
      name = resource.name || 'View Resource';
      type = resource.resource_type || resource.type || 'default';
    }
    
    const icon = getResourceIcon(type);
    const preview = canPreview(type);
    
    return `
      <div class="study-session-resource-item">
        ${preview ? `
          <button 
            class="study-session-resource-preview-btn" 
            data-action="preview-study-session-resource"
            data-url="${url}"
            data-name="${name}"
            data-type="${type}"
            title="Preview ${name}">
            ${icon} ${name}
          </button>
        ` : `
          <a href="${url}" target="_blank" class="study-session-resource-link" title="${name}">
            ${icon} ${name}
          </a>
        `}
      </div>
    `;
  }).join('');
  
  return `
    <div class="detail-item study-session-resources-section">
      <strong>${label}:</strong>
      <div class="study-session-resources-list">
        ${resourceItems}
      </div>
    </div>
  `;
};

  // Format time until session
  const getTimeUntilText = () => {
    if (!session.minutes_until) return '';
    
    if (session.minutes_until < 0) {
      return '<span class="time-passed">⏰ Time has passed</span>';
    }
    
    if (session.is_soon) {
      return `<span class="time-soon">⚡ Starting in ${session.minutes_until} minutes!</span>`;
    }
    
    if (session.minutes_until < 1440) { // Less than 24 hours
      const hours = Math.floor(session.minutes_until / 60);
      const minutes = session.minutes_until % 60;
      return `<span class="time-upcoming">⏰ In ${hours}h ${minutes}m</span>`;
    }
    
    const days = Math.floor(session.minutes_until / 1440);
    return `<span class="time-future">📅 In ${days} day${days > 1 ? 's' : ''}</span>`;
  };
  
  // Determine available actions based on backend flags
  const canConfirm = session.can_confirm || false; // Receiver can confirm pending/rescheduled
  const canReschedule = session.can_reschedule || false; // Requester can reschedule pending/confirmed
  const canCancel = session.can_cancel || false; // Requester can cancel pending/confirmed/rescheduled
  const canDecline = session.can_decline || false; // Receiver can decline pending/rescheduled
  
  // Determine if user can withdraw (receiver withdrawing from confirmed session)
  const canWithdraw = !session.am_requester && session.status === 'confirmed';
  
  // Build action buttons
  let actionButtonsHTML = '';
  
  if (canConfirm) {
    actionButtonsHTML += `
      <button class="btn btn-primary btn-confirm" 
              data-action="confirm-study-session" 
              data-session-id="${session.id}">
        ✅ Confirm Session
      </button>`;
  }
  
  if (canWithdraw) {
    actionButtonsHTML += `
      <button class="btn btn-warning btn-withdraw" 
              data-action="withdraw-study-session" 
              data-session-id="${session.id}">
        🔙 Withdraw
      </button>`;
  }
  
  if (canReschedule) {
    actionButtonsHTML += `
      <button class="btn btn-secondary btn-reschedule" 
              data-action="reschedule-study-session" 
              data-session-id="${session.id}">
        🔄 Reschedule
      </button>`;
  }
  
  if (canDecline) {
    actionButtonsHTML += `
      <button class="btn btn-danger btn-decline" 
              data-action="decline-study-session" 
              data-session-id="${session.id}">
        🚫 Decline
      </button>`;
  }
  
  if (canCancel) {
    actionButtonsHTML += `
      <button class="btn btn-danger btn-cancel" 
              data-action="cancel-study-session" 
              data-session-id="${session.id}">
        ❌ Cancel
      </button>`;
  }
  
  return `
    <div class="session-card ${statusClass}" data-session-id="${session.id}" data-status="${session.status}">
      <div class="session-header">
        <div class="session-info">
          <div class="session-title-row">
            <h3 class="session-title">${session.title}</h3>
            ${session.is_upcoming ? '<span class="badge badge-upcoming">Upcoming</span>' : ''}
            ${session.is_soon ? '<span class="badge badge-soon">Starting Soon!</span>' : ''}
          </div>
          <p class="session-subject">
            📚 ${session.subject || 'No subject specified'}
          </p>
        </div>
        <div class="session-status">
          <span class="status-badge ${statusClass}">${statusIcon} ${session.status.toUpperCase()}</span>
        </div>
      </div>
      
      <!-- Partner Info -->
      ${session.partner ? `
        <div class="session-partner">
          <img src="${session.partner.avatar || '/static/images/default-avatar.png'}" 
               alt="${session.partner.name}" 
               class="partner-avatar">
          <div class="partner-info">
            <strong>${session.am_requester ? 'With' : 'From'}: ${session.partner.name}</strong>
            <span class="partner-username">@${session.partner.username}</span>
          </div>
        </div>
      ` : ''}
      
      <!-- Description -->
      ${session.description ? `
        <div class="session-description">
          <p>${session.description}</p>
        </div>
      ` : ''}
      
      <!-- Session Details -->
      <div class="session-details">
        <!-- Duration -->
        <div class="detail-item">
          <strong>⏱️ Duration:</strong> ${session.duration_minutes} minutes
        </div>
        
        <!-- Confirmed Time or Proposed Times -->
        ${session.confirmed_time ? `
          <div class="detail-item confirmed-time">
            <strong>🗓️ Scheduled:</strong> 
            <span class="time-display">
              ${new Date(session.confirmed_time).toLocaleString('en-US', {
                weekday: 'long',
                year: 'numeric',
                month: 'long',
                day: 'numeric',
                hour: 'numeric',
                minute: '2-digit',
                hour12: true
              })}
            </span>
            ${getTimeUntilText()}
          </div>
        ` : `
          <div class="detail-item proposed-times-section">
            <strong>🗓️ Proposed Times:</strong>
            <div class="proposed-times-list">
              ${proposedTimesHTML}
            </div>
          </div>
        `}
        
        <!-- Created At -->
        <div class="detail-item">
          <strong>📅 Created:</strong> ${new Date(session.created_at).toLocaleDateString('en-US', {
            month: 'short',
            day: 'numeric',
            year: 'numeric'
          })}
        </div>
        
        <!-- Confirmed At (if applicable) -->
        ${session.confirmed_at ? `
          <div class="detail-item">
            <strong>✅ Confirmed:</strong> ${new Date(session.confirmed_at).toLocaleDateString('en-US', {
              month: 'short',
              day: 'numeric',
              year: 'numeric'
            })}
          </div>
        ` : ''}
        
        <!-- Requester Notes -->
        ${session.requester_notes ? `
          <div class="detail-item notes-section">
            <strong>📝 ${session.am_requester ? 'Your' : 'Their'} Notes:</strong>
            <p class="notes-content">${session.requester_notes}</p>
          </div>
        ` : ''}
        
        <!-- Receiver Notes -->
        ${session.receiver_notes ? `
          <div class="detail-item notes-section">
            <strong>📝 ${session.am_requester ? 'Their' : 'Your'} Response:</strong>
            <p class="notes-content">${session.receiver_notes}</p>
          </div>
        ` : ''}
        
        <!-- Requester Resources -->
        ${renderResources(session.requester_resources, `📎 ${session.am_requester ? 'Your' : 'Their'} Resources`)}
        
        <!-- Receiver Resources -->
        ${renderResources(session.receiver_resources, `📎 ${session.am_requester ? 'Their' : 'Your'} Resources`)}
      </div>
      <!-- Cancel Reason (if session was cancelled by requester) -->
${session.cancel_reason && session.status === 'cancelled' ? `
  <div class="detail-item study-session-reason-section study-session-cancelled-reason">
    <strong>❌ Cancellation Reason:</strong>
    <p class="study-session-reason-content">${session.cancel_reason}</p>
  </div>
` : ''}

<!-- Decline Reason (if session was declined by receiver) -->
${session.decline_reason && session.status === 'declined' ? `
  <div class="detail-item study-session-reason-section study-session-declined-reason">
    <strong>🚫 Decline Reason:</strong>
    <p class="study-session-reason-content">${session.decline_reason}</p>
  </div>
` : ''}
      <!-- Session Actions -->
      ${actionButtonsHTML ? `
        <div class="session-actions">
          ${actionButtonsHTML}
        </div>
      ` : ''}
      
      <!-- Role Badge -->
      <div class="session-footer">
        <span class="role-badge">
          ${session.am_requester ? '📤 You requested this session' : '📥 You received this request'}
        </span>
      </div>
    </div>
  `;
}

export async function saveSessionChanges() {
  const modal = document.getElementById('sessionDetailsModal');
  const sessionId = connectionState.getCurrentSessionEdit();
  
  
  // Get basic fields
  const title = document.getElementById('modal-session-title').textContent.trim();
  const subject = document.getElementById('modal-session-subject').textContent.trim();
  const description = document.getElementById('modal-session-description').textContent.trim();
  const duration_minutes = parseInt(document.getElementById('modal-session-duration').value) || 30;
  const requester_notes = document.getElementById('modal-session-notes').value.trim();
  const resources = ConnectionState.getRescheduleSessionResources();
  
  // Get selected time (if multiple times exist)
  const selectedTimeInput = document.querySelector(`input[name="selected-time-${sessionId}"]:checked`);
  const selectedTime = selectedTimeInput ? selectedTimeInput.value : null;
  
  // Get all proposed times (you can add functionality to add/remove times)
  const proposedTimes = [];
  document.querySelectorAll(`input[name="selected-time-${sessionId}"]`).forEach(radio => {
    proposedTimes.push(radio.value);
  });
  
  // If only one time and no radio button, get it from the container
  if (proposedTimes.length === 0) {
    const singleTimeElement = document.querySelector('#modal-proposed-times .proposed-time');
    if (singleTimeElement && singleTimeElement.dataset.time) {
      proposedTimes.push(singleTimeElement.dataset.time);
    }
  }
  
  // Build JSON array
  const sessionData = {
    session_id: parseInt(sessionId),
    title: title,
    subject: subject,
    requester_resources: resources.requester_resources,
    receiver_resources: resources.receiver_resources,
    
    description: description,
    duration_minutes: duration_minutes,
    requester_notes: requester_notes,
    proposed_times: proposedTimes,
    selected_time: selectedTime // For confirmation
  };
  try{
    const response = await ConnectionAPI.sendSessionChanges(sessionData, sessionId);
    if(response.status == 'success'){
      resetEditSessionModal();
      return;
    }
    showToast(response.message, 'error');
  }
  catch(error){
    showToast(`Error saving session changes ${error.message}`, 'error');
  }
  
}



// ============================================================================
// CONFIRM STUDY SESSION
// ============================================================================


export async function cancelStudySession(sessionId) {
  closeModal("viewSessionsModal");
  openModal('cancel-session-reason-modal');
  document.getElementById("cancel-session-reason-modal").dataset.sessionId = sessionId;
}
export function declineStudySession(sessionId) {
  closeModal("viewSessionsModal");
  openModal('decline-session-reason-modal');
  document.getElementById("decline-session-reason-modal").dataset.sessionId = sessionId;
  
}
export async function submitDeclineStudySession() {
  const modal = document.getElementById("decline-session-reason-modal");
  const sessionId = modal.dataset.sessionId;
  if(!sessionId){
    return;
  }
  const reason = modal.querySelector('input').value;
  try {
    const response = await ConnectionAPI.declineStudySession(sessionId, reason, false);
    
    if (response.status === 'success') {
      // Refresh the sessions list
      const userId = connectionState.getCurrentSessionPartner();
      if (userId) {
        showStudySessions(userId);
        modal.dataset.sessionId = '';
      }
    } else {
      showToast(response.message || 'Failed to decline session', 'error');
    }
  } catch (error) {
    console.error('Decline session error:', error);
    showToast('Failed to decline session', 'error');
  }
}

export async function confirmStudySession(sessionId) {
  let selectedTime = null;
  selectedTime = document.querySelector(`input[name="selected-time-${sessionId}"]:checked`)?.value;
  if(!selectedTime){
    return;
  }
  try {
    // For now, just confirm with the first proposed time
    // In production, you'd let user choose which time
    const response = await ConnectionAPI.confirmStudySession(sessionId, selectedTime);
    
    if (response.status === 'success') {
      // Refresh the sessions list
      const userId = connectionState.getCurrentSessionPartner();
      if (userId) {
        showStudySessions(userId);
      }
    } else {
      showToast(response.message || 'Failed to confirm session', 'error');
    }
  } catch (error) {
    console.error('Confirm session error:', error);
    showToast('Failed to confirm session', 'error');
  }
}

// ============================================================================
// CANCEL STUDY SESSION
// ============================================================================
export async function submitCancelStudySession() {
  const modal = document.getElementById("cancel-session-reason-modal");
  const reason = modal.querySelector("input").value || '';
  const sessionId = modal.dataset.sessionId;
  
  try {
    const response = await ConnectionAPI.cancelStudySession(sessionId, reason);
    
    if (response.status === 'success') {
      closeModal("cancel-session-reason-modal");
      
      // Refresh the sessions list
      const userId = connectionState.getCurrentSessionPartner();
      if (userId) {
        showStudySessions(userId);
      }
    } else {
      showToast(response.message || 'Failed to cancel session', 'error');
    }
  } catch (error) {
    console.error('Cancel session error:', error);
    showToast(response.message, 'error');
  }
}

// ============================================================================
// FILTER SESSIONS
// ============================================================================

export function filterSessions(target) {
  const filter = target.dataset.filter;
  
  // Update active filter button
  const modal = document.getElementById('viewSessionsModal');
  modal.querySelectorAll('.filter-chip').forEach(btn => btn.classList.remove('active'));
  target.classList.add('active');
  
  // Reload sessions with new filter
  const userId = connectionState.getCurrentSessionPartner();
  if (userId) {
    showStudySessions(userId);
  
  }
}

export async function sendConnectionRequest(userId, message = null, button = null) {
  // Store original button state if button provided
  let originalText = '';
  if (button) {
    button.disabled = true;
    originalText = button.textContent;
    button.textContent = 'Sending...';
  }
  
  try {
    const response = await ConnectionAPI.sendConnectionRequest(parseInt(userId), message);
    
    if (response.status === 'success') {
      // Handle instant connection
      if (response.data.is_instant) {
        showToast('🎉 Instant connection! You are now connected.', 'success');
        
        if (button) {
          button.textContent = 'Connected';
          button.classList.remove('btn-primary');
          button.classList.add('btn-secondary');
        }
        
        // Move card from suggestions to connected
        const card = button?.closest('.connection-card');
        if (card) {
          setTimeout(() => card.remove(), 1500);
        }
      } 
      // Handle regular connection request
      else {
        
        if (button) {
          button.textContent = 'Request Sent';
        }
        showToast("Request sent");
        
        // Move to sent tab
        const card = button?.closest('.connection-card');
        if (card) {
          setTimeout(() => card.remove(), 1500);
        }
        const card2 = target.closest('.user-card')
        if (card2) {
          setTimeout(() => card.remove(), 1500);
        }
      }
      
      // Refresh current tab
      const currentTab = connectionState.getCurrentTab();
      await loadAllConnectionData();
      renderConnectionTab(currentTab);
      
      return response;
    } else {
      showToast(response.message || 'Failed to send request', 'error');
      
      // Restore button state
      if (button) {
        button.disabled = false;
        button.textContent = originalText;
      }
      
      return response;
    }
  } catch (error) {
    
    
    // Restore button state
    if (button) {
      button.disabled = false;
      button.textContent = originalText;
    }
    
  
  }
}




export async function acceptRequest(connectionId, target) {
  target.disabled = true;
  const originalText = target.textContent;
  target.textContent = 'Accepting...';
  
  try {
    const response = await ConnectionAPI.acceptRequest(connectionId);
    
    if (response.status === 'success') {
      
      // Remove card with animation
      const card = target.closest('.connection-card');
      if (card) {
        card.style.opacity = '0';
        setTimeout(() => card.remove(), 300);
      }
    } else {
      showToast(response.message || 'Failed to accept', 'error');
      target.disabled = false;
      target.textContent = originalText;
    }
  } catch (error) {
    console.error('Accept request error:', error);
    showToast('Failed to accept request', 'error');
    target.disabled = false;
    target.textContent = originalText;
  }
}

export async function rejectRequest(connectionId, target) {
  target.disabled = true;
  const originalText = target.textContent;
  target.textContent = 'Rejecting...';
  
  try {
    const response = await ConnectionAPI.rejectRequest(connectionId);
    
    if (response.status === 'success') {
      
      const card = target.closest('.connection-card');
      if (card) {
        card.style.opacity = '0';
        setTimeout(() => card.remove(), 300);
      }
    } else {
      showToast(response.message || 'Failed to reject', 'error');
      target.disabled = false;
      target.textContent = originalText;
    }
  } catch (error) {
    console.error('Reject request error:', error);
    showToast('Failed to reject request', 'error');
    target.disabled = false;
    target.textContent = originalText;
  }
}

export async function cancelRequest(connectionId, target) {
  target.disabled = true;
  const originalText = target.textContent;
  target.textContent = 'Cancelling...';
  
  try {
    const response = await ConnectionAPI.cancelRequest(connectionId);
    
    if (response.status === 'success') {
      
      const card = target.closest('.connection-card');
      if (card) {
        card.style.opacity = '0';
        setTimeout(() => card.remove(), 300);
      }
    } else {
      showToast(response.message || 'Failed to cancel', 'error');
      target.disabled = false;
      target.textContent = originalText;
    }
  } catch (error) {
    console.error('Cancel request error:', error);
    showToast('Failed to cancel request', 'error');
    target.disabled = false;
    target.textContent = originalText;
  }
}

// ============================================================================
// BLOCK/UNBLOCK
// ============================================================================



// ============================================================================
// BATCH 2: REMOVE CONNECTION ("unfriend")
// ============================================================================

export async function removeConnectionRequest(userId, target) {
  if (!confirm('Remove this connection? You can send a new request later if you change your mind.')) return;

  target.disabled = true;
  const originalText = target.textContent;
  target.textContent = 'Removing...';

  try {
    const response = await ConnectionAPI.removeConnection(userId);

    if (response.status === 'success') {
      showToast('Connection removed', 'success');

      const card = target.closest('.connection-card');
      if (card) {
        card.style.opacity = '0';
        setTimeout(() => card.remove(), 300);
      }
    } else {
      showToast(response.message || 'Failed to remove connection', 'error');
      target.disabled = false;
      target.textContent = originalText;
    }
  } catch (error) {
    console.error('Remove connection error:', error);
    showToast('Failed to remove connection', 'error');
    target.disabled = false;
    target.textContent = originalText;
  }
}

export async function blockRequest(userId, target) {
  if (!confirm('Are you sure you want to block this user?')) return;
  
  try {
    const response = await ConnectionAPI.blockUser(userId);
    
    if (response.status === 'success') {
      showToast('User blocked', 'success');
      
      const card = target.closest('.connection-card');
      if (card) {
        card.style.opacity = '0';
        setTimeout(() => card.remove(), 300);
      }
    } else {
      showToast(response.message || 'Failed to block user', 'error');
    }
  } catch (error) {
    console.error('Block user error:', error);
    showToast('Failed to block user', 'error');
  }
}

export async function unblockRequest(userId, target) {
  target.disabled = true;
  const originalText = target.textContent;
  target.textContent = 'Unblocking...';
  
  try {
    const response = await ConnectionAPI.unblockUser(userId);
    
    if (response.status === 'success') {
      
      const card = target.closest('.connection-card');
      if (card) {
        card.style.opacity = '0';
        setTimeout(() => card.remove(), 300);
      }
    } else {
      showToast(response.message || 'Failed to unblock', 'error');
      target.disabled = false;
      target.textContent = originalText;
    }
  } catch (error) {
    console.error('Unblock user error:', error);
    showToast('Failed to unblock user', 'error');
    target.disabled = false;
    target.textContent = originalText;
  }
}

export async function viewBlockedUsers() {
  const modal = connectionContainer.querySelector('.blocked-users-modal');
  if (!modal) {
    console.error('Blocked users modal not found');
    return;
  }
  
  const contentContainer = modal.querySelector('.modal-content');
  contentContainer.innerHTML = getLoadingSkeleton();
  
  modal.classList.remove('hidden');
  
  try {
    const blockedUsers = await ConnectionAPI.getBlockedUsers();
    
    if (!blockedUsers || blockedUsers.length === 0) {
      contentContainer.innerHTML = `
        <div class="empty-state">
          <p>No blocked users</p>
        </div>
      `;
      return;
    }
    
    contentContainer.innerHTML = blockedUsers.map(user => createBlockedUserCard(user)).join('');
  } catch (error) {
    console.error('View blocked users error:', error);
    contentContainer.innerHTML = `
      <div class="empty-state">
        <p>Failed to load blocked users</p>
      </div>
    `;
  }
}

// ============================================================================
// MUTUAL CONNECTIONS
// ============================================================================

export async function showMutualConnections(userId) {
  const modal = document.getElementById('mutual-connections-modal');;
  if (!modal) {
    console.error('Mutual connections modal not found');
    return;
  }
  
  const contentContainer = modal.querySelector('.modal-content');
  contentContainer.innerHTML = getLoadingSkeleton();
  
  openModal('mutual-connections-modal');
  
  try {
    const response = await ConnectionAPI.getMutualConnections(userId);
    
    if (!response.data.mutual_connections || response.data.mutual_connections.length === 0) {
      contentContainer.innerHTML = `
        <div class="empty-state">
          <p>No mutual connections</p>
        </div>
      `;
      return;
    }
  
    
    contentContainer.innerHTML = response.data.mutual_connections
      .map(connection => createMutualConnectionCard(connection))
      .join('');
  } catch (error) {
    console.error('Show mutual connections error:', error);
    showToast(error.message, 'error');
    
    contentContainer.innerHTML = `
      <div class="empty-state">
        <p>Failed to load mutual connections</p>
      </div>
    `;
  }
  finally{
    closeModal('userActionsModal');
  }
}
export async function viewUserOverview(userId) {
  const modal = connectionContainer.querySelector('.user-overview-modal');
  if (!modal) {
    console.error('User overview modal not found');
    return;
  }
  
  modal.classList.remove('hidden');
  
  const loadingSection = modal.querySelector('#loading-section');
  const aiSection = modal.querySelector('#ai-section');
  const aiText = modal.querySelector('#ai-text');
  const streamingIndicator = modal.querySelector('#streaming-indicator');
  const errorContainer = modal.querySelector('#error-container');
  
  // Reset state
  loadingSection.style.display = 'block';
  aiSection.style.display = 'none';
  aiText.textContent = '';
  errorContainer.innerHTML = '';
  streamingIndicator.style.display = 'inline-block';
  
  let fullResponse = '';
  
  try {
    const response = await ConnectionAPI.getOverviewStream(userId);
    
    if (!response.ok) {
      throw new Error('Failed to connect');
    }
    
    
    
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    
    // Show AI section
    loadingSection.style.display = 'none';
    aiSection.style.display = 'block';
    
    function readStream() {
      reader.read().then(({ done, value }) => {
        if (done) {
          streamingIndicator.style.display = 'none';
          return;
        }
        
        const chunk = decoder.decode(value, { stream: true });
        const lines = chunk.split('\n');
        
        lines.forEach(line => {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));
              
              // Handle content streaming
              if (data.content) {
                fullResponse += data.content;
                aiText.textContent = fullResponse;
              }
              
              // Handle completion
              if (data.type === 'done') {
                streamingIndicator.style.display = 'none';
                
                if (data.already_connected) {
                  const successMessage = modal.querySelector('#success-message');
                  if (successMessage) {
                    successMessage.innerHTML = '<div class="success-message">✓ You are already connected with this user</div>';
                  }
                }
              }
              
              // Handle errors
              if (data.error) {
                showError(data.error);
              }
            } catch (e) {
              console.error('Parse error:', e, line);
            }
          }
        });
        
        readStream();
      }).catch(err => {
        console.error('Stream error:', err);
        showError('Connection lost. Please try again.');
      });
    }
    
    readStream();
  } catch (error) {
    console.error('View overview error:', error);
    showError('Failed to load connection overview. Please try again.');
  }
  function showError(message) {
    errorContainer.innerHTML = `
      <div class="error-message">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="10"></circle>
          <line x1="12" y1="8" x2="12" y2="12"></line>
          <line x1="12" y1="16" x2="12.01" y2="16"></line>
        </svg>
        <span>${message}</span>
      </div>
    `;
    loadingSection.style.display = 'none';
  }
}

// ============================================================================
// SEARCH
// ============================================================================

let searchTimeout = null;

export async function performConnectionSearch(query) {
  const searchResults = connectionContainer.querySelector('#connections-search-results');
  if (!searchResults) return;
  
  if (!query || query.length === 0) {
    searchResults.classList.add('hidden');
    searchResults.innerHTML = '';
    return;
  }
  
  searchResults.innerHTML = getLoadingSkeleton(2);
  searchResults.classList.remove('hidden');
  
  try {
    const results = await ConnectionAPI.searchConnections(query);
    
    if (!results.users || results.users.length === 0) {
      searchResults.innerHTML = `
        <div class="empty-state">
          <p>No results found for "${query}"</p>
        </div>
      `;
      return;
    }
    
    searchResults.innerHTML = results.users
      .map(user => createSearchResultCard(user))
      .join('');
  } catch (error) {
    console.error('Search error:', error);
    searchResults.innerHTML = `
      <div class="empty-state">
        <p>Search failed. Please try again.</p>
      </div>
    `;
  }
}

export function clearConnectionSearchInput() {
  const searchInput = connectionContainer.querySelector('#connections-search-input');
  const searchResults = connectionContainer.querySelector('#connections-search-results');
  const clearBtn = connectionContainer.querySelector('#search-clear-btn');
  
  if (searchInput) searchInput.value = '';
  if (searchResults) {
    searchResults.classList.add('hidden');
    searchResults.innerHTML = '';
  }
  if (clearBtn) clearBtn.classList.add('hidden');
}

// ============================================================================
// UI INTERACTIONS
// ============================================================================

export function toggleConnectionDetails(target) {
  const card = target.closest('.connection-card');
  if (!card) return;
  
  const details = card.querySelector('.card-details-expandable');
  const toggleIcon = target.querySelector('.toggle-icon');
  const toggleText = target.querySelector('.toggle-text');
  
  if (!details) return;
  
  const isHidden = details.classList.contains('hidden');
  
  if (isHidden) {
    details.classList.remove('hidden');
    if (toggleIcon) toggleIcon.style.transform = 'rotate(180deg)';
    if (toggleText) toggleText.textContent = 'Hide Details';
  } else {
    details.classList.add('hidden');
    if (toggleIcon) toggleIcon.style.transform = 'rotate(0deg)';
    if (toggleText) toggleText.textContent = 'Show Details';
  }
}

export function closeAdvancedOptionModals() {
  const allOptions = connectionContainer.querySelectorAll('.advanced-options');
  allOptions.forEach(option => option.classList.add('hidden'));
}

export function showAdvancedOptions(target) {
  closeAdvancedOptionModals();
  
  const option = target.nextElementSibling;
  if (!option) return;
  const isOpen = !option.classList.contains('hidden');
  if(isOpen){
    option.classList.add('hidden');
  }
  else{
    option.classList.remove('hidden');
    
  }
  
  // Add rotation effect to button for visual feedback
  target.classList.toggle('active');
}

export function showUserAvatar(src) {
  const avatarModal = document.getElementById('connection-avatar-modal');
  if (!avatarModal) return;
  
  const img = avatarModal.querySelector('.avatar-modal-image');
  img.src = src || '/static/default-avatar.png';
  
  
  // Show modal
  avatarModal.classList.remove('hidden');
  document.body.style.overflow = 'hidden';
}

// ============================================================================
// ACTIVE CONNECTIONS FILTER
// ============================================================================

export async function showActiveConnections() {
  const connectedSection = connectionContainer.querySelector('#connections-connected');
  if (!connectedSection) return;
  
  connectedSection.innerHTML = getLoadingSkeleton();
  
  try {
    const response = await ConnectionAPI.getOnlineConnections();
    
    if (!response.data || response.data.length === 0) {
      connectedSection.innerHTML = showEmptyState('online');
      return;
    }
    
    // Import createConnectedConnectionCard dynamically
    const { createConnectedConnectionCard } = await import('./connection.templates.js');
    
    connectedSection.innerHTML = response.data
      .map(connection => createConnectedConnectionCard(connection))
      .join('');
  } catch (error) {
    console.error('Show active connections error:', error);
    showToast('Failed to load active connections', 'error');
    connectedSection.innerHTML = showEmptyState('connected');
  }
}

// ============================================================================
// CONNECTION SETTINGS
// ============================================================================

export async function toggleConnectionSetting(target) {
  const isChecked = target.checked;
  
  try {
    await ConnectionAPI.toggleConnectionNotification(isChecked);
  } catch (error) {
    console.error('Toggle setting error:', error);
    showToast('Failed to update settings', 'error');
    target.checked = !isChecked; // Revert
  }
}

// ============================================================================
// FORM THREAD
// ============================================================================

export async function formThread(userId) {
  // Get user details for the thread creation modal
  const card = connectionContainer.querySelector(`[data-user-id="${userId}"]`);
  if (!card) return;
  
  const userName = card.querySelector('.user-name')?.textContent;
  
  // Pre-fill thread modal with member
  const threadModal = document.getElementById('create-thread-modal');
  if (!threadModal) {
    return;
  }
  const threadForm = threadModal.querySelector('#create-thread-form');
  
  threadModal.dataset.memberIds = JSON.stringify([userId]);
  
  
  // Open the modal
  openModal('create-thread-modal');
  
  // Show info toast
}

// ============================================================================
// CONNECTION NOTES
// ============================================================================

export async function viewConnectionNotes(connectionId) {
  const notesModal = connectionContainer.querySelector('.connection-notes-modal');
  if (!notesModal) {
    console.error('Notes modal not found');
    return;
  }
  
  const contentContainer = notesModal.querySelector('.notes-content');
  const notesInput = notesModal.querySelector('.notes-input');
  const saveBtn = notesModal.querySelector('.save-note-btn');
  const emptyState = notesModal.querySelector('.notes-empty-state');
  
  openModal('connection-notes-modal');
  notesModal.dataset.connectionId = connectionId;
  
  // Show loading
  if (contentContainer) contentContainer.style.opacity = '0.5';
  
  try {
    const notes = await ConnectionAPI.getConnectionNotes(connectionId);
    
    if (contentContainer) contentContainer.style.opacity = '1';
    
    // Safely check for notes content
    const hasNotes = notes != null && typeof notes === 'string' && notes.trim().length > 0;
    
    if (hasNotes) {
      // Notes exist - show edit mode
      if (notesInput) {
        notesInput.value = notes;
        notesInput.classList.remove('hidden');
      }
      if (saveBtn) saveBtn.classList.remove('hidden');
      if (emptyState) emptyState.classList.add('hidden');
    } else {
      // No notes - show empty state
      if (notesInput) {
        notesInput.value = '';
        notesInput.classList.add('hidden');
      }
      if (saveBtn) saveBtn.classList.add('hidden');
      if (emptyState) emptyState.classList.remove('hidden');
    }
  } catch (error) {
    console.error('View notes error:', error);
    if (contentContainer) contentContainer.style.opacity = '1';
    
    // Show error state
    if (notesInput) notesInput.classList.add('hidden');
    if (saveBtn) saveBtn.classList.add('hidden');
    if (emptyState) {
      emptyState.classList.remove('hidden');
      emptyState.innerHTML = '<p>Failed to load notes</p>';
    }
  }
}

export async function rescheduleStudySession(sessionId){
  if(!sessionId) return;
  const response = await ConnectionAPI.getSessionDetails(sessionId);
  if(response.status !== 'success'){
    showToast("Error loading sessuon details", 'error');
    return;
  }
  connectionState.rescheduleSessionResources = {
      requester_resources: [],
      receiver_resources: []
    };
  document.getElementById('reschedule-session-resource-preview-container').innerHTML = '';
  populateRescheduleModal(response.data);
  
}

export async function createOrUpdateNote() {
  const notesModal = connectionContainer.querySelector('.connection-notes-modal');
  if (!notesModal) return;
  
  const connectionId = notesModal.dataset.connectionId;
  const notesInput = notesModal.querySelector('.notes-input');
  const saveBtn = notesModal.querySelector('.save-note-btn');
  
  if (!connectionId || !notesInput) return;
  
  const notes = notesInput.value.trim();
  
  
  
  if (saveBtn) {
    saveBtn.disabled = true;
    saveBtn.textContent = 'Saving...';
  }
  
  try {
    const response = await ConnectionAPI.createConnectionNote(connectionId, notes);
    
    if (response.status === 'success') {
      closeModal('connection-notes-modal');
    } else {
      showToast(response.message || 'Failed to save notes', 'error');
    }
  } catch (error) {
    console.error('Save notes error:', error);
    showToast('Failed to save notes', 'error');
  } finally {
    if (saveBtn) {
      saveBtn.disabled = false;
      saveBtn.textContent = 'Save';
    }
  }
}

export function showCreateNoteUI() {
  const notesModal = connectionContainer.querySelector('.connection-notes-modal');
  if (!notesModal) return;
  
  const notesInput = notesModal.querySelector('.notes-input');
  const saveBtn = notesModal.querySelector('.save-note-btn');
  const emptyState = notesModal.querySelector('.notes-empty-state');
  
  if (notesInput) {
    notesInput.classList.remove('hidden');
    notesInput.focus();
  }
  if (saveBtn) saveBtn.classList.remove('hidden');
  if (emptyState) emptyState.classList.add('hidden');
}


export function openConnectionActionsModal(target) {
  const modal = document.getElementById('userActionsModal');
  const container = document.getElementById('modalActionsContainer');
  const advancedOptions = target.nextElementSibling;
  
  if (!modal || !container || !advancedOptions) return;
  
  // Get user info from the card
  const card = target.closest('.connection-card');
  if (!card) return;
  
  const avatar = card.querySelector('.user-avatar')?.src || '/static/default-avatar.png';
  const name = card.querySelector('.user-name')?.textContent || 'User';
  const username = card.querySelector('.user-username')?.textContent || '@user';
  
  // Populate user info in modal
  const modalAvatar = document.getElementById('modalUserAvatar');
  const modalName = document.getElementById('modalUserName');
  const modalUsername = document.getElementById('modalUserUsername');
  
  if (modalAvatar) modalAvatar.src = avatar;
  if (modalName) modalName.textContent = name;
  if (modalUsername) modalUsername.textContent = username;
  
  // Clear and populate actions
  container.innerHTML = '';
  
  // Get all buttons from advanced options
  const buttons = advancedOptions.querySelectorAll('button');
  buttons.forEach(button => {
    const clonedButton = button.cloneNode(true);
    clonedButton.classList.add('user-action-item');
    container.appendChild(clonedButton);
  });
  
  // Show modal
  openModal('userActionsModal');
  document.body.style.overflow = 'hidden';
}
