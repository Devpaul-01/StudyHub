/**
 * ============================================================================
 * ANALYTICS TEMPLATES — analytics.templates.js
 * Save as: /static/js/analytics/analytics.templates.js
 *
 * Pure functions: data in → HTML string out.
 * No DOM writes happen here — all rendering is done by analytics.delegation.js.
 * Mirrors the pattern of profile.templates.js.
 * ============================================================================
 */

// ── Shared formatter ─────────────────────────────────────────────────────────

function fmt(n) {
  if (n === null || n === undefined) return '—';
  n = Number(n);
  if (isNaN(n)) return '—';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000)     return (n / 1_000).toFixed(1) + 'k';
  return n.toString();
}

function signed(n) {
  n = Number(n);
  return n >= 0 ? `+${fmt(n)}` : fmt(n);
}

// ── Skeleton loader ──────────────────────────────────────────────────────────

export function buildAnalyticsSkeleton() {
  return `
    <div class="an-skeleton-wrap">
      <div class="an-skel an-skel--hero"></div>
      <div class="an-skel an-skel--hero"></div>
      <div class="an-skel an-skel--hero"></div>
      <div class="an-skel an-skel--hero"></div>
      <div class="an-skel an-skel--wide"></div>
      <div class="an-skel an-skel--block"></div>
      <div class="an-skel an-skel--block"></div>
      <div class="an-skel an-skel--block"></div>
    </div>`;
}

// ── Hero Stats ───────────────────────────────────────────────────────────────

export function buildHeroStats(data) {
  if (!data) return '';

  const h  = data.hero_stats;
  const c  = data.current_stats;
  const al = h.activity_level;

  const repChange = h.reputation_change;
  const repSign   = repChange >= 0 ? '+' : '';
  const repClass  = repChange >= 0 ? 'an-badge--pos' : 'an-badge--neg';
  const repLabel  = repChange >= 0 ? '▲ up' : '▼ down';

  return `
    <!-- Views -->
    <div class="an-hero-card">
      <div class="an-hero-icon">👁️</div>
      <div class="an-hero-value">${fmt(h.weekly_views)}</div>
      <div class="an-hero-label">Views this week</div>
      <div class="an-hero-badge an-badge--neu">7d</div>
    </div>

    <!-- Helpful -->
    <div class="an-hero-card">
      <div class="an-hero-icon">🤝</div>
      <div class="an-hero-value">${fmt(h.helpful_count)}</div>
      <div class="an-hero-label">Helpful marks</div>
      <div class="an-hero-badge an-badge--neu">${fmt(c.total_helpful)} total</div>
    </div>

    <!-- Dept rank -->
    <div class="an-hero-card">
      <div class="an-hero-icon">🏆</div>
      <div class="an-hero-value">#${h.department_rank}</div>
      <div class="an-hero-label">Dept. rank</div>
      <div class="an-hero-badge an-badge--pos">${c.reputation_level}</div>
    </div>

    <!-- Reputation change -->
    <div class="an-hero-card">
      <div class="an-hero-icon">⭐</div>
      <div class="an-hero-value">${repSign}${fmt(Math.abs(repChange))}</div>
      <div class="an-hero-label">Rep gained</div>
      <div class="an-hero-badge ${repClass}">${repLabel}</div>
    </div>

    <!-- Activity level -->
    <div class="an-hero-card an-hero-card--wide">
      <div class="an-hero-icon">${al.emoji}</div>
      <div class="an-hero-value">${al.level}</div>
      <div class="an-hero-label">Activity level</div>
      <div class="an-activity-dot" style="background:${al.color}"></div>
    </div>`;
}

// ── Activity Heatmap ─────────────────────────────────────────────────────────

export function buildHeatmap(data) {
  if (!data) return '<p class="an-empty">No activity data yet.</p>';

  const { heatmap, summary } = data;

  // Build month label positions
  const monthPositions = [];
  let lastMonth = null;
  heatmap.forEach((day, i) => {
    const m = new Date(day.date).getMonth();
    if (m !== lastMonth) {
      monthPositions.push({ idx: i, label: new Date(day.date).toLocaleString('default', { month: 'short' }) });
      lastMonth = m;
    }
  });

  // Each week is a column; 7 rows (Mon–Sun).
  // We render cells left-to-right, top-to-bottom in CSS grid-auto-flow: column.
  const cells = heatmap.map(day => {
    const tooltip = [
      new Date(day.date).toLocaleDateString('default', { weekday: 'short', month: 'short', day: 'numeric' }),
      `Score: ${day.score}`,
      `Posts: ${day.posts} · Comments: ${day.comments}`,
    ].join('|');
    return `<div class="an-heat-cell an-heat-${day.level}" data-tip="${tooltip}"></div>`;
  }).join('');

  // Month labels: rough positioning via inline flex spans
  const monthLabels = monthPositions.map(({ label }) =>
    `<span class="an-month-lbl">${label}</span>`
  ).join('');

  return `
    <div class="an-heatmap-months">${monthLabels}</div>
    <div class="an-heatmap-grid">${cells}</div>
    <div class="an-heatmap-legend">
      <span>Less</span>
      <span class="an-leg-cell" style="background:var(--an-heat-0)"></span>
      <span class="an-leg-cell" style="background:var(--an-heat-1)"></span>
      <span class="an-leg-cell" style="background:var(--an-heat-2)"></span>
      <span class="an-leg-cell" style="background:var(--an-heat-3)"></span>
      <span class="an-leg-cell" style="background:var(--an-heat-4)"></span>
      <span>More</span>
    </div>
    <div class="an-heatmap-stats">
      <span><strong>${summary.active_days}</strong> active days</span>
      <span><strong>${summary.current_streak}</strong> day streak 🔥</span>
      <span>Avg score: <strong>${summary.avg_daily_score}</strong></span>
    </div>`;
}

// ── AI Insights ──────────────────────────────────────────────────────────────

export function buildInsights(data) {
  if (!data?.insights?.length) {
    return `<p class="an-empty">Keep posting to unlock personalised insights!</p>`;
  }

  return data.insights.map((ins, i) => `
    <div class="an-insight-card" style="animation-delay:${i * 0.08}s">
      <div class="an-insight-head">${ins.icon} ${ins.title}</div>
      <div class="an-insight-msg">${ins.message}</div>
      <div class="an-insight-action">→ ${ins.actionable}</div>
    </div>`).join('');
}

// ── Engagement Breakdown ─────────────────────────────────────────────────────

export function buildEngagement(data) {
  if (!data) return '<p class="an-empty">No engagement data available.</p>';

  const p = data.posts;
  const c = data.comments;
  const t = data.threads;
  const totalLikes = (p.total_likes || 0) + (c.total_likes || 0);

  const bestPost = p.best_post ? `
    <div class="an-best-post">
      <div class="an-best-post-label">🌟 Best Post</div>
      <div class="an-best-post-title">${p.best_post.title}</div>
      <div class="an-best-post-stats">
        ${fmt(p.best_post.views)} views · ${fmt(p.best_post.likes)} likes · ${fmt(p.best_post.comments)} comments
      </div>
    </div>` : '';

  const rateLabel = p.engagement_rate
    ? `<span class="an-card-sub">${p.engagement_rate}% eng. rate</span>`
    : '';

  return `
    <div class="an-card-header">
      <h3 class="an-card-title">Engagement</h3>
      ${rateLabel}
    </div>
    <div class="an-eng-grid">
      <div class="an-eng-block">
        <div class="an-eng-icon">📝</div>
        <div class="an-eng-label">Posts</div>
        <div class="an-eng-value">${fmt(p.total_created)}</div>
        <div class="an-eng-sub">${fmt(p.total_views)} views total</div>
      </div>
      <div class="an-eng-block">
        <div class="an-eng-icon">💬</div>
        <div class="an-eng-label">Comments</div>
        <div class="an-eng-value">${fmt(c.total_created)}</div>
        <div class="an-eng-sub">${fmt(c.marked_helpful)} marked helpful</div>
      </div>
      <div class="an-eng-block">
        <div class="an-eng-icon">🧵</div>
        <div class="an-eng-label">Threads</div>
        <div class="an-eng-value">${fmt(t.joined)}</div>
        <div class="an-eng-sub">${fmt(t.messages_sent)} messages</div>
      </div>
      <div class="an-eng-block">
        <div class="an-eng-icon">👍</div>
        <div class="an-eng-label">Total Likes</div>
        <div class="an-eng-value">${fmt(totalLikes)}</div>
        <div class="an-eng-sub">${fmt(c.marked_solution)} solutions</div>
      </div>
    </div>
    ${bestPost}`;
}

// ── Impact Metrics ───────────────────────────────────────────────────────────

export function buildImpact(data) {
  if (!data) return '<p class="an-empty">No impact data available.</p>';

  const imp = data.impact;

  const rows = [
    { icon: '👥', label: 'People reached',      val: imp.people_reached },
    { icon: '❓', label: 'Questions answered',   val: imp.questions_answered },
    { icon: '✅', label: 'Questions solved',      val: imp.questions_solved },
    { icon: '📚', label: 'Resources shared',      val: imp.resources_shared },
    { icon: '🔖', label: 'Times bookmarked',      val: imp.times_bookmarked },
    { icon: '🔗', label: 'Active connections',    val: imp.active_connections },
  ].map(r => `
    <div class="an-impact-row">
      <span class="an-impact-icon">${r.icon}</span>
      <span class="an-impact-label">${r.label}</span>
      <span class="an-impact-val">${fmt(r.val)}</span>
    </div>`).join('');

  return `
    <div class="an-card-header">
      <h3 class="an-card-title">Impact</h3>
      <div class="an-impact-score-badge">Score: ${fmt(data.impact_score)}</div>
    </div>
    <div class="an-impact-list">${rows}</div>`;
}

// ── Platform Comparison ──────────────────────────────────────────────────────

export function buildComparison(data) {
  if (!data) return '<p class="an-empty">No comparison data available.</p>';

  const y = data.your_stats;
  const a = data.average_stats;
  const c = data.comparison;

  function row(label, youVal, avgVal, status, multiplier) {
    const pct      = Math.min((multiplier / 2) * 100, 100);
    const barClass = status === 'above' ? 'an-bar--above' : 'an-bar--below';
    const badgeCls = status === 'above' ? 'an-badge--pos' : 'an-badge--neg';
    const badgeTxt = status === 'above' ? `${multiplier}× avg` : 'below avg';

    return `
      <div class="an-compare-row">
        <div class="an-compare-label">${label}</div>
        <div class="an-compare-bar-wrap">
          <div class="an-compare-bar ${barClass}" style="width:${pct}%"></div>
        </div>
        <div class="an-compare-vals">
          <span class="an-cmp-you">${fmt(youVal)}</span>
          <span class="an-cmp-sep">vs</span>
          <span class="an-cmp-avg">${fmt(avgVal)}</span>
        </div>
        <div class="an-cmp-badge ${badgeCls}">${badgeTxt}</div>
      </div>`;
  }

  return `
    <div class="an-card-header">
      <h3 class="an-card-title">vs. Platform Average</h3>
      <span class="an-card-sub">How you compare</span>
    </div>
    <div class="an-compare-list">
      ${row('Posts',       y.posts,       a.posts,       c.posts.status,       c.posts.multiplier)}
      ${row('Reputation',  y.reputation,  a.reputation,  c.reputation.status,  c.reputation.multiplier)}
      ${row('Helpful',     y.helpful,     a.helpful,     c.helpful.status,     c.helpful.multiplier)}
      ${row('Connections', y.connections, a.connections, c.connections.status, c.connections.multiplier)}
    </div>`;
}
