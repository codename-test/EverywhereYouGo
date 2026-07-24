# Channel Plugin Development Guide

> 📌 Quick jump: [AI Prompt](#ai-prompt)

## Overview

A channel is EGo's push outlet. Each channel is a Python plugin that delivers a rendered message to a specific platform (Telegram, Bark, WeCom, DingTalk, etc.).

EGo ships with common built-in channels. When your target platform isn't supported, you can write a custom channel plugin without touching core code.

## Quick Start

Create `channels/my_platform.py`:

```python
class Channel(BaseChannel):
    CHANNEL_TYPE = "my_platform"
    CHANNEL_NAME = "My Platform"

    CONFIG_FIELDS = []

    def __init__(self, config: dict):
        super().__init__(config)

    def send(self, title: str, content: str) -> tuple:
        return True, ""

    def test(self) -> bool:
        return True
```

Place it in the `channels/` directory, then you can select this type when creating a channel.

## Interface Contract

### Class Structure

A plugin must define a `Channel` class inheriting from `BaseChannel`. `BaseChannel` is injected by the loader — do **NOT** import it.

```python
class Channel(BaseChannel):
    CHANNEL_TYPE = "my_platform"    # Unique type ID, used for routing
    CHANNEL_NAME = "My Platform"    # Display name in the UI
    CONFIG_FIELDS = [...]           # Config form definition
```

### Required Methods

| Method | Description | Returns |
|--------|-------------|---------|
| `__init__(self, config)` | Receives the config dict; must call `super().__init__(config)` first | - |
| `send(self, title, content)` | Sends the message | `(True, "")` on success; `(False, "error")` on failure |
| `test(self)` | Validates the config (triggered by the "Test" button) | `bool` |

`send()` receives the **final rendered text** — just deliver it; no variable processing needed.

### CONFIG_FIELDS Form

`CONFIG_FIELDS` defines the channel's config form. Each entry is a dict:

```python
CONFIG_FIELDS = [
    {
        "name": "api_key",           # Config key (read from config)
        "type": "password",          # text | password | textarea
        "label": "API Key",          # English label
        "label_zh": "API 密钥",      # Chinese label
        "desc": "Your API key",      # English help text
        "desc_zh": "你的 API 密钥",  # Chinese help text
        "placeholder": "sk-...",     # Input placeholder
        "required": True,            # Whether required
        "default": ""                # Default value
    }
]
```

Field `type` values:

| Type | Usage |
|------|-------|
| `text` | Plain single-line text |
| `password` | Secrets (masked display) |
| `textarea` | Multi-line long text |

Read user-entered values in `__init__` via `config.get("name")`.

## Available Modules

- `requests` — for HTTP calls
- `log` — use `log.logger.info()` / `log.logger.error()` for logging
- Python standard library

## Complete Example

A complete channel that pushes via an HTTP API:

```python
import requests
import log

class Channel(BaseChannel):
    CHANNEL_TYPE = "my_platform"
    CHANNEL_NAME = "My Platform"

    CONFIG_FIELDS = [
        {"name": "api_key", "type": "password", "label": "API Key",
         "label_zh": "API 密钥", "required": True, "default": ""},
        {"name": "endpoint", "type": "text", "label": "Endpoint",
         "label_zh": "接口地址", "default": "https://api.example.com/send"},
    ]

    def __init__(self, config: dict):
        super().__init__(config)
        self.api_key = config.get("api_key", "")
        self.endpoint = config.get("endpoint", "")

    def send(self, title: str, content: str) -> tuple:
        try:
            resp = requests.post(self.endpoint, json={
                "key": self.api_key,
                "title": title,
                "body": content,
            }, timeout=15)
            if resp.status_code == 200:
                return True, ""
            log.logger.error(f"my_platform push failed: {resp.status_code} {resp.text[:200]}")
            return False, resp.text
        except Exception as e:
            log.logger.error(f"my_platform error: {e}")
            return False, str(e)

    def test(self) -> bool:
        ok, _ = self.send("EGo Test", "Channel test successful!")
        return ok
```

Key points:

- Always set `timeout=15` on HTTP requests so you don't block the sender thread
- Catch all exceptions in `send()`; return `(False, str(e))` on failure
- Log failure details with `log.logger` for troubleshooting in System Logs

## Debugging & Testing

1. After creating the channel and filling in its config, click "Test" — EGo calls `test()` to send a test message
2. On failure, the error is shown in the UI and written to System Logs

## Best Practices

- Keep the plugin self-contained; no third-party deps beyond `requests`
- Use `password` type for secrets (tokens, keys)
- Have `test()` reuse `send()` to deliver a real test message
- Return human-readable platform errors to make diagnosis easier

## FAQ

**Q: Where does `BaseChannel` come from?**
A: It's injected by the channel loader at runtime. Just inherit from it — do **NOT** write `from xxx import BaseChannel`.

**Q: Are `send()`'s title and content the raw data?**
A: No. They are the final text after parser extraction, route matching, and template rendering.

**Q: Do I need to restart after editing a plugin?**
A: No. Save the file; it's reloaded on the next send.
