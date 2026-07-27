# EGo Deployment Configurations

All Docker Compose configs are in this directory. `default/` is the recommended quick start; `t1`–`t4` correspond to the four deployment tiers in `doc/architecture.md`. Pick one, `cd` into it, and run.

| Config | Directory | Network | Admin UI | Certificate | Use Case |
|--------|-----------|---------|----------|-------------|----------|
| Default | `default/` | `bridge` | HTTPS self-signed | Auto-generated | Quick start for beginners |
| T1 | `t1-host/` | `host` | HTTPS self-signed | Auto-generated | Home / LAN debugging, direct host ports |
| T2 | `t2-bridge/` | `bridge` | HTTPS self-signed | Auto-generated | Multi-container LAN, service name access |
| T3 | `t3-nginx/` | `bridge` + Nginx | HTTPS trusted cert | Manual | Production with existing cert |
| T4 | `t4-certbot/` | `bridge` + Nginx + Certbot | HTTPS trusted cert | Let's Encrypt auto | Public domain, fully automated |

## Why HTTPS for the Admin UI

The browser clipboard API (`navigator.clipboard`) used by various "copy" buttons only works in a **secure context** (HTTPS or localhost). Even for LAN debugging (T1/T2/Default), the admin UI uses built-in self-signed HTTPS — the browser will warn once, just accept it. Self-signed certs are persisted via the `ego_certs` volume, so you won't need to accept again after container rebuild.

Webhook receivers (`/in/...`) and health checks (`/api/health`) are machine-to-machine traffic and always go over HTTP (`WEB_PORT`).

## How to Choose

- **LAN/internal use**, need clipboard buttons to work → **Default** or **T1** (host network, most efficient) / **T2** (bridge, multi-container). All use self-signed HTTPS.
- **Public-facing**, need a green padlock → **T3** (existing cert) or **T4** (domain, Certbot auto).

## Ports & Switches

- `WEB_PORT` (default 5000): HTTP — Webhook receivers (`/in/...`) and `/api/health`.
- `WEB_SSL_PORT` (default 5001): HTTPS — Admin UI (listens only when SSL is enabled).
- `EGO_SSL_ENABLED` (default 1): Set to `0` to **fully disable** built-in HTTPS — skips cert generation, HTTP only, no redirect, session cookies without `Secure`. Only use this if you want pure HTTP or TLS is terminated by a reverse proxy and you don't want double encryption.

## Usage

### Default / T1 / T2 (Self-Signed HTTPS)

```bash
cd deploy/default        # or t1-host / t2-bridge
docker compose up -d
```

- Admin UI: `https://<host-IP>:5001` (accept self-signed cert warning)
- Webhook / Health: `http://<host-IP>:5000`

> T1 uses host network: if ports 5000/5001 are already taken (e.g., iStoreOS router's `miniupnpd` occupies 5000), change `WEB_PORT`/`WEB_SSL_PORT` in compose to free ports (e.g., 5080/5081).

### T3 (Nginx + Manual Cert)

```bash
cd deploy/t3-nginx
mkdir -p certs
cp /path/to/your/ego.crt certs/ego.crt
cp /path/to/your/ego.key certs/ego.key
docker compose up -d
```

Access `https://<domain-or-IP>/`. Nginx terminates TLS on 443 (your cert), proxies all traffic to EGo's HTTP 5000. Path routing is handled internally by EGo — change `path_prefix` in the UI, nginx needs no changes. Port 80 automatically redirects to 443.

### T4 (Nginx + Certbot Auto)

```bash
cd deploy/t4-certbot
# 1) First issue with standalone (nginx not running yet, certbot occupies port 80 temporarily)
mkdir certs
docker compose run --rm --service-ports certbot certonly --standalone \
  -d your.domain --email you@example.com --agree-tos --no-eff-email
# 2) Replace all your.domain in nginx.conf with your actual domain
# 3) Copy the issued cert to certs/ directory
docker compose run --rm certbot cp /etc/letsencrypt/live/your.domain/fullchain.pem /certs/ego.crt
docker compose run --rm certbot cp /etc/letsencrypt/live/your.domain/privkey.pem   /certs/ego.key
# 4) Start the full stack
docker compose up -d
```

The `certbot` container auto-renews every 12 hours (webroot mode). Renewed certs stay in the `certbot_conf` volume — you'll need to manually copy to `certs/` or configure a certbot renewal hook for auto-copy.
