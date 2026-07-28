#!/bin/bash
# EGo 一键部署脚本 / EGo One-click Deployment Script
# 用法 / Usage: ./init.sh
#
# 交互式选择部署模式并自动配置，最终在当前目录生成 ego-deploy/，
# 其中 docker-compose.yml / nginx.conf / .env / certs/ 均按你的选择写入。

set -e

BASE_URL="https://raw.githubusercontent.com/codename-test/EverywhereYouGo/main/deploy"

# ── 语言选择 / Language selection ──────────────────────────
echo "请选择语言 / Select language:"
echo "  1) 中文"
echo "  2) English"
printf "选择 / Choice [1-2, 默认/default 1]: "
read LANG_CHOICE
LANG_CHOICE=$(echo "$LANG_CHOICE" | tr -d '[:space:]')
if [ "$LANG_CHOICE" = "2" ]; then L=en; else L=zh; fi

# 双语输出：m "中文" "English"（带换行）；p "中文" "English"（不带换行，用于输入提示）
m() { if [ "$L" = "en" ]; then printf '%s\n' "$2"; else printf '%s\n' "$1"; fi; }
p() { if [ "$L" = "en" ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }

# 检测端口占用（占用返回 0，空闲返回 1），并打印占用进程
port_busy() {
    netstat -tlnp 2>/dev/null | grep -E ":$1 " | head -1 || true
}

echo ""
m "=== EGo 一键部署 ===" "=== EGo One-click Deployment ==="
echo ""
m "请选择部署模式：" "Select deployment mode:"
m "  1) default   — 快速起步（自签名 HTTPS，适合内网测试）"      "  1) default   — Quick start (self-signed HTTPS, LAN testing)"
m "  2) t1-host   — host 网络（直接占用主机端口，适合家庭/内网）"  "  2) t1-host   — host network (direct host ports, home/LAN)"
m "  3) t2-bridge — bridge 网络（容器间互访，适合多容器协同）"     "  3) t2-bridge — bridge network (multi-container LAN)"
m "  4) t3-nginx  — Nginx + 手动证书（生产环境，自有证书）"        "  4) t3-nginx  — Nginx + manual cert (production, own cert)"
m "  5) t4-acme   — Nginx + Let's Encrypt（Cloudflare DNS 全自动）" "  5) t4-acme   — Nginx + Let's Encrypt (Cloudflare DNS, fully auto)"
echo ""
p "请输入选项 [1-5]: " "Enter choice [1-5]: "
read MODE
MODE=$(echo "$MODE" | tr -d '[:space:]')

DEPLOY_DIR=""
DOMAIN=""
CERT_PATH=""
KEY_PATH=""
CF_Token=""
HTTP_PORT=""
HTTPS_PORT=""

case "$MODE" in
    1) DEPLOY_DIR="default" ;;
    2) DEPLOY_DIR="t1-host" ;;
    3) DEPLOY_DIR="t2-bridge" ;;
    4) DEPLOY_DIR="t3-nginx"
       p "请输入域名: " "Enter domain: "; read DOMAIN
       p "请输入证书路径: " "Enter certificate path: "; read CERT_PATH
       p "请输入私钥路径: " "Enter private key path: "; read KEY_PATH
       ;;
    5) DEPLOY_DIR="t4-acme"
       p "请输入域名: " "Enter domain: "; read DOMAIN
       p "请输入 Cloudflare API Token（需 Zone:DNS:Edit 权限）: " "Enter Cloudflare API Token (Zone:DNS:Edit permission): "; read CF_TOKEN
       CF_Token="$CF_TOKEN"
       ;;
    *) m "无效选项" "Invalid choice"; exit 1 ;;
esac

# ── T3/T4：Nginx 端口确认（默认 80/443，含占用检测）──────────
if [ "$DEPLOY_DIR" = "t3-nginx" ] || [ "$DEPLOY_DIR" = "t4-acme" ]; then
    HTTP_PORT=80
    HTTPS_PORT=443
    echo ""
    m "Nginx 默认使用 HTTP=80 / HTTPS=443。" "Nginx defaults to HTTP=80 / HTTPS=443."
    # 占用检测告警
    for prt in 80 443; do
        OCC=$(port_busy "$prt")
        if [ -n "$OCC" ]; then
            m "  ⚠ 端口 $prt 已被占用：$OCC" "  ⚠ Port $prt is already in use: $OCC"
        fi
    done
    p "是否修改这两个端口？[y/N]: " "Change these ports? [y/N]: "
    read CHG
    CHG=$(echo "$CHG" | tr -d '[:space:]')
    if [ "$CHG" = "y" ] || [ "$CHG" = "Y" ]; then
        p "  HTTP 端口 [回车保持 80]: "   "  HTTP port  [Enter to keep 80]: ";  read HP;  [ -n "$HP" ]  && HTTP_PORT="$HP"
        p "  HTTPS 端口 [回车保持 443]: " "  HTTPS port [Enter to keep 443]: "; read HSP; [ -n "$HSP" ] && HTTPS_PORT="$HSP"
    fi
    # 最终端口再次检测占用
    for prt in "$HTTP_PORT" "$HTTPS_PORT"; do
        if [ -n "$(port_busy "$prt")" ]; then
            m "  ⚠ 注意：端口 $prt 当前被占用，启动可能失败，可稍后手动释放。" \
              "  ⚠ Warning: port $prt is currently in use; startup may fail. Free it later if needed."
        fi
    done
fi

echo ""
m "部署模式: $DEPLOY_DIR" "Deployment mode: $DEPLOY_DIR"
echo ""

# ── 创建部署目录 ──────────────────────────────────────────
mkdir -p ego-deploy
cd ego-deploy

# ── 下载配置文件 ──────────────────────────────────────────
m "[1/3] 下载配置文件..." "[1/3] Downloading config files..."
curl -sL "$BASE_URL/$DEPLOY_DIR/docker-compose.yml" -o docker-compose.yml
if [ ! -s docker-compose.yml ]; then
    m "下载失败：docker-compose.yml 为空，请检查网络。" "Download failed: docker-compose.yml is empty. Check your network."
    exit 1
fi
if [ "$DEPLOY_DIR" = "t3-nginx" ] || [ "$DEPLOY_DIR" = "t4-acme" ]; then
    curl -sL "$BASE_URL/$DEPLOY_DIR/nginx.conf" -o nginx.conf
fi

# ── 按用户选择写入最终配置 ────────────────────────────────
m "[2/3] 写入配置..." "[2/3] Writing configuration..."
EGO_SECRET_KEY=$(openssl rand -hex 32)

case "$DEPLOY_DIR" in
    t3-nginx)
        # 域名 → nginx.conf
        sed -i.bak "s/your.domain/$DOMAIN/g" nginx.conf && rm -f nginx.conf.bak
        # 端口 → compose（占位符替换为用户最终值）
        sed -i.bak "s|\${HTTP_PORT:-80}|${HTTP_PORT}|g; s|\${HTTPS_PORT:-443}|${HTTPS_PORT}|g" docker-compose.yml && rm -f docker-compose.yml.bak
        # 自定义 HTTPS 端口时修正跳转目标
        if [ "$HTTPS_PORT" != "443" ]; then
            sed -i.bak "s|https://\$host\$request_uri|https://\$host:${HTTPS_PORT}\$request_uri|" nginx.conf && rm -f nginx.conf.bak
        fi
        # 导入证书
        mkdir -p certs
        cp "$CERT_PATH" certs/ego.crt
        cp "$KEY_PATH" certs/ego.key
        # 会话密钥
        printf 'EGO_SECRET_KEY=%s\n' "$EGO_SECRET_KEY" > .env
        ;;
    t4-acme)
        # 域名 → nginx.conf 与 certs/.domain
        sed -i.bak "s/your.domain/$DOMAIN/g" nginx.conf && rm -f nginx.conf.bak
        mkdir -p certs
        echo "$DOMAIN" > certs/.domain
        # 端口 → compose
        sed -i.bak "s|\${HTTP_PORT:-80}|${HTTP_PORT}|g; s|\${HTTPS_PORT:-443}|${HTTPS_PORT}|g" docker-compose.yml && rm -f docker-compose.yml.bak
        if [ "$HTTPS_PORT" != "443" ]; then
            sed -i.bak "s|https://\$host\$request_uri|https://\$host:${HTTPS_PORT}\$request_uri|" nginx.conf && rm -f nginx.conf.bak
        fi
        # CF_Token + 会话密钥 → .env（持久化，重启后续期仍可用）
        printf 'CF_Token=%s\nEGO_SECRET_KEY=%s\n' "$CF_Token" "$EGO_SECRET_KEY" > .env
        ;;
    *)
        # default / t1 / t2：仅持久化会话密钥
        printf 'EGO_SECRET_KEY=%s\n' "$EGO_SECRET_KEY" > .env
        ;;
esac

# ── 启动 ─────────────────────────────────────────────────
m "[3/3] 启动服务..." "[3/3] Starting services..."
docker compose up -d

echo ""
m "=== 部署完成 ===" "=== Deployment complete ==="
case "$DEPLOY_DIR" in
    default|t1-host|t2-bridge)
        m "管理页面: https://<主机IP>:5001（自签名证书，浏览器需放行）" "Admin UI: https://<host-IP>:5001 (self-signed; accept the browser warning)"
        m "Webhook:  http://<主机IP>:5000/in/"                          "Webhook:  http://<host-IP>:5000/in/"
        ;;
    t3-nginx|t4-acme)
        if [ "$HTTPS_PORT" = "443" ]; then
            m "管理页面: https://$DOMAIN/"            "Admin UI: https://$DOMAIN/"
            m "Webhook:  https://$DOMAIN/in/"         "Webhook:  https://$DOMAIN/in/"
        else
            m "管理页面: https://$DOMAIN:$HTTPS_PORT/" "Admin UI: https://$DOMAIN:$HTTPS_PORT/"
            m "Webhook:  https://$DOMAIN:$HTTPS_PORT/in/" "Webhook:  https://$DOMAIN:$HTTPS_PORT/in/"
        fi
        m "直连管理（自签名）: https://<主机IP>:5001"  "Direct admin (self-signed): https://<host-IP>:5001"
        ;;
esac
echo ""
m "已生成最终配置于 ./ego-deploy/（docker-compose.yml / nginx.conf / .env / certs/），均按上面的选择写入。" \
  "Final config written to ./ego-deploy/ (docker-compose.yml / nginx.conf / .env / certs/), all per your choices above."
m "查看日志: cd ego-deploy && docker compose logs -f" "View logs: cd ego-deploy && docker compose logs -f"
m "停止服务: cd ego-deploy && docker compose down"    "Stop:      cd ego-deploy && docker compose down"
