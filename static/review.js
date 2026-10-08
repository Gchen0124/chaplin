const $ = (id) => document.getElementById(id);
let mode = "vocab";

async function loadVocab() {
  const params = new URLSearchParams();
  if ($("search").value) params.set("q", $("search").value);
  if ($("starredOnly").checked) params.set("starred", "true");
  const items = await (await fetch("/api/v1/vocab?" + params.toString())).json();
  $("list").innerHTML = items.length ? "" : "<p class='status'>No vocabulary yet.</p>";
  items.forEach(v => {
    const row = document.createElement("div");
    row.className = "card"; row.style.marginBottom = "10px";
    row.innerHTML = `<div class="line"><div class="text">
      <span style="color:#9aa6a1;">${v.original_phrase}</span> →
      <mark>${v.refined_phrase}</mark></div>
      <div class="status">${v.reason} · ${v.source_lang}</div></div>`;
    const star = document.createElement("button");
    star.className = "secondary"; star.style.padding = "4px 10px";
    star.textContent = v.starred ? "★ Starred" : "☆ Star";
    star.onclick = async () => {
      await fetch(`/api/v1/vocab/${v.id}`, { method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ starred: !v.starred }) });
      loadVocab();
    };
    row.appendChild(star);
    $("list").appendChild(row);
  });
}

async function loadHistory() {
  const items = await (await fetch("/api/v1/sessions?limit=100")).json();
  $("list").innerHTML = items.length ? "" : "<p class='status'>No sessions yet.</p>";
  items.forEach(s => {
    const row = document.createElement("div");
    row.className = "card"; row.style.marginBottom = "10px";
    row.innerHTML = `<div class="tag">${s.created_at} · ${s.source_lang}→${s.target_lang} · ${s.input_source}</div>`;

    const wrap = document.createElement("div");
    wrap.style.display = "flex"; wrap.style.gap = "12px"; wrap.style.marginTop = "8px"; wrap.style.alignItems = "flex-start";

    // First-frame thumbnail; click to play the take (video never leaves the Mac).
    const media = document.createElement("div");
    media.style.flex = "0 0 auto";
    const img = document.createElement("img");
    img.src = `/api/v1/sessions/${s.id}/thumb`;
    img.alt = "recording";
    img.style.cssText = "width:168px;border-radius:8px;cursor:pointer;display:block;background:#0b0d12;";
    img.onerror = () => { img.style.display = "none"; };
    img.onclick = () => {
      const v = document.createElement("video");
      v.src = `/api/v1/sessions/${s.id}/video`;
      v.controls = true; v.autoplay = true; v.playsInline = true;
      v.style.cssText = "width:280px;border-radius:8px;display:block;";
      media.replaceChild(v, img);
      v.play().catch(() => {});
    };
    media.appendChild(img);

    const text = document.createElement("div");
    text.style.flex = "1"; text.style.minWidth = "0";
    text.innerHTML = `<div class="text">${s.original_text || ""}</div>
      <div class="text" style="color:#b9f3d0;">${s.refined_text || ""}</div>
      <div class="status">lip: ${s.vsr_raw_text || "—"}</div>`;

    wrap.appendChild(media);
    wrap.appendChild(text);
    row.appendChild(wrap);
    $("list").appendChild(row);
  });
}

function refresh() { mode === "vocab" ? loadVocab() : loadHistory(); }
$("tabVocab").onclick = () => { mode = "vocab"; $("tabVocab").className = "";
  $("tabHistory").className = "secondary"; refresh(); };
$("tabHistory").onclick = () => { mode = "history"; $("tabHistory").className = "";
  $("tabVocab").className = "secondary"; refresh(); };
$("search").oninput = () => { if (mode === "vocab") loadVocab(); };
$("starredOnly").onchange = () => { if (mode === "vocab") loadVocab(); };
refresh();
