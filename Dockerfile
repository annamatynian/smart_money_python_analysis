# ============================================
# Multi-stage Dockerfile для ARM64 (Oracle Cloud)
# Python 3.10 + PostgreSQL client + Asyncio HFT System
# ============================================

# ============ STAGE 1: Builder (зависимости) ============
FROM python:3.10-slim AS builder

# WHY: ARM64 совместимость + минимальный размер образа
LABEL maintainer="Smart Money Analysis Project"
LABEL architecture="arm64"
LABEL description="HFT Iceberg Detection System for Crypto Markets"

# Установка build dependencies
# WHY: Нужны для компиляции asyncpg, psycopg2-binary и других C-расширений
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Создаём виртуальное окружение
# WHY: Изоляция зависимостей + легче копировать в production stage
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Копируем только requirements.txt (для Docker cache layer)
COPY requirements.txt /tmp/requirements.txt

# Устанавливаем зависимости
# WHY: --no-cache-dir экономит ~100MB в образе
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r /tmp/requirements.txt


# ============ STAGE 2: Production (runtime) ============
FROM python:3.10-slim

# Runtime dependencies (только libpq для PostgreSQL клиента)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Копируем виртуальное окружение из builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Рабочая директория
WORKDIR /app

# Копируем исходный код приложения
# WHY: .dockerignore исключает ненужные файлы (см. .dockerignore)
COPY . /app/

# Создаём непривилегированного пользователя
# WHY: Безопасность - не запускаем от root
RUN useradd -m -u 1000 trader && \
    chown -R trader:trader /app

USER trader

# Healthcheck для Docker Swarm / K8s
# WHY: Проверяем что приложение живо (можно заменить на HTTP endpoint)
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import asyncio; import asyncpg" || exit 1

# Переменные окружения по умолчанию
# WHY: Можно переопределить через docker-compose.yml
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC

# Expose порт (если будет REST API в будущем)
# EXPOSE 8000

# Точка входа
# WHY: Используем exec форму для правильной обработки сигналов (SIGTERM)
CMD ["python", "main.py"]
