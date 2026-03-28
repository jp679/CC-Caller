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

    @app.get("/call-config")
    async def call_config():
        with app.state.web_call_lock:
            web_call = app.state.pending_web_call
        if not web_call:
            return {"error": "no pending call"}
        return web_call

    @app.get("/call", response_class=HTMLResponse)
    async def web_call_page():
        with app.state.web_call_lock:
            web_call = app.state.pending_web_call
        if not web_call:
            return HTMLResponse("<h1>No pending call</h1>", status_code=404)

        import json
        assistant_json = json.dumps(web_call.get("assistantConfig", {}))
        public_key = web_call.get("publicKey", "")

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
  #end {{ background: #ef4444; color: white; display: none; }}
</style>
</head><body>
<h1>CC-Caller</h1>
<p id="status">Tap Connect to start the voice call</p>
<button id="connect">Connect</button>
<button id="end">End Call</button>
<div id="log" style="margin-top:20px;font-size:0.9em;opacity:0.7;max-width:90vw;text-align:center"></div>

<script>
  const ASSISTANT_CONFIG = {assistant_json};
  const PUBLIC_KEY = "{public_key}";
  let vapiInstance = null;

  // Load VAPI SDK
  var s = document.createElement('script');
  s.src = "https://cdn.jsdelivr.net/gh/VapiAI/html-script-tag@latest/dist/assets/index.js";
  s.defer = true;
  s.async = true;
  s.onload = function() {{
    log('SDK loaded. Tap Connect.');
  }};
  document.head.appendChild(s);

  document.getElementById('connect').onclick = function() {{
    document.getElementById('status').textContent = 'Connecting...';
    document.getElementById('status').className = 'pulse';
    document.getElementById('connect').style.display = 'none';

    try {{
      vapiInstance = window.vapiSDK.run({{
        apiKey: PUBLIC_KEY,
        assistant: ASSISTANT_CONFIG,
        config: {{ hide: true }}
      }});
      document.getElementById('status').textContent = 'Connected — speak now';
      document.getElementById('status').className = '';
      document.getElementById('end').style.display = 'inline-block';
    }} catch(e) {{
      log('Failed: ' + e.message);
      document.getElementById('status').textContent = 'Failed to connect';
      document.getElementById('connect').style.display = 'inline-block';
    }}
  }};

  document.getElementById('end').onclick = function() {{
    if (vapiInstance) vapiInstance.stop();
    document.getElementById('status').textContent = 'Call ended';
    document.getElementById('end').style.display = 'none';
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
