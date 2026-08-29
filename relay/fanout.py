"""Copy-only FFmpeg fan-out. One process per enabled destination."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque

SOURCE_BASE = os.environ.get("MEDIAMTX_RTMP", "rtmp://mediamtx:1935")


def join_rtmp(ingest: str, key: str) -> str:
    ingest = (ingest or "").strip().rstrip("/")
    key = (key or "").strip().lstrip("/")
    if not ingest:
        return ""
    if not key:
        return ingest
    return f"{ingest}/{key}"


@dataclass
class DestState:
    running: bool = False
    pid: int | None = None
    restarts: int = 0
    last_error: str = ""
    last_start: float | None = None
    lines: Deque[str] = field(default_factory=lambda: deque(maxlen=80))


class Fanout:
    def __init__(self) -> None:
        self._procs: dict[str, subprocess.Popen] = {}
        self._state: dict[str, DestState] = {}
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
                    "log": list(st.lines)[-12:],
                }
            return out

    def logs(self, dest_id: str) -> list[str]:
        with self._lock:
            st = self._state.get(dest_id)
            return list(st.lines) if st else []

    def sync(self, publishing: bool, live_path: str, destinations: list[dict]) -> None:
        wanted: set[str] = set()
        if publishing and live_path:
            for dest in destinations:
                dest_id = dest.get("id")
                if not dest_id:
                    continue
                if dest.get("enabled") and join_rtmp(dest.get("ingest", ""), dest.get("key", "")):
                    wanted.add(dest_id)

        with self._lock:
            for dest_id in list(self._procs):
                if dest_id not in wanted:
                    self._stop(dest_id)

            if not wanted:
                return

            by_id = {d["id"]: d for d in destinations if "id" in d}
            for dest_id in wanted:
                dest = by_id[dest_id]
                proc = self._procs.get(dest_id)
                if proc is not None and proc.poll() is None:
                    continue
                if proc is not None:
                    self._note_exit(dest_id, proc)
                self._start(dest_id, dest, live_path)

    def stop_all(self) -> None:
        with self._lock:
            for dest_id in list(self._procs):
                self._stop(dest_id)

    def _start(self, dest_id: str, dest: dict, live_path: str) -> None:
        target = join_rtmp(dest.get("ingest", ""), dest.get("key", ""))
        source = f"{SOURCE_BASE.rstrip('/')}/{live_path}"
        st = self._state.setdefault(dest_id, DestState())
        if st.last_start and time.time() - st.last_start < 1.5:
            return
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
            "-c",
            "copy",
            "-f",
            "flv",
            "-flvflags",
            "no_duration_filesize",
            target,
        ]
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
            return
        self._procs[dest_id] = proc
        st.running = True
        st.pid = proc.pid
        st.last_start = time.time()
        st.last_error = ""
        st.restarts += 1
        st.lines.append(f"started pid={proc.pid} → {dest.get('ingest', '')}")
        threading.Thread(target=self._drain, args=(dest_id, proc), daemon=True).start()

    def _drain(self, dest_id: str, proc: subprocess.Popen) -> None:
        assert proc.stderr is not None
        try:
            for raw in proc.stderr:
                line = raw.strip()
                if not line:
                    continue
                with self._lock:
                    st = self._state.setdefault(dest_id, DestState())
                    st.lines.append(line)
                    if "error" in line.lower() or "failed" in line.lower():
                        st.last_error = line[-300:]
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
