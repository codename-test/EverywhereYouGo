## EGo Template Specification

You are writing message templates for the EGo (EverywhereYouGo) push notification platform.
Templates format parsed data into readable messages for different channels.

### Template Engines
EGo supports two engines:
1. **Jinja2** — Full template engine with conditions, loops, filters
2. **Simple** — Basic variable substitution only: `{var_name}`

### Available Variables
Variables come from the parser output. Common ones:
- `{title}` — Message title
- `{content}` — Message body
- `{url}` — Source URL
- `{author}` — Content author
- `{timestamp}` — Event time

### Jinja2 Example
```jinja2
**{{ title }}**

{{ content }}

{% if url %}
[View source]({{ url }})
{% endif %}

{% if author %}
By: {{ author }}
{% endif %}
```

### Simple Example
```
**{title}**

{content}

{url}
```

### Best Practices
1. Use Jinja2 for conditional content (some fields may be empty)
2. Keep templates clean — avoid complex logic
3. Test with sample data to verify output
4. Different channels may render markdown differently
