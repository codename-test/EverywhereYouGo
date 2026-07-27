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
read -p "请输入选项 [1-5]: " MODE

case $MODE in
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
        read -p "请输入域名: " DOMAIN
        read -p "请输入证书路径: " CERT_PATH
        read -p "请输入私钥路径: " KEY_PATH
        ;;
    5)
        DEPLOY_DIR="t4-certbot"
        read -p "请输入域名: " DOMAIN
        read -p "请输入邮箱: " EMAIL
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
        echo "签发 Let's Encrypt 证书（需要 80 端口可用）..."
        docker compose run --rm --service-ports certbot certonly --standalone \
            -d "$DOMAIN" --email "$EMAIL" --agree-tos --no-eff-password
        CERT_PATH=$(docker compose run --rm certbot find /etc/letsencrypt/live -name "fullchain.pem" | head -1 | xargs dirname)
        docker compose run --rm certbot cp "$CERT_PATH/fullchain.pem" /certs/ego.crt
        docker compose run --rm certbot cp "$CERT_PATH/privkey.pem" /certs/ego.key
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
