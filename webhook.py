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

    app.state.pending_gemini_call = None

    @app.middleware("http")
    async def no_cache(request, call_next):
        response = await call_next(request)
        if request.url.path in ("/call", "/call-gemini"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/gemini-transcript")
    async def gemini_transcript(request: Request):
        body = await request.json()
        user_messages = body.get("userMessages", [])
        if user_messages:
            transcript = " ".join(user_messages)
            print(f"[gemini] Transcript received: {transcript[:100]}...")
            transcript_queue.put(transcript)
        return {"status": "ok"}

    @app.get("/gemini-config")
    async def gemini_config():
        with app.state.web_call_lock:
            gc = app.state.pending_gemini_call
        if not gc:
            return {"ready": False}
        return {**gc, "ready": True}

    @app.get("/call-gemini", response_class=HTMLResponse)
    async def gemini_call_page():
        with app.state.web_call_lock:
            gc = app.state.pending_gemini_call

        system_prompt = gc.get("systemPrompt", "") if gc else ""
        gemini_key = gc.get("geminiKey", "") if gc else ""
        model = gc.get("model", "gemini-3.1-flash-live-preview")

        import json
        system_prompt_json = json.dumps(system_prompt)

        return HTMLResponse(f"""<!DOCTYPE html>
<html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CC-Caller (Gemini)</title>
<style>
  body {{ font-family: -apple-system, sans-serif; display: flex; flex-direction: column;
    align-items: center; justify-content: center; min-height: 100vh; margin: 0;
    background: #0a0a0a; color: #fff; }}
  #status {{ font-size: 1.5em; margin: 20px; text-align: center; }}
  .pulse {{ animation: pulse 2s infinite; }}
  @keyframes pulse {{ 0%,100%{{ opacity:1 }} 50%{{ opacity:0.5 }} }}
  button {{ padding: 16px 32px; font-size: 1.2em; border: none; border-radius: 12px;
    cursor: pointer; margin: 10px; }}
  #connect {{ background: #4285f4; color: white; }}
  #connect:disabled {{ background: #555; cursor: not-allowed; }}
  #end {{ background: #ef4444; color: white; display: none; }}
  #banner {{ display: none; background: #4285f4; color: white; padding: 12px 24px;
    border-radius: 12px; margin: 10px; font-size: 1.1em; animation: pulse 2s infinite; }}
</style>
</head><body>
<h1>CC-Caller <span style="font-size:0.5em;opacity:0.6">Gemini Live</span></h1>
<div id="banner">Update ready — tap Connect</div>
<p id="status">Waiting for Claude to finish...</p>
<button id="connect" disabled>Connect</button>
<button id="end">End Call</button>
<div id="log" style="margin-top:20px;font-size:0.9em;opacity:0.7;max-width:90vw;text-align:center"></div>

<script>
  const GEMINI_KEY = "{gemini_key}";
  const MODEL = "{model}";
  let SYSTEM_PROMPT = {system_prompt_json};

  let ws = null;
  let audioCtx = null;
  let micStream = null;
  let processor = null;
  let userMessages = [];
  let isConnected = false;
  let playQueue = [];
  let isPlaying = false;

  // Poll for updates
  setInterval(async () => {{
    if (isConnected) return;
    try {{
      const resp = await fetch('/gemini-config');
      if (resp.ok) {{
        const data = await resp.json();
        if (data.ready) {{
          SYSTEM_PROMPT = data.systemPrompt || SYSTEM_PROMPT;
          document.getElementById('banner').style.display = 'block';
          document.getElementById('connect').disabled = false;
          document.getElementById('status').textContent = 'Update ready — tap Connect';
        }} else {{
          document.getElementById('banner').style.display = 'none';
          document.getElementById('connect').disabled = true;
          document.getElementById('status').textContent = 'Waiting for Claude to finish...';
        }}
      }}
    }} catch(e) {{}}
  }}, 3000);

  // Initial state
  if ({system_prompt_json} !== "") {{
    document.getElementById('banner').style.display = 'block';
    document.getElementById('connect').disabled = false;
    document.getElementById('status').textContent = 'Update ready — tap Connect';
  }}

  document.getElementById('connect').onclick = async function() {{
    document.getElementById('status').textContent = 'Connecting...';
    document.getElementById('status').className = 'pulse';
    document.getElementById('connect').style.display = 'none';
    document.getElementById('banner').style.display = 'none';
    userMessages = [];

    try {{
      // Fetch latest config
      try {{
        const resp = await fetch('/gemini-config');
        if (resp.ok) {{
          const data = await resp.json();
          if (data.ready && data.systemPrompt) SYSTEM_PROMPT = data.systemPrompt;
        }}
      }} catch(e) {{}}

      const url = 'wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key=' + GEMINI_KEY;
      ws = new WebSocket(url);

      ws.onopen = () => {{
        ws.send(JSON.stringify({{
          setup: {{
            model: 'models/' + MODEL,
            generationConfig: {{
              responseModalities: ['AUDIO'],
              speechConfig: {{ voiceConfig: {{ prebuiltVoiceConfig: {{ voiceName: 'Kore' }} }} }}
            }},
            systemInstruction: {{ parts: [{{ text: SYSTEM_PROMPT }}] }},
            inputAudioTranscription: {{}},
            outputAudioTranscription: {{}}
          }}
        }}));
      }};

      ws.onmessage = (event) => {{
        const msg = JSON.parse(event.data);

        if (msg.setupComplete) {{
          isConnected = true;
          document.getElementById('status').textContent = 'Connected — speak now';
          document.getElementById('status').className = '';
          document.getElementById('end').style.display = 'inline-block';
          startMic();
          return;
        }}

        if (msg.serverContent) {{
          const sc = msg.serverContent;

          if (sc.inputTranscription && sc.inputTranscription.text) {{
            log('You: ' + sc.inputTranscription.text);
            userMessages.push(sc.inputTranscription.text);
          }}

          if (sc.outputTranscription && sc.outputTranscription.text) {{
            log('Agent: ' + sc.outputTranscription.text);
          }}

          if (sc.modelTurn && sc.modelTurn.parts) {{
            for (const part of sc.modelTurn.parts) {{
              if (part.inlineData && part.inlineData.data) {{
                playQueue.push(part.inlineData.data);
                if (!isPlaying) playNext();
              }}
            }}
          }}
        }}
      }};

      ws.onerror = (e) => {{
        log('WebSocket error');
        document.getElementById('status').textContent = 'Error';
        document.getElementById('connect').style.display = 'inline-block';
      }};

      ws.onclose = () => {{
        isConnected = false;
        stopMic();
        document.getElementById('status').textContent = 'Call ended — tap Connect for a new call';
        document.getElementById('end').style.display = 'none';
        document.getElementById('connect').style.display = 'inline-block';
        document.getElementById('connect').disabled = false;
        sendTranscript();
      }};

    }} catch(e) {{
      log('Failed: ' + e.message);
      document.getElementById('status').textContent = 'Failed';
      document.getElementById('connect').style.display = 'inline-block';
    }}
  }};

  document.getElementById('end').onclick = function() {{
    endCall();
  }};

  function endCall() {{
    stopMic();
    if (ws && ws.readyState === WebSocket.OPEN) ws.close();
    isConnected = false;
  }}

  async function sendTranscript() {{
    if (userMessages.length === 0) return;
    try {{
      await fetch('/gemini-transcript', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ userMessages: userMessages }})
      }});
    }} catch(e) {{ log('Failed to send transcript'); }}
  }}

  // --- Microphone ---
  async function startMic() {{
    micStream = await navigator.mediaDevices.getUserMedia({{ audio: {{ sampleRate: 16000, channelCount: 1, echoCancellation: true, noiseSuppression: true }} }});
    audioCtx = new AudioContext({{ sampleRate: 16000 }});
    const source = audioCtx.createMediaStreamSource(micStream);
    processor = audioCtx.createScriptProcessor(4096, 1, 1);

    processor.onaudioprocess = (e) => {{
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      const float32 = e.inputBuffer.getChannelData(0);
      const int16 = new Int16Array(float32.length);
      for (let i = 0; i < float32.length; i++) {{
        int16[i] = Math.max(-1, Math.min(1, float32[i])) * 0x7FFF;
      }}
      const bytes = new Uint8Array(int16.buffer);
      let binary = '';
      for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
      const b64 = btoa(binary);

      ws.send(JSON.stringify({{
        realtimeInput: {{ mediaChunks: [{{ mimeType: 'audio/pcm;rate=16000', data: b64 }}] }}
      }}));
    }};

    source.connect(processor);
    processor.connect(audioCtx.destination);
  }}

  function stopMic() {{
    if (processor) {{ processor.disconnect(); processor = null; }}
    if (micStream) {{ micStream.getTracks().forEach(t => t.stop()); micStream = null; }}
  }}

  // --- Audio playback ---
  const playCtx = new (window.AudioContext || window.webkitAudioContext)({{ sampleRate: 24000 }});

  function playNext() {{
    if (playQueue.length === 0) {{ isPlaying = false; return; }}
    isPlaying = true;
    const b64 = playQueue.shift();
    const binary = atob(b64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);

    const int16 = new Int16Array(bytes.buffer);
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 0x7FFF;

    const buf = playCtx.createBuffer(1, float32.length, 24000);
    buf.getChannelData(0).set(float32);
    const src = playCtx.createBufferSource();
    src.buffer = buf;
    src.connect(playCtx.destination);
    src.onended = () => playNext();
    src.start();
  }}

  function log(msg) {{
    document.getElementById('log').innerHTML = '<p>' + msg + '</p>' + document.getElementById('log').innerHTML;
  }}
</script>
</body></html>""")

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
