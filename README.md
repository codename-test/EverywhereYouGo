简体中文 | [English](README.en.md)

[开始使用](#快速开始) · [架构](#架构) · [配置](#配置文件) · [部署](#部署)

# EverywhereYouGo (EGo) v1.1.0

> 通用信息转发平台 — 数据 → 解析 → 路由 → 推送

接收任意 HTTP 请求，经解析器提取结构化字段，按条件路由到多个推送渠道。

Receives any HTTP request, extracts structured fields via parsers, and routes to multiple push channels based on conditions.

## 快速开始 / Quick Start

```bash
pip install -r requirements.txt
python3 main.py
```

打开 `http://localhost:5000` 即可配置。

Open `http://localhost:5000` to configure.

### Docker 部署 / Docker Deployment

```bash
docker compose up -d
```

或手动构建 / Or build manually:

```bash
docker build -t ego:1.0.0 .
docker run -d --name ego -p 5000:5000 -v ego_data:/app/data -v ego_config:/app/config ego:1.0.0
```

## 架构 / Architecture

```
HTTP POST → 数据源/Source → 解析器/Parser → 路由匹配/Route → 模板渲染/Render → 推送渠道/Channel
```

| 组件 / Component | 说明 / Description |
|------|------|
| **数据源 / Source** | 监听端口接收 HTTP POST / Listen on port for HTTP POST |
| **解析器 / Parser** | Python 脚本，提取字段并定义变量名 / Python script, extract fields and define variable names |
| **路由 / Route** | 条件表达式匹配渠道-模板对 / Condition expression matching channel-template pairs |
| **模板 / Template** | Simple / Jinja2 渲染标题和内容 / Simple / Jinja2 rendering for title and content |
| **渠道 / Channel** | 企业微信、钉钉、飞书、Telegram、Bark / WeCom, DingTalk, Feishu, Telegram, Bark |

## 认证 / Authentication

设置 `EGO_AUTH_TOKEN` 环境变量后开启访问控制，未设置则完全开放：

Set the `EGO_AUTH_TOKEN` environment variable to enable access control. Leave unset for open access:

```bash
EGO_AUTH_TOKEN=your-secret-token python3 main.py
```

开启后 / When enabled:
- Web 页面需通过登录页输入 Token / Web pages require Token via login page
- API 调用需携带 `Authorization: Bearer your-secret-token` 请求头 / API calls require `Authorization: Bearer your-secret-token` header
- 健康检查端点 `/api/health` 不需要认证 / Health check endpoint `/api/health` requires no authentication

可选设置 `EGO_SECRET_KEY` 自定义 Flask session 密钥，不设置则自动生成随机值。

Optionally set `EGO_SECRET_KEY` to customize the Flask session key. Auto-generated random value if unset.

## 配置文件 / Configuration

配置持久化为 JSON 文件，位于项目 `config/` 目录：

Config is persisted as JSON files in the `config/` directory:

| 文件 / File | 内容 / Content |
|------|------|
| `config/parsers.json` | 解析器元信息 / Parser metadata |
| `config/sources.json` | 数据源定义 / Source definitions |
| `config/channels.json` | 推送通道配置 / Push channel config |
| `config/templates.json` | 推送模板 / Push templates |
| `config/bindings.json` | 渠道绑定（含条件表达式）/ Channel bindings (with conditions) |

可直接编辑 JSON 后重启生效，也可通过 WebUI 管理。SQLite（`ego.db`）仅存消息记录等运行时数据。

You can edit JSON directly and restart, or manage via WebUI. SQLite (`ego.db`) only stores runtime data like message logs.

启动时从 JSON 加载到 SQLite，使用显式 ID 保证外键一致性。UI 编辑后自动同步回 JSON。

Loaded from JSON to SQLite at startup with explicit IDs for foreign key consistency. UI edits auto-sync back to JSON.

## 解析器 / Parsers

放在 `parsers/` 目录下的 `.py` 文件，定义一个 `parse()` 函数：

`.py` files in the `parsers/` directory define a `parse()` function:

```python
def parse(raw_body: bytes, headers: dict, query_params: dict) -> dict:
    data = json.loads(raw_body)
    event = data.get("Event", "")
    name = data.get("Item", {}).get("Name", "")
    # ... 提取字段 / extract fields
    return {
        "title": name,
        "event": event,
        "name": name,
        # 所有字段都会成为模板变量和路由条件
        # All fields become template variables and route conditions
    }
```

返回的 dict 中除 `title` 外的字段同时用于：

Fields in the returned dict (except `title`) are used for:
- **路由条件匹配 / Route condition matching**: `event == 'library.new' and media_type == 'Movie'`
- **模板变量引用 / Template variable reference**: `{name}` / `{{ msg.name }}`

解析器支持在线编辑和热重载，保存后立即生效无需重启。

Parsers support online editing and hot-reload. Changes take effect immediately after saving.

## 路由条件 / Route Conditions

支持 `and`、`or`、括号分组的条件表达式：

Supports `and`, `or`, and parenthesized condition expressions:

| 示例 / Example | 说明 / Description |
|------|------|
| `event == 'library.new'` | 仅新入库 / Only new additions |
| `event == 'library.new' and media_type == 'Movie'` | 仅新入库的电影 / Only new movies |
| `event == 'library.new' or event == 'test'` | 新入库或测试消息 / New additions or test messages |

## 功能特性 / Features

### 免打扰（DND）/ Do Not Disturb

设置免打扰时段后，消息进入队列等待，时段结束后自动刷新发送。支持为特定渠道绑定设置 **紧急** 标记，紧急路由不受 DND 影响。

When DND is active, messages are queued and auto-flushed when the period ends. Bindings marked as **Urgent** bypass DND.

### 消息去重 / Message Deduplication

渠道绑定可配置 `dedup_key_expr`（去重键表达式）和 `dedup_window`（去重窗口，默认 3600 秒）。同一去重键在窗口内不会重复发送。

Channel bindings can configure `dedup_key_expr` (dedup key expression) and `dedup_window` (window in seconds, default 3600). Same dedup key won't be sent twice within the window.

### 并行推送 / Parallel Push

同一消息匹配多个渠道时，使用线程池并行发送，总延迟取决于最慢的单个渠道而非累加。

When a message matches multiple channels, thread-pool parallel delivery ensures total latency equals the slowest channel, not the sum.

### 样本数据与在线调试 / Sample Data & Online Debug

每个数据源自动保存最近 20 条请求样本，可在 WebUI 中选取样本进行测试解析和测试推送，无需重启或外部工具。

Each source auto-saves the last 20 request samples. Test parsing and pushing in the WebUI without restarting or external tools.

### 消息重发 / Message Retry

失败消息支持两种重发模式：

Failed messages support two retry modes:
- **原始重发 / Original Retry**: 使用已解析的 msg_json 直接重发 / Re-send using the stored msg_json
- **重新解析 / Re-parse**: 重新解析原始 raw_body 后重发 / Re-parse the original raw_body then send

### 导入导出 / Import & Export

- **备份 / Backup**: 下载 ZIP 包，包含 `config/*.json` + `parsers/*.py` / Download ZIP with configs + parsers
- **恢复 / Restore**: 上传 ZIP 包，覆盖配置后自动生效 / Upload ZIP to overwrite configs
- **JSON 导入 / JSON Import**: 支持 dry_run 预览、insert/overwrite 两种模式、依赖检查 / Supports dry_run preview, insert/overwrite modes, dependency checks

## 国际化 / Internationalization

EGo 内置中英文双语支持，通过导航栏右上角的语言切换按钮随时切换。

EGo includes built-in Chinese/English bilingual support. Switch anytime via the language toggle in the top-right corner of the navigation bar.

- **后端 / Backend**: `i18n.py` 模块提供 `_()` 翻译函数 / `i18n.py` module provides `_()` translation function
- **模板 / Templates**: Jinja2 使用 `{{ _("key") }}`，JavaScript 使用 `t("key")` / Jinja2 uses `{{ _("key") }}`, JavaScript uses `t("key")`
- **API**: `/api/lang` 端点支持 GET（获取当前语言）和 POST（切换语言）/ `/api/lang` endpoint supports GET (current lang) and POST (switch lang)

## 通道类型 / Channel Types

| 通道 / Channel | 方式 / Method | 类型标识 / Type ID |
|------|------|---------|
| 企业微信 Bot / WeCom Bot | Webhook | `wechat_work_bot` |
| 企业微信 API / WeCom API | 应用消息 / App Message | `wechat_work_api` |
| 钉钉 / DingTalk | Webhook | `dingtalk` |
| 飞书 / Feishu | Webhook | `feishu` |
| Telegram | Bot API | `telegram_bot` |
| Bark | API | `bark` |

## 环境变量 / Environment Variables

| 变量 / Variable | 默认值 / Default | 说明 / Description |
|------|--------|------|
| `WEB_PORT` | `5000` | WebUI 端口 / WebUI port |
| `DB_PATH` | `ego.db` | 数据库路径 / Database path |
| `LOG_LEVEL` | `INFO` | 日志等级 / Log level |
| `EGO_AUTH_TOKEN` | *(空/empty)* | 访问控制 Token / Access control Token |
| `EGO_SECRET_KEY` | *(自动/auto)* | Flask session 密钥 / Flask session key |

## License

MIT
