from __future__ import annotations

import json
import os
import re
import threading
from copy import deepcopy
from pathlib import Path

from platforms import DESTINATIONS

DATA_DIR = Path(os.environ.get("RELAY_DATA", "/data"))
CONFIG_PATH = DATA_DIR / "config.json"

_lock = threading.Lock()
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")


def _seed() -> dict:
    return {"auto_hold": True, "standby_name": "", "destinations": deepcopy(DESTINATIONS)}


def load() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        cfg = _seed()
        _write(cfg)
        return cfg
    with CONFIG_PATH.open() as fh:
        cfg = json.load(fh)
    return _merge_builtins(cfg)


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
        "has_standby": bool(standby_file(cfg)),
        "destinations": [],
    }
    for item in cfg.get("destinations", []):
        row = dict(item)
        key = row.get("key") or ""
        row["has_key"] = bool(key)
        row["key_tail"] = key[-4:] if len(key) >= 4 else (key if key else "")
        row["key"] = ""
        row["hold"] = bool(row.get("hold", False))
        out["destinations"].append(row)
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
    return cfg


def _write(cfg: dict) -> None:
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n")
    tmp.replace(CONFIG_PATH)
