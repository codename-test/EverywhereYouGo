# EGo — 通用信息转发平台
FROM python:3.11-alpine3.18

LABEL maintainer="EGo Team"

ENV TZ=Asia/Shanghai LANG=zh_CN.UTF-8 PYTHONUNBUFFERED=1

# 5000 = HTTP（webhook/健康检查），5001 = HTTPS（管理页面）
EXPOSE 5000 5001

RUN set -eux && \
    apk --no-cache update && \
    apk -U --no-cache add tzdata openssl && \
    cp /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    echo "Asia/Shanghai" > /etc/timezone && \
    mkdir -p /app/data /app/config /app/parsers /app/channels

WORKDIR /app

COPY requirements.txt .
RUN python3 -m pip install --no-cache-dir -r requirements.txt -q

COPY . .

# 持久化：运行时产生的数据 + **用户上传的插件**。
# 内置插件在 parsers_builtin/ 与 channels_builtin/（随镜像更新），故意**不**打卷——
# named volume 首次创建会把镜像内容拷进去，之后以卷为准，内置插件就永远升不上去了。
VOLUME ["/app/data", "/app/config", "/app/parsers", "/app/channels"]

ENV WEB_PORT=5000
ENV WEB_SSL_PORT=5001
ENV DB_PATH=/app/data/ego.db
ENV LOG_LEVEL=INFO

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:$WEB_PORT/api/health')" || exit 1

ENTRYPOINT ["python3"]
CMD ["main.py"]
