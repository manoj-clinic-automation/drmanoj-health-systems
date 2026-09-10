#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog PWA layer - manifest, service worker, icons.

Kept in its own module so app.py needs exactly one added line:

    from pwa import pwa_bp; app.register_blueprint(pwa_bp)

WHY THE SERVICE WORKER CACHES NOTHING
-------------------------------------
Chrome will not offer "Install app" without a registered service worker
carrying a fetch handler. That is the ONLY reason this one exists.

It deliberately does not cache. A cached medication log is a hazard: it
could show yesterday's dose state as though it were today's, and the
whole point of the dose card is telling you what you have and have not
taken. Decision D-QL3 ruled out offline support; this honours that.

Every request goes to the network. If the network fails on a navigation,
the user gets an explicit "offline" page - never stale data.

Python 3.9.25 compatible.
"""

import os

from flask import Blueprint, Response, send_from_directory

BASE = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.environ.get("GUTLOG_ICONS", BASE)

pwa_bp = Blueprint("pwa", __name__)

APP_NAME = "GutLog"
APP_DESC = "Personal health diary - meds, vitals, gut"
THEME = "#111821"
BG = "#111821"

MANIFEST = {
    "name": APP_NAME,
    "short_name": APP_NAME,
    "description": APP_DESC,
    "id": "/",
    "start_url": "/?src=pwa",
    "scope": "/",
    "display": "standalone",
    "orientation": "portrait-primary",
    "theme_color": THEME,
    "background_color": BG,
    "categories": ["health", "medical", "productivity"],
    "icons": [
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png",
         "purpose": "any"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png",
         "purpose": "any"},
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png",
         "purpose": "maskable"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png",
         "purpose": "maskable"},
    ],
    # Long-press the home-screen icon -> straight into a tile.
    # These deep-link into the single page route with an ?open= hint.
    "shortcuts": [
        {"name": "Log BP", "short_name": "BP",
         "description": "Record blood pressure and pulse",
         "url": "/?open=bp",
         "icons": [{"src": "/icon-192.png", "sizes": "192x192"}]},
        {"name": "Meds", "short_name": "Meds",
         "description": "Today's doses and extras",
         "url": "/?open=meds",
         "icons": [{"src": "/icon-192.png", "sizes": "192x192"}]},
        {"name": "Symptom", "short_name": "Symptom",
         "description": "Log an episode",
         "url": "/?open=sym",
         "icons": [{"src": "/icon-192.png", "sizes": "192x192"}]},
    ],
}

# Bump SW_VERSION to force every installed client to pick up a new worker.
SW_VERSION = "3.3.0"

SERVICE_WORKER = """/* GutLog service worker v%(ver)s
   Network-only by design. Caches nothing, ever.
   Present solely to satisfy PWA installability. */

self.addEventListener('install', function (e) {
  self.skipWaiting();
});

self.addEventListener('activate', function (e) {
  /* Clear anything a previous version may have cached. */
  e.waitUntil(
    caches.keys().then(function (names) {
      return Promise.all(names.map(function (n) { return caches.delete(n); }));
    }).then(function () { return self.clients.claim(); })
  );
});

var OFFLINE_HTML =
  '<!doctype html><html><head><meta charset="utf-8">' +
  '<meta name="viewport" content="width=device-width,initial-scale=1">' +
  '<title>Offline</title><style>' +
  'body{background:#111821;color:#e6edf3;font:16px/1.5 system-ui,sans-serif;' +
  'margin:0;display:flex;min-height:100vh;align-items:center;' +
  'justify-content:center;text-align:center;padding:24px}' +
  'p{max-width:22rem}b{color:#7ec8a8}' +
  '</style></head><body><p><b>No connection.</b><br>' +
  'GutLog does not store data on this device, so nothing can be shown ' +
  'or logged while offline. Reconnect and try again.</p></body></html>';

self.addEventListener('fetch', function (e) {
  /* Pass everything straight through. On a failed navigation, show an
     explicit offline page rather than anything stale. */
  if (e.request.mode === 'navigate') {
    e.respondWith(
      fetch(e.request).catch(function () {
        return new Response(OFFLINE_HTML, {
          status: 503,
          headers: { 'Content-Type': 'text/html; charset=utf-8' }
        });
      })
    );
    return;
  }
  e.respondWith(fetch(e.request));
});
""" % {"ver": SW_VERSION}

# Injected into the <head> of the main page. Registers the worker and
# points at the manifest.
PWA_HEAD_SNIPPET = """<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="%(theme)s">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="%(name)s">
<link rel="apple-touch-icon" href="/icon-192.png">
<link rel="icon" type="image/png" sizes="192x192" href="/icon-192.png">
<script>
if ('serviceWorker' in navigator) {
  window.addEventListener('load', function () {
    navigator.serviceWorker.register('/sw.js', { scope: '/' })
      .catch(function (e) { console.warn('sw registration failed', e); });
  });
}
</script>""" % {"theme": THEME, "name": APP_NAME}


@pwa_bp.route("/manifest.webmanifest")
def manifest():
    # Deliberately public: the manifest carries no personal data, and
    # Chrome may fetch it before the session cookie is attached.
    import json as _json
    return Response(
        _json.dumps(MANIFEST, indent=2),
        mimetype="application/manifest+json",
        headers={"Cache-Control": "no-cache"})


@pwa_bp.route("/sw.js")
def service_worker():
    return Response(
        SERVICE_WORKER,
        mimetype="application/javascript",
        headers={
            # Must not be cached, or clients pin to an old worker.
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Service-Worker-Allowed": "/",
        })


@pwa_bp.route("/icon-192.png")
def icon_192():
    return send_from_directory(ICON_DIR, "icon-192.png",
                               max_age=60 * 60 * 24 * 7)


@pwa_bp.route("/icon-512.png")
def icon_512():
    return send_from_directory(ICON_DIR, "icon-512.png",
                               max_age=60 * 60 * 24 * 7)
