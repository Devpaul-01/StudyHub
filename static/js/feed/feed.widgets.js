/**
 * ============================================================================
 * FEED WIDGETS
 * Widget HTML generation
 * ============================================================================
 */

/**
 * Create widget based on ID
 */

export function createWidget(widgetId, index, widgets) {
  const widgetMap = {
    'suggestedConnections': () => createSuggestedConnectionsWidget(widgets.suggestedConnections),
    'popularTags': () => createPopularTagsWidget(widgets.popularTags),
    'risingStars': () => createRisingStarsWidget(widgets.risingStars),
    'openThreads': () => createOpenThreadsWidget(widgets.openThreads),
    'canHelp': () => createHelpSuggestionsWidget(widgets.canHelp),
    'topBadgeEarners': () => createTopBadgeEarnersWidget(widgets.topBadgeEarners)
  };

  return widgetMap[widgetId] ? widgetMap[widgetId]() : '';
}

/**
 * Suggested Connections Widget
 */
function createSuggestedConnectionsWidget(connections) {
  const items = connections.slice(0, 10);

  const cards = items.map(item => `
    <div class="user-card"
         data-action="view-profile"
         data-username="${item.user?.username}">
      
      <img src="${item.user?.avatar || '/static/default-avatar.png'}"
           class="user-avatar"
           style="width: 48px; height: 48px; border-radius: 50%; object-fit: cover; display: block;">
      <div class="user-name">${item.user?.name}</div>
      <div class="user-bio">${item.user?.department || 'Student'}</div>
      <div class="match-score">Match: ${item.match_score}%</div>

      <button class="connect-btn"
              data-action="open-connection-request"
              data-user-name='${item.user.name}'
              data-user-id="${item.user?.id}">
        Connect
      </button>
    </div>
  `).join('');

  return `
    <div class="carousel-widget">
      <div class="widget-header">
        <h3>Suggested Connections</h3>
        <button class="widget-action" data-action="navigate-connections">See All</button>
      </div>
      <div class="carousel-container">${cards}</div>
    </div>
  `;
}

/**
 * Popular Tags Widget
 */
function createPopularTagsWidget(tags) {
  const tagEntries = Object.entries(tags).slice(0, 10);

  const cards = tagEntries.map(([tag, count]) => `
    <div class="tag-card"
         data-action="view-tag-posts"
         data-tag="${tag}">
      <div class="tag-icon">🏷️</div>
      <div class="tag-name">${tag}</div>
      <div class="tag-count">${count} posts</div>
      <button class='view-tag-post' data-action='view-tag-posts' data-tag=${tag}>View Posts</button>
    </div>
  `).join('');

  return `
    <div class="carousel-widget">
      <div class="widget-header"><h3>Popular Tags</h3></div>
      <div class="carousel-container">${cards}</div>
    </div>
  `;
}

function createRisingStarsWidget(stars) {
  const cards = stars.slice(0, 10).map(star => `
    <div class="user-card"
         data-action="view-profile"
         data-username="${star.user?.username}">
      
      <img src="${star.user?.avatar || '/static/default-avatar.png'}"
           class="user-avatar"
           style="width: 48px; height: 48px; border-radius: 50%; object-fit: cover; display: block;">
      <div class="user-name">${star.user?.name}</div>
      <div class="user-bio">${star.user?.department || 'Student'}</div>
      <div class="stat-badge">${star.trend || '📈'} +${star.weekly_gain || 0}</div>
    </div>
  `).join('');

  return `
    <div class="carousel-widget">
      <div class="widget-header"><h3>Rising Stars</h3></div>
      <div class="carousel-container">${cards}</div>
    </div>
  `;
}




/**
 * Open Threads Widget
 */
function createOpenThreadsWidget(threads) {
  const cards = threads.slice(0, 10).map(thread => {
    const progress = (thread.member_count / thread.max_members) * 100;

    return `
      <div class="thread-card">
        <div class="thread-title">${thread.title}</div>
        <div class="thread-meta">${thread.member_count}/${thread.max_members} members</div>

        <div class="thread-progress">
          <div class="thread-progress-bar" style="width:${progress}%"></div>
        </div>

        <button class="view-thread-btn"
                data-action="view-thread"
                data-thread-id="${thread.id}"
                data-thread-type="thread">
          View Thread
        </button>
      </div>
    `;
  }).join('');

  return `
    <div class="carousel-widget">
      <div class="widget-header"><h3>Open Study Threads</h3></div>
      <div class="carousel-container">${cards}</div>
    </div>
  `;
}

/**
 * Help Suggestions Widget
 */
function createHelpSuggestionsWidget(suggestions) {
  const cards = suggestions.slice(0, 10).map(item => `
    <div class="user-card"
         data-action="view-profile"
         data-username="${item.user?.username}">
      
      <img src="${item.user?.avatar || '/static/default-avatar.png'}"
           class="user-avatar"
           style="width: 48px; height: 48px; border-radius: 50%; object-fit: cover; display: block;">
      <div class="user-name">${item.user?.name}</div>
      <div class="user-bio">${item.match_details?.can_help_with?.[0] || 'Needs help'}</div>

      <button class="connect-btn"
      data-action="open-connection-request"
              data-user-name='${item.user.name}'
            
              
              data-user-id="${item.user?.id}">
        Offer Help
      </button>
    </div>
  `).join('');

  return `
    <div class="carousel-widget">
      <div class="widget-header"><h3>People You Can Help</h3></div>
      <div class="carousel-container">${cards}</div>
    </div>
  `;
}

/**
 * Top Badge Earners Widget
 */
function createTopBadgeEarnersWidget(earners) {
  const cards = earners.slice(0, 10).map(earner => `
    <div class="user-card"
         data-action="view-profile"
         data-username="${earner.user?.username}">
      
      <img src="${earner.user?.avatar || '/static/default-avatar.png'}"
           class="user-avatar"
           style="width: 48px; height: 48px; border-radius: 50%; object-fit: cover; display: block;">
      <div class="user-name">${earner.user?.name}</div>
      <div class="user-bio">${earner.user?.department}</div>
      <div class="stat-badge">🏆 ${earner.stats?.total_badges || 0}</div>

      <button class="connect-btn"
      data-user-name="${earner.user.name}"
              data-action="open-connection-request"
              data-user-id="${earner.user?.id}">
        Connect
      </button>
    </div>
  `).join('');

  return `
    <div class="carousel-widget">
      <div class="widget-header"><h3>Top Badge Earners</h3></div>
      <div class="carousel-container">${cards}</div>
    </div>
  `;
}