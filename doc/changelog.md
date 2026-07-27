# EGo 版本记录

> 每个版本的实际改动内容。机器读版本信息见 `version.json`。

---

## v1.2.2（2026-07-26）

### HTTP/HTTPS 双端口

- 应用同时监听两个端口：HTTP（`WEB_PORT`，默认 5000）承载 Webhook 接收（`/in/...`）与 `/api/health`；HTTPS（`WEB_SSL_PORT`，默认 5001）承载管理页面。
- 管理页面被 HTTP 访问时 301 跳转到 HTTPS 端口；webhook / 健康检查保持 HTTP。
- 修复 Docker `HEALTHCHECK` 失败：健康检查改走 HTTP 端口（此前证书自动生成后服务仅 HTTPS，http 探针失败）。
- Dockerfile / docker-compose 暴露双端口，新增 `WEB_SSL_PORT` 环境变量。

### 可靠性与卫生加固

- #25 数据库连接改为 `threading.local()` 每线程独立连接，修复全局单例 + `check_same_thread=False` 在并发下的 "recursive use of cursors" / 事务串扰隐患。
- #29 `source_listener` 请求处理不再静默吞异常：客户端断开记 debug，其余错误记 warning。
- #22 备份恢复 / 导入的文件写入用 `os.path.basename()` 压平 + `_safe_filename()` 校验，封堵路径穿越。
- #32 备份恢复解压累计超过 10MB（`MAX_RESTORE_SIZE`）即整体拒绝，防 ZIP 炸弹。
- #24 会话 Cookie 加固：`HttpOnly` + `SameSite=Lax`，启用 HTTPS 时追加 `Secure`（最小化 CSRF 缓解，内网自管理场景不引入完整 Flask-WTF）。
- 新增 `tests/test_backup_hardening.py`（9 例）。

### 文档

- `doc/improvement.md` 结合"Docker 单容器 + WebUI 仅内网自管理"威胁模型重新分级，新增"部署上下文与威胁模型"一节。

---

## v1.2.0（2026-07-21）

### 安全加固（Phase 0）

- Jinja2 渲染改用 `SandboxedEnvironment`，拦截 `__class__`/`import`/`attr('__x__')` 等 SSTI 攻击向量
- HTTP Body 限制 5MB（Content-Length 超限返回 413）
- HTTP Server 超时：读取 10s、整体 60s
- Session 24h 自动过期（`PERMANENT_SESSION_LIFETIME`）
- 启动时检测弱 Secret Key 并告警
- `/api/health` 路由免认证

### 异步队列架构（Phase 1）

- 新增 `queue_backend.py`：队列抽象层，`SQLiteQueueBackend` 默认实现，预留 Redis 接口
- 新增 `worker.py`：后台消费线程，100ms 轮询，启动时恢复卡住的 processing 任务
- 新增 `message_queue` 表 + `dead_letter_queue` 表
- 按"消息 × 通道"粒度入队，单通道失败不影响其他通道
- 3 次指数退避重试（5s / 30s / 2min），耗尽移入 DLQ
- DLQ 支持 UI 手动重发或丢弃
- SQLite 启用 WAL 模式（`journal_mode=WAL`）
- `sender_engine` 双路径：webhook 流入队异步；flush/retry 直接发送
- 事件总线每个 listener 独立 try-except，单 handler 异常不中断链路

### 健壮性（Phase 2）

- 配置 JSON 文件锁（`fcntl.flock`，读 LOCK_SH / 写 LOCK_EX）
- 5 类配置 Schema 校验（parsers/sources/channels/templates/bindings 必需字段检查）
- DND 队列上限 10000 条，溢出 DISCARD 并告警
- 消息详情页展示 Trace ID + sent_at
- Parser 版本绑定：消息入库记录解析器 MD5（`parser_hash` 字段），重发时检测变更并告警

### 可观测性（Phase 3 部分）

- `/api/health` 深度健康检查：SQLite 连接 / 磁盘空间 / 配置文件 / 队列积压
- SSL 证书路径支持环境变量：`EGO_SSL_DIR` / `EGO_SSL_CERT` / `EGO_SSL_KEY`

### 测试

- 测试用例从 51 扩展到 85
- 新增 `test_queue_backend.py`（12 用例）：入队/出队/ack/nack/重试/DLQ/恢复/FIFO/单例
- 新增 `test_config_manager.py`（18 用例）：Schema 校验 + 文件锁读写
- `test_renderer.py` 新增 SSTI 防护测试（4 用例）

### 清理

- 删除死代码 `db.py`（被 `db/` 包完全 shadow）和 `sender.py`（零引用残留）
- `requirements.txt` 补充 `blinker>=1.7`

---

## v1.1.0（2026-07-18）

### 架构重构

- 事件总线 `bus.py`（blinker 信号系统），三大引擎解耦为独立包
- API 拆分为 11 个 Blueprint（`web_ui.py` 1217 行 → 25 行兼容层）
- `parser_engine/` — 解析引擎包
- `router_engine/` — 路由引擎包（含 DND 检测）
- `sender_engine/` — 发送引擎包（去重 + 并行发送）
- `source_listener/` — HTTP 监听器包（每源独立端口/线程）
- `db/` — 数据库拆为包（connection + schema + queries）
- `source_manager.py` 保留为编排层

### 功能

- WebUI 自签名 SSL（`gen_cert.py` 自动生成）
- 中英双语 i18n 全量支持
- 推送通道插件化（Channel SDK，`channel_loader.py` 动态加载）
- 去重配置：多字段拼接去重键 + 可配窗口时间
- 消息清理时间可配置
- 版本更新检查（GitHub `version.json`，后台线程 24h 轮询）

### 安全

- SQL 注入防护（参数化查询）
- 认证中间件（HMAC 对比）
- Parser 缓存 + 在线重载

### 文档

- 部署架构文档化（T1-T4 四层模型）
