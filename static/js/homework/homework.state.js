/**
 * ============================================================================
 * HOMEWORK STATE MANAGEMENT
 * Centralized state for homework section
 * ============================================================================
 */

export const homeworkState = {
  // Active tab: 'my-homework' or 'connections-homework'
  activeTab: 'my-homework',
  lastToggleId: null,
  _editResources: [],
  
  // My assignments data
  myAssignments: [],
  myAssignmentsStats: {},
  // Cursor pagination (infinite scroll) for "My Work"
  myAssignmentsCursor: null,
  myAssignmentsHasMore: false,
  myAssignmentsLoadingMore: false,
  
  // Connections homework data
  connectionsHomework: [],
  // Cursor pagination (infinite scroll) for "Connections"
  connectionsCursor: null,
  connectionsHasMore: false,
  connectionsLoadingMore: false,

  // Batch 3: "Offers on my work" (people who offered to help on MY
  // assignments) and "Helping others" (assignments I'm helping with).
  // Both backing routes (GET /homework/my-help-requests and
  // GET /homework/helping) already existed with no tab in the UI calling
  // either — there was no inbox for incoming offers and no "what am I
  // currently helping with" list, which the audit flagged as the two
  // screens needed to make the peer-help loop actually usable day to day.
  // No pagination here (unlike myAssignments/connectionsHomework above):
  // both backend routes return every matching row in one response, not a
  // cursor-paginated page, so there's nothing to page through client-side.
  myHelpRequests: [],
  myHelpRequestsStats: {},
  myHelpRequestsStatusFilter: 'all',
  helpingWith: [],
  helpingWithStats: {},
  helpingWithStatusFilter: 'all',

  streakData: {
  current_streak: 0,
  longest_streak: 0,
  streak_at_risk: false,
  helped_today: false
},


  
  // Current filters
  filters: {
    status: 'active',
    subject: null,
    difficulty: null
  },
  
  // Loading states
  loading: {
    myHomework: false,
    stats: false,
    connectionsHomework: false,
    myHelpRequests: false,
    helpingWith: false
  },
  dataLoaded: {
  myHomework: false,
  connectionsHomework: false,
  stats: false,
  myHelpRequests: false,
  helpingWith: false
},
  
  // Current modal data
  currentModal: null,
  currentSubmission: null,
  currentAssignment: null,
  
  // Upload state
  uploadedResources: [],
  statsData: null,
  
  setStatsData(data) {
  this.statsData = data;
  this.dataLoaded.stats = true;
},

/**
 * Get stats data
 */
getStatsData() {
  return this.statsData;
},

/**
 * Check if data is loaded
 */
isDataLoaded(type) {
  return this.dataLoaded[type] || false;
},

/**
 * Mark data as loaded
 */
markDataLoaded(type) {
  this.dataLoaded[type] = true;
},

/**
 * Force refresh (clear cache)
 */
forceRefresh(type) {
  this.dataLoaded[type] = false;
},
  
  /**
   * Set active tab
   */
  setLastToggle(id){
    this.lastToggleId = id;
  },
  getLastToggle(id){
    return this.lastToggleId || null;
  },
  setActiveTab(tab) {
    this.activeTab = tab;
  },
  setStreakData(data) {
  this.streakData = data;
},
  /**
   * Get active tab
   */
  getActiveTab() {
    return this.activeTab;
  },
  
  /**
   * Set my assignments.
   * @param {object} data - API response `data` payload (assignments, stats, next_cursor, has_more)
   * @param {boolean} append - true when this is an infinite-scroll "load more" page;
   *                           false to replace the list (first page / refresh)
   */
  setMyAssignments(data, append = false) {
    const incoming = data.assignments || [];
    this.myAssignments = append ? this.myAssignments.concat(incoming) : incoming;

    // Stats reflect the full filtered set server-side, always safe to overwrite
    if (data.stats) {
      this.myAssignmentsStats = data.stats;
    }

    this.myAssignmentsCursor = data.next_cursor || null;
    this.myAssignmentsHasMore = !!data.has_more;
    this.dataLoaded.myHomework = true;
  },

  /**
   * Set connections homework feed.
   * @param {object} data - API response `data` payload (homework, next_cursor, has_more)
   * @param {boolean} append - true when this is an infinite-scroll "load more" page;
   *                           false to replace the list (first page / refresh)
   */
  setConnectionsHomework(data, append = false) {
    const incoming = data.homework || [];
    this.connectionsHomework = append ? this.connectionsHomework.concat(incoming) : incoming;

    this.connectionsCursor = data.next_cursor || null;
    this.connectionsHasMore = !!data.has_more;
    this.dataLoaded.connectionsHomework = true;
  },

  // Batch 3: "Offers on my work" — data payload shape is
  // { help_requests: [...], total, stats: {pending, submitted, reviewed, completed} }
  // (see get_my_help_requests() in routes/student/homework_system.py).
  setMyHelpRequests(data) {
    this.myHelpRequests = data.help_requests || [];
    this.myHelpRequestsStats = data.stats || {};
    this.dataLoaded.myHelpRequests = true;
  },
  setMyHelpRequestsStatusFilter(status) {
    this.myHelpRequestsStatusFilter = status || 'all';
  },
  getMyHelpRequestsStatusFilter() {
    return this.myHelpRequestsStatusFilter;
  },

  // Batch 3: "Helping others" — data payload shape is
  // { helping_with: [...], total, stats: {...} }
  // (see get_homework_im_helping_with() in the same file).
  setHelpingWith(data) {
    this.helpingWith = data.helping_with || [];
    this.helpingWithStats = data.stats || {};
    this.dataLoaded.helpingWith = true;
  },
  setHelpingWithStatusFilter(status) {
    this.helpingWithStatusFilter = status || 'all';
  },
  getHelpingWithStatusFilter() {
    return this.helpingWithStatusFilter;
  },
  
  /**
   * Set loading state
   */
  setLoading(type, isLoading) {
    this.loading[type] = isLoading;
  },
  
  /**
   * Get assignment by ID
   */
  getAssignmentById(id) {
    return this.myAssignments.find(a => a.id === parseInt(id));
  },
  
  /**
   * Get homework by ID
   */
  getHomeworkById(id) {
    return this.connectionsHomework.find(h => h.id === parseInt(id));
  },
  
  /**
   * Add uploaded resource
   */
  addUploadedResource(resource) {
    this.uploadedResources.push(resource);
  },
  
  /**
   * Clear uploaded resources
   */
  clearUploadedResources() {
    this.uploadedResources = [];
  },
  
  /**
   * Get uploaded resources
   */
  getUploadedResources() {
    return this.uploadedResources;
  },
  
  /**
   * Remove uploaded resource
   */
  removeUploadedResource(index) {
    this.uploadedResources.splice(index, 1);
  },
  
  /**
   * Set current submission
   */
  setCurrentSubmission(submission) {
    this.currentSubmission = submission;
  },
  
  /**
   * Get current submission
   */
  getCurrentSubmission() {
    return this.currentSubmission;
  },
  
  /**
   * Set current assignment
   */
  setCurrentAssignment(assignment) {
    this.currentAssignment = assignment;
  },

// Add these methods with the other methods:

setEditResources(resources) {
  this._editResources = resources || [];
},

getEditResources() {
  return this._editResources;
},
  
  
  /**
   * Reset state
   */
  reset() {
    this.myAssignments = [];
    this.myAssignmentsCursor = null;
    this.myAssignmentsHasMore = false;
    this.connectionsHomework = [];
    this.connectionsCursor = null;
    this.connectionsHasMore = false;
    this.uploadedResources = [];
    this.currentSubmission = null;
    this.currentAssignment = null;
    // Batch 3
    this.myHelpRequests = [];
    this.myHelpRequestsStats = {};
    this.helpingWith = [];
    this.helpingWithStats = {};
  }
};
window.homeworkState = homeworkState;
