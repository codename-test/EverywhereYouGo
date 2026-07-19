# EGo 项目设计文档 v1.0.1

> EverywhereYouGo — 通用信息转发平台

## 架构 v2.0（重构中）

```
HTTP POST → Source → Parser → Router → Sender
                 ↑        ↑        ↑       ↑
              ┌──────────────────────────┐
              │    EventBus (blinker)     │  ← 待接入（阶段2-5）
              └──────────────────────────┘
                     ↕
              ┌──────────────────────────┐
              │  api/ (Flask Blueprints)  │
              └──────────────────────────┘
```

### 当前目录结构

```
EverywhereYouGo/
├── main.py              # 入口
├── web_ui.py            # 兼容层（→ api/）25行
├── bus.py               # 事件总线（blinker信号定义）40行
├── api/                 # RESTful API（Flask Blueprints）
│   ├── __init__.py      # Flask app + 蓝图注册 + 认证
│   ├── auth.py          # 登录/登出
│   ├── sources.py       # 数据源 CRUD + 绑定 + 样本
│   ├── parsers.py       # 解析器 CRUD + 内容编辑
│   ├── channels.py      # 通道 CRUD + 插件管理
│   ├── templates.py     # 模板 CRUD + 测试渲染
│   ├── messages.py      # 消息查询/操作/批量
│   ├── logs.py          # 日志查询/清理
│   ├── system.py        # 健康检查/语言/设置
│   ├── backup.py        # 导出/备份/恢复/导入
│   └── pages.py         # HTML 页面渲染
├── source_manager.py    # 数据源监听+消息处理（待拆）
├── parser_loader.py     # 解析器加载器
├── router.py            # 路由匹配
├── renderer.py          # 模板渲染
├── sender.py            # 发送引擎（旧版）
├── channel_loader.py    # 通道插件加载器
├── channels/            # 内置通道实现
├── parsers/             # 解析器脚本
├── config_manager.py    # JSON ↔ SQLite 配置同步
├── db.py                # 数据库层
├── i18n.py              # 中英双语支持
├── log.py               # 日志模块
├── gen_cert.py          # SSL 证书生成
├── certs/               # 证书目录（已 ignore）
├── templates/           # Jinja2 HTML 模板
├── tests/               # 自动化测试
├── doc/                 # 设计文档
│   ├── design.md
│   └── refactor_plan.md
├── Dockerfile
├── docker-compose.yml
├── build.py
├── requirements.txt
├── .dockerignore
├── .gitignore
├── README.md
├── README.en.md
└── deploy_test.py       # 测试部署脚本（容器内用）
```

### 数据流

```
HTTP POST → source_manager.process_message()
  → parser_loader.run_parser()
  → router.match_rules()
  → renderer.render_template()
  → channel.send()
```

### 配置存储

- `config/*.json` — 配置持久化（唯一真相源）
- `ego.db` — SQLite，仅存运行时消息记录

### 认证

- 环境变量 `EGO_AUTH_TOKEN` + `EGO_SECRET_KEY`
- `/login` 页面 + `api/__init__.py` `before_request` 认证中间件
- API 可设 `Authorization: Bearer <token>` 供外部调用

### SSL

- 首次启动自动生成自签名证书（`gen_cert.py` → `certs/`）
- `web_ui.py` `run_simple(ssl_context=...)` 支持 HTTPS

### 重构进度

| 阶段 | 内容 | 状态 |
|------|------|------|
| 0 | 事件总线 `bus.py` | ✅ |
| 1 | `web_ui.py` → `api/` 蓝图拆分 | ✅ |
| 2 | 拆数据源监听 → `source_listener/` | ⏳ |
| 3 | 拆解析引擎 → `parser_engine/` | ⏳ |
| 4 | 拆路由引擎 → `router_engine/` | ⏳ |
| 5 | 拆发送引擎 → `sender_engine/` | ⏳ |
| 6 | 拆数据库 `db.py` → `db/` | ⏳ |
| 7 | 清理旧代码 | ⏳ |

详见 `doc/refactor_plan.md`。

## v1.1 规划

1. 接口路径路由模式
2. 推送通道插件化（已完成）
3. 开放 API 文档
