// Drives the real leaderboard.events.js against a fake `api` under jsdom.
// Needs jsdom (npm install --prefix tests/frontend); skipped without it.
import test from 'node:test';
import assert from 'node:assert/strict';

let JSDOM = null;
try { ({ JSDOM } = await import('jsdom')); } catch { /* skipped below */ }
const skip = JSDOM ? false : 'jsdom not installed';

const HTML = `<!doctype html><body>
  <div id="lb-list"></div><div id="lb-my-strip-wrapper"></div><div id="lb-breakdown-wrapper"></div>
  <div id="lb-stats-banner"></div><section id="leaderboard"></section>
  <div id="lb-period-tabs"><button class="lb-period-tab" data-period="weekly"></button>
  <button class="lb-period-tab" data-period="monthly"></button></div><select id="lb-dept-filter"></select></body>`;

let events, state, pending, calls;

async function setup() {
  const dom = new JSDOM(HTML, { url: 'http://localhost/' });
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  pending = []; calls = [];
  globalThis.api = {
    get(url) { calls.push(url); return new Promise((resolve, reject) => pending.push({ url, resolve, reject })); },
  };
  state = (await import('../../static/js/leaderboard/leaderboard.state.js')).leaderboardState;
  events = await import('../../static/js/leaderboard/leaderboard.events.js');
  state.reset();
  state.listGeneration = 0; state.stripGeneration = 0; state.lastLoadedAt = 0;
}

const entry = (rank, id, label, isYou = false) => ({
  rank, rank_change: null, is_you: isYou, score: 100 - rank,
  user: { id, name: `${label}-${id}`, username: `u${id}`, avatar: null, department: null },
  reputation: { total: 1, level: { name: 'Newbie', icon: '', color: '' } }, streaks: {},
});
const board = (label, n = 5, hasMore = false, ranks) => ({
  status: 'success',
  data: {
    leaderboard: Array.from({ length: n }, (_, i) => entry(ranks ? ranks[i] : i + 1, i + 1, label, i === 0)),
    period: label, pagination: { has_more: hasMore, total: 99, page: 1, limit: 20 }, your_position: { rank: 1 },
  },
});
const settle = (match, value) => pending.filter((p) => p.url.includes(match)).forEach((p) => p.resolve(value));
const tick = () => new Promise((r) => setTimeout(r, 5));
const names = () => [...document.querySelectorAll('#lb-list .lb-pod-name, #lb-list .lb-row-name')].map((n) => n.textContent.trim());

test('a period change made during the first load still refetches', { skip }, async () => {
  await setup();
  state.setPeriod('weekly');
  const first = events.loadLeaderboard();
  await tick();
  const second = events.loadLeaderboard();               // what setPeriod('monthly') triggers
  state.setPeriod('monthly');
  await tick();

  assert.equal(calls.filter((u) => u.includes('/leaderboard/global')).length, 2, 'second load must not be dropped');
  pending.filter((p) => p.url.includes('/leaderboard/global')).forEach((p) =>
    p.resolve(board(p.url.includes('monthly') ? 'monthly' : 'weekly')));
  await Promise.all([first, second]); await tick();
});

test('a slow response for an older request does not overwrite the newer one', { skip }, async () => {
  await setup();
  state.setPeriod('weekly');
  const first = events.loadLeaderboard(); await tick();
  state.setPeriod('monthly');
  const second = events.loadLeaderboard(); await tick();

  const [older, newer] = pending.filter((p) => p.url.includes('/leaderboard/global'));
  newer.resolve(board('monthly')); await second; await tick();
  older.resolve(board('weekly'));  await first;  await tick();            // arrives late

  assert.ok(names().every((n) => n.startsWith('monthly')), `list shows ${names()}`);
  assert.equal(state.isLoading(), false);
});

test('a failed "Load more" re-enables the button, restores the page and shows an error', { skip }, async () => {
  await setup();
  state.setPeriod('weekly');
  const first = events.loadLeaderboard(); await tick();
  pending[0].resolve(board('weekly', 20, true)); await first; await tick();
  assert.equal(state.page, 1);

  const more = events.loadMoreLeaderboard(); await tick();
  pending[1].reject(new Error('Network down')); await more; await tick();

  const btn = document.querySelector('[data-action="lb-load-more"]');
  assert.equal(btn.disabled, false);
  assert.notEqual(btn.textContent.trim(), 'Loading…');
  assert.equal(state.page, 1, 'page number is given back so a retry asks for page 2 again');
  assert.ok(document.querySelector('.lb-inline-error'));
});

test('boards with fewer than 3 entries do not hide any rows', { skip }, async () => {
  await setup();
  state.setPeriod('weekly');
  const load = events.loadLeaderboard(); await tick();
  settle('/leaderboard/global', board('weekly', 2)); await load; await tick();

  assert.equal(document.querySelectorAll('.lb-podium').length, 0);
  assert.equal(document.querySelectorAll('.lb-row').length, 2);
  assert.equal(document.querySelectorAll('.lb-row-in-podium').length, 0);
});

test('with tied ranks only the three podium entries are hidden from the list', { skip }, async () => {
  await setup();
  state.setPeriod('weekly');
  const load = events.loadLeaderboard(); await tick();
  settle('/leaderboard/global', board('weekly', 5, false, [1, 2, 2, 2, 5])); await load; await tick();

  assert.equal(document.querySelectorAll('.lb-podium').length, 1);
  assert.equal(document.querySelectorAll('.lb-row-in-podium').length, 3, 'exactly the podium entries');
  assert.equal(document.querySelectorAll('.lb-row').length, 5);
});
