# EGo 部署配置

所有 Docker Compose 配置统一放在本目录。`default/` 是推荐快速起步；`t1`–`t4` 对应 `doc/architecture.md` 部署架构的四个层级。选一套、进入对应目录即可使用。

| 配置 | 目录 | 网络 | 管理页面 | 证书 | 适用场景 |
|------|------|------|----------|------|----------|
| 默认 | `default/` | `bridge` | HTTPS 自签名 | 自动生成 | 新手快速起步 |
| T1 | `t1-host/` | `host` | HTTPS 自签名 | 自动生成 | 家庭 / 内网调试，直接用主机端口 |
| T2 | `t2-bridge/` | `bridge` | HTTPS 自签名 | 自动生成 | 内网多容器协同，容器间用服务名互访 |
| T3 | `t3-nginx/` | `bridge` + Nginx | HTTPS 可信证书 | 手动 | 正式生产，自有证书 |
| T4 | `t4-certbot/` | `bridge` + Nginx + acme.sh | HTTPS 可信证书 | Let's Encrypt 自动 | 有公网域名，全自动免费证书 |

## 为什么管理页面要 HTTPS

浏览器剪贴板 API（页面上各种"复制"按钮依赖的 `navigator.clipboard`）只在**安全上下文**（HTTPS 或 localhost）下可用。所以即便是内网调试（T1/T2/默认），管理页面也走应用内置的自签名 HTTPS——首次访问浏览器会提示证书不受信任，放行一次即可。自签名证书通过 `ego_certs` 卷持久化，重建容器后无需再次放行。

Webhook 接收（`/in/...`）与健康检查（`/api/health`）是机器间流量，始终走 HTTP（`WEB_PORT`）。

## 怎么选

- 内网自己用、要复制按钮能用 → **默认** 或 **T1**（host 网络，端口最省）/**T2**（bridge，需与其它容器互访）。三者都用自签名 HTTPS。
- 要对外、要绿色小锁 → **T3**（已有证书）或 **T4**（有域名，Certbot 自动签）。

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

**一键部署：**

```bash
curl -O https://raw.githubusercontent.com/codename-test/EverywhereYouGo/main/deploy/init.sh
chmod +x init.sh
./init.sh
# 选择选项 4，按提示输入域名和证书路径
```

**手动部署（如需自定义）：**

```bash
cd deploy/t3-nginx
mkdir -p certs
cp /path/to/your/ego.crt certs/ego.crt
cp /path/to/your/ego.key certs/ego.key
docker compose up -d
```

访问 `https://<域名或IP>/`。Nginx 在 443 终结 TLS（你的证书），全流量透传 EGo 的 HTTP 5000；
路径路由由 EGo 内部处理（用户在前端改 path_prefix，nginx 无需改动）。80 自动跳 443。

### T4（Nginx + acme.sh 自动证书）

**证书分离设计：**
- nginx 443: 使用 `certs/ego.crt` + `ego.key`（Let's Encrypt 证书）
- EGo 5001: 使用 `certs/ego-selfsigned.crt` + `ego-selfsigned.key`（自签名证书）

**一键部署：**

```bash
curl -O https://raw.githubusercontent.com/codename-test/EverywhereYouGo/main/deploy/init.sh
chmod +x init.sh
./init.sh
# 选择选项 5，按提示输入域名、邮箱和验证方式
```

支持两种验证方式：
- **webroot 模式**（默认）：需要 80 端口，域名需解析到本机公网 IP
- **Cloudflare DNS API 模式**：不需要 80 端口，需要 Cloudflare API Token

**手动部署（如需自定义）：**

```bash
cd deploy/t4-certbot
mkdir -p certs
echo "your.domain" > certs/.domain

# 方式一：webroot 模式（需要 80 端口）
docker compose up -d
docker compose exec acme acme.sh --issue --webroot /var/www/acme -d your.domain --server letsencrypt

# 方式二：Cloudflare DNS API 模式（不需要 80 端口）
CF_Token=your_token docker compose up -d
docker compose exec acme acme.sh --issue --dns dns_cf -d your.domain --server letsencrypt

# 复制证书到正确位置（Let's Encrypt 证书给 nginx）
docker compose exec acme cp /acme.sh/your.domain/fullchain.cer /certs/ego.crt
docker compose exec acme cp /acme.sh/your.domain/your.domain.key /certs/ego.key
```

之后 `acme.sh` 容器每 12 小时自动尝试续期。续期成功后会自动复制证书到 `certs/` 目录，nginx 容器每 60 秒轮询检测证书变化，发现更新后自动 reload，全程无需人工干预。
