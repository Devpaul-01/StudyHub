// Concurrent notification loads: the newest request must win and none may be
// dropped (audit NOTIF-15).
import test from 'node:test';
import assert from 'node:assert/strict';

const { createLatestGuard } = await import('../../static/js/notification/notification.utils.js');

test('only the most recent ticket is "latest"', () => {
  const guard = createLatestGuard();
  const first = guard.next();
  assert.equal(guard.isLatest(first), true);
  const second = guard.next();
  assert.equal(guard.isLatest(first), false);
  assert.equal(guard.isLatest(second), true);
});

// Mirror of loadNotifications(): apply a response only when it is still the latest.
async function simulate(responses) {
  const guard = createLatestGuard();
  let shown = null;
  const run = async (label, delay) => {
    const ticket = guard.next();
    await new Promise((r) => setTimeout(r, delay));
    if (!guard.isLatest(ticket)) return;
    shown = label;
  };
  await Promise.all(responses.map(([label, delay]) => run(label, delay)));
  return shown;
}

test('a slow earlier response cannot overwrite a newer filter', async () => {
  // "all" starts first but answers last; the user already tapped "badges".
  assert.equal(await simulate([['all', 30], ['badges', 5]]), 'badges');
});

test('the request made during an in-flight load is not dropped', async () => {
  assert.equal(await simulate([['initial', 20], ['filtered', 10]]), 'filtered');
});

test('a single load still renders', async () => {
  assert.equal(await simulate([['only', 1]]), 'only');
});
