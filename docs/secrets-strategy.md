# ISP Secrets Strategy

## Overview

ISP uses a **provider-based secret abstraction** so the app code never changes when moving between environments. A single `secrets.py` module handles all secret access; the active backend is controlled by one env var (`SECRET_PROVIDER`).

```
Local Dev          →   SECRET_PROVIDER=vault   →  Vault (Docker container)
Production (AWS)   →   SECRET_PROVIDER=aws     →  AWS Secrets Manager
Minimal / CI       →   SECRET_PROVIDER=env     →  .env file (current behaviour)
```

---

## Why not just use `.env` everywhere?

| Risk | `.env` file | Vault / Secrets Manager |
|---|---|---|
| Secrets on disk (EC2) | Yes — plaintext | No |
| Stolen = all secrets exposed | Yes | No — no key file to steal |
| Secret rotation after breach | Re-deploy required | Change value, done |
| Audit trail (who accessed what) | None | Vault audit log / CloudTrail |
| Works offline | Yes | Vault yes, AWS no |

`.env` is kept as the `env` fallback for CI and minimal local setups only.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        ISP Backend                          │
│                                                             │
│   config.py / routers / services                           │
│       │                                                     │
│       │  get_secret("VOYAGE_API_KEY")                      │
│       ▼                                                     │
│   secrets.py  ◄── SECRET_PROVIDER env var                  │
│       │                                                     │
│       ├── "env"   → os.getenv()          (.env file)       │
│       ├── "vault" → hvac client          (Vault container) │
│       └── "aws"   → boto3 client         (Secrets Manager) │
└─────────────────────────────────────────────────────────────┘

Local (Docker Compose)              AWS (EC2 + Secrets Manager)
──────────────────────────          ───────────────────────────
┌──────────┐  ┌────────┐           ┌──────────┐
│ isp-api  │→ │ Vault  │           │ isp-api  │→  AWS Secrets Manager
│ isp-ui   │  │ :8200  │           │ isp-ui   │   (separate AWS service)
│ isp-db   │  └────────┘           │ isp-db   │   IAM Role — no key on disk
│ typesense│                       │ typesense│
│ redis    │                       │ redis    │
└──────────┘                       └──────────┘
```

---

## Secrets inventory

All secrets stored under the `isp/` path prefix:

| Key | Description | Example |
|---|---|---|
| `isp/POSTGRES_PASSWORD` | PostgreSQL password | `str0ng-db-pass` |
| `isp/TYPESENSE_API_KEY` | Typesense admin key | `ts-key-change-me` |
| `isp/VOYAGE_API_KEY` | Voyage AI embedding API key | `pa-xxx...` |
| `isp/GOOGLE_SERVICE_ACCOUNT_JSON` | Google service account key (Sheets connector) | `{"type":"service_account",...}` |
| `isp/OAUTH_CLIENT_SECRET` | OAuth2 client secret (Google, GitHub, or Microsoft) — required when `AUTH_ENABLED=true` | `GOCSPX-xxx...` |
| `isp/JWT_SECRET` | HS256 signing key for admin session JWTs — required when `AUTH_ENABLED=true` | 32+ random hex chars |

> **Note:** `OAUTH_PROVIDER`, `OAUTH_CLIENT_ID`, `OAUTH_TENANT_ID`, and `OAUTH_REDIRECT_URI` are not secrets — they are plain config values that live in `.env` and are not stored in Vault or AWS Secrets Manager.

---

## Implementation

### 1. `secrets.py`

`backend/src/isp/services/infra/secrets.py` — the **only file** that knows about Vault or AWS. Nothing else in ISP imports `hvac` or `boto3` directly.

Two public functions:

**`get_secret(key)`** — fetches one secret from the active provider.

**`bootstrap_secrets()`** — fetches all `_SECRET_KEYS` and injects them into `os.environ` at process startup. No-op when `SECRET_PROVIDER=env`. Everything else in ISP (`config.py`, routers, services) then reads from `os.environ` as normal — zero other code changes.

```python
def get_secret(key: str) -> str:

    if provider == "vault":
        # hvac connects to the Vault container via VAULT_ADDR and authenticates
        # using VAULT_TOKEN. In local dev both are set in backend/.env:
        #   VAULT_ADDR=http://isp-vault:8200   (container name on isp-net)
        #   VAULT_TOKEN=isp-dev-token          (root token set in docker-compose)
        # Secrets are stored under the "isp" KV v2 mount, one secret per key:
        #   vault kv put isp/VOYAGE_API_KEY value="..."
        # The full path Vault resolves is: isp/data/<key> (KV v2 adds /data/).
        client = hvac.Client(
            url=os.getenv("VAULT_ADDR", "http://isp-vault:8200"),
            token=os.getenv("VAULT_TOKEN"),
        )
        secret = client.secrets.kv.v2.read_secret_version(path=key, mount_point="isp")
        return secret["data"]["data"]["value"]

    elif provider == "aws":
        # boto3 resolves credentials automatically via the credential chain:
        #   1. AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY env vars  (local dev)
        #   2. ~/.aws/credentials file written by `aws configure`   (local dev)
        #   3. IAM Role attached to the EC2 instance               (production)
        #      → boto3 calls http://169.254.169.254/latest/meta-data/iam/
        #        to get a temporary rotating token; nothing on disk to steal
        # No explicit credentials are passed — boto3 picks the right source
        # depending on where the process is running.
        client = boto3.client("secretsmanager", region_name=os.getenv("AWS_REGION", "us-east-1"))
        secret = client.get_secret_value(SecretId=f"isp/{key}")
        return secret["SecretString"]

    else:
        return os.getenv(key, "")
```

### 2. Bootstrap call in `main.py`

`bootstrap_secrets()` is called at the very top of `main.py`, before any ISP import that would trigger `config.py` to load. `config.py` and pydantic-settings are unchanged — they pick up the injected env vars naturally.

```python
# main.py — before any other isp imports
from isp.services.infra.secrets import bootstrap_secrets
bootstrap_secrets()

# now safe to import everything else
from isp.config import settings
from isp.routers import indexes, search, ...
```

### 3. New pip dependencies

Added to `pyproject.toml` (lazily imported — only loaded when their provider is active):

```toml
"hvac>=2.0.0",    # HashiCorp Vault client — used when SECRET_PROVIDER=vault
"boto3>=1.34.0",  # AWS SDK — used when SECRET_PROVIDER=aws
```

---

## Local setup — Vault in Docker

### Vault service in `docker-compose.yml`

Already added. The container is named `isp-vault` and runs on `isp-net`, so
`isp-api` reaches it at `http://isp-vault:8200` without exposing it externally
beyond port `8200` on localhost.

```yaml
isp-vault:
  image: hashicorp/vault:latest
  container_name: isp-vault
  ports:
    - "8200:8200"
  environment:
    VAULT_DEV_ROOT_TOKEN_ID: isp-dev-token
    VAULT_DEV_LISTEN_ADDRESS: "0.0.0.0:8200"
  cap_add:
    - IPC_LOCK
  networks:
    - isp-net
```

### Populate secrets (one time, run from your Mac)

```bash
export VAULT_ADDR=http://localhost:8200
export VAULT_TOKEN=isp-dev-token

# Enable KV v2 secrets engine under the "isp" mount
vault secrets enable -path=isp kv-v2

# Store all ISP secrets — one key per secret, value field is mandatory (KV v2)
vault kv put isp/POSTGRES_PASSWORD           value="your-db-password"
vault kv put isp/TYPESENSE_API_KEY           value="your-typesense-key"
vault kv put isp/VOYAGE_API_KEY              value="your-voyage-key"
vault kv put isp/GOOGLE_SERVICE_ACCOUNT_JSON value='{"type":"service_account",...}'
vault kv put isp/OAUTH_CLIENT_SECRET         value="your-oauth-client-secret"
vault kv put isp/JWT_SECRET                  value="your-32-char-random-string"
```

Internally Vault stores each secret at path `isp/data/<key>` (KV v2 inserts `/data/`
automatically). `secrets.py` uses `mount_point="isp"` + `path=key` so this is handled
transparently.

### `backend/.env` for local Vault mode

```env
SECRET_PROVIDER=vault
VAULT_ADDR=http://isp-vault:8200   # container name on isp-net
VAULT_TOKEN=isp-dev-token          # root token set in docker-compose
```

---

## AWS setup — Secrets Manager

### How boto3 authenticates

`boto3` resolves AWS credentials automatically via the credential chain — no
explicit credentials are ever passed in code:

```
boto3.client("secretsmanager")
        │
        │  checks in order:
        ▼
1. AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY   env vars      (local dev)
2. ~/.aws/credentials                           aws configure  (local dev)
3. IAM Role on EC2 instance                    metadata API   (production)
   → http://169.254.169.254/latest/meta-data/iam/security-credentials/
   → temporary rotating token, nothing on disk to steal
```

On your Mac, run `aws configure` once. On EC2, attach an IAM Role — boto3 picks
it up automatically with no credentials file needed.

### Store secrets (one time, run from your Mac)

```bash
aws secretsmanager create-secret \
  --name isp/POSTGRES_PASSWORD \
  --secret-string "your-db-password"

aws secretsmanager create-secret \
  --name isp/TYPESENSE_API_KEY \
  --secret-string "your-typesense-key"

aws secretsmanager create-secret \
  --name isp/VOYAGE_API_KEY \
  --secret-string "your-voyage-key"

aws secretsmanager create-secret \
  --name isp/GOOGLE_SERVICE_ACCOUNT_JSON \
  --secret-string '{"type":"service_account",...}'

aws secretsmanager create-secret \
  --name isp/OAUTH_CLIENT_SECRET \
  --secret-string "your-oauth-client-secret"

aws secretsmanager create-secret \
  --name isp/JWT_SECRET \
  --secret-string "your-32-char-random-string"
```

### IAM Role for EC2

Create an IAM Role with this policy and attach it to the EC2 instance:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "arn:aws:secretsmanager:us-east-1:*:secret:isp/*"
    }
  ]
}
```

No access keys. No credentials on disk. The EC2 instance authenticates via the role automatically.

### `backend/.env` for AWS mode

```env
SECRET_PROVIDER=aws
AWS_REGION=us-east-1
```

---

## Migration path: Vault → AWS Secrets Manager

**No code changes required.** The only change is in `.env`:

```env
# before (local Vault)
SECRET_PROVIDER=vault
VAULT_ADDR=http://vault:8200
VAULT_TOKEN=isp-dev-token

# after (AWS)
SECRET_PROVIDER=aws
AWS_REGION=us-east-1
```

Then populate the same secrets in AWS Secrets Manager (see AWS setup above) and deploy.

---

## Environment summary

| | Local `.env` | Local Vault (Docker) | AWS Secrets Manager |
|---|---|---|---|
| `SECRET_PROVIDER` | `env` | `vault` | `aws` |
| Secrets on disk | Yes (plaintext) | No | No |
| Private key on disk | No | Vault token in `.env` | No (IAM Role) |
| Works offline | Yes | Yes | No |
| Audit trail | None | Vault audit log | CloudTrail |
| Secret rotation | Re-deploy | `vault kv put` | AWS console / CLI |
| Cost | Free | Free | ~$2–3/month |
| Code changes to migrate | — | Write `secrets.py` once | Change one env var |
| Best for | Dev / CI | Local dev parity with AWS | Production |

---

## What does NOT change between environments

- All ISP application code — routers, services, models
- `docker-compose.yml` structure (Vault is just one more service)
- The `deploy-aws.sh` script logic
- Typesense, PostgreSQL, Redis configuration

The entire migration surface is: **one env var**.
