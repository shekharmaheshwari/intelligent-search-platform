#!/usr/bin/env bash
# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== [1/3] Database ==="
bash "$SCRIPT_DIR/deploy-db.sh"

echo ""
echo "=== [2/3] API ==="
bash "$SCRIPT_DIR/deploy-api.sh"

echo ""
echo "=== [3/3] UI ==="
bash "$SCRIPT_DIR/deploy-ui.sh"

echo ""
echo "✓ All services running:"
echo "   UI  → http://localhost:3001"
echo "   API → http://localhost:8001"
echo "   DB  → localhost:5432"
