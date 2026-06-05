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
    row.innerHTML = `<div class="line"><div class="tag">${s.created_at} · ${s.source_lang}→${s.target_lang} · ${s.input_source}</div>
      <div class="text">${s.original_text}</div>
      <div class="text" style="color:#b9f3d0;">${s.refined_text}</div>
      <div class="status">lip: ${s.vsr_raw_text || "—"}</div></div>`;
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
