# Deploying ISP on AWS EC2 with Docker Compose

## Overview

This guide walks through deploying the Intelligent Search Platform (ISP) on a single AWS EC2 instance using Docker Compose. All five services (API, UI, PostgreSQL, Typesense, Redis) run as containers behind an nginx reverse proxy.

```
Internet
    │
    ▼
EC2 Instance (Ubuntu 22.04)
    │
nginx :80
    ├── /v1/  → isp-api:8001   (FastAPI)
    └── /     → isp-ui:80      (Astro/Svelte)
                    │
              isp-db:5432       (PostgreSQL)
              typesense:8108    (Typesense)
              isp-redis:6379    (Redis)
```

**Important:** Internal services (PostgreSQL, Typesense, Redis) are NOT exposed to the internet — they live only on the internal Docker network.

---

## Files Created

| File | Purpose |
|------|---------|
| `docker-compose.prod.yml` | Production compose — no host port exposure for internal services |
| `nginx/nginx.conf` | Reverse proxy: routes `/v1/` to API, `/` to UI |
| `ec2-setup.sh` | One-time Docker install on EC2 (run once per instance) |
| `deploy-aws.sh` | Main deploy script — rsync code + start containers remotely |
| `.env.prod.example` | Template for production secrets |

---

## Step-by-Step Deployment Plan

### Step 1 — Launch an EC2 Instance

1. Go to **AWS Console → EC2 → Launch Instance**
2. Choose:
   - **AMI:** Ubuntu Server 22.04 LTS (64-bit x86)
   - **Instance type:** `t3.large` (2 vCPU, 8 GB RAM) — minimum for all 5 services
   - **Storage:** 30 GB gp3 EBS (Typesense index data lives here)
3. **Key pair:** Create or select an existing `.pem` key — you need this to SSH in
4. **Security group — open these ports:**

   | Port | Protocol | Source | Purpose |
   |------|----------|--------|---------|
   | 22   | TCP | Your IP only | SSH access |
   | 80   | TCP | 0.0.0.0/0 | HTTP (UI + API via nginx) |

   > Do NOT open 5432, 8108, 6379, 8001, or 3001 — these stay internal.

5. Launch the instance and note the **Public IPv4 address**.

---

### Step 2 — Prepare Local Secrets File

Copy the example env file and fill in your values:

```bash
cp .env.prod.example .env.prod
```

Edit `.env.prod`:

```env
POSTGRES_USER=isp
POSTGRES_PASSWORD=<strong-password>
TYPESENSE_API_KEY=<strong-api-key>
DATABASE_URL=postgresql://isp:<strong-password>@isp-db:5432/isp
VOYAGE_API_KEY=<your-voyage-api-key>
HOST=0.0.0.0
PORT=8001
```

> `.env.prod` is in `.gitignore` — never commit it.

---

### Step 3 — Set Up Docker on EC2 (One Time Only)

Run the setup script on the EC2 instance:

```bash
ssh -i ~/.ssh/your-key.pem ubuntu@<EC2_IP> "bash -s" < ec2-setup.sh
```

This installs Docker, Docker Compose plugin, creates `/opt/isp`, and adds the `ubuntu` user to the docker group.

After it completes, reconnect to the instance once to refresh the group membership:

```bash
ssh -i ~/.ssh/your-key.pem ubuntu@<EC2_IP>
# then exit immediately
exit
```

---

### Step 4 — Deploy

From your local machine, in the project root:

```bash
./deploy-aws.sh <EC2_IP> ~/.ssh/your-key.pem
```

**What the script does:**
1. `rsync` — copies all project files to `/opt/isp` on EC2 (excludes `node_modules`, `.git`, build artifacts)
2. Injects `PUBLIC_API_URL=http://<EC2_IP>/v1` into `.env.prod` so the frontend JS knows where to call the API
3. Pulls base images (nginx, postgres, redis, typesense)
4. Builds `isp-api` and `isp-ui` Docker images on EC2
5. Starts all containers with `docker compose up -d`

**Expected output at the end:**

```
=== Deployed ===
  UI     → http://<EC2_IP>
  API    → http://<EC2_IP>/v1
  Health → http://<EC2_IP>/health
```

---

### Step 5 — Verify

```bash
# Check all containers are running
ssh -i ~/.ssh/your-key.pem ubuntu@<EC2_IP> \
  "docker compose -f /opt/isp/docker-compose.prod.yml ps"

# Check API health
curl http://<EC2_IP>/health
# Expected: {"ok": true}

# Open the UI
open http://<EC2_IP>
```

---

## Re-deploying After Code Changes

Just re-run the deploy script — it rsyncs only changed files and rebuilds containers:

```bash
./deploy-aws.sh <EC2_IP> ~/.ssh/your-key.pem
```

---

## Useful Remote Commands

```bash
# SSH into the instance
ssh -i ~/.ssh/your-key.pem ubuntu@<EC2_IP>

# View live logs
docker compose -f /opt/isp/docker-compose.prod.yml logs -f

# View logs for a specific service
docker compose -f /opt/isp/docker-compose.prod.yml logs -f isp-api

# Restart a single service (e.g. after config change)
docker compose -f /opt/isp/docker-compose.prod.yml restart isp-api

# Stop everything
docker compose -f /opt/isp/docker-compose.prod.yml down

# Stop and wipe all data volumes (destructive)
docker compose -f /opt/isp/docker-compose.prod.yml down -v
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| UI loads but API calls fail (404/CORS) | `PUBLIC_API_URL` not set correctly | Re-run `deploy-aws.sh` — it updates the URL automatically |
| Container stays in "starting" state | DB/Typesense health check failing | `docker logs isp-db` or `docker logs typesense` to see the error |
| `Permission denied` running docker | ubuntu not in docker group | Log out and back in after `ec2-setup.sh` |
| Port 80 unreachable | Security group missing inbound rule | Add port 80 TCP 0.0.0.0/0 in EC2 → Security Groups |
| Typesense keeps restarting | Disk full | Increase EBS volume or free up space with `docker system prune` |

---

## Estimated Cost (AWS us-east-1)

| Resource | Spec | Cost/month |
|----------|------|-----------|
| EC2 t3.large | 2 vCPU, 8 GB | ~$60 |
| EBS gp3 30 GB | Storage | ~$2.50 |
| Data transfer | Minimal for testing | ~$1-5 |
| **Total** | | **~$65/mo** |

Stop the instance when not in use to save costs — data volumes persist when the instance is stopped.
