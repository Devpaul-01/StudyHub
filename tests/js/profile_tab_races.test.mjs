// Run with: node --test tests/js
// Proves slow responses cannot overwrite a tab/filter the user moved on from.
import test from 'node:test';
import assert from 'node:assert/strict';

// ── Fake DOM ────────────────────────────────────────────────────────────────
const els = {};
function el(id) { return (els[id] ||= { id, innerHTML: '', dataset: {}, classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } }, style: {} }); }
globalThis.window = globalThis;
globalThis.document = {
  addEventListener() {}, getElementById: el, querySelector() { return null; }, querySelectorAll() { return []; },
  createElement() { return el('x'); }, body: {},
};
globalThis.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
globalThis.location = { href: '', pathname: '/', search: '', hash: '' };
globalThis.showToast = () => {};

// ── Controllable API: each request resolves when the test says so ───────────
const pending = {};
function deferred(key) {
  return new Promise((resolve) => { (pending[key] ||= []).push(resolve); });
}
globalThis.api = {
  get(url) {
    if (url.startsWith('/profile/my-posts')) return deferred('posts:' + new URL('http://x' + url).searchParams.get('type'));
    if (url === '/profile/my-stats') return deferred('stats');
    if (url.startsWith('/analytics/activity-heatmap')) return Promise.resolve({ data: null });
    if (url === '/connections/list') return deferred('connections');
    if (url === '/profile/me/data') return deferred('me');
    return Promise.resolve({ data: null });
  },
};
const release = (key, value) => (pending[key] || []).shift()(value);
const tick = () => new Promise((r) => setTimeout(r, 0));

const { ProfileHandlers, initProfile } = await import('../../static/js/profile/profile.delegation.js');
const { profileState } = await import('../../static/js/profile/profile.state.js');

function reset() {
  profileState.invalidateAll();
  profileState.setCurrentTab('posts');
  for (const k of Object.keys(els)) delete els[k];
  for (const k of Object.keys(pending)) delete pending[k];
}
const tab = (name) => ({ dataset: { tab: name } });

test('a slow tab response does not overwrite the tab the user switched to', async () => {
  reset();
  const toStats = ProfileHandlers['profile-tab'](tab('stats'));         // stats request pending
  await tick();
  const toConns = ProfileHandlers['profile-tab'](tab('connections'));   // user moves on
  await tick();

  release('connections', { data: [] });
  await toConns;
  const afterConnections = el('profile-tab-content').innerHTML;

  release('stats', { data: { posts: {}, help: {}, engagement: {} } });  // stale answer arrives last
  await toStats;

  assert.equal(profileState.getCurrentTab(), 'connections');
  assert.equal(el('profile-tab-content').innerHTML, afterConnections, 'stats must not repaint over connections');
  assert.ok(profileState.stats, 'the stats data is still kept for when the user returns');
});

test('an older post-filter response is dropped when a newer filter was chosen', async () => {
  reset();
  const first = ProfileHandlers['profile-filter-posts']({ dataset: { filter: 'question' } });
  await tick();
  const second = ProfileHandlers['profile-filter-posts']({ dataset: { filter: 'discussion' } });
  await tick();

  release('posts:discussion', { data: { posts: [{ id: 2, title: 'newer', post_type: 'discussion', tags: [], author: {}, posted_at: new Date().toISOString() }] } });
  await second;
  release('posts:question', { data: { posts: [{ id: 1, title: 'older', post_type: 'question', tags: [], author: {}, posted_at: new Date().toISOString() }] } });
  await first;

  assert.equal(profileState.postsFilter, 'discussion');
  assert.deepEqual(profileState.posts.map((p) => p.id), [2]);
});

test('a failed header load shows a Retry button that loads the profile', async () => {
  reset();
  const init = initProfile();
  await tick();
  release('me', null);                       // header request fails (wrapper returns null)
  release('posts:all', { data: { posts: [] } });
  await init;

  assert.match(el('profile-header-container').innerHTML, /profile-retry-header/);
  assert.equal(profileState.isLoaded('profile'), false);

  const retry = ProfileHandlers['profile-retry-header']();
  await tick();
  release('me', { data: { id: 1, name: 'Ada', username: 'ada', stats: {}, skills: [], learning_goals: [] } });
  await retry;

  assert.equal(profileState.isLoaded('profile'), true);
  assert.match(el('profile-header-container').innerHTML, /Ada/);
});

test('a tab chosen while the header is still loading is not reset to posts', async () => {
  reset();
  const init = initProfile();
  await tick();
  const toStats = ProfileHandlers['profile-tab'](tab('stats'));
  await tick();

  release('me', { data: { id: 1, name: 'Ada', username: 'ada', stats: {}, skills: [], learning_goals: [] } });
  release('posts:all', { data: { posts: [] } });
  await init;
  release('stats', { data: { posts: {}, help: {}, engagement: {} } });
  await toStats;

  assert.equal(profileState.getCurrentTab(), 'stats');
});
