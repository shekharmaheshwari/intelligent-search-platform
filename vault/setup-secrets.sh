#!/bin/sh
# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

# One-time script to populate ISP secrets into the local Vault container.
# Run this after `docker-compose up` on first setup, or whenever you need
# to add/rotate a secret.
#
# Usage:
#   ./vault/setup-secrets.sh
#
# The root token is read automatically from the running isp-vault container.
# Requires: docker, vault CLI (brew install vault)

set -e

VAULT_ADDR="http://localhost:8200"

echo "[setup] reading root token from isp-vault container..."
export VAULT_TOKEN
VAULT_TOKEN=$(docker exec isp-vault cat /vault/data/.root_token)

export VAULT_ADDR

# ── Enable KV v2 secrets engine (idempotent) ─────────────────────────────────
vault secrets list -address="$VAULT_ADDR" | grep -q "^isp/" || {
  echo "[setup] enabling KV v2 at isp/..."
  vault secrets enable -address="$VAULT_ADDR" -path=isp kv-v2
}

# ── Store ISP secrets ─────────────────────────────────────────────────────────

echo "[setup] writing TYPESENSE_API_KEY..."
vault kv put -address="$VAULT_ADDR" isp/TYPESENSE_API_KEY \
  value="local-dev-key-change-me"

echo "[setup] writing DATABASE_URL..."
vault kv put -address="$VAULT_ADDR" isp/DATABASE_URL \
  value="postgresql://isp:isp-dev-password@isp-db:5432/isp"

echo "[setup] writing VOYAGE_API_KEY..."
vault kv put -address="$VAULT_ADDR" isp/VOYAGE_API_KEY \
  value="${VOYAGE_API_KEY:-paste-your-voyage-key-here}"

echo "[setup] writing GOOGLE_SERVICE_ACCOUNT_JSON..."
GSHEET_JSON_PATH="${GSHEET_JSON_PATH:-$HOME/.config/isp/gsheet-service-account.json}"
if [ -f "$GSHEET_JSON_PATH" ]; then
  vault kv put -address="$VAULT_ADDR" isp/GOOGLE_SERVICE_ACCOUNT_JSON \
    value="$(cat "$GSHEET_JSON_PATH")"
  echo "[setup] GOOGLE_SERVICE_ACCOUNT_JSON loaded from $GSHEET_JSON_PATH"
else
  echo "[setup] WARNING: $GSHEET_JSON_PATH not found — skipping GOOGLE_SERVICE_ACCOUNT_JSON"
  echo "        Move the file there and re-run this script, or run:"
  echo "        vault kv put isp/GOOGLE_SERVICE_ACCOUNT_JSON value='\$(cat /path/to/key.json)'"
fi

# ── Print the root token for backend/.env ────────────────────────────────────
echo ""
echo "──────────────────────────────────────────────"
echo "  Vault setup complete."
echo "  Root token: $VAULT_TOKEN"
echo ""
echo "  Add this to backend/.env to use Vault:"
echo "    SECRET_PROVIDER=vault"
echo "    VAULT_ADDR=http://isp-vault:8200"
echo "    VAULT_TOKEN=$VAULT_TOKEN"
echo "──────────────────────────────────────────────"
