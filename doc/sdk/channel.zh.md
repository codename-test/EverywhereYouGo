# 通道插件开发指南

> 📌 快速跳转：[AI 提示词](#ai-prompt)

## 概述

通道（Channel）是 EGo 的推送出口。每个通道是一个 Python 插件，负责把渲染好的消息发送到具体平台（如 Telegram、Bark、企业微信、钉钉等）。

EGo 内置了常见通道；当目标平台没有内置支持时，你可以编写自定义通道插件，无需改动核心代码。

## 快速开始

创建 `channels/my_platform.py`：

```python
class Channel(BaseChannel):
    CHANNEL_TYPE = "my_platform"
    CHANNEL_NAME = "My Platform"

    CONFIG_FIELDS = []

    def __init__(self, config: dict):
        super().__init__(config)

    def send(self, title: str, content: str) -> tuple:
        return True, ""

    def test(self) -> bool:
        return True
```

放入 `channels/` 目录后，在推送通道页面即可选择该类型创建通道。

## 接口约定

### 类结构

插件必须定义继承自 `BaseChannel` 的 `Channel` 类。`BaseChannel` 由加载器自动注入，**不要** `import` 它。

```python
class Channel(BaseChannel):
    CHANNEL_TYPE = "my_platform"    # 唯一类型标识，用于路由
    CHANNEL_NAME = "My Platform"    # 界面显示名称
    CONFIG_FIELDS = [...]           # 配置表单定义
```

### 必须实现的方法

| 方法 | 说明 | 返回值 |
|------|------|--------|
| `__init__(self, config)` | 接收配置字典，须先调用 `super().__init__(config)` | - |
| `send(self, title, content)` | 发送消息 | `(True, "")` 成功；`(False, "错误信息")` 失败 |
| `test(self)` | 校验配置是否有效（「测试通道」按钮触发） | `bool` |

`send()` 收到的是**模板渲染后**的最终文本，直接发送即可，无需再处理变量。

### CONFIG_FIELDS 配置表单

`CONFIG_FIELDS` 定义通道配置页的表单，每项是一个字典：

```python
CONFIG_FIELDS = [
    {
        "name": "api_key",           # 配置键名（config 中读取）
        "type": "password",          # text | password | textarea
        "label": "API Key",          # 英文标签
        "label_zh": "API 密钥",      # 中文标签
        "desc": "Your API key",      # 英文说明
        "desc_zh": "你的 API 密钥",  # 中文说明
        "placeholder": "sk-...",     # 输入框占位符
        "required": True,            # 是否必填
        "default": ""                # 默认值
    }
]
```

字段 `type` 取值：

| 类型 | 用途 |
|------|------|
| `text` | 普通单行文本 |
| `password` | 密钥类敏感信息（掩码显示） |
| `textarea` | 多行长文本 |

在 `__init__` 中通过 `config.get("name")` 读取用户填写的值。

## 可用模块

- `requests` — 发起 HTTP 请求
- `log` — 用 `log.logger.info()` / `log.logger.error()` 记录日志
- Python 标准库

## 完整示例

一个调用 HTTP API 推送的完整通道：

```python
import requests
import log

class Channel(BaseChannel):
    CHANNEL_TYPE = "my_platform"
    CHANNEL_NAME = "My Platform"

    CONFIG_FIELDS = [
        {"name": "api_key", "type": "password", "label": "API Key",
         "label_zh": "API 密钥", "required": True, "default": ""},
        {"name": "endpoint", "type": "text", "label": "Endpoint",
         "label_zh": "接口地址", "default": "https://api.example.com/send"},
    ]

    def __init__(self, config: dict):
        super().__init__(config)
        self.api_key = config.get("api_key", "")
        self.endpoint = config.get("endpoint", "")

    def send(self, title: str, content: str) -> tuple:
        try:
            resp = requests.post(self.endpoint, json={
                "key": self.api_key,
                "title": title,
                "body": content,
            }, timeout=15)
            if resp.status_code == 200:
                return True, ""
            log.logger.error(f"my_platform 推送失败: {resp.status_code} {resp.text[:200]}")
            return False, resp.text
        except Exception as e:
            log.logger.error(f"my_platform 异常: {e}")
            return False, str(e)

    def test(self) -> bool:
        ok, _ = self.send("EGo Test", "Channel test successful!")
        return ok
```

要点：

- HTTP 请求务必设置 `timeout=15`，避免阻塞发送线程
- `send()` 内捕获所有异常，失败时返回 `(False, str(e))`
- 用 `log.logger` 记录失败详情，便于在系统日志排查

## 调试与测试

1. 创建通道并填写配置后，点击「测试」按钮，EGo 会调用 `test()` 发送一条测试消息
2. 发送失败时，错误信息会显示在界面，同时写入系统日志

## 最佳实践

- 插件保持自包含，除 `requests` 外不依赖第三方库
- 敏感配置（token、密钥）用 `password` 类型
- `test()` 复用 `send()` 逻辑，发一条真实测试消息
- 对平台返回的错误做可读化处理后返回，方便定位问题

## 常见问题

**Q：`BaseChannel` 从哪里来？**
A：由通道加载器在运行时注入，直接继承即可，**不要**写 `from xxx import BaseChannel`。

**Q：`send()` 的 title 和 content 是原始数据吗？**
A：不是。它们是经过解析器提取、路由匹配、模板渲染后的最终文本。

**Q：修改插件后需要重启吗？**
A：不需要，保存文件后下次发送时自动重新加载。
