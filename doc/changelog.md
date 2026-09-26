# EGo 版本记录

> 每个版本的实际改动内容。机器读版本信息见 `version.json`。

---

## v1.3.2（2026-09-26）

> 本版是 v1.3.1 之后的**边界收口**：不新增大型架构，集中修可靠性、上传 / 恢复边界与文档。

### 备份 / 恢复语义修正（本版最重要）

- **恢复不再被"启动加载"语义吞掉**：`/api/restore` 原先调 `config_manager.load_all()`，
  而后者在 DB 非空时以 DB 为准把 JSON 反向刷回 —— "恢复配置"实际是 **no-op**
  （只有插件文件真恢复）。新增 `config_manager.import_from_json()` 做**事务性**「JSON → DB」，
  restore 改调它。
- **恢复改为"部分恢复"**：只把备份里**存在**的配置文件写入数据库，未包含的配置**保持原样**
  （不再被当成"空配置"清空）。并且按 **ZIP 内容**（而非磁盘上有没有文件）决定允许覆盖哪些表 ——
  后者会把上一轮导出残留的旧 `config/*.json` 误当成备份内容导入。
  区分：文件被包含且为 `[]` → 清空该表；文件不在备份里 → 该表不动。
- **清理孤儿绑定**：部分恢复后，指向已消失 source / channel / template 的绑定会被删除
  （否则路由会持续匹配不存在的对象而反复失败）。
- **`created_at` 保真**：导入时还原备份里的创建时间（缺失才回落当前时间）。
- **最终提交做 best-effort 回滚**：替换前备份原文件，中途失败按逆序恢复。

### 恢复 / 上传的校验与边界

- **校验发生在"替换任何文件之前"**：抽出 `_stage_and_validate()`，做文件名安全、JSON 可解析、
  **JSON 结构合法**（原先只在导入时 `log.warning`，形状错要等 `KeyError` 才炸 —— 那时插件文件
  已替换完，会留"插件新版、配置没恢复"的半成品）、插件可加载。
- **dry-run（「预览」）走同一套校验**，只是不落盘；回报 `errors` / `warnings` / `staged`。
  前端在校验不通过时不再显示"确认恢复"按钮。
- **上传体积上限**：全局 `MAX_CONTENT_LENGTH`（默认 32MB，`EGO_MAX_UPLOAD_MB` 可调）+ 413 JSON 响应；
  备份上传另设 `MAX_BACKUP_UPLOAD_SIZE`（20MB，在 `file.read()` **之前**判断）。
- **同名解析器并发上传**：新增 `plugin_paths.filename_lock()`，把「查重 → 落盘 → 入库」串行化
  （原先是 TOCTOU：并发时可能出现"磁盘是 B 的内容、DB 是 A 的行"）。

### 插件 metadata

- 插件可声明 `PARSER_EGO_MIN_VERSION` / `CHANNEL_EGO_MIN_VERSION`（可选），
  `read_source_meta` 读取并在解析器列表接口暴露 `ego_min_version`。
  **注意**：目前仅为**兼容性声明**，加载器不会据此拒绝加载（暂不做依赖解析）。

### 修掉的真 bug

- **`main.py` 的 `web_ui.source_mgr = mgr` 是死代码**：API 侧读的是 `current_app.source_mgr`
  （Flask app 的属性），而 `create_app(source_mgr=None)` 已把它定成 `None`。
  后果：`/api/sources` 的增删改**从不启停监听**（新建带端口的数据源必须重启才生效），
  restore 的监听重启也是空操作。已补 `web_ui.app.source_mgr = mgr`。

### 文档

- `architecture.md` 同步（真相源 = SQLite、flush/retry 共用 `_send_via_channel()`、
  Blueprint / 通道 / 测试计数、日期）。
- README 中英：备份章节补 `channels/*.py`、通道表补 SMTP、恢复语义说明、
  **升级前请先备份**的提示。
- 部署文档（`deploy/README.md` / `.en.md`）新增「升级与备份」章节：卷清单、升级命令、
  以及 `down -v` 会连数据库与配置一起删除的警告。
- `doc/sdk/{parser,channel}.{zh,en}.md` 新增「插件元数据」章节。

---

## v1.3.1（已发布）

> 本版聚焦「插件持久化」：用户上传的插件在容器重建后不再丢失。

### 插件目录拆分（破坏性变更，部署需同步改）

内置插件与用户插件分目录：

| 目录 | 内容 | 卷 |
|------|------|----|
| `parsers_builtin/` | 内置解析器（随镜像发布） | 无 |
| `parsers/` | 用户上传的解析器 | `ego_parsers` |
| `channels_builtin/` | 内置通道插件（随镜像发布） | 无 |
| `channels/` | 用户上传的通道插件 | `ego_channels` |

- **根因**：旧版本用户上传的插件与内置插件同目录，且该目录没有任何持久化 → 容器重建即丢。
- **为什么不能只给原目录加卷**：named volume 首次创建会把镜像里该目录的内容拷进卷，
  之后以卷为准 → 内置插件永远升不上去。所以必须分目录。
- 新增 `plugin_paths.py` 作为唯一目录解析入口，收敛原先散在 **9 处**的硬编码路径
  （`parser_loader` / `channel_loader` / `source_manager` / `parser_engine` / `api/*`）。
- `BaseChannel` 从 `channels/__init__.py` 移到 `channel_base.py` ——
  `channels/` 现在是用户卷，基础设施不能放在会被卷遮蔽的位置。
- **同名规则**：与内置同名 → 上传直接拒绝（内置随镜像更新，同名文件不会生效）。
- **内置只读**：WebUI 中可查看，不可编辑/删除（新增 `err.plugin_builtin_readonly` 文案）。
- `Dockerfile` 的 `VOLUME` 与 **5 套** compose 配置同步新增两个用户插件卷。

### 升级提示红字告警

- `version.json` 新增 `upgrade_warning` 字段（`{zh, en}`），升级弹层**顶部**以红字告警块渲染。
  1.3.1 的内容为：「**请先行备份所有配置**：本次升级包含插件目录拆分（破坏性变更）……」。
- `version_checker` 透传该字段；前端按当前语言取文案，字段缺失时静默不显示（向后兼容）。

### 备份 / 恢复

- 备份改为打包**用户**的 `parsers/` 与 `channels/`（内置随镜像发布，打进备份反而会在
  恢复时用旧副本遮蔽新版内置插件）。
- 恢复写入用户目录，并**跳过与内置同名的条目**（响应里用 `skipped_builtin` 列出跳过了谁）。
- 顺带修掉一个漏项：**备份原先完全不含 `channels/`**，用户上传的通道插件从来没被备份过。

### 其它

- `_calc_parser_hash` 原先在两个文件里各写一份、各自拼 `parsers/` 路径，
  收敛到 `parser_loader.calc_parser_hash()`（走 plugin_paths，内置/用户都能定位）。
- 删除 `api/sources.py` 中从未被使用的死常量 `PARSERS_DIR`。
- `list_plugins()` 增加 `source` 标注（`builtin` / `user`），供前端区分来源。

### 新增通道：SMTP 邮件

- 🆕 `channels_builtin/smtp_email.py`。字段：服务器 / 端口 / 登录账号 /
  **密码（标签写作「密码 / 授权码」）** / 发件人 / 收件人 / 抄送 / 加密方式。
- ⚠️ **国内邮箱要的是授权码，不是登录密码** —— 这一点做了三处体现，缺一不可：
  1. 字段标签写「密码 / 授权码」，不写「密码」
  2. desc 说明要先去邮箱设置开启 SMTP 服务并生成授权码（Gmail 叫「应用专用密码」）
  3. **认证失败（535 等）的错误信息直接把用户引向授权码**，而不是让人反复试密码
- 加密方式按端口推断（`encryption=auto`）：**25→明文、587→STARTTLS、465/994→隐式 TLS**，
  可用 `encryption` 显式覆盖为 `ssl` / `starttls` / `none`。
  依据：IANA 注册 25=`smtp`(RFC5321)、587=`submission`(RFC4409, STARTTLS)、
  465=`submissions`(RFC8314, 隐式 TLS)；**994 无 IANA 注册**，是网易自定的 SMTP SSL 端口。
- 正文同时生成纯文本与 HTML（HTML 由 markdown 渲染），发件人留空则用登录账号（多数邮箱
  要求二者一致）。
- `test()` 改为可返回 `(ok, error)`，`channel_loader.test_channel()` 与
  `/api/channels/<id>/test` 都会把真实原因带给用户（原先只会显示一句"测试失败"）。

### 新增解析器：通用 JSON / 表单 / 文本

三个**内置**通用解析器，覆盖"不想为每种来源写解析器"的场景：

| 文件 | 能力 |
|------|------|
| `generic_json.py` | 任意 JSON，嵌套字段展平为**点号路径**（`item.name`）；数组元素全为标量时合成字符串 |
| `generic_form.py` | `x-www-form-urlencoded` 与 `multipart/form-data`；文件字段给 `.filename` / `.size`，不读内容 |
| `generic_text.py` | 纯文本：首行作标题，正文进 `content`，`KEY=VALUE` / `KEY: VALUE` 行提取为变量 |

设计取舍：
- **字段一律是标量**（数组合成字符串、同名字段合并）—— 因为路由条件只认标量，
  留成列表会让条件取不到值。
- **键统一小写**，与既有解析器（`emby.py` 产出 `event`/`name`）保持一致。
- 数值保留原类型（`size` 仍是 int），便于条件做数值比较。
- 有上限（JSON 深度 6 / 字段 300 / 值 2000 字符），防畸形 payload 拖垮渲染。

配套：`db.sync_builtin_parsers()` 在启动时把内置解析器**幂等登记**进 `parsers` 表 ——
否则新版本带来的内置解析器在 WebUI 里选不到。名字取源码里的 `PARSER_NAME`。

### 韧性链路收口

- 🔴 **统一发送入口**：worker（异步入队）与 flush/retry（直接发送）原先各走各的 ——
  `_do_send_direct` **既不判熔断、不记录熔断结果、也不限流**，等于整层韧性保护对
  flush / retry 失效。现抽出 `_send_via_channel()` 两条路径共用。
  被拦下的通道不计失败、不改写消息终态，留给后续重试。
- **HALF_OPEN 只放一个探测**：原先 HALF_OPEN 期间对所有请求放行，一次恢复可能瞬间
  给刚出问题的第三方打出一批请求。现在用闸门限制为单探测，探测返回即释放。
- **defer 超限语义修正**：超过 `max_defers` 时**直接进死信**并写明
  "deferred N times without ever being sent"，**不消耗重试次数** ——
  原先走 `nack()`，把"一直没轮到发"伪装成"发送失败"，排查时会被误导。
- **ingress 停机排空**：`_IngressPool.shutdown()` 现在会等在途请求收尾（超时如实报告），
  原先只置停止标志就返回，在途请求被直接掐断。

### 插件元信息与缺失状态

- 插件可声明 `PARSER_VERSION` / `CHANNEL_VERSION`（内置插件均已补 1.0；`emby.py` 为 1.1），
  解析器还会读 `PARSER_NAME` / `PARSER_DESC`（正则读取，不执行代码）。
- 列表接口新增 `source`（`builtin` / `user` / `missing`）与 `version`；
  设置页插件表新增「来源」「版本」两列。
- **插件缺失状态**：通道实例引用的插件文件若已不存在，`/api/channels` 返回
  `plugin_missing: true`，通道列表显示红色「插件缺失」标记；加载失败的错误信息也改为可行动
  （提示插件可能已被删除、需重新上传或换类型）。

### 测试

- `tests/test_plugin_dirs.py`：解析顺序、路径穿越、同名拒绝、内置只读、
  备份含用户插件且不含内置、恢复跳过同名内置、**升级保留**、**升级兼容性**、元信息与缺失状态
- `tests/test_smtp_email_channel.py`（16 例）：加密推断、地址分隔符解析、校验、
  MIME 构造，以及**对着 SMTP 桩服务器真发一封**（含认证失败要提示授权码）
- `tests/test_generic_parsers.py`（26 例）：三个解析器的展平/编码/截断/类型保真
- 另有统一发送入口、HALF_OPEN 单探测、ingress 排空等用例
- 213 → **296 passed**

> 版本号暂未 bump：v1.3.1 还包含「SMTP 邮件通道」与「Generic JSON / Form / Text 解析器」，
> 待一并完成后再发版。

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
