# EGo 开发文档

> EverywhereYouGo — 通用信息转发平台
> 最后更新：2026-07-22

---

## 架构总览

```
                    ┌─────────────── EventBus (blinker) ───────────────┐
                    │                                                  │
HTTP POST ──→ source_listener ──→ parser_engine ──→ router_engine ──→ sender_engine
                    │                     │               │               │
                    │            parser_loader.py      router.py      renderer.py
                    │            parsers/emby.py       simpleeval      Jinja2(Sandboxed)/Simple
                    │                                                  channel_loader.py
                    │                                                  channels/*.py
                    │                                                       │
                    │                                                  ┌────┴────┐
                    │                                                  │ enqueue │
                    │                                                  └────┬────┘
                    │                                                       ▼
                    │                                              message_queue (SQLite)
                    │                                                       │
                    │                                                  worker.py
                    │                                                  (后台消费)
                    │                                                       │
                    │                                              失败重试 → DLQ
                    │
                    └──────── db / config_manager / log ───────────────┘
```

**数据流**：Source HTTP 监听器接收 Webhook → 事件总线分发 → 解析引擎执行解析器 → 路由引擎匹配条件 → 发送引擎按通道入队 → Worker 后台消费 → 推送通道。

**事件总线信号**（`bus.py`，基于 blinker）：

| 信号 | 触发时机 |
|------|----------|
| `message.received` | HTTP 收到原始数据 |
| `message.parsed` | 解析器执行完成 |
| `message.routed` | 路由匹配完成 |
| `message.sending` | 开始推送 |
| `message.sent` | 推送成功 |
| `message.failed` | 推送失败 |
| `config.changed` | 配置变更 |
| `source.started` / `source.stopped` | 数据源启停 |

---

## 技术选型与理由

| 选型 | 选择 | 理由 |
|------|------|------|
| 事件总线 | blinker | 轻量（纯 Python，无外部依赖），信号/接收者模式天然适合引擎解耦；对比 PyPubSub 更活跃维护 |
| 队列后端 | SQLite（默认）| 路由器环境资源有限，单实例部署，日消息量百级；SQLite WAL 模式足够并发读写；通过 `QueueBackend` 接口预留 Redis 升级口 |
| 队列粒度 | 按通道入队 | 单通道失败不影响其他通道，重试只重发失败通道，避免整条消息重复推送 |
| 模板引擎 | Jinja2 SandboxedEnvironment | 用户可编辑模板，必须防 SSTI；Sandboxed 拦截 `__class__`/`import`/`attr('__x__')` 等危险操作 |
| 条件表达式 | simpleeval | 安全求值，不暴露 Python 内置函数；支持 and/or/比较/括号，满足路由条件需求 |
| 配置持久化 | JSON 文件（唯一真相源）| 用户可直接编辑/备份/版本管理；SQLite 仅作运行时缓存，启动时从 JSON 加载 |
| 配置并发保护 | fcntl.flock | 最小改动，不引入新依赖；读共享锁/写排他锁，防 API 并发写损坏 JSON |
| 通道插件化 | importlib.util 动态加载 | 用户放 .py 文件到 channels/ 即生效，无需注册；BaseChannel 注入模块命名空间 |
| 重试策略 | 指数退避 5s/30s/2min | 平衡及时性和对第三方 API 的压力；3 次耗尽进 DLQ 由用户决定 |
| Web 框架 | Flask | 轻量，路由器跑得起；Jinja2 模板原生支持；Blueprint 拆分 API |
| 数据库 | SQLite WAL | 零运维，单文件，路由器友好；WAL 允许并发读不阻塞写 |

---

## 组件说明

### 数据源 (Source)
- `source_listener/` — HTTP 监听器包，每个数据源独立端口 + 独立线程
- 接收 HTTP POST，提取 body + headers + query params
- 请求体限制 5MB（Content-Length 超限返回 413），读取超时 10s、整体 60s
- 通过 `source_manager.process_message()` 触发全链路
- 自动保存最近 20 条样本数据（供解析器测试）

### 解析器 (Parser)
- `parsers/*.py` — 用户自定义解析器，每个文件定义 `parse(raw_body, headers, query_params)` 函数
- `parser_loader.py` — 动态加载 + 缓存 + 在线重载
- `parser_engine/` — 事件引擎包，监听 `message.received`，调用 parser_loader 执行解析
- 返回结构：`dict`，必须包含 `title`（字符串）。其余顶层字段自动展平为 KV 对，同时作为路由条件变量和模板渲染变量
- title 自动生成兜底：按 Name/title/Subject/Event 优先级查找，找不到用第一个非空值
- Parser 版本绑定：消息入库时记录解析器内容 MD5（`parser_hash`），重发时若解析器已变更会告警

### 路由 (Router)
- `router.py` — 条件匹配引擎，基于 simpleeval 安全求值
- `router_engine/` — 事件引擎包，监听 `message.parsed`，执行路由匹配 + DND 检测
- 条件表达式：`event == 'library.new' and media_type == 'Movie'`
- 支持 `and`/`or`/括号分组，空条件 = 默认匹配
- DND（勿扰模式）：可配置起止时间，非紧急消息进入 PENDING 队列，结束后自动刷新
- DND 队列上限 10000 条，溢出 DISCARD 并告警

### 模板 (Template)
- `renderer.py` — 双引擎渲染
  - Simple：`{varName}` 替换
  - Jinja2：`{{ msg.varName }}`，SandboxedEnvironment 防 SSTI
- 模板存储为 title_tpl + content_tpl 两段
- 解析器不返回 content 且 content_tpl 为空时，渲染器自动生成 KV 列表

### 渠道 (Channel)
- `channels/` — 6 种内置通道：企业微信 Bot / 企业微信 API / 钉钉 / 飞书 / Telegram / Bark
- `channel_loader.py` — 通道插件加载器（Channel SDK）
  - importlib.util 动态加载 channels/*.py
  - BaseChannel 注入模块命名空间，插件禁用 `from . import`
  - 每个插件导出 Channel 类，CONFIG_FIELDS 类属性定义动态表单字段
- `sender_engine/` — 事件引擎包，监听 `message.routed`
  - 双路径：webhook 流入队异步发送；flush/retry 直接发送（绕过队列）
  - 去重：多字段拼接去重键 + 可配窗口时间
  - 并行：ThreadPoolExecutor，最多 10 并发

### 异步队列 (Queue)
- `queue_backend.py` — 队列抽象层，默认 `SQLiteQueueBackend`，预留 Redis 升级接口
- `worker.py` — 后台消费线程（默认 1 个，SQLite 单写者友好；Redis 后端可多开）
- 按"消息 × 通道"入队，单通道失败不影响其他通道
- 重试策略：3 次指数退避（5s / 30s / 2min），耗尽移入 DLQ
- DLQ：`dead_letter_queue` 表，UI 可手动重发或丢弃
- 崩溃恢复：启动时 `recover_processing()` 重置卡住的 processing 任务

### 消息生命周期

```
RECEIVED → PARSED → SENDING → SUCCESS / FAILED
                  ↘ NO_MATCH
                  ↘ PENDING (DND 期间)
                  ↘ DISCARDED (去重命中 / DND 队列溢出)
```

---

## 配置存储

- `config/*.json` — 唯一真相源，5 类配置：sources / channels / templates / bindings / settings
- SQLite（`ego.db`）— 消息日志 + 队列 + 运行时缓存
- `config_manager.py` — 启动时 JSON → SQLite 同步，UI 编辑即时双向同步，外部修改通过 mtime 检测
- 文件锁：`fcntl.flock`（读 LOCK_SH / 写 LOCK_EX），防并发写损坏
- Schema 校验：5 类配置加载时校验必需字段，格式错误记日志告警
- 注意：启动时 `load_all()` 从 JSON 覆盖 DB，改 DB 默认值必须同步改 JSON

---

## 认证

可选功能，默认不开启。T1/T2 信任局域网无需认证，T3/T4 由 Nginx 处理。

环境变量：
- `EGO_AUTH_TOKEN` — API Bearer Token 验证
- `EGO_SECRET_KEY` — Flask session 签名（启动时检查弱 Key 并告警）

Session 24h 自动过期，`/api/health` 免认证。

---

## SSL

仅用于 Web UI 管理页面（浏览器不报不安全 + 剪贴板 API 可用）。Webhook 数据接收始终 HTTP。

`gen_cert.py` 首次启动自动生成自签名证书，支持环境变量覆盖：
- `EGO_SSL_DIR` — 证书目录
- `EGO_SSL_CERT` — 证书文件路径
- `EGO_SSL_KEY` — 私钥文件路径

---

## 健康检查

`GET /api/health`（免认证）：SQLite 连接 / 磁盘空间 / 配置文件完整性 / 队列积压状态。

---

## 版本更新检测

- `version_checker.py` — 后台线程，启动 5s 后首检，之后每 24h
- 对比 GitHub `version.json` 与本地版本
- 有新版本时侧边栏绿点 + WebSocket 推送
- API：`GET /api/version/check` / `POST /api/version/check`

---

## 部署架构

| 层级 | 命名 | 网络模式 | HTTPS | 证书管理 | 推荐场景 |
|------|------|----------|-------|----------|----------|
| T1 | 裸机直连 | `host` | ❌ | 无 | 家庭/内网调试 |
| T2 | Docker 内网 | `bridge` | ❌ | 无 | 容器间协同 |
| T3 | 企业级部署 | `bridge` + Nginx | ✅ | 手动证书 | 正式生产环境 |
| T4 | 懒人全自动 | `bridge` + Nginx + Certbot | ✅ | Let's Encrypt | 个人/小团队云端 |

**职责分工**：EGo 负责核心消息处理 + WebUI SSL + 5MB Body 防护。Nginx（T3/T4）负责 HTTPS 终结 + 认证 + 限流 + 来源鉴权。

---

## 国际化

自定义 `i18n.py`，中英双语。服务端 Jinja2 用 `_()`，客户端 JS 用 `t()`（依赖 base.html 注入的 `window.__i18n__`）。语言切换 API `/api/lang`，session + cookie 存储。

---

## 目录结构

```
EverywhereYouGo/
├── main.py                # 入口：初始化 DB → 启动 Worker → 启动监听 → 启动 WebUI
├── web_ui.py              # Flask 启动（自签名 SSL + 环境变量证书路径）
├── bus.py                 # 事件总线（blinker）
│
├── source_listener/       # HTTP 监听器（独立端口 + 5MB 限制）
├── parser_engine/         # 解析引擎（监听 message.received）
├── router_engine/         # 路由引擎（DND + 条件匹配 + 队列上限）
├── sender_engine/         # 发送引擎（去重 → 入队/直发）
│
├── queue_backend.py       # 队列抽象层（SQLite 默认，Redis 升级口）
├── worker.py              # 后台消费线程（轮询 + 重试 + DLQ）
│
├── parser_loader.py       # 解析器动态加载 + 缓存
├── router.py              # 条件匹配（simpleeval）
├── renderer.py            # 模板渲染（Simple + Jinja2 Sandboxed）
├── channel_loader.py      # 通道插件加载器
├── source_manager.py      # 编排层：全链路 + 队列刷新 + 重发
│
├── api/                   # RESTful API（11 个 Blueprint）
├── db/                    # 数据库层（connection + schema + queries，WAL）
├── channels/              # 6 种内置通道
├── parsers/               # 用户自定义解析器
├── templates/             # HTML 前端模板
├── tests/                 # 自动化测试（85 个）
├── config/                # JSON 配置文件
│
├── config_manager.py      # JSON ↔ SQLite 同步（文件锁 + Schema）
├── version_checker.py     # GitHub 版本检测
├── i18n.py                # 中英双语
├── log.py                 # 日志
├── gen_cert.py            # SSL 证书生成
│
├── doc/                   # 项目文档
├── version.json           # 版本号（机器读，供版本检查器）
├── requirements.txt
├── Dockerfile / docker-compose.yml
└── .github/workflows/     # CI/CD
```
