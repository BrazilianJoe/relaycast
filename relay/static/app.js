const $ = (sel) => document.querySelector(sel);

let lastHls = "";
let hls;
let lastStatus = null;
let connOpen = false;
const editing = new Set();
const editData = {};

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "content-type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (res.status === 401) {
    document.body.innerHTML = "<p style='padding:2rem'>Unauthorized. Refresh and enter the admin password.</p>";
    throw new Error("401");
  }
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

function copy(text) {
  if (text) navigator.clipboard.writeText(text);
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function preview(url) {
  const video = $("#preview");
  if (!url) {
    lastHls = "";
    if (hls) {
      hls.destroy();
      hls = null;
    }
    video.removeAttribute("src");
    video.load();
    return;
  }
  if (url === lastHls) return;
  lastHls = url;
  if (hls) {
    hls.destroy();
    hls = null;
  }
  if (window.Hls && Hls.isSupported()) {
    hls = new Hls({ liveDurationInfinity: true, lowLatencyMode: false });
    hls.loadSource(url);
    hls.attachMedia(video);
  } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = url;
  }
}

function previewUrl(s) {
  const path = (s.path || "").replace(/^\/+/, "");
  if (!s.publishing || !path) return "";
  return `/hls/${path}/index.m3u8`;
}

function fmtBytes(n) {
  const x = Number(n) || 0;
  if (x < 1024) return `${x} B`;
  if (x < 1024 * 1024) return `${(x / 1024).toFixed(1)} KB`;
  return `${(x / (1024 * 1024)).toFixed(1)} MB`;
}

function intentOf(d) {
  return !d.enabled ? "off" : d.hold ? "hold" : "live";
}

function dotOf(d) {
  const sending = d.sending || "off";
  if (sending === "hold") return "hold";
  if (d.pushing) return "push";
  if (d.last_error && d.enabled) return "err";
  return "";
}

function kickSizeOf(s, d) {
  const fromDest = d && d.kickTranscode;
  const raw = fromDest || (s && s.kickTranscode) || "720p60";
  if (raw === "copy" || raw === "off") return "copy";
  return raw === "1080p60" ? "1080p60" : "720p60";
}

function metaOf(d) {
  const sending = d.sending || "off";
  const size = kickSizeOf(lastStatus, d);
  if (sending === "hold") return d.hold ? "holding · slate" : "holding · Action! down";
  if (d.pushing) return d.transcode ? `transcoding Kick · ${size}` : "copying to platform";
  if (d.last_error && d.enabled) return d.last_error;
  if (sending === "live") return d.transcode ? `transcoding Kick · ${size}…` : "connecting…";
  if (!d.has_ingest || !d.has_key) return "set URL and key in edit";
  if (d.enabled) return "waiting for ingest";
  return "off";
}

function fillConn(s) {
  if (!s || !connOpen) return;
  $("#rtmpServer").textContent = s.rtmpServer || "";
}

function editFields(d) {
  const ed = editData[d.id] || {};
  const keyPh = d.has_key ? "stream key saved — paste to replace" : "stream key";
  const del = d.builtin ? "" : `<button type="button" data-del="${esc(d.id)}">remove</button>`;
  const docs = ed.docs || d.docs || "";
  const help = ed.help ? `<p class="hint">${esc(ed.help)}</p>` : "";
  return `
    <div class="edit-panel">
      ${help}
      <div class="fields">
        <label>ingest URL
          <input type="text" data-ingest="${esc(d.id)}" value="${esc(ed.ingest || "")}" placeholder="rtmp(s)://…" autocomplete="off" />
        </label>
        <label>stream key
          <input type="password" data-key="${esc(d.id)}" value="" placeholder="${esc(keyPh)}" autocomplete="off" />
        </label>
        ${d.id === "rumble" ? `<label>live page
          <input type="text" data-page="${esc(d.id)}" value="${esc(ed.page_url || "")}" placeholder="https://rumble.com/user/…/live" autocomplete="off" />
        </label>` : ""}
      </div>
      <div class="row-actions">
        <button type="button" data-save="${esc(d.id)}">save</button>
        ${docs ? `<a href="${esc(docs)}" target="_blank" rel="noreferrer"><button type="button">dashboard</button></a>` : ""}
        ${del}
      </div>
    </div>`;
}

function card(d) {
  const intent = intentOf(d);
  const open = editing.has(d.id);
  const size = kickSizeOf(lastStatus, d);
  const sizes = d.id === "kick" ? `
      <div class="sizes" role="group" aria-label="Kick encode">
        <button type="button" data-kick-size="copy" class="${size === "copy" ? "on" : ""}">copy</button>
        <button type="button" data-kick-size="720p60" class="${size === "720p60" ? "on" : ""}">720p60</button>
        <button type="button" data-kick-size="1080p60" class="${size === "1080p60" ? "on" : ""}">1080p60</button>
      </div>` : "";
  return `
    <article class="card" data-id="${esc(d.id)}">
      <div class="card-top">
        <div>
          <div class="name-row"><span class="dot ${dotOf(d)}"></span><div class="name">${esc(d.name)}</div></div>
          <div class="meta ${d.last_error && d.enabled ? "err" : ""}">${esc(metaOf(d))}</div>
        </div>
        <button type="button" class="edit-btn ${open ? "on" : ""}" data-edit="${esc(d.id)}">${open ? "close" : "edit"}</button>
      </div>
      <div class="modes">
        <button type="button" data-mode="off" data-id="${esc(d.id)}" class="${intent === "off" ? "on" : ""}">off</button>
        <button type="button" data-mode="live" data-id="${esc(d.id)}" class="${intent === "live" ? "on" : ""}">live</button>
        <button type="button" data-mode="hold" data-id="${esc(d.id)}" class="${intent === "hold" ? "on" : ""}">hold</button>
      </div>
      ${sizes}
      ${open ? editFields(d) : ""}
    </article>`;
}

function bindDest() {
  const root = $("#dest-list");
  root.querySelectorAll("[data-save]").forEach((btn) => {
    btn.onclick = () => save(btn.dataset.save).catch((e) => alert(e.message));
  });
  root.querySelectorAll("[data-mode]").forEach((btn) => {
    btn.onclick = () => setMode(btn.dataset.id, btn.dataset.mode).catch((e) => alert(e.message));
  });
  root.querySelectorAll("[data-kick-size]").forEach((btn) => {
    btn.onclick = () => setKickSize(btn.dataset.kickSize).catch((e) => alert(e.message));
  });
  root.querySelectorAll("[data-edit]").forEach((btn) => {
    btn.onclick = () => toggleEdit(btn.dataset.edit).catch((e) => alert(e.message));
  });
  root.querySelectorAll("[data-del]").forEach((btn) => {
    btn.onclick = async () => {
      if (!confirm("remove this destination?")) return;
      await api(`/api/destinations/${btn.dataset.del}`, { method: "DELETE" });
      editing.delete(btn.dataset.del);
      delete editData[btn.dataset.del];
      tick();
    };
  });
}

let lastDestIds = "";

function paintDests(s, force) {
  const root = $("#dest-list");
  const ids = (s.destinations || []).map((d) => d.id).join("\0");
  const canPatch = !force && ids === lastDestIds && root.querySelector(".card");
  if (canPatch) {
    for (const d of s.destinations) {
      const el = root.querySelector(`.card[data-id="${d.id}"]`);
      if (!el) continue;
      const meta = el.querySelector(".meta");
      const dot = el.querySelector(".dot");
      meta.textContent = metaOf(d);
      meta.classList.toggle("err", Boolean(d.last_error && d.enabled));
      if (dot) dot.className = `dot ${dotOf(d)}`;
      const intent = intentOf(d);
      el.querySelectorAll("[data-mode]").forEach((btn) => {
        btn.classList.toggle("on", btn.dataset.mode === intent);
      });
      const size = kickSizeOf(s, d);
      el.querySelectorAll("[data-kick-size]").forEach((btn) => {
        btn.classList.toggle("on", btn.dataset.kickSize === size);
      });
    }
    return;
  }
  lastDestIds = ids;
  root.innerHTML = s.destinations.map(card).join("");
  bindDest();
}

async function toggleEdit(id) {
  if (editing.has(id)) {
    editing.delete(id);
    paintDests(lastStatus, true);
    return;
  }
  editData[id] = await api(`/api/destinations/${id}`);
  editing.add(id);
  paintDests(lastStatus, true);
}

async function save(id) {
  const ingest = document.querySelector(`[data-ingest="${id}"]`).value;
  const key = document.querySelector(`[data-key="${id}"]`).value;
  const pageEl = document.querySelector(`[data-page="${id}"]`);
  const body = { ingest };
  if (key) body.key = key;
  if (pageEl) body.page_url = pageEl.value;
  await api(`/api/destinations/${id}`, { method: "PATCH", body: JSON.stringify(body) });
  editing.delete(id);
  delete editData[id];
  await tick();
}

async function setMode(id, mode) {
  await api(`/api/destinations/${id}`, { method: "PATCH", body: JSON.stringify({ mode }) });
  await tick();
}

async function setKickSize(size) {
  await api("/api/settings", { method: "PATCH", body: JSON.stringify({ kick_transcode: size }) });
  await tick();
}

function showSlate(s) {
  const img = $("#standby-img");
  const vid = $("#standby-vid");
  const clear = $("#standby-clear");
  const meta = $("#standby-meta");
  const auto = $("#auto-hold");
  if (document.activeElement !== auto) auto.checked = !!s.autoHold;
  const hint = $("#kick-hint");
  if (hint) {
    const size = kickSizeOf(s);
    hint.textContent = size === "copy"
      ? "Looping still or clip while platforms stay online. Kick copies OBS like the others (needs 2s keyframes, no B-frames)."
      : `Looping still or clip while platforms stay online. Kick live is transcoded to ${size}; everything else is copy.`;
  }
  if (!s.hasStandby) {
    img.hidden = true;
    vid.hidden = true;
    clear.hidden = true;
    meta.textContent = "No file: default STAND BY card.";
    return;
  }
  const url = `/api/standby?name=${encodeURIComponent(s.standbyName || "")}`;
  const video = /\.(mp4|webm|mov|mkv|m4v)$/i.test(s.standbyName || "");
  meta.textContent = s.standbyName;
  clear.hidden = false;
  if (video) {
    img.hidden = true;
    vid.hidden = false;
    if (vid.dataset.src !== url) {
      vid.src = url;
      vid.dataset.src = url;
    }
  } else {
    vid.hidden = true;
    img.hidden = false;
    if (img.dataset.src !== url) {
      img.src = url;
      img.dataset.src = url;
    }
  }
}

async function tick() {
  const s = await api("/api/status");
  lastStatus = s;
  paintPill(s);
  paintRumbleLink(s);
  $("#st-live").textContent = s.publishing ? "live" : "no publisher";
  $("#st-tracks").textContent = (s.tracks || []).join(", ") || "—";
  $("#st-bytes").textContent = s.publishing ? fmtBytes(s.bytesReceived) : "—";
  $("#st-mtx").textContent = s.mediamtx ? "mediamtx up" : "waiting for mediamtx";
  paintHost(s);
  fillConn(s);
  if (s.publishing) preview(previewUrl(s));
  else preview("");
  showSlate(s);
  paintDests(s, false);
}

$("#conn-toggle").onclick = () => {
  connOpen = !connOpen;
  $("#conn-panel").hidden = !connOpen;
  $("#conn-toggle").textContent = connOpen ? "hide connection" : "connection";
  fillConn(lastStatus);
};
async function copyConn(field) {
  const c = await api("/api/connection");
  copy(c[field]);
}
document.querySelector("[data-copy=rtmpServer]").onclick = () => {
  if (lastStatus && lastStatus.rtmpServer) copy(lastStatus.rtmpServer);
  else copyConn("rtmpServer").catch((e) => alert(e.message));
};
document.querySelector("[data-copy=rtmpKey]").onclick = () => copyConn("rtmpKey").catch((e) => alert(e.message));
document.querySelector("[data-copy=srt]").onclick = () => copyConn("srtUrl").catch((e) => alert(e.message));

$("#add-btn").onclick = () => $("#add-dlg").showModal();
$("#add-form").onsubmit = async (ev) => {
  if (ev.submitter && ev.submitter.id !== "add-save") return;
  ev.preventDefault();
  const fd = new FormData($("#add-form"));
  try {
    await api("/api/destinations", {
      method: "POST",
      body: JSON.stringify({
        id: fd.get("id"),
        name: fd.get("name"),
        ingest: fd.get("ingest"),
      }),
    });
    $("#add-dlg").close();
    $("#add-form").reset();
    tick();
  } catch (e) {
    alert(e.message);
  }
};

tick();
setInterval(tick, 1000);

$("#hold-all").onclick = () =>
  api("/api/hold-all", { method: "POST", body: JSON.stringify({ hold: true }) }).then(tick).catch((e) => alert(e.message));
$("#resume-all").onclick = () =>
  api("/api/hold-all", { method: "POST", body: JSON.stringify({ hold: false }) }).then(tick).catch((e) => alert(e.message));
$("#auto-hold").onchange = () =>
  api("/api/settings", { method: "PATCH", body: JSON.stringify({ auto_hold: $("#auto-hold").checked }) }).catch((e) => alert(e.message));
$("#standby-file").onchange = async (ev) => {
  const file = ev.target.files && ev.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/api/standby", { method: "POST", body: fd });
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
  if (!res.ok) alert(data.detail || res.statusText);
  ev.target.value = "";
  tick();
};
$("#standby-clear").onclick = () =>
  api("/api/standby", { method: "DELETE" }).then(tick).catch((e) => alert(e.message));
