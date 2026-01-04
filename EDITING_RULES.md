# CRITICAL EDITING RULES - Предотвращение поломки кода

## ПРОБЛЕМА (2025-01-02)
Потрачено 50+ минут и 80K+ токенов на исправление сломанной индентации в services.py.
**Причина:** Использование str_replace для редактирования многострочного блока (200+ строк) привело к случайной поломке отступов на строках 343-515.

---

## ПРАВИЛО #1: НИКОГДА НЕ РЕДАКТИРУЙ БОЛЬШИЕ БЛОКИ ЧЕРЕЗ str_replace

### ❌ ЗАПРЕЩЕНО:
```python
# str_replace с блоком >50 строк
Filesystem:edit_file(
    old_str="def method():\n    line1\n    line2\n...\n    line200",
    new_str="def method():\n    line1\n    CHANGED\n...\n    line200"
)
```

**Почему опасно:**
- Один лишний/недостающий пробел → SyntaxError
- Трудно визуально проверить 200 строк отступов
- Python очень чувствителен к индентации (4 spaces vs 3 spaces)

### ✅ ПРАВИЛЬНО:
**Для изменений >50 строк:**
1. Прочитай весь файл: `Filesystem:read_text_file(path="X.py")`
2. Сохрани в переменную, внеси изменения
3. Перезапиши полностью: `Filesystem:write_file(path="X.py", content=full_content)`

**Для изменений <50 строк:**
- str_replace OK, но **КОПИРУЙ ТОЧНО** все пробелы из оригинала

---

## ПРАВИЛО #2: ПРОВЕРЯЙ СИНТАКСИС НЕМЕДЛЕННО

### После КАЖДОГО редактирования:

```bash
# Шаг 1: Проверь синтаксис Python
python -m py_compile services.py

# Шаг 2: Если есть тесты - запусти их
pytest tests/test_X.py -v
```

### Если SyntaxError:
1. **НЕ ПЫТАЙСЯ ИСПРАВЛЯТЬ вслепую** через str_replace
2. **ПРОЧИТАЙ файл целиком** и найди точное место поломки
3. Если поломано >10 строк → **ОТКАТИ к backup версии**

---

## ПРАВИЛО #3: BACKUP ДО БОЛЬШИХ ИЗМЕНЕНИЙ

### Перед редактированием критичного файла (services.py, domain.py):

```bash
# Создай backup
cp services.py services.py.backup_$(date +%Y%m%d_%H%M%S)

# Или через Python
import shutil
from datetime import datetime
backup_name = f"services.py.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
shutil.copy('services.py', backup_name)
```

### Если что-то сломалось:
```bash
# Восстанови из backup
cp services.py.backup_20250102_171544 services.py
```

---

## ПРАВИЛО #4: WINDOWS PATHS - ТОЛЬКО MCP FILESYSTEM

### ❌ НИКОГДА на Windows:
```bash
# Bash НЕ работает корректно с C:\... путями
cat >> "C:\path\file.py"
echo "content" >> "C:\path\file.py"
sed -i 's/old/new/' "C:\path\file.py"
```

**Эти команды ПЕРЕЗАПИСЫВАЮТ или ПОВРЕЖДАЮТ файлы!**

### ✅ ТОЛЬКО MCP Filesystem:
```python
# Чтение
Filesystem:read_text_file(path="C:\Users\...\file.py")

# Запись
Filesystem:write_file(path="C:\Users\...\file.py", content="...")

# Редактирование
Filesystem:edit_file(path="C:\Users\...\file.py", edits=[...])
```

---

## ПРАВИЛО #5: ИНДЕНТАЦИЯ - 4 ПРОБЕЛА, НЕ ТАБЫ

### Проверка перед commit:
```bash
# Найди файлы с табами (должно быть пусто)
grep -P '\t' services.py

# Найди файлы со смешанными отступами
python -tt services.py  # -tt = строгий режим
```

### Автофикс (если что-то сломано):
```bash
# Замени табы на 4 пробела (ОСТОРОЖНО!)
expand -t 4 services.py > services_fixed.py
```

---

## ПРАВИЛО #6: СТРАТЕГИЯ ВОССТАНОВЛЕНИЯ

### Если файл сломан после редактирования:

**Шаг 1: Диагностика**
```bash
python -m py_compile services.py
# Покажет: SyntaxError на строке X
```

**Шаг 2: Найди проблему**
```python
# Создай диагностический скрипт
with open('services.py') as f:
    lines = f.readlines()
    for i, line in enumerate(lines[340:520], start=341):
        if len(line) - len(line.lstrip()) not in [0, 4, 8, 12, 16, 20]:
            print(f"Line {i}: BAD INDENT ({len(line) - len(line.lstrip())} spaces)")
```

**Шаг 3: Восстановление**
- Если поломано <5 строк → исправь вручную через Filesystem:edit_file
- Если поломано >5 строк → восстанови из backup
- Если нет backup → используй git:
  ```bash
  git checkout HEAD -- services.py
  ```

---

## ПРАВИЛО #7: GIT WORKFLOW

### Commit ПЕРЕД большими изменениями:
```bash
git add services.py
git commit -m "BEFORE: Adding VULNERABILITY fixes"

# Делаешь изменения...

# Если сломалось:
git diff services.py  # Посмотри что изменилось
git checkout -- services.py  # Откати к последнему commit
```

---

## ЧЕКЛИСТ ПЕРЕД РЕДАКТИРОВАНИЕМ КРИТИЧНЫХ ФАЙЛОВ

- [ ] Создан backup или git commit
- [ ] Изменение <50 строк? (Если нет → используй write_file, не edit_file)
- [ ] Скопированы точные отступы из оригинала
- [ ] После редактирования: `python -m py_compile file.py`
- [ ] После редактирования: `pytest tests/` (если есть)
- [ ] На Windows: используй ТОЛЬКО MCP Filesystem tools
- [ ] Если SyntaxError → НЕ чини вслепую, ЧИТАЙ файл целиком

---

## ПАМЯТКА: СТОИМОСТЬ ОШИБОК

### Индентация в services.py (2025-01-02):
- **Время потеряно:** 50+ минут
- **Токены потрачены:** 80,000+ (42% бюджета)
- **Попыток исправления:** 8+
- **Root cause:** str_replace на блоке 200 строк

### Урок:
**10 секунд на backup экономят 50 минут отладки.**

---

## ЭКСТРЕННЫЙ RECOVERY ПЛАН

Если services.py критически сломан и нет backup:

1. **Скачай чистую версию из Git:**
   ```bash
   git fetch origin
   git checkout origin/main -- services.py
   ```

2. **Или используй /tmp/services_fixed.py** (если есть):
   ```python
   Filesystem:write_file(
       path="C:\...\services.py",
       content=open('/tmp/services_fixed.py').read()
   )
   ```

3. **Или восстанови из транскриптов:**
   - Найди последний рабочий файл в `/mnt/transcripts/`
   - Извлеки полный контент из транскрипта

---

## АВТОР
Создано после инцидента 2025-01-02 17:15 UTC
Чтобы больше никогда не тратить часы на исправление отступов.

**Remember: Prevention > Recovery**
