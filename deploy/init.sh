#!/bin/bash
# EGo 一键部署脚本
# 用法: ./init.sh
# 交互式选择部署模式并自动配置

set -e

BASE_URL="https://raw.githubusercontent.com/codename-test/EverywhereYouGo/main/deploy"

echo "=== EGo 一键部署 ==="
echo ""
echo "请选择部署模式："
echo "  1) default  — 快速起步（自签名 HTTPS，适合内网测试）"
echo "  2) t1-host  — host 网络（直接占用主机端口，适合家庭/内网）"
echo "  3) t2-bridge — bridge 网络（容器间互访，适合多容器协同）"
echo "  4) t3-nginx  — Nginx + 手动证书（生产环境，自有证书）"
echo "  5) t4-certbot — Nginx + Let's Encrypt（全自动证书，需公网域名）"
echo ""
printf "请输入选项 [1-5]: "
read MODE
MODE=$(echo "$MODE" | tr -d '[:space:]')

case "$MODE" in
    1)
        DEPLOY_DIR="default"
        ;;
    2)
        DEPLOY_DIR="t1-host"
        ;;
    3)
        DEPLOY_DIR="t2-bridge"
        ;;
    4)
        DEPLOY_DIR="t3-nginx"
        printf "请输入域名: "
        read DOMAIN
        printf "请输入证书路径: "
        read CERT_PATH
        printf "请输入私钥路径: "
        read KEY_PATH
        ;;
    5)
        DEPLOY_DIR="t4-certbot"
        printf "请输入域名: "
        read DOMAIN
        printf "请输入邮箱: "
        read EMAIL
        echo ""
        echo "证书验证方式："
        echo "  1) webroot 模式（默认，需要 80 端口，域名需解析到本机公网 IP）"
        echo "  2) Cloudflare DNS API 模式（不需要 80 端口，需要 CF_Token）"
        printf "请选择 [1-2，默认 1]: "
        read CERT_MODE
        CERT_MODE=$(echo "$CERT_MODE" | tr -d '[:space:]')
        if [ "$CERT_MODE" = "2" ]; then
            printf "请输入 Cloudflare API Token: "
            read CF_TOKEN
            export CF_Token="$CF_TOKEN"
        fi
        ;;
    *)
        echo "无效选项"
        exit 1
        ;;
esac

echo ""
echo "部署模式: $DEPLOY_DIR"
echo ""

# 创建部署目录
mkdir -p ego-deploy
cd ego-deploy

# 下载 docker-compose.yml
echo "[1/3] 下载配置文件..."
curl -sL "$BASE_URL/$DEPLOY_DIR/docker-compose.yml" -o docker-compose.yml

# 下载 nginx.conf（如果有）
if [ "$DEPLOY_DIR" = "t3-nginx" ] || [ "$DEPLOY_DIR" = "t4-certbot" ]; then
    curl -sL "$BASE_URL/$DEPLOY_DIR/nginx.conf" -o nginx.conf
fi

# 配置
echo "[2/3] 配置..."
case $DEPLOY_DIR in
    t3-nginx)
        sed -i.bak "s/your.domain/$DOMAIN/g" nginx.conf
        rm nginx.conf.bak
        mkdir -p certs
        cp "$CERT_PATH" certs/ego.crt
        cp "$KEY_PATH" certs/ego.key
        ;;
    t4-certbot)
        sed -i.bak "s/your.domain/$DOMAIN/g" nginx.conf
        rm nginx.conf.bak
        mkdir -p certs
        echo "$DOMAIN" > certs/.domain
        
        # 安装 acme.sh 到临时目录
        echo "安装 acme.sh..."
        curl -sL https://get.acme.sh | sh -s email=$EMAIL
        ACME_SH="$HOME/.acme.sh/acme.sh"
        
        if [ -n "$CF_Token" ]; then
            echo "使用 Cloudflare DNS API 模式签发证书（含泛域名）..."
            export CF_Token
            $ACME_SH --issue --dns dns_cf -d "$DOMAIN" -d "*.$DOMAIN" --server letsencrypt
        else
            echo "使用 webroot 模式签发证书（需要 80 端口）..."
            
            # 检查 80 端口是否被 uhttpd 占用
            UHTTPD_STOPPED=0
            if netstat -tlnp 2>/dev/null | grep -q ":80.*uhttpd"; then
                echo "检测到 80 端口被 uhttpd 占用，临时停止..."
                /etc/init.d/uhttpd stop
                UHTTPD_STOPPED=1
                sleep 2
            fi
            
            # 启动临时 web 服务器
            mkdir -p /tmp/acme_webroot
            cd /tmp/acme_webroot
            python3 -m http.server 80 &
            WEB_PID=$!
            sleep 2
            
            $ACME_SH --issue --webroot /tmp/acme_webroot -d "$DOMAIN" --server letsencrypt
            
            # 停止临时 web 服务器
            kill $WEB_PID 2>/dev/null || true
            cd - > /dev/null
            
            # 恢复 uhttpd
            if [ "$UHTTPD_STOPPED" = "1" ]; then
                echo "恢复 uhttpd 服务..."
                /etc/init.d/uhttpd start
            fi
        fi
        
        # 复制证书到正确位置（acme.sh 默认使用 ECC 证书）
        if [ -d "$HOME/.acme.sh/${DOMAIN}_ecc" ]; then
            cp "$HOME/.acme.sh/${DOMAIN}_ecc/fullchain.cer" certs/ego.crt
            cp "$HOME/.acme.sh/${DOMAIN}_ecc/${DOMAIN}.key" certs/ego.key
        else
            cp "$HOME/.acme.sh/$DOMAIN/fullchain.cer" certs/ego.crt
            cp "$HOME/.acme.sh/$DOMAIN/$DOMAIN.key" certs/ego.key
        fi
        ;;
esac

# 启动
echo "[3/3] 启动服务..."
docker compose up -d

echo ""
echo "=== 部署完成 ==="
case $DEPLOY_DIR in
    default|t1-host|t2-bridge)
        echo "管理页面: https://<主机IP>:5001（自签名证书，浏览器需放行）"
        echo "Webhook:  http://<主机IP>:5000"
        ;;
    t3-nginx|t4-certbot)
        echo "管理页面: https://$DOMAIN"
        echo "Webhook:  http://$DOMAIN/in/"
        ;;
esac
echo ""
echo "查看日志: docker compose logs -f"
echo "停止服务: docker compose down"
