from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import store
from fanout import IMAGE_EXT, VIDEO_EXT, Fanout, dest_mode, join_rtmp, normalize_ingest

PUBLISH_KEY = os.environ.get("PUBLISH_KEY", "").strip()
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
PUBLIC_HOST = os.environ.get("PUBLIC_HOST", "localhost").strip() or "localhost"
MEDIAMTX_API = os.environ.get("MEDIAMTX_API", "http://mediamtx:9997").rstrip("/")

STATIC = Path(__file__).parent / "static"
OFFLINE_GRACE = 2.5
COOKIE = "relaycast"
STANDBY_MAX = 64 * 1024 * 1024
STANDBY_EXT = IMAGE_EXT | VIDEO_EXT
log = logging.getLogger("uvicorn.error")

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
    """Accept MediaMTX-native /KEY and Action!/OBS-style /live/KEY."""
    if not PUBLISH_KEY:
        return False
    parts = [p for p in (path or "").strip("/").split("/") if p]
    key = PUBLISH_KEY.strip("/")
    return bool(parts) and key in (parts[0], parts[-1])


def _kick_fanout() -> None:
    with store.locked():
        cfg = store.load()
        dests = list(cfg.get("destinations", []))
        auto_hold = bool(cfg.get("auto_hold", True))
        standby = store.standby_file(cfg)
    fanout.sync(_publishing, _live_path, dests, auto_hold, standby)


def _client_ip(request: Request) -> str:
    return (request.client.host if request.client else "") or ""


def _cookie_token() -> str:
    return hmac.new(ADMIN_PASSWORD.encode(), b"session", hashlib.sha256).hexdigest()


def _authed(request: Request) -> bool:
    cookie = request.cookies.get(COOKIE, "")
    if cookie and secrets.compare_digest(cookie, _cookie_token()):
        return True
    return _check_basic(request)


@app.middleware("http")
async def gate(request: Request, call_next):
    path = request.url.path
    if (
        path in ("/api/health", "/login")
        or path.startswith("/internal/")
        or path == "/static/app.css"
    ):
        return await call_next(request)
    if not ADMIN_PASSWORD:
        return JSONResponse({"detail": "ADMIN_PASSWORD is not set"}, status_code=500)
    if not _authed(request):
        if path.startswith("/api/"):
            return _unauthorized()
        return RedirectResponse("/login", status_code=302)
    return await call_next(request)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/login")
def login_page() -> FileResponse:
    return FileResponse(STATIC / "login.html")


@app.post("/login")
async def login_submit(request: Request):
    data = parse_qs((await request.body()).decode("utf-8", "replace"))
    user = (data.get("username") or [""])[0]
    password = (data.get("password") or [""])[0]
    ok_user = secrets.compare_digest(user, ADMIN_USER)
    ok_pass = secrets.compare_digest(password, ADMIN_PASSWORD)
    if not (ok_user and ok_pass):
        return RedirectResponse("/login?err=1", status_code=303)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(COOKIE, _cookie_token(), httponly=True, samesite="lax", max_age=7 * 24 * 3600)
    return resp


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "mediamtx": _mtx_ok, "publishing": _publishing}


@app.get("/api/status")
def status() -> dict:
    with store.locked():
        cfg = store.load()
        dests = list(cfg.get("destinations", []))
        auto_hold = bool(cfg.get("auto_hold", True))
        pub = store.public_copy(cfg)
    dest_state = fanout.snapshot()
    raw_by_id = {d["id"]: d for d in dests if "id" in d}
    holding_any = False
    for row in pub["destinations"]:
        st = dest_state.get(row["id"], {})
        raw = raw_by_id.get(row["id"], row)
        sending = dest_mode(raw, _publishing, _live_path, auto_hold) or "off"
        if sending == "hold":
            holding_any = True
        row["sending"] = sending
        row["pushing"] = bool(st.get("running"))
        row["last_error"] = st.get("last_error") or ""
        row["restarts"] = st.get("restarts") or 0
        row["log"] = st.get("log") or []
        row["ready"] = bool(row.get("enabled") and row.get("has_key") and row.get("ingest"))
        if row["id"] == "kick":
            row["ready"] = bool(row.get("enabled") and row.get("ingest") and row.get("has_key"))
    return {
        "publishing": _publishing,
        "holding": holding_any,
        "path": _live_path,
        "tracks": _tracks,
        "bytesReceived": _bytes_received,
        "mediamtx": _mtx_ok,
        "publicHost": PUBLIC_HOST,
        "publishKey": PUBLISH_KEY,
        "rtmpUrl": f"rtmp://{PUBLIC_HOST}:1935/{PUBLISH_KEY}",
        "rtmpServer": f"rtmp://{PUBLIC_HOST}:1935/live",
        "rtmpKey": PUBLISH_KEY,
        "srtUrl": (
            f"srt://{PUBLIC_HOST}:8890?streamid=publish:{PUBLISH_KEY}"
            "&pkt_size=1316&latency=250000"
        ),
        "hlsUrl": f"http://{PUBLIC_HOST}:8888/{(_live_path or PUBLISH_KEY).strip('/')}/index.m3u8",
        "autoHold": auto_hold,
        "hasStandby": pub["has_standby"],
        "standbyName": pub["standby_name"],
        "destinations": pub["destinations"],
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
            dest["ingest"] = normalize_ingest(str(body["ingest"]))
        if "key" in body:
            incoming = str(body["key"])
            if incoming and incoming not in ("********", "••••"):
                dest["key"] = incoming.strip()
        if "enabled" in body:
            dest["enabled"] = bool(body["enabled"])
        if "hold" in body:
            dest["hold"] = bool(body["hold"])
        mode = body.get("mode")
        if mode == "off":
            dest["enabled"] = False
            dest["hold"] = False
        elif mode == "live":
            dest["enabled"] = True
            dest["hold"] = False
        elif mode == "hold":
            dest["enabled"] = True
            dest["hold"] = True
        elif mode is not None:
            raise HTTPException(400, "mode must be off, live, or hold")
        if dest.get("enabled") and not join_rtmp(dest.get("ingest", ""), dest.get("key", "")):
            dest["enabled"] = False
            dest["hold"] = False
            store.save(cfg)
            raise HTTPException(400, "set ingest URL and stream key before enabling")
        store.save(cfg)
    _kick_fanout()
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
                "hold": False,
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
    _kick_fanout()
    return {"ok": True}


@app.post("/api/hold-all")
async def hold_all(request: Request) -> dict:
    body = await request.json()
    hold = bool(body.get("hold", True))
    with store.locked():
        cfg = store.load()
        for dest in cfg.get("destinations", []):
            if dest.get("enabled"):
                dest["hold"] = hold
        store.save(cfg)
    _kick_fanout()
    return {"ok": True}


@app.patch("/api/settings")
async def update_settings(request: Request) -> dict:
    body = await request.json()
    with store.locked():
        cfg = store.load()
        if "auto_hold" in body:
            cfg["auto_hold"] = bool(body["auto_hold"])
        store.save(cfg)
    _kick_fanout()
    return {"ok": True}


@app.get("/api/standby")
def get_standby():
    with store.locked():
        path = store.standby_file(store.load())
    if path is None:
        raise HTTPException(404, "no standby file")
    return FileResponse(path)


@app.post("/api/standby")
async def upload_standby(file: UploadFile = File(...)) -> dict:
    name = Path(file.filename or "standby").name
    ext = Path(name).suffix.lower()
    if ext not in STANDBY_EXT:
        raise HTTPException(400, "use an image (png/jpg/webp) or a short clip (mp4/webm/gif)")
    data = await file.read(STANDBY_MAX + 1)
    if len(data) > STANDBY_MAX:
        raise HTTPException(400, "file too large (max 64 MB)")
    stored = f"standby{ext}"
    with store.locked():
        cfg = store.load()
        old = store.standby_file(cfg)
        dest = store.DATA_DIR / stored
        dest.write_bytes(data)
        if old and old.resolve() != dest.resolve() and old.exists():
            old.unlink()
        cfg["standby_name"] = stored
        store.save(cfg)
    _kick_fanout()
    return {"ok": True, "name": stored}


@app.delete("/api/standby")
def delete_standby() -> dict:
    with store.locked():
        cfg = store.load()
        old = store.standby_file(cfg)
        if old and old.exists():
            old.unlink()
        cfg["standby_name"] = ""
        store.save(cfg)
    _kick_fanout()
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
    if action == "publish" and path.strip("/") in ("probe", "standby") and _private(ip):
        return JSONResponse({})
    log.warning("auth deny action=%s path=%r ip=%s", action, path, ip)
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
        cfg = store.load()
        auto_hold = bool(cfg.get("auto_hold", True))
        standby = store.standby_file(cfg)
        destinations = list(cfg.get("destinations", []))
    fanout.sync(_publishing, _live_path, destinations, auto_hold, standby)


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
