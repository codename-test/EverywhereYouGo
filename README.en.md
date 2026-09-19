# EverywhereYouGo (EGo) v1.3.0

[中文](README.md) | English

> Universal Message Forwarding Platform — Data → Parse → Route → Push

Receives any HTTP request, extracts structured fields through parsers, routes by conditions to multiple push channels.

## Docker Deployment

**One-click Deployment (Recommended):**

```bash
curl -O https://raw.githubusercontent.com/codename-test/EverywhereYouGo/main/deploy/init.sh
chmod +x init.sh
./init.sh
# Follow prompts to select deployment mode
```

Supports 5 deployment modes: default (quick start), t1-host (host network), t2-bridge (bridge network), t3-nginx (Nginx + manual certificate), t4-acme (Nginx + Let's Encrypt auto certificate).

> More deployment options in [deploy/README.en.md](deploy/README.en.md).

After startup: Admin UI at `https://<Host IP>:5001` (self-signed certificate, browser needs to allow); Webhook receiver and health check on `http://<Host IP>:5000`.

## Architecture

```
HTTP POST → Data Source → Parser → Route Match → Template Render → Push Channel
```

| Component | Description |
|------|------|
| **Data Source** | Listens on port to receive HTTP POST |
| **Parser** | Python script, extracts fields and defines variable names |
| **Route** | Condition expression matches channel-template pairs |
| **Template** | Simple / Jinja2 renders title and content |
| **Channel** | WeChat Work, DingTalk, Feishu, Telegram, Bark |

## Authentication

Set `EGO_AUTH_TOKEN` environment variable to enable access control:

```bash
EGO_AUTH_TOKEN=your-secret-token python3 main.py
```

- Web pages require login via token input page
- API calls require `Authorization: Bearer your-secret-token` header
- Health check `/api/health` does not require authentication

Optionally set `EGO_SECRET_KEY` to customize Flask session key.

## Configuration Files

Configuration lives in two places, with distinct roles:

| Storage | Role |
|---------|------|
| SQLite (`ego.db`) | **Runtime source of truth** — all reads/writes go through it |
| `config/*.json` | **Export / backup medium** — for backup, versioning and migration |

| File | Content |
|------|---------|
| `config/parsers.json` | Parser metadata |
| `config/sources.json` | Data source definitions |
| `config/channels.json` | Push channel configurations |
| `config/templates.json` | Push templates |
| `config/bindings.json` | Channel bindings (with condition expressions) |

**Load rules at startup:**

1. Database **not empty** → the database wins; JSON is not read, and the current
   config is **written back** to `config/*.json` as a snapshot
2. Database **empty** and JSON present → import from JSON (first run / migration / restore)
3. Database empty and no JSON → export the initial config to JSON

So **use the WebUI for day-to-day config changes** (they take effect immediately).
Hand-editing `config/*.json` is only read on first import when the database is empty —
it is not the normal path for applying changes.

System settings (DND, log level, etc.), the message log and the queue are also stored in SQLite.
For backup/restore use **Settings → Backup**, which packages `config/*.json` + `parsers/*.py`.

## Parsers

Place `.py` files in `parsers/` directory, define a `parse()` function:

```python
def parse(raw_body: bytes, headers: dict, query_params: dict) -> dict:
    data = json.loads(raw_body)
    event = data.get("Event", "")
    name = data.get("Item", {}).get("Name", "")
    return {
        "title": name,
        "event": event,
        "name": name,
    }
```

Fields in returned dict except `title` are also used for:
- **Route condition matching**: `event == 'library.new' and media_type == 'Movie'`
- **Template variable reference**: `{name}` / `{{ msg.name }}`

## Route Conditions

Supports `and`, `or`, parentheses grouping:

| Example | Description |
|------|------|
| `event == 'library.new'` | New items only |
| `event == 'library.new' and media_type == 'Movie'` | New movies only |
| `event == 'library.new' or event == 'test'` | New items or test messages |

## Features

### Do Not Disturb (DND)
Set DND time period, messages enter queue and wait, automatically flush when period ends. Urgent routes are not affected by DND.

### Message Deduplication
Channel bindings can configure `dedup_key_expr` and `dedup_window` (default 3600 seconds). Same dedup key will not be sent repeatedly within the window.

### Parallel Push
When multiple channels match, thread pool sends in parallel, total latency depends on the slowest single channel.

### Sample Data & Online Debugging
Each data source automatically saves the last 20 request samples, can select samples in WebUI for test parsing and pushing.

### Message Resend
Failed messages support original resend (using parsed msg_json) or re-parse and resend.

### Import & Export
- **Backup**: Download ZIP package (`config/*.json` + `parsers/*.py`)
- **Restore**: Upload ZIP package, automatically takes effect after overwriting configuration
- **JSON Import**: Supports dry_run preview, insert/overwrite two modes, dependency check

## Internationalization

Built-in Chinese and English bilingual support, switch languages anytime via language switch button in top-right corner of navigation bar.

## Channel Types

| Channel | Method | Type Identifier |
|------|------|---------|
| WeChat Work Bot | Webhook | `wechat_work_bot` |
| WeChat Work API | App Message | `wechat_work_api` |
| DingTalk | Webhook | `dingtalk` |
| Feishu | Webhook | `feishu` |
| Telegram | Bot API | `telegram_bot` |
| Bark | API | `bark` |

## Environment Variables

| Variable | Default | Description |
|------|--------|------|
| `WEB_PORT` | `5000` | HTTP port (Webhook receiver / health check) |
| `WEB_SSL_PORT` | `5001` | HTTPS port (Admin page, not enabled when certificate is missing) |
| `EGO_SSL_ENABLED` | `1` | Set to `0` to completely disable built-in HTTPS (HTTP only, no redirect, no certificate generation) |
| `EGO_SSL_DIR` | `./certs` | SSL certificate directory, where `ego.crt` and `ego.key` are stored |
| `EGO_SSL_CERT` | `./certs/ego.crt` | Certificate file path (overrides `EGO_SSL_DIR`) |
| `EGO_SSL_KEY` | `./certs/ego.key` | Private key file path (overrides `EGO_SSL_DIR`) |
| `DB_PATH` | `ego.db` | Database path |
| `LOG_LEVEL` | `INFO` | Log level |
| `EGO_AUTH_TOKEN` | *(empty)* | Access control Token |
| `EGO_SECRET_KEY` | *(auto)* | Flask session key |
| `EGO_INGRESS_WORKERS` | `8` | Ingress worker threads per port source |
| `EGO_INGRESS_MAX_QUEUE` | `200` | Ingress queue cap; beyond it returns 503 (backpressure) |
| `EGO_CLEANUP_INTERVAL` | `600` | Interval for purging old messages / dedup keys (s) |
| `EGO_BREAKER_WINDOW` | `60` | Circuit breaker sliding window (s) |
| `EGO_BREAKER_MIN_SAMPLES` | `5` | Min samples before the failure-ratio rule applies |
| `EGO_BREAKER_FAILURE_RATIO` | `0.5` | Failure ratio that trips the breaker |
| `EGO_BREAKER_CONSECUTIVE` | `5` | Consecutive-failure threshold (low-traffic channels) |
| `EGO_BREAKER_OPEN_BASE` | `30` | Base cooldown (s), doubles on each open |
| `EGO_BREAKER_OPEN_MAX` | `600` | Cooldown cap (s) |
| `EGO_BREAKER_HALF_OPEN_OK` | `3` | Consecutive probe successes needed to recover |
| `EGO_RATE_MAX_WAIT` | `1.0` | Max wait for a rate-limit token (s), then defer |
| `EGO_RATE_MISS_TTL` | `30` | Re-check interval for channels without a rate limit (s) |

## License

MIT
