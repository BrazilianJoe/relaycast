# relaycast

Self-hosted restream proxy: OBS sends **one** encode to a small US VM, and the VM copies that bitstream to Twitch, YouTube, X, Kick, and Rumble. Each destination has an on/off switch. Nothing is transcoded.

Brazil → US is high-RTT. Prefer **SRT** ingest; RTMP is the fallback. The VM sits in **us-ashburn-1** (Ashburn, Virginia) so platform ingest is US-side.

## How it works

```
OBS (your PC) --SRT/RTMP--> MediaMTX --FFmpeg -c copy--> Twitch
                                         |--------------> YouTube
                                         |--------------> X
                                         |--------------> Kick
                                         |--------------> Rumble
```

[MediaMTX](https://github.com/bluenviron/mediamtx) accepts the publisher. A tiny FastAPI process watches that path and starts one `ffmpeg -c copy` per enabled destination. CPU stays near idle; the limiter is **egress bandwidth**, not cores.

Admin UI: `http://<host>:8080` (HTTP basic auth). Paste stream keys, toggle platforms, watch a local HLS preview.

## Local (this is the “VM”)

Needs Docker Compose v2.

```bash
git clone https://github.com/BrazilianJoe/relaycast.git
cd relaycast
./scripts/init-env.sh          # writes .env with random secrets
docker compose up --build -d
./scripts/smoke.sh             # testsrc → ingest → loopback copy-out
```

Open `http://localhost:8080` (user `admin`, password printed by `init-env.sh`).

OBS:

| Field | RTMP | SRT (recommended from Brazil) |
| --- | --- | --- |
| Service | Custom | Custom |
| Server | `rtmp://<host>:1935/<PUBLISH_KEY>` | the SRT URL from the UI |
| Stream key | empty | empty |

`PUBLISH_KEY` is the path name. Treat it like a password.

### Encode once for every platform

Twitch is the tightest common cap. Use this in OBS and every destination gets the same copy:

- 1080p60 CBR **6000 kbps** (or 1080p30 at 4500)
- x264 `veryfast` / NVENC P5, profile High
- **keyframe interval 2 seconds**
- AAC 160 kbps, 48 kHz stereo
- no extra local restream plugins — this box is the restreamer

## Oracle Cloud Always Free

Conservative shape that still has headroom for five copy-only outputs:

| | |
| --- | --- |
| Region | `us-ashburn-1` |
| Shape | `VM.Standard.A1.Flex` (Ampere) |
| OCPU / RAM | **1 / 6 GB** |
| Boot | 50 GB |

Always Free Ampere quota is 4 OCPU / 24 GB; this leaves the rest for other VMs. Copy-only restream of ~6 Mbps × 5 destinations is ~30 Mbps egress and almost no CPU. Oracle’s 10 TB/month outbound cap is plenty for hobby streaming.

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# fill OCIDs, API key, SSH public key
terraform init
terraform apply
terraform output
```

Cloud-init installs Docker and brings the stack up from this repo. If Ashburn is out of Ampere capacity, retry or set `region = "us-phoenix-1"`.

Tighten `ssh_cidr` / `admin_cidr` to your home IP when you have a stable one. `ingest_cidr` can stay `0.0.0.0/0` if your Brazilian ISP NATs you onto rotating addresses — the publish path is still `PUBLISH_KEY`.

Do **not** publish MediaMTX API (`9997`) or HLS (`8888`) on the VCN security list. Compose maps them locally for debugging; Terraform only opens 22, 8080, 1935, and 8890.

## Destinations

| Platform | Default ingest | Notes |
| --- | --- | --- |
| Twitch | `rtmp://iad.contribute.live-video.net/app` | Ashburn ingest, same region as the VM |
| YouTube | `rtmp://a.rtmp.youtube.com/live2` | Use the URL Studio shows if it differs |
| X | `rtmps://va.pscp.tv:443/x` | Media Studio often issues a unique URL — paste that |
| Kick | *(empty)* | Per-account `rtmps://….global-contribute.live-video.net:443/app` |
| Rumble | `rtmp://live.rumble.com/live` | Prefer the URL from Rumble Studio |

Keys stay in `data/config.json` on the VM, not in git. Custom RTMP/RTMPS destinations can be added in the UI.

## Layout

```
docker-compose.yml   MediaMTX + relay
mediamtx.yml         ingest only (RTMP/SRT/HLS)
relay/               admin UI + FFmpeg supervisor
terraform/           Always Free Ampere VM
scripts/             init-env.sh, smoke.sh
```

## Ops

```bash
docker compose logs -f relay
docker compose logs -f mediamtx
docker compose restart relay
```

If a platform drops, FFmpeg is restarted while ingest is still live. Toggling a destination off SIGTERMs that copy only.
