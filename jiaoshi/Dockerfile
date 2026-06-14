# -*- coding: utf-8 -*-
"""
智能试卷生成系统 - Docker 部署配置
构建: docker build -t exam-generator .
启动: docker-compose up -d
"""

# ============================================================
# Stage 1: 构建阶段（安装依赖、构建向量索引）
# ============================================================
FROM python:3.11-slim-bookworm AS builder

# 系统依赖（ChromaDB 需要 sqlite3, WeasyPrint 需要 GTK 库）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*

# 拷贝依赖文件
WORKDIR /app
COPY requirements.txt .

# 在虚拟环境中安装 Python 依赖
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt && \
    /opt/venv/bin/pip install --no-cache-dir sentence-transformers weasyprint jinja2

# ============================================================
# Stage 2: 运行阶段
# ============================================================
FROM python:3.11-slim-bookworm

# 运行时系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsqlite3-0 \
    libgobject-2.0-0 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libffi8 \
    && rm -rf /var/lib/apt/lists/*

# 继承虚拟环境
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 应用代码
WORKDIR /app
COPY . .

# 创建非 root 用户
RUN useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app
USER appuser

# 构建默认向量索引（启动时也可能触发）
RUN python -c "from backend.rag_indexer import build_index; build_index()" || true

# 暴露端口
EXPOSE 5000

# 启动服务
CMD ["python", "backend/server.py"]
