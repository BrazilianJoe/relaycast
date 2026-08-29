from __future__ import annotations

import ipaddress
import os
import secrets
import threading
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import store
from fanout import Fanout, join_rtmp

PUBLISH_KEY = os.environ.get("PUBLISH_KEY", "").strip()
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
PUBLIC_HOST = os.environ.get("PUBLIC_HOST", "localhost").strip() or "localhost"
MEDIAMTX_API = os.environ.get("MEDIAMTX_API", "http://mediamtx:9997").rstrip("/")

STATIC = Path(__file__).parent / "static"
OFFLINE_GRACE = 2.5

app = FastAPI(title="relaycast", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
fanout = Fanout()

_live_path = ""
_publishing = False
_tracks: list[str] = []
_bytes_received = 0
_offline_since: float | None = None
_mtx_ok = False
_stop = threading.Event()


def _unauthorized() -> JSONResponse:
    return JSONResponse({"detail": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Basic"})


def _check_basic(request: Request) -> bool:
    header = request.headers.get("authorization", "")
    if not header.startswith("Basic "):
        return False
    import base64

    try:
        raw = base64.b64decode(header.split(" ", 1)[1]).decode()
        user, _, password = raw.partition(":")
    except Exception:
        return False
    return secrets.compare_digest(user, ADMIN_USER) and secrets.compare_digest(password, ADMIN_PASSWORD)


def _private(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback


def _path_allowed(path: str) -> bool:
    if not PUBLISH_KEY:
        return False
    path = (path or "").strip("/")
    key = PUBLISH_KEY.strip("/")
    return path == key or path.startswith(key + "/")


def _client_ip(request: Request) -> str:
    return (request.client.host if request.client else "") or ""


@app.middleware("http")
async def gate(request: Request, call_next):
    path = request.url.path
    if path in ("/api/health",) or path.startswith("/internal/"):
        return await call_next(request)
    if not ADMIN_PASSWORD:
        return JSONResponse({"detail": "ADMIN_PASSWORD is not set"}, status_code=500)
    if not _check_basic(request):
        if path.startswith("/api/"):
            return _unauthorized()
        return _unauthorized()
    return await call_next(request)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "mediamtx": _mtx_ok, "publishing": _publishing}


@app.get("/api/status")
def status() -> dict:
    with store.locked():
        cfg = store.public_copy(store.load())
    dest_state = fanout.snapshot()
    for row in cfg["destinations"]:
        st = dest_state.get(row["id"], {})
        row["pushing"] = bool(st.get("running"))
        row["last_error"] = st.get("last_error") or ""
        row["restarts"] = st.get("restarts") or 0
        row["log"] = st.get("log") or []
        row["ready"] = bool(row.get("enabled") and row.get("has_key") and row.get("ingest"))
        if row["id"] == "kick":
            row["ready"] = bool(row.get("enabled") and row.get("ingest") and row.get("has_key"))
    return {
        "publishing": _publishing,
        "path": _live_path,
        "tracks": _tracks,
        "bytesReceived": _bytes_received,
        "mediamtx": _mtx_ok,
        "publicHost": PUBLIC_HOST,
        "publishKey": PUBLISH_KEY,
        "rtmpUrl": f"rtmp://{PUBLIC_HOST}:1935/{PUBLISH_KEY}",
        "srtUrl": (
            f"srt://{PUBLIC_HOST}:8890?streamid=publish:{PUBLISH_KEY}"
            "&pkt_size=1316&latency=250000"
        ),
        "hlsUrl": f"http://{PUBLIC_HOST}:8888/{PUBLISH_KEY}/index.m3u8",
        "destinations": cfg["destinations"],
    }


@app.patch("/api/destinations/{dest_id}")
async def update_destination(dest_id: str, request: Request) -> dict:
    body = await request.json()
    with store.locked():
        cfg = store.load()
        dest = store.destination(cfg, dest_id)
        if dest is None:
            raise HTTPException(404, "unknown destination")
        if "name" in body and not dest.get("builtin"):
            name = str(body["name"]).strip()
            if name:
                dest["name"] = name[:40]
        if "ingest" in body:
            dest["ingest"] = str(body["ingest"]).strip()
        if "key" in body:
            incoming = str(body["key"])
            if incoming and incoming not in ("********", "••••"):
                dest["key"] = incoming.strip()
        if "enabled" in body:
            dest["enabled"] = bool(body["enabled"])
        if dest.get("enabled") and not join_rtmp(dest.get("ingest", ""), dest.get("key", "")):
            dest["enabled"] = False
            store.save(cfg)
            raise HTTPException(400, "set ingest URL and stream key before enabling")
        store.save(cfg)
    return {"ok": True}


@app.post("/api/destinations")
async def add_destination(request: Request) -> dict:
    body = await request.json()
    dest_id = str(body.get("id") or "").strip().lower()
    name = str(body.get("name") or dest_id).strip()
    ingest = str(body.get("ingest") or "").strip()
    if not store.valid_id(dest_id):
        raise HTTPException(400, "id must be 2-41 chars: lowercase, digits, _-")
    with store.locked():
        cfg = store.load()
        if store.destination(cfg, dest_id):
            raise HTTPException(409, "id already exists")
        cfg["destinations"].append(
            {
                "id": dest_id,
                "name": name[:40] or dest_id,
                "ingest": ingest,
                "key": str(body.get("key") or "").strip(),
                "enabled": False,
                "help": "Custom RTMP/RTMPS destination.",
                "docs": "",
                "builtin": False,
            }
        )
        store.save(cfg)
    return {"ok": True}


@app.delete("/api/destinations/{dest_id}")
def delete_destination(dest_id: str) -> dict:
    with store.locked():
        cfg = store.load()
        dest = store.destination(cfg, dest_id)
        if dest is None:
            raise HTTPException(404, "unknown destination")
        if dest.get("builtin"):
            raise HTTPException(400, "cannot delete a built-in destination; disable it instead")
        cfg["destinations"] = [d for d in cfg["destinations"] if d.get("id") != dest_id]
        store.save(cfg)
    return {"ok": True}


@app.post("/internal/auth")
async def mediamtx_auth(request: Request) -> JSONResponse:
    if not _private(_client_ip(request)):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    action = payload.get("action") or ""
    path = payload.get("path") or ""
    ip = payload.get("ip") or ""
    if action == "publish" and _path_allowed(path):
        return JSONResponse({})
    # Loopback probe used by scripts/smoke.sh
    if action == "publish" and path.strip("/") == "probe" and _private(ip):
        return JSONResponse({})
    return JSONResponse({"error": "denied"}, status_code=403)


def _poll_once() -> None:
    global _live_path, _publishing, _tracks, _bytes_received, _offline_since, _mtx_ok
    try:
        with httpx.Client(timeout=2.0) as client:
            res = client.get(f"{MEDIAMTX_API}/v3/paths/list")
            res.raise_for_status()
            data = res.json()
        _mtx_ok = True
    except Exception:
        _mtx_ok = False
        return

    items = data.get("items") or []
    match = None
    for item in items:
        name = (item.get("name") or "").strip("/")
        if item.get("ready") and _path_allowed(name):
            match = item
            break
    now = time.time()
    if match:
        _live_path = match.get("name") or ""
        _tracks = match.get("tracks") or []
        _bytes_received = int(match.get("bytesReceived") or 0)
        _offline_since = None
        _publishing = True
    else:
        if _publishing:
            if _offline_since is None:
                _offline_since = now
            elif now - _offline_since >= OFFLINE_GRACE:
                _publishing = False
                _live_path = ""
                _tracks = []
                _bytes_received = 0
        else:
            _live_path = ""
            _tracks = []
            _bytes_received = 0

    with store.locked():
        destinations = store.load().get("destinations", [])
    fanout.sync(_publishing, _live_path, destinations)


def _loop() -> None:
    while not _stop.wait(1.0):
        try:
            _poll_once()
        except Exception:
            continue


@app.on_event("startup")
def startup() -> None:
    if not PUBLISH_KEY or PUBLISH_KEY.startswith("change-me"):
        raise RuntimeError("set PUBLISH_KEY in .env to a long random value")
    store.load()
    thread = threading.Thread(target=_loop, name="mtx-poll", daemon=True)
    thread.start()


@app.on_event("shutdown")
def shutdown() -> None:
    _stop.set()
    fanout.stop_all()
