// Run with: node --test tests/js
// Verifies the own-profile templates never emit user-controlled markup.
import test from 'node:test';
import assert from 'node:assert/strict';

// Minimal browser globals so the shared feed modules can be imported.
globalThis.window = globalThis;
globalThis.document = {
  addEventListener() {}, getElementById() { return null; },
  querySelector() { return null; }, querySelectorAll() { return []; },
  createElement() { return { style: {}, classList: { add() {}, remove() {} }, setAttribute() {}, appendChild() {}, addEventListener() {} }; },
  body: {},
};
globalThis.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
globalThis.location = { href: '', pathname: '/', search: '', hash: '' };

const t = await import('../../static/js/profile/profile.templates.js');
const { escapeHtml } = await import('../../static/js/core/escape.js');

const PAYLOADS = [
  '"><img src=x onerror=alert(1)>',
  '<script>alert(1)</script>',
  "'><svg onload=alert(1)>",
];

// Remove every correctly escaped copy of the payload from the output. If the
// payload (or any part of it that can execute) is still present afterwards,
// something rendered it unescaped or only partially escaped.
function assertNoInjectedMarkup(html, payload) {
  const leftover = html.split(escapeHtml(payload)).join('');
  assert.ok(!leftover.includes(payload), `payload rendered verbatim: ${payload}`);
  assert.ok(!/alert\(1\)/.test(leftover), `executable text left after removing escaped payload: ${payload}`);
  assert.ok(!/<script/i.test(leftover), 'injected <script>');
}

test('escapeHtml escapes the five HTML metacharacters and tolerates null', () => {
  assert.equal(escapeHtml(`<a href="x">&'</a>`), '&lt;a href=&quot;x&quot;&gt;&amp;&#39;&lt;/a&gt;');
  assert.equal(escapeHtml(null), '');
  assert.equal(escapeHtml(undefined), '');
  assert.equal(escapeHtml(12), '12');
});

for (const payload of PAYLOADS) {
  test(`header escapes name, bio, department and class level: ${payload}`, () => {
    const html = t.buildProfileHeader({
      name: payload, username: 'u1', bio: payload, avatar: payload,
      department: payload, class_level: payload, reputation: 1, reputation_level: payload,
      stats: {}, learning_goals: [payload],
    });
    assertNoInjectedMarkup(html, payload);
  });

  test(`connections tab escapes other users' fields: ${payload}`, () => {
    const html = t.buildConnectionsTab([
      { user: { id: 1, name: payload, username: 'x', department: payload, avatar: payload, reputation_level: payload } },
    ]);
    assertNoInjectedMarkup(html, payload);
  });

  test(`badge details escape recent earners (attribute context): ${payload}`, () => {
    const html = t.buildBadgeDetailsModal({
      badge: { id: 1, name: payload, description: payload, rarity: 'common', icon: '🏅' },
      recent_earners: [{ name: payload, avatar: payload }],
    });
    assertNoInjectedMarkup(html, payload);
  });

  test(`skills and goals lists escape values: ${payload}`, () => {
    assertNoInjectedMarkup(t.buildSkillsList([payload]), payload);
    assertNoInjectedMarkup(t.buildGoalsList([payload]), payload);
  });

  test(`post card escapes the author: ${payload}`, () => {
    const html = t.buildProfilePostCard({
      id: 1, post_type: 'discussion', tags: [payload],
      author: { name: payload, username: payload, avatar: payload },
      posted_at: new Date().toISOString(), resources: [],
    });
    assertNoInjectedMarkup(html, payload);
  });
}
