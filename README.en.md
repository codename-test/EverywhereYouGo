
# EverywhereYouGo (EGo) v1.2.3

> Universal Message Forwarding Platform — Data → Parse → Route → Push

EGo receives arbitrary HTTP requests, extracts structured fields through parsers, matches routes by conditions, and pushes to multiple channels.

## Quick Start

```bash
pip install -r requirements.txt
python3 main.py
```

Open `https://localhost:5001` for the admin UI (self-signed cert; accept the browser warning). Webhook receivers and the health check run over plain HTTP on `http://localhost:5000`.

## Architecture

```
HTTP POST → Source → Parser → Route Match → Template Render → Push Channel
```

| Component | Description |
|-----------|-------------|
| **Source** | Listens on a port, receives HTTP POST |
| **Parser** | Python script, extracts fields and defines variable names |
| **Route** | Condition expression matching channel-template pairs |
| **Template** | Simple / Jinja2 rendering for title and content |
| **Channel** | WeChat Work, DingTalk, Feishu, Telegram, Bark |

## Config

Persistent configuration is stored as JSON files in `config/`:

| File | Content |
|------|---------|
| `config/parsers.json` | Parser metadata |
| `config/sources.json` | Source definitions |
| `config/channels.json` | Push channel configs |
| `config/templates.json` | Push templates |
| `config/bindings.json` | Channel bindings (with condition expressions) |

Edit JSON directly and restart, or manage via WebUI. System settings (DND, log level) and runtime data (message logs) are stored in SQLite (`ego.db`).

## Parser

Place `.py` files in `parsers/`, define a `parse()` function:

```python
def parse(raw_body: bytes, headers: dict, query_params: dict) -> dict:
    data = json.loads(raw_body)
    event = data.get("Event", "")
    name = data.get("Item", {}).get("Name", "")
    return {
        "title": name,
        "content": "- **event**: " + event + "\n- **name**: " + name,
        "event": event,
        "name": name,
    }
```

Fields other than `title`/`content` are used for:
- **Route condition matching**: `event == 'library.new' and media_type == 'Movie'`
- **Template variable reference**: `{name}` / `{{ msg.name }}`

## Route Conditions

Supports `and`, `or`, and parenthesized expressions:

| Example | Description |
|---------|-------------|
| `event == 'library.new'` | New items only |
| `event == 'library.new' and media_type == 'Movie'` | New movies only |
| `event == 'library.new' or event == 'test'` | New items or test messages |
| `media_type in ('Movie', 'Series')` | Movies or series |

## Deploy

### Docker

**One-click deployment (recommended):**

```bash
curl -O https://raw.githubusercontent.com/codename-test/EverywhereYouGo/main/deploy/init.sh
chmod +x init.sh
./init.sh
# Follow prompts to select deployment mode
```

Supports 5 deployment modes: default (quick start), t1-host (host network), t2-bridge (bridge network), t3-nginx (Nginx + manual cert), t4-certbot (Nginx + Let's Encrypt auto cert).

> More deployment options are in the `deploy/` directory with `README.en.md` (English) and `README.md` (Chinese).

After startup: admin UI at `https://<host-IP>:5001` (self-signed cert, accept the browser warning); webhook receivers and health check at `http://<host-IP>:5000`.

### Env Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WEB_PORT` | `5000` | HTTP port (webhook receivers / health check) |
| `WEB_SSL_PORT` | `5001` | HTTPS port (admin UI; disabled if no cert) |
| `EGO_SSL_ENABLED` | `1` | Set to `0` to fully disable built-in HTTPS (HTTP only, no redirect, no cert generation) |
| `EGO_SSL_DIR` | `./certs` | SSL certificate directory for `ego.crt` and `ego.key` |
| `EGO_SSL_CERT` | `./certs/ego.crt` | Certificate file path (overrides `EGO_SSL_DIR`) |
| `EGO_SSL_KEY` | `./certs/ego.key` | Private key file path (overrides `EGO_SSL_DIR`) |
| `DB_PATH` | `ego.db` | Database path |
| `LOG_LEVEL` | `INFO` | Log level |
| `EGO_AUTH_TOKEN` | `""` | Bearer token for API auth (empty = no auth) |

## Channel Types

| Channel | Method | Type Identifier |
|---------|--------|-----------------|
| WeChat Work Bot | Webhook | `wechat_work_bot` |
| WeChat Work API | App Message | `wechat_work_api` |
| DingTalk | Webhook | `dingtalk` |
| Feishu | Webhook | `feishu` |
| Telegram | Bot API | `telegram_bot` |
| Bark | API | `bark` |

## Backup & Restore

Available in System Settings:

- **Backup**: Download ZIP containing `config/*.json` + `parsers/*.py`
- **Restore**: Upload ZIP, config is automatically reloaded

## License

MIT
