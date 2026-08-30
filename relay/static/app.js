const $ = (sel) => document.querySelector(sel);

let lastHls = "";
let hls;

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
  navigator.clipboard.writeText(text);
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
  return `http://${location.hostname}:8888/${path}/index.m3u8`;
}

function fmtBytes(n) {
  const x = Number(n) || 0;
  if (x < 1024) return `${x} B`;
  if (x < 1024 * 1024) return `${(x / 1024).toFixed(1)} KB`;
  return `${(x / (1024 * 1024)).toFixed(1)} MB`;
}

function card(d) {
  const sending = d.sending || "off";
  const intent = !d.enabled ? "off" : d.hold ? "hold" : "live";
  const dot = sending === "hold" ? "hold" : d.pushing ? "push" : d.last_error && d.enabled ? "err" : "";
  const meta = sending === "hold"
    ? (d.hold ? "holding · slate" : "holding · Action! down")
    : d.pushing
      ? (d.transcode ? "transcoding for Kick" : "copying to platform")
      : d.last_error
        ? d.last_error
        : sending === "live"
          ? (d.transcode ? "transcoding for Kick…" : "connecting…")
          : d.enabled
            ? "waiting for ingest"
            : "off";

  const keyPh = d.has_key ? `saved ·${d.key_tail}` : "stream key";
  const del = d.builtin ? "" : `<button data-del="${esc(d.id)}">remove</button>`;
  return `
    <article class="card" data-id="${esc(d.id)}">
      <div class="card-top">
        <div>
          <div class="name">${esc(d.name)}</div>
          <div class="meta ${d.last_error && d.enabled ? "err" : ""}">${esc(meta)}</div>
        </div>
        <div class="modes">
          <span class="dot ${dot}"></span>
          <button type="button" data-mode="off" data-id="${esc(d.id)}" class="${intent === "off" ? "on" : ""}">off</button>
          <button type="button" data-mode="live" data-id="${esc(d.id)}" class="${intent === "live" ? "on" : ""}">live</button>
          <button type="button" data-mode="hold" data-id="${esc(d.id)}" class="${intent === "hold" ? "on" : ""}">hold</button>
        </div>
      </div>
      <div class="fields">
        <label>ingest URL
          <input type="text" data-ingest="${esc(d.id)}" value="${esc(d.ingest || "")}" placeholder="rtmp(s)://…" />
        </label>
        <label>stream key
          <input type="password" data-key="${esc(d.id)}" value="" placeholder="${esc(keyPh)}" autocomplete="off" />
        </label>
      </div>
      <div class="row-actions">
        <button data-save="${esc(d.id)}">save</button>
        ${d.docs ? `<a href="${esc(d.docs)}" target="_blank" rel="noreferrer"><button type="button">dashboard</button></a>` : ""}
        ${del}
      </div>
    </article>`;
}

async function save(id) {
  const ingest = document.querySelector(`[data-ingest="${id}"]`).value;
  const key = document.querySelector(`[data-key="${id}"]`).value;
  const body = { ingest };
  if (key) body.key = key;
  await api(`/api/destinations/${id}`, { method: "PATCH", body: JSON.stringify(body) });
  await tick();
}

async function setMode(id, mode) {
  await api(`/api/destinations/${id}`, { method: "PATCH", body: JSON.stringify({ mode }) });
  await tick();
}

function showSlate(s) {
  const img = $("#standby-img");
  const vid = $("#standby-vid");
  const clear = $("#standby-clear");
  const meta = $("#standby-meta");
  const auto = $("#auto-hold");
  if (document.activeElement !== auto) auto.checked = !!s.autoHold;
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
  const pill = $("#pill");
  if (s.holding) {
    pill.textContent = "hold";
    pill.className = "pill hold";
  } else if (s.publishing) {
    pill.textContent = "live";
    pill.className = "pill on";
  } else {
    pill.textContent = "standby";
    pill.className = "pill off";
  }
  $("#st-live").textContent = s.publishing ? `live · ${s.path}` : "no publisher";
  $("#st-tracks").textContent = (s.tracks || []).join(", ") || "—";
  $("#st-bytes").textContent = s.publishing ? fmtBytes(s.bytesReceived) : "—";
  $("#st-mtx").textContent = s.mediamtx ? "mediamtx up" : "waiting for mediamtx";
  $("#rtmpServer").textContent = s.rtmpServer;
  $("#rtmpKey").textContent = s.rtmpKey;
  $("#srtUrl").textContent = s.srtUrl;
  document.querySelector("[data-copy=rtmpServer]").onclick = () => copy(s.rtmpServer);
  document.querySelector("[data-copy=rtmpKey]").onclick = () => copy(s.rtmpKey);
  document.querySelector("[data-copy=srt]").onclick = () => copy(s.srtUrl);
  if (s.publishing) preview(previewUrl(s));
  else preview("");
  showSlate(s);
  const focus = document.activeElement;
  const editing = focus && focus.matches("input[type=text], input[type=password]");
  if (!editing) {
    $("#dest-list").innerHTML = s.destinations.map(card).join("");
    $("#dest-list").querySelectorAll("[data-save]").forEach((btn) => {
      btn.onclick = () => save(btn.dataset.save).catch((e) => alert(e.message));
    });
    $("#dest-list").querySelectorAll("[data-mode]").forEach((btn) => {
      btn.onclick = () => setMode(btn.dataset.id, btn.dataset.mode).catch((e) => alert(e.message));
    });
    $("#dest-list").querySelectorAll("[data-del]").forEach((btn) => {
      btn.onclick = async () => {
        if (!confirm("remove this destination?")) return;
        await api(`/api/destinations/${btn.dataset.del}`, { method: "DELETE" });
        tick();
      };
    });
  }
}

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
setInterval(tick, 1500);

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
