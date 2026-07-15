# EverywhereYouGo (EGo)

> 通用信息转发平台 — 数据 → 解析 → 路由 → 推送

接收任意 HTTP 请求，经解析器提取结构化字段，按条件路由到多个推送渠道。

## 快速开始

```bash
pip install -r requirements.txt
python3 main.py
```

打开 `http://localhost:5000` 即可配置。

### Docker 部署

```bash
docker compose up -d
```

或手动构建：

```bash
docker build -t ego:1.0.0 .
docker run -d --name ego -p 5000:5000 -v ego_data:/app/data -v ego_config:/app/config ego:1.0.0
```

## 架构

```
HTTP POST → 数据源 → 解析器 → 路由匹配 → 模板渲染 → 推送渠道
```

| 组件 | 说明 |
|------|------|
| **数据源** | 监听端口接收 HTTP POST |
| **解析器** | Python 脚本，提取字段并定义变量名 |
| **路由** | 条件表达式匹配渠道-模板对 |
| **模板** | Simple / Jinja2 渲染标题和内容 |
| **渠道** | 企业微信、钉钉、飞书、Telegram、Bark |

## 认证

设置 `EGO_AUTH_TOKEN` 环境变量后开启访问控制，未设置则完全开放：

```bash
EGO_AUTH_TOKEN=your-secret-token python3 main.py
```

开启后：
- Web 页面需通过登录页输入 Token
- API 调用需携带 `Authorization: Bearer your-secret-token` 请求头
- 健康检查端点 `/api/health` 不需要认证

可选设置 `EGO_SECRET_KEY` 自定义 Flask session 密钥，不设置则自动生成随机值。

## 配置文件

配置持久化为 JSON 文件，位于项目 `config/` 目录：

| 文件 | 内容 |
|------|------|
| `config/parsers.json` | 解析器元信息 |
| `config/sources.json` | 数据源定义 |
| `config/channels.json` | 推送通道配置 |
| `config/templates.json` | 推送模板 |
| `config/bindings.json` | 渠道绑定（含条件表达式） |

可直接编辑 JSON 后重启生效，也可通过 WebUI 管理。SQLite（`ego.db`）仅存消息记录等运行时数据。

启动时从 JSON 加载到 SQLite，使用显式 ID 保证外键一致性。UI 编辑后自动同步回 JSON。

## 解析器

放在 `parsers/` 目录下的 `.py` 文件，定义一个 `parse()` 函数：

```python
def parse(raw_body: bytes, headers: dict, query_params: dict) -> dict:
    data = json.loads(raw_body)
    event = data.get("Event", "")
    name = data.get("Item", {}).get("Name", "")
    # ... 提取字段
    return {
        "title": name,
        "content": "- **event**: " + event + "\n- **name**: " + name,
        "event": event,
        "name": name,
        # 所有字段都会成为模板变量和路由条件
    }
```

返回的 dict 中除 `title`/`content` 外的字段同时用于：
- **路由条件匹配**：`event == 'library.new' and media_type == 'Movie'`
- **模板变量引用**：`{name}` / `{{ msg.name }}`

解析器支持在线编辑和热重载，保存后立即生效无需重启。

## 路由条件

支持 `and`、`or`、括号分组的条件表达式：

| 示例 | 说明 |
|------|------|
| `event == 'library.new'` | 仅新入库 |
| `event == 'library.new' and media_type == 'Movie'` | 仅新入库的电影 |
| `event == 'library.new' or event == 'test'` | 新入库或测试消息 |
| `media_type in ('Movie', 'Series')` | 电影或剧集 |

## 功能特性

### 免打扰（DND）

设置免打扰时段后，消息进入队列等待，时段结束后自动刷新发送。支持为特定渠道绑定设置 **紧急** 标记，紧急路由不受 DND 影响。

### 消息去重

渠道绑定可配置 `dedup_key_expr`（去重键表达式）和 `dedup_window`（去重窗口，默认 3600 秒）。同一去重键在窗口内不会重复发送。

### 并行推送

同一消息匹配多个渠道时，使用线程池并行发送，总延迟取决于最慢的单个渠道而非累加。

### 样本数据与在线调试

每个数据源自动保存最近 20 条请求样本，可在 WebUI 中选取样本进行测试解析和测试推送，无需重启或外部工具。

### 消息重发

失败消息支持两种重发模式：
- **原始重发**：使用已解析的 msg_json 直接重发
- **重新解析**：重新解析原始 raw_body 后重发

### 导入导出

- **备份**：下载 ZIP 包，包含 `config/*.json` + `parsers/*.py`
- **恢复**：上传 ZIP 包，覆盖配置后自动生效
- **JSON 导入**：支持 dry_run 预览、insert/overwrite 两种模式、依赖检查

## 通道类型

| 通道 | 方式 | 类型标识 |
|------|------|---------|
| 企业微信 Bot | Webhook | `wechat_work_bot` |
| 企业微信 API | 应用消息 | `wechat_work_api` |
| 钉钉 | Webhook | `dingtalk` |
| 飞书 | Webhook | `feishu` |
| Telegram | Bot API | `telegram_bot` |
| Bark | API | `bark` |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `WEB_PORT` | `5000` | WebUI 端口 |
| `DB_PATH` | `ego.db` | 数据库路径 |
| `LOG_LEVEL` | `INFO` | 日志等级 |
| `EGO_AUTH_TOKEN` | *(空)* | 访问控制 Token，设置后开启认证 |
| `EGO_SECRET_KEY` | *(自动生成)* | Flask session 密钥 |

## License

MIT
