#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Fixing .env formatting (strip leading spaces from keys)..."
if [ -f .env ]; then
  sed -i 's/^[[:space:]]*//' .env
else
  echo "Missing .env — copy .env.example and fill in secrets first."
  exit 1
fi

if [ ! -f app/config/plans.yaml ]; then
  echo "==> Creating app/config/plans.yaml from example..."
  cp app/config/plans.example.yaml app/config/plans.yaml
fi

echo "==> Pulling latest code..."
git pull

echo "==> Rebuilding and restarting bot..."
docker compose down
docker compose build --no-cache bot
docker compose up -d --force-recreate bot

echo "==> Recent logs (check for 'Admin IDs loaded'):"
sleep 3
docker compose logs --tail=40 bot
