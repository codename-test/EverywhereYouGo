# EGo 路线图

> 敲定的后续开发内容。完成后记入 changelog.md。
> 最后更新：2026-07-28

---

## v1.3.0（下一个大版本）

### 开放 API（规划中）

- 外部系统通过 REST API 直接投递消息（不依赖 Webhook 格式）
- 需配套 API Key 认证机制
- 与路径路由共享入口端口

### 韧性增强

| 项目 | 改进方向 | 来源 |
|------|----------|------|
| 通道熔断 | 连续失败 N 次自动熔断，定期探测恢复，防第三方宕机拖垮线程池 | 改进文档 #19 |
| 出站通道限流 | 令牌桶（条/分钟）按通道独立，防多通道并发被封号（Nginx 管不到出站） | 改进文档 #21 |

---

## 待定（需确认后纳入版本）

| 项目 | 触发条件 | 来源 |
|------|----------|------|
| Prometheus Metrics（`/metrics`） | T3/T4 部署接监控时 | 改进文档 #18 |
| API 限流 | 公网暴露时 | 改进文档 #20 |

---

## 已完成版本

- **v1.2.4**（2026-07-28）— 部署配置重构（T4 改 acme.sh + Cloudflare DNS、证书分离、双语 init.sh、端口检测）+ #22a 上传路径穿越修复，详见 changelog.md
- **v1.2.3**（2026-07-27）— deploy/ 五套环境配置统一、EGO_SSL_DIR 参数一致性、nginx 反代跳转修复，详见 changelog.md
- **v1.2.2**（2026-07-26）— 可靠性加固（线程安全 DB、静默异常日志、备份路径穿越、ZIP 炸弹、CSRF cookie）+ **路径路由（#17，统一入口 `/in/<slug>`）**，详见 changelog.md
- **v1.2.1**（2026-07-24）— Sentinel 代码审核补充，详见 changelog.md
- **v1.2.0**（2026-07-21）— 异步队列 + 安全加固 + 健壮性，详见 changelog.md
- **v1.1.0**（2026-07-18）— 事件总线重构 + 通道插件化 + i18n，详见 changelog.md

---

## 已交付功能存档

### 路径路由（统一入口）— v1.2.2 交付

**URL 结构**：`https://<domain>/<前缀>/<源slug>/<子路径>`

```
POST /in/emby          → 空子路由（兜底）
POST /in/emby/movie    → "movie" 子路由
POST /in/emby/tv       → "tv" 子路由
POST /in/emby/xyz      → 404（未匹配，防垃圾数据）
```

**数据模型**：

```
Source 组：name + slug + enabled
  └── 子路由：path + parser_id + bindings + enabled
```

- Source 组：`sources` 表加 `slug`（文本标识）、`parent_id`（自引用，NULL=组/旧模式）、`path`（子路径）
- 子路由配置与现有端口 Source 一致，只是触发方式从端口变为路径
- Parser 挂子路由级别（能拆散不合并），组级别可加"批量设置解析器"快捷操作
- Bindings（source_channels）挂子路由
- 空子路由（path=""）仅匹配 `/prefix/slug` 本身，做兜底保证消息必转发
- 未匹配子路径 → 404

**运行方式**：

- 路径路由跑在 Flask 主端口（与 WebUI 同端口），handler 只做入队+返回200（毫秒级）
- 与旧端口监听模式天然并行（不同端口，互不干扰）
- Docker 部署只需映射一个端口，Nginx 统一转发

**配置**：

- 全局路径前缀：系统设置中配置（非硬编码，用户自定义）
- `sub_path` 在 `message.received` 阶段注入消息上下文，Parser/Router/Template 均可用
- 路由条件可写 `sub_path == 'movie'`，实现路径级分流

**Nginx 配置示例**：

```nginx
map $host $ego_source {
    emby.example.com   emby;
    sonarr.example.com sonarr;
}
server {
    server_name *.example.com;
    location / {
        proxy_pass http://ego:5000/in/$ego_source;
    }
}
```

来源：改进文档 #17
