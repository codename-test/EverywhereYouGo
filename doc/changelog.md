# EGo 版本记录

> 每个版本的实际改动内容。机器读版本信息见 `version.json`。

---

## v1.3.0（2026-07-28）

> 主题：**工程收口** —— 从「功能实现」转向「异常情况下是否可靠」。
> 可靠性 + 入口并发 + 一个让重试形同失效的时区 bug。

### 可靠性（本轮重点）

- 🆕 **通道熔断（#19）** `circuit_breaker.py`：CLOSED / OPEN / HALF_OPEN 三态机。
  - 滑动窗口 60s 内失败率 > 50%（样本数 ≥ 5）**或**连续失败 ≥ 5 → 熔断（后者照顾低频通道）
  - OPEN 冷却指数退避 30→60→120→240→480→600s（封顶 10min）
  - HALF_OPEN 连续 3 次探测成功才恢复 CLOSED，恢复后退避计数归零
  - **4xx 不计失败**，只对 5xx / 超时 / 连接类错误计数
  - 状态持久化 `channel_breaker` 表，重启后自动恢复
  - 参数由 `EGO_BREAKER_*` 环境变量覆盖；`GET /api/resilience` 可查，支持手工 reset
- 🆕 **出站限流（#21）** `rate_limiter.py`：每通道独立令牌桶（条/分钟）。
  - 桶容量 = 1 分钟额度（允许小幅突发）
  - `acquire()` 最多等 1s，拿不到令牌就**延迟重排**，不长阻塞 worker 线程
  - 配置存 `channel_rate_limit` 表；缓存带 30s TTL 回查，直接改库也能自愈
- 🆕 **延迟重排** `queue_backend.defer()`：熔断 / 限流期间把任务放回队列，
  但**不消耗重试次数**——否则故障期内消息会被重试耗尽、直接跌进死信队列。
  超过 50 次仍发不出去，才交回正常重试 / DLQ 路径。

> 为什么限流不能靠 Retry 兜底：发送过快 → 429 → 重试 → 再次 429。
> 限流必须位于 `Worker → Rate Limiter → Channel`（Nginx 只管入站，管不到出站）。

- 🆕 **优雅停机**：`worker.stop_workers(timeout=30)` 改为「停消费 → join 等在途任务 →
  超时兜底把未完成的刷入死信队列」；`main.shutdown()` 明确**先停接收端再停 worker**
  （反过来的话接收端还会继续塞消息，worker 已退出，消息会卡在队列里）。
  原实现只置 `_running=False` 就返回，进程随即退出，在途任务会卡在 processing 状态。

### 入口并发

- 🆕 **端口数据源入口并发** `source_listener/__init__.py`：单线程 `HTTPServer`
  → 固定大小工作线程池（`_IngressPool` + `_ThreadPoolHTTPServer`）。
  原实现下同一数据源的请求被**串行**处理，一个慢解析器会阻塞该数据源上所有后续请求。
  - 线程复用 → `threading.local()` 的 DB 连接随之复用，不再每请求新建 SQLite 连接
  - 队列满回 503 做背压，不无限堆积
  - 新增 `EGO_INGRESS_WORKERS`（默认 8）、`EGO_INGRESS_MAX_QUEUE`（默认 200）
- `request_queue_size = 128`：默认仅 5，突发时内核会拒掉多余连接，客户端只能等
  TCP SYN 重传（实测表现为 ~1.1s 长尾）。
- `stop_source()` 补 `server_close()`：原来只 `shutdown()`，监听 socket 与线程池不释放。

> **范围说明**：路径路由入口（`/in/...`）本就并发（`run_simple(threaded=True)`），不在此列。
> **实测结论**：并发 8 时 p50 由 127ms 降至 21ms；**但加线程并不提升吞吐**——
> 吞吐受单进程串行段限制，线程池的价值是「慢请求不阻塞其它请求」，故默认值取 8 而非更大。

### 渠道级语义（P1）

- 🆕 **渠道级重发（Channel-Level Retry）**：`retry_message(msg_id, mode, scope="failed")`
  默认**只重发上次失败的渠道**，不再把已成功的渠道重复推送。
  结果条目补 `channel_id` 作为定位依据；旧记录缺该字段时自动回退「整条重发」（向后兼容）。
  `scope="all"` 保留整体重推能力。
- 🆕 **渠道级去重（Channel-Level Dedup）**：去重粒度从「整条消息」下沉到
  **message × channel**。新增 `dedup_keys(channel_id, dedup_key, sent_at)` 表，
  逐绑定判定——命中的只跳过该渠道，全部命中才把消息标记 `DISCARDED`。
  修掉了原实现的两个问题：只取第一个绑定的去重表达式（其余被 `break` 忽略）、
  以及任一命中就丢弃整条消息。

### 并发一致性（P1）

- 🔴 **多 worker 结果回写丢更新**：`update_message_results()` 是「读 JSON → 改 → 写回」，
  并发下会互相覆盖。实测 60 线程并发写同一条 trace：**加锁保留 60/60，不加锁只活下来 2/60**。
  修复：按 `hash(trace_id)` 取模的**条带锁**（固定 64 把，不增长、无需清理）。

### 性能

- **SQLite 调优 pragma**：`db/connection.py` 补 `synchronous=NORMAL` + `cache_size=-64000`。
  实测单次 commit 由 **6.98ms → 0.02ms（约 350×）**，整机吞吐（并发 8）由 **31 → 322 req/s（约 10×）**。
  代价：掉电可能丢最后若干条已提交事务（库本身仍保持一致）。

### Bug 修复

- 🔴 **重试被推迟约 8 小时（时区基准错误）**
  `queue_backend.nack()` 用 Python `datetime.now()`（本地时间）写 `next_retry_at`，
  而 `dequeue()` 比较的是 SQLite `datetime('now')`（**UTC**）。
  在 UTC+8 等非 UTC 时区部署下，退避时间比当前 UTC 晚 8 小时 → 重试实际被推迟约 8 小时，
  **等于重试机制失效**。改用 `datetime('now', '+N seconds')`，两侧统一为 UTC。
  `defer()` 同样处理。回归测试：`tests/test_queue_defer.py::test_retry_due_time_uses_utc_not_local`。

- 🔴 **每条消息重复投递 3 次**（自 v1.1.0 / commit `47ac7f9a` 起存在）
  `source_manager.process_message()` 除事件链外，自己又重复 emit 了 `message.parsed`
  与 `message.routed`，导致同一消息被投递 3 次（解析链内各发一次 + 这里再发两次）。
  **定位过程**：线上临时探针显示 `receivers=1`（信号只有一个订阅者）但处理器被调用 3 次
  → 不是重复注册，而是重复 emit。修复：链路改为**只由事件总线单向驱动**，
  `process_message` 只 emit 一次 `message.received`；`extra_fields`（如 `sub_path`）
  改由 `parser_engine` 在触发 `message.parsed` **之前**合并
  （原先它只在冗余 emit 里才生效）。回归测试：`tests/test_event_chain.py`。
- 🟠 **`sent_at` 与 `created_at` 时区基准不一致**
  `dt_now_str()` 用 `datetime.now()`（本地时间）写 `sent_at`，而 `created_at` 由
  `CURRENT_TIMESTAMP` 生成（UTC）——两者相差一个时区偏移，消息列表里「创建/发送时间」
  对不上、按二者算延迟会错好几小时。修复：`dt_now_str()` 改为返回 UTC。

### API

- 🆕 韧性接口（`api/system.py`）：
  - `GET  /api/resilience` — 熔断状态 + 限流配置
  - `POST /api/resilience/rate_limit/<channel_id>` — 设置限流（条/分钟，0 = 不限）
  - `POST /api/resilience/breaker/<channel_id>/reset` — 手工恢复熔断通道
- 🆕 `GET /api/metrics`：队列深度 / 死信总数 / 各通道成功率 / 端到端延迟 /
  熔断与限流状态。定位是「curl 一查就有」，**不引入 Prometheus**；`?hours=N` 调整窗口。
- 🆕 **API 入参校验（#27）**：新增 `api/validation.py`（轻量助手，不引 JSON Schema），
  `ValidationError` 由统一错误处理器转 400。覆盖数据源 / 通道 / 模板 / 设置 / 消息批量的写接口——
  原先缺字段会直接 500，端口、slug、引擎、时段等无效值会被原样写进配置。

### 界面

- 🆕 **熔断 / 限流的 WebUI**（此前只有 API，只能 curl 或改库）：
  - 通道编辑弹窗新增「出站限流（条/分钟）」输入框，留空/0 = 不限流；保存通道时一并写入
  - 通道列表新增「韧性」列：限流显示徽章（如 `60/分`），熔断显示红色倒计时徽章
    （如 `熔断中 21s`）并提供一键「手工恢复」按钮
  - 中英文文案齐备

### 测试

- 新增 96 例（102 → **198 passed**）：
  - 入口并发 3、熔断 13、限流 9、队列延迟重排 4（P0）
  - 渠道级去重/重发 + 多 worker 一致性 14（P1）
  - 事件链与生命周期 9（P2，含「入队恰好 1 次」的重复投递回归测试）
  - 系统级可靠性 13（故障注入 / 熔断×队列 / 重试→死信 / 崩溃恢复 / 并发消费 / 守恒）
  - 优雅停机、`/api/metrics`、入参校验、模块解耦、时间基准、韧性 UI 31（P2 收口）

### 工程（工具链）

- 🆕 `skills/ego_deploy/deploy.py`：测试环境**非破坏同步**
  （按 md5 只写变化文件、**永不删除**、排除 `config/`/`certs/`/`ego.db*`），替代原 `rm -rf` + `cp -r`。
- 🔴 修 `ego_deploy` 的**「重启」空操作**：原用 `ps | grep 目录名` 定位进程，但 busybox 的 `ps`
  命令行**不含工作目录**，永远匹配到空列表 → 不 kill、只新起一个抢不到端口的进程，
  **旧进程继续跑旧代码，脚本却报「已重启成功」**。改用扫描 `/proc/<pid>/cwd` + 核对 cmdline，
  并强制校验 **PID 必须变化**，否则明确报错。
- **优雅停机改用 SIGTERM**：部署脚本先发 SIGTERM 等进程自然退出（超时再 SIGKILL），
  而不是直接 `kill -9` —— 后者会跳过整个优雅停机路径。
- **模块解耦**：`log.py` 不再 `import db`；写库的 `DBLogHandler` 移到
  `db/log_handler.py`，由 `main.py` 注入。依赖方向固定为 `main → db → log`。
- **清理间隔可配**：`EGO_CLEANUP_INTERVAL`（默认 600s），原先硬编码。
- **`requirements.txt` 移除 UTF-8 BOM**（首字节原为 `\ufeff`）。

---

## v1.2.4（2026-07-28）

### 部署配置重构（deploy/）

- T4 证书方案由 certbot 改为 **acme.sh + Cloudflare DNS 验证**（无需 80 端口，适合 80 被占用 / 无公网 80 / 内网穿透）；目录 `t4-certbot/` 更名 `t4-acme/`。
- **证书分离**：EGo 5001 管理页面恒用 Flask 自动生成的自签名证书（`ego_certs` 卷持久化），T1–T4 统一遵循；nginx（T3/T4）独立使用真实证书（T3 手动导入、T4 Let's Encrypt），反代到 EGo 明文 HTTP 5000。两张证书互不影响。
- T3/T4 nginx 主机端口参数化：`${HTTP_PORT:-80}` / `${HTTPS_PORT:-443}`，可用 `.env` 覆盖。
- 一键脚本 `init.sh` 支持**中英双语**；T3/T4 增加端口占用检测（netstat 告警）与现场自定义，并把端口/域名/CF_Token/EGO_SECRET_KEY 写入最终生成的 compose 与 `.env`。
- `deploy/README.md` / `README.en.md` 增补「证书规则」说明与 T4 证书续签运维操作（查日志 / 查有效期 / 强制续期 / 换域名 / 换 Token）。

### 安全修复

- #22a 路径穿越：解析器上传（`api/parsers.py`）、通道插件上传及其 `<filename>` 路由（GET/PUT/DELETE/test/fields，`api/channels.py`）统一对文件名做 `os.path.basename()`，封堵 `../` 逃逸（PUT/DELETE 可写/删任意文件，风险最高）。

### 其它

- `build.py`：版本升至 1.2.4，镜像名改为 `codenametest/everywhereyougo`（与 deploy/ 拉取一致），部署示例改为双端口。
- `doc/roadmap.md`：路径路由（#17）已在 v1.2.2 交付，移入已完成；补充已完成版本列表。

---

## v1.2.3（2026-07-27）

### 部署配置重构

- 统一 `deploy/` 目录结构：`default/`、`t1-host/`、`t2-bridge/`、`t3-nginx/`、`t4-certbot/` 五套环境配置。
- 所有 compose 文件统一使用 `codenametest/everywhereyougo:latest` 镜像，移除 `build:` 块。
- 删除根目录 `docker-compose.yml`，避免与 `deploy/` 下的配置混淆。

### Nginx 配置标准化

- T3/T4 nginx 证书统一为 `ego.crt` / `ego.key`，与 EGo 内置证书文件名一致。
- nginx `proxy_pass` 统一指向 `http://ego:5000`，全流量透传，路径路由由 EGo 内部处理。
- T3 暴露 5001 端口直通管理页面（自签名 HTTPS），内网访问无需经过 nginx。

### 参数一致性修复

- `EGO_SSL_DIR` 环境变量在 `api/__init__.py`、`web_ui.py`、`gen_cert.py` 三处统一生效。
- 优先级：`EGO_SSL_CERT`/`EGO_SSL_KEY` > `EGO_SSL_DIR` > 默认 `./certs/`。

### 反向代理兼容

- `_https_redirect()` 检查 `X-Forwarded-Proto` 头，nginx 代理时不再触发 301 跳转到 5001。
- 修复 nginx 反代管理页面被强转到 HTTPS 5001 的问题。

### T4 证书续期全自动

- certbot 续期成功后通过 `--deploy-hook` 自动复制证书到 `certs/` 目录。
- nginx 容器每 60s 轮询检测证书文件 md5 变化，发现更新自动 `nginx -s reload`。
- 全程无需人工干预，单 compose 跑起来后免维护。

### 文档更新

- `deploy/README.md` 和 `deploy/README.en.md` 同步更新，包含五套环境的完整使用说明。
- 根目录 `README.md` 和 `README.en.md` 环境变量表格新增 `EGO_SSL_DIR`/`EGO_SSL_CERT`/`EGO_SSL_KEY`。
- 配置持久化说明修正：系统设置（DND、日志级别）存储在 SQLite `system_config` 表，非 JSON 文件。

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

## v1.2.1（2026-07-24）

### 路径路由

- 新增 `path_router.py`：数据源支持按 URL 路径分流（`/in/emby/`、`/in/jellyfin/` 等），一个端口接收多个媒体服务器的 Webhook。
- 路径前缀在数据源配置中指定，请求到达时自动匹配对应解析器。

### SDK 文档体系

- 新增 `doc/sdk/` 目录，包含通道（channel）、解析器（parser）、模板（template）三类 SDK 的中英文开发指南。
- 每类文档包含：概念说明、字段定义、示例代码、最佳实践。
- WebUI 文档详情页改为从 Markdown 渲染，支持在线编辑和预览。

### 部署架构文档

- 新增 `doc/architecture.md`：四层部署模型（T1-T4），从单机 host 网络到 Nginx + Let's Encrypt 全自动。
- 新增 `doc/roadmap.md`：改进计划与优先级。

### 其他改进

- Dockerfile 重构：多阶段构建优化，镜像体积减小。
- `i18n.py` 增强：支持更多翻译键和 fallback 逻辑。
- `db/queries.py` / `db/schema.py`：新增查询方法和字段。
- `api/sources.py`：数据源管理 API 增强。
- `templates/sources_page.html`：数据源页面 UI 改进。
- 新增 `tests/test_source_cascade.py`：数据源级联删除测试。

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
