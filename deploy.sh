#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== ISP Full Deploy ==="
echo ""

echo "[1/5] Pulling base images (typesense, redis, postgres)..."
docker compose pull typesense isp-redis isp-db

echo ""
echo "[2/5] Building frontend assets..."
cd "$SCRIPT_DIR/frontend"
npm install --frozen-lockfile
npm run build
cd "$SCRIPT_DIR"

echo ""
echo "[3/5] Building Docker images (isp-api, isp-ui)..."
docker compose build isp-api isp-ui

echo ""
echo "[4/5] Starting all services..."
docker compose up -d

echo ""
echo "[5/5] Waiting for services to be healthy..."

wait_healthy() {
  local name=$1
  local retries=30
  until [ "$(docker inspect --format='{{.State.Health.Status}}' "$name" 2>/dev/null)" = "healthy" ]; do
    retries=$((retries - 1))
    if [ "$retries" -eq 0 ]; then
      echo "  WARNING: $name did not become healthy in time"
      return
    fi
    sleep 2
  done
  echo "  ✓ $name"
}

wait_healthy typesense
wait_healthy isp-redis
wait_healthy isp-db

echo ""
echo "All services deployed and running:"
echo "  UI         → http://localhost:3001"
echo "  API        → http://localhost:8001"
echo "  Typesense  → http://localhost:8108"
echo "  PostgreSQL → localhost:5432"
echo "  Redis      → localhost:6379"
echo "  T-Mobile PLP → http://localhost:3000"
