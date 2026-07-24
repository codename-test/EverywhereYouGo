# Parser Development Guide

> 📌 Quick jump: [AI Prompt](#ai-prompt)

## Overview

A parser is the first stage of EGo's data processing pipeline. When a data source receives an HTTP request, EGo invokes the parser bound to that source to extract structured fields from the raw request body. These fields are then used for route matching and template rendering.

The full processing chain:

```
HTTP request → parser parse() → structured dict → route condition matching → template rendering → channel push
```

The keys of the dict returned by the parser (e.g. `title`, `content`) become template variables and also serve as the evaluation context for route condition expressions.

## Quick Start

A minimal parser needs just one file and one function. Create `parsers/my_parser.py`:

```python
import json

def parse(raw_body: bytes, headers: dict, query_params: dict) -> dict:
    data = json.loads(raw_body)
    return {
        "title": data.get("title", "Notification"),
        "content": data.get("body", ""),
    }
```

Place the file in the `parsers/` directory, refresh the Parsers page to see it, then bind it to a data source.

## Interface Contract

### Function Signature

A parser must define a module-level `parse` function with a fixed signature:

```python
def parse(raw_body: bytes, headers: dict, query_params: dict) -> dict:
    ...
```

Parameters:

| Param | Type | Description |
|-------|------|-------------|
| `raw_body` | `bytes` | Raw HTTP request body; you decode/parse it |
| `headers` | `dict` | Request headers, keys lowercased |
| `query_params` | `dict` | URL query parameters |

### Return Value

Return a `dict` whose key-value pairs become the available fields:

- Keys act as **template variables** (e.g. `{title}`, `{{ title }}`)
- Keys can be used in **route conditions** (e.g. `event == 'library.new'`)
- You should return at least `title` and `content`
- Values should be strings (or stringifiable); provide defaults for missing fields

## Available Modules

Parsers run in a restricted sandbox with access to:

- Standard library: `json`, `xml.etree.ElementTree`, `re`, `html`, `urllib.parse`, `datetime`, etc.
- The built-in `log` module: use `log.logger.info()` / `log.logger.error()` for logging

Arbitrary third-party imports are not allowed, nor is filesystem access.

## Complete Example

Parsing an Emby media server webhook — handling nested JSON, extracting meaningful fields, and error handling:

```python
import json
import log

def parse(raw_body: bytes, headers: dict, query_params: dict) -> dict:
    try:
        data = json.loads(raw_body)
    except Exception as e:
        log.logger.error(f"JSON parse failed: {e}, raw: {raw_body[:500]}")
        return {"title": "Parse failed", "content": str(e)}

    # Emby webhook event type lives in NotificationType
    event = data.get("NotificationType", "unknown")
    item = data.get("Item", {}) or {}

    return {
        "event": event,                              # used by route conditions
        "title": item.get("Name", "Media notification"),
        "content": data.get("NotificationText", ""),
        "author": item.get("AlbumArtist", ""),
        "url": data.get("PlaybackUrl", ""),
        "item_type": item.get("Type", ""),           # e.g. Movie / Episode
    }
```

Key points:

- Wrap parsing in `try/except`; on failure return a dict with error info instead of raising
- Use `.get(key, default)` at every level to avoid `KeyError`
- Emit extra fields like `event` and `item_type` so route conditions can split traffic precisely

## Debugging & Testing

1. When a source receives a message, the raw request is recorded as **sample data**
2. In the source config dialog, select a sample under "Sample Data"
3. Click "Test Parse" to see the parser's output for that sample — no real push happens

Parser code changes take effect without a restart (reloaded on the next message).

## Best Practices

- Support both JSON and form-encoded bodies where possible
- On error, log the (truncated) raw body for troubleshooting
- Use lowercase underscore naming with clear semantics
- Avoid network calls or slow operations in a parser; keep it millisecond-fast

## FAQ

**Q: What happens if the parser raises an exception?**
A: The message is marked `FAILED` and logged, and nothing is pushed. Always catch exceptions internally.

**Q: Any restrictions on returned dict key names?**
A: Keys become template variable names — use lowercase underscores. Avoid a leading `_` (reserved for system fields like `_trace_id`).

**Q: How do I distinguish different event types from the same source?**
A: Emit an event-type field (e.g. `event`) and use it in the channel binding's condition expression, e.g. `event == 'library.new'`.
