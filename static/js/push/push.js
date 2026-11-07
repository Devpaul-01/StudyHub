/**
 * ============================================================================
 * PUSH NOTIFICATIONS — push.js
 *
 * Batch 5: the frontend half of push notifications. The backend side
 * (User.fcm_token, PushNotificationService, and every existing call to
 * send a push) already existed; this is what actually requests
 * permission, registers with Firebase, and saves the resulting token
 * via the new POST /profile/fcm-token route (routes/student/profile.py).
 *
 * Uses the global `api` client from core/api.js, the same convention
 * every other feature module in this app follows.
 *
 * This file only loads the Firebase SDK and attempts anything once the
 * toggle is actually switched on — not on every page load — so an
 * unconfigured deployment (placeholder values still in push.config.js)
 * costs nothing for users who never touch the toggle.
 * ============================================================================
 */

import { FIREBASE_CONFIG, VAPID_KEY } from './push.config.js';

const STATUS_EL_ID = 'push-notification-status';
const TOGGLE_EL_ID  = 'toggle-push-notification';

function setStatus(text) {
  const el = document.getElementById(STATUS_EL_ID);
  if (el) el.textContent = text;
}

function isConfigured() {
  return !FIREBASE_CONFIG.apiKey.startsWith('YOUR_') && !VAPID_KEY.startsWith('YOUR_');
}

let firebaseAppPromise = null;

async function getMessaging() {
  if (!firebaseAppPromise) {
    firebaseAppPromise = (async () => {
      const { initializeApp } = await import('https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js');
      const { getMessaging, getToken, deleteToken } =
        await import('https://www.gstatic.com/firebasejs/10.12.2/firebase-messaging.js');
      const app = initializeApp(FIREBASE_CONFIG);
      const messaging = getMessaging(app);
      return { messaging, getToken, deleteToken };
    })();
  }
  return firebaseAppPromise;
}

async function enablePush() {
  if (!isConfigured()) {
    setStatus('Push notifications aren\u2019t set up for this deployment yet.');
    return false;
  }
  if (!('Notification' in window) || !('serviceWorker' in navigator)) {
    setStatus('Push notifications aren\u2019t supported in this browser.');
    return false;
  }

  const permission = await Notification.requestPermission();
  if (permission !== 'granted') {
    setStatus('Notification permission was not granted.');
    return false;
  }

  try {
    const registration = await navigator.serviceWorker.register('/firebase-messaging-sw.js');
    const { messaging, getToken } = await getMessaging();
    const token = await getToken(messaging, { vapidKey: VAPID_KEY, serviceWorkerRegistration: registration });

    if (!token) {
      setStatus('Could not get a push token. Please try again.');
      return false;
    }

    const response = await api.post('/profile/fcm-token', { token });
    if (response?.status !== 'success') {
      setStatus(response?.message || 'Could not save push token.');
      return false;
    }

    setStatus('Push notifications are on for this device.');
    return true;
  } catch (error) {
    console.error('Enable push error:', error);
    setStatus('Something went wrong enabling push notifications.');
    return false;
  }
}

async function disablePush() {
  try {
    const firebaseState = firebaseAppPromise ? await firebaseAppPromise : null;
    if (firebaseState) {
      try {
        await firebaseState.deleteToken(firebaseState.messaging);
      } catch (e) {
        // Non-fatal — still clear the server-side token below even if
        // the client-side unregister fails (e.g. permission already
        // revoked by the browser).
        console.warn('Firebase deleteToken failed (continuing):', e);
      }
    }
    await api.delete('/profile/fcm-token');
    setStatus('Push notifications are off for this device.');
  } catch (error) {
    console.error('Disable push error:', error);
    setStatus('Could not fully disable push notifications.');
  }
}

function setupToggle() {
  const toggle = document.getElementById(TOGGLE_EL_ID);
  if (!toggle) return;

  // Reflect the browser's actual current permission state on load,
  // rather than assuming "off" — if the user already granted permission
  // in a previous session (and we have a live registration), show the
  // toggle as on.
  if ('Notification' in window && Notification.permission === 'granted') {
    toggle.checked = true;
    setStatus('Push notifications are on for this device.');
  } else if ('Notification' in window && Notification.permission === 'denied') {
    toggle.disabled = true;
    setStatus('Notifications are blocked for this site in your browser settings.');
  }

  toggle.addEventListener('change', async () => {
    toggle.disabled = true;
    if (toggle.checked) {
      const ok = await enablePush();
      if (!ok) toggle.checked = false;
    } else {
      await disablePush();
    }
    toggle.disabled = false;
  });
}

document.addEventListener('DOMContentLoaded', setupToggle);
