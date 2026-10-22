/**
 * ============================================================================
 * ANALYTICS SHOWCASE DATA — analytics.showcase-data.js
 * Save as: /static/js/analytics/analytics.showcase-data.js
 *
 * ⚠️ PORTFOLIO / DEMO USE ONLY — NOT FOR PRODUCTION ⚠️
 *
 * v2 — refined against the real backend (routes/student/analytics.py).
 * Every field, formula, and gate condition below is derived to match what
 * that file can actually produce for a real user — not hand-picked to look
 * plausible. See the per-section notes for the specific backend logic each
 * value is derived from.
 *
 * Persona: "Priya Nair" — CS junior, consistent contributor, ~7 months on
 * StudyHub, department of 40 students, currently ranked #4.
 *
 * TO REMOVE: delete this file and revert analytics.api.js to call the real
 * `api.get(...)` endpoints again (see the commented originals left in place
 * there for exactly this purpose).
 * ============================================================================
 */

// ── Overview (hero stats) ────────────────────────────────────────────────────
// Matches get_analytics_overview(). Two fields are backend-forced constants:
//   - hero_stats.helpful_count is ALWAYS 0 (this_week_helpful is hardcoded 0
//     in the route due to a known reaction_type mismatch bug — see the
//     route's own AUDIT §4.2 comment). Showing anything else is not
//     something this backend can produce.
//   - department_rank = (# dept users with strictly greater reputation) + 1.
//     Kept at #4 — this number is reused verbatim by the Top Contributor
//     insight gate below, so it isn't free to change without touching that
//     insight's math too (see SHOWCASE_INSIGHTS notes).

export const SHOWCASE_OVERVIEW = {
  hero_stats: {
    monthly_views: 187,          // this_week_views (route param name is legacy; actually 7-day window)
    helpful_count: 0,            // forced 0 — see note above, this is not a display choice
    department_rank: 4,
    reputation_change: 71,       // this_week_rep, int() of a 7-day ReputationHistory sum
    activity_level: {
      // Shape from services/analytics_service.py::get_activity_level — not
      // in the file you gave me, so I've kept the same 3-key shape the
      // frontend already expects and chosen a level consistent with a
      // 43-day streak + this week's activity. Flag if the real bucket
      // labels/thresholds differ from "Highly Active".
      emoji: '🔥',
      level: 'Highly Active',
      color: '#22c55e',
    },
  },
  current_stats: {
    total_posts: 34,
    total_reputation: 1926,
    reputation_level: 'Rising Contributor', // same caveat as activity_level — not defined in this file
    login_streak: 43,
    total_helpful: 118,          // this one is real (current_user.total_helpful), unlike hero_stats.helpful_count
  },
  quick_facts: {
    joined_at: '2026-02-09T00:00:00Z',
    days_active: 199,            // (utcnow - joined_at).days, kept consistent with joined_at above
    department: 'Computer Science',
    class_level: 'Junior',       // profile.class_name — StudentProfile shape not in the file you gave me;
                                  // flag if class_name uses a different label set (e.g. "3rd Year")
  },
};

// ── Activity heatmap (90 days) ───────────────────────────────────────────────
// Matches get_activity_heatmap(). Level bins are the route's exact thresholds:
//   score 0 → level 0 | 1-5 → 1 | 6-15 → 2 | 16-30 → 3 | 31+ → 4
// current_streak here is NOT computed from this array — the route sets it
// directly to current_user.login_streak (the same field as
// current_stats.login_streak above), so the two numbers agreeing is a hard
// requirement, not a coincidence to preserve by hand.
// best_day is the full matching day object (route uses max() on the array
// itself), not a trimmed {date, score} pair.
// `messages` and `helpful` are real response fields (UserActivity columns)
// that the current frontend templates don't render — included for shape
// fidelity since the real API always sends them, even though they won't be
// visible in a screenshot. `helpful` here is a small per-day counter,
// distinct from the lifetime current_stats.total_helpful; nothing in this
// file defines what increments it, so these values are a reasonable but
// genuinely assumed fill, not a derivation — flag if there's a real spec.

export const SHOWCASE_HEATMAP = {
  heatmap: [
    { date: '2026-05-30', score: 0, posts: 0, comments: 0, messages: 0, helpful: 0, level: 0 },
    { date: '2026-05-31', score: 0, posts: 0, comments: 0, messages: 0, helpful: 0, level: 0 },
    { date: '2026-06-01', score: 7, posts: 0, comments: 0, messages: 1, helpful: 0, level: 2 },
    { date: '2026-06-02', score: 19, posts: 0, comments: 3, messages: 2, helpful: 0, level: 3 },
    { date: '2026-06-03', score: 3, posts: 0, comments: 1, messages: 0, helpful: 0, level: 1 },
    { date: '2026-06-04', score: 0, posts: 0, comments: 0, messages: 0, helpful: 0, level: 0 },
    { date: '2026-06-05', score: 9, posts: 0, comments: 2, messages: 1, helpful: 1, level: 2 },
    { date: '2026-06-06', score: 0, posts: 0, comments: 0, messages: 0, helpful: 0, level: 0 },
    { date: '2026-06-07', score: 0, posts: 0, comments: 0, messages: 0, helpful: 0, level: 0 },
    { date: '2026-06-08', score: 0, posts: 0, comments: 0, messages: 0, helpful: 0, level: 0 },
    { date: '2026-06-09', score: 0, posts: 0, comments: 0, messages: 0, helpful: 0, level: 0 },
    { date: '2026-06-10', score: 0, posts: 0, comments: 0, messages: 0, helpful: 0, level: 0 },
    { date: '2026-06-11', score: 0, posts: 0, comments: 0, messages: 0, helpful: 0, level: 0 },
    { date: '2026-06-12', score: 0, posts: 0, comments: 0, messages: 0, helpful: 0, level: 0 },
    { date: '2026-06-13', score: 0, posts: 0, comments: 0, messages: 0, helpful: 0, level: 0 },
    { date: '2026-06-14', score: 2, posts: 0, comments: 0, messages: 2, helpful: 0, level: 1 },
    { date: '2026-06-15', score: 2, posts: 0, comments: 0, messages: 2, helpful: 0, level: 1 },
    { date: '2026-06-16', score: 13, posts: 0, comments: 2, messages: 4, helpful: 0, level: 2 },
    { date: '2026-06-17', score: 13, posts: 0, comments: 1, messages: 3, helpful: 0, level: 2 },
    { date: '2026-06-18', score: 9, posts: 0, comments: 1, messages: 1, helpful: 1, level: 2 },
    { date: '2026-06-19', score: 11, posts: 0, comments: 0, messages: 4, helpful: 0, level: 2 },
    { date: '2026-06-20', score: 0, posts: 0, comments: 0, messages: 0, helpful: 0, level: 0 },
    { date: '2026-06-21', score: 4, posts: 0, comments: 1, messages: 0, helpful: 0, level: 1 },
    { date: '2026-06-22', score: 2, posts: 0, comments: 0, messages: 2, helpful: 0, level: 1 },
    { date: '2026-06-23', score: 4, posts: 0, comments: 1, messages: 2, helpful: 0, level: 1 },
    { date: '2026-06-24', score: 13, posts: 0, comments: 1, messages: 1, helpful: 0, level: 2 },
    { date: '2026-06-25', score: 7, posts: 0, comments: 1, messages: 1, helpful: 1, level: 2 },
    { date: '2026-06-26', score: 13, posts: 0, comments: 2, messages: 3, helpful: 1, level: 2 },
    { date: '2026-06-27', score: 11, posts: 0, comments: 1, messages: 1, helpful: 0, level: 2 },
    { date: '2026-06-28', score: 0, posts: 0, comments: 0, messages: 0, helpful: 0, level: 0 },
    { date: '2026-06-29', score: 0, posts: 0, comments: 0, messages: 0, helpful: 0, level: 0 },
    { date: '2026-06-30', score: 0, posts: 0, comments: 0, messages: 0, helpful: 0, level: 0 },
    { date: '2026-07-01', score: 8, posts: 0, comments: 2, messages: 2, helpful: 0, level: 2 },
    { date: '2026-07-02', score: 23, posts: 0, comments: 3, messages: 2, helpful: 0, level: 3 },
    { date: '2026-07-03', score: 3, posts: 0, comments: 0, messages: 1, helpful: 0, level: 1 },
    { date: '2026-07-04', score: 4, posts: 0, comments: 1, messages: 2, helpful: 0, level: 1 },
    { date: '2026-07-05', score: 8, posts: 0, comments: 1, messages: 1, helpful: 0, level: 2 },
    { date: '2026-07-06', score: 3, posts: 0, comments: 1, messages: 0, helpful: 0, level: 1 },
    { date: '2026-07-07', score: 8, posts: 0, comments: 0, messages: 3, helpful: 0, level: 2 },
    { date: '2026-07-08', score: 0, posts: 0, comments: 0, messages: 0, helpful: 0, level: 0 },
    { date: '2026-07-09', score: 15, posts: 0, comments: 0, messages: 3, helpful: 0, level: 2 },
    { date: '2026-07-10', score: 14, posts: 0, comments: 2, messages: 4, helpful: 0, level: 2 },
    { date: '2026-07-11', score: 0, posts: 0, comments: 0, messages: 0, helpful: 0, level: 0 },
    { date: '2026-07-12', score: 5, posts: 0, comments: 0, messages: 0, helpful: 0, level: 1 },
    { date: '2026-07-13', score: 0, posts: 0, comments: 0, messages: 0, helpful: 0, level: 0 },
    { date: '2026-07-14', score: 3, posts: 0, comments: 0, messages: 0, helpful: 0, level: 1 },
    { date: '2026-07-15', score: 7, posts: 0, comments: 1, messages: 1, helpful: 1, level: 2 },
    { date: '2026-07-16', score: 21, posts: 0, comments: 1, messages: 6, helpful: 0, level: 3 },
    { date: '2026-07-17', score: 18, posts: 0, comments: 2, messages: 4, helpful: 1, level: 3 },
    { date: '2026-07-18', score: 17, posts: 0, comments: 2, messages: 5, helpful: 1, level: 3 },
    { date: '2026-07-19', score: 17, posts: 0, comments: 1, messages: 3, helpful: 0, level: 3 },
    { date: '2026-07-20', score: 23, posts: 1, comments: 1, messages: 3, helpful: 1, level: 3 },
    { date: '2026-07-21', score: 36, posts: 2, comments: 2, messages: 4, helpful: 2, level: 4 },
    { date: '2026-07-22', score: 17, posts: 0, comments: 1, messages: 4, helpful: 1, level: 3 },
    { date: '2026-07-23', score: 36, posts: 0, comments: 3, messages: 4, helpful: 2, level: 4 },
    { date: '2026-07-24', score: 28, posts: 0, comments: 3, messages: 3, helpful: 0, level: 3 },
    { date: '2026-07-25', score: 14, posts: 0, comments: 0, messages: 4, helpful: 0, level: 2 },
    { date: '2026-07-26', score: 43, posts: 1, comments: 4, messages: 5, helpful: 1, level: 4 },
    { date: '2026-07-27', score: 11, posts: 0, comments: 1, messages: 3, helpful: 0, level: 2 },
    { date: '2026-07-28', score: 13, posts: 0, comments: 1, messages: 2, helpful: 0, level: 2 },
    { date: '2026-07-29', score: 30, posts: 0, comments: 3, messages: 6, helpful: 0, level: 3 },
    { date: '2026-07-30', score: 7, posts: 0, comments: 1, messages: 1, helpful: 0, level: 2 },
    { date: '2026-07-31', score: 18, posts: 0, comments: 2, messages: 5, helpful: 1, level: 3 },
    { date: '2026-08-01', score: 5, posts: 0, comments: 1, messages: 1, helpful: 0, level: 1 },
    { date: '2026-08-02', score: 17, posts: 0, comments: 1, messages: 3, helpful: 0, level: 3 },
    { date: '2026-08-03', score: 5, posts: 0, comments: 0, messages: 2, helpful: 0, level: 1 },
    { date: '2026-08-04', score: 25, posts: 1, comments: 2, messages: 5, helpful: 1, level: 3 },
    { date: '2026-08-05', score: 16, posts: 0, comments: 0, messages: 2, helpful: 1, level: 3 },
    { date: '2026-08-06', score: 30, posts: 0, comments: 2, messages: 3, helpful: 1, level: 3 },
    { date: '2026-08-07', score: 10, posts: 0, comments: 1, messages: 2, helpful: 0, level: 2 },
    { date: '2026-08-08', score: 21, posts: 0, comments: 3, messages: 4, helpful: 1, level: 3 },
    { date: '2026-08-09', score: 4, posts: 0, comments: 1, messages: 1, helpful: 0, level: 1 },
    { date: '2026-08-10', score: 30, posts: 0, comments: 2, messages: 6, helpful: 0, level: 3 },
    { date: '2026-08-11', score: 5, posts: 0, comments: 0, messages: 0, helpful: 0, level: 1 },
    { date: '2026-08-12', score: 18, posts: 0, comments: 3, messages: 3, helpful: 0, level: 3 },
    { date: '2026-08-13', score: 17, posts: 0, comments: 1, messages: 6, helpful: 0, level: 3 },
    { date: '2026-08-14', score: 24, posts: 0, comments: 1, messages: 6, helpful: 1, level: 3 },
    { date: '2026-08-15', score: 3, posts: 0, comments: 0, messages: 1, helpful: 0, level: 1 },
    { date: '2026-08-16', score: 24, posts: 0, comments: 1, messages: 5, helpful: 1, level: 3 },
    { date: '2026-08-17', score: 38, posts: 0, comments: 3, messages: 5, helpful: 2, level: 4 },
    { date: '2026-08-18', score: 23, posts: 0, comments: 2, messages: 6, helpful: 0, level: 3 },
    { date: '2026-08-19', score: 34, posts: 1, comments: 4, messages: 6, helpful: 1, level: 4 },
    { date: '2026-08-20', score: 7, posts: 0, comments: 0, messages: 2, helpful: 0, level: 2 },
    { date: '2026-08-21', score: 10, posts: 0, comments: 1, messages: 1, helpful: 0, level: 2 },
    { date: '2026-08-22', score: 8, posts: 0, comments: 0, messages: 4, helpful: 0, level: 2 },
    { date: '2026-08-23', score: 13, posts: 0, comments: 1, messages: 2, helpful: 1, level: 2 },
    { date: '2026-08-24', score: 12, posts: 0, comments: 2, messages: 4, helpful: 0, level: 2 },
    { date: '2026-08-25', score: 11, posts: 0, comments: 1, messages: 1, helpful: 1, level: 2 },
    { date: '2026-08-26', score: 5, posts: 0, comments: 0, messages: 1, helpful: 0, level: 1 },
    { date: '2026-08-27', score: 14, posts: 0, comments: 0, messages: 3, helpful: 1, level: 2 },
  ],
  summary: {
    total_days: 90,
    active_days: 72,
    total_score: 1021,
    avg_daily_score: 11.3,
    best_day: { date: '2026-07-26', score: 43, posts: 1, comments: 4, messages: 5, helpful: 1, level: 4 },
    current_streak: 43, // = current_user.login_streak, same value as current_stats.login_streak above
  },
};

// ── Engagement breakdown ─────────────────────────────────────────────────────
// Matches get_engagement_stats(). Includes total_comments, avg_likes_per_post,
// avg_views_per_post, and threads.created — real response fields the current
// templates don't render, kept for shape fidelity. best_post uses only the
// 5 keys the route actually returns (id, title, likes, comments, views) —
// no posted_at/tags/etc., since best_post here isn't the full Post model,
// just this trimmed dict.

export const SHOWCASE_ENGAGEMENT = {
  posts: {
    total_created: 34,
    total_views: 12480,
    total_likes: 611,          // sum of positive_reactions_count across posts
    total_comments: 428,       // sum of comments_count across posts
    avg_likes_per_post: 18.0,  // round(611/34, 1)
    avg_views_per_post: 367.1, // round(12480/34, 1)
    engagement_rate: 34,       // calculate_engagement_rate() is in analytics_service.py, not in the file
                                // you gave me — kept the same number as before; flag if the real formula differs
    best_post: {
      id: 4821,
      title: 'Big-O cheat sheet: the 12 complexities you actually see in interviews',
      likes: 187,
      comments: 42,
      views: 2103,
    },
  },
  comments: {
    total_created: 156,
    total_likes: 340,
    marked_helpful: 118,       // = current_user.total_helpful (route reads this field directly, not a comment-level count)
    marked_solution: 61,       // count(Comment.is_solution == True) — reused as questions_solved in Impact below,
                                // since both routes count the exact same underlying rows
  },
  threads: {
    created: 6,
    joined: 19,
    messages_sent: 214,
  },
};

// ── Impact metrics ───────────────────────────────────────────────────────────
// Matches get_impact_metrics(). impact_score is NOT a free-standing number —
// the route computes it live as:
//   people_reached*1 + questions_solved*10 + total_helpful*5 + resources_shared*3
// = 3920 + 610 + 590 + 81 = 5201 (recomputed below, not chosen by feel).
// questions_solved (61) intentionally matches comments.marked_solution above
// — same underlying query (Comment.is_solution == True) via two routes.
// study_buddies_helped is hardcoded 0 in the route (explicit "future
// feature" placeholder) — shown as 0 here for the same reason, not omitted.

export const SHOWCASE_IMPACT = {
  impact: {
    people_reached: 3920,
    questions_answered: 89,
    questions_solved: 61,
    resources_shared: 27,
    times_bookmarked: 74,
    active_connections: 46,
    study_buddies_helped: 0,   // real backend placeholder, always 0 — not a data gap
    total_helpful: 118,
  },
  impact_score: 5201,          // 3920*1 + 61*10 + 118*5 + 27*3 = 3920+610+590+81
};

// ── AI Insights ──────────────────────────────────────────────────────────────
// Matches generate_insights(), which can ONLY produce these 6 fixed insight
// types, each behind its own numeric gate, in this fixed order, capped at 5:
//   1. Best Posting Day   — needs a posting-day-vs-likes pattern
//   2. Quick Responder     — needs avg comment response time < 3h
//   3. You're Trending     — needs >=20 views TODAY on the most recent post
//   4. Badge Almost Unlocked — needs real Badge/UserBadge data I don't have
//      (badge names, icons, progress types) — see note below, omitted rather
//      than invented.
//   5. Top Contributor     — needs dept percentile >= 90. With a 40-person
//      CS department and rank #4 (matching hero_stats.department_rank
//      above), percentile = round((1 - 4/40) * 100) = 90 — exactly clears
//      the gate.
//   6. On Fire              — needs login_streak >= 7. 43 clears this easily.
//
// Badge Almost Unlocked (#4) is left out on purpose: fabricating a badge
// name/icon/progress-type would be inventing a new subsystem, not refining
// existing data. If you want it included, send me the real Badge model /
// seed data (name, icon, progress "type" values) and I'll add it correctly
// gated at >=50% progress.
//
// "You're Trending" (#3) is the most fragile of the five for a static demo:
// its gate is same-day view count on your single most recent post, so it
// only reads as "true" on the literal day someone views the dashboard.
// Included since it's genuinely backend-producible, but worth knowing it's
// the one most likely to look odd if this screenshot is revisited later.

export const SHOWCASE_INSIGHTS = {
  insights: [
    {
      type: 'timing',
      icon: '📅',
      title: 'Best Posting Day',
      message: 'Your posts get 14.2 likes on Tuesdays - 131% above average!',
      actionable: 'Try posting more on Tuesdays',
    },
    {
      type: 'engagement',
      icon: '⚡',
      title: 'Quick Responder',
      message: 'You respond to posts in 1.8 hours on average - faster than 75% of users!',
      actionable: 'Your quick responses help build reputation',
    },
    {
      type: 'trending',
      icon: '🔥',
      title: "You're Trending!",
      message: 'Your post "Big-O cheat sheet: the 12 complexities..." has 24 views today!',
      actionable: 'Keep engaging with comments to maintain momentum',
    },
    {
      type: 'achievement',
      icon: '🌟',
      title: 'Top Contributor',
      message: "You're in the top 10% of your department!",
      actionable: 'Your expertise is valued by the community',
    },
    {
      type: 'consistency',
      icon: '🔥',
      title: 'On Fire!',
      message: "43 day streak! You're building a strong learning habit.",
      actionable: "Don't break the streak - come back tomorrow!",
    },
  ],
};

// ── Platform comparison ──────────────────────────────────────────────────────
// Matches get_comparison_stats(). average_stats are the (chosen, believable)
// platform-wide averages; every multiplier below is the exact live formula
// the route runs — round(your_stat / avg_stat, 1) — not picked independently.
// status is "above" whenever multiplier > 1 else "below"; there's no
// backend-supported "at par" state, so connections (1.1×) is legitimately
// "above" under this rule despite being close to average.

export const SHOWCASE_COMPARISON = {
  your_stats: {
    posts: 34,
    reputation: 1926,
    helpful: 118,
    connections: 46,
  },
  average_stats: {
    posts: 14.2,
    reputation: 940.5,
    helpful: 51.8,
    connections: 40.9,
  },
  comparison: {
    posts: { multiplier: 2.4, status: 'above' },       // round(34/14.2, 1)
    reputation: { multiplier: 2.0, status: 'above' },  // round(1926/940.5, 1)
    helpful: { multiplier: 2.3, status: 'above' },     // round(118/51.8, 1)
    connections: { multiplier: 1.1, status: 'above' }, // round(46/40.9, 1)
  },
};
