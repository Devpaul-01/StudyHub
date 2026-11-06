
/** * ============================================================================ * StudyHub - Core Utilities * Helper functions used across the application * ============================================================================ */
/**
============================================================================
DATE & TIME FORMATTING
============================================================================
*/
/**
Format date to relative time (e.g., "2h ago", "3 days ago")
@param {string|Date} dateString - Date to format
@returns {string} Formatted relative time
*/
function formatDate(dateString) {
    const date = new Date(dateString);
    const now = new Date();
    const diff = now - date;

    // Less than a minute
    if (diff < 60000) {
        return 'Just now';
    }

    // Less than an hour
    if (diff < 3600000) {
        const minutes = Math.floor(diff / 60000);
        return `${minutes}m ago`;  // ✅ Fixed
    }

    // Less than a day
    if (diff < 86400000) {
        const hours = Math.floor(diff / 3600000);
        return `${hours}h ago`;  // ✅ Fixed
    }

    // Less than a week
    if (diff < 604800000) {
        const days = Math.floor(diff / 86400000);
        return `${days}d ago`;  // ✅ Fixed
    }

    // Less than a month
    if (diff < 2592000000) {
        const weeks = Math.floor(diff / 604800000);
        return `${weeks}w ago`;  // ✅ Fixed
    }

    // Format as date
    return date.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: date.getFullYear() !== now.getFullYear() ? 'numeric' : undefined
    });
}
/**
Format date to full readable format
@param {string|Date} dateString - Date to format
@returns {string} Formatted date (e.g., "January 15, 2024")
*/
function formatFullDate(dateString) {
const date = new Date(dateString);
return date.toLocaleDateString('en-US', {
month: 'long',
day: 'numeric',
year: 'numeric'
});
}
/**
Format time (e.g., "3:45 PM")
@param {string|Date} dateString - Date to format
@returns {string} Formatted time
*/
function formatTime(dateString) {
const date = new Date(dateString);
return date.toLocaleTimeString('en-US', {
hour: 'numeric',
minute: '2-digit',
hour12: true
});
}
/**
============================================================================
VALIDATION FUNCTIONS
============================================================================
*/
/**
Validate email format
@param {string} email - Email to validate
@returns {boolean} True if valid
*/
function isValidEmail(email) {
const regex = /^[^\s@]+@[^\s@]+.[^\s@]+$/;
return regex.test(email);
}
/**
Validate username format (3-20 chars, lowercase alphanumeric)
@param {string} username - Username to validate
@returns {boolean} True if valid
*/
function isValidUsername(username) {
const regex = /^[a-z0-9]{3,20}$/;
return regex.test(username);
}
/**
Validate password strength
@param {string} password - Password to check
@returns {object} { strength: string, score: number, feedback: string }
*/
function checkPasswordStrength(password) {
let score = 0;
let feedback = [];
// Length check
if (password.length >= 6) score++;
if (password.length >= 10) score++;
if (password.length >= 14) score++;
// Character variety
if (/[a-z]/.test(password)) score++;
if (/[A-Z]/.test(password)) score++;
if (/[0-9]/.test(password)) score++;
if (/[^a-zA-Z0-9]/.test(password)) score++;
// Determine strength
let strength = 'weak';
if (score <= 3) {
strength = 'weak';
feedback.push('Use at least 8 characters');
feedback.push('Mix uppercase and lowercase');
} else if (score <= 5) {
strength = 'medium';
feedback.push('Add numbers or symbols for better security');
} else {
strength = 'strong';
feedback.push('Great password!');
}
return {
strength,
score,
feedback: feedback[0] || 'Enter a password'
};
}
/**
============================================================================
UI HELPER FUNCTIONS
============================================================================
*/
/**
Show loading spinner on button
@param {HTMLElement} button - Button element
@param {boolean} isLoading - Show/hide loading state
@param {string} loadingText - Text to show while loading
*/
function setButtonLoading(button, isLoading, loadingText = 'Loading...') {
if (!button) return;
if (isLoading) {
// Store original content
button.dataset.originalContent = button.innerHTML;
button.dataset.originalDisabled = button.disabled;
// Set loading state
button.innerHTML = `<span class="spinner"></span> ${loadingText}`;
button.disabled = true;
button.classList.add('loading');
} else {
// Restore original state
button.innerHTML = button.dataset.originalContent || button.innerHTML;
button.disabled = button.dataset.originalDisabled === 'true';
button.classList.remove('loading');
}
}
/**
Show loading state on element
@param {HTMLElement} element - Element to show loading on
@param {boolean} isLoading - Show/hide loading
*/
function setElementLoading(element, isLoading) {
if (!element) return;
if (isLoading) {
element.classList.add('loading');
element.style.opacity = '0.6';
element.style.pointerEvents = 'none';
} else {
element.classList.remove('loading');
element.style.opacity = '1';
element.style.pointerEvents = 'auto';
}
}
/**
Smooth scroll to element
@param {HTMLElement|string} target - Element or selector
@param {number} offset - Offset from top in pixels
*/
function scrollToElement(target, offset = 0) {
const element = typeof target === 'string'
? document.querySelector(target)
: target;
if (!element) return;
const elementPosition = element.getBoundingClientRect().top;
const offsetPosition = elementPosition + window.pageYOffset - offset;
window.scrollTo({
top: offsetPosition,
behavior: 'smooth'
});
}
/**
============================================================================
STRING MANIPULATION
============================================================================
*/
/**
Truncate text to specified length
@param {string} text - Text to truncate
@param {number} length - Max length
@param {string} suffix - Suffix to add (default: '...')
@returns {string} Truncated text
*/
function truncateText(text, length, suffix = '...') {
if (!text || text.length <= length) return text;
return text.substring(0, length).trim() + suffix;
}
/**
Escape HTML to prevent XSS
@param {string} text - Text to escape
@returns {string} Escaped text
*/
function escapeHtml(text) {
const map = {
'&': '&amp;',
'<': '&lt;',
'>': '&gt;',
'"': '&quot;',
"'": '&#39;'
};
return text.replace(/[&<>"']/g, m => map[m]);
}
/**
Convert text to URL-friendly slug
@param {string} text - Text to slugify
@returns {string} URL-friendly slug
*/
function slugify(text) {
return text
.toString()
.toLowerCase()
.trim()
.replace(/\s+/g, '-')
.replace(/[^\w-]+/g, '')
.replace(/--+/g, '-')
.replace(/^-+/, '')
.replace(/-+$/, '');
}
/**
Capitalize first letter of string
@param {string} text - Text to capitalize
@returns {string} Capitalized text
*/
function capitalize(text) {
if (!text) return '';
return text.charAt(0).toUpperCase() + text.slice(1).toLowerCase();
}
/**
============================================================================
DEBOUNCE & THROTTLE
============================================================================
*/
/**
Debounce function execution
@param {Function} func - Function to debounce
@param {number} wait - Wait time in milliseconds
@returns {Function} Debounced function
*/
function debounce(func, wait = 300) {
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
Throttle function execution
@param {Function} func - Function to throttle
@param {number} limit - Time limit in milliseconds
@returns {Function} Throttled function
*/
function throttle(func, limit = 300) {
let inThrottle;
return function(...args) {
if (!inThrottle) {
func.apply(this, args);
inThrottle = true;
setTimeout(() => inThrottle = false, limit);
}
};
}
/**
============================================================================
LOCAL STORAGE HELPERS
============================================================================
*/
/**
Store data in localStorage with JSON encoding
@param {string} key - Storage key
@param {any} value - Value to store
*/
function storeLocal(key, value) {
try {
localStorage.setItem(key, JSON.stringify(value));
} catch (error) {
console.error('localStorage error:', error);
}
}
/**
Get data from localStorage with JSON decoding
@param {string} key - Storage key
@param {any} defaultValue - Default value if not found
@returns {any} Stored value or default
*/
function getLocal(key, defaultValue = null) {
try {
const item = localStorage.getItem(key);
return item ? JSON.parse(item) : defaultValue;
} catch (error) {
console.error('localStorage error:', error);
return defaultValue;
}
}
/**
Remove item from localStorage
@param {string} key - Storage key
*/
function removeLocal(key) {
try {
localStorage.removeItem(key);
} catch (error) {
console.error('localStorage error:', error);
}
}
/**
Clear all localStorage
*/
function clearLocal() {
try {
localStorage.clear();
} catch (error) {
console.error('localStorage error:', error);
}
}
/**
============================================================================
FILE HANDLING
============================================================================
*/
/**
Format file size to human-readable format
@param {number} bytes - File size in bytes
@returns {string} Formatted size (e.g., "2.5 MB")
*/
function formatFileSize(bytes) {
if (bytes === 0) return '0 Bytes';
const k = 1024;
const sizes = ['Bytes', 'KB', 'MB', 'GB'];
const i = Math.floor(Math.log(bytes) / Math.log(k));
return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i];
}
/**
Get file extension from filename
@param {string} filename - Filename
@returns {string} Extension (lowercase)
*/
function getFileExtension(filename) {
return filename.slice((filename.lastIndexOf('.') - 1 >>> 0) + 2).toLowerCase();
}
/**
Check if file is image
@param {string} filename - Filename or extension
@returns {boolean} True if image
*/
function isImageFile(filename) {
const imageExtensions = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp'];
const ext = getFileExtension(filename);
return imageExtensions.includes(ext);
}
/**
Check if file is video
@param {string} filename - Filename or extension
@returns {boolean} True if video
*/
function isVideoFile(filename) {
const videoExtensions = ['mp4', 'webm', 'ogg', 'mov', 'avi'];
const ext = getFileExtension(filename);
return videoExtensions.includes(ext);
}
/**
============================================================================
ARRAY HELPERS
============================================================================
*/
/**
Remove duplicates from array
@param {Array} array - Array with duplicates
@returns {Array} Array without duplicates
*/
function uniqueArray(array) {
return [...new Set(array)];
}
/**
Chunk array into smaller arrays
@param {Array} array - Array to chunk
@param {number} size - Chunk size
@returns {Array} Array of chunks
*/
function chunkArray(array, size) {
const chunks = [];
for (let i = 0; i < array.length; i += size) {
chunks.push(array.slice(i, i + size));
}
return chunks;
}
/**
Shuffle array randomly
@param {Array} array - Array to shuffle
@returns {Array} Shuffled array
*/
function shuffleArray(array) {
const shuffled = [...array];
for (let i = shuffled.length - 1; i > 0; i--) {
const j = Math.floor(Math.random() * (i + 1));
[shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
}
return shuffled;
}
/**
============================================================================
URL HELPERS
============================================================================
*/
/**
Get query parameter from URL
@param {string} param - Parameter name
@returns {string|null} Parameter value
*/
function getUrlParam(param) {
const urlParams = new URLSearchParams(window.location.search);
return urlParams.get(param);
}
/**
Update URL without page reload
@param {string} url - New URL
@param {string} title - Page title
*/
function updateUrl(url, title = '') {
window.history.pushState({}, title, url);
}
/**
Copy text to clipboard
@param {string} text - Text to copy
@returns {Promise} Success status
*/
async function copyToClipboard(text) {
try {
await navigator.clipboard.writeText(text);
showToast('Copied to clipboard!', 'success', 2000);
return true;
} catch (error) {
console.error('Clipboard error:', error);
showToast('Failed to copy', 'error');
return false;
}
}
/**
============================================================================
FORM HELPERS
============================================================================
*/
/**
Get form data as object
@param {HTMLFormElement} form - Form element
@returns {Object} Form data as key-value pairs
*/
function getFormData(form) {
const formData = new FormData(form);
const data = {};
for (const [key, value] of formData.entries()) {
// Handle multiple values (checkboxes with same name)
if (data[key]) {
if (!Array.isArray(data[key])) {
data[key] = [data[key]];
}
data[key].push(value);
} else {
data[key] = value;
}
}
return data;
}
/**
Reset form and clear errors
@param {HTMLFormElement} form - Form element
*/
function resetForm(form) {
form.reset();
// Clear error messages
form.querySelectorAll('.form-error').forEach(error => {
error.classList.add('hidden');
error.textContent = '';
});
// Remove error classes from inputs
form.querySelectorAll('.form-input, .form-textarea, .form-select').forEach(input => {
input.classList.remove('error');
});
}
/**
Show field error
@param {string} fieldId - Field ID
@param {string} message - Error message
*/
function showFieldError(fieldId, message) {
const field = document.getElementById(fieldId);
const errorSpan = document.getElementById(`${fieldId}_error`);
if (field) {
field.classList.add('error');
}
if (errorSpan) {
errorSpan.classList.remove('hidden');
errorSpan.textContent = message;
}
}
/**
Clear field error
@param {string} fieldId - Field ID
*/
function clearFieldError(fieldId) {
const field = document.getElementById(fieldId);
const errorSpan = document.getElementById(`${fieldId}_error`);
if (field) {
field.classList.remove('error');
}
if (errorSpan) {
errorSpan.classList.add('hidden');
errorSpan.textContent = '';
}
}
/**
============================================================================
EXPORT (for modules)
============================================================================
*/
// If using modules, export functions
if (typeof module !== 'undefined' && module.exports) {
module.exports = {
formatDate,
formatFullDate,
formatTime,
isValidEmail,
isValidUsername,
checkPasswordStrength,
setElementLoading,
scrollToElement,
truncateText,
escapeHtml,
slugify,
capitalize,
debounce,
throttle,
storeLocal,
getLocal,
removeLocal,
clearLocal,
formatFileSize,
getFileExtension,
isImageFile,
isVideoFile,
uniqueArray,
chunkArray,
shuffleArray,
getUrlParam,
updateUrl,
copyToClipboard,
getFormData,
resetForm,
showFieldError,
clearFieldError
};
}
// Fix: everything after this point (previously ~820 more lines, a
// Modal class plus confirm()/alert()-style helpers built on it) was
// corrupted — a huge number of template literals throughout had their
// backticks stripped (e.g. `id: options.id || modal-${Date.now()},`
// instead of `` id: options.id || `modal-${Date.now()}`, ``), a stray
// section-header fragment ("3⃣⃣ Modal Component" plus a
// mojibake replacement character) had leaked in as bare, uncommented
// text, and the file no longer parsed as valid JavaScript at all —
// confirmed with `node --check`, and confirmed as the actual root
// cause of a real "missing ) after argument list" page error
// (document.getElementById(${fieldId}_error) — same stripped-backtick
// bug — two instances of which are fixed above) that fired on every
// single page that loads this script, since the whole file failed to
// parse and nothing after the error ever ran, including this module's
// own exports. Searched the rest of the codebase for any real use of
// that Modal class (`new Modal(`, `window.Modal`, an import of it) —
// there is none; every other file's own "xModal" identifiers are
// unrelated local DOM variables (avatarModal, notesModal, etc.), not
// this class. It was dead code, so rather than hand-reconstruct ~820
// lines of mangled template literals (every attempted fix would be a
// guess at the original content), it's removed here. If a shared
// modal/confirm/alert helper is needed again later, it should be
// rebuilt cleanly rather than un-mangled from this.
