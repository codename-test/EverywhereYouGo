# 解析器开发指南

> 📌 快速跳转：[AI 提示词](#ai-prompt)

## 概述

解析器（Parser）是 EGo 数据处理流水线的第一环。当数据源收到 HTTP 请求后，EGo 会调用该数据源绑定的解析器，把原始请求体提取成结构化字段，供后续的路由匹配与模板渲染使用。

完整处理链路：

```
HTTP 请求 → 解析器 parse() → 结构化字典 → 路由条件匹配 → 模板渲染 → 通道推送
```

解析器输出的字典键（如 `title`、`content`）会直接成为模板变量，也会作为路由条件表达式的求值上下文。

## 快速开始

一个最简解析器只需要一个文件、一个函数。创建 `parsers/my_parser.py`：

```python
import json

def parse(raw_body: bytes, headers: dict, query_params: dict) -> dict:
    data = json.loads(raw_body)
    return {
        "title": data.get("title", "新通知"),
        "content": data.get("body", ""),
    }
```

把文件放入 `parsers/` 目录，刷新解析器页面即可看到它，然后在数据源配置中绑定即可生效。

## 接口约定

### 函数签名

解析器必须定义模块级 `parse` 函数，签名固定：

```python
def parse(raw_body: bytes, headers: dict, query_params: dict) -> dict:
    ...
```

参数说明：

| 参数 | 类型 | 说明 |
|------|------|------|
| `raw_body` | `bytes` | 原始 HTTP 请求体，需自行解码/解析 |
| `headers` | `dict` | 请求头，键已统一转为小写 |
| `query_params` | `dict` | URL 查询参数 |

### 返回值约定

返回一个 `dict`，其键值对即为后续可用的字段：

- 键会作为**模板变量**（如 `{title}`、`{{ title }}`）
- 键也可用于**路由条件**（如 `event == 'library.new'`）
- 建议至少返回 `title` 和 `content` 两个键
- 值应为字符串或可转为字符串的类型；缺失字段请给默认值

## 可用模块

解析器运行在受限沙箱中，可使用：

- 标准库：`json`、`xml.etree.ElementTree`、`re`、`html`、`urllib.parse`、`datetime` 等
- 内置 `log` 模块：用 `log.logger.info()` / `log.logger.error()` 记录日志

不允许 `import` 任意第三方库，也不应读写文件系统。

## 完整示例

以解析 Emby 媒体服务器 webhook 为例，展示如何处理嵌套 JSON、提取有意义字段并容错：

```python
import json
import log

def parse(raw_body: bytes, headers: dict, query_params: dict) -> dict:
    try:
        data = json.loads(raw_body)
    except Exception as e:
        log.logger.error(f"JSON 解析失败: {e}, 原始内容: {raw_body[:500]}")
        return {"title": "解析失败", "content": str(e)}

    # Emby webhook 的事件类型在 NotificationType 字段
    event = data.get("NotificationType", "unknown")
    item = data.get("Item", {}) or {}

    return {
        "event": event,                              # 供路由条件使用
        "title": item.get("Name", "媒体通知"),
        "content": data.get("NotificationText", ""),
        "author": item.get("AlbumArtist", ""),
        "url": data.get("PlaybackUrl", ""),
        "item_type": item.get("Type", ""),           # 如 Movie / Episode
    }
```

要点：

- 用 `try/except` 包住解析，失败时返回带错误信息的字典而不是抛异常
- 用 `.get(key, 默认值)` 逐层容错，避免 `KeyError`
- 额外输出 `event`、`item_type` 等字段，方便在路由条件里精确分流

## 调试与测试

1. 数据源收到消息后，原始请求会被记录为**样本数据**
2. 在数据源配置弹窗的「样本数据」区勾选一条样本
3. 点击「测试解析」即可看到解析器对该样本的输出，无需真实推送

修改解析器代码后无需重启，保存文件即生效（下次消息到来时重新加载）。

## 最佳实践

- 优先同时兼容 JSON 与表单编码两种请求体
- 出错时把原始请求体（截断后）写入日志，便于排查
- 字段命名用小写下划线风格，语义清晰
- 不要在解析器里做网络请求或耗时操作，保持毫秒级返回

## 常见问题

**Q：解析器抛异常会怎样？**
A：该条消息会被标记为 `FAILED` 并进入日志，不会推送。请始终在内部捕获异常。

**Q：返回的字典键名有什么限制？**
A：键名即模板变量名，建议使用小写下划线；避免以 `_` 开头（系统保留字段如 `_trace_id` 使用此前缀）。

**Q：如何区分同一数据源的不同事件类型？**
A：在返回值中输出一个事件类型字段（如 `event`），然后在通道绑定的条件表达式中使用它，例如 `event == 'library.new'`。
