// CC-Caller PWA: WS audio bridge + captions + status + push + wake lock.
const qs = new URLSearchParams(location.search);
if (qs.get('token')) localStorage.setItem('cc_token', qs.get('token'));
const TOKEN = localStorage.getItem('cc_token') || '';

const $ = (id) => document.getElementById(id);
let ws = null, micCtx = null, micStream = null, spkCtx = null;
let playHead = 0, wakeLock = null, elapsedTimer = null, workingSince = null;

function setStatus(text, cls) {
  const el = $('status');
  el.textContent = text;
  el.className = 'status ' + cls;
}

function addCaption(role, text) {
  const box = $('captions');
  const last = box.lastElementChild;
  if (last && last.dataset.role === role) {
    last.textContent += text;
  } else {
    const div = document.createElement('div');
    div.className = 'cap ' + role;
    div.dataset.role = role;
    div.textContent = text;
    box.appendChild(div);
  }
  box.scrollTop = box.scrollHeight;
}

function b64ToF32(b64) {
  const bin = atob(b64);
  const i16 = new Int16Array(new Uint8Array([...bin].map(c => c.charCodeAt(0))).buffer);
  const f32 = new Float32Array(i16.length);
  for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 0x8000;
  return f32;
}

function bufToB64(buf) {
  const bytes = new Uint8Array(buf);
  let bin = '';
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}

function playAudio(b64) {
  if (!spkCtx) spkCtx = new AudioContext({ sampleRate: 24000 });
  const f32 = b64ToF32(b64);
  const buf = spkCtx.createBuffer(1, f32.length, 24000);
  buf.copyToChannel(f32, 0);
  const src = spkCtx.createBufferSource();
  src.buffer = buf;
  src.connect(spkCtx.destination);
  const t = Math.max(spkCtx.currentTime, playHead);
  src.start(t);
  playHead = t + buf.duration;
}

async function startMic() {
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });
  micCtx = new AudioContext({ sampleRate: 16000 });
  await micCtx.audioWorklet.addModule('/static/audio-worklet.js');
  const src = micCtx.createMediaStreamSource(micStream);
  const node = new AudioWorkletNode(micCtx, 'pcm-capture');
  node.port.onmessage = (e) => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'audio', data: bufToB64(e.data) }));
    }
  };
  src.connect(node);
}

function setWorking(on) {
  $('taskbar').classList.toggle('hidden', !on);
  if (on) {
    clearInterval(elapsedTimer);
    workingSince = Date.now();
    setStatus('working', 'working');
    elapsedTimer = setInterval(() => {
      const s = Math.floor((Date.now() - workingSince) / 1000);
      $('elapsed').textContent = s >= 60 ? Math.floor(s / 60) + 'm ' + (s % 60) + 's' : s + 's';
    }, 1000);
  } else {
    clearInterval(elapsedTimer);
    setStatus('live', 'live');
  }
}

async function setupPush() {
  try {
    if ((await Notification.requestPermission()) !== 'granted') return;
    const reg = await navigator.serviceWorker.register('/sw.js');
    const cfg = await (await fetch('/api/config?token=' + TOKEN)).json();
    const raw = atob(cfg.vapidPublicKey.replace(/-/g, '+').replace(/_/g, '/'));
    const key = new Uint8Array([...raw].map(c => c.charCodeAt(0)));
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true, applicationServerKey: key,
    });
    await fetch('/api/push-subscribe?token=' + TOKEN, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(sub.toJSON()),
    });
  } catch (e) { console.log('[push]', e); }
}

async function connect() {
  setupPush();
  setStatus('connecting…', 'idle');
  const proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
  ws = new WebSocket(proto + location.host + '/ws?token=' + TOKEN);
  ws.onmessage = async (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === 'ready') {
      setStatus('live', 'live');
      $('connect').textContent = 'Hang up';
      $('connect').classList.add('connected');
      await startMic();
      try { wakeLock = await navigator.wakeLock.request('screen'); } catch (e) {}
    } else if (msg.type === 'audio') playAudio(msg.data);
    else if (msg.type === 'caption') addCaption(msg.role, msg.text);
    else if (msg.type === 'status') {
      if (msg.state === 'working') setWorking(true);
      else if (msg.state === 'done') setWorking(false);
      else if (msg.state === 'ended') disconnect();
    } else if (msg.type === 'error') addCaption('agent', '⚠ ' + msg.message);
  };
  ws.onclose = () => disconnect(true);
}

function disconnect(remote) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'end' }));
    ws.close();
  }
  ws = null;
  if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
  if (micCtx) { micCtx.close(); micCtx = null; }
  if (spkCtx) { spkCtx.close(); spkCtx = null; }
  playHead = 0;
  if (wakeLock) { wakeLock.release(); wakeLock = null; }
  clearInterval(elapsedTimer);
  $('taskbar').classList.add('hidden');
  $('connect').textContent = 'Connect';
  $('connect').classList.remove('connected');
  setStatus('disconnected', 'idle');
}

$('connect').onclick = () => (ws ? disconnect() : connect());
if (qs.get('callback') === '1') connect();
