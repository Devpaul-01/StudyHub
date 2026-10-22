/**
 * ============================================================================
 * PROFILE SHOWCASE DATA — profile.showcase.data.js
 * Save as: /static/js/profile/profile.showcase.data.js
 *
 * TEMPORARY — portfolio/demo data only.
 * Feeds the existing profile.templates.js build...() functions with
 * realistic, internally-consistent data so the Profile section can be
 * screenshotted without depending on live backend state.
 *
 * To revert: delete this file and restore the original profileApi calls
 * in profile.delegation.js (see the ORIGINAL comments left in place there).
 * ============================================================================
 */

// ── Time helpers — everything is relative to "now" so screenshots taken on
//    any date still show fresh, believable timestamps. ─────────────────────

const NOW = Date.now();
const MIN = 60 * 1000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

function ago(ms) {
  return new Date(NOW - ms).toISOString();
}

// ── Profile header ───────────────────────────────────────────────────────

export const SHOWCASE_PROFILE = {
  name: 'Amara Okafor',
  username: 'amara.okafor',
  avatar: null, // renders initials avatar — clean, no dependency on image hosting
  bio: 'CS junior into distributed systems and compilers. Currently TA-ing Data Structures — drop your pointer questions here 👋',
  department: 'Computer Science',
  class_level: '300 Level',
  reputation: 2840,
  reputation_level: 'Mentor',
  login_streak: 14,
  learning_goals: [
    'Finish CS341 compiler project by finals',
    'Contribute to an open-source Rust CLI tool',
    'Get comfortable with distributed consensus (Raft)',
  ],
  stats: {
    total_posts: 47,
    connections_count: 38,
    total_helpful: 156,
  },
};

// ── Posts tab ─────────────────────────────────────────────────────────────

export const SHOWCASE_POSTS = [
  {
    id: 9001,
    post_type: 'resource',
    title: 'Cleaned-up notes: Recursive Descent Parsers (CS341)',
    text_content:
      "Wrote these up after office hours this week since a few people asked. Covers grammar → parser table by hand, plus a worked example building a parser for simple arithmetic expressions. PDF has the full derivation, slides are the condensed version I used for the study group.",
    posted_at: ago(3 * HOUR),
    is_solved: false,
    is_pinned: true,
    likes_count: 34,
    comments_count: 11,
    author: { name: 'Amara Okafor', username: 'amara.okafor', avatar: null },
    resources: [
      { type: 'document', filename: 'recursive-descent-parsers-notes.pdf', url: '#' },
      { type: 'document', filename: 'parser-worked-example.pdf', url: '#' },
    ],
    tags: ['compilers', 'cs341', 'parsing'],
    user_reacted: true,
  },
  {
    id: 9002,
    post_type: 'question',
    title: 'Segfault only in -O2 builds, fine in debug — anyone hit this before?',
    text_content:
      "Working on the memory allocator assignment and getting a segfault only when compiling with -O2. Debug build (-O0) runs clean through the full test suite. Smells like undefined behavior the optimizer is exposing — maybe an uninitialized read or a strict-aliasing violation. Anyone debugged something similar with valgrind + ASan?",
    posted_at: ago(1 * DAY + 4 * HOUR),
    is_solved: true,
    is_pinned: false,
    likes_count: 19,
    comments_count: 23,
    author: { name: 'Amara Okafor', username: 'amara.okafor', avatar: null },
    resources: [],
    tags: ['c', 'debugging', 'undefined-behavior'],
    user_reacted: false,
  },
  {
    id: 9003,
    post_type: 'discussion',
    title: 'Forming a study group for the OS midterm — anyone in?',
    text_content:
      "Thinking Tuesdays/Thursdays, 6-8pm in the CS building. Want to go through past papers together and actually whiteboard the scheduling algorithms instead of just reading slides. Already have 3 people, room for a couple more.",
    posted_at: ago(2 * DAY + 6 * HOUR),
    is_solved: false,
    is_pinned: false,
    likes_count: 12,
    comments_count: 15,
    author: { name: 'Amara Okafor', username: 'amara.okafor', avatar: null },
    resources: [],
    tags: ['operating-systems', 'study-group'],
    user_reacted: false,
  },
  {
    id: 9004,
    post_type: 'resource',
    title: 'Git workflow cheat sheet for group projects',
    text_content:
      "Half the questions in #cs220-help this week were git-related, so I put together a one-pager covering branching, resolving conflicts, and the rebase-vs-merge thing everyone gets confused about. Should save your group some pain before the next sprint deadline.",
    posted_at: ago(4 * DAY + 2 * HOUR),
    is_solved: false,
    is_pinned: false,
    likes_count: 41,
    comments_count: 8,
    author: { name: 'Amara Okafor', username: 'amara.okafor', avatar: null },
    resources: [
      { type: 'document', filename: 'git-workflow-cheatsheet.pdf', url: '#' },
    ],
    tags: ['git', 'tooling', 'teamwork'],
    user_reacted: true,
  },
  {
    id: 9005,
    post_type: 'problem',
    title: 'Dynamic programming: is my O(n²) knapsack actually correct?',
    text_content:
      "Got the right answer on the sample cases but TLE on the hidden test set for the assignment. Pretty sure the recurrence is right but I might be re-computing subproblems somewhere. Sharing my approach below — would appreciate a second pair of eyes before I rewrite it with memoization from scratch.",
    posted_at: ago(6 * DAY + 1 * HOUR),
    is_solved: true,
    is_pinned: false,
    likes_count: 8,
    comments_count: 6,
    author: { name: 'Amara Okafor', username: 'amara.okafor', avatar: null },
    resources: [],
    tags: ['algorithms', 'dynamic-programming'],
    user_reacted: false,
  },
  {
    id: 9006,
    post_type: 'announcement',
    title: 'TA office hours moved to Wednesdays this week (room change too)',
    text_content:
      "Heads up for anyone in Data Structures — office hours are Wednesday 3-5pm this week instead of the usual Monday slot, and we've moved to CS 214 since the usual room is booked for a defense. Same Zoom link for anyone remote.",
    posted_at: ago(9 * DAY),
    is_solved: false,
    is_pinned: false,
    likes_count: 27,
    comments_count: 4,
    author: { name: 'Amara Okafor', username: 'amara.okafor', avatar: null },
    resources: [],
    tags: ['office-hours', 'data-structures'],
    user_reacted: false,
  },
];

// ── Stats tab: engagement / help / threads / posts + heatmap ────────────────

export const SHOWCASE_STATS = {
  engagement: {
    reactions_received: 312,
    helpful_received: 156,
    total_comments: 203,
    questions_solved: 41,
    questions_answered: 68,
  },
  help: {
    total_helps_given: 89,
    total_helps_received: 22,
    streak_current: 14,
    streak_longest: 26,
  },
  threads: {
    joined: 9,
    created: 3,
  },
  posts: {
    resources_shared: 17,
  },
};

// Generate a believable 90-day activity heatmap — busier on weekdays,
// occasional bursts around "exam-like" weeks, some rest days scattered in.
function generateHeatmap() {
  const days = [];
  const totalDays = 90;

  for (let i = totalDays - 1; i >= 0; i--) {
    const date = new Date(NOW - i * DAY);
    const dow = date.getDay(); // 0 Sun ... 6 Sat
    const isWeekend = dow === 0 || dow === 6;

    // Base probability of activity, higher on weekdays
    const activeRoll = Math.random();
    const activeChance = isWeekend ? 0.45 : 0.78;

    let level = 0;
    let posts = 0, comments = 0, helpful = 0, score = 0;

    if (activeRoll < activeChance) {
      // Weighted toward light/medium activity, occasional heavy day
      const intensity = Math.random();
      if (intensity < 0.45) level = 1;
      else if (intensity < 0.75) level = 2;
      else if (intensity < 0.93) level = 3;
      else level = 4;

      comments = Math.max(0, Math.round(level * (1.5 + Math.random() * 1.5)));
      posts = Math.random() < 0.25 * level ? 1 : 0;
      helpful = Math.round(level * Math.random() * 1.2);
      score = level * 3 + posts * 4 + comments + helpful * 2;
    }

    days.push({
      date: date.toISOString().slice(0, 10),
      level,
      score,
      posts,
      comments,
      helpful,
    });
  }
  return days;
}

const HEATMAP_DAYS = generateHeatmap();
const ACTIVE_DAYS = HEATMAP_DAYS.filter(d => d.level > 0).length;

export const SHOWCASE_HEATMAP = {
  heatmap: HEATMAP_DAYS,
  summary: {
    current_streak: 14,
    active_days: ACTIVE_DAYS,
  },
};

// ── Connections tab ──────────────────────────────────────────────────────

export const SHOWCASE_CONNECTIONS = [
  {
    user: {
      id: 201, name: 'Tobenna Chukwu', username: 'tobenna.c',
      avatar: null, is_online: true,
      department: 'Computer Science', reputation: 1980, reputation_level: 'Scholar',
    },
  },
  {
    user: {
      id: 202, name: 'Priya Nandakumar', username: 'priya.n',
      avatar: null, is_online: true,
      department: 'Software Engineering', reputation: 2415, reputation_level: 'Mentor',
    },
  },
  {
    user: {
      id: 203, name: 'Kelechi Uba', username: 'kelechi.uba',
      avatar: null, is_online: false,
      department: 'Electrical Engineering', reputation: 890, reputation_level: 'Contributor',
    },
  },
  {
    user: {
      id: 204, name: 'Fatima Al-Sayed', username: 'fatima.alsayed',
      avatar: null, is_online: true,
      department: 'Mathematics', reputation: 1650, reputation_level: 'Scholar',
    },
  },
  {
    user: {
      id: 205, name: 'Daniel Osei', username: 'daniel.osei',
      avatar: null, is_online: false,
      department: 'Computer Science', reputation: 3120, reputation_level: 'Mentor',
    },
  },
  {
    user: {
      id: 206, name: 'Chiamaka Nwankwo', username: 'chiamaka.n',
      avatar: null, is_online: true,
      department: 'Data Science & AI', reputation: 2005, reputation_level: 'Scholar',
    },
  },
  {
    user: {
      id: 207, name: 'Segun Adeyemi', username: 'segun.a',
      avatar: null, is_online: false,
      department: 'Computer Engineering', reputation: 540, reputation_level: 'Contributor',
    },
  },
  {
    user: {
      id: 208, name: 'Ngozi Eze', username: 'ngozi.eze',
      avatar: null, is_online: true,
      department: 'Cybersecurity', reputation: 1275, reputation_level: 'Scholar',
    },
  },
];

// ── Reputation tab ───────────────────────────────────────────────────────

export const SHOWCASE_REPUTATION = {
  reputation: {
    points: 2840,
    level: { name: 'Mentor', icon: '🧭' },
    next_level: {
      name: 'Sage', icon: '🦉',
      points_needed: 660,
      progress_percentage: 62,
    },
    rank: { global: 47, percentile: 96.2 },
  },
};

const HISTORY_ACTIONS = [
  { action: 'post_helpful', pts: [8, 15] },
  { action: 'comment_helpful', pts: [4, 8] },
  { action: 'question_solved', pts: [12, 20] },
  { action: 'helped_user', pts: [5, 10] },
  { action: 'post_liked', pts: [1, 3] },
  { action: 'streak_bonus', pts: [5, 5] },
  { action: 'comment_created', pts: [1, 2] },
  { action: 'post_created', pts: [3, 5] },
];

function generateReputationHistory() {
  const history = [];
  let runningTotal = 2840;
  let cursor = NOW - 2 * HOUR;

  for (let i = 0; i < 30; i++) {
    const pick = HISTORY_ACTIONS[Math.floor(Math.random() * HISTORY_ACTIONS.length)];
    const change = pick.pts[0] + Math.floor(Math.random() * (pick.pts[1] - pick.pts[0] + 1));
    history.push({
      action: pick.action,
      points_change: change,
      reputation_after: runningTotal,
      created_at: new Date(cursor).toISOString(),
    });
    runningTotal -= change;
    cursor -= (2 + Math.random() * 14) * HOUR;
  }
  return history;
}

const REP_HISTORY = generateReputationHistory();
const totalGained = REP_HISTORY.filter(h => h.points_change > 0)
  .reduce((s, h) => s + h.points_change, 0);
const totalLost = REP_HISTORY.filter(h => h.points_change < 0)
  .reduce((s, h) => s + Math.abs(h.points_change), 0);

export const SHOWCASE_REPUTATION_HISTORY = {
  history: REP_HISTORY,
  summary: {
    total_gained: totalGained,
    total_lost: totalLost,
    net_change: totalGained - totalLost,
  },
};
