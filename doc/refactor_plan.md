# EGo 重构计划 v2.0

> 目标：事件总线驱动架构，模块解耦，可独立修改

## 架构目标

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

## 事件定义

| 事件名 | 触发时机 | data |
|--------|---------|------|
| `message.received` | HTTP 收到原始数据 | `{source_id, raw_body, headers, query_params}` |
| `message.parsed` | 解析完成 | `{trace_id, source_id, msg}` |
| `message.routed` | 路由匹配完成 | `{trace_id, msg, matched_channels}` |
| `message.sending` | 开始发送某个通道 | `{trace_id, channel, rendered}` |
| `message.sent` | 发送完成 | `{trace_id, channel, ok, error}` |
| `message.failed` | 任一环节失败 | `{trace_id, stage, error}` |
| `config.changed` | 配置被修改 | `{table, action, data}` |

## 迁移阶段

### 阶段 0：建事件总线 ✅
- [x] `bus.py` — blinker 信号定义
- [x] 事件注册/触发基础

### 阶段 1：拆分 API ✅
- [x] `web_ui.py`（1217行）→ `api/` 蓝图 + `web_ui.py` 兼容层（70行）
- [x] 文件列表：
  - `api/__init__.py` — Flask app + 蓝图注册
  - `api/auth.py` — 登录/登出
  - `api/sources.py` — 数据源 CRUD + 绑定 + 样本 + 测试
  - `api/parsers.py` — 解析器 CRUD + 内容 + 变量
  - `api/channels.py` — 通道 CRUD + 插件管理
  - `api/templates.py` — 模板 CRUD + 测试渲染
  - `api/messages.py` — 消息查询/操作/批量/清理
  - `api/logs.py` — 日志查询/清理
  - `api/system.py` — 健康检查/语言/设置
  - `api/backup.py` — 导出/备份/恢复/导入
  - `api/pages.py` — HTML 页面渲染

### 阶段 2：拆数据源监听 ⏳
- [ ] `source_manager.py` 的 HTTP 监听部分 → `source_listener/`
- [ ] 收到消息后 `bus.emit("message.received", ...)`
- [ ] 保留旧调用链作为兼容层

### 阶段 3：拆解析引擎 ⏳
- [ ] 解析逻辑 → `parser_engine/`
- [ ] 监听 `message.received`，emit `message.parsed`

### 阶段 4：拆路由引擎 ⏳
- [ ] 路由匹配 → `router_engine/`
- [ ] 监听 `message.parsed`，emit `message.routed`

### 阶段 5：拆发送引擎 ⏳
- [ ] 发送逻辑 → `sender_engine/`
- [ ] 监听 `message.routed`，emit `message.sending/sent`

### 阶段 6：拆分数据库 ⏳
- [ ] `db.py` → `db/` 子模块（schema + models + queries）

### 阶段 7：清理旧代码 ⏳
- [ ] 删除 `web_ui.py`、`source_manager.py` 等旧文件
- [ ] 所有模块统一通过事件总线通信
