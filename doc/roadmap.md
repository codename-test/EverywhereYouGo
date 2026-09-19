# EGo 路线图

> 敲定的后续开发内容。完成后记入 changelog.md。
> 最后更新：2026-07-28

---

## 后续版本（规划中）

### 开放 API（规划中）

- 外部系统通过 REST API 直接投递消息（不依赖 Webhook 格式）
- 需配套 API Key 认证机制
- 与路径路由共享入口端口

### 韧性增强 —— ✅ 已在 v1.3.0 交付

| 项目 | 状态 | 来源 |
|------|------|------|
| 通道熔断 | ✅ v1.3.0 `circuit_breaker.py`（三态机 + 滑动窗口 + 指数退避 + 4xx 不计失败 + 持久化） | 改进文档 #19 |
| 出站通道限流 | ✅ v1.3.0 `rate_limiter.py`（每通道令牌桶，拿不到令牌延迟重排） | 改进文档 #21 |

### v1.3.0 追加交付

| 项 | 说明 |
|------|------|
| Channel-Level Retry | 重发只针对上次失败的渠道，`scope=all` 可整体重推 |
| Channel-Level Dedup | 去重粒度下沉到 message × channel（`dedup_keys` 表） |
| Multi-Worker 一致性 | 结果回写条带锁；实测 2/60 → 60/60 不丢更新 |
| 系统级测试 | 故障注入 / 熔断×队列 / 重试→死信 / 崩溃恢复 / 并发消费 / 守恒 |
| 优雅停机 | 停接收 → 等在途任务（超时 30s）→ 未完成刷死信 |
| 可观测性 | `GET /api/metrics`（队列/死信/通道成功率/延迟/熔断） |
| 韧性 WebUI | 通道弹窗限流输入框 + 列表「韧性」列（熔断倒计时徽章 + 一键恢复） |
| API 入参校验（#27） | 新增 `api/validation.py`，非法入参统一 400 |
| 去重逻辑收敛（#31） | 两处发送路径共用 `_plan_dedup()` |
| 日志模块解耦 | `log.py` 不再依赖 `db`；`DBLogHandler` 移到 `db/log_handler.py` |
| 清理间隔可配 | `EGO_CLEANUP_INTERVAL`（默认 600s） |
| 时间基准修正 | `sent_at` 与 `created_at` 统一 UTC |

**注意**：本轮还修了一个长期存在的严重 bug —— 每条消息被**重复投递 3 次**
（自 v1.1.0 起，`process_message` 重复 emit 下游事件）。详见 `changelog.md`。

### 待办（v1.4.0 候选）

- 结构化日志（JSON formatter + 敏感信息脱敏）
- 事件总线超时机制（`bus.emit()` 仍是同步阻塞）
- 类型注解补齐
- 开放 API（REST 投递 + API Key 认证）

---

## 待定（需确认后纳入版本）

| 项目 | 触发条件 | 来源 |
|------|----------|------|
| Prometheus Metrics（`/metrics`） | T3/T4 部署接监控时 | 改进文档 #18 |
| API 限流 | 公网暴露时 | 改进文档 #20 |

---

## 已完成版本

- **v1.3.0**（2026-07-28）— 工程收口：通道熔断(#19) + 出站限流(#21) + 延迟重排 + 端口数据源入口线程池 + SQLite pragma 调优（吞吐 10×）；修复「重试被推迟 8 小时」时区 bug，详见 changelog.md
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
