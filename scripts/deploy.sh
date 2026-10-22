#!/usr/bin/env bash
# Update the shop on the server.
#
# The pip layer is the expensive part of the image, so the build keeps the
# Docker cache: requirements.txt stays stable and optional packages live in
# requirements-extra.txt. Never build with --no-cache on the small server.
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
git pull --ff-only

echo "==> Rebuilding and restarting the bot..."
# The server has 1 CPU and little RAM: build as nicely as possible.
nice -n 19 ionice -c3 docker compose build bot
docker compose up -d --force-recreate bot

echo "==> Recent logs:"
sleep 3
docker compose logs --tail=40 bot
