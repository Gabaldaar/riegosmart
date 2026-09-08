// firebase-messaging-sw.js - Service Worker Unificado para PWA y Notificaciones Push FCM
importScripts('https://www.gstatic.com/firebasejs/10.7.1/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/10.7.1/firebase-messaging-compat.js');

const CACHE_NAME = 'riego-pwa-v71';
const ASSETS = [
  './index.html',
  './styles.css?v=71',
  './comms.js?v=71',
  './app.js?v=71',
  './manifest.json',
  './favicon.ico',
  './icon-512.png'
];

// === 1. CICLO DE VIDA DEL SERVICE WORKER (CACHE OFFLINE) ===
self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS);
    })
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cacheName) => {
          if (cacheName !== CACHE_NAME) {
            console.log('[SW] Eliminando caché antigua:', cacheName);
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  // Ignorar peticiones externas (Open-Meteo, Firestore, Google APIs, etc.)
  if (event.request.method !== 'GET' || !event.request.url.startsWith(self.location.origin)) {
    return;
  }

  // Estrategia Network-First para index.html para tener siempre la última versión
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

  // Cache-First con fallback a red para recursos estáticos
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
      .catch(() => caches.match(event.request))
  );
});

// === 2. FIREBASE CLOUD MESSAGING (PUSH EN SEGUNDO PLANO) ===
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
  console.log('[SW] Push recibido con la App en segundo plano o cerrada:', payload);

  const title = (payload.notification && payload.notification.title) || 
                (payload.data && payload.data.title) || 
                'Smart Riego';
                
  const body = (payload.notification && payload.notification.body) || 
               (payload.data && payload.data.body) || 
               'Notificación del sistema de riego.';

  const notificationOptions = {
    body: body,
    icon: '/icon-512.png',
    badge: '/favicon.ico',
    tag: (payload.data && payload.data.eventType) || 'riego-notification',
    data: payload.data || { url: '/' },
    vibrate: [200, 100, 200],
    requireInteraction: false
  };

  self.registration.showNotification(title, notificationOptions);
});

// === 3. ACCIÓN AL TOCAR LA NOTIFICACIÓN ===
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) ? event.notification.data.url : '/';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
      // Si ya hay una ventana abierta de la app, traerla al frente
      for (let client of windowClients) {
        if ('focus' in client) {
          return client.focus();
        }
      }
      // Si está cerrada, abrirla
      if (clients.openWindow) {
        return clients.openWindow(targetUrl);
      }
    })
  );
});
