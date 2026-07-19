# EGo 项目设计文档 v1.1.0

> EverywhereYouGo — 通用信息转发平台

## 架构

```
HTTP POST → Source → Parser → Route → Render → Send
                ↓        ↓       ↓       ↓        ↓
              ┌─────────────────────────────────────┐
              │            db / config_manager       │
              └─────────────────────────────────────┘
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

### 渠道 (Channel)
- 内置：企微 Bot/API、钉钉、飞书、Telegram、Bark
- SDK 插件：用户自定义 Python 通道插件

## 配置存储

- `config/*.json` — 配置持久化（唯一真相源）
- SQLite — 仅存运行时消息记录
- 启动时 JSON → SQLite，UI 编辑即时同步回 JSON

## 认证

- 环境变量 `EGO_AUTH_TOKEN` + `EGO_SECRET_KEY`
- 登录页 + Bearer Token API 认证

## SSL

- 首次启动自动生成自签名证书 (`gen_cert.py` → `certs/`)

## 版本更新检测

- 后台线程每 24 小时检查 GitHub `version.json`
- 有新版本时侧边栏显示绿点提示
- API：`GET/POST /api/version/check`

## 备份恢复

- **备份**：下载 ZIP（`config/*.json` + `parsers/*.py`）
- **恢复**：上传 ZIP，覆盖配置后即时生效

## 国际化

- 中英双语支持（`i18n.py`），758 条翻译
- 页面顶部语言切换

## 目录结构

```
EverywhereYouGo/
├── main.py              # 入口
├── web_ui.py            # Flask + SSL 启动
├── bus.py               # 事件总线
├── api/                 # RESTful API（11 个蓝图文件）
├── db/                  # 数据库层
├── channels/            # 内置通道实现
├── parsers/             # 解析器脚本
├── config_manager.py    # JSON ↔ SQLite 同步
├── i18n.py              # 中英双语
├── version_checker.py   # 版本检测
├── templates/           # HTML 模板
├── tests/               # 自动化测试
├── doc/                 # 设计文档
├── Dockerfile / docker-compose.yml / build.py
└── README.md / README.en.md
```

## 下一阶段规划

### 接口路径路由模式
- 单端口多路径入口：`/webhook/emby`、`/webhook/dingtalk`
- 兼容保留多端口监听能力

### 开放 API 接口
- 完整 RESTful API
- OpenAPI / Swagger 文档
- 覆盖数据源/通道/模板/消息/日志全量 CRUD
