const $ = (id) => document.getElementById(id);
let stream, recorder, chunks = [], mimeType = "", lastRefined = "";

function chooseMimeType() {
  return ["video/webm;codecs=vp9,opus", "video/webm;codecs=vp8,opus",
    "video/webm", "video/mp4"].find(t => MediaRecorder.isTypeSupported(t)) || "";
}

async function loadConfig() {
  const cfg = await (await fetch("/api/v1/config")).json();
  $("targetLang").value = cfg.default_lang;
  renderKeys(cfg.keys_set);
}

function renderKeys(keysSet) {
  const names = { gladia: "Gladia (speech)", elevenlabs: "ElevenLabs (voice)", openai: "OpenAI (trainer)" };
  $("keyRows").innerHTML = "";
  Object.keys(names).forEach(name => {
    const row = document.createElement("div");
    row.className = "key-row";
    row.innerHTML = `<span>${names[name]}: ${keysSet[name] ? "set ✓" : "not set"}</span>`;
    const btn = document.createElement("button");
    btn.className = "secondary"; btn.textContent = "Change"; btn.style.padding = "4px 10px";
    btn.onclick = async () => {
      const val = prompt(`Enter ${names[name]} API key`);
      if (!val) return;
      const resp = await fetch("/api/v1/keys", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [name]: val }) });
      renderKeys((await resp.json()).keys_set);
    };
    row.appendChild(btn);
    $("keyRows").appendChild(row);
  });
}

async function ensureStream() {
  if (stream && stream.active) return stream;
  $("status").textContent = "Requesting camera + mic…";
  stream = await navigator.mediaDevices.getUserMedia({
    video: { width: 640, height: 480, frameRate: 25 }, audio: true });
  $("preview").srcObject = stream;
  mimeType = chooseMimeType();
  return stream;
}

$("startCamera").onclick = async () => {
  try {
    await ensureStream();
    $("record").disabled = false;
    $("status").textContent = "Camera ready";
  } catch (e) {
    $("status").textContent = `Camera error: ${e.message}`;
  }
};

async function startRecording() {
  if (recorder && recorder.state === "recording") return;
  // Recording needs the camera/mic stream; acquire it on demand instead of
  // forcing the user to find "Start Camera" first.
  if (!stream || !stream.active) {
    try {
      await ensureStream();
    } catch (e) {
      $("status").textContent = `Camera error: ${e.message}`;
      return;
    }
  }
  chunks = [];
  $("status").textContent = "Recording…";
  $("record").disabled = true;
  $("stop").disabled = false;

  try {
    recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  } catch (e) {
    $("status").textContent = `Recorder error: ${e.message}`;
    $("record").disabled = false;
    return;
  }

  // A timeslice makes the browser emit chunks as we go, so a short recording
  // still has data even if the final event is delayed.
  recorder.ondataavailable = e => { if (e.data && e.data.size > 0) chunks.push(e.data); };
  recorder.onerror = e => { $("status").textContent = `Recorder error: ${e.error?.name || "unknown"}`; };
  recorder.onstop = () => {
    // Give the encoder a moment to flush its last chunk before uploading.
    setTimeout(upload, 60);
  };
  recorder.start(250);
  hudState('recording');
}

$("record").onclick = startRecording;

function stopRecording() {
  $("stop").disabled = true;
  $("status").textContent = "Processing…";
  if (recorder && recorder.state !== "inactive") recorder.stop();
}

// Copy via the Electron shell when available: it works even if the window is
// hidden/minimized, unlike navigator.clipboard which needs a focused document.
function copyText(text) {
  if (window.chaplinShell && window.chaplinShell.copy) return window.chaplinShell.copy(text);
  return navigator.clipboard.writeText(text);
}

// Electron shell: mirror state into the bottom HUD (no-op in a plain browser).
function hudState(state, extra) {
  if (window.chaplinShell && window.chaplinShell.hudState) {
    window.chaplinShell.hudState(Object.assign({ state }, extra || {}));
  }
}

function minimizeShell() {
  if (window.chaplinShell && window.chaplinShell.minimize) window.chaplinShell.minimize();
}

// Drop the camera/mic as soon as a take is over so the recording light goes out.
function releaseCamera() {
  if (stream) {
    stream.getTracks().forEach((t) => t.stop());
    stream = null;
  }
  const preview = $("preview");
  if (preview) preview.srcObject = null;
}

function takeRunning() {
  return Boolean(recorder) && (recorder.state === "recording" || recorder.state === "paused");
}

// Pause / resume the current take (HUD ⏸).
function pauseTake() {
  if (!recorder || recorder.state !== "recording") return;
  recorder.pause();
  $("status").textContent = "Paused";
  hudState('paused');
}

function resumeTake() {
  if (!recorder || recorder.state !== "paused") return;
  recorder.resume();
  $("status").textContent = "Recording…";
  hudState('recording');
}

// Stop + transcribe + get out of the way (hotkey 2nd press or HUD ■).
function endTake() {
  if (!takeRunning()) return;
  stopRecording();
  hudState('processing');
  minimizeShell();
}

// Stop and throw the take away (HUD ✕).
function cancelTake() {
  if (recorder && recorder.state !== "inactive") {
    recorder.onstop = null;
    recorder.ondataavailable = null;
    recorder.stop();
  }
  chunks = [];
  releaseCamera();
  $("stop").disabled = true;
  $("record").disabled = false;
  $("status").textContent = "Cancelled";
  hudState('idle');
  minimizeShell();
}

// In-app Stop keeps the window open but must still sync the bottom HUD.
$("stop").onclick = () => { stopRecording(); hudState('processing'); };

function renderRefined(text, highlights) {
  let html = text;
  (highlights || []).forEach(h => {
    if (h.refined_phrase && html.includes(h.refined_phrase)) {
      html = html.replace(h.refined_phrase, `<mark title="${h.reason || ""}">${h.refined_phrase}</mark>`);
    }
  });
  return html;
}

function fmt(ms) { return ms >= 1000 ? (ms / 1000).toFixed(1) + "s" : ms + "ms"; }
function setTime(id, ms) {
  const el = $(id); el.textContent = fmt(ms);
  el.classList.toggle("slow", ms >= 3000);
}
function resetResults() {
  $("original").textContent = "…";
  $("refined").textContent = "…";
  $("vsr").textContent = $("lipread").checked ? "…" : "(off)";
  ["lipTime", "origTime", "refTime"].forEach(id => { $(id).textContent = ""; $(id).classList.remove("slow"); });
  $("srcLang").textContent = ""; $("explanation").textContent = ""; $("totalTime").textContent = "";
  $("copied").textContent = "";
}

function handleEvent(ev) {
  if (ev.type === "lip") {
    $("vsr").textContent = ev.text || "—"; setTime("lipTime", ev.stage_ms);
  } else if (ev.type === "original") {
    $("original").textContent = ev.text || "—";
    $("srcLang").textContent = ev.source_lang ? `(${ev.source_lang}${ev.from_lip ? " · from lips" : ""})` : "";
    setTime("origTime", ev.stage_ms);
    // The clipboard already holds the live (Doubao) transcript from the moment
    // we stopped; leave it alone so the user can paste before the AI lands.
  } else if (ev.type === "refined") {
    $("refined").innerHTML = renderRefined(ev.text, ev.highlights);
    if (ev.explanation) $("explanation").textContent = ev.explanation;
    setTime("refTime", ev.stage_ms);
    lastRefined = ev.text || "";
    if (lastRefined) {
      copyText(lastRefined).then(() => {
        $("copied").textContent = "refined copied ✓";
        setTimeout(() => { if ($("copied").textContent === "refined copied ✓") $("copied").textContent = ""; }, 2500);
        // Paste the finished text into whatever app the user is typing in.
        if (window.chaplinShell && window.chaplinShell.paste) window.chaplinShell.paste();
      }).catch(() => {});
      playTTS(lastRefined);
      hudState('done');
    }
  } else if (ev.type === "done") {
    $("status").textContent = "Done";
    $("totalTime").textContent = `total ${fmt(ev.total_ms)}`;
  } else if (ev.type === "error") {
    $("status").textContent = `${ev.stage}: ${ev.detail}`;
    hudState('error', { text: ev.detail });
  }
}

async function upload() {
  $("record").disabled = false;
  $("stop").disabled = true;
  releaseCamera();

  const total = chunks.reduce((n, c) => n + c.size, 0);
  if (!total) {
    $("status").textContent = "Nothing was recorded — check the mic and try again.";
    return;
  }

  const blob = new Blob(chunks, { type: mimeType || "video/webm" });
  // Let the user watch/hear their own recording (audio + video).
  const rec = $("myRecording");
  if (rec.src) URL.revokeObjectURL(rec.src);
  rec.src = URL.createObjectURL(blob);
  rec.style.display = "block";
  $("noRecording").style.display = "none";
  const form = new FormData();
  form.append("file", blob, "clip.webm");
  form.append("target_lang", $("targetLang").value);
  form.append("source_lang", $("sourceLang").value);
  form.append("lipread", $("lipread").checked ? "on" : "off");
  resetResults();
  $("status").textContent = "Processing…";
  let resp;
  try {
    resp = await fetch("/api/v1/sessions/stream", { method: "POST", body: form });
  } catch (e) { $("status").textContent = "Network error"; $("record").disabled = false; return; }
  $("record").disabled = false;
  if (!resp.ok || !resp.body) {
    const err = await resp.json().catch(() => ({ detail: "Error" }));
    $("status").textContent = err.detail || "Error"; return;
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, idx).trim();
      buf = buf.slice(idx + 1);
      if (line) { try { handleEvent(JSON.parse(line)); } catch (_) {} }
    }
  }
}

async function playTTS(text) {
  try {
    const resp = await fetch("/api/v1/tts", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }) });
    if (!resp.ok) return;
    const buf = await resp.blob();
    $("ttsAudio").src = URL.createObjectURL(buf);
    $("ttsAudio").play();
  } catch (_) {}
}

$("play").onclick = () => { if (lastRefined) playTTS(lastRefined); };

// Electron shell: one press starts a take, the next stops it + minimizes.
if (window.chaplinShell && window.chaplinShell.onToggleRecord) {
  window.chaplinShell.onToggleRecord(() => {
    if (takeRunning()) endTake();
    else startRecording();
  });
}

// HUD buttons: ■ stop + transcribe, ⏸ pause/resume, ✕ discard.
if (window.chaplinShell && window.chaplinShell.onHudAction) {
  window.chaplinShell.onHudAction((action) => {
    if (action === 'stop') endTake();
    else if (action === 'cancel') cancelTake();
    else if (action === 'pause') {
      if (recorder && recorder.state === 'paused') resumeTake();
      else pauseTake();
    }
  });
}

loadConfig();
