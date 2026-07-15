# EGo — 通用信息转发平台 v1.0.0
# 构建: python3 build.py

FROM python:3.11-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


FROM python:3.11-slim

LABEL name="EGo" \
      version="1.0.0" \
      description="通用信息转发平台 — 数据→解析→路由→推送"

WORKDIR /app

# 从 builder 复制依赖
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 复制项目文件（排除构建和开发文件）
COPY . .

# 清理不需要的文件
RUN rm -f build.py docker-compose.yml .gitignore
RUN find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
RUN find . -name ".git" -type d -exec rm -rf {} + 2>/dev/null || true

# 创建持久化目录
RUN mkdir -p /app/data /app/config /app/parsers

# 默认端口
EXPOSE 5000

# 持久化数据卷
VOLUME ["/app/data", "/app/config"]

# 环境变量
ENV WEB_PORT=5000
ENV DB_PATH=/app/data/ego.db
ENV LOG_LEVEL=INFO

# 健康检查
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:$WEB_PORT/api/parsers')" || exit 1

# 入口
CMD ["python3", "main.py"]
