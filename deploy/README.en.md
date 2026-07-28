# EGo Deployment Configurations

[中文](README.md) | English

> Back to project home: [README.en.md](../README.en.md)

All Docker Compose configs are in this directory. `default/` is the recommended quick start; `t1`–`t4` correspond to the four deployment tiers in `doc/architecture.md`. Pick one, `cd` into it, and run.

| Config | Directory | Network | Admin UI | Certificate | Use Case |
|--------|-----------|---------|----------|-------------|----------|
| Default | `default/` | `bridge` | HTTPS self-signed | Auto-generated | Quick start for beginners |
| T1 | `t1-host/` | `host` | HTTPS self-signed | Auto-generated | Home / LAN debugging, direct host ports |
| T2 | `t2-bridge/` | `bridge` | HTTPS self-signed | Auto-generated | Multi-container LAN, service-name access |
| T3 | `t3-nginx/` | `bridge` + Nginx | HTTPS trusted cert | Manual | Production with your own cert |
| T4 | `t4-acme/` | `bridge` + Nginx + acme.sh | HTTPS trusted cert | Let's Encrypt auto (Cloudflare DNS) | Public domain, fully automated |

## Why HTTPS for the Admin UI

The browser clipboard API (`navigator.clipboard`) used by various "copy" buttons only works in a **secure context** (HTTPS or localhost). Even for LAN debugging (T1/T2/Default), the admin UI uses built-in self-signed HTTPS — the browser warns once, just accept it. Self-signed certs are persisted via the `ego_certs` volume, so you won't need to accept again after a container rebuild.

Webhook receivers (`/in/...`) and health checks (`/api/health`) are machine-to-machine traffic and always go over HTTP (`WEB_PORT`).

## Certificate Rule (uniform across all tiers)

- **EGo 5001 admin**: always uses Flask's auto-generated **self-signed certificate** (persisted in the `ego_certs` volume). T1/T2/T3/T4 all follow this rule; browsing directly to `https://<IP>:5001` requires accepting the warning once.
- **Nginx (T3/T4 only)**: terminates TLS on 80/443 with a **real certificate**, then proxies to EGo's plaintext HTTP 5000. T3 uses a certificate you import manually; T4 uses a Let's Encrypt certificate that acme.sh issues/renews automatically.
- The two certificates are **independent**: EGo's self-signed cert lives in the `ego_certs` volume; nginx's cert lives in this directory's `certs/`. Public access goes through `https://<domain>/` (nginx, trusted cert); on the LAN you can also hit `https://<IP>:5001` directly (self-signed).

## How to Choose

- **LAN/internal use**, need clipboard buttons to work → **Default** or **T1** (host network) / **T2** (bridge, multi-container). All use self-signed HTTPS.
- **Public-facing**, need a green padlock → **T3** (existing cert) or **T4** (domain + Cloudflare, acme.sh DNS auto).

## Ports & Switches

- `WEB_PORT` (default 5000): HTTP — webhook receivers (`/in/...`) and `/api/health`.
- `WEB_SSL_PORT` (default 5001): HTTPS — admin UI (listens only when SSL is enabled).
- `EGO_SSL_ENABLED` (default 1): set to `0` to **fully disable** built-in HTTPS — skips cert generation, HTTP only, no redirect, session cookies without `Secure`. Only use this if you want pure HTTP or TLS is terminated upstream and you don't want double encryption.
- `HTTP_PORT` / `HTTPS_PORT` (T3/T4 only, default 80/443): host ports for nginx. Override via `.env` (the one-click script guides you and detects conflicts).

## Usage

### Default / T1 / T2 (Self-Signed HTTPS)

```bash
cd deploy/default        # or t1-host / t2-bridge
docker compose up -d
```

- Admin UI: `https://<host-IP>:5001` (accept the self-signed warning)
- Webhook / Health: `http://<host-IP>:5000`

> T1 uses host network: if ports 5000/5001 are taken (e.g., iStoreOS router's `miniupnpd` occupies 5000), change `WEB_PORT`/`WEB_SSL_PORT` in the compose to free ports (e.g., 5080/5081).

### T3 (Nginx + Manual Cert)

**One-click (bilingual script, detects port conflicts and lets you change them):**

```bash
curl -O https://raw.githubusercontent.com/codename-test/EverywhereYouGo/main/deploy/init.sh
chmod +x init.sh
./init.sh
# Choose language → option 4 → enter domain and cert paths; default HTTP=80/HTTPS=443, changeable on the fly
```

**Manual deployment (for customization):**

```bash
cd deploy/t3-nginx
mkdir -p certs
cp /path/to/your/ego.crt certs/ego.crt
cp /path/to/your/ego.key certs/ego.key
# Optional: custom nginx ports (default 80/443)
# echo "HTTP_PORT=8080" >> .env; echo "HTTPS_PORT=4430" >> .env
docker compose up -d
```

Access `https://<domain-or-IP>/`. Nginx terminates TLS on 443 with your imported cert and proxies all traffic to EGo's HTTP 5000. Path routing is handled internally by EGo — change `path_prefix` in the UI, nginx needs no changes. HTTP 80 redirects to HTTPS. EGo 5001 stays self-signed (independent); on the LAN you can hit `https://<IP>:5001` directly.

> If you use a custom HTTPS port (e.g., 4430) manually, also change the redirect in `nginx.conf` to `https://$host:4430$request_uri`; the one-click script handles this automatically.

### T4 (Nginx + acme.sh Auto, Cloudflare DNS)

**Certificate separation:** EGo 5001 uses Flask's self-signed cert (`ego_certs` volume, independent); nginx 443 uses the Let's Encrypt cert that acme.sh writes to `certs/`. nginx terminates TLS and proxies to EGo's HTTP 5000. The two certs don't interfere.

**Validation: Cloudflare DNS API** (no port 80 required — ideal when port 80 is taken, there's no public port 80, or behind NAT). Requirements:
- Domain hosted on Cloudflare;
- A Cloudflare API Token with `Zone:DNS:Edit` permission.

**One-click (bilingual script, detects port conflicts and lets you change them):**

```bash
curl -O https://raw.githubusercontent.com/codename-test/EverywhereYouGo/main/deploy/init.sh
chmod +x init.sh
./init.sh
# Choose language → option 5 → enter domain and Cloudflare API Token; default HTTP=80/HTTPS=443, changeable on the fly
```

**Manual deployment (for customization):**

```bash
cd deploy/t4-acme
mkdir -p certs
echo "your.domain" > certs/.domain            # change to your domain
sed -i 's/your.domain/your.domain/g' nginx.conf
# Put CF_Token in .env (so renewal still works after container/host restarts)
echo "CF_Token=your_Cloudflare_Token" > .env
# Optional: custom nginx ports (default 80/443)
# echo "HTTP_PORT=8080" >> .env; echo "HTTPS_PORT=4430" >> .env
docker compose up -d
```

On startup the `acme` container issues the certificate (including wildcard `*.your.domain`) via Cloudflare DNS validation and writes it to `certs/`; nginx detects the change and reloads automatically. It then auto-renews every 12 hours — fully automated, no manual intervention. On first boot nginx restarts a few times until the certificate is issued; this is normal.

> If you use a custom HTTPS port (e.g., 4430) manually, also change the redirect in `nginx.conf` to `https://$host:4430$request_uri`; the one-click script handles this automatically.

#### T4 Certificate Renewal Operations

- **Automatic renewal**: the `acme` container checks every 12 hours; as the certificate nears expiry (Let's Encrypt certs are valid 90 days) it renews via Cloudflare DNS, writes to `certs/`, and nginx reloads within 60 seconds. No manual intervention needed.
- **View renewal logs**: `docker compose logs -f acme`
- **Check current certificate validity**: `openssl x509 -in certs/ego.crt -noout -dates`
- **Force a manual renewal**: `docker compose exec acme acme.sh --renew -d your.domain --force`
- **Change domain**: edit `certs/.domain` and the domain in `nginx.conf`, then `docker compose restart acme nginx`.
- **Change Cloudflare Token**: edit `CF_Token` in `.env`, then `docker compose up -d` (recreates the acme container).
- **Troubleshoot failed renewal**: start with `docker compose logs acme`; common causes are an expired/under-permissioned CF_Token, or the domain not being hosted on Cloudflare.
