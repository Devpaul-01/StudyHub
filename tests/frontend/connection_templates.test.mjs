// Dependency-free frontend tests for the connections UI. Run with:
//   node --test tests/frontend/*.test.mjs
// (audit CON-04: user-controlled text must be HTML-escaped in templates)
import test from 'node:test';
import assert from 'node:assert/strict';

// connection.constants.js touches `document` at import time.
globalThis.document = { querySelector: () => null, getElementById: () => null };

const utils = await import('../../static/js/connection/connection.utils.js');
const tpl = await import('../../static/js/connection/connection.templates.js');

const PAYLOAD = '<img src=x onerror=alert(1)>"\'&';

test('escapeHtml escapes every HTML-significant character', () => {
  assert.equal(utils.escapeHtml(PAYLOAD), '&lt;img src=x onerror=alert(1)&gt;&quot;&#39;&amp;');
  assert.equal(utils.escapeHtml(null), '');
  assert.equal(utils.escapeHtml(undefined), '');
  assert.equal(utils.escapeHtml(42), '42');
});

const user = (over = {}) => ({
  id: 7, name: PAYLOAD, username: PAYLOAD, avatar: PAYLOAD, bio: PAYLOAD,
  department: PAYLOAD, class_level: PAYLOAD, reputation_level: 'Bronze', ...over,
});

function assertNoRawPayload(html) {
  assert.ok(!html.includes('<img src=x'), 'raw <img> payload must not survive');
  assert.ok(!html.includes('onerror=alert(1)>'), 'raw event-handler tag must not survive');
  assert.ok(html.includes('&lt;img src=x'), 'payload should appear escaped');
}

test('received request card escapes the sender message and profile fields', () => {
  assertNoRawPayload(tpl.createReceivedConnectionCard({
    id: 1, user: user(), message: PAYLOAD, time_ago: '1h', mutuals_count: 0, onboarding_details: {},
  }));
});

test('sent request card escapes your_message and profile fields', () => {
  assertNoRawPayload(tpl.createSentConnectionCard({
    id: 1, user: user(), your_message: PAYLOAD, status: 'pending', requested_at: new Date().toISOString(),
    onboarding_details: {},
  }));
});

test('connected, discovery, blocked and search cards escape profile fields', () => {
  assertNoRawPayload(tpl.createConnectedConnectionCard({
    id: 1, user: user(), health_score: 80, suggestion: PAYLOAD, shared_threads: 0,
    onboarding_details: { strong_subjects: [PAYLOAD], subjects: [PAYLOAD] },
  }));
  assertNoRawPayload(tpl.createDiscoveryConnectionCard({ user: user(), mutual_connections: [], onboarding_details: {} }));
  assertNoRawPayload(tpl.createBlockedUserCard(user({ connection_id: 3 })));
  assertNoRawPayload(tpl.createSearchResultCard(user({ connection_status: 'none' })));
});

test('available-now list escapes names and the subject', () => {
  const html = tpl.buildAvailableNowList([{
    user: user(), availability: { can_help_with_subject: true, is_online: true, schedule_available: false },
  }], PAYLOAD);
  assertNoRawPayload(html);
});
