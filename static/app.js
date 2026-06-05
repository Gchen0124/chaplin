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

$("startCamera").onclick = async () => {
  stream = await navigator.mediaDevices.getUserMedia({
    video: { width: 640, height: 480, frameRate: 25 }, audio: true });
  $("preview").srcObject = stream;
  mimeType = chooseMimeType();
  $("record").disabled = false;
  $("status").textContent = "Camera ready";
};

$("record").onclick = () => {
  chunks = [];
  recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  recorder.ondataavailable = e => { if (e.data.size > 0) chunks.push(e.data); };
  recorder.onstop = upload;
  recorder.start();
  $("record").disabled = true; $("stop").disabled = false;
  $("status").textContent = "Recording…";
};

$("stop").onclick = () => { $("stop").disabled = true; $("status").textContent = "Processing…"; recorder.stop(); };

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
  } else if (ev.type === "refined") {
    $("refined").innerHTML = renderRefined(ev.text, ev.highlights);
    if (ev.explanation) $("explanation").textContent = ev.explanation;
    setTime("refTime", ev.stage_ms);
    lastRefined = ev.text || "";
    if (lastRefined) {
      navigator.clipboard.writeText(lastRefined).then(() => {
        $("copied").textContent = "copied ✓"; setTimeout(() => $("copied").textContent = "", 2000);
      }).catch(() => {});
      playTTS(lastRefined);
    }
  } else if (ev.type === "done") {
    $("status").textContent = "Done";
    $("totalTime").textContent = `total ${fmt(ev.total_ms)}`;
  } else if (ev.type === "error") {
    $("status").textContent = `${ev.stage}: ${ev.detail}`;
  }
}

async function upload() {
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
loadConfig();
