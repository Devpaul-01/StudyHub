// node --test tests/frontend/*.test.mjs
// Audit CON-13 / CON-15: API wrappers match the backend contract.
import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.document = { querySelector: () => null, getElementById: () => null };

const calls = [];
let handler = async () => ({});
globalThis.api = { get: async (url) => { calls.push(url); return handler(url); }, post: async () => ({}) };

const { ConnectionAPI } = await import('../../static/js/connection/connection.api.js');

test('getBlockedUsers unwraps data.blocked_users (backend shape)', async () => {
  handler = async () => ({ status: 'success', data: { blocked_users: [{ id: 1 }, { id: 2 }], total: 2 } });
  const users = await ConnectionAPI.getBlockedUsers();
  assert.ok(Array.isArray(users));
  assert.deepEqual(users.map(u => u.id), [1, 2]);

  handler = async () => ({ status: 'success', data: { blocked_users: [], total: 0 } });
  assert.deepEqual(await ConnectionAPI.getBlockedUsers(), []);
});

test('getConnectionNotes tolerates a missing data object', async () => {
  handler = async () => ({ status: 'success' });
  assert.equal(await ConnectionAPI.getConnectionNotes(5), null);
});

test('fetchAllPages follows next_cursor until has_more is false', async () => {
  calls.length = 0;
  const pages = {
    '/x': { data: [1, 2], has_more: true, next_cursor: '2' },
    '/x?cursor=2': { data: [3], has_more: false, next_cursor: null },
  };
  handler = async (url) => pages[url];
  const result = await ConnectionAPI.fetchAllPages('/x');
  assert.deepEqual(result.data, [1, 2, 3]);
  assert.deepEqual(calls, ['/x', '/x?cursor=2']);
});

test('fetchAllPages is bounded even if the server keeps saying has_more', async () => {
  handler = async () => ({ data: [1], has_more: true, next_cursor: 'again' });
  const result = await ConnectionAPI.fetchAllPages('/loop', 3);
  assert.equal(result.data.length, 3);
});

test('loadAllConnectionData survives one failing section but not total failure', async () => {
  handler = async (url) => {
    if (url.includes('suggestions')) throw new Error('429');
    return { data: [], has_more: false };
  };
  const data = await ConnectionAPI.loadAllConnectionData();
  assert.deepEqual(data.suggestions, {});
  assert.deepEqual(data.connected, []);

  handler = async () => { throw new Error('down'); };
  await assert.rejects(() => ConnectionAPI.loadAllConnectionData(), /down/);
});
