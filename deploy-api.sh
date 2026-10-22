#!/usr/bin/env bash
# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "→ Rebuilding Docker image..."
cd "$SCRIPT_DIR"
docker compose build isp-api

echo "→ Restarting container..."
docker compose up -d isp-api

echo "✓ Done — http://localhost:8001"
