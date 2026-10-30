// 고티처 서비스 워커(G2): 웹 푸시 묶음 알림만 띄운다. 요청 가로채기·캐시는 하지 않는다.
// 서버가 보내는 것은 {title, body, url} — 모으기 제목·건수만.
self.addEventListener('install', function () { self.skipWaiting(); });

self.addEventListener('push', function (e) {
  var d = {};
  try { d = e.data ? e.data.json() : {}; } catch (x) { d = {}; }
  e.waitUntil(self.registration.showNotification(d.title || '고티처', {
    body: d.body || '안 읽은 알림이 있어요.',
    tag: 'goteacher-digest',  // 묶음은 한 장만 남긴다
    data: { url: d.url || '/' }
  }));
});

self.addEventListener('notificationclick', function (e) {
  e.notification.close();
  var url = new URL((e.notification.data && e.notification.data.url) || '/', self.location.origin);
  if (url.origin !== self.location.origin) url = new URL('/', self.location.origin);
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
    for (var i = 0; i < list.length; i++) if (list[i].url === url.href && 'focus' in list[i]) return list[i].focus();
    return self.clients.openWindow(url.href);
  }));
});
