// Run with: node --test tests/js
import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = globalThis;
globalThis.document = { addEventListener() {}, getElementById() { return null; }, querySelector() { return null; }, querySelectorAll() { return []; } };

const { renderPublicProfile, renderPrivateProfile } = await import('../../static/js/profile-view/profile-view.templates.js');

const baseUser = (over = {}) => ({
  id: 1, username: 'ada', name: 'Ada', bio: 'hi', avatar: '', department: 'CS', class_level: 'Junior',
  reputation: 5, reputation_level: 'Newbie', active_status: true,
  last_active: new Date(Date.now() - 3 * 3600 * 1000).toISOString(), ...over,
});
const publicData = (user) => ({ user, stats: {}, badges: [], recent_posts: [], active_threads: [], skills: [], learning_goals: [] });

test('the activity text says "Last signed in", not "Active"', () => {
  const html = renderPublicProfile(publicData(baseUser()), false);
  assert.match(html, /Last signed in/);
  assert.doesNotMatch(html, /• Active \d|• Active just/);
});

test('no activity text when the owner hides it (null last_active)', () => {
  const html = renderPublicProfile(publicData(baseUser({ last_active: null })), false);
  assert.doesNotMatch(html, /Last signed in/);
});

test('avatar URLs are attribute-escaped on public and private views', () => {
  const evil = '"><img src=x onerror=alert(1)>';
  const pub = renderPublicProfile(publicData(baseUser({ avatar: evil })), false);
  const priv = renderPrivateProfile({ user: { ...baseUser({ avatar: evil }), pinned_posts: [] }, privacy: { message: 'x' } }, false);
  for (const html of [pub, priv]) {
    assert.ok(!html.includes(evil), 'raw payload present');
    assert.ok(!/<img src=x/i.test(html), 'injected element present');
  }
});
