/**
 * ============================================================================
 * SHOWCASE MOCK DATA — My Homework & Connections Homework
 * TEMPORARY: for portfolio screenshots only. Remove this file and the two
 * intercepts in homework.api.js (getMyAssignments / getConnectionsHomework)
 * to restore real backend data.
 * ============================================================================
 */

// Relative to "now" so due dates/timestamps always look current whenever
// screenshots are taken.
const NOW = Date.now();
const hoursFromNow = (h) => new Date(NOW + h * 60 * 60 * 1000).toISOString();
const hoursAgo = (h) => new Date(NOW - h * 60 * 60 * 1000).toISOString();

const AVATAR = (seed) => `https://i.pravatar.cc/150?img=${seed}`;

// ----------------------------------------------------------------------------
// SUBMISSION DETAIL FRAGMENTS — reused across helpers modal, submission
// detail modal, and connections cards so names/subjects stay consistent
// everywhere a given submission ID appears.
// ----------------------------------------------------------------------------

// Two people helping ME with the shared Biochem lab report (assignment 9002)
const HELPER_MARCUS = { name: 'Marcus Chen', username: 'mchen_bio', avatar: AVATAR(11), department: 'Biochemistry' };
const HELPER_ELENA = { name: 'Elena Petrova', username: 'elenap', avatar: AVATAR(23), department: 'Biochemistry' };

// The requesters whose assignments I'M helping with (from the Connections feed)
const REQUESTER_PRIYA = { name: 'Priya Nair', username: 'priya.nair', avatar: AVATAR(32), department: 'Electrical Engineering' };
const REQUESTER_JONAH = { name: 'Jonah Whitfield', username: 'jwhitfield', avatar: AVATAR(53), department: 'Economics' };

// "Me" as the helper, for submissions where I'm the one who submitted a solution
const ME_AS_HELPER = { name: 'You', username: 'you', avatar: AVATAR(70), department: 'Engineering', active_details: { is_online: true } };


// ----------------------------------------------------------------------------
// MY HOMEWORK — 6 cards, even mix of states
// ----------------------------------------------------------------------------
export const SHOWCASE_MY_ASSIGNMENTS = {
  status: 'success',
  data: {
    assignments: [
      {
        id: 9001,
        title: 'Problem Set 6: Eigenvalues & Diagonalization',
        subject: 'Linear Algebra',
        description: 'Cover sections 5.1–5.3. Show full working for the diagonalization proofs, not just final matrices.',
        difficulty: 'hard',
        status: 'in_progress',
        due_date: hoursFromNow(29),
        hours_until_due: 29,
        urgency_level: 'medium',
        is_overdue: false,
        estimated_hours: 4,
        is_shared: false,
        help_requests_count: 0,
        resources: [
          { url: '/static/showcase/lecture-notes.pdf', type: 'document', filename: 'lecture-notes-wk9.pdf' },
          { url: '/static/showcase/practice-set.pdf', type: 'document', filename: 'practice-set-6.pdf' }
        ]
      },
      {
        id: 9002,
        title: 'Lab Report: Enzyme Kinetics',
        subject: 'Biochemistry',
        description: 'Write up Michaelis-Menten results from Tuesday\'s lab. Include the Lineweaver-Burk plot and error analysis.',
        difficulty: 'medium',
        status: 'in_progress',
        due_date: hoursFromNow(52),
        hours_until_due: 52,
        urgency_level: 'low',
        is_overdue: false,
        estimated_hours: 3,
        is_shared: true,
        help_requests_count: 2,
        resources: []
      },
      {
        id: 9003,
        title: 'Essay: The Federalist Papers & Ratification Debates',
        subject: 'US History',
        description: '1200 words on the anti-federalist objections to a strong central government, with at least 3 primary sources.',
        difficulty: 'medium',
        status: 'not_started',
        due_date: hoursFromNow(-6),
        hours_until_due: -6,
        urgency_level: 'critical',
        is_overdue: true,
        estimated_hours: 2.5,
        is_shared: false,
        help_requests_count: 0,
        resources: []
      },
      {
        id: 9004,
        title: 'Recursion & Backtracking Exercises',
        subject: 'Data Structures',
        description: 'Implement N-Queens and Sudoku solver. Focus on pruning efficiency, not just correctness.',
        difficulty: 'hard',
        status: 'not_started',
        due_date: hoursFromNow(96),
        hours_until_due: 96,
        urgency_level: 'low',
        is_overdue: false,
        estimated_hours: 5,
        is_shared: false,
        help_requests_count: 0,
        resources: []
      },
      {
        id: 9005,
        title: 'Reading Response: Kant\'s Categorical Imperative',
        subject: 'Philosophy',
        description: 'Two-page response connecting the universalizability test to a modern ethical dilemma of your choice.',
        difficulty: 'easy',
        status: 'completed',
        due_date: hoursAgo(30),
        hours_until_due: -30,
        urgency_level: 'low',
        is_overdue: false,
        estimated_hours: 1.5,
        is_shared: false,
        help_requests_count: 0,
        resources: []
      },
      {
        id: 9006,
        title: 'Statistics Take-Home: Hypothesis Testing',
        subject: 'Statistics',
        description: 'Analyze the provided dataset with a two-sample t-test and report confidence intervals.',
        difficulty: 'medium',
        status: 'in_progress',
        due_date: hoursFromNow(14),
        hours_until_due: 14,
        urgency_level: 'high',
        is_overdue: false,
        estimated_hours: 2,
        is_shared: false,
        help_requests_count: 0,
        resources: []
      }
    ],
    stats: {
      not_started: 2,
      in_progress: 3,
      completed: 1,
      overdue: 1
    },
    next_cursor: null,
    has_more: false
  }
};

// ----------------------------------------------------------------------------
// CONNECTIONS HOMEWORK FEED — 6 cards covering all helper-side states
// ----------------------------------------------------------------------------
export const SHOWCASE_CONNECTIONS_HOMEWORK = {
  status: 'success',
  data: {
    homework: [
      {
        id: 8101,
        title: 'Organic Chemistry: Reaction Mechanism Practice',
        subject: 'Organic Chemistry',
        description: 'Stuck on the SN1 vs SN2 mechanism problems from chapter 7 — could use a second pair of eyes on my arrow-pushing.',
        difficulty: 'hard',
        due_date: hoursFromNow(20),
        hours_until_due: 20,
        urgency_level: 'medium',
        is_overdue: false,
        help_count: 3,
        already_helping: false,
        resources: [
          { url: '/static/showcase/chem-worksheet.pdf', type: 'document', filename: 'mechanism-worksheet.pdf' }
        ],
        student: {
          name: 'Amara Okafor',
          department: 'Chemistry',
          avatar: AVATAR(47),
          active_details: { is_online: true, in_study_session: false }
        }
      },
      {
        id: 8102,
        title: 'Spanish Oral Presentation Script Review',
        subject: 'Spanish',
        description: 'Wrote a 3-minute script on climate policy in Spanish for Friday\'s presentation — would love feedback on grammar and flow.',
        difficulty: 'easy',
        due_date: hoursFromNow(68),
        hours_until_due: 68,
        urgency_level: 'low',
        is_overdue: false,
        help_count: 1,
        already_helping: false,
        resources: [],
        student: {
          name: 'Diego Salinas',
          department: 'Modern Languages',
          avatar: AVATAR(15),
          active_details: { is_online: false, last_active: '2h ago' }
        }
      },
      {
        id: 8103,
        title: 'Circuit Analysis: Thevenin Equivalent Problems',
        subject: 'Electrical Engineering',
        description: 'Need help finding the Thevenin equivalent for problems 4 and 5 — my resistance calculations keep coming out wrong.',
        difficulty: 'medium',
        due_date: hoursFromNow(10),
        hours_until_due: 10,
        urgency_level: 'high',
        is_overdue: false,
        help_count: 0,
        already_helping: true,
        my_help_status: 'pending',
        my_submission_id: 7301,
        resources: [],
        student: {
          name: 'Priya Nair',
          department: 'Electrical Engineering',
          avatar: AVATAR(32),
          active_details: { is_online: true, in_study_session: true }
        }
      },
      {
        id: 8104,
        title: 'Macroeconomics: IS-LM Model Short Answer',
        subject: 'Economics',
        description: 'Answering how a fiscal expansion shifts the IS curve — want to check my graph and reasoning before submitting.',
        difficulty: 'medium',
        due_date: hoursFromNow(40),
        hours_until_due: 40,
        urgency_level: 'low',
        is_overdue: false,
        help_count: 2,
        already_helping: true,
        my_help_status: 'submitted',
        my_submission_id: 7302,
        resources: [],
        student: {
          name: 'Jonah Whitfield',
          department: 'Economics',
          avatar: AVATAR(53),
          active_details: { is_online: false, last_active: '5h ago' }
        }
      },
      {
        id: 8105,
        title: 'Data Structures: Binary Search Tree Balancing',
        subject: 'Computer Science',
        description: 'Implementing AVL rotations and getting inconsistent heights after multiple inserts — code attached.',
        difficulty: 'hard',
        due_date: hoursFromNow(6),
        hours_until_due: 6,
        urgency_level: 'critical',
        is_overdue: false,
        help_count: 4,
        already_helping: false,
        resources: [
          { url: '/static/showcase/bst-code.pdf', type: 'document', filename: 'avl_tree.py.pdf' }
        ],
        student: {
          name: 'Sofia Marchetti',
          department: 'Computer Science',
          avatar: AVATAR(28),
          active_details: { is_online: true, in_study_session: false }
        }
      },
      {
        id: 8106,
        title: 'Cell Biology: Mitosis vs Meiosis Comparison Chart',
        subject: 'Biology',
        description: 'Building a comparison chart for the midterm review session — want a second opinion on the crossover phase details.',
        difficulty: 'easy',
        due_date: hoursFromNow(84),
        hours_until_due: 84,
        urgency_level: 'low',
        is_overdue: false,
        help_count: 1,
        already_helping: false,
        resources: [],
        student: {
          name: 'Tunde Adeyemi',
          department: 'Biology',
          avatar: AVATAR(60),
          active_details: { is_online: false, last_active: '1d ago' }
        }
      }
    ],
    next_cursor: null,
    has_more: false
  }
};

// ----------------------------------------------------------------------------
// STATS TAB — GET /homework/stats
// ----------------------------------------------------------------------------
export const SHOWCASE_STATS = {
  status: 'success',
  data: {
    my_assignments: {
      total: 6,
      not_started: 2,
      in_progress: 3,
      completed: 1,
      overdue: 1,
      shared_for_help: 1
    },
    help_given: {
      total: 4,
      pending: 1,
      submitted: 1,
      reviewed: 0,
      completed: 2
    },
    help_received: {
      total: 2,
      pending: 0,
      submitted: 1,
      reviewed: 0,
      completed: 1
    },
    subjects: {
      my_subjects: ['Linear Algebra', 'Biochemistry', 'US History', 'Data Structures', 'Philosophy', 'Statistics'],
      helping_with: ['Electrical Engineering', 'Economics', 'Organic Chemistry', 'Computer Science']
    }
  }
};

// ----------------------------------------------------------------------------
// STREAK WIDGET — GET /homework/my-streak
// ----------------------------------------------------------------------------
export const SHOWCASE_STREAK = {
  status: 'success',
  data: {
    current_streak: 9,
    longest_streak: 14,
    streak_at_risk: false,
    helped_today: true
  }
};

// ----------------------------------------------------------------------------
// WEEKLY CHAMPIONS — GET /homework/champions
// ----------------------------------------------------------------------------
export const SHOWCASE_CHAMPIONS = {
  status: 'success',
  data: {
    week_start: hoursAgo(4 * 24),
    week_end: hoursFromNow(3 * 24),
    champions: {
      most_helpful: {
        user: { name: 'Elena Petrova', avatar: AVATAR(23) },
        help_count: 11,
        is_you: false
      },
      subject_champions: [
        { subject: 'Biochemistry', user: { name: 'Elena Petrova', avatar: AVATAR(23) }, help_count: 6, is_you: false },
        { subject: 'Electrical Engineering', user: { name: 'You', avatar: AVATAR(70) }, help_count: 5, is_you: true },
        { subject: 'Computer Science', user: { name: 'Sofia Marchetti', avatar: AVATAR(28) }, help_count: 4, is_you: false },
        { subject: 'Economics', user: { name: 'You', avatar: AVATAR(70) }, help_count: 3, is_you: true }
      ],
      fastest_helper: {
        user: { name: 'Marcus Chen', avatar: AVATAR(11) },
        help_count: 8,
        is_you: false
      }
    },
    your_progress: {
      total_helps: 8,
      by_subject: {
        'Electrical Engineering': 5,
        'Economics': 3
      }
    }
  }
};

// ----------------------------------------------------------------------------
// CHARTS — GET /homework/stats/charts
// ----------------------------------------------------------------------------
export const SHOWCASE_CHART_DATA = {
  status: 'success',
  data: {
    daily_activity: [
      { day: 'Mon', helps_given: 1, assignments_created: 0 },
      { day: 'Tue', helps_given: 2, assignments_created: 1 },
      { day: 'Wed', helps_given: 0, assignments_created: 0 },
      { day: 'Thu', helps_given: 3, assignments_created: 1 },
      { day: 'Fri', helps_given: 1, assignments_created: 0 },
      { day: 'Sat', helps_given: 0, assignments_created: 1 },
      { day: 'Sun', helps_given: 2, assignments_created: 0 }
    ],
    subject_completion: [
      { subject: 'Electrical Engineering', completed: 4, total: 5, rate: 80 },
      { subject: 'Economics', completed: 2, total: 3, rate: 67 },
      { subject: 'Biochemistry', completed: 3, total: 3, rate: 100 },
      { subject: 'Organic Chemistry', completed: 1, total: 3, rate: 33 }
    ],
    reactions_received: [
      { reaction: 'Thanks 🙏', count: 5 },
      { reaction: 'Lifesaver 🔥', count: 3 },
      { reaction: 'Mind Blown 🧠', count: 2 },
      { reaction: 'Perfect ⭐', count: 1 }
    ],
    response_time: {
      average: '38m',
      fastest: '6m',
      total_timed: 8
    }
  }
};

// ----------------------------------------------------------------------------
// HELPERS MODAL — GET /homework/:id/helpers
// Only assignment 9002 (shared Biochem lab report) is shared, so that's the
// only ID this needs to respond to.
// ----------------------------------------------------------------------------
export const SHOWCASE_HELPERS = {
  status: 'success',
  data: {
    assignment: {
      id: 9002,
      title: 'Lab Report: Enzyme Kinetics',
      subject: 'Biochemistry',
      difficulty: 'medium',
      status: 'in_progress',
      due_date: hoursFromNow(52)
    },
    total_helpers: 2,
    helpers: [
      {
        id: 7401,
        helper: HELPER_MARCUS,
        status: 'submitted',
        subject: 'Biochemistry',
        difficulty: 'medium',
        created_at: hoursAgo(20),
        submitted_at: hoursAgo(3),
        response_time_seconds: 61200,
        feedback_at: null,
        has_solution: true,
        has_feedback: false,
        is_marked_helpful: false,
        reaction_type: null
      },
      {
        id: 7402,
        helper: HELPER_ELENA,
        status: 'completed',
        subject: 'Biochemistry',
        difficulty: 'medium',
        created_at: hoursAgo(26),
        submitted_at: hoursAgo(18),
        response_time_seconds: 28800,
        feedback_at: hoursAgo(15),
        has_solution: true,
        has_feedback: true,
        is_marked_helpful: true,
        reaction_type: 'lifesaver'
      }
    ]
  }
};

// ----------------------------------------------------------------------------
// SUBMISSION DETAILS — GET /homework/submission/:id
// Keyed by submission ID; homework.api.js picks the right one at call time.
// Covers both directions (me as helper / me as requester) and pending,
// submitted, and completed states.
// ----------------------------------------------------------------------------
export const SHOWCASE_SUBMISSIONS = {
  // I'm HELPING Priya with her circuit analysis — pending, no solution yet
  7301: {
    status: 'success',
    data: {
      id: 7301,
      title: 'Circuit Analysis: Thevenin Equivalent Problems',
      subject: 'Electrical Engineering',
      difficulty: 'medium',
      description: 'Need help finding the Thevenin equivalent for problems 4 and 5 — my resistance calculations keep coming out wrong.',
      status: 'pending',
      created_at: hoursAgo(14),
      submitted_at: null,
      requester: REQUESTER_PRIYA,
      helper: ME_AS_HELPER,
      assignment: { due_date: hoursFromNow(10), status: 'shared_for_help' },
      solution: null,
      feedback: null,
      i_am_helper: true,
      i_am_requester: false
    }
  },
  // I'm HELPING Jonah with macro — I already submitted, waiting on his feedback
  7302: {
    status: 'success',
    data: {
      id: 7302,
      title: 'Macroeconomics: IS-LM Model Short Answer',
      subject: 'Economics',
      difficulty: 'medium',
      description: 'Answering how a fiscal expansion shifts the IS curve — want to check my graph and reasoning before submitting.',
      status: 'submitted',
      created_at: hoursAgo(30),
      submitted_at: hoursAgo(5),
      requester: REQUESTER_JONAH,
      helper: ME_AS_HELPER,
      assignment: { due_date: hoursFromNow(40), status: 'shared_for_help' },
      solution: {
        text: 'A fiscal expansion (higher G or lower T) shifts the IS curve rightward since aggregate expenditure rises at every interest rate. This raises equilibrium output and the interest rate along the LM curve — the rate increase then partially crowds out private investment, which is why the output gain is smaller than the simple multiplier would predict. I sketched the shift on the graph and walked through the crowding-out mechanism step by step.',
        submitted_at: hoursAgo(5),
        resources: []
      },
      feedback: null,
      i_am_helper: true,
      i_am_requester: false
    }
  },
  // Marcus is helping ME with Biochem — he submitted, I haven't given feedback yet
  7401: {
    status: 'success',
    data: {
      id: 7401,
      title: 'Lab Report: Enzyme Kinetics',
      subject: 'Biochemistry',
      difficulty: 'medium',
      description: 'Write up Michaelis-Menten results from Tuesday\'s lab. Include the Lineweaver-Burk plot and error analysis.',
      status: 'submitted',
      created_at: hoursAgo(20),
      submitted_at: hoursAgo(3),
      requester: ME_AS_HELPER,
      helper: HELPER_MARCUS,
      assignment: { due_date: hoursFromNow(52), status: 'shared_for_help' },
      solution: {
        text: 'Plotted 1/V against 1/[S] for all six substrate concentrations — the Lineweaver-Burk fit gives Km ≈ 0.42 mM and Vmax ≈ 18.3 µmol/min. Your raw data at the two lowest concentrations had more scatter, so I noted the propagated error range in the writeup rather than treating them as outliers. Full regression and residuals are in the attached PDF.',
        submitted_at: hoursAgo(3),
        resources: [
          { url: '/static/showcase/enzyme-kinetics-analysis.pdf', type: 'document', filename: 'lineweaver-burk-analysis.pdf' }
        ]
      },
      feedback: null,
      i_am_helper: false,
      i_am_requester: true
    }
  },
  // Elena helped ME with Biochem — fully completed, feedback already given
  7402: {
    status: 'success',
    data: {
      id: 7402,
      title: 'Lab Report: Enzyme Kinetics',
      subject: 'Biochemistry',
      difficulty: 'medium',
      description: 'Write up Michaelis-Menten results from Tuesday\'s lab. Include the Lineweaver-Burk plot and error analysis.',
      status: 'completed',
      created_at: hoursAgo(26),
      submitted_at: hoursAgo(18),
      requester: ME_AS_HELPER,
      helper: HELPER_ELENA,
      assignment: { due_date: hoursFromNow(52), status: 'shared_for_help' },
      solution: {
        text: 'Walked through the Michaelis-Menten derivation and cross-checked it against a nonlinear regression instead of just the linearized Lineweaver-Burk plot — the two methods agreed within 4%, which is worth mentioning in your discussion section since it strengthens your Km estimate.',
        submitted_at: hoursAgo(18),
        resources: []
      },
      feedback: {
        text: 'This saved me so much time, thank you! The nonlinear regression cross-check was exactly the kind of rigor my TA wanted to see.',
        given_at: hoursAgo(15),
        resources: []
      },
      i_am_helper: false,
      i_am_requester: true
    }
  }
};

