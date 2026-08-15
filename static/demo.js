const $ = (id) => document.getElementById(id);

let displayStream = null;
let micStream = null;
let camStream = null;
let mixedStream = null;
let audioCtx = null;
let recorder = null;
let chunks = [];
let mimeType = "";
let draft = null;
let dirty = new Set();

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
    btn.className = "secondary";
    btn.textContent = "Change";
    btn.style.padding = "4px 10px";
    btn.onclick = async () => {
      const val = prompt(`Enter ${names[name]} API key`);
      if (!val) return;
      const resp = await fetch("/api/v1/keys", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [name]: val }),
      });
      renderKeys((await resp.json()).keys_set);
    };
    row.appendChild(btn);
    $("keyRows").appendChild(row);
  });
}

function setStatus(text) {
  $("status").textContent = text;
}

function fmtTime(s) {
  const t = Number(s) || 0;
  const m = Math.floor(t / 60);
  const sec = (t % 60).toFixed(1);
  return `${m}:${sec.padStart(4, "0")}`;
}

$("startScreen").onclick = async () => {
  try {
    displayStream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
    micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    setStatus("Need screen and microphone permission.");
    return;
  }
  audioCtx = new AudioContext();
  const src = audioCtx.createMediaStreamSource(micStream);
  const dest = audioCtx.createMediaStreamDestination();
  src.connect(dest);
  mixedStream = new MediaStream([
    ...displayStream.getVideoTracks(),
    ...dest.stream.getAudioTracks(),
  ]);
  $("screenPreview").srcObject = displayStream;
  mimeType = chooseMimeType();
  $("record").disabled = false;
  setStatus("Screen ready");
  try {
    camStream = await navigator.mediaDevices.getUserMedia({ video: true });
    $("camPreview").srcObject = camStream;
  } catch (_err) {
    /* camera is optional */
  }
  displayStream.getVideoTracks()[0].addEventListener("ended", () => {
    if (recorder && recorder.state === "recording") recorder.stop();
    $("record").disabled = true;
    setStatus("Screen share ended");
  });
};

$("record").onclick = () => {
  chunks = [];
  recorder = new MediaRecorder(mixedStream, mimeType ? { mimeType } : undefined);
  recorder.ondataavailable = e => { if (e.data.size > 0) chunks.push(e.data); };
  recorder.onstop = upload;
  recorder.start();
  $("record").disabled = true;
  $("stop").disabled = false;
  setStatus("Recording…");
};

$("stop").onclick = () => {
  $("stop").disabled = true;
  setStatus("Saving…");
  recorder.stop();
};

async function upload() {
  const blob = new Blob(chunks, { type: mimeType || "video/webm" });
  if (blob.size < 8000) {
    setStatus("Clip too short — record at least 1 second.");
    $("record").disabled = false;
    return;
  }
  setStatus("Transcribing…");
  const fd = new FormData();
  fd.append("file", blob, "screen.webm");
  fd.append("source_lang", $("sourceLang").value);
  fd.append("target_lang", $("targetLang").value);
  const resp = await fetch("/api/v1/demos", { method: "POST", body: fd });
  let body = {};
  try { body = await resp.json(); } catch (_e) { /* not json */ }
  if (!resp.ok) {
    const detail = body.detail;
    const msg = typeof detail === "string" ? detail : (detail && detail.error) || resp.statusText;
    setStatus(msg);
    if (String(msg).toLowerCase().includes("key")) {
      $("keyRows").scrollIntoView({ behavior: "smooth" });
    }
    $("record").disabled = false;
    return;
  }
  renderDraft(body);
  setStatus(body.warning ? `Ready to edit — ${body.warning}` : "Ready to edit");
  $("record").disabled = false;
}

function renderDraft(next) {
  draft = next;
  dirty = new Set();
  const tbody = $("utterances");
  tbody.innerHTML = "";
  (next.utterances || []).forEach(u => {
    const tr = document.createElement("tr");
    const ta = document.createElement("textarea");
    ta.dataset.id = u.id;
    ta.value = u.english_text || "";
    ta.addEventListener("input", () => dirty.add(u.id));
    ta.addEventListener("blur", () => saveIfDirty(u.id, ta));
    tr.innerHTML = `<td class="time-cell">${fmtTime(u.start_s)}–${fmtTime(u.end_s)}</td>
      <td class="orig-cell"></td><td></td>`;
    tr.children[1].textContent = u.original_text || "";
    tr.children[2].appendChild(ta);
    tbody.appendChild(tr);
  });
  $("editor").hidden = false;
}

async function saveIfDirty(id, ta) {
  if (!draft || !dirty.has(id)) return;
  const resp = await fetch(`/api/v1/demos/${draft.id}/utterances`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ utterances: [{ id, english_text: ta.value }] }),
  });
  if (!resp.ok) {
    $("exportStatus").textContent = "Could not save edit.";
    return;
  }
  draft = await resp.json();
  dirty.delete(id);
}

async function flushDirty() {
  if (!draft) return;
  const edits = [];
  document.querySelectorAll("#utterances textarea").forEach(ta => {
    if (dirty.has(ta.dataset.id)) {
      edits.push({ id: ta.dataset.id, english_text: ta.value });
    }
  });
  if (!edits.length) return;
  const resp = await fetch(`/api/v1/demos/${draft.id}/utterances`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ utterances: edits }),
  });
  if (resp.ok) {
    draft = await resp.json();
    dirty = new Set();
  }
}

$("exportBtn").onclick = async () => {
  if (!draft) return;
  $("exportBtn").disabled = true;
  $("exportStatus").textContent = "Generating voice…";
  await flushDirty();
  const resp = await fetch(`/api/v1/demos/${draft.id}/export`, { method: "POST" });
  if (!resp.ok) {
    let msg = resp.statusText;
    try {
      const body = await resp.json();
      const d = body.detail;
      msg = typeof d === "string" ? d : (d && d.error) || msg;
    } catch (_e) { /* ignore */ }
    $("exportStatus").textContent = msg;
    $("exportBtn").disabled = false;
    if (String(msg).toLowerCase().includes("key")) {
      $("keyRows").scrollIntoView({ behavior: "smooth" });
    }
    return;
  }
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const player = $("exportPlayer");
  player.src = url;
  player.style.display = "block";
  const link = $("downloadLink");
  link.href = url;
  link.download = `chaplin-demo-${draft.id}.mp4`;
  link.hidden = false;
  link.textContent = `Download chaplin-demo-${draft.id}.mp4`;
  $("exportStatus").textContent = "Done";
  $("exportBtn").disabled = false;
};

loadConfig();
