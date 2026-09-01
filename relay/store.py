from __future__ import annotations

import json
import os
import re
import threading
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

from platforms import DESTINATIONS

DATA_DIR = Path(os.environ.get("RELAY_DATA", "/data"))
CONFIG_PATH = DATA_DIR / "config.json"
KICK_SIZES = ("copy", "720p60", "1080p60")
KICK_COPY_ALIASES = {"copy", "off", "none", "false", "0"}
DEFAULT_KICK_TRANSCODE = "720p60"

_lock = threading.Lock()
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")


def parse_kick_transcode(raw: str | None) -> str | None:
    value = str(raw or "").strip().lower()
    if value in KICK_COPY_ALIASES:
        return "copy"
    if value in ("1080", "1080p", "1080p60"):
        return "1080p60"
    if value in ("720", "720p", "720p60"):
        return "720p60"
    return None


def kick_transcode(cfg: dict | None = None) -> str:
    return parse_kick_transcode((cfg or {}).get("kick_transcode")) or DEFAULT_KICK_TRANSCODE


def kick_live_size(mode: str) -> str:
    """Empty means Kick live is copy-only."""
    parsed = parse_kick_transcode(mode) or DEFAULT_KICK_TRANSCODE
    return "" if parsed == "copy" else parsed


def normalize_page_url(raw: str) -> str:
    url = (raw or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("page URL must be http(s)")
    return url[:300]


def _seed() -> dict:
    return {
        "auto_hold": True,
        "standby_name": "",
        "kick_transcode": DEFAULT_KICK_TRANSCODE,
        "destinations": deepcopy(DESTINATIONS),
    }


def load() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        cfg = _seed()
        _write(cfg)
        return cfg
    with CONFIG_PATH.open() as fh:
        cfg = json.load(fh)
    had_rumble_page = any(
        d.get("id") == "rumble" and "page_url" in d for d in cfg.get("destinations", [])
    )
    missing = "kick_transcode" not in cfg
    cfg = _merge_builtins(cfg)
    if missing or not had_rumble_page:
        _write(cfg)
    return cfg


def save(cfg: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _write(cfg)


def locked() -> threading.Lock:
    return _lock


def valid_id(value: str) -> bool:
    return bool(_ID_RE.match(value))


def destination(cfg: dict, dest_id: str) -> dict | None:
    for item in cfg.get("destinations", []):
        if item.get("id") == dest_id:
            return item
    return None


def public_copy(cfg: dict) -> dict:
    out = {
        "auto_hold": bool(cfg.get("auto_hold", True)),
        "standby_name": cfg.get("standby_name") or "",
        "kick_transcode": kick_transcode(cfg),
        "has_standby": bool(standby_file(cfg)),
        "destinations": [],
    }
    for item in cfg.get("destinations", []):
        key = item.get("key") or ""
        out["destinations"].append(
            {
                "id": item.get("id"),
                "name": item.get("name") or item.get("id"),
                "enabled": bool(item.get("enabled")),
                "hold": bool(item.get("hold", False)),
                "has_key": bool(key),
                "has_ingest": bool(item.get("ingest")),
                "docs": item.get("docs") or "",
                "builtin": bool(item.get("builtin")),
                "pageUrl": item.get("page_url") or "",
            }
        )
    return out


def standby_file(cfg: dict) -> Path | None:
    name = cfg.get("standby_name") or ""
    if not name or "/" in name or "\\" in name:
        return None
    path = DATA_DIR / name
    return path if path.is_file() else None


def _merge_builtins(cfg: dict) -> dict:
    """Keep user keys/toggles; pick up help text and new platforms from code."""
    existing = {d["id"]: d for d in cfg.get("destinations", []) if "id" in d}
    merged = []
    seen = set()
    for stock in DESTINATIONS:
        seen.add(stock["id"])
        prev = existing.get(stock["id"], {})
        row = dict(stock)
        row["ingest"] = prev.get("ingest", stock["ingest"])
        row["key"] = prev.get("key", "")
        row["enabled"] = bool(prev.get("enabled", False))
        row["hold"] = bool(prev.get("hold", False))
        if "page_url" in stock:
            if "page_url" in prev:
                row["page_url"] = str(prev.get("page_url") or "")
            else:
                row["page_url"] = stock.get("page_url") or ""
        merged.append(row)
    for dest_id, prev in existing.items():
        if dest_id in seen:
            continue
        row = dict(prev)
        row.setdefault("builtin", False)
        row.setdefault("help", "Custom RTMP/RTMPS destination.")
        row.setdefault("hold", False)
        merged.append(row)
    cfg["destinations"] = merged
    cfg.setdefault("auto_hold", True)
    cfg.setdefault("standby_name", "")
    cfg["kick_transcode"] = kick_transcode(cfg)
    return cfg


def _write(cfg: dict) -> None:
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n")
    tmp.replace(CONFIG_PATH)
