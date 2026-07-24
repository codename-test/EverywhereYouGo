# EGo — 通用信息转发平台 v1.2.1
FROM python:3.11-alpine3.18

LABEL maintainer="EGo Team"

ENV TZ=Asia/Shanghai LANG=zh_CN.UTF-8 PYTHONUNBUFFERED=1

EXPOSE 5000

RUN set -eux && \
    apk --no-cache update && \
    apk -U --no-cache add tzdata && \
    cp /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    echo "Asia/Shanghai" > /etc/timezone && \
    mkdir -p /app/data /app/config /app/parsers

WORKDIR /app

COPY requirements.txt .
RUN python3 -m pip install --no-cache-dir -r requirements.txt -q

COPY . .

VOLUME ["/app/data", "/app/config"]

ENV WEB_PORT=5000
ENV DB_PATH=/app/data/ego.db
ENV LOG_LEVEL=INFO

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:$WEB_PORT/api/health')" || exit 1

ENTRYPOINT ["python3"]
CMD ["main.py"]
