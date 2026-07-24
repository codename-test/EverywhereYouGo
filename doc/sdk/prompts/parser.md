## EGo Parser Plugin Specification

You are writing a parser script for the EGo (EverywhereYouGo) push notification platform.
The parser extracts structured data from raw HTTP request bodies.

### File Requirements
- Filename: lowercase + underscores, ending with `.py`
- Location: `parsers/` directory
- Must define a `parse(raw_body: bytes, headers: dict, query_params: dict) -> dict` function

### Function Signature
```python
def parse(raw_body: bytes, headers: dict, query_params: dict) -> dict:
    """
    Parse incoming webhook data and return a dict.
    The returned dict keys will be used as template variables.
    Common keys: title, content, url, author, timestamp
    """
    import json
    data = json.loads(raw_body)
    return {
        "title": data.get("title", "Notification"),
        "content": data.get("body", data.get("text", "")),
        "url": data.get("url", ""),
    }
```

### Available Modules
- `json` — for JSON parsing
- `xml.etree.ElementTree` — for XML parsing
- `re` — for regex
- `html` — for HTML entity decoding
- `urllib.parse` — for URL parsing
- `log` — use `log.logger.info()` / `log.logger.error()` for logging

### Best Practices
1. Always handle both JSON and form-encoded bodies when possible
2. Return at least `title` and `content` keys
3. Handle missing fields gracefully with defaults
4. Use `try/except` to catch parsing errors
5. Log raw body on error for debugging
