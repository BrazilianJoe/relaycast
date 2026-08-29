#!/usr/bin/env bash
# Publish a 12s test pattern into the local relay and confirm copy-out to a loopback RTMP sink.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "run scripts/init-env.sh first" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

auth=(-u "${ADMIN_USER}:${ADMIN_PASSWORD}")
base="http://127.0.0.1:8080"

echo "waiting for admin API…"
for _ in $(seq 1 60); do
  if curl -sf "${base}/api/health" >/dev/null; then
    break
  fi
  sleep 1
done
curl -sf "${base}/api/health" >/dev/null

echo "enabling loopback destination…"
curl -sf "${auth[@]}" -X POST "${base}/api/destinations" \
  -H "content-type: application/json" \
  -d '{"id":"loopback","name":"loopback","ingest":"rtmp://mediamtx:1935/probe"}' \
  >/dev/null || true

curl -sf "${auth[@]}" -X PATCH "${base}/api/destinations/loopback" \
  -H "content-type: application/json" \
  -d '{"enabled":true}' >/dev/null

echo "publishing test pattern for 12s…"
docker compose exec -T relay ffmpeg -nostdin -hide_banner -loglevel error -re \
  -f lavfi -i testsrc2=size=1280x720:rate=30 \
  -f lavfi -i sine=frequency=1000:sample_rate=48000 \
  -t 12 \
  -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p -g 60 -keyint_min 60 \
  -c:a aac -b:a 96k -ar 48000 -ac 2 \
  -f flv "rtmp://mediamtx:1935/${PUBLISH_KEY}" &
pub=$!

ok=0
for _ in $(seq 1 25); do
  sleep 1
  if ! kill -0 "${pub}" 2>/dev/null; then
    break
  fi
  js="$(curl -sf "${auth[@]}" "${base}/api/status")"
  if python3 -c '
import json, sys
s = json.loads(sys.argv[1])
d = {x["id"]: x for x in s.get("destinations", [])}
lb = d.get("loopback") or {}
print("publishing=%s loopback=%s err=%s" % (s.get("publishing"), lb.get("pushing"), (lb.get("last_error") or "")[:120]))
sys.exit(0 if s.get("publishing") and lb.get("pushing") else 1)
' "${js}"; then
    ok=1
    break
  fi
done

wait "${pub}" || true

curl -sf "${auth[@]}" -X PATCH "${base}/api/destinations/loopback" \
  -H "content-type: application/json" \
  -d '{"enabled":false}' >/dev/null || true

if [[ "${ok}" != "1" ]]; then
  echo "smoke failed — last logs:" >&2
  docker compose logs --tail=80 >&2
  exit 1
fi

echo "smoke ok: ingest went live and copy-out to loopback was running"
