'use strict';

/* Camera bubble + live captions.
 *
 * Shown while a take is recording. Streams the mic to the local server's
 * /ws/asr (Doubao) and shows the recognised text under the self-view.
 *
 * Lifecycle is driven by main: `bubble:state` with {state:"start", port} or
 * {state:"stop"}. `bubble:mode` sets the layout/shape classes.
 */

const camEl = document.getElementById('cam');
const prevEl = document.getElementById('prev');
const nowEl = document.getElementById('now');
const clockEl = document.getElementById('clock');

let micStream = null;
let camStream = null;
let audioCtx = null;
let processor = null;
let source = null;
let ws = null;
let running = false;
let tick = null;
let startedAt = 0;
let rules = [];   // [{from, to}] custom dictionary from the app

function applyRules(text) {
  let out = text;
  for (const r of rules) {
    if (!r || !r.from) continue;
    const esc = String(r.from).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    try {
      out = out.replace(new RegExp(esc, 'gi'), r.to == null ? '' : String(r.to));
    } catch (_e) { /* skip bad pattern */ }
  }
  return out;
}

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
  ws = new WebSocket(`ws://127.0.0.1:${port}/ws/asr`);
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => ws.send(JSON.stringify({ type: 'start' }));
  ws.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch { return; }
    if (msg.type === 'asr') {
      const text = applyRules((msg.text || '').trim());
      if (!text) return;
      // `text` is the whole-session transcript: last sentence big, rest dim.
      const parts = text.split(/(?<=[.!?。！？])\s*/);
      const last = parts.length > 1 ? parts.pop() : '';
      prevEl.textContent = parts.join(' ');
      nowEl.textContent = last || text;
      if (window.chaplinShell && window.chaplinShell.transcript) {
        window.chaplinShell.transcript(text);
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
  prevEl.textContent = '';
  nowEl.textContent = 'Listening…';
  startedAt = Date.now();
  setClock();
  tick = setInterval(setClock, 500);

  // Kick off the ASR handshake immediately; the mic joins a moment later.
  connect(port);

  let mic = null;
  try {
    mic = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch (err) {
    nowEl.textContent = `⚠︎ mic: ${err.message}`;
    return;
  }
  if (!running) { mic.getTracks().forEach((t) => t.stop()); return; }
  micStream = mic;

  audioCtx = new AudioContext({ sampleRate: 16000 });
  source = audioCtx.createMediaStreamSource(micStream);
  processor = audioCtx.createScriptProcessor(2048, 1, 1);
  processor.onaudioprocess = (event) => {
    if (!running || !ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(floatToPcm16(event.inputBuffer.getChannelData(0)).buffer);
  };
  source.connect(processor);
  const mute = audioCtx.createGain();
  mute.gain.value = 0;
  processor.connect(mute);
  mute.connect(audioCtx.destination);

  // Camera for the self-view (slower to warm up; lags behind the captions).
  let cam = null;
  try {
    cam = await navigator.mediaDevices.getUserMedia({
      video: { width: 640, height: 480, frameRate: 24 },
    });
  } catch (_err) {
    cam = null;
  }
  // If the take ended while the camera was still opening, drop it immediately
  // so the recording light goes out.
  if (!running) { if (cam) cam.getTracks().forEach((t) => t.stop()); return; }
  camStream = cam;
  camEl.srcObject = cam
    ? new MediaStream([...cam.getVideoTracks(), ...micStream.getAudioTracks()])
    : micStream;
}

function stop() {
  running = false;
  if (tick) clearInterval(tick);
  tick = null;
  if (processor) { try { processor.disconnect(); } catch { /* ignore */ } }
  if (source) { try { source.disconnect(); } catch { /* ignore */ } }
  if (audioCtx) { try { audioCtx.close(); } catch { /* ignore */ } }
  processor = null; source = null; audioCtx = null;
  [micStream, camStream].forEach((s) => {
    if (s) { try { s.getTracks().forEach((t) => t.stop()); } catch { /* ignore */ } }
  });
  micStream = null; camStream = null;
  camEl.srcObject = null;
  if (ws) { try { ws.send(JSON.stringify({ type: 'stop' })); ws.close(); } catch { /* ignore */ } ws = null; }
}

const root = document.querySelector('.bubble');

function applyMode(mode) {
  if (!mode) return;
  root.className = `bubble layout-${mode.layout || 'corner'} shape-${mode.shape || 'wide'}`;
}

if (window.chaplinShell && window.chaplinShell.onBubbleMode) {
  window.chaplinShell.onBubbleMode(applyMode);
}

if (window.chaplinShell && window.chaplinShell.onRules) {
  window.chaplinShell.onRules((list) => { rules = Array.isArray(list) ? list : []; });
}

if (window.chaplinShell && window.chaplinShell.onBubble) {
  window.chaplinShell.onBubble((payload) => {
    if (!payload) return;
    if (payload.state === 'start') start(payload.port);
    else stop();
  });
}
