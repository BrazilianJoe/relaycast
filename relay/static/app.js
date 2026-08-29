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
  if (!url || url === lastHls) return;
  lastHls = url;
  if (hls) {
    hls.destroy();
    hls = null;
  }
  if (window.Hls && Hls.isSupported()) {
    hls = new Hls({ liveDurationInfinity: true });
    hls.loadSource(url);
    hls.attachMedia(video);
  } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = url;
  }
}

function card(d) {
  const state = d.pushing ? "pushing" : d.last_error ? "error" : "idle";
  const dot = d.pushing ? "push" : d.last_error && d.enabled ? "err" : "";
  const meta = d.pushing
    ? "copying to platform"
    : d.last_error
      ? d.last_error
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
        <label class="toggle">
          <span class="dot ${dot}"></span>
          <input type="checkbox" data-en="${esc(d.id)}" ${d.enabled ? "checked" : ""} />
          ${state}
        </label>
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
  const enabled = document.querySelector(`[data-en="${id}"]`).checked;
  const body = { ingest, enabled };
  if (key) body.key = key;
  await api(`/api/destinations/${id}`, { method: "PATCH", body: JSON.stringify(body) });
  await tick();
}

async function tick() {
  const s = await api("/api/status");
  const pill = $("#pill");
  pill.textContent = s.publishing ? "live" : "standby";
  pill.className = "pill " + (s.publishing ? "on" : "off");
  $("#st-live").textContent = s.publishing ? `live · ${s.path}` : "no publisher";
  $("#st-tracks").textContent = (s.tracks || []).join(", ") || "—";
  $("#st-mtx").textContent = s.mediamtx ? "mediamtx up" : "waiting for mediamtx";
  $("#rtmpUrl").textContent = s.rtmpUrl;
  $("#srtUrl").textContent = s.srtUrl;
  document.querySelector("[data-copy=rtmp]").onclick = () => copy(s.rtmpUrl);
  document.querySelector("[data-copy=srt]").onclick = () => copy(s.srtUrl);
  if (s.publishing) preview(s.hlsUrl);
  const focus = document.activeElement;
  const editing = focus && focus.matches("input");
  if (!editing) {
    $("#dest-list").innerHTML = s.destinations.map(card).join("");
    $("#dest-list").querySelectorAll("[data-save]").forEach((btn) => {
      btn.onclick = () => save(btn.dataset.save).catch((e) => alert(e.message));
    });
    $("#dest-list").querySelectorAll("[data-en]").forEach((box) => {
      box.onchange = () => save(box.dataset.en).catch((e) => {
        box.checked = !box.checked;
        alert(e.message);
      });
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
