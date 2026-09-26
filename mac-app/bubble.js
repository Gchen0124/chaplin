'use strict';

/* Camera bubble + live captions.
 *
 * Shown while a take is recording. Streams the mic to the local server's
 * /ws/asr (Doubao) and shows the recognised text under the self-view.
 *
 * Lifecycle is driven by main: `bubble:state` with {state:"start", port} or
 * {state:"stop"}.
 */

const camEl = document.getElementById('cam');
const prevEl = document.getElementById('prev');
const nowEl = document.getElementById('now');
const clockEl = document.getElementById('clock');

let stream = null;
let audioCtx = null;
let processor = null;
let source = null;
let ws = null;
let running = false;
let committed = '';
let tick = null;
let startedAt = 0;

function setClock() {
  const s = Math.floor((Date.now() - startedAt) / 1000);
  clockEl.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function floatToPcm16(f32) {
  const out = new Int16Array(f32.length);
  for (let i = 0; i < f32.length; i += 1) {
    const v = Math.max(-1, Math.min(1, f32[i]));
    out[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
  }
  return out;
}

function connect(port) {
  const wsUrl = `ws://127.0.0.1:${port}/ws/asr`;
  ws = new WebSocket(wsUrl);
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => ws.send(JSON.stringify({ type: 'start' }));
  ws.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch { return; }
    if (msg.type === 'asr') {
      const text = (msg.text || '').trim();
      if (msg.definite) {
        committed = text || committed;
        prevEl.textContent = committed;
        nowEl.textContent = '';
      } else {
        nowEl.textContent = text;
      }
      // Hand the best-known live text to main so a stop can copy it at once.
      if (window.chaplinShell && window.chaplinShell.transcript) {
        window.chaplinShell.transcript([committed, text].filter(Boolean).join(' ').trim());
      }
    } else if (msg.type === 'error') {
      nowEl.textContent = `⚠︎ ${msg.detail}`;
    }
  };
  ws.onclose = () => { ws = null; };
}

async function start(port) {
  if (running) return;
  running = true;
  committed = '';
  prevEl.textContent = '';
  nowEl.textContent = 'Listening…';
  startedAt = Date.now();
  setClock();
  tick = setInterval(setClock, 500);

  connect(port);

  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { width: 640, height: 480, frameRate: 24 },
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch (err) {
    nowEl.textContent = `⚠︎ camera/mic: ${err.message}`;
    return;
  }
  camEl.srcObject = stream;

  audioCtx = new AudioContext({ sampleRate: 16000 });
  source = audioCtx.createMediaStreamSource(stream);
  processor = audioCtx.createScriptProcessor(2048, 1, 1);
  processor.onaudioprocess = (event) => {
    if (!running || !ws || ws.readyState !== WebSocket.OPEN) return;
    const pcm = floatToPcm16(event.inputBuffer.getChannelData(0));
    ws.send(pcm.buffer);
  };
  source.connect(processor);
  const mute = audioCtx.createGain();
  mute.gain.value = 0;
  processor.connect(mute);
  mute.connect(audioCtx.destination);
}

function stop() {
  running = false;
  if (tick) clearInterval(tick);
  tick = null;
  if (processor) { try { processor.disconnect(); } catch { /* ignore */ } }
  if (source) { try { source.disconnect(); } catch { /* ignore */ } }
  if (audioCtx) { try { audioCtx.close(); } catch { /* ignore */ } }
  processor = null; source = null; audioCtx = null;
  if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
  camEl.srcObject = null;
  if (ws) { try { ws.send(JSON.stringify({ type: 'stop' })); ws.close(); } catch { /* ignore */ } ws = null; }
}

if (window.chaplinShell && window.chaplinShell.onBubble) {
  window.chaplinShell.onBubble((payload) => {
    if (!payload) return;
    if (payload.state === 'start') start(payload.port);
    else stop();
  });
}
