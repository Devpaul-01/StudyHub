/**
 * ============================================================================
 * FIREBASE MESSAGING SERVICE WORKER — firebase-messaging-sw.js
 *
 * Batch 5: shows push notifications when the tab is closed or backgrounded.
 * Served from the site root via a dedicated Flask route — see
 * app.py's firebase_messaging_sw() for why (it needs root scope, which
 * the default /static/... path doesn't give it).
 *
 * Service workers can't use the same ES-module `import` syntax a normal
 * page script can — Firebase's own setup instructions specifically call
 * for the "compat" build loaded via importScripts() here, even though
 * the rest of this app's frontend uses the modern modular SDK pattern
 * elsewhere (see static/js/core/socket.js for that pattern). This file
 * is the one deliberate exception, because the browser requires it.
 *
 * REQUIRED BEFORE THIS WORKS: fill in firebaseConfig below with your
 * actual Firebase project's config (find it in the Firebase console:
 * Project Settings → General → Your apps → SDK setup and configuration).
 * These are the same values as in static/js/push/push.config.js — keep
 * the two in sync, since this file can't import that one.
 * ============================================================================
 */

importScripts('https://www.gstatic.com/firebasejs/10.12.2/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/10.12.2/firebase-messaging-compat.js');

firebase.initializeApp({
  apiKey:            'YOUR_FIREBASE_API_KEY',
  authDomain:        'YOUR_PROJECT.firebaseapp.com',
  projectId:         'YOUR_PROJECT_ID',
  storageBucket:      'YOUR_PROJECT.appspot.com',
  messagingSenderId: 'YOUR_SENDER_ID',
  appId:              'YOUR_APP_ID',
});

const messaging = firebase.messaging();

// Shown when a push arrives while the tab is closed/backgrounded — a
// foreground push (tab open and focused) is instead handled inline by
// push.js's onMessage listener, so the two don't double-show the same
// notification.
messaging.onBackgroundMessage((payload) => {
  const title = payload.notification?.title || payload.data?.title || 'StudyHub';
  const options = {
    body: payload.notification?.body || payload.data?.body || '',
    icon: '/static/default-avatar.png',
    data: { link: payload.data?.link || '/student/profile/homepage' },
  };
  self.registration.showNotification(title, options);
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const link = event.notification.data?.link || '/student/profile/homepage';
  event.waitUntil(clients.openWindow(link));
});
