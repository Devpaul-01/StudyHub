// Live-notification helpers: duplicate suppression and the chime (audit NOTIF-16 / NOTIF-20).
import test from 'node:test';
import assert from 'node:assert/strict';

const { createSeenTracker, playNotificationSound } =
  await import('../../static/js/notification/notification.utils.js');

test('the same id is only handled once', () => {
  const seen = createSeenTracker();
  assert.equal(seen.markIfNew(5), true);
  assert.equal(seen.markIfNew(5), false);
  assert.equal(seen.markIfNew(6), true);
});

test('the tracker is bounded and forgets the oldest ids', () => {
  const seen = createSeenTracker(3);
  [1, 2, 3, 4].forEach((id) => seen.markIfNew(id));
  assert.equal(seen.markIfNew(1), true, 'oldest id was evicted');
  assert.equal(seen.markIfNew(4), false, 'recent id still remembered');
});

test('playNotificationSound never throws without Web Audio', () => {
  delete globalThis.AudioContext;
  delete globalThis.webkitAudioContext;
  assert.doesNotThrow(() => playNotificationSound());
});

test('playNotificationSound plays a tone when Web Audio exists', () => {
  const calls = { started: 0, stopped: 0 };
  class FakeAudioContext {
    constructor() { this.currentTime = 0; this.state = 'running'; this.destination = {}; }
    createOscillator() {
      return { type: '', frequency: { setValueAtTime() {} }, connect() {},
               start() { calls.started += 1; }, stop() { calls.stopped += 1; } };
    }
    createGain() {
      return { gain: { setValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {} };
    }
  }
  globalThis.AudioContext = FakeAudioContext;
  playNotificationSound();
  assert.deepEqual(calls, { started: 1, stopped: 1 });
});

test('a failing audio device is swallowed', () => {
  globalThis.AudioContext = class { constructor() { throw new Error('no device'); } };
  assert.doesNotThrow(() => playNotificationSound());
});
