// The unread badge must exist, track its count, and never go negative
// (audit NOTIF-03 / NOTIF-18).
import test from 'node:test';
import assert from 'node:assert/strict';

function fakeBadge() {
  return { textContent: '', style: { display: '' } };
}

const badges = [fakeBadge(), fakeBadge()];
globalThis.document = {
  querySelectorAll(selector) {
    assert.match(selector, /data-notification-badge/);
    return badges;
  },
};

const { updateBadgeCount, adjustBadgeCount, getBadgeCount } =
  await import('../../static/js/notification/notification.utils.js');

test('updates every badge on the page, not one hard-coded id', () => {
  updateBadgeCount(3);
  for (const b of badges) {
    assert.equal(b.textContent, '3');
    assert.equal(b.style.display, 'flex');
  }
});

test('zero hides the badge and clears its text', () => {
  updateBadgeCount(0);
  for (const b of badges) {
    assert.equal(b.textContent, '');
    assert.equal(b.style.display, 'none');
  }
});

test('adjustBadgeCount tracks +1 / -1 from the owned count', () => {
  updateBadgeCount(2);
  adjustBadgeCount(1);
  assert.equal(getBadgeCount(), 3);
  adjustBadgeCount(-1);
  adjustBadgeCount(-1);
  assert.equal(badges[0].textContent, '1');
});

test('the count can never go negative', () => {
  updateBadgeCount(1);
  adjustBadgeCount(-5);
  assert.equal(getBadgeCount(), 0);
  assert.equal(badges[0].style.display, 'none');
});

test('garbage counts are treated as zero', () => {
  for (const bad of [undefined, null, 'abc', NaN, -4]) {
    updateBadgeCount(bad);
    assert.equal(getBadgeCount(), 0, String(bad));
  }
});
