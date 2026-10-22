# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Secret provider abstraction for ISP.

Controlled by the SECRET_PROVIDER env var:
  - "env"   → os.getenv() from .env file (default, current behaviour)
  - "vault" → HashiCorp Vault running as a Docker container (local dev)
  - "aws"   → AWS Secrets Manager (production)

Call bootstrap_secrets() once at process startup (before config.py loads)
to inject secrets from the active provider into os.environ. Everything else
in ISP then reads from os.environ as normal — no other code needs to change.
"""
from __future__ import annotations
import logging
import os

log = logging.getLogger(__name__)

# All secret keys ISP reads from the environment.
# These are fetched from Vault / AWS and injected into os.environ at startup.
_SECRET_KEYS = [
    "TYPESENSE_API_KEY",
    "VOYAGE_API_KEY",
    "OPENAI_API_KEY",
    "DATABASE_URL",
    "GOOGLE_SERVICE_ACCOUNT_JSON",
    "OAUTH_CLIENT_SECRET",
    "JWT_SECRET",
]


def get_secret(key: str) -> str:
    """Fetch a single secret by key from the configured provider."""
    provider = os.getenv("SECRET_PROVIDER", "env")

    if provider == "vault":
        import hvac  # type: ignore[import]
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
        try:
            secret = client.secrets.kv.v2.read_secret_version(
                path=key,
                mount_point="isp",
            )
            return secret["data"]["data"]["value"]
        except Exception as e:
            log.warning("Vault: failed to read secret %s: %s", key, e)
            return ""

    elif provider == "aws":
        import boto3  # type: ignore[import]
        # boto3 resolves credentials automatically via the credential chain:
        #   1. AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY env vars  (local dev)
        #   2. ~/.aws/credentials file written by `aws configure`   (local dev)
        #   3. IAM Role attached to the EC2 instance               (production)
        #      → boto3 calls http://169.254.169.254/latest/meta-data/iam/
        #        to get a temporary rotating token; nothing on disk to steal
        # No explicit credentials are passed here — boto3 picks the right
        # source depending on where the process is running.
        client = boto3.client(
            "secretsmanager",
            region_name=os.getenv("AWS_REGION", "us-east-1"),
        )
        try:
            secret = client.get_secret_value(SecretId=f"isp/{key}")
            return secret["SecretString"]
        except Exception as e:
            log.warning("AWS Secrets Manager: failed to read secret %s: %s", key, e)
            return ""

    else:
        return os.getenv(key, "")


def bootstrap_secrets() -> None:
    """
    Fetch all ISP secrets from the active provider and inject into os.environ.

    Must be called before any ISP module that reads config (i.e. before
    config.py is imported). Safe to call multiple times — skips keys that
    are already set in os.environ.

    No-op when SECRET_PROVIDER=env (default) since values are already in
    os.environ via the .env file loaded by pydantic-settings / python-dotenv.
    """
    provider = os.getenv("SECRET_PROVIDER", "env")
    if provider == "env":
        return

    log.info("Bootstrapping secrets from provider: %s", provider)
    for key in _SECRET_KEYS:
        if os.environ.get(key):
            continue  # already set — don't overwrite
        value = get_secret(key)
        if value:
            os.environ[key] = value
            log.debug("Loaded secret: %s", key)
