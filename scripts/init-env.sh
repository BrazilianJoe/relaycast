#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  echo ".env already exists"
  exit 0
fi

cp .env.example .env
key="$(openssl rand -hex 16)"
pass="$(openssl rand -base64 18 | tr -d '/+=' | head -c 20)"
sed -i "s|^PUBLISH_KEY=.*|PUBLISH_KEY=${key}|" .env
sed -i "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=${pass}|" .env

echo "wrote .env"
echo "  admin user:     admin"
echo "  admin password: ${pass}"
echo "  publish key:    ${key}"
