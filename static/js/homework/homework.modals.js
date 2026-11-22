/**
 * ============================================================================
 * HOMEWORK MODALS
 * Modal windows and their interactions
 * FLEXIBLE VERSION: Works with both <form> and <div> containers
 * ============================================================================
 */

import { homeworkState } from './homework.state.js';
import { homeworkAPI } from './homework.api.js';
import {
  showHomeworkToast, getFileType, escapeHtml, safeUrl,
  localInputToUtcIso, serverUtcToLocalInput
} from './homework.utils.js';
import { refreshCurrentTab } from './homework.render.js';
import { renderResourceItem } from './homework.templates.js';

/**
 * Open create homework modal
 */
 // ============================================================================
// ADD THIS TO THE END OF homework_modals.js
// ============================================================================

/**
 * Open edit homework modal
 */
export function openEditHomeworkModal(assignmentId) {
  const assignment = homeworkState.getAssignmentById(assignmentId);
  
  if (!assignment) {
    console.error('Assignment not found:', assignmentId);
    showHomeworkToast('Assignment not found', 'error');
    return;
  }

  const modal = document.getElementById('hw-edit-modal');
  
  if (!modal) {
    console.error('Edit homework modal not found');
    
    return;
  }

  modal.dataset.assignmentId = assignmentId;

  const titleInput = modal.querySelector('#hw-edit-title');
  const subjectInput = modal.querySelector('#hw-edit-subject');
  const descriptionInput = modal.querySelector('#hw-edit-description');
  const dueDateInput = modal.querySelector('#hw-edit-due-date');
  const difficultySelect = modal.querySelector('#hw-edit-difficulty');
  const estimatedHoursInput = modal.querySelector('#hw-edit-estimated-hours');

  if (titleInput) titleInput.value = assignment.title || '';
  if (subjectInput) subjectInput.value = assignment.subject || '';
  if (descriptionInput) descriptionInput.value = assignment.description || '';
  
  if (dueDateInput && assignment.due_date) {
    dueDateInput.value = serverUtcToLocalInput(assignment.due_date);
  }
  
  if (difficultySelect) difficultySelect.value = assignment.difficulty || 'medium';
  if (estimatedHoursInput) estimatedHoursInput.value = assignment.estimated_hours || '';

  homeworkState.setEditResources(assignment.resources || []);
  updateEditResourcePreview();

  const fileInput = document.getElementById('hw-edit-resource-input');
  if (fileInput) {
    const newFileInput = fileInput.cloneNode(true);
    fileInput.parentNode.replaceChild(newFileInput, fileInput);
    newFileInput.addEventListener('change', handleEditResourceUpload);
  }
  openModal('hw-edit-modal');

  document.body.style.overflow = 'hidden';
}

/**
 * Handle edit homework submission
 */
export async function handleEditHomework(container, event) {
  event.preventDefault();

  const modal = document.getElementById('hw-edit-modal');
  const assignmentId = modal?.dataset.assignmentId;

  if (!assignmentId) {
    showHomeworkToast('Assignment ID not found', 'error');
    return;
  }

  const searchContainer = modal || container;

  const titleInput = searchContainer.querySelector('#hw-edit-title');
  const subjectInput = searchContainer.querySelector('#hw-edit-subject');
  const descriptionInput = searchContainer.querySelector('#hw-edit-description');
  const dueDateInput = searchContainer.querySelector('#hw-edit-due-date');
  const difficultySelect = searchContainer.querySelector('#hw-edit-difficulty');
  const estimatedHoursInput = searchContainer.querySelector('#hw-edit-estimated-hours');

  if (!titleInput || !dueDateInput) {
    showHomeworkToast('Form fields not found', 'error');
    return;
  }

  const title = titleInput.value.trim();
  const subject = subjectInput ? subjectInput.value.trim() : '';
  const description = descriptionInput ? descriptionInput.value.trim() : '';
  const dueDate = dueDateInput.value;
  const difficulty = difficultySelect ? difficultySelect.value : 'medium';
  const estimatedHours = estimatedHoursInput ? estimatedHoursInput.value : '';
  const resources = homeworkState.getEditResources();

  if (!title) {
    showHomeworkToast('Please enter a title', 'error');
    titleInput.focus();
    return;
  }

  if (!dueDate) {
    showHomeworkToast('Please select a due date', 'error');
    dueDateInput.focus();
    return;
  }

  const submitBtn = container.tagName === 'BUTTON' ? container : 
                    searchContainer.querySelector('.hw-btn-primary');

  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = 'Saving...';
  }

  try {
    const payload = {
      title,
      subject,
      description,
      due_date: localInputToUtcIso(dueDate),
      difficulty,
      estimated_hours: estimatedHours ? parseFloat(estimatedHours) : null,
      resources: resources
    };

    const response = await homeworkAPI.updateAssignment(assignmentId, payload);

    if (response.status === 'success') {
      closeHomeworkModal('hw-edit-modal');
      refreshCurrentTab();
    } else {
      throw new Error(response.message || 'Failed to update assignment');
    }
  } catch (error) {
    console.error('Error updating assignment:', error);
    showHomeworkToast(error.message || 'Failed to update assignment', 'error');
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Save Changes';
    }
  }
}

/**
 * Handle edit resource upload
 */
async function handleEditResourceUpload(event) {
  const input = event.target;
  const file = input.files[0];

  if (!file) return;

  const MAX_FILE_SIZE = 10 * 1024 * 1024;
  if (file.size > MAX_FILE_SIZE) {
    const fileSizeMB = (file.size / (1024 * 1024)).toFixed(2);
    showHomeworkToast(`File size (${fileSizeMB}MB) exceeds 10MB limit`, 'error');
    input.value = '';
    return;
  }

  try {
    const response = await homeworkAPI.uploadResource(file);

    if (response.status === 'success') {
      const currentResources = homeworkState.getEditResources();
      currentResources.push(response.data);
      homeworkState.setEditResources(currentResources);
      
      updateEditResourcePreview();
    } else {
      throw new Error(response.message || 'Upload failed');
    }
  } catch (error) {
    console.error('Error uploading resource:', error);
    showHomeworkToast(error.message || 'Failed to upload resource', 'error');
  } finally {
    input.value = '';
  }
}

/**
 * Update edit resource preview
 */
function updateEditResourcePreview() {
  const container = document.getElementById('hw-edit-resources-preview');
  
  if (!container) return;

  const resources = homeworkState.getEditResources();

  if (resources.length === 0) {
    container.innerHTML = '';
    container.classList.add('hidden');
    return;
  }

  container.classList.remove('hidden');
  container.innerHTML = `
    <div class="hw-resources-list">
      <div class="hw-resources-header">
        <span class="hw-resources-title">Resources (${resources.length})</span>
      </div>
      ${resources.map((resource, index) => renderResourceItem(resource, index, true)).join('')}
    </div>
  `;
}

/**
 * Remove edit resource
 */
export function removeEditResource(index) {
  const resources = homeworkState.getEditResources();
  resources.splice(index, 1);
  homeworkState.setEditResources(resources);
  updateEditResourcePreview();
}

export function openCreateHomeworkModal() {
  const modal = document.getElementById('hw-create-modal');
  
  if (!modal) {
    console.error('Create homework modal not found');
    return;
  }

  // Reset form
  const titleInput = modal.querySelector('#hw-create-title');
  const subjectInput = modal.querySelector('#hw-create-subject');
  const descriptionInput = modal.querySelector('#hw-create-description');
  const dueDateInput = modal.querySelector('#hw-create-due-date');
  const difficultySelect = modal.querySelector('#hw-create-difficulty');
  const estimatedHoursInput = modal.querySelector('#hw-create-estimated-hours');
  const shareCheckbox = modal.querySelector('#hw-create-share-for-help');

  if (titleInput) titleInput.value = '';
  if (subjectInput) subjectInput.value = '';
  if (descriptionInput) descriptionInput.value = '';
  if (dueDateInput) dueDateInput.value = '';
  if (difficultySelect) difficultySelect.value = 'medium';
  if (estimatedHoursInput) estimatedHoursInput.value = '';
  if (shareCheckbox) shareCheckbox.checked = false;

  // Clear resources
  homeworkState.clearUploadedResources();
  updateResourcePreview('hw-create-resources-preview');

  // Setup file input change handler
  const fileInput = document.getElementById('hw-resource-upload-input');
  if (fileInput) {
    // Remove any existing listeners by cloning
    const newFileInput = fileInput.cloneNode(true);
    fileInput.parentNode.replaceChild(newFileInput, fileInput);
    
    // Add fresh change event listener
    newFileInput.addEventListener('change', handleResourceUpload);
  }

  // Show modal
  openModal('hw-create-modal');
}

/**
 * Close any homework modal
 * Delegates to the shared closeModal (feed.utils.js) so header
 * show/hide and any other shared cleanup stays consistent.
 */
export function closeHomeworkModal(modalId) {
  closeModal(modalId);
}

/**
 * Handle create homework form submission
 * FLEXIBLE: Works with both form and div containers
 * @param {HTMLElement} container - The form or div container from delegation
 * @param {Event} event - The submit/click event
 */
export async function handleCreateHomework(container, event) {
  event.preventDefault();

  console.log('=== handleCreateHomework called ===');
  console.log('container:', container);
  console.log('container type:', container?.tagName);

  // Validate container - accept FORM, DIV, or BUTTON
  if (!container) {
    console.error('❌ Container not provided by delegation');
    showHomeworkToast('Form element not found', 'error');
    return;
  }

  // If container is a button, get the modal container
  let formContainer = container;
  if (container.tagName === 'BUTTON') {
    console.log('⚠️ Container is BUTTON, searching for modal...');
    const modal = container.closest('.hw-modal');
    formContainer = modal?.querySelector('form') || modal?.querySelector('[data-action="submit-create-homework"]') || modal;
    console.log('Found modal container:', formContainer?.tagName);
  }

  // Additional validation
  if (!formContainer || formContainer.tagName === 'HTML' || formContainer.tagName === 'BODY') {
    console.error('❌ Invalid container:', formContainer?.tagName);
    showHomeworkToast('Invalid form element', 'error');
    return;
  }

  console.log('✅ Container verified:', formContainer.tagName);

  // Get submit button (might be inside container or the container itself)
  const submitBtn = formContainer.querySelector('[type="submit"]') || 
                    formContainer.querySelector('.hw-btn-primary') ||
                    (container.tagName === 'BUTTON' ? container : null);

  // Get form inputs - search in modal if needed
  const modal = document.getElementById('hw-create-modal');
  const searchContainer = modal || formContainer;

  const titleInput = searchContainer.querySelector('#hw-create-title');
  const subjectInput = searchContainer.querySelector('#hw-create-subject');
  const descriptionInput = searchContainer.querySelector('#hw-create-description');
  const dueDateInput = searchContainer.querySelector('#hw-create-due-date');
  const difficultySelect = searchContainer.querySelector('#hw-create-difficulty');
  const estimatedHoursInput = searchContainer.querySelector('#hw-create-estimated-hours');
  const shareCheckbox = searchContainer.querySelector('#hw-create-share-for-help');

  console.log('Form inputs found:', {
    titleInput: !!titleInput,
    subjectInput: !!subjectInput,
    descriptionInput: !!descriptionInput,
    dueDateInput: !!dueDateInput,
    difficultySelect: !!difficultySelect,
    estimatedHoursInput: !!estimatedHoursInput,
    shareCheckbox: !!shareCheckbox
  });

  // Validate required inputs exist
  if (!titleInput) {
    console.error('❌ Title input not found');
    showHomeworkToast('Title input not found in form', 'error');
    return;
  }

  if (!dueDateInput) {
    console.error('❌ Due date input not found');
    showHomeworkToast('Due date input not found in form', 'error');
    return;
  }

  // Get values
  const title = titleInput.value.trim();
  const subject = subjectInput ? subjectInput.value.trim() : '';
  const description = descriptionInput ? descriptionInput.value.trim() : '';
  const dueDate = dueDateInput.value;
  const difficulty = difficultySelect ? difficultySelect.value : 'medium';
  const estimatedHours = estimatedHoursInput ? estimatedHoursInput.value : '';
  const shareForHelp = shareCheckbox ? shareCheckbox.checked : false;

  console.log('Form values:', {
    title,
    subject,
    description,
    dueDate,
    difficulty,
    estimatedHours,
    shareForHelp
  });

  // Get uploaded resources from state
  const resources = homeworkState.getUploadedResources();
  console.log('Resources from state:', resources);

  // Validate
  if (!title) {
    showHomeworkToast('Please enter a title', 'error');
    titleInput.focus();
    return;
  }

  if (!dueDate) {
    showHomeworkToast('Please select a due date', 'error');
    dueDateInput.focus();
    return;
  }

  // Disable submit button
  if (submitBtn) {
    submitBtn.disabled = true;
    const originalText = submitBtn.textContent;
    submitBtn.textContent = 'Creating...';
    
    // Restore button after function completes
    setTimeout(() => {
      submitBtn.disabled = false;
      submitBtn.textContent = originalText;
    }, 5000); // Safety timeout
  }

  try {
    // Create payload with resources
    const payload = {
      title,
      subject,
      description,
      due_date: localInputToUtcIso(dueDate),
      difficulty,
      estimated_hours: estimatedHours ? parseFloat(estimatedHours) : null,
      share_for_help: shareForHelp,
      resources: resources  // Include uploaded resources
    };

    console.log('📤 Sending payload to API:', payload);

    const response = await homeworkAPI.createAssignment(payload);

    console.log('📥 API Response:', response);

    if (response.status === 'success') {
      closeHomeworkModal('hw-create-modal');
      refreshCurrentTab();
    } else {
      throw new Error(response.message || 'Failed to create assignment');
    }
  } catch (error) {
    console.error('❌ Error creating assignment:', error);
    showHomeworkToast(error.message || 'Failed to create assignment', 'error');
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Create Assignment';
    }
  }
}

/**
 * Open homework options menu
 */
export function openHomeworkOptions(assignmentId, event) {
  let alreadyExist = false;
  document.querySelectorAll('.hw-options-menu').forEach(menu => {
    menu.dataset.assignmentId == assignmentId? alreadyExist = true : '';
    menu.remove();
  });
  if(alreadyExist) return;
  // Close any existing options menus
  

  const assignment = homeworkState.getAssignmentById(assignmentId);
  
  if (!assignment) return;
  

  const button = event.target.closest('[data-action="toggle-homework-options"]');
  const rect = button.getBoundingClientRect();

  const menu = document.createElement('div');
  menu.className = 'hw-options-menu';
  menu.dataset.assignmentId = assignmentId;
  menu.innerHTML = `
<button class="hw-option-item"
        onclick="event.stopPropagation(); openEditHomeworkModal(${assignmentId})"
      
        
        data-assignment-id="${assignmentId}">
        
    <svg width="16" height="16" viewBox="0 0 24 24"
         fill="none" stroke="currentColor" stroke-width="2">
        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
        <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
    </svg>
    
    Edit
</button>

    ${assignment.is_shared ? `
      <button class="hw-option-item" data-action="unshare-homework" data-assignment-id="${assignmentId}">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="18" cy="5" r="3"/>
          <circle cx="6" cy="12" r="3"/>
          <circle cx="18" cy="19" r="3"/>
          <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/>
          <line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/>
        </svg>
        Unshare
      </button>
    ` : `
      <button class="hw-option-item" data-action="share-homework-for-help" data-assignment-id="${assignmentId}">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="18" cy="5" r="3"/>
          <circle cx="6" cy="12" r="3"/>
          <circle cx="18" cy="19" r="3"/>
          <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/>
          <line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/>
        </svg>
        Share for Help
      </button>
    `}
    <button class="hw-option-item hw-option-danger" data-action="delete-homework" data-assignment-id="${assignmentId}">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <polyline points="3 6 5 6 21 6"/>
        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
      </svg>
      Delete
    </button>
  `;

  // Position menu
  menu.style.position = 'fixed';
  menu.style.top = `${rect.bottom + 5}px`;
  menu.style.right = `${window.innerWidth - rect.right}px`;

  document.body.appendChild(menu);

  // Close menu when clicking outside
  setTimeout(() => {
    const closeMenu = (e) => {
      if (!menu.contains(e.target) && !button.contains(e.target)) {
        menu.remove();
        document.removeEventListener('click', closeMenu);
      }
    };
    document.addEventListener('click', closeMenu);
  }, 10);
}

/**
 * Handle resource upload
 */
export async function handleResourceUpload(event) {
  const input = event.target;
  const file = input.files[0];

  if (!file) return;

  // Validate file size (10MB max for better performance)
  const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10MB
  if (file.size > MAX_FILE_SIZE) {
    const fileSizeMB = (file.size / (1024 * 1024)).toFixed(2);
    showHomeworkToast(`File size (${fileSizeMB}MB) exceeds 10MB limit`, 'error');
    input.value = '';
    return;
  }

  // Validate file type (optional but recommended)
  const allowedTypes = [
    'image/', 'video/', 'application/pdf', 
    'application/msword', 'application/vnd.openxmlformats',
    'text/'
  ];
  const isAllowedType = allowedTypes.some(type => file.type.startsWith(type));
  
  if (!isAllowedType) {
    showHomeworkToast('File type not supported. Please upload images, videos, PDFs, or documents', 'error');
    input.value = '';
    return;
  }

  try {
    const response = await homeworkAPI.uploadResource(file);

    if (response.status === 'success') {
      const resource = response.data;
      homeworkState.addUploadedResource(resource);
      
      // Update preview
      updateResourcePreview('hw-create-resources-preview');
    } else {
      throw new Error(response.message || 'Upload failed');
    }
  } catch (error) {
    console.error('Error uploading resource:', error);
    showHomeworkToast(error.message || 'Failed to upload resource', 'error');
  } finally {
    input.value = '';
  }
}

/**
 * Update resource preview
 */
function updateResourcePreview(containerId) {
  const container = document.getElementById(containerId);
  
  if (!container) return;

  const resources = homeworkState.getUploadedResources();

  if (resources.length === 0) {
    container.innerHTML = '';
    container.classList.add('hidden');
    return;
  }

  container.classList.remove('hidden');
  container.innerHTML = `
    <div class="hw-resources-list">
      <div class="hw-resources-header">
        <span class="hw-resources-title">Resources (${resources.length})</span>
      </div>
      ${resources.map((resource, index) => renderResourceItem(resource, index)).join('')}
    </div>
  `;
}

/**
 * Remove resource
 */
export function removeResource(index) {
  homeworkState.removeUploadedResource(index);
  
  // Update all preview containers
  document.querySelectorAll('.hw-resources-preview').forEach(container => {
    updateResourcePreview(container.id);
  });
}

/**
 * View resource in modal
 */
export function viewResource(url, type) {
  const modal = document.getElementById('hw-resource-preview-modal');
  
  if (!modal) return;

  const container = modal.querySelector('#hw-resource-preview-content');
  const downloadBtn = modal.querySelector('[data-action="download-resource"]');

  // Set download URL
  if (downloadBtn) {
    downloadBtn.dataset.url = url;
  }

  // Only http(s)/same-site URLs may reach src attributes (blocks javascript:/data: URLs)
  const safeResourceUrl = safeUrl(url);
  if (!safeResourceUrl) {
    showHomeworkToast('This file link is not supported', 'error');
    return;
  }
  const escapedUrl = escapeHtml(safeResourceUrl);

  // Render preview based on type
  if (type === 'image') {
    container.innerHTML = `<img src="${escapedUrl}" alt="Resource preview" class="hw-resource-preview-image" />`;
  } else if (type === 'video') {
    container.innerHTML = `
      <video controls class="hw-resource-preview-video">
        <source src="${escapedUrl}" type="video/mp4">
        Your browser does not support video playback.
      </video>
    `;
  } else if (type === 'pdf' || type === 'document') {
    container.innerHTML = `
      <iframe src="${escapedUrl}" class="hw-resource-preview-iframe"></iframe>
    `;
  } else {
    container.innerHTML = `
      <div class="hw-resource-preview-placeholder">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/>
          <polyline points="13 2 13 9 20 9"/>
        </svg>
        <p>Preview not available</p>
        <p>Click download to view this file</p>
      </div>
    `;
  }

  // Show modal
  openModal('hw-resource-preview-modal');
}

/**
 * Download resource
 */
export function downloadResource(url) {
  const safe = safeUrl(url);
  if (!safe) {
    showHomeworkToast('This file link is not supported', 'error');
    return;
  }
  const link = document.createElement('a');
  link.href = safe;
  link.rel = 'noopener noreferrer';
  link.download = '';
  link.target = '_blank';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

/**
 * Open homework details modal
 */
 export async function openHomeworkDetailsModal(homeworkId, submissionId = null) {
  const homework = homeworkState.getHomeworkById(homeworkId);
  if (!homework) return;

  const modal = document.getElementById('hw-details-modal');
  if (!modal) return;

  const modalBody = modal.querySelector('.hw-modal-body');
  if (!modalBody) return;

  // Show loading
  modalBody.innerHTML = `<div class="hw-loading-state"><div class="hw-spinner"></div><p>Loading details...</p></div>`;
  openModal('hw-details-modal');

  const formatDate = (dateString) => {
    if (!dateString) return 'N/A';
    return new Date(dateString).toLocaleDateString('en-US', { 
      month: 'short', day: 'numeric', year: 'numeric',
      hour: '2-digit', minute: '2-digit'
    });
  };

  // Fetch submission details if user is already helping
  let submission = null;
  if (submissionId) {
    try {
      const res = await homeworkAPI.getSubmissionDetails(submissionId);
      if (res.status === 'success') submission = res.data;
    } catch (e) {
      console.error('Failed to load submission details:', e);
    }
  }

  modalBody.innerHTML = `
    <div class="hw-details-content">
      <!-- Title & Status -->
      <div class="hw-details-header">
        <h3 class="hw-details-title">${escapeHtml(homework.title)}</h3>
        ${homework.already_helping ? `
          <span class="hw-badge hw-badge-${escapeHtml(homework.my_help_status)}">
            ${getStatusDisplayText(homework.my_help_status)}
          </span>
        ` : ''}
      </div>

      <!-- Meta Info -->
      <div class="hw-details-meta">
        ${homework.subject ? `<div class="hw-detail-item"><strong>Subject:</strong> ${escapeHtml(homework.subject)}</div>` : ''}
        <div class="hw-detail-item"><strong>Difficulty:</strong> ${escapeHtml(homework.difficulty)}</div>
        <div class="hw-detail-item"><strong>Due Date:</strong> ${formatDate(homework.due_date)}</div>
        <div class="hw-detail-item"><strong>Estimated Time:</strong> ${homework.estimated_hours ? homework.estimated_hours + ' hours' : 'Not specified'}</div>
        ${homework.help_count > 0 ? `<div class="hw-detail-item"><strong>Helpers:</strong> ${homework.help_count}</div>` : ''}
      </div>

      ${homework.description ? `
        <div class="hw-details-section">
          <h4>Description</h4>
          <p>${escapeHtml(homework.description)}</p>
        </div>
      ` : ''}

      <!-- Student Info -->
      <div class="hw-details-student">
        <img src="${escapeHtml(safeUrl(homework.student?.avatar) || '/static/default-avatar.png')}" class="hw-party-avatar" alt="${escapeHtml(homework.student?.name)}" />
        <div>
          <div class="hw-party-name">${escapeHtml(homework.student?.name)}</div>
          ${homework.student?.department ? `<div class="hw-party-meta">${escapeHtml(homework.student.department)}</div>` : ''}
        </div>
      </div>

      ${submission ? `
        <!-- Submission Section -->
        <div class="hw-details-section hw-details-submission">
          <h4>Your Submission</h4>
          ${submission.solution?.text ? `
            <div class="hw-solution-display">
              <p>${escapeHtml(submission.solution.text)}</p>
              ${submission.solution.resources?.length > 0 ? `
                <div class="hw-resources-list">
                  ${submission.solution.resources.map((r, i) => renderResourceItem(r, i)).join('')}
                </div>
              ` : ''}
            </div>
            <div class="hw-solution-submitted-time">Submitted ${formatDate(submission.submitted_at)}</div>
          ` : `
            <p class="hw-text-muted">You haven't submitted a solution yet.</p>
          `}
        </div>
      ` : ''}

      <!-- Actions -->
      <div class="hw-details-actions">
        ${homework.already_helping && homework.my_help_status === 'pending' ? `
          <button class="hw-btn hw-btn-primary" data-action="open-submit-solution" data-submission-id="${homework.my_submission_id}" 
            onclick="closeModal('hw-details-modal')">
            Submit Solution
          </button>
        ` : ''}
        ${!homework.already_helping ? `
          <button class="hw-btn hw-btn-primary" data-action="offer-help-homework" data-homework-id="${homework.id}">
            Offer Help
          </button>
        ` : ''}
        <button class="hw-btn hw-btn-secondary" data-action="close-homework-modal" data-modal-id="hw-details-modal">
          Close
        </button>
      </div>
    </div>
  `;
 }

window.openEditHomeworkModal = openEditHomeworkModal;