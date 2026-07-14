# EverywhereYouGo (EGo)

> 通用信息转发平台 — 任意 HTTP 数据 → 解析器 → 路由 → 多渠道推送

EGo 是一个轻量级通用信息转发平台，接收外部 HTTP 请求，经过解析、路由匹配、模板渲染后，推送到企业微信/钉钉/飞书/Telegram/Bark 等多个渠道。

## 适用场景

- Emby / Jellyfin 媒体库更新通知
- 系统告警、服务监控回调
- Webhook 统一中转与格式化
- 任意 JSON/表单数据的多渠道分发

## 架构

```
HTTP 请求 → 数据源 (Source) → 解析器 (Parser) → 路由 (Router) → 模板 (Template) → 渠道 (Channel)
```

| 组件 | 说明 |
|------|------|
| **数据源 (Source)** | 监听端口，接收 HTTP POST 请求 |
| **解析器 (Parser)** | Python 脚本，解析为结构化消息 |
| **路由 (Router)** | 条件表达式匹配渠道-模板对 |
| **模板 (Template)** | Jinja2 渲染标题和内容 |
| **渠道 (Channel)** | 推送通道 |

## 快速开始

```bash
pip install -r requirements.txt
python3 main.py
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `WEB_PORT` | `5000` | WebUI 端口 |
| `DB_PATH` | `ego.db` | 数据库路径 |
| `LOG_LEVEL` | `INFO` | 日志等级 |

## 通道支持

| 通道 | 类型 |
|------|------|
| 企业微信 Bot | Webhook |
| 企业微信 API | 应用消息 |
| 钉钉 | Webhook |
| 飞书 | Webhook |
| Telegram | Bot API |
| Bark | API |

## License

MIT
