#!/bin/sh
# Starts Vault with file storage, auto-initialises on first run,
# and auto-unseals on every subsequent restart.
#
# Init data (unseal key + root token) is written once to /vault/data/.init
# and persisted in the isp-vault-data Docker volume — it survives restarts.
# The root token alone is also written to /vault/data/.root_token for easy
# retrieval via:
#   docker exec isp-vault cat /vault/data/.root_token

set -e

VAULT_ADDR="http://127.0.0.1:8200"
INIT_FILE="/vault/data/.init"

# Start Vault server in the background
vault server -config=/vault/config/vault.hcl &
VAULT_PID=$!

# Wait until Vault's TCP port is accepting connections.
# vault status exit codes: 0 = unsealed, 1 = error/not up, 2 = sealed.
# We stop waiting as soon as exit code is NOT 1 (i.e. Vault is up, sealed or not).
echo "[vault] waiting for server to start..."
for i in $(seq 1 30); do
  vault status -address="$VAULT_ADDR" > /dev/null 2>&1
  CODE=$?
  if [ "$CODE" -ne 1 ]; then
    break
  fi
  sleep 1
done

# ── First-run initialisation ──────────────────────────────────────────────────
if [ ! -f "$INIT_FILE" ]; then
  echo "[vault] initialising (first run)..."
  vault operator init \
    -address="$VAULT_ADDR" \
    -key-shares=1 \
    -key-threshold=1 \
    > "$INIT_FILE"

  # Write the root token to a dedicated file so setup-secrets.sh can read it
  grep "Initial Root Token:" "$INIT_FILE" | awk '{print $NF}' \
    > /vault/data/.root_token

  echo "[vault] initialised — token saved to /vault/data/.root_token"
fi

# ── Unseal on every start ─────────────────────────────────────────────────────
UNSEAL_KEY=$(grep "Unseal Key 1:" "$INIT_FILE" | awk '{print $NF}')

vault status -address="$VAULT_ADDR" 2>&1 | grep -q "Sealed.*true" && {
  echo "[vault] unsealing..."
  vault operator unseal -address="$VAULT_ADDR" "$UNSEAL_KEY" > /dev/null
  echo "[vault] unsealed"
}

echo "[vault] ready at $VAULT_ADDR"

# Keep the foreground process alive
wait $VAULT_PID
