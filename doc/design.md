# EGo 项目设计文档

> EverywhereYouGo — 通用信息转发平台
> v1.1

## 架构（当前）

```
HTTP POST → 数据源 (Source) → 解析器 (Parser) → 路由 (Router) → 模板 (Template) → 通道 (Channel)
```

## 组件说明

### 数据源 (Source)
- 监听端口接收 HTTP POST
- 支持多端口多数据源

### 解析器 (Parser)
- `parsers/*.py`，定义 `parse()` 函数
- 返回 dict：`title` + `content` + 自定义变量
- 变量同时用于路由条件和模板引用

### 路由 (Router)
- 条件表达式：`event == 'library.new' and media_type == 'Movie'`
- 支持 `and`/`or`/括号分组

### 模板 (Template)
- Simple 模式：`{varName}`
- Jinja2 模式：`{{ msg.varName }}`

### 通道 (Channel)
- 内置：企微 Bot/API、钉钉、飞书、Telegram、Bark
- 插件化：用户自定义 Python 插件，`send(title, content) -> (ok, error)`
- 插件热加载，自动发现 `channels/` 目录
- `channels/*.py`，统一接口规范

## 配置存储

- `config/*.json`：配置持久化（唯一真相源）
- `ego.db`：SQLite，仅存运行时消息记录
- 启动时 JSON → SQLite，UI 编辑即时同步回 JSON
- 支持外部修改检测（文件 mtime）

## 认证

- 环境变量 `EGO_AUTH_TOKEN`
- API 请求需 `Authorization: Bearer <token>`
- 未设置则不开启认证

---

## 当前开发重点：事件总线驱动重构

> 目标：模块解耦，各引擎可独立修改和测试

### 架构目标

```
                    ┌──────────────────────────┐
                    │       EventBus (blinker)  │
                    └──────────────────────────┘
                     ↕        ↕        ↕       ↕
source_listener  parser_engine  router_engine  sender_engine
                     ↕        ↕        ↕       ↕
                    ┌──────────────────────────┐
                    │   db / config_manager     │
                    └──────────────────────────┘
                     ↕
                    ┌──────────────────────────┐
                    │   api/ (Flask Blueprints) │
                    └──────────────────────────┘
```

### 事件定义

| 事件名 | 触发时机 | data |
|--------|---------|------|
| `message.received` | HTTP 收到原始数据 | `{source_id, raw_body, headers, query_params}` |
| `message.parsed` | 解析完成 | `{trace_id, source_id, msg}` |
| `message.routed` | 路由匹配完成 | `{trace_id, msg, matched_channels}` |
| `message.sending` | 开始发送某个通道 | `{trace_id, channel, rendered}` |
| `message.sent` | 发送完成 | `{trace_id, channel, ok, error}` |
| `message.failed` | 任一环节失败 | `{trace_id, stage, error}` |
| `config.changed` | 配置被修改 | `{table, action, data}` |

### 迁移阶段

#### 阶段 0：建事件总线 ✅
- [x] `bus.py` — blinker 信号定义
- [x] 事件注册/触发基础

#### 阶段 1：拆分 API ✅
- [x] `web_ui.py` → `api/` 蓝图 + `pages.py`
- [x] SSL 启动逻辑移入 `main.py`
- [ ] 文件列表：
  - `api/__init__.py` — Flask app + 蓝图注册
  - `api/auth.py` — 登录/登出/认证中间件
  - `api/sources.py` — 数据源 CRUD + 绑定 + 样本 + 测试
  - `api/parsers.py` — 解析器 CRUD + 内容 + 变量
  - `api/channels.py` — 通道 CRUD + 插件管理
  - `api/templates.py` — 模板 CRUD + 测试渲染
  - `api/messages.py` — 消息查询/操作/批量/清理
  - `api/logs.py` — 日志查询/清理
  - `api/system.py` — 健康检查/语言/设置
  - `api/backup.py` — 导出/备份/恢复/导入
  - `pages.py` — HTML 页面渲染

#### 阶段 2：拆数据源监听 ✅
- [x] `source_manager.py` 的 HTTP 监听部分 → `source_listener/`
- [x] `_HookHandler`、`ListenerManager`（原 `SourceManager`）、样本管理
- [x] 收到消息后 `bus.emit("message.received", ...)` 触发事件链
- [x] `source_manager.py` 保留向后兼容：重导出 `SourceManager`、`get_samples`、`clear_samples`

#### 阶段 3：拆解析引擎 ✅
- [x] 解析逻辑 → `parser_engine/`
- [x] 监听 `message.received`，执行解析器，emit `message.parsed`
- [x] `bus.emit()` 返回 handler 结果列表，实现同步事件链传值
- [x] 解析失败时触发 `message.failed` 事件

#### 阶段 4：拆路由引擎 ✅
- [x] 路由匹配 → `router_engine/`
- [x] 监听 `message.parsed`，执行路由匹配 + DND 检测，emit `message.routed`
- [x] 提供 `match_for_source()` 直接调用接口，供队列刷新和重发使用

#### 阶段 5：拆发送引擎 ✅
- [x] 发送逻辑 → `sender_engine/`
- [x] 监听 `message.routed`，执行去重 → 渲染 → 并行发送 → 记录结果
- [x] 提供 `send_to_channels()` 直接调用接口，供队列刷新和重发使用

#### 阶段 6：拆分数据库 ✅
- [x] `db.py` → `db/` 包（`connection.py` + `schema.py` + `queries.py`）
- [x] `__init__.py` 统一导出，现有代码 `import db` 无需修改

#### 阶段 7：清理旧代码 ⏳
- [ ] 删除 `web_ui.py`（兼容层，当前仍作为入口转发到 `api/`）
- [ ] 删除 `router.py`（已迁移到 `router_engine/`，但底层 match_rules 仍被引用）
- [ ] 所有模块统一通过事件总线通信（已完成 ✅）

### 版本更新检测 (Version Checker)

> 自动检测 GitHub 仓库新版本，侧边栏提示用户更新

**模块文件：**
- `version_checker.py` — 版本检测逻辑、缓存、后台线程
- `version.json` — 版本信息文件，托管在 GitHub 仓库供远程查询

**检测机制：**
- 启动后延迟 5 秒执行首次检测，之后每 24 小时检测一次
- 通过 `urllib.request` 请求 GitHub raw URL 获取 `version.json`
- 语义化版本比较（`_compare_versions()`），判断是否有新版本
- 检测结果缓存在内存 `_cache` 字典中，避免重复请求
- 网络错误不中断程序，仅记录错误信息

**缓存字段：**
- `latest_version` — 最新版本号
- `release_date` — 发布日期
- `changelog` — 更新说明列表
- `url` — 发布页面链接
- `has_update` — 是否有新版本（bool）
- `checked_at` — 最后检测时间
- `error` — 错误信息（如有）

**API 接口：**
- `GET /api/version/check` — 返回缓存的版本信息
- `POST /api/version/check` — 触发立即检测

**UI 展示：**
- 侧边栏底部版本号旁显示绿色圆点（`update-dot`）提示有新版本
- 点击圆点弹出浮层显示版本号、changelog 列表和发布页链接
- 点击浮层外部自动关闭

**配置项：**
- `GITHUB_VERSION_URL` — GitHub raw 地址，默认占位符需替换为实际仓库地址
- `CHECK_INTERVAL` — 检测间隔，默认 86400 秒（24 小时）

---

## 存档：后续规划

以下功能计划在架构重构完成后推进。

### 接口路径路由模式
- 数据源新增「URL 路径」识别方式
- 单端口多路径入口：`/webhook/emby`、`/webhook/dingtalk`
- 兼容保留多端口监听能力

### 开放 API 接口
- 完整 RESTful API
- OpenAPI / Swagger 文档
- 覆盖数据源/通道/模板/消息/日志全量 CRUD
