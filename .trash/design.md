# EGo 项目设计文档 v1.2.0

> EverywhereYouGo — 通用信息转发平台

## 架构

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

**数据流**：Source HTTP 监听器接收 Webhook → 事件总线分发 → 解析引擎执行解析器 → 路由引擎匹配条件 → 发送引擎渲染模板并**异步入队** → Worker 后台消费 → 推送通道。

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

## 组件说明

### 数据源 (Source)
- `source_listener/` — HTTP 监听器包，每个数据源独立端口 + 独立线程
- 接收 HTTP POST，提取 body + headers + query params
- **请求体限制 5MB**（Content-Length 超限返回 413），读取超时 10s、整体 60s
- 通过 `source_manager.process_message()` 触发全链路
- 自动保存最近 20 条样本数据（供解析器测试）

### 解析器 (Parser)
- `parsers/*.py` — 用户自定义解析器，每个文件定义 `parse(raw_body, headers, query_params)` 函数
- `parser_loader.py` — 动态加载 + 缓存 + 在线重载
- `parser_engine/` — 事件引擎包，监听 `message.received`，调用 parser_loader 执行解析
- **返回结构**：`dict`，必须包含 `title`（字符串）。其余所有顶层字段自动展平为 KV 对，同时作为路由条件变量和模板渲染变量
  ```python
  # 示例返回
  {"title": "星际穿越", "event": "library.new", "media_type": "Movie", "Overview": "一部科幻电影..."}
  ```
- title 自动生成兜底：若解析器未设 title，按 Name/title/Subject/Event 优先级查找，找不到用第一个非空值
- **Parser 版本绑定**：消息入库时记录解析器内容 MD5（`parser_hash`），重发时若解析器已变更会告警，确保 PENDING 消息用原始版本语义

### 路由 (Router)
- `router.py` — 条件匹配引擎，基于 simpleeval 安全求值
- `router_engine/` — 事件引擎包，监听 `message.parsed`，执行路由匹配 + DND 检测
- 条件表达式：`event == 'library.new' and media_type == 'Movie'`
- 支持 `and`/`or`/括号分组
- 空条件 = 默认匹配（无条件绑定兜底）
- **DND（勿扰模式）**：可配置起止时间，DND 时段内非紧急消息自动进入 PENDING 队列，结束后自动刷新发送
- **DND 队列上限 10000 条**，溢出消息直接 DISCARD 并告警，防长时间 DND 内存膨胀

### 模板 (Template)
- `renderer.py` — 双引擎渲染
  - **Simple**：`{varName}` 替换，变量为 msg 的所有顶层标量字段
  - **Jinja2**：`{{ msg.varName }}`，msg 作为上下文变量注入，**使用 `SandboxedEnvironment` 防 SSTI**（拦截 `__class__`/`import`/`attr('__xxx__')` 等危险操作）
- 模板存储为 title_tpl + content_tpl 两段

### 渠道 (Channel)
- `channels/` — 6 种内置通道：
  - 企业微信 Bot（`wechat_work_bot.py`）
  - 企业微信 API（`wechat_work_api.py`）
  - 钉钉（`dingtalk.py`）
  - 飞书（`feishu.py`）
  - Telegram（`telegram_bot.py`）
  - Bark（`bark.py`）
- `channel_loader.py` — 通道插件加载器，支持用户自定义 Python 通道插件（Channel SDK）
- `sender_engine/` — 事件引擎包，监听 `message.routed`，执行去重检查 → **按通道入队** → Worker 异步消费
  - 去重：支持多字段拼接去重键（如 `event+Item.Type`）+ 可配窗口时间
  - 双路径：webhook 流入队异步发送；flush/retry 直接发送（绕过队列）
  - 并行：ThreadPoolExecutor，最多 10 并发

### 异步队列 (Queue)
- `queue_backend.py` — 队列抽象层，默认 `SQLiteQueueBackend`，预留 Redis 升级接口
- `worker.py` — 后台消费线程（默认 1 个，SQLite 单写者友好；Redis 后端可多开）
- **粒度**：按"消息 × 通道"入队，单通道失败不影响其他通道，重试只重发失败通道
- **重试策略**：3 次指数退避（5s / 30s / 2min），耗尽后移入死信队列（DLQ）
- **DLQ**：`dead_letter_queue` 表，UI 可手动重发或丢弃
- **崩溃恢复**：启动时 `recover_processing()` 把卡在 processing 的任务重置为 pending
- **WAL 模式**：SQLite 启用 `journal_mode=WAL`，并发读写不阻塞

### 消息生命周期

```
RECEIVED → PARSED → SENDING → SUCCESS / FAILED
                  ↘ NO_MATCH
                  ↘ PENDING (DND 期间)
                  ↘ DISCARDED (去重命中 / DND 队列溢出)
```

## 配置存储

- `config/*.json` — 配置持久化（**唯一真相源**），5 类配置：
  - `sources.json`（数据源）
  - `channels.json`（推送渠道）
  - `templates.json`（推送模板）
  - `bindings.json`（数据源→渠道绑定，含条件表达式/去重配置）
  - `settings.json`（系统设置：DND/日志等）
- SQLite（`ego.db`）— 消息日志 + 队列 + 运行时缓存
- `config_manager.py` — 启动时 JSON → SQLite 同步，UI 编辑即时双向同步，外部修改通过 mtime 检测
- **文件锁**：JSON 读写使用 `fcntl.flock`（读共享锁 / 写排他锁），防并发写损坏
- **Schema 校验**：5 类配置加载时校验必需字段（parsers/sources/channels/templates/bindings），格式错误记日志告警

## 认证

可选功能，默认不开启。T1/T2 信任局域网无需认证，T3/T4 由 Nginx 处理。

若手动开启，通过环境变量配置：
- `EGO_AUTH_TOKEN` — API Bearer Token 验证
- `EGO_SECRET_KEY` — Flask session 签名（启动时检查弱 Key 并告警）

Session 24h 自动过期，`/api/health` 路由免认证（供监控探针）。

## SSL

EGo 支持自签名 SSL，仅用于 **Web UI 管理页面**，提供两个便利：
- 浏览器不报"不安全"
- 剪贴板 API（`navigator.clipboard`）在 HTTPS 下正常工作

**Webhook 数据接收端口始终走 HTTP**，不受 SSL 影响。T3/T4 部署中数据接收的 SSL 由 Nginx 统一处理。

通过 `gen_cert.py` 首次启动自动生成自签名证书（默认 `certs/ego.crt` + `certs/ego.key`），支持环境变量覆盖：
- `EGO_SSL_DIR` — 证书目录
- `EGO_SSL_CERT` — 证书文件路径
- `EGO_SSL_KEY` — 私钥文件路径

## 健康检查

`GET /api/health` 返回 JSON，检查项：
- SQLite 连接可用性
- 磁盘剩余空间
- 配置文件完整性
- 队列积压状态（pending / processing / dlq）

免认证访问，供 Docker healthcheck、Nginx upstream 探针、Prometheus blackbox 等使用。

## 版本更新检测

- `version_checker.py` — 后台线程，启动 5s 后首次检查，之后每 24h
- 对比 GitHub `version.json` 与本地版本
- 有新版本时侧边栏显示绿点提示 + WebSocket 推送
- API：`GET /api/version/check`（查看）、`POST /api/version/check`（手动触发）

## 部署架构

EGo 支持 4 种部署层级，按场景从简到繁：

| 层级 | 命名 | 网络模式 | HTTPS | 证书管理 | 运维复杂度 | 推荐场景 |
|------|------|----------|-------|----------|-----------|----------|
| T1 | 裸机直连 | `host` | ❌ | 无 | ⭐ | 家庭/内网调试 |
| T2 | Docker 内网 | `bridge` | ❌ | 无 | ⭐⭐ | 容器间协同 |
| T3 | 企业级部署 | `bridge` + Nginx | ✅ | 手动证书 | ⭐⭐⭐ | 正式生产环境 |
| T4 | 懒人全自动 | `bridge` + Nginx + Certbot | ✅ | 自动 Let's Encrypt | ⭐⭐ | 个人/小团队云端部署 |

### T1 裸机直连
- EGo 直接监听宿主机端口（HTTP），局域网内直连访问
- 适用于家庭内网设备（NAS、路由器等）

### T2 Docker 内网
- EGo 运行在 Docker 容器中，bridge 网络，通过宿主机端口映射访问
- 适用于容器编排场景（多服务间协同）

### T3 企业级部署
- EGo 在 Docker bridge 内只开 HTTP，前端由 Nginx 反向代理 + SSL 终结
- Nginx 负责：HTTPS 证书、认证、速率限制、请求体大小限制
- 适用于正式生产环境，有固定域名或公网 IP

### T4 懒人全自动
- 在 T3 基础上增加 Certbot 自动申请和续签 Let's Encrypt 证书
- 适合个人/小团队云端服务器，无需手动管理证书

### 职责分工

| 功能 | EGo（所有层级） | Nginx（T3/T4） |
|------|----------------|----------------|
| 消息接收与转发 | ✅ 核心处理 | — |
| WebUI SSL | ✅ 自签名证书，方便浏览器访问 | ✅ 可选代理接管 |
| Webhook 数据接收 SSL | ❌ 始终 HTTP | ✅ Nginx 统一处理 |
| 用户认证 | ❌ 默认无（局域网信任） | ✅ 按需配置 |
| API 限流 | ❌ 不内置 | ✅ `limit_req` |
| 请求体大小限制 | ✅ 5MB 内置防护 | ✅ `client_max_body_size` 可叠加 |
| Webhook 来源鉴权 | ❌ 不内置 | ✅ 按需配置 |

> **说明**：
> - **WebUI SSL**：EGo 用自签名证书给管理页面开 HTTPS，T1/T2 下浏览器不报不安全、剪贴板 API 正常工作。T3/T4 里可以交由 Nginx 统一管理。
> - **Webhook 数据接收**：始终走 HTTP，与 SSL 无关。T3/T4 由 Nginx 前置代理做 SSL 终结。
> - **安全边界**：T1/T2 信任局域网环境。T3/T4 将外部安全交由 Nginx 处理。EGo 代码层只负责核心逻辑相关事项——如 SSTI 防护（功能级 RCE）、出站通道限流（Nginx 管不到出站）等。

## 备份恢复

- **备份**：下载 ZIP（`config/*.json` + `parsers/*.py` + `version.txt`）
- **恢复**：上传 ZIP，覆盖配置后即时生效，支持插入/覆盖模式

## 国际化

- 中英双语支持（`i18n.py`）
- 页面顶部语言切换

## 目录结构

```
EverywhereYouGo/
├── main.py                # 入口：初始化 DB → 启动 Worker → 启动监听 → 启动 WebUI
├── web_ui.py              # Flask 启动（兼容层，支持自签名 SSL + 环境变量证书路径）
├── bus.py                 # 事件总线（blinker 信号声明 + emit/on/off）
│
├── source_listener/       # HTTP 监听器包（Source HTTP Server）
│   └── __init__.py        # HookHandler + ListenerManager + 样本存储 + 5MB Body 限制
├── parser_engine/         # 解析引擎包（事件驱动）
│   └── __init__.py        # 监听 message.received，调用 parser_loader，记录 parser_hash
├── router_engine/         # 路由引擎包（事件驱动）
│   └── __init__.py        # 监听 message.parsed，DND 检测 + 路由匹配 + 队列上限保护
├── sender_engine/         # 发送引擎包（事件驱动）
│   └── __init__.py        # 监听 message.routed，去重 → 按通道入队 / 直发
│
├── queue_backend.py       # 队列抽象层（SQLite 默认，Redis 升级口）
├── worker.py              # 后台消费线程（轮询 + 重试 + DLQ）
│
├── parser_loader.py       # 解析器动态加载 + 缓存 + 重载
├── router.py              # 路由条件匹配（simpleeval）
├── renderer.py            # 模板渲染（Simple + Jinja2 SandboxedEnvironment）
├── channel_loader.py      # 通道插件加载器（Channel SDK）
├── source_manager.py      # 编排层：全链路 process_message + 队列刷新 + 重发
│
├── api/                   # RESTful API（11 个蓝图）
│   ├── __init__.py        # Blueprint 注册 + 认证中间件 + Session 24h 过期
│   ├── auth.py            # 登录/登出
│   ├── backup.py          # 导出/导入/备份/恢复
│   ├── channels.py        # 通道 CRUD
│   ├── logs.py            # 日志查询/清理
│   ├── messages.py        # 消息查询/重发/批量/忽略
│   ├── pages.py           # HTML 页面渲染
│   ├── parsers.py         # 解析器 CRUD + 在线编辑 + 测试
│   ├── sources.py         # 数据源 CRUD + 绑定 + 样本 + 测试
│   ├── system.py          # 系统设置/健康检查/语言/队列状态
│   └── templates.py       # 模板 CRUD + 测试渲染
│
├── db/                    # 数据库层
│   ├── __init__.py        # 公开接口（与旧 db.py 兼容）
│   ├── connection.py      # 连接管理（WAL 模式）
│   ├── queries.py         # SQL 查询（含 message_queue / dead_letter_queue）
│   └── schema.py          # 表结构定义（含队列表 + DLQ 表 + parser_hash）
├── db.py                  # 兼容入口（代理到 db/ 包）
│
├── channels/              # 6 种内置通道实现
│   ├── wechat_work_bot.py
│   ├── wechat_work_api.py
│   ├── dingtalk.py
│   ├── feishu.py
│   ├── telegram_bot.py
│   └── bark.py
│
├── parsers/               # 用户自定义解析器
│   └── emby.py            # Emby Webhook 解析器（示例）
│
├── templates/             # HTML 前端模板（Jinja2 渲染）
├── tests/                 # 自动化测试（85 个）
├── config/                # JSON 配置文件目录
│
├── config_manager.py       # JSON ↔ SQLite 配置同步（文件锁 + Schema 校验）
├── version_checker.py      # GitHub 版本更新检测
├── i18n.py                 # 中英双语翻译
├── log.py                  # 日志系统
│
├── gen_cert.py             # SSL 自签名证书生成（支持 EGO_SSL_* 环境变量）
├── certs/                  # SSL 证书目录
│
├── doc/                    # 设计文档
├── Dockerfile
├── docker-compose.yml
├── build.py                # 构建脚本
├── requirements.txt
├── version.json            # 版本号 + 更新日志
├── README.md / README.en.md
└── .github/workflows/      # GitHub Actions CI/CD
```

## 版本历史

### v1.2.0（当前）

**Phase 0 安全加固**
- Jinja2 渲染改用 `SandboxedEnvironment`，防 SSTI（`__class__`/`import`/`attr` 全部拦截）
- HTTP Body 限制 5MB（超限 413），读取/整体超时（10s / 60s）
- Session 24h 过期，弱 Secret Key 启动告警，`/api/health` 免认证

**Phase 1 异步队列架构**
- 新增 `queue_backend.py` + `worker.py`：HTTP 立即返回 200，后台 Worker 消费
- 按"消息 × 通道"粒度入队，单通道失败不影响其他通道
- 3 次指数退避重试（5s / 30s / 2min），耗尽进入死信队列（DLQ）
- DLQ 支持 UI 手动重发或丢弃
- SQLite WAL 模式，进程崩溃后自动恢复 processing 任务
- 队列后端接口化，预留 Redis 升级路径

**Phase 2 健壮性**
- 配置 JSON 文件锁（`fcntl.flock`），防并发写损坏
- 5 类配置 Schema 校验，缺字段记日志告警
- DND 队列上限 10000 条，溢出 DISCARD 并告警
- 消息详情页展示 Trace ID + sent_at
- Parser 版本绑定（MD5 哈希），重发时检测解析器变更

**Phase 3 可观测性（部分）**
- `/api/health` 深度健康检查（SQLite / 磁盘 / 配置 / 队列）
- SSL 证书路径支持环境变量（`EGO_SSL_DIR` / `EGO_SSL_CERT` / `EGO_SSL_KEY`）

**测试**
- 测试用例从 51 个扩展到 85 个
- 新增 `test_queue_backend.py`（12 用例）+ `test_config_manager.py`（18 用例）
- `test_renderer.py` 新增 SSTI 防护测试（4 用例）

### v1.1.0

- 事件总线架构（blinker 信号系统）
- API 拆分为 11 个蓝图（web_ui.py 1217 行 → 25 行）
- 三大引擎包：parser_engine / router_engine / sender_engine
- WebUI 自签名 SSL 支持（方便浏览器访问）
- 中英双语 i18n 全量支持
- 推送通道插件化（Channel SDK）
- 去重配置 + 多字段拼接去重键
- 消息清理时间可配置
- 版本更新检查（GitHub version.json）
- 多项安全修复（SQL 注入防护 / HMAC 对比 / parser 缓存）
- 部署架构文档化（4 层模型：裸机 / Docker / Nginx / Nginx+Certbot）
