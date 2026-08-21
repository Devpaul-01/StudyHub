/**
 * ============================================================================
 * StudyHub API Client with Automatic Token Refresh - FIXED VERSION
 * ============================================================================
 */

const API_BASE_URL = '/student';

/**
 * API Client Class with Token Management
 */
class APIClient {
  constructor(baseURL) {
    this.baseURL = baseURL;
    this.isRefreshing = false;
    this.refreshSubscribers = [];
    this.maxRetries = 1;
  }

  /**
   * Check if current page is a public auth page (no token needed)
   */
  isPublicAuthPage() {
    const path = window.location.pathname;
    const publicPaths = [
      '/student/login',
      '/student/register',
      '/student/reset-password',
      '/student/set-password',
      '/student/verify-email',
      '/student/verify-reset',
      '/student/complete-registration'
    ];
    return publicPaths.some(p => path.startsWith(p));
  }

  /**
   * Document 3 §1.6/§2: access_token is now httponly (once the backend's
   * ACCESS_TOKEN_HTTPONLY flag is enabled) — it is no longer readable via
   * document.cookie, by design. getToken() is kept ONLY as a fallback for
   * as long as the backend flag might still be off; once httponly is
   * confirmed on in your environment, this always returns null and every
   * caller correctly falls through to cookie-based auth (the browser
   * attaches access_token automatically to same-origin requests — no
   * Authorization header needed at all).
   */
  getToken() {
    const cookies = document.cookie.split(';');
    for (let cookie of cookies) {
      const [name, value] = cookie.trim().split('=');
      if (name === 'access_token') {
        return value;
      }
    }
    return null;
  }

  /**
   * Document 3 §2.1: csrf_token is deliberately NOT httponly — it's a
   * shared-secret double-submit value, not a bearer credential, so it
   * must stay JS-readable. Read it here and attach as X-CSRF-Token on
   * every mutating request (Document 3 §2.2's enforcement point checks
   * this header against the cookie of the same name).
   */
  getCsrfToken() {
    const cookies = document.cookie.split(';');
    for (let cookie of cookies) {
      const [name, value] = cookie.trim().split('=');
      if (name === 'csrf_token') {
        return value;
      }
    }
    return null;
  }

  /**
   * Best-effort expiry check. Only meaningful while access_token is
   * still JS-readable (httponly=False) — once httponly is on this always
   * returns true (token unreadable), which is fine: it just means we
   * stop trying to proactively refresh ahead of expiry and instead rely
   * on the reactive 401 -> refresh -> retry flow every call already goes
   * through (isTokenExpired's only role is "should I refresh before
   * bothering to make this call at all", which is an optimization, not
   * a security boundary).
   */
  isTokenExpired(token) {
    if (!token) return true;
    
    try {
      const payload = JSON.parse(atob(token.split('.')[1]));
      const now = Math.floor(Date.now() / 1000);
      
      // Check if expired or will expire in next 60 seconds
      return payload.exp < (now + 60);
    } catch (error) {
      console.error('Error checking token expiry:', error);
      return true;
    }
  }

  /**
   * Refresh access token
   */
  async refreshAccessToken() {
    try {
      console.log('🔄 Refreshing access token...');
      
      const response = await fetch(`${this.baseURL}/refresh-token`, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json'
        }
      });

      if (!response.ok) {
        throw new Error('Token refresh failed');
      }

      const data = await response.json();
      
      if (data.status === 'success') {
        console.log('✅ Token refreshed successfully');
        return true;
      }
      
      throw new Error('Token refresh failed');
    } catch (error) {
      console.error('❌ Token refresh error:', error);
      
      // ✅ FIXED: Only clear auth if NOT on public pages
      if (!this.isPublicAuthPage()) {
        this.clearAuth();
      }
      
      return false;
    }
  }

  /**
   * Clear authentication and redirect
   */
  clearAuth() {
    document.cookie = 'access_token=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;';
    document.cookie = 'refresh_token=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;';
    document.cookie = 'csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;';
    
    if (typeof showToast === 'function') {
      showToast('Session expired. Please login again.', 'error');
    }
    
    setTimeout(() => {
      window.location.href = '/student/login';
    }, 1500);
  }

  /**
   * Add subscriber for token refresh
   */
  subscribeTokenRefresh(callback) {
    this.refreshSubscribers.push(callback);
  }

  /**
   * Notify all subscribers when token is refreshed
   */
  onTokenRefreshed(token) {
    this.refreshSubscribers.forEach(callback => callback(token));
    this.refreshSubscribers = [];
  }

  /**
   * Get headers for a request.
   *
   * Document 3 §1.6 rewrite: this used to proactively decode
   * access_token client-side to check expiry and refresh ahead of time.
   * That's no longer possible once access_token is httponly (and doing
   * it via isTokenExpired(null) would loop-refresh on every call, since
   * an unreadable token always looks "expired"). The new model:
   *   - The browser attaches access_token/refresh_token cookies
   *     automatically to every same-origin request — no Authorization
   *     header needed at all for the common case.
   *   - X-CSRF-Token is attached from the (still JS-readable) csrf_token
   *     cookie on every mutating request, per Document 3 §2.2's
   *     enforcement hook.
   *   - Refresh is now purely REACTIVE: handleResponse() below detects a
   *     401 and triggers refreshAccessToken() + one retry, rather than
   *     this method guessing ahead of time whether the token has expired.
   *   - If access_token happens to still be JS-readable (httponly=False,
   *     i.e. the backend flag not yet flipped in this environment), it's
   *     still attached as a Bearer header for backward compatibility —
   *     harmless either way, since the backend checks the cookie first
   *     and the header second.
   */
  async getHeaders(isJSON = true) {
    const headers = {};

    if (isJSON) {
      headers['Content-Type'] = 'application/json';
    }

    if (this.isPublicAuthPage()) {
      return headers;
    }

    const token = this.getToken();
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const csrfToken = this.getCsrfToken();
    if (csrfToken) {
      headers['X-CSRF-Token'] = csrfToken;
    }

    return headers;
  }

  /**
   * Legacy synchronous-shaped headers path, kept for anything that still
   * awaits getHeaders() expecting the old refresh-then-return behavior —
   * unused internally now (see getHeaders above) but left in place in
   * case external callers depended on the old signature/behavior.
   */
  async _legacyGetHeadersUnused(isJSON = true) {
    const headers = {};
    
    // ✅ FIXED: If on public auth page, don't try to refresh tokens
    if (this.isPublicAuthPage()) {
      if (isJSON) {
        headers['Content-Type'] = 'application/json';
      }
      return headers;
    }
    
    let token = this.getToken();
    
    // Check if token needs refresh
    if (this.isTokenExpired(token)) {
      console.log('⚠️ Token expired or expiring soon, refreshing...');
      
      // Prevent multiple simultaneous refresh requests
      if (this.isRefreshing) {
        return new Promise((resolve) => {
          this.subscribeTokenRefresh((newToken) => {
            headers['Authorization'] = `Bearer ${newToken}`;
            if (isJSON) {
              headers['Content-Type'] = 'application/json';
            }
            resolve(headers);
          });
        });
      }
      
      this.isRefreshing = true;
      
      const refreshed = await this.refreshAccessToken();
      
      this.isRefreshing = false;
      
      if (refreshed) {
        token = this.getToken();
        this.onTokenRefreshed(token);
      } else {
        throw new Error('Authentication failed');
      }
    }
    
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    
    if (isJSON) {
      headers['Content-Type'] = 'application/json';
    }
    
    return headers;
  }

  /**
   * Handle API response
   */
  async handleResponse(response) {
    const contentType = response.headers.get('content-type');
    
    if (contentType && contentType.includes('application/json')) {
      const data = await response.json();
      
      if (!response.ok) {
        const err = new Error(data.message || 'Request failed');
        err.status = response.status;
        throw err;
      }
      
      return data;
    }
    
    if (!response.ok) {
      const err = new Error('Request failed');
      err.status = response.status;
      throw err;
    }
    
    return response;
  }

  /**
   * Document 3 §1.6: reactive refresh-and-retry-once wrapper.
   *
   * Replaces the old proactive "decode the token, check exp, refresh
   * ahead of time" model, which stops working once access_token is
   * httponly (nothing to decode client-side anymore). Instead: make the
   * request; if it comes back 401, refresh once (de-duped against
   * concurrent 401s via this.isRefreshing/refreshSubscribers, same as
   * before) and retry the original request exactly once. A second 401
   * after a successful refresh is treated as a genuine auth failure.
   */
  async _requestWithRefresh(doFetch) {
    if (this.isPublicAuthPage()) {
      const response = await doFetch();
      return await this.handleResponse(response);
    }

    try {
      const response = await doFetch();
      return await this.handleResponse(response);
    } catch (error) {
      if (error.status !== 401) {
        throw error;
      }

      // De-dupe concurrent refreshes: only one in-flight refresh at a time.
      if (this.isRefreshing) {
        await new Promise((resolve) => this.subscribeTokenRefresh(resolve));
      } else {
        this.isRefreshing = true;
        const refreshed = await this.refreshAccessToken();
        this.isRefreshing = false;

        if (!refreshed) {
          throw new Error('Authentication failed');
        }
        this.onTokenRefreshed();
      }

      // Retry exactly once after a successful refresh.
      const retryResponse = await doFetch();
      return await this.handleResponse(retryResponse);
    }
  }

  /**
   * GET request
   */
  async get(endpoint, params = {}) {
    const queryString = Object.keys(params).length > 0
      ? '?' + new URLSearchParams(params).toString()
      : '';
    
    const url = `${this.baseURL}${endpoint}${queryString}`;

    return this._requestWithRefresh(async () => {
      const headers = await this.getHeaders();
      return fetch(url, {
        method: 'GET',
        headers: headers,
        credentials: 'include'
      });
    }).catch((error) => {
      console.error('GET request failed:', error);
      throw error;
    });
  }

  /**
   * POST request - ✅ FIXED: Don't require auth for login/register
   */
  async post(endpoint, data = {}, isFormData = false) {
    const url = `${this.baseURL}${endpoint}`;
    const isAuthEndpoint = ['/login', '/register', '/refresh-token', '/verify-email', '/complete-registration', '/reset-password', '/set-password'].some(e => endpoint.includes(e));

    return this._requestWithRefresh(async () => {
      const headers = isAuthEndpoint ? {} : await this.getHeaders(!isFormData);

      if (!isFormData) {
        headers['Content-Type'] = 'application/json';
      }

      const options = {
        method: 'POST',
        credentials: 'include',
        headers: headers
      };

      if (isFormData) {
        delete options.headers['Content-Type'];
        options.body = data;
      } else {
        options.body = JSON.stringify(data);
      }

      return fetch(url, options);
    }).catch((error) => {
      console.error('POST request failed:', error);
      throw error;
    });
  }
  async put(endpoint, data = {}, isFormData = false) {
    const url = `${this.baseURL}${endpoint}`;
    const isAuthEndpoint = ['/login', '/register', '/refresh-token', '/verify-email', '/complete-registration', '/reset-password', '/set-password'].some(e => endpoint.includes(e));

    return this._requestWithRefresh(async () => {
      const headers = isAuthEndpoint ? {} : await this.getHeaders(!isFormData);

      if (!isFormData) {
        headers['Content-Type'] = 'application/json';
      }

      const options = {
        method: 'PUT',
        credentials: 'include',
        headers: headers
      };

      if (isFormData) {
        delete options.headers['Content-Type'];
        options.body = data;
      } else {
        options.body = JSON.stringify(data);
      }

      return fetch(url, options);
    }).catch((error) => {
      console.error('PUT request failed:', error);
      throw error;
    });
  }

  /**
   * PATCH request
   */
  async patch(endpoint, data = {}) {
    const url = `${this.baseURL}${endpoint}`;

    return this._requestWithRefresh(async () => {
      const headers = await this.getHeaders();
      return fetch(url, {
        method: 'PATCH',
        headers: headers,
        credentials: 'include',
        body: JSON.stringify(data)
      });
    }).catch((error) => {
      console.error('PATCH request failed:', error);
      throw error;
    });
  }

  /**
   * DELETE request
   */
  async delete(endpoint) {
    const url = `${this.baseURL}${endpoint}`;

    return this._requestWithRefresh(async () => {
      const headers = await this.getHeaders();
      return fetch(url, {
        method: 'DELETE',
        headers: headers,
        credentials: 'include'
      });
    }).catch((error) => {
      console.error('DELETE request failed:', error);
      throw error;
    });
  }

  /**
   * Upload file
   */
  async uploadFile(endpoint, fileInput, additionalData = {}) {
    const formData = new FormData();
    
    if (fileInput.files && fileInput.files[0]) {
      formData.append('file', fileInput.files[0]);
    }
    
    for (const [key, value] of Object.entries(additionalData)) {
      formData.append(key, value);
    }
    
    return await this.post(endpoint, formData, true);
  }

  /**
   * Verify authentication status - ✅ FIXED: Don't call on public pages
   */
  async verifyAuth() {
    try {
      // ✅ FIXED: If on public auth page, don't verify
      if (this.isPublicAuthPage()) {
        console.log('ℹ️ On public auth page, skipping auth verification');
        return null;
      }

      // Document 3 §1.6 fix: previously bailed out early if getToken()
      // couldn't read access_token client-side — that's always the case
      // once access_token is httponly, so this would have permanently
      // reported "not authenticated" for genuinely logged-in users. The
      // cookie is attached automatically by the browser regardless of
      // whether JS can read it, so just make the request and let the
      // server be the source of truth; only fall back to an explicit
      // Bearer header if the token happens to still be JS-readable.
      const token = this.getToken();
      const headers = token ? { 'Authorization': `Bearer ${token}` } : {};

      let response = await fetch(`${this.baseURL}/verify-auth`, {
        method: 'GET',
        credentials: 'include',
        headers: headers
      });

      if (response.status === 401) {
        // Reactive refresh-and-retry-once, same pattern as _requestWithRefresh.
        const refreshed = await this.refreshAccessToken();
        if (!refreshed) {
          return null;
        }
        response = await fetch(`${this.baseURL}/verify-auth`, {
          method: 'GET',
          credentials: 'include',
          headers: this.getToken() ? { 'Authorization': `Bearer ${this.getToken()}` } : {}
        });
      }

      const data = await response.json();
      
      if (data.authenticated) {
        return data.data.user;
      }
      
      return null;
    } catch (error) {
      console.error('Auth verification failed:', error);
      return null;
    }
  }
}

// Create global API instance
const api = new APIClient(API_BASE_URL);

/**
 * ============================================================================
 * AUTHENTICATION HELPERS
 * ============================================================================
 */

/**
 * Document 3 §1.4/§1.6: both of these used to decode the JWT client-side
 * from a directly-readable access_token cookie. That's fundamentally
 * incompatible with httponly=True — an httponly cookie is invisible to
 * document.cookie/atob() by design, that's the entire security property
 * being added. There's no client-side substitute for this once the flag
 * is on, so both become async and delegate to the server
 * (GET /student/auth/me or /student/verify-auth, whichever the caller
 * already has data from) rather than decoding anything locally.
 *
 * Callers of the old synchronous isAuthenticated()/getCurrentUser() need
 * to add `await` — flagged here rather than silently kept sync-shaped
 * and wrong. Per Document 3 §1.6 point 4: user display data should come
 * from the login/register response body (already handled at login time)
 * or from an explicit /auth/me call — not from decoding a cookie.
 */
async function isAuthenticated() {
  const user = await api.verifyAuth();
  return user !== null;
}

async function getCurrentUser() {
  return await api.verifyAuth();
}

async function requireAuth() {
  const user = await api.verifyAuth();
  
  if (!user) {
    window.location.href = '/student/login';
    return false;
  }
  
  return true;
}

async function logout() {
  try {
    await api.post('/logout');
    window.location.href = '/student/login';
  } catch (error) {
    console.error('Logout failed:', error);
    api.clearAuth();
  }
}

/**
 * ============================================================================
 * UI HELPER FUNCTIONS
 * ============================================================================
 */

function setButtonLoading(button, isLoading, loadingText = 'Loading...') {
  if (!button) return;
  
  if (isLoading) {
    button.dataset.originalText = button.innerHTML;
    button.innerHTML = `<span class="spinner"></span> ${loadingText}`;
    button.disabled = true;
  } else {
    button.innerHTML = button.dataset.originalText || loadingText;
    button.disabled = false;
  }
}

function debounce(func, wait) {
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

function isValidEmail(email) {
  const regex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  return regex.test(email);
}

function isValidUsername(username) {
  const regex = /^[a-z0-9]{3,20}$/;
  return regex.test(username);
}

function showToast(message, type = "info", duration = 6000) {
  let container = document.getElementById("toast-container");
  if (!container) {
    container = document.createElement("div");
    container.id = "toast-container";
    // Container itself is the fixed, bottom-centered anchor.
    // Toasts inside are normal-flow children stacked via flex column-reverse
    // (newest toast appears closest to the bottom, like most modern apps).
    container.style.cssText = `
      position: fixed;
      bottom: 24px;
      left: 50%;
      transform: translateX(-50%);
      z-index: var(--z-tooltip, 9999);
      display: flex;
      flex-direction: column-reverse;
      align-items: center;
      gap: 10px;
      max-width: 90vw;
      pointer-events: none;
    `;
    document.body.appendChild(container);
  }

  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;

  // Status colors map to your existing CSS variables; default/info uses accent
  const accentColors = {
    success: "var(--success)",
    error: "var(--danger)",
    warning: "var(--warning)",
    info: "var(--accent)"
  };
  const stripeColor = accentColors[type] || accentColors.info;

  toast.style.cssText = `
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px 20px;
    border-radius: var(--radius-md);
    font-family: sans-serif;
    font-size: 14px;
    color: var(--text-primary);
    background: var(--bg-card);
    border: 1px solid var(--border-light);
    border-left: 3px solid ${stripeColor};
    box-shadow: var(--shadow-lg);
    opacity: 0;
    transform: translateY(12px);
    transition: opacity var(--transition-base), transform var(--transition-base);
    max-width: 90vw;
    white-space: nowrap;
    pointer-events: auto;
  `;

  toast.textContent = message;
  container.appendChild(toast);

  // Fade/slide in from below, matching modern app toast behavior
  requestAnimationFrame(() => {
    toast.style.opacity = "1";
    toast.style.transform = "translateY(0)";
  });

  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(12px)";
    setTimeout(() => toast.remove(), 200);
  }, duration);
}

window.addEventListener('unhandledrejection', (event) => {
  console.error('Unhandled promise rejection:', event.reason);
  if (typeof showToast === 'function') {
    showToast('An error occurred. Please try again.', 'error');
  }
});