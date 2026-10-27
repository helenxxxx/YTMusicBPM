"use strict";

// ------------------------------------------------------------------ helpers
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  let data = {};
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) throw new Error(data.error || `Something went wrong (${res.status}).`);
  return data;
}

function fmtDuration(totalSec) {
  const m = Math.round(totalSec / 60);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
}
function fmtTrackTime(sec) {
  if (!sec) return "";
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}
function fmtAgo(ts) {
  if (!ts) return "";
  const s = Date.now() / 1000 - ts;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} minutes ago`;
  if (s < 86400) return `${Math.round(s / 3600)} hours ago`;
  const d = Math.round(s / 86400);
  return d === 1 ? "yesterday" : `${d} days ago`;
}
function num(n) { return Number.isInteger(n) ? String(n) : n.toFixed(1); }

let toastTimer;
function toast(content, { error = false, ms = 6000 } = {}) {
  const el = $("#toast");
  el.replaceChildren(...(Array.isArray(content) ? content : [content]));
  el.classList.toggle("error", error);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), ms);
}
function showError(sel, msg) {
  const el = $(sel);
  el.textContent = msg || "";
  el.hidden = !msg;
}

// -------------------------------------------------------------------- state
const state = {
  status: null,
  tracks: [],
  prefs: { cadence: 170, tolerance: 3, half: true, order: "bpm", privacy: "PRIVATE" },
  excluded: new Set(),
  tab: "matches",
  search: "",
  shuffleKey: new Map(),
  nameEdited: false,
  forceSetup: { key: false, ytm: false },
};

function matchKind(bpm, cadence, tol, half) {
  if (bpm == null) return null;
  if (Math.abs(bpm - cadence) <= tol) return "full";
  if (half && Math.abs(bpm - cadence / 2) <= tol / 2) return "half";
  return null;
}

function classify() {
  const { cadence, tolerance, half } = state.prefs;
  return state.tracks.map((t, i) => ({ ...t, idx: i, kind: matchKind(t.bpm, cadence, tolerance, half) }));
}

function ordered(list) {
  const order = state.prefs.order;
  const copy = list.slice();
  if (order === "library") return copy.sort((a, b) => a.idx - b.idx);
  if (order === "shuffle") {
    for (const t of copy) if (!state.shuffleKey.has(t.video_id)) state.shuffleKey.set(t.video_id, Math.random());
    return copy.sort((a, b) => state.shuffleKey.get(a.video_id) - state.shuffleKey.get(b.video_id));
  }
  // by tempo: half-time songs sorted by the step rate they give you (bpm × 2)
  const eff = (t) => (t.kind === "half" ? t.bpm * 2 : t.bpm ?? 0);
  return copy.sort((a, b) => eff(a) - eff(b) || a.idx - b.idx);
}

// ------------------------------------------------------------------ prefs
let prefsTimer;
function savePrefs() {
  clearTimeout(prefsTimer);
  prefsTimer = setTimeout(() => api("/api/settings", { prefs: state.prefs }).catch(() => {}), 500);
}

function syncControls() {
  const p = state.prefs;
  $("#in-cadence").value = p.cadence;
  $("#out-cadence").textContent = p.cadence;
  $("#in-tol").value = p.tolerance;
  $("#out-tol").textContent = `± ${p.tolerance} BPM`;
  $("#in-half").checked = p.half;
  $("#in-order").value = p.order;
  $("#in-privacy").value = p.privacy;
  $("#btn-reshuffle").hidden = p.order !== "shuffle";
  $("#half-hint").textContent = `e.g. ${num(p.cadence / 2)} BPM, two steps per beat`;

  const lo = p.cadence - p.tolerance, hi = p.cadence + p.tolerance;
  const rd = $("#range-readout");
  rd.innerHTML = "";
  const line1 = document.createElement("div");
  line1.innerHTML = `<span class="swatch full"></span>Songs at <b>${lo}–${hi}</b> BPM`;
  rd.append(line1);
  if (p.half) {
    const line2 = document.createElement("div");
    line2.innerHTML = `<span class="swatch half"></span>Half-time at <b>${num(lo / 2)}–${num(hi / 2)}</b> BPM`;
    rd.append(line2);
  }
  if (!state.nameEdited) $("#in-name").value = `Running ${p.cadence} spm`;
}

function setPref(key, value) {
  state.prefs[key] = value;
  syncControls();
  render();
  savePrefs();
}

// ------------------------------------------------------------------ render
function render() {
  const all = classify();
  const matches = all.filter((t) => t.kind);
  const missing = all.filter((t) => t.bpm == null);

  $("#count-matches").textContent = matches.length;
  $("#count-missing").textContent = missing.length;
  $("#count-all").textContent = all.length;
  $$(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === state.tab));

  const base = state.tab === "matches" ? matches : state.tab === "missing" ? missing : all;
  const q = state.search.trim().toLowerCase();
  const shown = ordered(base).filter((t) => !q || `${t.title} ${t.artist}`.toLowerCase().includes(q));

  // selection & summary (always about the matches, whatever tab you're on)
  const selected = ordered(matches).filter((t) => !state.excluded.has(t.video_id));
  const secs = selected.reduce((s, t) => s + (t.duration || 0), 0);
  $("#sum-songs").textContent = `${selected.length} song${selected.length === 1 ? "" : "s"}`;
  $("#sum-time").textContent = secs ? fmtDuration(secs) : "—";
  $("#btn-create").disabled = selected.length === 0;
  const ca = $("#check-all");
  ca.checked = selected.length === matches.length && matches.length > 0;
  ca.indeterminate = selected.length > 0 && selected.length < matches.length;
  $("#select-summary").textContent =
    matches.length === 0 ? "No matches" :
    selected.length === matches.length ? `All ${matches.length} selected` :
    `${selected.length} of ${matches.length} selected`;
  $("#list-tools").hidden = state.tab !== "matches" || matches.length === 0;

  // list
  const list = $("#song-list");
  list.replaceChildren(...shown.map(rowFor));

  // empty states
  const empty = $("#empty");
  empty.replaceChildren();
  let show = shown.length === 0;
  if (show) {
    if (state.tracks.length === 0) {
      empty.append(emptyBlock("No songs scanned yet",
        "Pick where your songs come from on the left, then scan. Cadence looks up each song's tempo once and remembers it.",
        ["Scan liked songs", () => $("#btn-scan").click()]));
    } else if (q) {
      empty.append(emptyBlock("Nothing matches that filter", "Try a different word, or clear the filter box."));
    } else if (state.tab === "matches") {
      empty.append(emptyBlock(`No songs at ${state.prefs.cadence} spm yet`,
        "Try more wiggle room, switch on half-time songs, or add BPMs for the songs that need one.",
        missing.length ? [`Fill in ${missing.length} missing BPMs`, () => setTab("missing")] : null));
    } else if (state.tab === "missing") {
      empty.append(emptyBlock("Every song has a BPM", "Nice. Nothing to fill in here."));
    }
  }
  empty.hidden = !show;
  list.hidden = show;
}

function emptyBlock(title, text, action) {
  const div = document.createElement("div");
  const h = document.createElement("h3"); h.textContent = title;
  const p = document.createElement("p"); p.textContent = text;
  div.append(h, p);
  if (action) {
    const b = document.createElement("button");
    b.className = "btn btn-secondary";
    b.textContent = action[0];
    b.addEventListener("click", action[1]);
    div.append(b);
  }
  return div;
}

function rowFor(t) {
  const li = document.createElement("li");
  const inMatches = state.tab === "matches";
  li.className = "song" + (inMatches ? "" : " no-check") + (inMatches && state.excluded.has(t.video_id) ? " excluded" : "");

  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.checked = !state.excluded.has(t.video_id);
  cb.setAttribute("aria-label", `Include ${t.title}`);
  cb.addEventListener("change", () => {
    if (cb.checked) state.excluded.delete(t.video_id); else state.excluded.add(t.video_id);
    render();
  });

  const pill = document.createElement("button");
  pill.className = "bpm " + (t.bpm == null ? "none" : t.kind || "");
  pill.title = t.bpm == null ? "Add a BPM" : "Edit BPM";
  if (t.bpm == null) pill.textContent = "add";
  else {
    pill.textContent = t.bpm;
    if (t.kind === "half") { const s = document.createElement("small"); s.textContent = "½"; pill.append(s); }
  }
  pill.addEventListener("click", (e) => openBpmPopover(t, e.currentTarget));

  const text = document.createElement("div");
  text.className = "song-text";
  const ti = document.createElement("div"); ti.className = "song-title"; ti.textContent = t.title;
  const ar = document.createElement("div"); ar.className = "song-artist"; ar.textContent = t.artist;
  text.append(ti, ar);

  const meta = document.createElement("span");
  meta.className = "song-meta";
  if (t.bpm_source === "manual") {
    const tag = document.createElement("span");
    tag.className = "song-tag"; tag.textContent = "edited"; tag.title = "You set this BPM";
    meta.append(tag);
  }
  const dur = document.createElement("span");
  dur.className = "song-dur";
  dur.textContent = fmtTrackTime(t.duration);
  meta.append(dur);

  li.append(cb, pill, text, meta);
  return li;
}

function setTab(tab) { state.tab = tab; render(); }

// ---------------------------------------------------------- BPM popover
let popTrack = null;
function openBpmPopover(t, anchor) {
  popTrack = t;
  const pop = $("#bpm-pop");
  $("#pop-title").textContent = `${t.title} · ${t.artist}`;
  $("#pop-input").value = t.bpm ?? "";
  $("#pop-lookup").href = "https://www.google.com/search?q=" + encodeURIComponent(`${t.artist} ${t.title} bpm`);
  $("#pop-clear").hidden = t.bpm == null;
  pop.hidden = false;
  const r = anchor.getBoundingClientRect();
  const w = pop.offsetWidth, h = pop.offsetHeight;
  let left = Math.min(r.left, window.innerWidth - w - 12);
  let top = r.bottom + 8;
  if (top + h > window.innerHeight - 12) top = r.top - h - 8;
  pop.style.left = `${Math.max(12, left)}px`;
  pop.style.top = `${Math.max(12, top)}px`;
  $("#pop-input").focus();
  $("#pop-input").select();
}
function closePopover() { $("#bpm-pop").hidden = true; popTrack = null; }

async function saveBpm(value) {
  if (!popTrack) return;
  const t = popTrack;
  try {
    const res = await api(`/api/tracks/${encodeURIComponent(t.video_id)}/bpm`, { bpm: value });
    const tr = state.tracks.find((x) => x.video_id === t.video_id);
    if (tr) { tr.bpm = res.bpm ?? null; tr.bpm_source = "manual"; }
    closePopover();
    render();
  } catch (e) { toast(e.message, { error: true }); }
}

// ------------------------------------------------------------------- scan
let pollTimer;
async function startScan() {
  showError("#err-scan", "");
  const sel = $("#in-source");
  try {
    await api("/api/scan", { source: sel.value, source_title: sel.selectedOptions[0]?.textContent || "" });
  } catch (e) { showError("#err-scan", e.message); return; }
  state.excluded.clear();
  setScanning(true);
  poll();
}

function setScanning(on) {
  $("#scan-progress").hidden = !on;
  $("#btn-scan").disabled = on;
  $("#in-source").disabled = on;
}

async function poll() {
  clearTimeout(pollTimer);
  let job;
  try { job = await api("/api/scan"); } catch (_) { pollTimer = setTimeout(poll, 1500); return; }
  const fill = $("#bar-fill");
  if (job.state === "running") {
    setScanning(true);
    if (job.total > 0) {
      fill.classList.remove("indeterminate");
      fill.style.width = `${Math.round((job.done / job.total) * 100)}%`;
      $("#scan-text").textContent = `${job.done} of ${job.total} new songs · ${job.current}`;
    } else {
      fill.classList.add("indeterminate");
      $("#scan-text").textContent = job.current || "Reading your songs…";
    }
    pollTimer = setTimeout(poll, 700);
    return;
  }
  setScanning(false);
  fill.style.width = "0";
  if (job.state === "error") showError("#err-scan", job.error);
  if (job.state === "done" || job.state === "stopped") {
    await refreshStatus();
    await loadTracks();
    if (job.state === "stopped") toast("Scan stopped. Tempos found so far are saved, so the next scan picks up where it left off.");
  }
}

function updateScanInfo() {
  const lib = state.status?.library || {};
  const el = $("#scan-info");
  if (!lib.scanned_at) { el.textContent = "Scanning looks up each song once; after that it's instant."; return; }
  const known = state.tracks.filter((t) => t.bpm != null).length;
  el.textContent = `${lib.source_title || "Songs"} scanned ${fmtAgo(lib.scanned_at)}. Tempo known for ${known} of ${state.tracks.length}.`;
  $("#btn-scan").textContent = "Scan again";
}

// --------------------------------------------------------------- loaders
async function refreshStatus() {
  state.status = await api("/api/status");
  const s = state.status;
  $("#demo-badge").hidden = !s.demo;
  $("#chip-ytm").className = "chip " + (s.ytm_connected ? "on" : "off");
  $("#chip-key").className = "chip " + (s.api_key_set ? "on" : "off");
  $("#chip-ytm").title = s.ytm_connected ? "YouTube Music connected" : "YouTube Music not connected";
  $("#chip-key").title = s.api_key_set ? "GetSongBPM key saved" : "No GetSongBPM key";
  return s;
}

async function loadTracks() {
  const { tracks } = await api("/api/tracks");
  state.tracks = tracks;
  updateScanInfo();
  render();
}

async function loadSources() {
  const sel = $("#in-source");
  try {
    const { sources } = await api("/api/sources");
    sel.replaceChildren(...sources.map((s) => {
      const o = document.createElement("option");
      o.value = s.id;
      o.textContent = s.title;
      return o;
    }));
    const last = state.status?.library?.source;
    if (last && sources.some((s) => s.id === last)) sel.value = last;
  } catch (e) {
    showError("#err-scan", e.message);
  }
}

// ----------------------------------------------------------------- setup
function renderSetup() {
  const s = state.status;
  const keyDone = s.api_key_set && !state.forceSetup.key;
  const ytmDone = s.ytm_connected && !state.forceSetup.ytm;
  $("#step-key").classList.toggle("done", keyDone);
  $("#step-ytm").classList.toggle("done", ytmDone);
  $("#btn-setup-done").disabled = !(s.api_key_set && s.ytm_connected);
}

function showView(name) {
  $("#view-setup").hidden = name !== "setup";
  $("#view-main").hidden = name !== "main";
  window.scrollTo(0, 0);
}

async function enterMain() {
  state.forceSetup = { key: false, ytm: false };
  showView("main");
  syncControls();
  await loadSources();
  await loadTracks();
  poll(); // pick up a scan that was already running
}

// ----------------------------------------------------------------- wiring
function wire() {
  // cadence controls
  $("#in-cadence").addEventListener("input", (e) => setPref("cadence", +e.target.value));
  $$(".step-btn").forEach((b) => b.addEventListener("click", () => {
    setPref("cadence", Math.max(140, Math.min(200, state.prefs.cadence + +b.dataset.step)));
  }));
  $("#in-tol").addEventListener("input", (e) => setPref("tolerance", +e.target.value));
  $("#in-half").addEventListener("change", (e) => setPref("half", e.target.checked));
  $("#in-order").addEventListener("change", (e) => setPref("order", e.target.value));
  $("#in-privacy").addEventListener("change", (e) => setPref("privacy", e.target.value));
  $("#btn-reshuffle").addEventListener("click", () => { state.shuffleKey.clear(); render(); });
  $("#in-name").addEventListener("input", () => { state.nameEdited = $("#in-name").value.trim() !== ""; });

  // tabs, search, select all
  $$(".tab").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
  $("#in-search").addEventListener("input", (e) => { state.search = e.target.value; render(); });
  $("#check-all").addEventListener("change", (e) => {
    const matches = classify().filter((t) => t.kind);
    if (e.target.checked) state.excluded.clear();
    else matches.forEach((t) => state.excluded.add(t.video_id));
    render();
  });

  // scan
  $("#btn-scan").addEventListener("click", startScan);
  $("#btn-stop").addEventListener("click", () => api("/api/scan/stop", {}).catch(() => {}));

  // create playlist
  $("#btn-create").addEventListener("click", async () => {
    const btn = $("#btn-create");
    const matches = ordered(classify().filter((t) => t.kind)).filter((t) => !state.excluded.has(t.video_id));
    const name = $("#in-name").value.trim() || `Running ${state.prefs.cadence} spm`;
    const p = state.prefs;
    btn.disabled = true;
    btn.textContent = "Saving…";
    try {
      const res = await api("/api/playlist", {
        name,
        privacy: p.privacy,
        video_ids: matches.map((t) => t.video_id),
        description: `${matches.length} songs for ${p.cadence} spm (${p.cadence - p.tolerance}–${p.cadence + p.tolerance} BPM${p.half ? ", plus half-time" : ""}).`,
      });
      const a = document.createElement("a");
      a.href = res.url; a.target = "_blank"; a.rel = "noopener";
      a.textContent = "Open in YouTube Music ↗";
      const span = document.createElement("span");
      span.textContent = `Saved “${name}” with ${matches.length} songs.`;
      toast(state.status.demo ? [span] : [span, a], { ms: 12000 });
    } catch (e) {
      toast(e.message, { error: true });
    } finally {
      btn.textContent = "Save to YouTube Music";
      render();
    }
  });

  // popover
  $("#pop-save").addEventListener("click", () => saveBpm($("#pop-input").value));
  $("#pop-clear").addEventListener("click", () => saveBpm(null));
  $("#pop-cancel").addEventListener("click", closePopover);
  $("#pop-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") saveBpm($("#pop-input").value);
    if (e.key === "Escape") closePopover();
  });
  $$("#bpm-pop [data-mult]").forEach((b) => b.addEventListener("click", () => {
    const v = parseFloat($("#pop-input").value);
    if (!isNaN(v)) $("#pop-input").value = Math.round(v * parseFloat(b.dataset.mult));
  }));
  document.addEventListener("mousedown", (e) => {
    const pop = $("#bpm-pop");
    if (!pop.hidden && !pop.contains(e.target) && !e.target.closest(".bpm")) closePopover();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePopover(); });

  // setup forms
  $("#form-key").addEventListener("submit", async (e) => {
    e.preventDefault();
    showError("#err-key", "");
    try {
      await api("/api/settings", { api_key: $("#input-key").value });
      $("#input-key").value = "";
      state.forceSetup.key = false;
      await refreshStatus();
      renderSetup();
    } catch (err) { showError("#err-key", err.message); }
  });
  $("#form-ytm").addEventListener("submit", async (e) => {
    e.preventDefault();
    showError("#err-ytm", "");
    const btn = $("#btn-connect");
    btn.disabled = true; btn.textContent = "Checking…";
    try {
      await api("/api/connect", { headers: $("#input-headers").value });
      $("#input-headers").value = "";
      state.forceSetup.ytm = false;
      await refreshStatus();
      renderSetup();
    } catch (err) { showError("#err-ytm", err.message); }
    finally { btn.disabled = false; btn.textContent = "Connect"; }
  });
  $("#btn-setup-done").addEventListener("click", enterMain);

  // settings dialog
  const dlg = $("#dlg-settings");
  $("#btn-settings").addEventListener("click", () => {
    const s = state.status;
    $("#set-key-state").textContent = s.demo ? "Not needed in demo mode" : s.api_key_set ? "Saved" : "Not set";
    $("#set-ytm-state").textContent = s.demo ? "Using the demo library" : s.ytm_connected ? "Connected" : "Not connected";
    dlg.showModal();
  });
  $("#set-change-key").addEventListener("click", () => {
    dlg.close(); state.forceSetup.key = true; renderSetup(); showView("setup");
  });
  $("#set-reconnect").addEventListener("click", () => {
    dlg.close(); state.forceSetup.ytm = true; renderSetup(); showView("setup");
  });
}

// -------------------------------------------------------------------- boot
(async function boot() {
  wire();
  try {
    const s = await refreshStatus();
    if (s.prefs && typeof s.prefs === "object") Object.assign(state.prefs, s.prefs);
    if (s.api_key_set && s.ytm_connected) await enterMain();
    else { renderSetup(); showView("setup"); }
  } catch (e) {
    toast(`Couldn't reach the Cadence server. Is run.py still running? (${e.message})`, { error: true, ms: 60000 });
  }
})();
