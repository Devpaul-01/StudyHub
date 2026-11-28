// Run with: node --test tests/js/
import test from 'node:test';
import assert from 'node:assert/strict';
import { escapeHtml, getAvatarHTML } from '../../static/js/leaderboard/leaderboard.utils.js';
import { createEntryRow, createPodium } from '../../static/js/leaderboard/leaderboard.templates.js';

const HOSTILE = 'x" onmouseover="window.__pwned=1" data-x="';

const entry = (name) => ({
  rank: 7, rank_change: null, is_you: false, score: 5,
  user: { id: 9, name, username: 'evil1', avatar: null, department: 'CS' },
  reputation: { total: 5, level: { name: 'Newbie', icon: 'N', color: '#000' } },
  streaks: {},
});

test('escapeHtml escapes quotes as well as angle brackets and ampersands', () => {
  assert.equal(escapeHtml(`<a href="x">'&'</a>`), '&lt;a href=&quot;x&quot;&gt;&#39;&amp;&#39;&lt;/a&gt;');
});

test('escapeHtml keeps falsy-but-meaningful values and drops null/undefined', () => {
  assert.equal(escapeHtml(0), '0');
  assert.equal(escapeHtml(null), '');
  assert.equal(escapeHtml(undefined), '');
});

test('a hostile display name cannot break out of the message button attributes', () => {
  const html = createEntryRow(entry(HOSTILE));
  assert.ok(!/"\s+onmouseover=/.test(html), 'no injected event-handler attribute');
  assert.ok(html.includes('&quot;'), 'quotes are entity-encoded');
});

test('a hostile display name is inert in podium markup too', () => {
  const html = createPodium([entry(HOSTILE), entry('b'), entry('c')]);
  assert.ok(!/"\s+onmouseover=/.test(html));
});

test('avatar initials are escaped', () => {
  const html = getAvatarHTML({ name: '<script>', username: 'u' });
  assert.ok(!html.includes('<script>'));
});
