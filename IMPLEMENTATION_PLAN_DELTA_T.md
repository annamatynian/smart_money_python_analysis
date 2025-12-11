# План реализации Delta-t валидации для фильтрации ложных айсбергов

## Проблема
Текущий код не различает:
- **Биржевой refill** (айсберг-ордер, автоматический) — Delta-t: 5-30ms
- **Новый ордер** третьей стороны (маркет-мейкер) — Delta-t: 50-500ms

Это приводит к высокому проценту ложных срабатываний.

## Теоретическая база
Согласно документу "Идентификация Айсберг-Ордеров на Binance L2" (раздел 1.2):

> "Время реакции алгоритма мэтчинга биржи на необходимость пополнения айсберга является 
> детерминированным процессом с малым шумом, тогда как приход новых ордеров — 
> стохастическим процессом."

Математическая модель:
```
P(Refill|Δt) = 1 / (1 + e^(α(Δt - τ_cutoff)))
```
где:
- τ_cutoff ≈ 30ms (99-й перцентиль обработки Binance)
- α — коэффициент крутизны (настраивается)

## Архитектурные изменения

### Шаг 1: Добавить состояние в LocalOrderBook (domain.py)

```python
class LocalOrderBook(BaseModel):
    # ... существующие поля ...
    
    # НОВОЕ: Очередь ожидающих проверки trade-ов
    pending_refill_checks: deque = Field(default_factory=deque)
    # Структура: [(trade_event, visible_vol_before, timestamp_ms), ...]
```

**WHY**: Нужно помнить последние trades (max 100ms назад) для проверки при получении update.

### Шаг 2: Модифицировать TradeEvent обработку (services.py)

**ТЕКУЩАЯ ЛОГИКА** (в `_consume_and_analyze()`):
```python
elif isinstance(event, TradeEvent):
    # ... обработка trade ...
    iceberg_event = IcebergAnalyzer.analyze(self.book, trade, target_vol)
```

**НОВАЯ ЛОГИКА**:
```python
elif isinstance(event, TradeEvent):
    trade = event
    
    # 1. Определяем visible volume ПЕРЕД trade
    target_vol = Decimal("0")
    if trade.is_buyer_maker:
        target_vol = self.book.bids.get(trade.price, Decimal("0"))
    else:
        target_vol = self.book.asks.get(trade.price, Decimal("0"))
    
    # 2. КРИТИЧНО: Не анализируем сразу, а добавляем в очередь ожидания
    self.book.pending_refill_checks.append({
        'trade': trade,
        'visible_before': target_vol,
        'trade_time_ms': trade.event_time,  # Timestamp биржи в ms
        'price': trade.price,
        'is_ask': not trade.is_buyer_maker
    })
    
    # 3. Очищаем старые записи (> 100ms назад)
    self._cleanup_pending_checks(current_time_ms=trade.event_time)
    
    # 4. Остальная логика (whale analysis, CVD) — без изменений
    category, vol_usd, algo_alert = WhaleAnalyzer.update_stats(self.book, trade)
    # ...
```

### Шаг 3: Модифицировать OrderBookUpdate обработку

**ТЕКУЩАЯ ЛОГИКА**:
```python
if isinstance(event, OrderBookUpdate):
    if self.book.apply_update(event):
        # Только проверка целостности
```

**НОВАЯ ЛОГИКА**:
```python
if isinstance(event, OrderBookUpdate):
    update = event
    
    # 1. Применяем update к стакану
    if self.book.apply_update(update):
        
        # 2. НОВОЕ: Проверяем, не был ли это refill айсберга?
        update_time_ms = int(update.event_time.timestamp() * 1000)
        
        # Итерируемся по pending checks
        for pending in list(self.book.pending_refill_checks):
            trade = pending['trade']
            
            # Проверка 1: Та же цена?
            if pending['price'] != trade.price:
                continue
            
            # Проверка 2: Delta-t в допустимом диапазоне?
            delta_t = update_time_ms - pending['trade_time_ms']
            
            if delta_t < -20:  # Race condition (update пришел раньше trade)
                continue
            
            if delta_t > 100:  # Слишком старая trade (пропускаем)
                self.book.pending_refill_checks.remove(pending)
                continue
            
            # Проверка 3: Объем восстановился?
            current_vol = self._get_volume_at_price(trade.price, pending['is_ask'])
            
            # Если объем вернулся (или даже вырос) — это refill
            if current_vol >= pending['visible_before']:
                
                # ВЫЗЫВАЕМ АНАЛИЗАТОР С DELTA-T!
                iceberg_event = IcebergAnalyzer.analyze_with_timing(
                    book=self.book,
                    trade=trade,
                    visible_before=pending['visible_before'],
                    delta_t_ms=delta_t,
                    update_time_ms=update_time_ms
                )
                
                if iceberg_event:
                    # Логика вывода алерта (без изменений)
                    lvl = self.book.active_icebergs.get(trade.price)
                    total_hidden = lvl.total_hidden_volume if lvl else iceberg_event.detected_hidden_volume
                    obi = self.book.get_weighted_obi(depth=20)
                    self._print_iceberg_update(iceberg_event, total_hidden, obi, lvl)
                
                # Удаляем обработанную проверку
                self.book.pending_refill_checks.remove(pending)
        
        # Проверка целостности (без изменений)
        if not self.book.validate_integrity():
            await self._resync()
```

### Шаг 4: Новый метод анализатора (analyzers.py)

```python
class IcebergAnalyzer:
    
    @staticmethod
    def analyze_with_timing(
        book: LocalOrderBook, 
        trade: TradeEvent, 
        visible_before: Decimal,
        delta_t_ms: int,
        update_time_ms: int
    ) -> Optional[IcebergDetectedEvent]:
        """
        WHY: Анализ с учетом временной валидации (Delta-t).
        
        Args:
            book: Локальный стакан
            trade: Событие сделки
            visible_before: Видимый объем ДО trade
            delta_t_ms: Время между trade и update (в миллисекундах)
            update_time_ms: Timestamp update события (для логирования)
        
        Returns:
            IcebergDetectedEvent если найден РЕАЛЬНЫЙ айсберг, иначе None
        """
        
        # --- 1. ФИЛЬТР ВРЕМЕННОЙ ВАЛИДАЦИИ (ГЛАВНОЕ НОВОВВЕДЕНИЕ) ---
        
        # Константы (можно вынести в config)
        MAX_REFILL_DELAY_MS = 50  # Порог для Public API
        CUTOFF_MS = 30  # τ_cutoff из теории
        ALPHA = 0.15  # Коэффициент крутизны сигмоиды
        
        # Вычисляем вероятность refill (сигмоида)
        # P(Refill|Δt) = 1 / (1 + e^(α(Δt - τ)))
        from math import exp
        exponent = ALPHA * (delta_t_ms - CUTOFF_MS)
        
        # Защита от overflow
        if exponent > 50:
            refill_probability = 0.0
        elif exponent < -50:
            refill_probability = 1.0
        else:
            refill_probability = 1.0 / (1.0 + exp(exponent))
        
        # ЖЕСТКАЯ ГРАНИЦА: Если delta_t > 50ms → точно НЕ refill
        if delta_t_ms > MAX_REFILL_DELAY_MS:
            return None
        
        # МЯГКАЯ ГРАНИЦА: Если вероятность < 0.6 → недостаточно уверенности
        if refill_probability < 0.6:
            return None
        
        # --- 2. ОСТАЛЬНЫЕ ФИЛЬТРЫ (БЕЗ ИЗМЕНЕНИЙ) ---
        
        if visible_before < Decimal("0.0001"):
            return None
        
        if trade.quantity <= visible_before:
            return None
        
        hidden_volume = trade.quantity - visible_before
        
        if trade.quantity > 0:
            iceberg_ratio = hidden_volume / trade.quantity
        else:
            iceberg_ratio = Decimal("0")
        
        if hidden_volume > Decimal("0.05") and iceberg_ratio > Decimal("0.3"):
            
            is_ask_iceberg = not trade.is_buyer_maker
            
            # МОДИФИКАЦИЯ: Уверенность теперь учитывает Delta-t
            # base_confidence = min(iceberg_ratio, 0.95)
            # timing_confidence = refill_probability
            # final_confidence = base_confidence * timing_confidence
            
            dynamic_confidence = float(min(iceberg_ratio, Decimal("0.95"))) * refill_probability
            
            iceberg_lvl = book.register_iceberg(
                price=trade.price,
                hidden_vol=hidden_volume,
                is_ask=is_ask_iceberg,
                confidence=dynamic_confidence
            )
            
            iceberg_lvl.refill_count += 1
            
            return IcebergDetectedEvent(
                symbol=book.symbol,
                price=trade.price,
                detected_hidden_volume=hidden_volume,
                visible_volume_before=visible_before,
                confidence=iceberg_lvl.confidence_score
            )
        
        return None
```

### Шаг 5: Вспомогательный метод в services.py

```python
def _cleanup_pending_checks(self, current_time_ms: int):
    """
    Удаляет устаревшие pending checks (старше 100ms).
    
    WHY: Предотвращает утечку памяти и избегает обработки 
    несвязанных trade-update пар.
    """
    cutoff_time = current_time_ms - 100  # 100ms назад
    
    # Удаляем старые элементы с начала очереди
    while self.book.pending_refill_checks:
        first = self.book.pending_refill_checks[0]
        if first['trade_time_ms'] < cutoff_time:
            self.book.pending_refill_checks.popleft()
        else:
            break  # Остальные элементы новее

def _get_volume_at_price(self, price: Decimal, is_ask: bool) -> Decimal:
    """
    Получает текущий объем на уровне цены.
    
    Args:
        price: Ценовой уровень
        is_ask: True если Ask (сопротивление), False если Bid (поддержка)
    
    Returns:
        Decimal объем или 0 если уровня нет
    """
    if is_ask:
        return self.book.asks.get(price, Decimal("0"))
    else:
        return self.book.bids.get(price, Decimal("0"))
```

## Эмпирическая калибровка параметров

После реализации нужно:

1. **Собрать статистику Delta-t** на реальных данных (1-2 дня)
2. **Построить гистограмму распределения** для:
   - Подтвержденных айсбергов (вручную размеченных)
   - Ложных срабатываний
3. **Оптимизировать пороги**:
   - `MAX_REFILL_DELAY_MS` (сейчас 50ms)
   - `CUTOFF_MS` (сейчас 30ms)
   - `ALPHA` (сейчас 0.15)

## Ожидаемые результаты

- **Precision (точность)**: +30-40% (меньше ложных срабатываний)
- **Recall (полнота)**: -5-10% (некоторые медленные айсберги будут пропущены)
- **F1-Score**: +20-25% (общее качество детекции)

## Риски и ограничения

1. **Latency Spikes**: В периоды высокой нагрузки на биржу Delta-t может увеличиться до 100ms даже для реальных айсбергов
   - *Решение*: Адаптивный порог на основе скользящей медианы задержек
   
2. **Race Conditions**: Update может прийти раньше Trade из-за маршрутизации
   - *Решение*: Проверка `delta_t < -20` и пропуск таких случаев

3. **Memory Leak**: Если забыть чистить `pending_refill_checks`
   - *Решение*: Периодическая очистка в `_cleanup_pending_checks()`

## Следующие шаги

1. Реализовать изменения в указанном порядке
2. Запустить на тестовой паре (BTCUSDT) на 1 час
3. Логировать все Delta-t в CSV для анализа
4. Построить ROC-кривую для оптимизации порогов
5. Добавить unit-тесты для `analyze_with_timing()`
