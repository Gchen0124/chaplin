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

async function upload() {
  const blob = new Blob(chunks, { type: mimeType || "video/webm" });
  const form = new FormData();
  form.append("file", blob, "clip.webm");
  form.append("target_lang", $("targetLang").value);
  const resp = await fetch("/api/v1/sessions", { method: "POST", body: form });
  $("record").disabled = false;
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: "Error" }));
    $("status").textContent = err.detail || "Error";
    return;
  }
  const data = await resp.json();
  $("original").textContent = data.original_text || "—";
  $("srcLang").textContent = data.source_lang ? `(${data.source_lang})` : "";
  $("refined").innerHTML = renderRefined(data.refined_text, data.highlights);
  $("vsr").textContent = data.vsr_raw_text || "—";
  lastRefined = data.refined_text || "";
  $("status").textContent = "Done";
  if (lastRefined) {
    try { await navigator.clipboard.writeText(lastRefined);
      $("copied").textContent = "copied ✓"; setTimeout(() => $("copied").textContent = "", 2000);
    } catch (_) {}
    playTTS(lastRefined);
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
