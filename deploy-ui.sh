#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "→ Building frontend..."
cd "$SCRIPT_DIR/frontend"
npm run build

echo "→ Rebuilding Docker image..."
cd "$SCRIPT_DIR"
docker compose build isp-ui

echo "→ Restarting container..."
docker compose up -d isp-ui

echo "✓ Done — http://localhost:3001"
