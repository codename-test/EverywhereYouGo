# EverywhereYouGo (EGo) v1.2.3

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

Supports 5 deployment modes: default (quick start), t1-host (host network), t2-bridge (bridge network), t3-nginx (Nginx + manual certificate), t4-certbot (Nginx + Let's Encrypt auto certificate).

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

Configuration is persisted as JSON files in `config/` directory:

| File | Content |
|------|------|
| `config/parsers.json` | Parser metadata |
| `config/sources.json` | Data source definitions |
| `config/channels.json` | Push channel configurations |
| `config/templates.json` | Push templates |
| `config/bindings.json` | Channel bindings (with condition expressions) |

Can directly edit JSON and restart to take effect, or manage via WebUI. System settings (DND, log level, etc.) and runtime data (message logs) are stored in SQLite (`ego.db`).

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

## License

MIT
