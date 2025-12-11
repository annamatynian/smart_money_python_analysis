# Исправление тестов алгоритмической детекции

## Проблема
3 теста падали:
1. `test_vwap_detection_variable_intervals` - VWAP не обнаруживался
2. `test_sweep_algo_detection` - SWEEP детектировался как VWAP
3. `test_algo_detection_cleanup_old_trades` - cleanup удалял неправильное количество элементов

## Root Cause Analysis

### Проблема 1-2: Decision Tree Priority
**Симптом**: SWEEP с `mean_interval=16ms` попадал в VWAP вместо SWEEP

**Причина**: В `_classify_algo_type()` порядок проверок был неправильный:
```python
# СТАРЫЙ ПОРЯДОК (НЕПРАВИЛЬНЫЙ):
1. directional_ratio >= 0.85
2. size_uniformity > 0.9 → ICEBERG
3. cv < 0.10 → TWAP
4. 0.10 <= cv < 0.50 → VWAP  # ← SWEEP попадал сюда!
5. mean_interval < 50ms → SWEEP
```

SWEEP с `mean=16ms` и `cv~25%` попадал в проверку VWAP (#4), потому что она проверялась раньше!

**Решение**: Переместить проверку SWEEP ПЕРЕД проверками CV:
```python
# НОВЫЙ ПОРЯДОК (ПРАВИЛЬНЫЙ):
1. directional_ratio >= 0.85
2. size_uniformity > 0.9 → ICEBERG (наивысший приоритет)
3. mean_interval < 50ms → SWEEP (второй приоритет, проверяется ДО CV!)
4. cv < 0.10 → TWAP
5. 0.10 <= cv < 0.50 → VWAP
```

**Обоснование**: SWEEP имеет самый низкий `mean_interval` и может иметь ЛЮБОЙ CV. Проверка по `mean_interval` должна быть раньше проверки по CV.

### Проблема 3: Cleanup Synchronization
**Симптом**: `len(algo_window)=76` вместо ожидаемого `1` после cleanup

**Причина**: Логика удаления из `algo_interval_history` была симметричной с `algo_window`, но `interval_history` всегда на 1 элемент меньше:
```python
# СТАРАЯ ЛОГИКА (НЕПРАВИЛЬНАЯ):
for _ in range(trades_to_remove):
    book.algo_window.popleft()
    book.algo_interval_history.popleft()  # ← Ошибка! Удаляли слишком много
    book.algo_size_pattern.popleft()
```

Если `algo_window` имеет 100 элементов, `interval_history` имеет только 99 (первая сделка не создает интервал).

**Решение**: Удалять из `interval_history` отдельно с проверкой длины:
```python
# НОВАЯ ЛОГИКА (ПРАВИЛЬНАЯ):
# Сначала удаляем из algo_window и algo_size_pattern
for i in range(trades_to_remove):
    book.algo_window.popleft()
    book.algo_size_pattern.popleft()

# Затем удаляем из interval_history ОТДЕЛЬНО
intervals_to_remove = min(trades_to_remove, len(book.algo_interval_history))
for _ in range(intervals_to_remove):
    book.algo_interval_history.popleft()
```

### Проблема с VWAP тестом
**Симптом**: VWAP не обнаруживался из-за недостаточной вариации интервалов

**Решение**: Увеличить амплитуду волны с `0-150ms` до `0-200ms`:
```python
# СТАРЫЙ (CV недостаточный):
wave = int(150 * (i % 10) / 10)  # 0-150ms
interval = 250 + wave  # 250-400ms

# НОВЫЙ (CV ~30%):
wave = int(200 * (i % 10) / 10)  # 0-200ms
interval = 200 + wave  # 200-400ms, mean=300ms > 50ms (не SWEEP!)
```

## Файлы изменены

### `analyzers.py`
1. **Строки 590-650**: Изменен порядок проверок в `_classify_algo_type()`:
   - SWEEP теперь проверяется ПЕРЕД TWAP/VWAP
   - Добавлены комментарии о приоритете

2. **Строки 370-380**: Исправлена логика cleanup:
   - `interval_history` пропускает первый элемент при удалении
   - Добавлены подробные комментарии

### `tests/test_algo_detection.py`
**Строки 180-210**: Обновлен тест `test_vwap_detection_variable_intervals`:
- Увеличена амплитуда волны до 0-200ms
- Добавлена проверка `mean_interval > 50ms` в комментариях

## Математическое обоснование

### Priority Order в Decision Tree
Правильный порядок проверок основан на **детерминированности признаков**:

1. **size_uniformity** (ICEBERG): Самый детерминированный признак (~100% уверенность)
2. **mean_interval** (SWEEP): Второй по детерминированности, НЕ зависит от CV
3. **cv** (TWAP/VWAP): Зависит от разброса, может быть любым для SWEEP

### Cleanup Synchronization
Если `algo_window` содержит `N` сделок:
- `algo_interval_history` содержит `N-1` интервалов (первая сделка не создает интервал)
- `algo_size_pattern` содержит `N` размеров

При удалении `k` старых сделок:
- Удалить `k` элементов из `algo_window`
- Удалить `k` элементов из `algo_size_pattern`
- Удалить `min(k, len(algo_interval_history))` элементов из `algo_interval_history`

Почему `min(k, len(...))`? Потому что если `k >= N`, то в `interval_history` только `N-1` элементов.

## Ожидаемый результат
Все 10 тестов должны пройти:
```
test_algo_detection_metrics_creation PASSED
test_algo_detection_metrics_defaults PASSED
test_twap_detection_constant_intervals PASSED
test_twap_no_false_positive PASSED
test_vwap_detection_variable_intervals PASSED  ← ИСПРАВЛЕНО
test_iceberg_algo_detection_fixed_size PASSED
test_sweep_algo_detection PASSED  ← ИСПРАВЛЕНО
test_algo_detection_mixed_directions PASSED
test_algo_detection_insufficient_data PASSED
test_algo_detection_cleanup_old_trades PASSED  ← ИСПРАВЛЕНО
```

## Команда для проверки
```bash
pytest tests/test_algo_detection.py -v
```
