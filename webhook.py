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

    @app.get("/call", response_class=HTMLResponse)
    async def web_call_page():
        with app.state.web_call_lock:
            web_call = app.state.pending_web_call
        if not web_call:
            return HTMLResponse("<h1>No pending call</h1>", status_code=404)

        public_key = web_call.get("publicKey", "")
        web_call_url = web_call.get("webCallUrl", "")
        call_id = web_call.get("id", "")

        return HTMLResponse(f"""<!DOCTYPE html>
<html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CC-Caller</title>
<style>
  body {{ font-family: -apple-system, sans-serif; display: flex; flex-direction: column;
    align-items: center; justify-content: center; min-height: 100vh; margin: 0;
    background: #111; color: #fff; }}
  #status {{ font-size: 1.5em; margin: 20px; }}
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
<button id="connect" onclick="startCall()">Connect</button>
<button id="end" onclick="endCall()">End Call</button>
<div id="log" style="margin-top:20px;font-size:0.9em;opacity:0.7;max-width:90vw"></div>

<script src="https://cdn.jsdelivr.net/npm/@vapi-ai/web@2.5.2/dist/vapi.min.js"></script>
<script>
  let vapi;
  const WEB_CALL_URL = "{web_call_url}";
  const CALL_ID = "{call_id}";
  const PUBLIC_KEY = "{public_key}";

  async function startCall() {{
    document.getElementById('status').textContent = 'Connecting...';
    document.getElementById('status').className = 'pulse';
    document.getElementById('connect').style.display = 'none';

    try {{
      vapi = new window.Vapi(PUBLIC_KEY);

      vapi.on('call-start', () => {{
        document.getElementById('status').textContent = 'Connected — speak now';
        document.getElementById('status').className = '';
        document.getElementById('end').style.display = 'inline-block';
      }});

      vapi.on('call-end', () => {{
        document.getElementById('status').textContent = 'Call ended';
        document.getElementById('status').className = '';
        document.getElementById('end').style.display = 'none';
      }});

      vapi.on('message', (msg) => {{
        if (msg.type === 'transcript' && msg.transcriptType === 'final') {{
          log(msg.role + ': ' + msg.transcript);
        }}
      }});

      vapi.on('error', (e) => {{
        log('Error: ' + JSON.stringify(e));
        document.getElementById('status').textContent = 'Error — check log';
        document.getElementById('status').className = '';
      }});

      await vapi.reconnect({{ webCallUrl: WEB_CALL_URL, id: CALL_ID }});
    }} catch(e) {{
      log('Failed: ' + e.message);
      document.getElementById('status').textContent = 'Failed to connect';
      document.getElementById('connect').style.display = 'inline-block';
    }}
  }}

  function endCall() {{
    if (vapi) vapi.end();
  }}

  function log(msg) {{
    const el = document.getElementById('log');
    el.innerHTML = '<p>' + msg + '</p>' + el.innerHTML;
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
