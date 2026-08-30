"""Built-in destination templates. Ingest URLs are the common defaults;
Kick and X issue per-account URLs — paste whatever the dashboard shows."""

from __future__ import annotations

DESTINATIONS = [
    {
        "id": "twitch",
        "name": "Twitch",
        "ingest": "rtmp://iad.contribute.live-video.net/app",
        "key": "",
        "enabled": False,
        "hold": False,
        "help": "Twitch Dashboard → Settings → Stream. US East (Ashburn) ingest, same region as the Oracle VM.",
        "docs": "https://dashboard.twitch.tv/u/settings/stream",
        "builtin": True,
    },
    {
        "id": "youtube",
        "name": "YouTube",
        "ingest": "rtmp://a.rtmp.youtube.com/live2",
        "key": "",
        "enabled": False,
        "hold": False,
        "help": "YouTube Studio → Go live → Stream. Use the RTMP URL they show if it is not live2.",
        "docs": "https://studio.youtube.com",
        "builtin": True,
    },
    {
        "id": "x",
        "name": "X",
        "ingest": "rtmps://va.pscp.tv:443/x",
        "key": "",
        "enabled": False,
        "hold": False,
        "help": "studio.x.com → Media Studio → Sources. Paste the exact ingest URL X gives you if it differs.",
        "docs": "https://studio.x.com",
        "builtin": True,
    },
    {
        "id": "kick",
        "name": "Kick",
        "ingest": "",
        "key": "",
        "enabled": False,
        "hold": False,
        "help": "Creator Dashboard → Stream URL & Key. Paste the Stream URL exactly; we add :443/app if Kick omitted it. Live Kick is transcoded (720p60 or 1080p60, 2s GOP); other dests stay copy.",
        "docs": "https://kick.com/dashboard/settings/stream",
        "builtin": True,
    },
    {
        "id": "rumble",
        "name": "Rumble",
        "ingest": "rtmp://live.rumble.com/live",
        "key": "",
        "enabled": False,
        "hold": False,
        "help": "Rumble Studio → stream URL + key. Prefer the URL printed in their dashboard if it is region-specific.",
        "docs": "https://rumble.com/account/livestreams",
        "builtin": True,
    },
]
