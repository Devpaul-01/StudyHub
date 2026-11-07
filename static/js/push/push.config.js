/**
 * ============================================================================
 * PUSH CONFIG — push.config.js
 *
 * Fill these in with your actual Firebase project's values (Firebase
 * console → Project Settings → General → Your apps → SDK setup and
 * configuration) before push notifications can work. Keep this file and
 * static/firebase-messaging-sw.js's firebaseConfig in sync — the service
 * worker can't import this file, so its copy has to be kept up to date
 * by hand if these values ever change.
 *
 * VAPID_KEY comes from a different page: Project Settings → Cloud
 * Messaging → Web configuration → "Web Push certificates" → generate
 * one if you don't have one yet.
 * ============================================================================
 */

export const FIREBASE_CONFIG = {
  apiKey:            'YOUR_FIREBASE_API_KEY',
  authDomain:        'YOUR_PROJECT.firebaseapp.com',
  projectId:         'YOUR_PROJECT_ID',
  storageBucket:      'YOUR_PROJECT.appspot.com',
  messagingSenderId: 'YOUR_SENDER_ID',
  appId:              'YOUR_APP_ID',
};

export const VAPID_KEY = 'YOUR_VAPID_KEY';
