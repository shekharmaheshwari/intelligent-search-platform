#!/usr/bin/env bash
# Deploy ISP to an EC2 instance via rsync + docker compose.
#
# Usage:
#   ./deploy-aws.sh <EC2_IP> <path/to/key.pem>
#
# Example:
#   ./deploy-aws.sh 54.123.45.67 ~/.ssh/isp-key.pem
#
# Prerequisites:
#   - EC2 instance already set up via ec2-setup.sh
#   - .env.prod file exists locally (copy from .env.prod.example and fill in values)
set -e

EC2_IP="${1:?Usage: ./deploy-aws.sh <EC2_IP> <path/to/key.pem>}"
KEY_FILE="${2:?Usage: ./deploy-aws.sh <EC2_IP> <path/to/key.pem>}"
REMOTE_USER="ubuntu"
REMOTE_DIR="/opt/isp"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -f "$SCRIPT_DIR/.env.prod" ]; then
  echo "ERROR: .env.prod not found. Copy .env.prod.example and fill in your values."
  exit 1
fi

echo "=== ISP AWS Deploy → $EC2_IP ==="

echo ""
echo "[1/3] Syncing code to EC2..."
rsync -az --progress \
  --exclude '.git' \
  --exclude 'node_modules' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude 'frontend/dist' \
  --exclude 'frontend/node_modules' \
  --exclude 'backend/.env' \
  -e "ssh -i $KEY_FILE -o StrictHostKeyChecking=no" \
  "$SCRIPT_DIR/" \
  "$REMOTE_USER@$EC2_IP:$REMOTE_DIR/"

echo ""
echo "[2/3] Setting PUBLIC_API_URL for frontend build..."
# Frontend JS is built at docker build time, so it needs the live EC2 IP baked in.
ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no "$REMOTE_USER@$EC2_IP" bash <<EOF
  cd $REMOTE_DIR
  # Inject PUBLIC_API_URL pointing at this EC2's public IP
  if ! grep -q "^PUBLIC_API_URL=" .env.prod; then
    echo "PUBLIC_API_URL=http://$EC2_IP/v1" >> .env.prod
  else
    sed -i "s|^PUBLIC_API_URL=.*|PUBLIC_API_URL=http://$EC2_IP/v1|" .env.prod
  fi
EOF

echo ""
echo "[3/3] Building and starting containers on EC2..."
ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no "$REMOTE_USER@$EC2_IP" bash <<EOF
  set -e
  cd $REMOTE_DIR
  docker compose -f docker-compose.prod.yml --env-file .env.prod pull typesense isp-redis isp-db nginx
  docker compose -f docker-compose.prod.yml --env-file .env.prod build --no-cache isp-api isp-ui
  docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
  echo ""
  echo "Waiting for services..."
  sleep 10
  docker compose -f docker-compose.prod.yml ps
EOF

echo ""
echo "=== Deployed ==="
echo "  UI   → http://$EC2_IP"
echo "  API  → http://$EC2_IP/v1"
echo "  Health → http://$EC2_IP/health"
