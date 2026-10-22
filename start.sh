#!/usr/bin/env bash
# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "Starting all ISP services..."
docker compose up -d

echo ""
echo "Waiting for services to be healthy..."

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
echo "All services up:"
echo "  UI         → http://localhost:3001"
echo "  API        → http://localhost:8001"
echo "  Typesense  → http://localhost:8108"
echo "  PostgreSQL → localhost:5432"
echo "  Redis      → localhost:6379"
echo "  PLP → http://localhost:3000"
