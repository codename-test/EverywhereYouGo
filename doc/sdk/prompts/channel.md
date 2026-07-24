## EGo Channel Plugin Specification

You are writing a channel plugin for the EGo (EverywhereYouGo) push notification platform.
The plugin is a Python script that sends messages to a specific platform.

### File Requirements
- Filename: lowercase + underscores, ending with `.py`
- Location: `channels/` directory
- Must define a `Channel` class that inherits from `BaseChannel`
- `BaseChannel` is injected by the loader — do NOT import it

### Class Structure
```python
class Channel(BaseChannel):
    CHANNEL_TYPE = "my_platform"    # Unique ID for routing
    CHANNEL_NAME = "My Platform"    # Display name in UI

    CONFIG_FIELDS = [
        {
            "name": "api_key",           # Config key name
            "type": "password",          # text | password | textarea
            "label": "API Key",          # English label
            "label_zh": "API 密钥",       # Chinese label
            "desc": "Your API key",      # English help text
            "desc_zh": "你的 API 密钥",  # Chinese help text
            "placeholder": "sk-...",     # Input placeholder
            "required": True,            # Whether field is required
            "default": ""                # Default value
        }
    ]

    def __init__(self, config: dict):
        super().__init__(config)
        # Read config values
        self.api_key = config.get("api_key", "")

    def send(self, title: str, content: str) -> tuple:
        # Send message. Return (True, "") on success, (False, "error") on failure.
        try:
            import requests
            resp = requests.post("https://api.example.com/send", json={
                "key": self.api_key,
                "title": title,
                "body": content
            }, timeout=15)
            if resp.status_code == 200:
                return True, ""
            return False, resp.text
        except Exception as e:
            return False, str(e)

    def test(self) -> bool:
        # Test if config is valid. Return True/False.
        ok, _ = self.send("EGo Test", "Channel test successful!")
        return ok
```

### Available Modules
- `requests` — for HTTP calls
- `log` — use `log.logger.info()` / `log.logger.error()` for logging
- Any Python standard library module

### Best Practices
1. Always set `timeout=15` on HTTP requests
2. Catch all exceptions in `send()` and return `(False, str(e))`
3. Use `log.logger` for error logging
4. `CONFIG_FIELDS` defines the UI form — use `password` type for secrets
5. Keep the plugin self-contained, no external dependencies beyond `requests`
