import asyncio
import json as json_module
import pathlib
import queue
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response

STATIC_DIR = pathlib.Path(__file__).resolve().parents[1] / "static"


def create_app(transcript_queue: queue.Queue) -> FastAPI:
    app = FastAPI()

    # PWA state
    app.state.pwa_config = None  # {assistantConfig, publicKey, vapidPublicKey}
    app.state.push_subscriptions = []  # list of Web Push subscription dicts

    @app.get("/webhook")
    async def webhook_health():
        return {"status": "healthy"}

    @app.middleware("http")
    async def add_headers(request, call_next):
        response = await call_next(request)
        # Skip ngrok browser warning interstitial
        response.headers["ngrok-skip-browser-warning"] = "true"
        if request.url.path == "/pwa":
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    # --- VAPI tool-call endpoint for persistent SIP sessions ---
    app.state.tool_call_handler = None  # Set by cc_caller for --sip / --pwa

    @app.post("/tool-call")
    async def tool_call(request: Request):
        body = await request.json()
        message = body.get("message", {})

        if message.get("type") != "tool-calls":
            return {"status": "ignored"}

        tool_calls = message.get("toolCallList", [])
        results = []

        for tc in tool_calls:
            fn = tc.get("function", {})
            tool_call_id = tc.get("id", "")
            fn_name = fn.get("name", "")
            fn_args = fn.get("arguments", {})

            print(f"[tool-call] {fn_name}({json_module.dumps(fn_args)[:100]})")

            if fn_name == "askCodingAgent" and app.state.tool_call_handler:
                task = fn_args.get("task", "")
                # Run in a thread pool so webhook events aren't blocked
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, app.state.tool_call_handler, task)
                results.append({"toolCallId": tool_call_id, "result": result})
            else:
                results.append({"toolCallId": tool_call_id, "result": "Tool not available."})

        return {"results": results}

    # --- PWA endpoints ---

    @app.get("/sw.js")
    async def service_worker():
        sw_path = STATIC_DIR / "sw.js"
        content = sw_path.read_text()
        return Response(content, media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/"})

    @app.get("/static/manifest.json")
    async def manifest():
        mf_path = STATIC_DIR / "manifest.json"
        content = mf_path.read_text()
        return Response(content, media_type="application/manifest+json")

    @app.get("/pwa-config")
    async def pwa_config():
        if not app.state.pwa_config:
            return {"ready": False}
        return {**app.state.pwa_config, "ready": True}

    @app.post("/push-subscribe")
    async def push_subscribe(request: Request):
        sub = await request.json()
        # Deduplicate by endpoint
        existing = [s for s in app.state.push_subscriptions if s.get("endpoint") == sub.get("endpoint")]
        if not existing:
            app.state.push_subscriptions.append(sub)
            print(f"[push] Subscription added ({len(app.state.push_subscriptions)} total)")
        return {"status": "ok"}

    @app.get("/pwa", response_class=HTMLResponse)
    async def pwa_page():
        return HTMLResponse("""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#22c55e">
<link rel="manifest" href="/static/manifest.json">
<title>CC-Caller</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, sans-serif;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    min-height: 100vh; background: #0a0a0a; color: #fff; padding: 20px;
  }
  h1 { font-size: 1.8em; margin-bottom: 4px; }
  .sub { font-size: 0.7em; opacity: 0.4; }
  #status { font-size: 1.2em; margin: 15px 0; text-align: center; opacity: 0.8; }
  .pulse { animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
  .controls { display: flex; gap: 12px; margin: 16px 0; align-items: center; }
  button {
    padding: 16px 32px; font-size: 1.1em; border: none; border-radius: 50px;
    cursor: pointer; font-weight: 600; transition: all 0.2s;
  }
  #connect { background: #22c55e; color: white; }
  #connect:disabled { background: #333; color: #666; cursor: not-allowed; }
  #end { background: #ef4444; color: white; display: none; }
  #mic {
    width: 64px; height: 64px; border-radius: 50%;
    font-size: 1.5em; display: none; background: #22c55e; color: #fff;
    align-items: center; justify-content: center;
  }
  #mic.muted { background: #555; }
  #transcript {
    width: 90vw; max-width: 500px; max-height: 40vh;
    overflow-y: auto; margin-top: 16px; font-size: 0.85em; opacity: 0.7;
  }
  #transcript p { margin: 4px 0; }
  #push-status { font-size: 0.75em; opacity: 0.4; margin-top: 8px; }
  #vapi-support-btn, .vapi-btn { display: none !important; }
</style>
</head><body>
<h1>CC-Caller <span class="sub">PWA</span></h1>
<p id="status">Initializing...</p>
<div class="controls">
  <button id="connect" disabled>Connect</button>
  <button id="mic">&#x1f399;</button>
  <button id="end">End</button>
</div>
<div id="transcript"></div>
<button id="notify-btn" style="display:none; background:#555; color:#fff; padding:10px 20px; font-size:0.9em; border:none; border-radius:8px; cursor:pointer; margin-top:8px;">Enable Notifications</button>
<p id="push-status"></p>

<script type="module">
  import VapiModule from "https://cdn.jsdelivr.net/npm/@vapi-ai/web@2.5.2/+esm";
  const Vapi = VapiModule.default || VapiModule;

  let assistantConfig = null;
  let publicKey = null;
  let vapi = null;
  let wakeLock = null;

  // --- Push notifications ---
  let savedVapidKey = null;

  async function initPush(vapidPublicKey) {
    savedVapidKey = vapidPublicKey;
    const ps = document.getElementById('push-status');
    const btn = document.getElementById('notify-btn');

    if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
      ps.textContent = 'Push not supported (install PWA to home screen first)';
      return;
    }

    await navigator.serviceWorker.register('/sw.js', { scope: '/' });
    await navigator.serviceWorker.ready;

    const current = Notification.permission;
    if (current === 'granted') {
      await subscribePush(vapidPublicKey);
    } else if (current === 'denied') {
      ps.textContent = 'Notifications denied \u2014 delete PWA, re-add, tap Allow';
    } else {
      // Show button — iOS requires user gesture for permission
      btn.style.display = 'inline-block';
      btn.onclick = async () => {
        const perm = await Notification.requestPermission();
        if (perm === 'granted') {
          btn.style.display = 'none';
          await subscribePush(vapidPublicKey);
        } else {
          ps.textContent = 'Permission ' + perm + ' \u2014 try again';
        }
      };
      ps.textContent = 'Tap button above to enable notifications';
    }
  }

  async function subscribePush(vapidPublicKey) {
    const ps = document.getElementById('push-status');
    try {
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(vapidPublicKey)
      });

      await fetch('/push-subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(sub.toJSON())
      });

      ps.textContent = 'Push notifications active';
    } catch (e) {
      console.error('Push subscribe failed:', e);
      ps.textContent = 'Push failed: ' + e.message;
    }
  }

  function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(base64);
    return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
  }

  // --- Wake Lock ---
  async function requestWakeLock() {
    try {
      if ('wakeLock' in navigator) {
        wakeLock = await navigator.wakeLock.request('screen');
        wakeLock.addEventListener('release', () => { wakeLock = null; });
      }
    } catch (e) {}
  }
  function releaseWakeLock() {
    if (wakeLock) { wakeLock.release(); wakeLock = null; }
  }

  // --- Fetch config from server ---
  async function fetchConfig() {
    try {
      const resp = await fetch('/pwa-config');
      const data = await resp.json();
      if (data.ready) {
        assistantConfig = data.assistantConfig;
        publicKey = data.publicKey;
        if (data.vapidPublicKey) initPush(data.vapidPublicKey);
        return true;
      }
    } catch (e) {}
    return false;
  }

  // --- VAPI call ---
  function cleanupVapi() {
    if (vapi) {
      try { vapi.stop(); } catch(e) {}
      vapi = null;
    }
    // Remove stale Daily.co iframes left behind
    document.querySelectorAll('iframe[allow*="microphone"]').forEach(f => f.remove());
  }

  function createVapi() {
    cleanupVapi();
    vapi = new Vapi(publicKey);

    vapi.on('call-start', () => {
      document.getElementById('status').textContent = 'Connected \u2014 speak now';
      document.getElementById('status').className = '';
      document.getElementById('end').style.display = 'inline-block';
      document.getElementById('mic').style.display = 'inline-flex';
      muted = false;
      document.getElementById('mic').className = '';
      document.getElementById('mic').innerHTML = '&#x1f399;';
      requestWakeLock();
    });

    vapi.on('call-end', () => {
      document.getElementById('status').textContent = 'Call ended \u2014 tap Connect';
      document.getElementById('status').className = '';
      document.getElementById('end').style.display = 'none';
      document.getElementById('mic').style.display = 'none';
      document.getElementById('connect').style.display = 'inline-block';
      document.getElementById('connect').disabled = false;
      releaseWakeLock();
    });

    vapi.on('message', (msg) => {
      if (msg.type === 'transcript' && msg.transcriptType === 'final') {
        log((msg.role === 'user' ? 'You' : 'Agent') + ': ' + msg.transcript);
      }
    });

    vapi.on('error', (e) => {
      // "Meeting has ended" / "ejected" is normal call cleanup, not a real error
      const msg = JSON.stringify(e);
      if (msg.includes('Meeting has ended') || msg.includes('ejected')) return;
      log('Error: ' + (e.message || msg));
    });

    return vapi;
  }

  async function startCall() {
    await fetchConfig();
    if (!assistantConfig || !publicKey) {
      log('No config available yet');
      return;
    }

    document.getElementById('status').textContent = 'Connecting...';
    document.getElementById('status').className = 'pulse';
    document.getElementById('connect').style.display = 'none';

    try {
      createVapi();
      await vapi.start(assistantConfig);
    } catch (e) {
      log('Failed: ' + e.message);
      document.getElementById('status').textContent = 'Failed \u2014 tap Connect to retry';
      document.getElementById('connect').style.display = 'inline-block';
      document.getElementById('connect').disabled = false;
    }
  }

  // Clean up stale state when PWA resumes from background
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && vapi) {
      // If we had a call but page was suspended, the connection is dead
      cleanupVapi();
      document.getElementById('status').textContent = 'Reconnect \u2014 tap Connect';
      document.getElementById('end').style.display = 'none';
      document.getElementById('mic').style.display = 'none';
      document.getElementById('connect').style.display = 'inline-block';
      document.getElementById('connect').disabled = false;
    }
  });

  // --- UI handlers ---
  document.getElementById('connect').onclick = startCall;
  document.getElementById('end').onclick = () => { cleanupVapi(); };

  let muted = false;
  document.getElementById('mic').onclick = () => {
    muted = !muted;
    if (vapi) vapi.setMuted(muted);
    const btn = document.getElementById('mic');
    btn.className = muted ? 'muted' : '';
    btn.innerHTML = muted ? '&#x1f507;' : '&#x1f399;';
  };

  function log(msg) {
    const el = document.getElementById('transcript');
    el.innerHTML = '<p>' + msg + '</p>' + el.innerHTML;
  }

  // --- Init ---
  async function init() {
    const ready = await fetchConfig();
    if (ready) {
      document.getElementById('connect').disabled = false;
      document.getElementById('status').textContent = 'Ready \u2014 tap Connect';

      // Auto-connect if this is a callback from push notification
      if (new URLSearchParams(location.search).has('callback')) {
        startCall();
      }
    } else {
      document.getElementById('status').textContent = 'Waiting for server...';
      const poll = setInterval(async () => {
        if (await fetchConfig()) {
          clearInterval(poll);
          document.getElementById('connect').disabled = false;
          document.getElementById('status').textContent = 'Ready \u2014 tap Connect';
        }
      }, 3000);
    }
  }

  init();
</script>
</body></html>""")

    app.state.on_webhook_event = None  # Set by cc_caller for hybrid mode

    @app.post("/webhook")
    async def webhook(request: Request):
        body = await request.json()
        event_type = body.get('message', {}).get('type', 'unknown')
        print(f"[webhook] Received event: {event_type}")
        message = body.get("message", {})

        # Notify hybrid mode of call lifecycle events
        if app.state.on_webhook_event:
            app.state.on_webhook_event(event_type)

        if message.get("type") != "end-of-call-report":
            return {"status": "ignored"}

        artifact = message.get("artifact", {})
        messages = artifact.get("messages", [])

        user_messages = [
            msg["message"] for msg in messages
            if msg.get("role") == "user"
        ]

        if user_messages:
            transcript = " ".join(user_messages)
            transcript_queue.put(transcript)

        return {"status": "ok"}

    return app
