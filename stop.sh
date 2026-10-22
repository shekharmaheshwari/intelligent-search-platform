#!/usr/bin/env bash
# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

usage() {
  echo "Usage: $0 [--stop | --down]"
  echo "  --stop  Pause containers (auto-restart on machine reboot)"
  echo "  --down  Remove containers (requires ./start.sh to bring back up)"
  exit 1
}

case "${1:-}" in
  --stop)
    echo "Pausing all ISP services (containers preserved)..."
    docker compose stop
    echo ""
    echo "All services paused. Run ./start.sh to resume."
    ;;
  --down)
    echo "Stopping and removing all ISP containers..."
    docker compose down
    echo ""
    echo "All services removed. Run ./start.sh to bring back up."
    ;;
  *)
    usage
    ;;
esac
