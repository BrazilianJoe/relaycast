"""FFmpeg fan-out. Destinations copy the ingest bitstream except Kick live,
which is transcoded so IVS gets a 2s GOP. Kick live is 720p60 or 1080p60
ultrafast. Hold encodes a looping slate once, then copies."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Deque
from urllib.parse import urlparse, urlunparse

SOURCE_BASE = os.environ.get("MEDIAMTX_RTMP", "rtmp://mediamtx:1935")
SLATE_PATH = "standby"
FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO_EXT = {".mp4", ".webm", ".mov", ".mkv", ".gif", ".m4v"}


def normalize_ingest(url: str) -> str:
    """Kick's dashboard often omits :443/app; ffmpeg needs that path or TLS dies."""
    raw = (url or "").strip()
    if not raw:
        return ""
    if "global-contribute.live-video.net" not in raw.lower():
        return raw.rstrip("/")
    parsed = urlparse(raw if "://" in raw else f"rtmps://{raw}")
    host = parsed.hostname or ""
    if not host:
        return raw.rstrip("/")
    path = parsed.path or ""
    if path in ("", "/"):
        path = "/app"
    elif not path.rstrip("/").endswith("/app"):
        path = path.rstrip("/") + "/app"
    scheme = parsed.scheme or "rtmps"
    netloc = f"{host}:{parsed.port or 443}"
    return urlunparse((scheme, netloc, path, "", "", "")).rstrip("/")


def join_rtmp(ingest: str, key: str) -> str:
    ingest = normalize_ingest(ingest)
    key = (key or "").strip().lstrip("/")
    if not ingest:
        return ""
    if not key:
        return ingest
    return f"{ingest}/{key}"


def redact(line: str) -> str:
    line = re.sub(r"sk_[A-Za-z0-9_-]+", "sk_…", line)
    line = re.sub(r"live_[A-Za-z0-9_]+", "live_…", line)
    return line


def dest_mode(dest: dict, publishing: bool, live_path: str, auto_hold: bool) -> str | None:
    """'live', 'hold', or None (do not send)."""
    if not dest.get("enabled"):
        return None
    if not join_rtmp(dest.get("ingest", ""), dest.get("key", "")):
        return None
    if dest.get("hold"):
        return "hold"
    if publishing and live_path:
        return "live"
    if auto_hold:
        return "hold"
    return None


@dataclass
class DestState:
    running: bool = False
    pid: int | None = None
    restarts: int = 0
    last_error: str = ""
    last_start: float | None = None
    mode: str = ""
    source: str = ""
    target: str = ""
    transcode: bool = False
    kick_size: str = ""
    lines: Deque[str] = field(default_factory=lambda: deque(maxlen=80))


class Fanout:
    def __init__(self) -> None:
        self._procs: dict[str, subprocess.Popen] = {}
        self._state: dict[str, DestState] = {}
        self._slate: subprocess.Popen | None = None
        self._slate_key = ""
        self._kick_size = "720p60"
        self._lock = threading.Lock()

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            out = {}
            for dest_id, st in self._state.items():
                out[dest_id] = {
                    "running": st.running,
                    "pid": st.pid,
                    "restarts": st.restarts,
                    "last_error": st.last_error,
                    "last_start": st.last_start,
                    "mode": st.mode,
                    "transcode": st.transcode,
                    "kick_size": st.kick_size,
                    "log": list(st.lines)[-12:],
                }
            return out

    def sync(
        self,
        publishing: bool,
        live_path: str,
        destinations: list[dict],
        auto_hold: bool,
        standby_file: Path | None,
        kick_transcode: str = "720p60",
    ) -> None:
        self._kick_size = "1080p60" if kick_transcode == "1080p60" else "720p60"
        plan: dict[str, tuple[str, str, str]] = {}
        by_id = {d["id"]: d for d in destinations if "id" in d}
        for dest in destinations:
            dest_id = dest.get("id")
            if not dest_id:
                continue
            mode = dest_mode(dest, publishing, live_path, auto_hold)
            if not mode:
                continue
            source = live_path if mode == "live" else SLATE_PATH
            target = join_rtmp(dest.get("ingest", ""), dest.get("key", ""))
            plan[dest_id] = (mode, source, target)

        with self._lock:
            if any(mode == "hold" for mode, _, _ in plan.values()):
                self._ensure_slate(standby_file)
            else:
                self._stop_slate()

            for dest_id in list(self._procs):
                if dest_id not in plan:
                    self._stop(dest_id)

            for dest_id, (mode, source, target) in plan.items():
                dest = by_id[dest_id]
                st = self._state.setdefault(dest_id, DestState())
                proc = self._procs.get(dest_id)
                alive = proc is not None and proc.poll() is None
                size = self._kick_size if (kick_target(dest_id, target) and mode == "live") else ""
                if alive and st.mode == mode and st.source == source and st.target == target and st.kick_size == size:
                    continue
                if proc is not None:
                    if alive:
                        self._stop(dest_id)
                    else:
                        self._note_exit(dest_id, proc)
                self._start(dest_id, dest, mode, source, force=alive)

    def stop_all(self) -> None:
        with self._lock:
            self._stop_slate()
            for dest_id in list(self._procs):
                self._stop(dest_id)

    def _start(self, dest_id: str, dest: dict, mode: str, source: str, force: bool = False) -> None:
        target = join_rtmp(dest.get("ingest", ""), dest.get("key", ""))
        st = self._state.setdefault(dest_id, DestState())
        gap = 8.0 if kick_target(dest_id, target) else 1.5
        if not force and st.last_start and time.time() - st.last_start < gap:
            return
        url = f"{SOURCE_BASE.rstrip('/')}/{source}"
        size = self._kick_size if (kick_target(dest_id, target) and mode == "live") else ""
        cmd = _ffmpeg_out(url, target, dest_id, mode, size)
        if not self._spawn(dest_id, cmd, st):
            return
        proc = self._procs[dest_id]
        st.running = True
        st.pid = proc.pid
        st.last_start = time.time()
        st.mode = mode
        st.source = source
        st.target = target
        st.transcode = bool(size)
        st.kick_size = size
        st.restarts += 1
        kind = "transcode" if st.transcode else "copy"
        st.lines.append(f"{mode} {kind} pid={proc.pid} src={source}")
        threading.Thread(target=self._drain, args=(dest_id, proc), daemon=True).start()

    def _ensure_slate(self, standby_file: Path | None) -> None:
        key = str(standby_file) if standby_file and standby_file.is_file() else "lavfi"
        proc = self._slate
        if proc is not None and proc.poll() is None and self._slate_key == key:
            return
        self._stop_slate()
        cmd = _slate_cmd(standby_file)
        try:
            self._slate = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._slate_key = key
        except OSError:
            self._slate = None
            self._slate_key = ""

    def _stop_slate(self) -> None:
        proc = self._slate
        self._slate = None
        self._slate_key = ""
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)

    def _spawn(self, dest_id: str, cmd: list[str], st: DestState) -> bool:
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            st.last_error = str(exc)
            st.running = False
            st.lines.append(str(exc))
            return False
        self._procs[dest_id] = proc
        return True

    def _drain(self, dest_id: str, proc: subprocess.Popen) -> None:
        assert proc.stderr is not None
        try:
            for raw in proc.stderr:
                line = raw.strip()
                if not line:
                    continue
                with self._lock:
                    st = self._state.setdefault(dest_id, DestState())
                    st.lines.append(redact(line))
                    if "error" in line.lower() or "failed" in line.lower():
                        st.last_error = redact(line[-300:])
        except Exception:
            return

    def _note_exit(self, dest_id: str, proc: subprocess.Popen) -> None:
        code = proc.poll()
        st = self._state.setdefault(dest_id, DestState())
        st.running = False
        st.pid = None
        if code not in (0, None, -signal.SIGTERM, -signal.SIGKILL):
            st.last_error = st.last_error or f"ffmpeg exited {code}"
            st.lines.append(st.last_error)
        self._procs.pop(dest_id, None)

    def _stop(self, dest_id: str) -> None:
        proc = self._procs.pop(dest_id, None)
        st = self._state.setdefault(dest_id, DestState())
        st.running = False
        st.pid = None
        st.mode = ""
        st.source = ""
        st.transcode = False
        st.kick_size = ""
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        st.lines.append("stopped")


def kick_target(dest_id: str, target: str) -> bool:
    return dest_id == "kick" or "global-contribute.live-video.net" in (target or "").lower()


def _kick_threads() -> int:
    return max(1, os.cpu_count() or 1)


def _kick_live_video(size: str) -> list[str]:
    """720p60 or 1080p60 ultrafast, 2s GOP. Thread count follows guest OCPUs."""
    n = str(_kick_threads())
    height = 1080 if size == "1080p60" else 720
    rate = "6000k" if height == 1080 else "4500k"
    return [
        "-threads",
        n,
        "-filter:v",
        f"scale=-2:'min({height},ih)':flags=neighbor,fps=60",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-profile:v",
        "baseline",
        "-pix_fmt",
        "yuv420p",
        "-bf",
        "0",
        "-g",
        "120",
        "-keyint_min",
        "120",
        "-x264-params",
        f"scenecut=0:bframes=0:open_gop=0:aud=1:threads={n}:sliced-threads=0",
        "-b:v",
        rate,
        "-maxrate",
        rate,
        "-bufsize",
        rate,
        "-bsf:v",
        "dump_extra",
    ]


def _ffmpeg_out(source: str, target: str, dest_id: str, mode: str = "live", kick_size: str = "") -> list[str]:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "level+warning",
        "-rw_timeout",
        "15000000",
        "-i",
        source,
        "-map",
        "0:v:0?",
        "-map",
        "0:a:0?",
    ]
    if kick_target(dest_id, target) and mode == "live":
        cmd += [*_kick_live_video(kick_size or "720p60"), "-c:a", "copy"]
    elif kick_target(dest_id, target):
        cmd += ["-c:v", "copy", "-bsf:v", "dump_extra", "-c:a", "copy"]
    else:
        cmd += ["-c", "copy"]
    cmd += ["-f", "flv", "-flvflags", "no_duration_filesize", target]
    return cmd


def _slate_cmd(path: Path | None) -> list[str]:
    target = f"{SOURCE_BASE.rstrip('/')}/{SLATE_PATH}"
    encode = [
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-r", "30", "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
        "-b:v", "2000k", "-maxrate", "2000k", "-bufsize", "2000k",
        "-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-ac", "2",
        "-f", "flv", "-flvflags", "no_duration_filesize",
        target,
    ]
    head = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-re"]
    scale = "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2"
    if path and path.is_file():
        ext = path.suffix.lower()
        if ext in IMAGE_EXT:
            return head + [
                "-loop", "1", "-framerate", "30", "-i", str(path),
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-vf", scale, "-tune", "stillimage",
                *encode,
            ]
        if ext in VIDEO_EXT:
            extra = ["-ignore_loop", "0"] if ext == ".gif" else []
            return head + extra + [
                "-stream_loop", "-1", "-i", str(path),
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-filter:v", scale, "-map", "0:v:0", "-map", "1:a:0",
                *encode,
            ]
    vf = scale
    if FONT.is_file():
        vf = (
            "drawtext=fontfile="
            + str(FONT)
            + ":text='STAND BY':fontsize=72:fontcolor=0xe0a14a:x=(w-text_w)/2:y=(h-text_h)/2"
        )
    return head + [
        "-f", "lavfi", "-i", "color=c=0x100e0c:s=1280x720:r=30",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-vf", vf,
        *encode,
    ]
