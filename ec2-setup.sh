#!/usr/bin/env bash
# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

# Run once on a fresh Ubuntu 22.04 / 24.04 EC2 instance to install Docker.
# Usage: ssh -i key.pem ubuntu@<EC2_IP> "bash -s" < ec2-setup.sh
set -e

echo "=== Installing Docker ==="
sudo apt-get update -y
sudo apt-get install -y ca-certificates curl gnupg lsb-release

sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update -y
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

sudo usermod -aG docker "$USER"
sudo systemctl enable docker
sudo systemctl start docker

echo ""
echo "=== Creating app directory ==="
sudo mkdir -p /opt/isp
sudo chown "$USER:$USER" /opt/isp

echo ""
echo "Done. Log out and back in for docker group to take effect."
echo "App will be deployed to /opt/isp"
