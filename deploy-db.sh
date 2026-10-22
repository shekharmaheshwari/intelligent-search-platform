#!/usr/bin/env bash
# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "→ Starting isp-db..."
docker compose up -d isp-db

echo "→ Waiting for PostgreSQL to be ready..."
until docker compose exec isp-db pg_isready -U isp -d isp -q; do
  sleep 1
done

echo "✓ Done — PostgreSQL ready at localhost:5432"
