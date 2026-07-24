# Push Template Guide

> 📌 Quick jump: [AI Prompt](#ai-prompt)

## Overview

A template renders the structured fields produced by the parser into text suitable for a specific channel. Each channel binding can specify its own template, so the same data can be formatted differently per platform.

When rendering happens:

```
parser output dict → route matching → template rendering (this doc) → channel send()
```

## Template Engines

EGo supports two engines, chosen when creating a template:

| Engine | Capability | Use case |
|--------|-----------|----------|
| **Simple** | Variable substitution only `{var}` | Simple fixed formats |
| **Jinja2** | Variables, conditionals, loops, filters | Null-checks, branching, complex layout |

## Available Variables

Variables come from the keys of the parser's returned dict. Common ones:

| Variable | Meaning |
|----------|---------|
| `title` | Message title |
| `content` | Message body |
| `url` | Source link |
| `author` | Content author |
| `timestamp` | Event time |

There is also a system field `_trace_id` (message trace ID). The exact variables depend on what your bound parser outputs — see the "Available Variables" panel in the source config.

## Simple Engine

Use `{var_name}` for direct substitution:

```
**{title}**

{content}

{url}
```

The Simple engine has no conditionals; empty variables render as empty strings.

## Jinja2 Engine

### Variables

```jinja2
{{ title }}
```

### Conditionals

Wrap optional fields in conditions to avoid blank lines:

```jinja2
**{{ title }}**

{{ content }}

{% if url %}
View details: {{ url }}
{% endif %}

{% if author %}
By: {{ author }}
{% endif %}
```

### Filters

```jinja2
{{ content | truncate(200) }}     {# truncate to 200 chars #}
{{ title | upper }}               {# uppercase #}
```

### Loops

Iterate when the parser outputs a list field:

```jinja2
{% for tag in tags %}
#{{ tag }}
{% endfor %}
```

## Complete Example

A media notification template with null-checks (Jinja2):

```jinja2
🎬 **{{ title }}**

{{ content }}

{% if item_type %}Type: {{ item_type }}{% endif %}
{% if author %}Artist: {{ author }}{% endif %}
{% if url %}
▶ Watch now: {{ url }}
{% endif %}
```

## Best Practices

- Use Jinja2 `{% if %}` for fields that may be missing, to avoid blank lines/links
- Keep templates simple; push complex logic into the parser
- Use the source's "Test Parse / Test Push" with real samples to verify output
- Channels differ in Markdown support (e.g. Bark lacks rich Markdown) — adapt to the target platform

## FAQ

**Q: What happens with a wrong variable name?**
A: The Simple engine leaves `{xxx}` as-is; Jinja2 renders it as an empty string. Check names against the parser output.

**Q: How do I know which variables are available?**
A: In the source config dialog, select the parser and click the "Vars" button to list all keys it outputs.

**Q: Can I write Python code in a template?**
A: No. The Jinja2 sandbox only allows template syntax and built-in filters — no arbitrary code execution.
