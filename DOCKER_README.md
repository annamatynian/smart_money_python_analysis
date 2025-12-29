# 🐳 Docker Quick Start

## ⚡ Fast Deploy (для опытных)

```bash
# 1. Клонируй проект
git clone <repo-url>
cd smart_money_python_analysis

# 2. Настрой .env
cp .env.example .env
nano .env  # Заполни BINANCE_API_KEY и POSTGRES_PASSWORD

# 3. Создай директории
mkdir -p pg_data logs && chmod 700 pg_data

# 4. Запусти
docker-compose up -d --build

# 5. Применить миграции
docker-compose exec app python apply_migrations.py

# 6. Backfill (опционально - первичная загрузка данных)
docker-compose exec app python candle_materializer.py
```

---

## 📊 Мониторинг

```bash
# Логи
docker-compose logs -f app

# RAM usage (КРИТИЧНО для 6GB!)
docker stats
# Ожидаем: db ~2.5GB, app ~1.5GB, total <5.5GB

# Статус
docker-compose ps
```

---

## 🛑 Остановка

```bash
# Остановить (данные сохранятся)
docker-compose down

# УДАЛИТЬ ВСЁ (включая базу!)
docker-compose down -v  # ⚠️ ОСТОРОЖНО!
```

---

## 📚 Полная документация

См. **[DEPLOYMENT_GUIDE.md](./DEPLOYMENT_GUIDE.md)** для:
- Подробных инструкций по Oracle Cloud
- Troubleshooting
- Production checklist
- Backup стратегия

---

## 🏗️ Архитектура

```
┌─────────────────┐
│  smart_money_app│  ← Python asyncio + WebSocket
│  (Trading Engine)│     (2GB RAM limit)
└────────┬────────┘
         │
         ↓
┌─────────────────┐
│ PostgreSQL 15   │  ← TimescaleDB для временных рядов
│ (smart_money_db)│     (3.5GB RAM limit)
└─────────────────┘
```

**Оптимизация для 6GB RAM:**
- PostgreSQL: `shared_buffers=1536MB`, `work_mem=32MB`
- App: asyncio (non-blocking I/O), minimal memory footprint
- Total: <5.5GB (остаётся 0.5GB для OS)

---

## ⚙️ Переменные окружения

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `POSTGRES_PASSWORD` | ✅ Yes | - | Database password |
| `BINANCE_API_KEY` | ✅ Yes | - | Binance WebSocket access |
| `BINANCE_API_SECRET` | ✅ Yes | - | Binance API secret |
| `DERIBIT_API_KEY` | ❌ No | - | For GEX integration (optional) |
| `LOG_LEVEL` | ❌ No | INFO | DEBUG/INFO/WARNING/ERROR |
| `ENVIRONMENT` | ❌ No | production | development/production |

---

## 🔒 Security

**ВАЖНО перед production:**

1. ✅ Смени `POSTGRES_PASSWORD` на сильный пароль
2. ✅ `chmod 600 .env`
3. ✅ Binance API: включи IP whitelist
4. ✅ Firewall: закрой порт 5432 (если не нужен внешний доступ)

---

## 📈 RAM Monitoring Commands

```bash
# Real-time RAM usage
watch -n 1 'docker stats --no-stream'

# PostgreSQL внутри контейнера
docker-compose exec db psql -U trader -d smart_money_db -c "
SELECT 
    pg_size_pretty(pg_database_size('smart_money_db')) as db_size,
    count(*) as table_count
FROM information_schema.tables 
WHERE table_schema = 'public';
"

# Если RAM >5.5GB → уменьшить shared_buffers в docker-compose.yml
```

---

## 🐛 Common Issues

**Проблема:** Container restarting  
**Решение:** `docker-compose logs db` → часто OOM Killer

**Проблема:** `asyncpg.exceptions.ConnectionDoesNotExistError`  
**Решение:** Проверь `docker-compose ps` - база должна быть Healthy

**Проблема:** Backfill вылетает с Killed  
**Решение:** Запускать по неделям вручную (см. DEPLOYMENT_GUIDE.md)

---

**Версия:** 1.0  
**Platform:** ARM64 (Oracle Cloud Ampere A1)  
**Tested on:** Ubuntu 24.04 LTS + Docker 24.0+
