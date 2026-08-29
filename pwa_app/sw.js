// sw.js - Service Worker Unificado para PWA y Notificaciones Push FCM
importScripts('https://www.gstatic.com/firebasejs/10.7.1/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/10.7.1/firebase-messaging-compat.js');

const CACHE_NAME = 'riego-pwa-v56';
const ASSETS = [
  './',
  './index.html',
  './styles.css?v=56',
  './comms.js?v=56',
  './app.js?v=56',
  './manifest.json',
  './favicon.ico',
  './icon-512.png?v=56'
];

// === 1. CACHÉ FUERA DE LÍNEA PWA ===
self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS);
    })
  );
});

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cacheName) => {
          if (cacheName !== CACHE_NAME) {
            console.log('[Service Worker] Eliminando caché obsoleta:', cacheName);
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET' || !event.request.url.startsWith(self.location.origin)) {
    return;
  }

  // Network-First para index.html para asegurar siempre la versión más reciente
  if (event.request.mode === 'navigate' || event.request.url.includes('index.html')) {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          if (response && response.status === 200) {
            const responseToCache = response.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, responseToCache);
            });
          }
          return response;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response && response.status === 200 && response.type === 'basic') {
          const responseToCache = response.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, responseToCache);
          });
        }
        return response;
      })
      .catch(() => {
        return caches.match(event.request);
      })
  );
});

// === 2. FIREBASE CLOUD MESSAGING (PUSH EN 2DO PLANO / APP CERRADA) ===
firebase.initializeApp({
  apiKey: "AIzaSyDOXu7MTGkr0In2NluAggFejTW7Ukap604",
  authDomain: "riego-smart-b8487.firebaseapp.com",
  projectId: "riego-smart-b8487",
  storageBucket: "riego-smart-b8487.firebasestorage.app",
  messagingSenderId: "127532086869",
  appId: "1:127532086869:web:b3a1a142e9fd4f9dc186dc"
});

const messaging = firebase.messaging();

messaging.onBackgroundMessage((payload) => {
  console.log('[SW] Push recibido con la App cerrada:', payload);

  const title = (payload.notification && payload.notification.title) ||
                (payload.data && payload.data.title) ||
                'Control de Riego Smart';

  const body = (payload.notification && payload.notification.body) ||
               (payload.data && payload.data.body) ||
               '';

  const notificationOptions = {
    body: body,
    icon: (payload.notification && payload.notification.icon) || './icon-512.png',
    badge: './favicon.ico',
    tag: (payload.data && payload.data.tag) || 'riego-smart-alert',
    data: payload.data || {},
    vibrate: [200, 100, 200],
    requireInteraction: true
  };

  return self.registration.showNotification(title, notificationOptions);
});

// === 3. ACCIÓN AL TOCAR LA NOTIFICACIÓN ===
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) ? event.notification.data.url : '/';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
      for (let client of windowClients) {
        if ('focus' in client) {
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow(targetUrl);
      }
    })
  );
});
