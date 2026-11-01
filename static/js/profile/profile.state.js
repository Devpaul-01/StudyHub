/**
 * ============================================================================
 * PROFILE STATE — profile.state.js
 * Save as: /static/js/profile/profile.state.js
 * Mirrors the FeedState class pattern from feed.state.js
 * ============================================================================
 */

class ProfileState {
  constructor() {
    this._initialized = false;

    // Cached API data
    this.profileData  = null;
    this.posts        = [];
    this.postsFilter  = 'all';
    this.stats        = null;
    this.heatmap      = null;
    this.connections  = [];
    this.reputation   = null;
    this.repHistory   = null;
    this.myBadges     = null; // Batch 3
    this.badgeProgress = null; // Batch 3

    // Which tabs have been fetched already
    this.loaded = {
      profile     : false,
      posts       : false,
      stats       : false,
      connections : false,
      reputation  : false,
    };

    this.currentTab = 'posts';
  }

  // ── Initialization flag ──────────────────────────────────────────────────

  setInitialized(val)  { this._initialized = val; }
  isInitialized()      { return this._initialized; }

  // ── Loaded flags ─────────────────────────────────────────────────────────

  markLoaded(key)  { this.loaded[key] = true; }
  isLoaded(key)    { return this.loaded[key] === true; }

  /** Force a re-fetch next time this tab is shown */
  invalidate(key)  { this.loaded[key] = false; }

  /** Full reset — e.g. after logout / token change */
  invalidateAll() {
    Object.keys(this.loaded).forEach(k => (this.loaded[k] = false));
    this._initialized = false;
    this.profileData  = null;
    this.posts        = [];
    this.postsFilter  = 'all';
    this.stats        = null;
    this.heatmap      = null;
    this.connections  = [];
    this.reputation   = null;
    this.repHistory   = null;
    this.myBadges     = null; // Batch 3
    this.badgeProgress = null; // Batch 3
    this.currentTab   = 'posts';
  }

  // ── Tab ──────────────────────────────────────────────────────────────────

  setCurrentTab(tab)  { this.currentTab = tab; }
  getCurrentTab()     { return this.currentTab; }
}

export const profileState = new ProfileState();
