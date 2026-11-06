// ============================================================================
// CONNECTION STATE MANAGEMENT
// ============================================================================

// ============================================================================
// CONNECTION STATE MANAGEMENT (FIXED)
// ============================================================================

class ConnectionState {
  constructor() {
    this.connections = {
      connected: [],
      received: [],
      sent: [],
      suggestions: [],
      discovery: []
    };

    this.loadedFilters = {
      connected: false,
      received: false,
      sent: false,
      suggestions: false,
      discovery: false
    };

    this.badges = {
      received: 0,
      sent: 0
    };

    this.currentTab = 'received';
    this.connectedCurrentPage = 1;
    this.hasMoreConnected = true;
    this.currentConnect = {};
  }

  setPendingConnection(userId, userName){
    this.currentConnect = {
      user_name: userName,
      user_id: userId
    };
  }
  getPendingConnection(){
    return this.currentConnect;
  }

  // Tab Management
  switchTab(tab) {
    this.currentTab = tab;
  }

  getCurrentTab() {
    return this.currentTab;
  }

  // Connection Data
  setConnections(filter, data) {
    this.connections[filter] = data;
    this.loadedFilters[filter] = true;
  }

  getConnections(filter) {
    return this.connections[filter] || [];
  }

  appendConnections(filter, data) {
    this.connections[filter] = [...this.connections[filter], ...data];
  }

  // Filter Status
  setFilterLoaded(filter) {
    this.loadedFilters[filter] = true;
  }

  isFilterLoaded(filter) {
    return this.loadedFilters[filter];
  }

  // Badge Counts
  setBadge(type, count) {
    this.badges[type] = count;
  }

  getBadge(type) {
    return this.badges[type];
  }

  getAllBadges() {
    return this.badges;
  }

  // Pagination (Connected only)
  getConnectedCurrentPage() {
    return this.connectedCurrentPage;
  }

  incrementConnectedPage() {
    this.connectedCurrentPage++;
  }

  resetConnectedPage() {
    this.connectedCurrentPage = 1;
    this.hasMoreConnected = true;
  }

  setHasMoreConnected(value) {
    this.hasMoreConnected = value;
  }

  getHasMoreConnected() {
    return this.hasMoreConnected;
  }

  // Reset All
  reset() {
    this.connections = {
      connected: [],
      received: [],
      sent: [],
      suggestions: [],
      discovery: []
    };
    this.loadedFilters = {
      connected: false,
      received: false,
      sent: false,
      suggestions: false,
      discovery: false
    };
    this.resetConnectedPage();
  }
}

export const connectionState = new ConnectionState();