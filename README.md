# EverywhereYouGo (EGo) v1.3.0

[English](README.en.md) | 中文

> 通用信息转发平台 — 数据 → 解析 → 路由 → 推送

接收任意 HTTP 请求，经解析器提取结构化字段，按条件路由到多个推送渠道。

## Docker 部署

**一键部署（推荐）：**

```bash
curl -O https://raw.githubusercontent.com/codename-test/EverywhereYouGo/main/deploy/init.sh
chmod +x init.sh
./init.sh
# 按提示选择部署模式
```

支持 5 种部署模式：default（快速起步）、t1-host（host 网络）、t2-bridge（bridge 网络）、t3-nginx（Nginx + 手动证书）、t4-acme（Nginx + Let's Encrypt 全自动证书）。

> 更多部署形态说明见 [deploy/README.md](deploy/README.md)。

启动后：管理页面 `https://<主机IP>:5001`（自签名证书，浏览器需放行）；Webhook 接收与健康检查走 `http://<主机IP>:5000`。

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

设置 `EGO_AUTH_TOKEN` 环境变量后开启访问控制：

```bash
EGO_AUTH_TOKEN=your-secret-token python3 main.py
```

- Web 页面需通过登录页输入 Token
- API 调用需携带 `Authorization: Bearer your-secret-token` 请求头
- 健康检查 `/api/health` 无需认证

可选设置 `EGO_SECRET_KEY` 自定义 Flask session 密钥。

## 配置存储

配置有两份，角色不同：

| 存储 | 角色 |
|------|------|
| SQLite（`ego.db`） | **运行时真相源** —— 所有读写以库内数据为准 |
| `config/*.json` | **导出 / 备份介质** —— 便于备份、版本管理与迁移 |

| 文件 | 内容 |
|------|------|
| `config/parsers.json` | 解析器元信息 |
| `config/sources.json` | 数据源定义 |
| `config/channels.json` | 推送通道配置 |
| `config/templates.json` | 推送模板 |
| `config/bindings.json` | 渠道绑定（含条件表达式） |

**启动时的加载规则：**

1. 数据库**已有**配置 → 以数据库为准，不读 JSON，并把当前配置**刷写**回 `config/*.json`
2. 数据库**为空**且有 JSON → 从 JSON 导入（首次启动 / 迁移 / 恢复）
3. 数据库为空且无 JSON → 导出初始配置到 JSON

因此 **日常改配置请用 WebUI**（改完即时生效）。直接编辑 `config/*.json` 只在
「数据库为空」的首次导入场景才会被读取，不是常规生效路径。

系统设置（DND、日志级别等）、消息日志与队列同样存储在 SQLite。
配置备份 / 恢复请用「系统设置 → 备份」，会打包 `config/*.json` + **用户上传的** `parsers/*.py` 与 `channels/*.py`。

## 插件目录

内置插件与用户上传的插件**分开放**：

| 目录 | 内容 | Docker |
|------|------|--------|
| `parsers_builtin/` | 内置解析器 | 随镜像发布，**不打卷** |
| `parsers/` | 你上传的解析器 | 挂 `ego_parsers` 卷，容器重建不丢 |
| `channels_builtin/` | 内置通道插件 | 随镜像发布，**不打卷** |
| `channels/` | 你上传的通道插件 | 挂 `ego_channels` 卷，容器重建不丢 |

规则：

- **解析顺序：用户目录优先**，其次内置目录
- **与内置插件同名不允许上传**（返回明确错误）—— 内置插件随镜像更新，
  同名文件不会生效，不如一开始就说清楚
- **内置插件只读**：WebUI 里可以查看，但不能改、不能删
- 确实要改内置插件的行为：把改好的文件**换个名字**放进用户目录，
  或直接改源码重建镜像

> **为什么必须分开**：如果把卷挂在放内置插件的目录上，named volume 首次创建会把
> 镜像里该目录的内容拷进卷里，之后就以卷为准 —— 镜像升级再也更新不到内置插件。
> 两者同目录无解：要么丢用户文件，要么内置永远升不上去。

**从旧版本升级**：旧版本把用户上传的插件直接写在内置目录里，而该目录没有持久化，
容器重建就会丢失。升级请**先**「系统设置 → 备份」导出一份，升级后再恢复 ——
恢复会写进新的用户目录，并自动跳过与内置同名的条目（避免用旧副本遮蔽新版内置插件）。

## 解析器

放在 `parsers/` 目录下的 `.py` 文件，定义一个 `parse()` 函数：

```python
def parse(raw_body: bytes, headers: dict, query_params: dict) -> dict:
    data = json.loads(raw_body)
    event = data.get("Event", "")
    name = data.get("Item", {}).get("Name", "")
    return {
        "title": name,
        "event": event,
        "name": name,
    }
```

返回 dict 中除 `title` 外的字段同时用于：
- **路由条件匹配**：`event == 'library.new' and media_type == 'Movie'`
- **模板变量引用**：`{name}` / `{{ msg.name }}`

## 路由条件

支持 `and`、`or`、括号分组：

| 示例 | 说明 |
|------|------|
| `event == 'library.new'` | 仅新入库 |
| `event == 'library.new' and media_type == 'Movie'` | 仅新入库电影 |
| `event == 'library.new' or event == 'test'` | 新入库或测试消息 |

## 功能特性

### 免打扰（DND）
设置免打扰时段后，消息进入队列等待，结束后自动刷新。紧急路由不受 DND 影响。

### 消息去重
去重粒度是 **消息 × 通道**：每个渠道绑定各自配置 `dedup_key_expr` 与 `dedup_window`
（默认 3600 秒），互不影响。命中的渠道被跳过，其余渠道照常发送；
只有**全部**渠道命中时，整条消息才标记为 `DISCARDED`。

### 并行推送
多渠道匹配时线程池并行发送，总延迟取决于最慢的单个渠道。

### 样本数据与在线调试
每个数据源自动保存最近 20 条请求样本，可在 WebUI 中选取样本进行测试解析和推送。

### 消息重发
失败消息支持原始重发（使用已解析的 msg_json）或重新解析后重发。
默认**只重发上次失败的渠道**，不会把已经成功的渠道重复推送一遍；
需要整体重推时可用 `scope=all`。

### 导入导出
- **备份**：下载 ZIP 包（`config/*.json` + `parsers/*.py`）
- **恢复**：上传 ZIP 包，覆盖配置后自动生效
- **JSON 导入**：支持 dry_run 预览、insert/overwrite 两种模式、依赖检查

### 通道熔断
第三方渠道持续故障时自动隔离，避免拖垮整条发送链路：

- 滑动窗口（默认 60s）内失败率 > 50%，**或**连续失败 ≥ 5 次 → 熔断
- 冷却时间指数退避 30 → 60 → 120 → … → 600 秒（封顶 10 分钟）
- 冷却结束后进入半开探测，连续 3 次成功才恢复
- **4xx 不计失败**（业务侧拒绝不等于服务故障），只对 5xx / 超时 / 连接类错误计数
- 熔断期间消息**留在队列等待**：不消耗重试次数、不丢弃；状态持久化，重启后仍生效

### 出站限流
按通道独立限流（条/分钟），防止发送过快被对方封禁。重试解决不了 429——
限流必须发生在发送**之前**。拿不到令牌的消息会排队等待，而不是被丢弃。

### 韧性界面
| 位置 | 能做什么 |
|------|---------|
| 通道列表 → **「韧性」列** | 查看限流徽章与熔断倒计时；熔断时可一键「手工恢复」 |
| 通道编辑弹窗 | 设置该通道的出站限流（留空/0 = 不限流） |
| 系统设置 → **韧性（通道熔断）** | 手工调整滑动窗口、连续失败阈值、冷却基数/上限、探测次数等参数 |

参数取值优先级：`system_config`（设置页 / 直接改库） > 环境变量 > 内置默认，
改完即时生效、无需重启。

### 可观测性
| 端点 | 说明 |
|------|------|
| `GET /api/metrics` | 队列深度、死信总数、各通道成功率、端到端延迟、熔断与限流状态；`?hours=N` 调整统计窗口（默认 24h） |
| `GET /api/resilience` | 当前处于熔断 / 限流状态的通道 |
| `GET /api/queue/stats` | 队列与死信计数 |
| `GET /api/health` | 健康检查（SQLite / 磁盘 / 配置 / 队列） |

### 优雅停机
收到 `SIGTERM` 时先停止接收新消息，再等待在途任务完成（最长 30 秒），
超时未完成的转入死信队列——容器重启不会丢消息。

## 国际化

内置中英文双语支持，通过导航栏右上角语言切换按钮随时切换。

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
| `WEB_PORT` | `5000` | HTTP 端口（Webhook 接收 / 健康检查） |
| `WEB_SSL_PORT` | `5001` | HTTPS 端口（管理页面，证书缺失时不启用） |
| `EGO_SSL_ENABLED` | `1` | 设为 `0` 完全关闭内置 HTTPS（仅 HTTP，不跳转、不生成证书） |
| `EGO_SSL_DIR` | `./certs` | SSL 证书目录，`ego.crt` 和 `ego.key` 存放位置 |
| `EGO_SSL_CERT` | `./certs/ego.crt` | 证书文件路径（覆盖 `EGO_SSL_DIR`） |
| `EGO_SSL_KEY` | `./certs/ego.key` | 私钥文件路径（覆盖 `EGO_SSL_DIR`） |
| `DB_PATH` | `ego.db` | 数据库路径 |
| `LOG_LEVEL` | `INFO` | 日志等级 |
| `EGO_AUTH_TOKEN` | *(空)* | 访问控制 Token |
| `EGO_SECRET_KEY` | *(自动)* | Flask session 密钥 |
| `EGO_INGRESS_WORKERS` | `8` | 每个端口数据源的入口工作线程数 |
| `EGO_INGRESS_MAX_QUEUE` | `200` | 入口等待队列上限，超出返回 503（背压） |
| `EGO_CLEANUP_INTERVAL` | `600` | 旧消息 / 去重键的清理间隔（秒） |
| `EGO_BREAKER_WINDOW` | `60` | 熔断滑动窗口（秒） |
| `EGO_BREAKER_MIN_SAMPLES` | `5` | 窗口内触发失败率判定的最少样本数 |
| `EGO_BREAKER_FAILURE_RATIO` | `0.5` | 窗口失败率阈值（超过则熔断） |
| `EGO_BREAKER_CONSECUTIVE` | `5` | 连续失败阈值（照顾低频通道） |
| `EGO_BREAKER_OPEN_BASE` | `30` | 熔断冷却基数（秒），逐次翻倍 |
| `EGO_BREAKER_OPEN_MAX` | `600` | 熔断冷却上限（秒） |
| `EGO_BREAKER_HALF_OPEN_OK` | `3` | 恢复所需连续探测成功次数 |
| `EGO_RATE_MAX_WAIT` | `1.0` | 限流取令牌的最长等待（秒），超时改为延迟重排 |
| `EGO_RATE_MISS_TTL` | `30` | 未配置限流的通道，回查数据库的间隔（秒） |

## License

MIT
