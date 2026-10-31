/**
 * ============================================================================
 * StudyHub — Messaging Showcase Seed  (PORTFOLIO / DEMO ONLY)
 * ============================================================================
 * This file is NOT part of production. It injects realistic mock data into
 * the existing messaging state layer so the Messages feature can be
 * screenshotted fully populated, without depending on a live backend.
 *
 * How it works:
 *  - Imports the real messageState + render modules (no duplication).
 *  - Waits for the real initializeMessaging() bootstrap to finish its
 *    attempt (which will fail gracefully against a demo/no backend and
 *    just toast an error — harmless), then overwrites state with mock
 *    data and re-renders.
 *  - Opens one conversation by default so the thread view is visible too.
 *  - Stages two pending attachments in the composer so that state is
 *    also screenshot-ready.
 *
 * TO REMOVE: delete the <script type="module" src="message.showcase-seed.js">
 * tag (or this file). Nothing else in the app references it.
 * ============================================================================
 */

import * as messageState from './message.state.js';
import * as render       from './message.render.js';
import { showConversationView } from './message.modals.js';

// ----------------------------------------------------------------------------
// POV USER — the "logged in" student whose inbox this is
// ----------------------------------------------------------------------------

const ME = {
  id: 1001,
  name: 'Amara Osei',
  avatar: 'https://i.pravatar.cc/150?img=47',
  username: 'amara.osei',
};

// ----------------------------------------------------------------------------
// TIME HELPERS — offsets from "now" so the demo always looks current
// ----------------------------------------------------------------------------

const MIN = 60 * 1000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

function ago(ms) {
  return new Date(Date.now() - ms).toISOString();
}

// ----------------------------------------------------------------------------
// CONVERSATION PARTNERS
// ----------------------------------------------------------------------------

const partners = {
  jamal: {
    id: 2001,
    name: 'Jamal Reyes',
    avatar: 'https://i.pravatar.cc/150?img=12',
    username: 'jamal.reyes',
  },
  priya: {
    id: 2002,
    name: 'Priya Nandakumar',
    avatar: 'https://i.pravatar.cc/150?img=32',
    username: 'priya.n',
  },
  drChen: {
    id: 2003,
    name: 'Dr. Wen Chen',
    avatar: 'https://i.pravatar.cc/150?img=68',
    username: 'wchen.ta',
  },
  liam: {
    id: 2004,
    name: 'Liam Whitfield',
    avatar: 'https://i.pravatar.cc/150?img=14',
    username: 'liam.w',
  },
  sofia: {
    id: 2005,
    name: 'Sofia Marchetti',
    avatar: 'https://i.pravatar.cc/150?img=45',
    username: 'sofia.m',
  },
  devon: {
    id: 2006,
    name: 'Devon Blackwood',
    avatar: 'https://i.pravatar.cc/150?img=53',
    username: 'devon.b',
  },
};

// ----------------------------------------------------------------------------
// CONVERSATION LIST  (drives the left/list panel)
// ----------------------------------------------------------------------------

const mockConversations = [
  {
    partner: partners.priya,
    partner_online: true,
    is_blocked_by_me: false,
    blocked_by_partner: false,
    unread_count: 2,
    last_message: {
      is_own: false,
      body: "wait yes!! i just found the exact theorem we needed, sending it over",
      sent_at: ago(6 * MIN),
    },
  },
  {
    partner: partners.jamal,
    partner_online: true,
    is_blocked_by_me: false,
    blocked_by_partner: false,
    unread_count: 0,
    last_message: {
      is_own: true,
      body: "sounds good, I'll have the intro + methodology done before Thursday",
      sent_at: ago(38 * MIN),
    },
  },
  {
    partner: partners.drChen,
    partner_online: false,
    is_blocked_by_me: false,
    blocked_by_partner: false,
    unread_count: 1,
    last_message: {
      is_own: false,
      body: null, // attachment-only last message
      sent_at: ago(3 * HOUR),
    },
  },
  {
    partner: partners.sofia,
    partner_online: true,
    is_blocked_by_me: false,
    blocked_by_partner: false,
    unread_count: 0,
    last_message: {
      is_own: false,
      body: 'omg congrats on the internship offer!! we need to celebrate',
      sent_at: ago(5 * HOUR),
    },
  },
  {
    partner: partners.liam,
    partner_online: false,
    is_blocked_by_me: false,
    blocked_by_partner: false,
    unread_count: 0,
    last_message: {
      is_own: true,
      body: 'appreciate you, good luck on the final!',
      sent_at: ago(1 * DAY + 4 * HOUR),
    },
  },
  {
    partner: partners.devon,
    partner_online: false,
    is_blocked_by_me: false,
    blocked_by_partner: false,
    unread_count: 0,
    last_message: {
      is_own: false,
      body: 'can you send the recording from Tuesday\u2019s lecture when you get a sec',
      sent_at: ago(2 * DAY + 2 * HOUR),
    },
  },
];

// ----------------------------------------------------------------------------
// OPEN THREAD — Priya Nandakumar (Linear Algebra study partner)
// This is the conversation we'll open by default so the message view
// itself is fully screenshot-ready, not just the list.
// ----------------------------------------------------------------------------

const mockThread_priya = [
  {
    id: 9001,
    sender_id: partners.priya.id,
    sender: partners.priya,
    body: 'hey!! are you still up for going over the eigenvalues problem set tonight?',
    sent_at: ago(1 * DAY + 3 * HOUR),
    is_read: true,
    status: 'read',
    resources: [],
    reactions: {},
  },
  {
    id: 9002,
    sender_id: ME.id,
    sender: ME,
    body: "yes! I got through Q1-4 but Q5 is destroying me, the diagonalization step isn't clicking",
    sent_at: ago(1 * DAY + 2 * HOUR + 55 * MIN),
    is_read: true,
    status: 'sent',
    resources: [],
    reactions: {},
  },
  {
    id: 9003,
    sender_id: partners.priya.id,
    sender: partners.priya,
    body: 'oh that one is genuinely evil, professor Okafor said half the class got stuck there too',
    sent_at: ago(1 * DAY + 2 * HOUR + 50 * MIN),
    is_read: true,
    status: 'read',
    resources: [],
    reactions: {
      laugh: { emoji: '😂', count: 1 },
    },
  },
  {
    id: 9004,
    sender_id: partners.priya.id,
    sender: partners.priya,
    body: 'library study room 4, 7pm? I already booked it',
    sent_at: ago(1 * DAY + 2 * HOUR + 48 * MIN),
    is_read: true,
    status: 'read',
    resources: [],
    reactions: {},
  },
  {
    id: 9005,
    sender_id: ME.id,
    sender: ME,
    body: "perfect, see you there. I'll bring the practice midterm too",
    sent_at: ago(1 * DAY + 2 * HOUR + 40 * MIN),
    is_read: true,
    status: 'sent',
    resources: [],
    reactions: {},
  },
  {
    id: 9006,
    sender_id: partners.priya.id,
    sender: partners.priya,
    body: 'session was so helpful, I finally get diagonalization. thank you for explaining the change of basis part',
    sent_at: ago(20 * HOUR),
    is_read: true,
    status: 'read',
    resources: [],
    reactions: {
      love: { emoji: '❤️', count: 1 },
    },
  },
  {
    id: 9007,
    sender_id: ME.id,
    sender: ME,
    body: 'anytime! honestly explaining it out loud helped me more than the textbook did',
    sent_at: ago(19 * HOUR + 50 * MIN),
    is_read: true,
    status: 'sent',
    resources: [],
    reactions: {},
  },
  {
    id: 9008,
    sender_id: partners.priya.id,
    sender: partners.priya,
    body: 'quick q, does the final cover SVD or just eigendecomposition? the syllabus is vague',
    sent_at: ago(45 * MIN),
    is_read: false,
    status: 'read',
    resources: [],
    reactions: {},
  },
  {
    id: 9009,
    sender_id: partners.priya.id,
    sender: partners.priya,
    body: null,
    sent_at: ago(8 * MIN),
    is_read: false,
    status: 'read',
    resources: [
      {
        type: 'image',
        url: 'https://images.unsplash.com/photo-1596495577886-d920f1fb7238?w=800&q=80',
        filename: 'whiteboard_svd_notes.jpg',
        size: 1_240_000,
      },
    ],
    reactions: {},
  },
  {
    id: 9010,
    sender_id: partners.priya.id,
    sender: partners.priya,
    body: 'wait yes!! i just found the exact theorem we needed, sending it over',
    sent_at: ago(6 * MIN),
    is_read: false,
    status: 'read',
    resources: [],
    reactions: {},
  },
];

// Keep a couple of secondary threads in memory too, so switching
// conversations during the demo still shows populated content rather
// than an empty state — keyed by partner id.
const mockThreadsByPartnerId = {
  [partners.priya.id]: mockThread_priya,

  [partners.jamal.id]: [
    {
      id: 9101,
      sender_id: partners.jamal.id,
      sender: partners.jamal,
      body: "how's the research methods section coming along? I can take the literature review if you want to split it",
      sent_at: ago(2 * HOUR + 10 * MIN),
      is_read: true,
      status: 'read',
      resources: [],
      reactions: {},
    },
    {
      id: 9102,
      sender_id: ME.id,
      sender: ME,
      body: "that would help a ton, I've been buried in the data section",
      sent_at: ago(2 * HOUR),
      is_read: true,
      status: 'sent',
      resources: [],
      reactions: {},
    },
    {
      id: 9103,
      sender_id: partners.jamal.id,
      sender: partners.jamal,
      body: null,
      sent_at: ago(1 * HOUR + 20 * MIN),
      is_read: true,
      status: 'read',
      resources: [
        {
          type: 'document',
          url: '#',
          filename: 'group_project_outline_v3.pdf',
          size: 812_000,
        },
      ],
      reactions: {},
    },
    {
      id: 9104,
      sender_id: partners.jamal.id,
      sender: partners.jamal,
      body: 'draft outline, lmk what you think before our call',
      sent_at: ago(1 * HOUR + 19 * MIN),
      is_read: true,
      status: 'read',
      resources: [],
      reactions: {
        thumbs_up: { emoji: '👍', count: 1 },
      },
    },
    {
      id: 9105,
      sender_id: ME.id,
      sender: ME,
      body: "sounds good, I'll have the intro + methodology done before Thursday",
      sent_at: ago(38 * MIN),
      is_read: false,
      status: 'sent',
      resources: [],
      reactions: {},
    },
  ],

  [partners.drChen.id]: [
    {
      id: 9201,
      sender_id: ME.id,
      sender: ME,
      body: 'Hi Dr. Chen, I had a question about problem 3 on the algorithms homework \u2014 specifically the time complexity proof',
      sent_at: ago(4 * HOUR),
      is_read: true,
      status: 'sent',
      resources: [],
      reactions: {},
    },
    {
      id: 9202,
      sender_id: partners.drChen.id,
      sender: partners.drChen,
      body: 'Good question \u2014 attaching the annotated slide, look specifically at the recurrence relation on slide 14',
      sent_at: ago(3 * HOUR + 5 * MIN),
      is_read: true,
      status: 'read',
      resources: [],
      reactions: {},
    },
    {
      id: 9203,
      sender_id: partners.drChen.id,
      sender: partners.drChen,
      body: null,
      sent_at: ago(3 * HOUR),
      is_read: false,
      status: 'read',
      resources: [
        {
          type: 'document',
          url: '#',
          filename: 'lecture14_recurrence_relations_annotated.pdf',
          size: 2_150_000,
        },
      ],
      reactions: {},
    },
  ],

  [partners.sofia.id]: [
    {
      id: 9301,
      sender_id: ME.id,
      sender: ME,
      body: 'got the offer!! starting as a SWE intern this summer',
      sent_at: ago(5 * HOUR + 10 * MIN),
      is_read: true,
      status: 'sent',
      resources: [],
      reactions: {},
    },
    {
      id: 9302,
      sender_id: partners.sofia.id,
      sender: partners.sofia,
      body: 'omg congrats on the internship offer!! we need to celebrate',
      sent_at: ago(5 * HOUR),
      is_read: true,
      status: 'read',
      resources: [],
      reactions: {
        celebrate: { emoji: '🎉', count: 1 },
        fire: { emoji: '🔥', count: 1 },
      },
    },
  ],

  [partners.liam.id]: [
    {
      id: 9401,
      sender_id: partners.liam.id,
      sender: partners.liam,
      body: 'thanks again for the study guide, it honestly saved me',
      sent_at: ago(1 * DAY + 4 * HOUR + 5 * MIN),
      is_read: true,
      status: 'read',
      resources: [],
      reactions: {},
    },
    {
      id: 9402,
      sender_id: ME.id,
      sender: ME,
      body: 'appreciate you, good luck on the final!',
      sent_at: ago(1 * DAY + 4 * HOUR),
      is_read: true,
      status: 'sent',
      resources: [],
      reactions: {},
    },
  ],

  [partners.devon.id]: [
    {
      id: 9501,
      sender_id: partners.devon.id,
      sender: partners.devon,
      body: 'can you send the recording from Tuesday\u2019s lecture when you get a sec',
      sent_at: ago(2 * DAY + 2 * HOUR),
      is_read: true,
      status: 'read',
      resources: [],
      reactions: {},
    },
  ],
};

// ----------------------------------------------------------------------------
// PENDING ATTACHMENTS  (staged in the composer, not yet sent)
// Populates the `pending-attachments-strip` — one image thumbnail mid-upload,
// one settled file chip, so both visual states are visible in a screenshot.
// ----------------------------------------------------------------------------

const mockPendingAttachments = [
  {
    id: 'file_showcase_1',
    type: 'image',
    filename: 'linalg_review_photo.jpg',
    localUrl: 'https://images.unsplash.com/photo-1543286386-713bdd548da4?w=400&q=80',
    url: 'https://images.unsplash.com/photo-1543286386-713bdd548da4?w=400&q=80',
    size: 980_000,
    uploading: true,
  },
  {
    id: 'file_showcase_2',
    type: 'document',
    filename: 'practice_midterm_answers.pdf',
    localUrl: null,
    url: '#',
    size: 430_000,
    uploading: false,
  },
];

// ----------------------------------------------------------------------------
// SEED ROUTINE
// ----------------------------------------------------------------------------

function seedMessagingShowcase() {
  // 1. Current user
  messageState.setCurrentUser(ME);

  // 2. Conversation list
  messageState.setConversations(mockConversations);

  // 3. Open Priya's conversation by default \u2014 the richest thread
  //    (mirrors what setCurrentConversation does, but we also need to
  //    inject the thread's messages since there's no live backend to
  //    serve them).
  messageState.setCurrentConversation(partners.priya.id);
  messageState.setMessages(mockThread_priya, false);

  // 4. Stage pending attachments in the composer
  mockPendingAttachments.forEach(a => messageState.addPendingAttachment(a));

  // 5. Render everything
  render.renderConversationList();
  showConversationView(); // flips list \u2192 chat panel, renders header + messages
  render.renderPendingAttachments();

  console.log('\ud83c\udfa8 StudyHub messaging showcase data seeded.');
}

// ----------------------------------------------------------------------------
// WIRE-UP: run after the real init has had its turn (and failed gracefully
// against the demo environment's lack of a real backend), so this seed's
// render calls are the last word.
// ----------------------------------------------------------------------------

function whenMessagesActive(callback) {
  const section = document.getElementById('messages');
  if (!section) return;

  const run = () => setTimeout(callback, 50); // let the real init's async chain settle first

  if (section.classList.contains('active')) {
    run();
  }

  const observer = new MutationObserver((mutations) => {
    for (const m of mutations) {
      if (m.type === 'attributes' && m.attributeName === 'class') {
        if (section.classList.contains('active')) run();
      }
    }
  });
  observer.observe(section, { attributes: true, attributeFilter: ['class'] });
}

document.addEventListener('DOMContentLoaded', () => {
  whenMessagesActive(seedMessagingShowcase);
});

// Also expose manually in case #messages is already active and DOMContentLoaded
// already fired before this script loaded (module scripts are deferred by default,
// so this covers that race too).
if (document.readyState !== 'loading') {
  whenMessagesActive(seedMessagingShowcase);
}

// Manual re-trigger if you need to reset the demo state mid-session
window.reseedMessagingShowcase = seedMessagingShowcase;
