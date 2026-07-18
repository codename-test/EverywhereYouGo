# EGo 项目设计文档

> EverywhereYouGo — 通用信息转发平台
> v1.0.1

## 架构

```
HTTP POST → 数据源 (Source) → 解析器 (Parser) → 路由 (Router) → 模板 (Template) → 渠道 (Channel)
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
- `channels/*.py`，实现 `send(title, content) -> (ok, error)`

## 配置存储

- `config/*.json`：配置持久化（唯一真相源）
- `ego.db`：SQLite，仅存运行时消息记录
- 启动时 JSON → SQLite，UI 编辑即时同步回 JSON
- 支持外部修改检测（文件 mtime）

## 认证

- 环境变量 `EGO_AUTH_TOKEN`
- API 请求需 `Authorization: Bearer <token>`
- 未设置则不开启认证

## 下一阶段规划 (v1.1)

### 1. 接口路径路由模式
- 数据源新增「URL 路径」识别方式
- 单端口多路径入口：`/webhook/emby`、`/webhook/dingtalk`
- 兼容保留多端口监听能力

### 2. 推送通道插件化
- 统一通道接口规范
- 用户自定义 Python 插件，`send(title, content) -> (ok, error)`
- 插件热加载，自动发现 `channels/` 目录
- 保留现有内置通道

### 3. 开放 API 接口
- 完整 RESTful API
- OpenAPI / Swagger 文档
- 覆盖数据源/通道/模板/消息/日志全量 CRUD
