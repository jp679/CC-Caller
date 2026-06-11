// CC-Caller Service Worker — push notifications + offline shell

self.addEventListener('push', function(event) {
  const data = event.data ? event.data.json() : {};
  event.waitUntil(
    self.registration.showNotification(data.title || 'CC-Caller', {
      body: data.body || 'Result ready',
      data: { url: data.url || '/?callback=1' },
      requireInteraction: true,
      tag: 'cc-caller-result',
    })
  );
});

self.addEventListener('notificationclick', function(event) {
  event.notification.close();
  const url = event.notification.data && event.notification.data.url
    ? event.notification.data.url
    : '/?callback=1';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function(list) {
      // Focus existing PWA window if open
      for (var i = 0; i < list.length; i++) {
        var client = list[i];
        if (client.url.indexOf(self.registration.scope) === 0 && 'focus' in client) {
          return client.focus().then(function(c) { return c.navigate(url); });
        }
      }
      // Otherwise open new window
      return clients.openWindow(url);
    })
  );
});
