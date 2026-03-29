import asyncio
import json as json_module
import queue
import threading
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from starlette.responses import StreamingResponse


def create_app(transcript_queue: queue.Queue) -> FastAPI:
    app = FastAPI()
    app.state.pending_web_call = None
    app.state.web_call_lock = threading.Lock()
    # SSE for live mode
    app.state.live_sse_queue = queue.Queue()
    app.state.live_gemini_config = None

    @app.get("/webhook")
    async def webhook_health():
        return {"status": "healthy"}

    app.state.pending_gemini_call = None

    @app.middleware("http")
    async def no_cache(request, call_next):
        response = await call_next(request)
        if request.url.path in ("/call", "/call-gemini"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
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

      // Transcript batching
      let userBuf = '';
      let agentBuf = '';
      let userTimer = null;
      let agentTimer = null;

      function flushUser() {{
        if (userBuf.trim()) {{
          log('You: ' + userBuf.trim());
          userMessages.push(userBuf.trim());
        }}
        userBuf = '';
        userTimer = null;
      }}
      function flushAgent() {{
        if (agentBuf.trim()) log('Agent: ' + agentBuf.trim());
        agentBuf = '';
        agentTimer = null;
      }}

      ws.onopen = () => {{
        log('Connecting...');
        ws.send(JSON.stringify({{
          setup: {{
            model: 'models/' + MODEL,
            generationConfig: {{
              responseModalities: ['AUDIO'],
              speechConfig: {{ voiceConfig: {{ prebuiltVoiceConfig: {{ voiceName: 'Kore' }} }} }}
            }},
            systemInstruction: {{ parts: [{{ text: SYSTEM_PROMPT }}] }},
            tools: [{{ functionDeclarations: [{{
              name: 'endCall',
              description: 'End the voice call. Use this when the user says go ahead, that is all, stop, or we are done.'
            }}] }}],
            inputAudioTranscription: {{}},
            outputAudioTranscription: {{}}
          }}
        }}));
      }};

      ws.onmessage = async (event) => {{
        let raw = event.data;
        if (raw instanceof Blob) raw = await raw.text();
        let msg;
        try {{
          msg = JSON.parse(raw);
        }} catch(e) {{ return; }}

        // Setup complete
        if (msg.setupComplete !== undefined) {{
          log('Connected — speak now');
          isConnected = true;
          document.getElementById('status').textContent = 'Connected — speak now';
          document.getElementById('status').className = '';
          document.getElementById('end').style.display = 'inline-block';
          startMic().catch(err => log('Mic error: ' + err.message));
          return;
        }}

        // Function call (endCall)
        if (msg.toolCall) {{
          const fc = msg.toolCall.functionCalls;
          if (fc) {{
            for (const call of fc) {{
              if (call.name === 'endCall') {{
                log('Call ending...');
                // Send tool response then close
                ws.send(JSON.stringify({{
                  toolResponse: {{
                    functionResponses: [{{ id: call.id, name: 'endCall', response: {{ result: 'ok' }} }}]
                  }}
                }}));
                setTimeout(() => endCall(), 2000);
                return;
              }}
            }}
          }}
        }}

        // Server content (audio + transcripts)
        if (msg.serverContent) {{
          const sc = msg.serverContent;

          // Interruption: if model turn is done, clear flag
          if (sc.turnComplete) {{
            playQueue = [];
          }}

          if (sc.inputTranscription && sc.inputTranscription.text) {{
            // User is speaking — stop all audio playback immediately
            stopPlayback();
            flushAgent();
            userBuf += ' ' + sc.inputTranscription.text;
            if (userTimer) clearTimeout(userTimer);
            userTimer = setTimeout(flushUser, 1000);

            // Client-side end detection as fallback
            const lower = userBuf.toLowerCase();
            const endPhrases = ['go ahead', "that's all", 'stop', "we're done", 'done for now', 'end session', 'hang up'];
            if (endPhrases.some(p => lower.includes(p))) {{
              log('End phrase detected, closing...');
              setTimeout(() => endCall(), 3000);
            }}
          }}

          if (sc.outputTranscription && sc.outputTranscription.text) {{
            flushUser();
            agentBuf += ' ' + sc.outputTranscription.text;
            if (agentTimer) clearTimeout(agentTimer);
            agentTimer = setTimeout(flushAgent, 1000);

            // If agent says goodbye phrases, auto-close
            const aLower = agentBuf.toLowerCase();
            if (aLower.includes('call back when') || aLower.includes('starting now') || aLower.includes('ending session')) {{
              setTimeout(() => endCall(), 3000);
            }}
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

      // Safety: max 5 minutes per call, then auto-close
      setTimeout(() => {{
        if (isConnected) {{
          log('Max call duration reached, closing...');
          endCall();
        }}
      }}, 300000);

      ws.onerror = (e) => {{
        log('WebSocket error: ' + JSON.stringify(e));
        document.getElementById('status').textContent = 'Error';
        document.getElementById('connect').style.display = 'inline-block';
        document.getElementById('connect').disabled = false;
      }};

      ws.onclose = (e) => {{
        isConnected = false;
        stopMic();
        flushUser();
        flushAgent();
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
        realtimeInput: {{ audio: {{ data: b64, mimeType: 'audio/pcm;rate=16000' }} }}
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
  let playCtx = null;
  function getPlayCtx() {{
    if (!playCtx) playCtx = new (window.AudioContext || window.webkitAudioContext)({{ sampleRate: 24000 }});
    if (playCtx.state === 'suspended') playCtx.resume();
    return playCtx;
  }}

  let currentSource = null;

  function stopPlayback() {{
    playQueue = [];
    isPlaying = false;
    if (currentSource) {{
      try {{ currentSource.stop(); }} catch(e) {{}}
      currentSource = null;
    }}
  }}

  function playNext() {{
    if (playQueue.length === 0) {{ isPlaying = false; currentSource = null; return; }}
    isPlaying = true;
    const b64 = playQueue.shift();
    const binary = atob(b64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);

    const int16 = new Int16Array(bytes.buffer);
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 0x7FFF;

    const ctx = getPlayCtx();
    const buf = ctx.createBuffer(1, float32.length, 24000);
    buf.getChannelData(0).set(float32);
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    currentSource = src;
    src.onended = () => {{ currentSource = null; playNext(); }};
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

    # --- LiveKit browser join page ---

    @app.get("/call-livekit", response_class=HTMLResponse)
    async def livekit_page(token: str = "", url: str = ""):
        return HTMLResponse(f"""<!DOCTYPE html>
<html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CC-Caller LiveKit</title>
<style>
  body {{ font-family: -apple-system, sans-serif; display: flex; flex-direction: column;
    align-items: center; justify-content: center; min-height: 100vh; margin: 0;
    background: #0a0a0a; color: #fff; }}
  #status {{ font-size: 1.5em; margin: 20px; text-align: center; }}
  .pulse {{ animation: pulse 2s infinite; }}
  @keyframes pulse {{ 0%,100%{{ opacity:1 }} 50%{{ opacity:0.5 }} }}
  button {{ padding: 16px 32px; font-size: 1.2em; border: none; border-radius: 12px;
    cursor: pointer; margin: 10px; }}
  #connect {{ background: #22c55e; color: white; }}
  #end {{ background: #ef4444; color: white; display: none; }}
  #mic {{ background: #555; color: white; display: none; font-size: 1.4em; padding: 20px 40px; }}
  #mic.live {{ background: #ef4444; }}
</style>
</head><body>
<h1>CC-Caller <span style="font-size:0.5em;opacity:0.6">LiveKit</span></h1>
<p id="status">Tap Connect to join</p>
<button id="connect">Connect</button>
<button id="mic">Mic OFF</button>
<button id="end">End Session</button>
<div id="log" style="margin-top:20px;font-size:0.9em;opacity:0.7;max-width:90vw;text-align:center"></div>

<script type="module">
  import {{ Room, RoomEvent, Track }} from 'https://cdn.jsdelivr.net/npm/livekit-client@2/+esm';

  const TOKEN = "{token}";
  const WS_URL = "{url}";
  let room = null;
  let micMuted = true;

  document.getElementById('connect').onclick = async () => {{
    document.getElementById('status').textContent = 'Connecting...';
    document.getElementById('status').className = 'pulse';
    document.getElementById('connect').style.display = 'none';

    try {{
      room = new Room();

      room.on(RoomEvent.TrackSubscribed, (track) => {{
        if (track.kind === Track.Kind.Audio) {{
          const el = track.attach();
          document.body.appendChild(el);
          log('Audio track attached');
        }}
      }});

      room.on(RoomEvent.Disconnected, () => {{
        document.getElementById('status').textContent = 'Disconnected';
        document.getElementById('end').style.display = 'none';
        document.getElementById('mic').style.display = 'none';
        document.getElementById('connect').style.display = 'inline-block';
      }});

      await room.connect(WS_URL, TOKEN);
      log('Connected to room');

      // Publish mic (muted initially)
      await room.localParticipant.setMicrophoneEnabled(true);
      await room.localParticipant.setMicrophoneEnabled(false);
      micMuted = true;

      document.getElementById('status').textContent = 'Connected — tap Mic to speak';
      document.getElementById('status').className = '';
      document.getElementById('end').style.display = 'inline-block';
      document.getElementById('mic').style.display = 'inline-block';
    }} catch(e) {{
      log('Failed: ' + e.message);
      document.getElementById('status').textContent = 'Failed';
      document.getElementById('connect').style.display = 'inline-block';
    }}
  }};

  document.getElementById('mic').onclick = async () => {{
    if (!room) return;
    micMuted = !micMuted;
    await room.localParticipant.setMicrophoneEnabled(!micMuted);
    const btn = document.getElementById('mic');
    btn.textContent = micMuted ? 'Mic OFF' : 'Mic ON';
    btn.className = micMuted ? '' : 'live';
  }};

  document.getElementById('end').onclick = () => {{
    if (room) room.disconnect();
  }};

  function log(msg) {{
    document.getElementById('log').innerHTML = '<p>' + msg + '</p>' + document.getElementById('log').innerHTML;
  }}
</script>
</body></html>""")

    # --- Live mode polling + page ---
    app.state.live_messages = []  # list of {type, message, id}
    app.state.live_msg_counter = 0

    @app.get("/live-poll")
    async def live_poll(after: int = 0):
        """Return messages newer than 'after' ID."""
        msgs = [m for m in app.state.live_messages if m["id"] > after]
        return {"messages": msgs}

    @app.get("/live-config")
    async def live_config():
        gc = app.state.live_gemini_config
        if not gc:
            return {"ready": False}
        return {**gc, "ready": True}

    @app.get("/call-gemini-live", response_class=HTMLResponse)
    async def gemini_live_page():
        gc = app.state.live_gemini_config
        gemini_key = gc.get("geminiKey", "") if gc else ""
        model = gc.get("model", "gemini-3.1-flash-live-preview") if gc else "gemini-3.1-flash-live-preview"
        system_prompt = gc.get("systemPrompt", "") if gc else ""

        return HTMLResponse(f"""<!DOCTYPE html>
<html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CC-Caller Live</title>
<style>
  body {{ font-family: -apple-system, sans-serif; display: flex; flex-direction: column;
    align-items: center; justify-content: center; min-height: 100vh; margin: 0;
    background: #0a0a0a; color: #fff; }}
  #status {{ font-size: 1.5em; margin: 20px; text-align: center; }}
  .pulse {{ animation: pulse 2s infinite; }}
  @keyframes pulse {{ 0%,100%{{ opacity:1 }} 50%{{ opacity:0.5 }} }}
  button {{ padding: 16px 32px; font-size: 1.2em; border: none; border-radius: 12px;
    cursor: pointer; margin: 10px; }}
  #connect {{ background: #22c55e; color: white; }}
  #end {{ background: #ef4444; color: white; display: none; }}
  #mic {{ background: #555; color: white; display: none; font-size: 1.4em; padding: 20px 40px; }}
  #mic.live {{ background: #ef4444; }}
</style>
</head><body>
<h1>CC-Caller <span style="font-size:0.5em;opacity:0.6">Live</span></h1>
<p id="status">Tap Connect to start a live session</p>
<button id="connect">Connect</button>
<button id="mic">Mic OFF</button>
<button id="end">End Session</button>
<div id="log" style="margin-top:20px;font-size:0.9em;opacity:0.7;max-width:90vw;text-align:center"></div>

<script>
  const GEMINI_KEY = "{gemini_key}";
  const MODEL = "{model}";
  const SYSTEM_PROMPT = "You are a voice relay between a user and a coding agent that runs in the background.\\n" +
    "You do NOT write code or answer technical questions yourself. You are a messenger.\\n" +
    "Your job:\\n" +
    "1) Collect what the user says and pass it along (this happens automatically).\\n" +
    "2) When the user gives a task or instruction, say 'Sending that to the agent now.' and STOP. Do not attempt to do the task yourself.\\n" +
    "3) When you receive a text message, it is the response FROM the coding agent. Read it to the user exactly as received. Do not add your own interpretation.\\n" +
    "4) After reading a response, ask 'What would you like to do next?'\\n" +
    "5) If the user asks you a coding question, say 'Let me ask the agent.' Do NOT answer it yourself.\\n" +
    "6) If the user says 'end session' or 'we are done', say 'Ending session.' and stop.\\n" +
    "NEVER make up information about code, files, or the project. You only know what the agent tells you.\\n" +
    "Always respond in English. Keep responses short.";

  let ws = null;
  let audioCtx = null;
  let micStream = null;
  let processor = null;
  let micMuted = true;
  let userMessages = [];
  let isConnected = false;
  let playQueue = [];
  let isPlaying = false;
  let currentSource = null;
  let idleTimer = null;
  const IDLE_TIMEOUT = 2 * 60 * 60 * 1000; // 2 hours

  // Transcript batching
  let userBuf = '';
  let agentBuf = '';
  let userTimer = null;
  let agentTimer = null;
  let userSendTimer = null;

  function flushUser() {{
    if (userBuf.trim()) {{
      log('You: ' + userBuf.trim());
      userMessages.push(userBuf.trim());
    }}
    userBuf = '';
    userTimer = null;
  }}
  function flushAgent() {{
    if (agentBuf.trim()) log('Agent: ' + agentBuf.trim());
    agentBuf = '';
    agentTimer = null;
  }}

  function resetIdleTimer() {{
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {{
      log('Idle timeout — disconnecting');
      endSession();
    }}, IDLE_TIMEOUT);
  }}

  document.getElementById('connect').onclick = async function() {{
    document.getElementById('status').textContent = 'Connecting...';
    document.getElementById('status').className = 'pulse';
    document.getElementById('connect').style.display = 'none';
    userMessages = [];

    try {{
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
            tools: [{{ functionDeclarations: [{{
              name: 'endCall',
              description: 'End the session when user says end session or we are done.'
            }}] }}],
            inputAudioTranscription: {{}},
            outputAudioTranscription: {{}}
          }}
        }}));
      }};

      ws.onmessage = async (event) => {{
        let raw = event.data;
        if (raw instanceof Blob) raw = await raw.text();
        let msg;
        try {{ msg = JSON.parse(raw); }} catch(e) {{ return; }}

        if (msg.setupComplete !== undefined) {{
          log('Connected — tap Mic to speak');
          isConnected = true;
          document.getElementById('status').textContent = 'Live — tap Mic to speak';
          document.getElementById('status').className = '';
          document.getElementById('end').style.display = 'inline-block';
          document.getElementById('mic').style.display = 'inline-block';
          startMic();  // starts mic but muted
          startSSE();
          resetIdleTimer();
          return;
        }}

        if (msg.toolCall) {{
          const fc = msg.toolCall.functionCalls;
          if (fc) {{
            for (const call of fc) {{
              if (call.name === 'endCall') {{
                ws.send(JSON.stringify({{
                  toolResponse: {{
                    functionResponses: [{{ id: call.id, name: 'endCall', response: {{ result: 'ok' }} }}]
                  }}
                }}));
                setTimeout(() => endSession(), 2000);
                return;
              }}
            }}
          }}
        }}

        if (msg.serverContent) {{
          const sc = msg.serverContent;

          if (sc.turnComplete) playQueue = [];

          if (sc.inputTranscription && sc.inputTranscription.text) {{
            stopPlayback();
            flushAgent();
            userBuf += ' ' + sc.inputTranscription.text;
            if (userTimer) clearTimeout(userTimer);
            userTimer = setTimeout(flushUser, 1000);
            resetIdleTimer();

            // Send transcript after 2s of silence
            if (userSendTimer) clearTimeout(userSendTimer);
            userSendTimer = setTimeout(sendTranscript, 2000);

            // End detection
            const lower = userBuf.toLowerCase();
            const endPhrases = ['end session', "we're done", 'done for now', 'stop session'];
            if (endPhrases.some(p => lower.includes(p))) {{
              setTimeout(() => endSession(), 3000);
            }}
          }}

          if (sc.outputTranscription && sc.outputTranscription.text) {{
            flushUser();
            agentBuf += ' ' + sc.outputTranscription.text;
            if (agentTimer) clearTimeout(agentTimer);
            agentTimer = setTimeout(flushAgent, 1000);

            if (agentBuf.toLowerCase().includes('ending session')) {{
              setTimeout(() => endSession(), 3000);
            }}
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

      ws.onerror = () => {{ log('Connection error'); }};
      ws.onclose = () => {{
        isConnected = false;
        stopMic();
        stopSSE();
        flushUser();
        flushAgent();
        document.getElementById('status').textContent = 'Session ended';
        document.getElementById('end').style.display = 'none';
        document.getElementById('connect').style.display = 'inline-block';
      }};

    }} catch(e) {{
      log('Failed: ' + e.message);
      document.getElementById('status').textContent = 'Failed';
      document.getElementById('connect').style.display = 'inline-block';
    }}
  }};

  document.getElementById('end').onclick = () => endSession();

  document.getElementById('mic').onclick = () => {{
    micMuted = !micMuted;
    const btn = document.getElementById('mic');
    if (micMuted) {{
      btn.textContent = 'Mic OFF';
      btn.classList.remove('live');
    }} else {{
      btn.textContent = 'Mic ON';
      btn.classList.add('live');
    }}
  }};

  function endSession() {{
    stopMic();
    stopSSE();
    flushUser();
    flushAgent();
    sendTranscript();
    if (ws && ws.readyState === WebSocket.OPEN) ws.close();
    isConnected = false;
    if (idleTimer) clearTimeout(idleTimer);
  }}

  // --- Polling listener ---
  let lastMsgId = 0;
  let pollInterval = null;
  function startSSE() {{
    pollInterval = setInterval(async () => {{
      try {{
        const resp = await fetch('/live-poll?after=' + lastMsgId);
        if (!resp.ok) return;
        const data = await resp.json();
        for (const msg of data.messages) {{
          lastMsgId = msg.id;
          log('[poll] type=' + msg.type + ' id=' + msg.id);
          if (!ws || ws.readyState !== WebSocket.OPEN) {{
            log('[poll] WebSocket not open!');
            continue;
          }}
          if (msg.type === 'progress') {{
            ws.send(JSON.stringify({{
              realtimeInput: {{ text: msg.message }}
            }}));
          }}
          if (msg.type === 'result') {{
            const text = msg.message.length > 1000 ? msg.message.substring(0, 1000) : msg.message;
            ws.send(JSON.stringify({{
              realtimeInput: {{ text: 'Here is what I found: ' + text + '. What would you like to do next?' }}
            }}));
            log('[poll] Result sent to Gemini (' + text.length + ' chars)');
          }}
        }}
      }} catch(e) {{}}
    }}, 2000);
  }}

  function stopSSE() {{
    if (pollInterval) {{ clearInterval(pollInterval); pollInterval = null; }}
  }}

  async function sendTranscript() {{
    if (userMessages.length === 0) return;
    const msgs = [...userMessages];
    userMessages = [];
    try {{
      await fetch('/gemini-transcript', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ userMessages: msgs }})
      }});
    }} catch(e) {{}}
  }}

  // --- Microphone ---
  async function startMic() {{
    micStream = await navigator.mediaDevices.getUserMedia({{ audio: {{ sampleRate: 16000, channelCount: 1, echoCancellation: true, noiseSuppression: true }} }});
    audioCtx = new AudioContext({{ sampleRate: 16000 }});
    const source = audioCtx.createMediaStreamSource(micStream);
    processor = audioCtx.createScriptProcessor(4096, 1, 1);
    processor.onaudioprocess = (e) => {{
      if (!ws || ws.readyState !== WebSocket.OPEN || micMuted) return;
      const float32 = e.inputBuffer.getChannelData(0);
      const int16 = new Int16Array(float32.length);
      for (let i = 0; i < float32.length; i++) int16[i] = Math.max(-1, Math.min(1, float32[i])) * 0x7FFF;
      const bytes = new Uint8Array(int16.buffer);
      let binary = '';
      for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
      ws.send(JSON.stringify({{ realtimeInput: {{ audio: {{ data: btoa(binary), mimeType: 'audio/pcm;rate=16000' }} }} }}));
    }};
    source.connect(processor);
    processor.connect(audioCtx.destination);
  }}

  function stopMic() {{
    if (processor) {{ processor.disconnect(); processor = null; }}
    if (micStream) {{ micStream.getTracks().forEach(t => t.stop()); micStream = null; }}
  }}

  // --- Audio playback ---
  let playCtx = null;
  function getPlayCtx() {{
    if (!playCtx) playCtx = new (window.AudioContext || window.webkitAudioContext)({{ sampleRate: 24000 }});
    if (playCtx.state === 'suspended') playCtx.resume();
    return playCtx;
  }}

  function stopPlayback() {{
    playQueue = [];
    isPlaying = false;
    if (currentSource) {{ try {{ currentSource.stop(); }} catch(e) {{}} currentSource = null; }}
  }}

  function playNext() {{
    if (playQueue.length === 0) {{ isPlaying = false; currentSource = null; return; }}
    isPlaying = true;
    const b64 = playQueue.shift();
    const binary = atob(b64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    const int16 = new Int16Array(bytes.buffer);
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 0x7FFF;
    const ctx = getPlayCtx();
    const buf = ctx.createBuffer(1, float32.length, 24000);
    buf.getChannelData(0).set(float32);
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    currentSource = src;
    src.onended = () => {{ currentSource = null; playNext(); }};
    src.start();
  }}

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
