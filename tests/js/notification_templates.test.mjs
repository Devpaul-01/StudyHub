// Run with: node --test tests/js/notification_templates.test.mjs (or via pytest: tests/unit/test_frontend_js.py)
// Notification cards must never turn server-provided text into markup (audit NOTIF-01).
import test from 'node:test';
import assert from 'node:assert/strict';

const { createNotificationCard } = await import('../../static/js/notification/notification.templates.js');
const { safeNotificationLink } = await import('../../static/js/notification/notification.utils.js');
const { escapeHtml } = await import('../../static/js/core/escape.js');

const PAYLOADS = [
  '<img src=x onerror=alert(1)>',
  '"><svg onload=alert(2)>',
  "'><script>alert(3)</script>",
];

function card(overrides = {}) {
  return createNotificationCard({
    id: 7, title: 'Title', body: 'Body', link: '/student/post/1',
    is_read: false, created_at: new Date().toISOString(), ...overrides,
  });
}

// Remove every correctly escaped copy of the payload; anything executable that is
// still present afterwards was rendered unescaped.
function assertInert(html, payload) {
  const leftover = html.split(escapeHtml(payload)).join('');
  assert.ok(!leftover.includes(payload), `payload rendered verbatim: ${payload}`);
  assert.ok(!/alert\(\d\)/.test(leftover), `executable text left over: ${payload}`);
  assert.ok(!/<script/i.test(leftover), 'injected <script>');
  assert.ok(!/<img/i.test(leftover), 'injected <img>');
}

for (const payload of PAYLOADS) {
  test(`title and body are escaped: ${payload}`, () => {
    const html = card({ title: `${payload} commented on your post`, body: `"${payload}"` });
    assertInert(html, payload);
    assert.ok(html.includes(escapeHtml(payload)), 'escaped text should still be visible to the user');
  });
}

test('link attribute cannot be broken out of', () => {
  const html = card({ link: 'x" onmouseover="alert(3)' });
  assert.ok(!/onmouseover=/.test(html));
  assert.match(html, /data-link=""/);
});

test('non-numeric ids cannot inject attributes', () => {
  const html = card({ id: '1" onclick="alert(4)' });
  assert.ok(!/onclick=/.test(html));
  assert.match(html, /data-notification-id="0"/);
});

test('a valid same-origin link is preserved', () => {
  assert.match(card({ link: '/student/post/42?x=1#c' }), /data-link="\/student\/post\/42\?x=1#c"/);
});

test('safeNotificationLink accepts only app-relative paths', () => {
  for (const ok of ['/student/post/1', '/a?b=c#d', '  /padded  ']) {
    assert.notEqual(safeNotificationLink(ok), '', ok);
  }
  for (const bad of [
    'javascript:alert(1)', 'JaVaScRiPt:alert(1)', '//evil.example', 'https://evil.example/x',
    'data:text/html,<script>1</script>', '/\\evil.example', '/ok\nnewline', '/has space',
    'relative/path', '', null, undefined, 42, {},
  ]) {
    assert.equal(safeNotificationLink(bad), '', String(bad));
  }
});
