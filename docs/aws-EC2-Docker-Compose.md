# Deploying ISP on AWS EC2 with Docker Compose
### A Complete Beginner's Guide

---

## What You Will Build

By the end of this guide, you will have the Intelligent Search Platform running on a real server in the cloud, accessible from any browser.

```
Your Browser
     │
     ▼
EC2 Instance  (your server in AWS)
     │
  nginx :80  (reverse proxy — the front door)
     ├──  /v1/*  ──►  isp-api  :8001  (FastAPI backend)
     └──  /      ──►  isp-ui   :80    (Astro/Svelte UI)
                           │
                    ┌──────┼──────┐
                    ▼      ▼      ▼
                 isp-db  redis  typesense
                 :5432   :6379   :8108
               (private — not reachable from internet)
```

**Key idea:** Only port 80 (HTTP) is open to the internet. All databases and search engines live inside a private Docker network — the outside world cannot reach them directly.

---

## Prerequisites

- A Mac or Linux computer
- A terminal (Mac: press `Cmd + Space`, type "Terminal")
- A credit card for AWS (testing costs ~$2/day on a t3.large)

---

## Overview of Steps

| Phase | What you do |
|-------|-------------|
| 1. AWS Account | Create account, understand the console |
| 2. Key Pair | Create your SSH login key |
| 3. Security Group | Set up the firewall |
| 4. EC2 Instance | Launch your server |
| 5. Connect | SSH into the server |
| 6. Install Docker | Set up Docker on the server |
| 7. Secrets | Create your `.env.prod` file |
| 8. Deploy | Run the deploy script |
| 9. Verify | Confirm everything is working |

---

## Phase 1 — AWS Account

### Step 1.1 — Create an AWS Account

1. Go to **https://aws.amazon.com**
2. Click **"Create an AWS Account"** (top right)
3. Fill in your email, password, and account name
4. Choose **Personal** account type
5. Enter credit card details (you won't be charged for the free tier; t3.large is not free tier but costs ~$60/month)
6. Verify your phone number
7. Choose **Basic support (Free)**
8. Click **Complete sign up**

You will receive a confirmation email. Sign in at **https://console.aws.amazon.com**.

---

### Step 1.2 — Understanding the AWS Console

When you log in you will see the **AWS Management Console**. Key things to know:

```
┌─────────────────────────────────────────────────────────┐
│  AWS Console                              Region: ▼      │
│  Services ▼   Search bar                 us-east-1      │
├─────────────────────────────────────────────────────────┤
│  Recently visited:  EC2  |  VPC  |  IAM               │
└─────────────────────────────────────────────────────────┘
```

- **Region selector** (top right) — always set this to the same region throughout this guide. Use **us-east-1 (N. Virginia)** — it's cheapest.
- **Services menu** — click it to navigate to any AWS service (EC2, RDS, etc.)
- **Search bar** — fastest way to find a service (type "EC2" and press Enter)

> Before doing anything: click the **Region dropdown** (top right) and select **US East (N. Virginia) — us-east-1**.

---

## Phase 2 — Create a Key Pair

A **key pair** is how you log into your EC2 server. Think of it as a very strong password — a file on your computer that proves your identity. AWS keeps the lock; you keep the key.

### Step 2.1 — Create the Key Pair in AWS Console

1. In the AWS Console search bar, type **EC2** and press Enter
2. In the left sidebar, scroll down to **"Network & Security"**
3. Click **Key Pairs**
4. Click the orange **"Create key pair"** button (top right)
5. Fill in:
   - **Name:** `isp-key`
   - **Key pair type:** RSA
   - **Private key file format:** `.pem` (for Mac/Linux)
6. Click **"Create key pair"**

A file called `isp-key.pem` will automatically download to your `~/Downloads` folder.

### Step 2.2 — Secure the Key File

Open your terminal and run:

```bash
# Move the key to your .ssh folder
mv ~/Downloads/isp-key.pem ~/.ssh/isp-key.pem

# Lock it down — SSH refuses keys that are too open
chmod 400 ~/.ssh/isp-key.pem

# Verify it's there
ls -la ~/.ssh/isp-key.pem
# Should show: -r-------- (read-only for owner)
```

> **Important:** Never share this file, commit it to git, or delete it. If you lose it, you cannot SSH into your server and will need to recreate it.

---

## Phase 3 — Create a Security Group

A **security group** is a firewall that controls which traffic can reach your server. By default, everything is blocked. You add rules to allow specific ports.

### Step 3.1 — Create the Security Group

1. In the EC2 left sidebar → **"Network & Security"** → **Security Groups**
2. Click **"Create security group"**
3. Fill in:
   - **Security group name:** `isp-sg`
   - **Description:** `ISP application firewall`
   - **VPC:** leave as default (the one already selected)

### Step 3.2 — Add Inbound Rules

Under **Inbound rules**, click **"Add rule"** twice to add:

| Rule | Type | Port range | Source | Description |
|------|------|-----------|--------|-------------|
| 1 | SSH | 22 | My IP | SSH access (your IP only) |
| 2 | HTTP | 80 | Anywhere-IPv4 (0.0.0.0/0) | Web traffic |

When you select **"My IP"** for SSH, AWS automatically fills in your current public IP address. This means only your computer can SSH in — nobody else.

> We do NOT open ports 5432 (PostgreSQL), 8108 (Typesense), or 6379 (Redis). These stay private inside Docker's internal network.

4. Leave **Outbound rules** as default (all traffic allowed — your server needs this to download Docker images)
5. Click **"Create security group"**

---

## Phase 4 — Launch Your EC2 Instance

An **EC2 instance** is a virtual server in AWS. You choose how much CPU, RAM, and disk it has, and what operating system it runs.

### Step 4.1 — Launch the Instance

1. In the EC2 left sidebar → **Instances** → **Instances**
2. Click the orange **"Launch instances"** button

### Step 4.2 — Configure the Instance

**Name and tags**
- Name: `isp-server`

**Application and OS Images (AMI)**
- Click **"Ubuntu"** (the orange Ubuntu logo)
- Confirm it shows: **Ubuntu Server 22.04 LTS (HVM), SSD Volume Type**
- Architecture: **64-bit (x86)**

**Instance type**
- Click the dropdown, type `t3.large`, select **t3.large**
- This gives you 2 vCPU and 8 GB RAM — enough to run all 5 containers

> Why t3.large? PostgreSQL + Typesense + Redis + API + UI together need at least 4 GB RAM comfortably. t3.medium (4 GB) can work but may be tight.

**Key pair**
- Select **isp-key** from the dropdown (the one you created in Phase 2)

**Network settings**
- Click **"Edit"**
- VPC: leave as default
- Subnet: leave as default (any availability zone is fine)
- Auto-assign public IP: **Enable**
- Firewall: select **"Select existing security group"**
- Choose **isp-sg** (the one you created in Phase 3)

**Configure storage**
- Change **8 GiB** to **30 GiB**
- Volume type: **gp3**

> Why 30 GB? Typesense stores its search index on disk. Docker images also take space. 30 GB gives comfortable room for testing.

**Summary** (right side panel)
- Verify: 1 instance, t3.large, isp-key, isp-sg, 30 GB gp3
- Click **"Launch instance"**

### Step 4.3 — Get Your Server's IP Address

1. Go to **EC2 → Instances → Instances**
2. Wait until **Instance state** shows **"Running"** (refresh the page after ~1 minute)
3. Click on your instance name `isp-server`
4. Copy the **Public IPv4 address** (looks like `54.123.45.67`)

Save this IP — you will use it throughout the rest of this guide. Replace `<YOUR_EC2_IP>` in all commands below with this actual IP.

---

## Phase 5 — Connect to Your Server via SSH

**SSH** (Secure Shell) lets you run commands on your remote server from your local terminal, as if you were sitting in front of it.

### Step 5.1 — Connect

Open your terminal on your local Mac:

```bash
ssh -i ~/.ssh/isp-key.pem ubuntu@<YOUR_EC2_IP>
```

The first time, you will see:

```
The authenticity of host '54.123.45.67' can't be established.
ECDSA key fingerprint is SHA256:xxxx...
Are you sure you want to continue connecting (yes/no)?
```

Type `yes` and press Enter. You will then see:

```
Welcome to Ubuntu 22.04.3 LTS (GNU/Linux 6.2.0-1012-aws x86_64)
ubuntu@ip-172-31-xx-xx:~$
```

You are now inside your server. The `ubuntu@ip-...` prompt means commands you type run on EC2, not your Mac.

Type `exit` to return to your local machine anytime.

---

## Phase 6 — Install Docker on EC2

### Step 6.1 — Run the Setup Script

Exit back to your local machine first (`exit` if you're still SSH'd in).

From the ISP project directory on your Mac:

```bash
cd /Users/shekharmaheshwari/intelligent-search-platform

ssh -i ~/.ssh/isp-key.pem ubuntu@<YOUR_EC2_IP> "bash -s" < ec2-setup.sh
```

This command sends the `ec2-setup.sh` script to your server and runs it remotely. You will see a lot of output as it installs Docker. It takes about 1–2 minutes.

**What `ec2-setup.sh` does:**
1. Updates Ubuntu's package list (`apt-get update`)
2. Adds Docker's official package repository (so you get the real Docker, not the outdated Ubuntu version)
3. Installs Docker Engine and the Docker Compose plugin
4. Adds the `ubuntu` user to the `docker` group (so you can run `docker` without `sudo`)
5. Creates the `/opt/isp` directory where the app will live

### Step 6.2 — Reconnect Once (Required)

The docker group change only takes effect after a new login. SSH in and out once:

```bash
ssh -i ~/.ssh/isp-key.pem ubuntu@<YOUR_EC2_IP>
# You are now inside the server
exit
# Back on your Mac
```

### Step 6.3 — Verify Docker Works

```bash
ssh -i ~/.ssh/isp-key.pem ubuntu@<YOUR_EC2_IP> "docker run hello-world"
```

You should see:
```
Hello from Docker!
This message shows that your installation appears to be working correctly.
```

---

## Phase 7 — Prepare Your Secrets File

Your app needs passwords and API keys. These must never be hardcoded in code or committed to git. Instead, they live in a `.env.prod` file on your local machine and get copied to EC2 during deploy.

### Step 7.1 — Create `.env.prod`

On your local machine:

```bash
cd /Users/shekharmaheshwari/intelligent-search-platform
cp .env.prod.example .env.prod
```

### Step 7.2 — Edit `.env.prod`

Open the file:

```bash
open -e .env.prod
```

Replace the placeholder values:

```env
# PostgreSQL — choose any strong password
POSTGRES_USER=isp
POSTGRES_PASSWORD=MyStr0ngPassword123!

# Typesense API key — any strong string (used internally between services)
TYPESENSE_API_KEY=MyTypesenseKey456!

# Must match POSTGRES_USER and POSTGRES_PASSWORD above
DATABASE_URL=postgresql://isp:MyStr0ngPassword123!@isp-db:5432/isp

# Your real Voyage AI key
VOYAGE_API_KEY=pa-WqxFAGAxsDX9oeabjzs_R-BbkhMVA_pbyMGgXKtKHJX

HOST=0.0.0.0
PORT=8001
```

Save and close the file.

> `.env.prod` is listed in `.gitignore` — it will never be accidentally committed.

---

## Phase 8 — Deploy the Application

### Step 8.1 — Run the Deploy Script

From your local machine, in the project root:

```bash
cd /Users/shekharmaheshwari/intelligent-search-platform
./deploy-aws.sh <YOUR_EC2_IP> ~/.ssh/isp-key.pem
```

You will see three stages:

```
=== ISP AWS Deploy → 54.123.45.67 ===

[1/3] Syncing code to EC2...
```
**What happens:** `rsync` copies your entire project to `/opt/isp` on EC2. It skips `node_modules`, `.git`, build artifacts. On future deploys, it only sends changed files — very fast.

```
[2/3] Setting PUBLIC_API_URL for frontend build...
```
**What happens:** The Astro/Svelte frontend is a static website built at compile time. The API URL gets baked into the JavaScript bundle at build time — so it needs to know `http://<EC2_IP>/v1` before building. The script writes this into `.env.prod` automatically.

```
[3/3] Building and starting containers on EC2...
```
**What happens:**
1. Pulls `nginx`, `postgres:16`, `redis:7`, `typesense:0.25.2` images from Docker Hub (~500 MB total)
2. Builds your `isp-api` image (installs Python packages)
3. Builds your `isp-ui` image (runs `npm install` + `npm run build` + packages into nginx)
4. Runs `docker compose up -d` — starts all 6 containers in the background

**First deploy takes 5–8 minutes** (downloading images, compiling frontend). Subsequent deploys take 1–2 minutes.

### Step 8.2 — Expected Output

At the end you should see:

```
NAME          IMAGE            STATUS
isp-nginx     nginx:alpine     Up
isp-api       isp-api          Up
isp-ui        isp-ui           Up
isp-db        postgres:16      Up (healthy)
typesense     typesense:0.25   Up (healthy)
isp-redis     redis:7          Up (healthy)

=== Deployed ===
  UI     → http://54.123.45.67
  API    → http://54.123.45.67/v1
  Health → http://54.123.45.67/health
```

---

## Phase 9 — Verify Everything Works

### Step 9.1 — Test the API Health

```bash
curl http://<YOUR_EC2_IP>/health
```
Expected response:
```json
{"ok": true}
```

### Step 9.2 — Open the UI in Your Browser

Open your browser and go to:
```
http://<YOUR_EC2_IP>
```

You should see the ISP Management Interface.

### Step 9.3 — Check All Container Logs (if something is wrong)

SSH into the server:

```bash
ssh -i ~/.ssh/isp-key.pem ubuntu@<YOUR_EC2_IP>
```

Then run:

```bash
# See status of all containers
docker compose -f /opt/isp/docker-compose.prod.yml ps

# See logs for the API
docker compose -f /opt/isp/docker-compose.prod.yml logs isp-api

# Watch live logs (Ctrl+C to stop)
docker compose -f /opt/isp/docker-compose.prod.yml logs -f isp-api

# See logs for all services
docker compose -f /opt/isp/docker-compose.prod.yml logs
```

---

## Day-to-Day Operations

### Redeploy After Code Changes

Whenever you change code on your Mac, just re-run the deploy script:

```bash
./deploy-aws.sh <YOUR_EC2_IP> ~/.ssh/isp-key.pem
```

It only syncs changed files and rebuilds affected containers.

### SSH into the Server

```bash
ssh -i ~/.ssh/isp-key.pem ubuntu@<YOUR_EC2_IP>
```

### Stop All Services (on the server)

```bash
docker compose -f /opt/isp/docker-compose.prod.yml down
```

### Start All Services Again

```bash
docker compose -f /opt/isp/docker-compose.prod.yml --env-file /opt/isp/.env.prod up -d
```

### Restart a Single Service

```bash
docker compose -f /opt/isp/docker-compose.prod.yml restart isp-api
```

### Stop EC2 to Save Money (in AWS Console)

1. Go to **EC2 → Instances**
2. Select `isp-server`
3. Click **Instance state → Stop instance**

Your data is preserved. You are only charged for storage (~$0.08/day) while stopped. Start it again the same way when needed. Note: the Public IP changes when you stop/start — update your commands with the new IP.

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `Permission denied (publickey)` when SSH | Wrong key file or wrong user | Make sure you use `-i ~/.ssh/isp-key.pem` and user is `ubuntu` |
| `UNPROTECTED PRIVATE KEY FILE` error | Key permissions too open | Run `chmod 400 ~/.ssh/isp-key.pem` |
| Connection timeout when SSH | Security group missing port 22, or EC2 not running | Check security group inbound rules; check instance is running |
| Port 80 unreachable in browser | Security group missing port 80 | Add HTTP port 80 inbound rule in `isp-sg` |
| UI loads but API calls fail | `PUBLIC_API_URL` points to wrong host | Re-run `./deploy-aws.sh` — it updates the URL |
| Container keeps restarting | Check logs | `docker logs isp-db` or `docker logs typesense` |
| `docker: command not found` | Docker not installed or group not applied | Re-run `ec2-setup.sh` and reconnect once |
| Disk full on EC2 | Docker images accumulating | `docker system prune -f` removes unused images |

---

## Understanding the Architecture

Here is what each file does in this deployment:

```
your Mac
│
├── deploy-aws.sh          ← you run this to deploy
│     │
│     ├── rsync ──────────────► /opt/isp/ on EC2
│     └── ssh ─────────────────► runs docker compose on EC2
│
├── docker-compose.prod.yml  ← defines all 6 containers
│     ├── nginx      (port 80, public)
│     ├── isp-api    (port 8001, internal only)
│     ├── isp-ui     (port 80, internal only)
│     ├── isp-db     (port 5432, internal only)
│     ├── typesense  (port 8108, internal only)
│     └── isp-redis  (port 6379, internal only)
│
├── nginx/nginx.conf         ← routes /v1/ to API, / to UI
│
└── .env.prod                ← your secrets (never committed)
```

---

## Estimated AWS Cost

| Resource | Spec | Monthly Cost |
|----------|------|-------------|
| EC2 t3.large | 2 vCPU, 8 GB RAM | ~$60 |
| EBS gp3 30 GB | Disk storage | ~$2.50 |
| Data transfer | Minimal for testing | ~$1–5 |
| **Total (running 24/7)** | | **~$65/month** |
| **Total (stopped when not in use)** | Storage only | **~$2.50/month** |

> For testing, stop the instance when you are done for the day. Cost drops to almost nothing.

---

## What You Learned

| Concept | What it is in plain English |
|---------|----------------------------|
| EC2 | A virtual computer running in Amazon's data center |
| AMI | A pre-installed OS image (we used Ubuntu 22.04) |
| Instance type | How much CPU and RAM your server has |
| Key pair | A file that proves your identity to the server (like a very strong password) |
| Security group | A firewall that decides which ports are open to the internet |
| EBS volume | The hard disk attached to your EC2 instance |
| Public IP | The address of your server on the internet |
| SSH | A secure way to run commands on a remote server |
| Docker Compose | A tool to start multiple containers with one command |
| `rsync` | A smart file-copy tool that only transfers what changed |
| Reverse proxy (nginx) | A gatekeeper that routes requests to the right container |
| `.env.prod` | A file holding secrets — never committed to git |

---

## References

> Citations below are sourced from training knowledge (not live browsing). DOI and arXiv links are provided where known.

1. **Armbrust, M., Fox, A., Griffith, R., Joseph, A. D., Katz, R., Konwinski, A., Lee, G., Patterson, D., Rabkin, A., Stoica, I., & Zaharia, M.** (2010). "A View of Cloud Computing." *Communications of the ACM*, 53(4), 50–58. DOI: [10.1145/1721654.1721672](https://doi.org/10.1145/1721654.1721672) — Foundational cloud computing paper defining IaaS (EC2 is Infrastructure as a Service); motivates the trade-off between on-demand provisioning cost and operational flexibility that ISP deploys on.

2. **Merkel, D.** (2014). "Docker: Lightweight Linux Containers for Consistent Development and Deployment." *Linux Journal*, 2014(239). ISSN: 1075-3583 — Introduces Docker containers and images; ISP uses Docker Compose to run six containers (API, UI, PostgreSQL, Typesense, Redis, nginx) with a single `up -d` command.

3. **Fielding, R. T.** (2000). "Architectural Styles and the Design of Network-Based Software Architectures." *Doctoral dissertation, University of California, Irvine*. — Reverse proxy pattern: nginx routes `/v1/*` to the API and `/` to the UI, acting as the single public endpoint that enforces the REST interface contract.

4. **Saltzer, J. H., Reed, D. P., & Clark, D. D.** (1984). "End-To-End Arguments in System Design." *ACM Transactions on Computer Systems*, 2(4), 277–288. DOI: [10.1145/357401.357402](https://doi.org/10.1145/357401.357402) — End-to-end principle: internal services (PostgreSQL 5432, Typesense 8108, Redis 6379) are not exposed to the internet; security checks are implemented at the nginx boundary, not duplicated at each service.

5. **Amazon Web Services.** (2022). *AWS Well-Architected Framework — Security Pillar*. AWS Whitepaper. URL: [docs.aws.amazon.com/wellarchitected](https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/welcome.html) — Security group `isp-sg` with SSH restricted to `My IP` and HTTP open on port 80 follows the Principle of Least Privilege outlined in the Security Pillar.

6. **Tange, O.** (2011). "GNU Parallel: The Command-Line Power Tool." *USENIX ;login:*, 36(1), 42–47. — `rsync` (used in `deploy-aws.sh`) follows the same delta-transfer principle: only changed files are sent on re-deployment, reducing deploy times from minutes to seconds after the first deploy.
