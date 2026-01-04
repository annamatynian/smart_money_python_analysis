# 🚀 Deployment Guide: Oracle Cloud ARM64 (Ubuntu 24.04)

## 📋 Prerequisites

**Сервер:**
- Oracle Cloud Free Tier (Ampere A1 ARM64)
- 4 CPU cores (2.4 GHz)
- **6GB RAM** (критично!)
- Ubuntu 24.04 LTS
- 50GB+ Storage

**Локальная машина:**
- Git
- SSH client

---

## 🔧 STEP 1: Подготовка сервера

### 1.1 SSH подключение
```bash
ssh ubuntu@<your-oracle-instance-ip>
```

### 1.2 Обновление системы
```bash
sudo apt update && sudo apt upgrade -y
```

### 1.3 Установка Docker
```bash
# Установка Docker
sudo apt install -y docker.io docker-compose

# Проверка версии
docker --version
# Ожидаем: Docker version 24.0+

docker-compose --version
# Ожидаем: docker-compose version 1.29+

# Добавление текущего юзера в группу docker
sudo usermod -aG docker $USER

# Применение изменений (или перелогиниться)
newgrp docker

# Тест без sudo
docker ps
# Должно работать без ошибок
```

### 1.4 Настройка firewall (опционально)
```bash
# Открыть порт 5432 для PostgreSQL (если нужен внешний доступ)
sudo ufw allow 5432/tcp

# Проверка статуса
sudo ufw status
```

---

## 📦 STEP 2: Клонирование проекта

```bash
# Создать рабочую директорию
mkdir -p ~/trading
cd ~/trading

# Клонировать репозиторий (замени на свой URL)
git clone https://github.com/your-username/smart_money_python_analysis.git
cd smart_money_python_analysis

# Проверка структуры
ls -la
# Ожидаем: Dockerfile, docker-compose.yml, .env.example, requirements.txt
```

---

## 🔐 STEP 3: Конфигурация

### 3.1 Создание .env файла
```bash
# Копируем шаблон
cp .env.example .env

# Редактируем
nano .env
```

**Заполни следующие переменные:**
```bash
# PostgreSQL (смени пароль!)
POSTGRES_PASSWORD=твой_сильный_пароль_здесь

# Binance API (обязательно)
BINANCE_API_KEY=твой_api_key
BINANCE_API_SECRET=твой_api_secret

# Остальное можно оставить по умолчанию
```

**Сохранить:** `Ctrl+O`, `Enter`, `Ctrl+X`

### 3.2 Права доступа
```bash
# Защита .env файла
chmod 600 .env

# Создание директорий для данных
mkdir -p pg_data logs

# Права для PostgreSQL volume
chmod 700 pg_data
```

---

## 🏗️ STEP 4: Сборка и запуск

### 4.1 Сборка образов
```bash
# Первая сборка (займёт 5-10 минут)
docker-compose build

# Проверка образов
docker images
# Ожидаем: smart_money_python_analysis_app, timescale/timescaledb
```

### 4.2 Запуск контейнеров
```bash
# Запуск в detached режиме
docker-compose up -d

# Проверка статуса
docker-compose ps
# Ожидаем: 
# - smart_money_db (healthy)
# - smart_money_app (running)
```

### 4.3 Просмотр логов
```bash
# Все сервисы
docker-compose logs -f

# Только приложение
docker-compose logs -f app

# Только база
docker-compose logs -f db

# Последние 100 строк
docker-compose logs --tail=100 app

# Выход из логов: Ctrl+C
```

---

## 🔍 STEP 5: Проверка работоспособности

### 5.1 Проверка базы данных
```bash
# Подключение к PostgreSQL
docker-compose exec db psql -U trader -d smart_money_db

# SQL команды для проверки:
\dt  -- Список таблиц (ожидаем: market_metrics_full, smart_candles, migrations)
\q   -- Выход
```

### 5.2 Применение миграций (если нужно)
```bash
docker-compose exec app python apply_migrations.py

# Ожидаем вывод:
# ✅ Applied migration: 001_create_market_metrics.sql
# ✅ Applied migration: 002_...
```

### 5.3 Первичная загрузка данных (backfill)
```bash
# Запуск материализации свечей
docker-compose exec app python candle_materializer.py

# Процесс займёт 30-60 минут
# Можно отследить прогресс в логах:
docker-compose logs -f app
```

---

## 📊 STEP 6: Мониторинг

### 6.1 Мониторинг RAM (КРИТИЧНО!)
```bash
# Использование памяти контейнерами
docker stats

# Ожидаем:
# smart_money_db:  2.5-3.0 GB  (не должно превышать 3.5GB)
# smart_money_app: 1.0-1.5 GB
# TOTAL:           <5.5 GB     (оставляем 0.5GB для OS)
```

**⚠️ ALARM:** Если RAM >5.5GB → см. раздел Troubleshooting

### 6.2 Мониторинг диска
```bash
# Использование диска
df -h

# Размер PostgreSQL данных
du -sh pg_data/
# Ожидаем: ~5-10GB после полного backfill (6 месяцев)
```

### 6.3 Healthcheck
```bash
# Проверка здоровья контейнеров
docker-compose ps

# Если статус "unhealthy":
docker-compose logs db  # Смотрим что случилось
```

---

## 🔄 STEP 7: Управление

### Перезапуск
```bash
# Перезапустить всё
docker-compose restart

# Только приложение
docker-compose restart app
```

### Остановка
```bash
# Остановить (данные сохраняются)
docker-compose down

# Остановить + удалить volumes (УДАЛИТ БАЗУ!)
docker-compose down -v  # ⚠️ ОСТОРОЖНО!
```

### Обновление кода
```bash
# Получить изменения
git pull origin main

# Пересобрать и перезапустить
docker-compose up -d --build
```

---

## 🐛 Troubleshooting

### Проблема: OOM Killer убивает контейнеры

**Симптомы:**
```bash
docker-compose logs db
# Вывод: "Killed" или "137 exit code"
```

**Решение:**
```bash
# 1. Уменьшить shared_buffers в docker-compose.yml:
nano docker-compose.yml

# Изменить:
-c shared_buffers=1536MB  →  -c shared_buffers=1024MB
-c work_mem=32MB          →  -c work_mem=16MB

# 2. Перезапустить
docker-compose down
docker-compose up -d
```

---

### Проблема: База не запускается

**Симптомы:**
```bash
docker-compose ps
# db: Restarting (1)
```

**Решение:**
```bash
# Проверка логов
docker-compose logs db | tail -50

# Частая причина: повреждённые данные
# Решение: удалить pg_data и начать заново
docker-compose down
sudo rm -rf pg_data
mkdir -p pg_data && chmod 700 pg_data
docker-compose up -d
```

---

### Проблема: Приложение не подключается к базе

**Симптомы:**
```bash
docker-compose logs app
# asyncpg.exceptions.ConnectionDoesNotExistError
```

**Решение:**
```bash
# 1. Проверить что база здорова
docker-compose exec db pg_isready -U trader

# 2. Проверить переменные окружения
docker-compose exec app env | grep DB_
# Ожидаем: DB_HOST=db, DB_PORT=5432

# 3. Проверить сеть
docker network ls
docker network inspect smart_money_python_analysis_smart_money_net
```

---

### Проблема: Backfill вылетает с OOM

**Симптомы:**
```bash
docker-compose logs app
# Killed signal 9
```

**Решение:**
```bash
# Запускать backfill вручную по батчам
docker-compose exec app python -c "
from candle_materializer import CandleMaterializer
import asyncio
from datetime import datetime, timedelta

async def main():
    m = CandleMaterializer('postgresql://trader:password@db:5432/smart_money_db')
    await m.connect()
    
    # Только 1 неделя за раз
    await m.materialize_candles(
        symbol='BTCUSDT',
        start_time=datetime(2024, 12, 1),
        end_time=datetime(2024, 12, 8),
        timeframe_minutes=5
    )
    
    await m.close()

asyncio.run(main())
"
```

---

## 📈 Production Checklist

**Перед запуском в production:**

- [ ] ✅ Сменил `POSTGRES_PASSWORD` на сильный пароль
- [ ] ✅ Binance API ключи имеют ограничения по IP
- [ ] ✅ `.env` файл имеет права `chmod 600`
- [ ] ✅ Настроен `ufw` firewall (закрыты лишние порты)
- [ ] ✅ Установлен мониторинг RAM: `docker stats`
- [ ] ✅ Настроен backup PostgreSQL (cron + pg_dump)
- [ ] ✅ Логи ротируются (настроить logrotate)
- [ ] ✅ Healthcheck работает корректно

**Бэкап базы (ежедневный cron):**
```bash
# Добавить в crontab
crontab -e

# Ежедневный бэкап в 3:00 ночи
0 3 * * * cd ~/trading/smart_money_python_analysis && docker-compose exec -T db pg_dump -U trader smart_money_db | gzip > ~/backups/db_$(date +\%Y\%m\%d).sql.gz
```

---

## 🎯 Next Steps

1. **Мониторинг:** Установить Grafana + Prometheus (опционально)
2. **Alerts:** Настроить Telegram бота для уведомлений
3. **Continuous Training:** Настроить cron для еженедельного переобучения ML
4. **Incremental Backfill:** Запустить постепенное заполнение старых данных (см. ML_TRAINING_ROADMAP.md)

---

**🎉 Поздравляю! Система запущена на Oracle Cloud ARM64!**

Для вопросов и багов → смотри логи: `docker-compose logs -f`
