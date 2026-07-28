# EGo 部署配置

[English](README.en.md) | 中文

> 返回项目主页：[README.md](../README.md)

所有 Docker Compose 配置统一放在本目录。`default/` 是推荐快速起步；`t1`–`t4` 对应 `doc/architecture.md` 部署架构的四个层级。选一套、进入对应目录即可使用。

| 配置 | 目录 | 网络 | 管理页面 | 证书 | 适用场景 |
|------|------|------|----------|------|----------|
| 默认 | `default/` | `bridge` | HTTPS 自签名 | 自动生成 | 新手快速起步 |
| T1 | `t1-host/` | `host` | HTTPS 自签名 | 自动生成 | 家庭 / 内网调试，直接用主机端口 |
| T2 | `t2-bridge/` | `bridge` | HTTPS 自签名 | 自动生成 | 内网多容器协同，容器间用服务名互访 |
| T3 | `t3-nginx/` | `bridge` + Nginx | HTTPS 可信证书 | 手动 | 正式生产，自有证书 |
| T4 | `t4-acme/` | `bridge` + Nginx + acme.sh | HTTPS 可信证书 | Let's Encrypt 自动（Cloudflare DNS） | 有公网域名，全自动免费证书 |

## 为什么管理页面要 HTTPS

浏览器剪贴板 API（页面上各种"复制"按钮依赖的 `navigator.clipboard`）只在**安全上下文**（HTTPS 或 localhost）下可用。所以即便是内网调试（T1/T2/默认），管理页面也走应用内置的自签名 HTTPS——首次访问浏览器会提示证书不受信任，放行一次即可。自签名证书通过 `ego_certs` 卷持久化，重建容器后无需再次放行。

Webhook 接收（`/in/...`）与健康检查（`/api/health`）是机器间流量，始终走 HTTP（`WEB_PORT`）。

## 证书规则（所有层级统一）

- **EGo 5001 管理页面**：始终使用 Flask 自动生成的**自签名证书**（持久化在 `ego_certs` 卷）。T1/T2/T3/T4 都遵循这一规则；浏览器直连 `https://<IP>:5001` 需放行一次证书警告。
- **Nginx（仅 T3/T4）**：在 80/443 用**真实证书**终结 TLS，再反代到 EGo 的明文 HTTP 5000。T3 用你手动导入的证书，T4 用 acme.sh 自动签发/续期的 Let's Encrypt 证书。
- 这两张证书**相互独立**：EGo 的自签证书在 `ego_certs` 卷，nginx 的证书在本目录 `certs/`。对外访问走 `https://<域名>/`（nginx，可信证书）；内网也可直连 `https://<IP>:5001`（自签）。

## 怎么选

- 内网自己用、要复制按钮能用 → **默认** 或 **T1**（host 网络，端口最省）/**T2**（bridge，需与其它容器互访）。三者都用自签名 HTTPS。
- 要对外、要绿色小锁 → **T3**（已有证书）或 **T4**（有域名 + Cloudflare，acme.sh DNS 自动签）。

## 端口与开关

- `WEB_PORT`（默认 5000）：HTTP，Webhook 接收（`/in/...`）与 `/api/health`。
- `WEB_SSL_PORT`（默认 5001）：HTTPS，管理页面（SSL 启用时监听）。
- `EGO_SSL_ENABLED`（默认 1）：设为 `0` 可**完全关闭**应用自身 HTTPS——跳过证书生成、只监听 HTTP、不跳转、会话 Cookie 不加 `Secure`。仅当你明确要纯 HTTP（或 TLS 由前置反代终结且不想让应用再加密）时使用；本目录各配置默认都不关。

## 各配置用法

### 默认 / T1 / T2（自签名 HTTPS）

```bash
cd deploy/default        # 或 t1-host / t2-bridge
docker compose up -d
```

- 管理页面：`https://<主机IP>:5001`（放行自签名证书警告）
- Webhook / 健康检查：`http://<主机IP>:5000`

> T1 为 host 网络：若主机 5000/5001 被占用（例如 iStoreOS 路由器的 `miniupnpd` 占 5000），把 compose 里 `WEB_PORT`/`WEB_SSL_PORT` 改成空闲端口（如 5080/5081）。

### T3（Nginx + 手动证书）

**一键部署（脚本支持中/英双语，会检测端口占用并允许修改）：**

```bash
curl -O https://raw.githubusercontent.com/codename-test/EverywhereYouGo/main/deploy/init.sh
chmod +x init.sh
./init.sh
# 选择语言 → 选项 4 → 按提示输入域名、证书路径；默认 HTTP=80/HTTPS=443，可现场改
```

**手动部署（如需自定义）：**

```bash
cd deploy/t3-nginx
mkdir -p certs
cp /path/to/your/ego.crt certs/ego.crt
cp /path/to/your/ego.key certs/ego.key
# 可选：自定义 nginx 端口（默认 80/443）
# echo "HTTP_PORT=8080" >> .env; echo "HTTPS_PORT=4430" >> .env
docker compose up -d
```

访问 `https://<域名或IP>/`。Nginx 在 443 用你导入的证书终结 TLS，全流量透传 EGo 的 HTTP 5000；
路径路由由 EGo 内部处理（用户在前端改 path_prefix，nginx 无需改动）。HTTP 80 自动跳 HTTPS。
EGo 5001 仍是自签名证书（独立），可内网直连 `https://<IP>:5001`。

> 自定义 HTTPS 端口（如 4430）时，手动部署需同时把 `nginx.conf` 里的跳转改成 `https://$host:4430$request_uri`；一键脚本会自动处理。

### T4（Nginx + acme.sh 自动证书，Cloudflare DNS）

**证书分离设计：** EGo 5001 用 Flask 自签名证书（`ego_certs` 卷，独立）；nginx 443 用 acme.sh 写入 `certs/` 的 Let's Encrypt 证书。nginx 终结 TLS 后反代到 EGo 的 HTTP 5000。两张证书互不影响。

**验证方式：Cloudflare DNS API**（不需要 80 端口，适合 80 被占用 / 无公网 80 / 内网穿透）。前提：
- 域名托管在 Cloudflare；
- 一个 Cloudflare API Token（`Zone:DNS:Edit` 权限）。

**一键部署（脚本支持中/英双语，会检测端口占用并允许修改）：**

```bash
curl -O https://raw.githubusercontent.com/codename-test/EverywhereYouGo/main/deploy/init.sh
chmod +x init.sh
./init.sh
# 选择语言 → 选项 5 → 按提示输入域名、Cloudflare API Token；默认 HTTP=80/HTTPS=443，可现场改
```

**手动部署（如需自定义）：**

```bash
cd deploy/t4-acme
mkdir -p certs
echo "your.domain" > certs/.domain            # 改成你的域名
sed -i 's/your.domain/你的域名/g' nginx.conf
# 把 CF_Token 写进 .env（容器重启 / 重启机器后续期仍可用）
echo "CF_Token=你的Cloudflare_Token" > .env
# 可选：自定义 nginx 端口（默认 80/443）
# echo "HTTP_PORT=8080" >> .env; echo "HTTPS_PORT=4430" >> .env
docker compose up -d
```

启动后 `acme` 容器通过 Cloudflare DNS 验证签发证书（含泛域名 `*.你的域名`）写入 `certs/`，nginx 检测到证书变化自动 reload；之后每 12 小时自动续期，全程无需人工干预。首次启动 nginx 会重启几次，直到 acme 签出证书，属正常现象。

> 自定义 HTTPS 端口（如 4430）时，手动部署需同时把 `nginx.conf` 里的跳转改成 `https://$host:4430$request_uri`；一键脚本会自动处理。

#### T4 证书续签运维

- **自动续期**：`acme` 容器每 12 小时检查一次，证书临近到期（Let's Encrypt 有效期 90 天）时自动经 Cloudflare DNS 续期，写入 `certs/` 后 nginx 60 秒内自动 reload。无需人工干预。
- **查看续期日志**：`docker compose logs -f acme`
- **查看当前证书有效期**：`openssl x509 -in certs/ego.crt -noout -dates`
- **手动强制续期**：`docker compose exec acme acme.sh --renew -d 你的域名 --force`
- **更换域名**：修改 `certs/.domain` 与 `nginx.conf` 里的域名，然后 `docker compose restart acme nginx`。
- **更换 Cloudflare Token**：修改 `.env` 里的 `CF_Token`，然后 `docker compose up -d`（会重建 acme 容器）。
- **排查续期失败**：先看 `docker compose logs acme`；常见原因是 CF_Token 失效/权限不足，或域名未托管在 Cloudflare。
