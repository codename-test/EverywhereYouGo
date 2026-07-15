# EverywhereYouGo (EGo)

> 通用信息转发平台 — 数据 → 解析 → 路由 → 推送

接收任意 HTTP 请求，经解析器提取结构化字段，按条件路由到多个推送渠道。

## 快速开始

```bash
pip install -r requirements.txt
python3 main.py
```

打开 `http://localhost:5000` 即可配置。

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

## 路由条件

支持 `and`、`or`、括号分组的条件表达式：

| 示例 | 说明 |
|------|------|
| `event == 'library.new'` | 仅新入库 |
| `event == 'library.new' and media_type == 'Movie'` | 仅新入库的电影 |
| `event == 'library.new' or event == 'test'` | 新入库或测试消息 |
| `media_type in ('Movie', 'Series')` | 电影或剧集 |

## 备份恢复

系统设置中提供备份/恢复功能：

- **备份**：下载 ZIP 包，包含 `config/*.json` + `parsers/*.py`
- **恢复**：上传 ZIP 包，覆盖配置后自动生效

## 部署

测试环境通过共享目录部署：

```bash
# 本地同步到共享目录
rm -rf /app/out_file/ego_test
cp -r EverywhereYouGo /app/out_file/ego_test

# 路由器启动
cd /mnt/sata1-5/copaw/out_file/ego_test
WEB_PORT=50001 python3 main.py
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `WEB_PORT` | `5000` | WebUI 端口 |
| `DB_PATH` | `ego.db` | 数据库路径 |
| `LOG_LEVEL` | `INFO` | 日志等级 |

## 通道类型

| 通道 | 方式 | 类型标识 |
|------|------|---------|
| 企业微信 Bot | Webhook | `wechat_work_bot` |
| 企业微信 API | 应用消息 | `wechat_work_api` |
| 钉钉 | Webhook | `dingtalk` |
| 飞书 | Webhook | `feishu` |
| Telegram | Bot API | `telegram_bot` |
| Bark | API | `bark` |

## License

MIT
