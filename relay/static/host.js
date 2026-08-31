function spark(svg, hist) {
  if (!svg) return;
  const w = 60;
  const h = 18;
  const vals = Array.isArray(hist) ? hist : [];
  if (!vals.length) {
    svg.innerHTML = "";
    return;
  }
  const pts = vals.map((v, i) => {
    const x = vals.length <= 1 ? 0 : (i / (vals.length - 1)) * w;
    const y = h - 0.6 - (Math.max(0, Math.min(100, Number(v) || 0)) / 100) * (h - 1.2);
    return [x, y];
  });
  const line = pts.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
  const last = pts[pts.length - 1];
  const area = `0,${h} ${line} ${last[0].toFixed(2)},${h}`;
  svg.innerHTML = `<polygon points="${area}" /><polyline points="${line}" />`;
}

function paintCores(cores) {
  const box = document.getElementById("cpu-cores");
  if (!box) return;
  const rows = Array.isArray(cores) ? cores : [];
  box.innerHTML = rows.map((c, i) => {
    const pct = Math.max(0, Math.min(100, Number(c && c.cpu) || 0));
    return `<div class="core"><span>${i}</span><span class="bar"><i class="${pct >= 85 ? "hot" : ""}" style="width:${pct.toFixed(0)}%"></i></span><strong>${Math.round(pct)}%</strong></div>`;
  }).join("");
}

function paintHost(s) {
  const host = (s && s.host) || {};
  const cpuEl = document.getElementById("cpu-pct");
  const memEl = document.getElementById("mem-pct");
  if (!cpuEl || !memEl) return;
  const cpu = Number(host.cpu);
  const mem = Number(host.mem);
  cpuEl.textContent = Number.isFinite(cpu) ? `${Math.round(cpu)}%` : "—";
  memEl.textContent = Number.isFinite(mem) ? `${Math.round(mem)}%` : "—";
  cpuEl.classList.toggle("hot", Number.isFinite(cpu) && cpu >= 85);
  memEl.classList.toggle("hot", Number.isFinite(mem) && mem >= 90);
  spark(document.getElementById("cpu-spark"), host.cpuHist);
  spark(document.getElementById("mem-spark"), host.memHist);
  paintCores(host.cores);
}

function paintPill(s) {
  const pill = document.getElementById("pill");
  if (!pill || !s) return;
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
}

function paintRumbleLink(s) {
  const a = document.getElementById("rumble-live-link");
  if (!a) return;
  const url = (s && s.rumblePageUrl) || "";
  if (!url) {
    a.hidden = true;
    a.removeAttribute("href");
    return;
  }
  a.hidden = false;
  a.href = url;
}

async function tickHost() {
  try {
    const res = await fetch("/api/status");
    if (!res.ok) return;
    const s = await res.json();
    paintHost(s);
    paintPill(s);
    paintRumbleLink(s);
  } catch {
    /* ignore */
  }
}

if (document.body.dataset.hostTick === "1") {
  tickHost();
  setInterval(tickHost, 1000);
}
