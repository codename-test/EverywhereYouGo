[简体中文](README.md) | English

[Quick Start](#quick-start) · [Architecture](#architecture) · [Config](#config) · [Deploy](#deploy) · [API](#api)

# EverywhereYouGo (EGo)

> Universal Message Forwarding Platform — Data → Parse → Route → Push

EGo receives arbitrary HTTP requests, extracts structured fields through parsers, matches routes by conditions, and pushes to multiple channels.

## Quick Start

```bash
pip install -r requirements.txt
python3 main.py
```

Open `http://localhost:5000` to configure.

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
| `config/settings.json` | System settings (DND, log level) |

Edit JSON directly and restart, or manage via WebUI. SQLite (`ego.db`) only stores runtime message logs.

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

```bash
pip install -r requirements.txt
WEB_PORT=5000 python3 main.py
```

### Docker

```bash
docker run -d --name ego -p 5000:5000 -v ./ego_data:/app/data ghcr.io/codename-test/EverywhereYouGo/ego:latest
```

## Env Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WEB_PORT` | `5000` | WebUI port |
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
