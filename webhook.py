import queue
import threading
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse


def create_app(transcript_queue: queue.Queue) -> FastAPI:
    app = FastAPI()
    app.state.pending_web_call = None
    app.state.web_call_lock = threading.Lock()

    @app.get("/webhook")
    async def webhook_health():
        return {"status": "healthy"}

    @app.middleware("http")
    async def no_cache(request, call_next):
        response = await call_next(request)
        if request.url.path == "/call":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/call-config")
    async def call_config():
        with app.state.web_call_lock:
            web_call = app.state.pending_web_call
        if not web_call:
            return {"ready": False}
        return {**web_call, "ready": True}

    @app.get("/call", response_class=HTMLResponse)
    async def web_call_page():
        with app.state.web_call_lock:
            web_call = app.state.pending_web_call

        import json
        assistant_json = json.dumps(web_call.get("assistantConfig", {})) if web_call else "null"
        public_key = web_call.get("publicKey", "") if web_call else ""

        return HTMLResponse(f"""<!DOCTYPE html>
<html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CC-Caller</title>
<style>
  body {{ font-family: -apple-system, sans-serif; display: flex; flex-direction: column;
    align-items: center; justify-content: center; min-height: 100vh; margin: 0;
    background: #111; color: #fff; }}
  #status {{ font-size: 1.5em; margin: 20px; text-align: center; }}
  .pulse {{ animation: pulse 2s infinite; }}
  @keyframes pulse {{ 0%,100% {{ opacity:1 }} 50% {{ opacity:0.5 }} }}
  button {{ padding: 16px 32px; font-size: 1.2em; border: none; border-radius: 12px;
    cursor: pointer; margin: 10px; }}
  #connect {{ background: #22c55e; color: white; }}
  #connect:disabled {{ background: #555; cursor: not-allowed; }}
  #end {{ background: #ef4444; color: white; display: none; }}
  #banner {{ display: none; background: #22c55e; color: white; padding: 12px 24px;
    border-radius: 12px; margin: 10px; font-size: 1.1em; animation: pulse 2s infinite; }}
  #vapi-support-btn, .vapi-btn {{ display: none !important; }}
</style>
</head><body>
<h1>CC-Caller</h1>
<div id="banner">Update ready — tap Connect</div>
<p id="status">Waiting for Claude to finish...</p>
<button id="connect" disabled>Connect</button>
<button id="end">End Call</button>
<div id="log" style="margin-top:20px;font-size:0.9em;opacity:0.7;max-width:90vw;text-align:center"></div>

<script type="module">
  const ASSISTANT_CONFIG = {assistant_json};
  const PUBLIC_KEY = "{public_key}";

  import VapiModule from "https://cdn.jsdelivr.net/npm/@vapi-ai/web@2.5.2/+esm";
  const Vapi = VapiModule.default || VapiModule;

  let vapi = null;
  let callReady = {assistant_json} !== null;

  // Poll for updates every 3 seconds
  setInterval(async () => {{
    if (vapi) return; // don't poll during a call
    try {{
      const resp = await fetch('/call-config');
      if (resp.ok) {{
        const data = await resp.json();
        if (data.ready) {{
          callReady = true;
          document.getElementById('banner').style.display = 'block';
          document.getElementById('connect').disabled = false;
          document.getElementById('status').textContent = 'Update ready — tap Connect';
        }} else {{
          callReady = false;
          document.getElementById('banner').style.display = 'none';
          document.getElementById('connect').disabled = true;
          document.getElementById('status').textContent = 'Waiting for Claude to finish...';
        }}
      }}
    }} catch(e) {{}}
  }}, 3000);

  // Set initial state
  if (callReady) {{
    document.getElementById('banner').style.display = 'block';
    document.getElementById('connect').disabled = false;
    document.getElementById('status').textContent = 'Update ready — tap Connect';
  }}

  async function freshCall() {{
    let config = null;
    try {{
      const resp = await fetch('/call-config');
      if (resp.ok) {{
        const data = await resp.json();
        if (data.ready && data.assistantConfig) config = data.assistantConfig;
      }}
    }} catch(e) {{}}
    if (!config) config = ASSISTANT_CONFIG;

    // Destroy previous instance if any
    if (vapi) {{
      try {{ vapi.stop(); }} catch(e) {{}}
      vapi = null;
    }}

    vapi = new Vapi(PUBLIC_KEY);

    vapi.on('call-start', () => {{
      document.getElementById('status').textContent = 'Connected — speak now';
      document.getElementById('status').className = '';
      document.getElementById('end').style.display = 'inline-block';
    }});

    vapi.on('call-end', () => {{
      document.getElementById('status').textContent = 'Call ended — tap Connect for a new call';
      document.getElementById('status').className = '';
      document.getElementById('end').style.display = 'none';
      document.getElementById('connect').style.display = 'inline-block';
      vapi = null;
    }});

    vapi.on('message', (msg) => {{
      if (msg.type === 'transcript' && msg.transcriptType === 'final') {{
        log(msg.role + ': ' + msg.transcript);
      }}
    }});

    vapi.on('error', (e) => {{
      log('Error: ' + JSON.stringify(e));
    }});

    await vapi.start(config);
  }}

  document.getElementById('connect').onclick = async function() {{
    document.getElementById('status').textContent = 'Connecting...';
    document.getElementById('status').className = 'pulse';
    document.getElementById('connect').style.display = 'none';
    document.getElementById('banner').style.display = 'none';

    try {{
      await freshCall();
    }} catch(e) {{
      log('Failed: ' + e.message);
      document.getElementById('status').textContent = 'Failed to connect';
      document.getElementById('status').className = '';
      document.getElementById('connect').style.display = 'inline-block';
    }}
  }};

  document.getElementById('end').onclick = function() {{
    if (vapi) vapi.stop();
  }};

  function log(msg) {{
    document.getElementById('log').innerHTML = '<p>' + msg + '</p>' + document.getElementById('log').innerHTML;
  }}
</script>
</body></html>""")

    @app.post("/webhook")
    async def webhook(request: Request):
        body = await request.json()
        print(f"[webhook] Received event: {body.get('message', {}).get('type', 'unknown')}")
        message = body.get("message", {})

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
