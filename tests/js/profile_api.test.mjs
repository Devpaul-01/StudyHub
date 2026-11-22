// Run with: node --test tests/js
import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.showToast = () => {};
const calls = [];
globalThis.api = {
  async get() { throw new Error('boom'); },
  async post(url, body) { calls.push(['post', url, body]); throw new Error('Name must be at least 3 characters'); },
  async patch(url, body) { calls.push(['patch', url, body]); throw new Error('Name must be at least 3 characters'); },
  async delete(url, body) { calls.push(['delete', url, body]); throw new Error('Skill name required'); },
};

const profileApi = await import('../../static/js/profile/profile.api.js');

test('failed writes surface the server message instead of null', async () => {
  const res = await profileApi.saveProfileEdit({ name: 'ab' });
  assert.deepEqual(res, { status: 'error', message: 'Name must be at least 3 characters' });
});

for (const [fn, args] of [
  ['addSkill', ['x']], ['removeSkill', ['x']], ['addLearningGoal', ['x']], ['removeLearningGoal', ['x']],
  ['saveVisibilitySettings', [{}]], ['saveStudySchedule', [{}]], ['saveAcademicInfo', [{}]],
  ['pinPost', [1]], ['unpinPost', [1]], ['uploadAvatar', [new Blob(['x'])]], ['removeAvatar', []],
  ['toggleFeatureBadge', [1]], ['deletePost', [1]],
]) {
  test(`${fn} returns an error object callers can show`, async () => {
    const res = await profileApi[fn](...args);
    assert.equal(res.status, 'error');
    assert.ok(res.message, 'message should be forwarded');
  });
}

test('reads still return empty values rather than error objects', async () => {
  assert.equal(await profileApi.fetchProfileData(), null);
  assert.deepEqual(await profileApi.fetchMyPosts(), []);
});

test('skill and goal removal send the value in the request body', async () => {
  calls.length = 0;
  await profileApi.removeSkill('C/C++');
  await profileApi.removeLearningGoal('Learn ML');
  assert.deepEqual(calls, [
    ['delete', '/profile/skills', { skill: 'C/C++' }],
    ['delete', '/profile/learning-goals', { goal: 'Learn ML' }],
  ]);
});
