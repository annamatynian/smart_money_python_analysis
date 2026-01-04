# DELTA-T INTEGRATION - QUICK GUIDE

## 🎯 ТОЧКА 1: TradeEvent Handler (services.py, line ~238)

### Найти блок:
```python
# Анализируем: была ли сделка больше, чем видимый объем?
iceberg_event = IcebergAnalyzer.analyze(self.book, trade, target_vol)
```

### Заменить на:
```python
# === NEW DELTA-T LOGIC ===
# 2. DO NOT analyze immediately - add to pending queue
self.book.pending_refill_checks.append({
    'trade': trade,
    'visible_before': target_vol,
    'trade_time_ms': trade.event_time,
    'price': trade.price,
    'is_ask': not trade.is_buyer_maker
})

# 3. Cleanup old entries
self._cleanup_pending_checks(current_time_ms=trade.event_time)
```

### ⚠️ ВАЖНО: НЕ удалять ML LOGIC блок ниже (строки ~245-290)!

---

## 🎯 ТОЧКА 2: OrderBookUpdate Handler (services.py, line ~180)

### Найти блок:
```python
if isinstance(event, OrderBookUpdate):
    try:
        if self.book.apply_update(event):
            if not self.book.validate_integrity():
```

### Добавить ПОСЛЕ `if self.book.apply_update(event):`:
```python
            # === NEW: Check for iceberg refills ===
            update_time_ms = int(update.event_time.timestamp() * 1000)
            
            for pending in list(self.book.pending_refill_checks):
                trade = pending['trade']
                
                # Check 1: Same price?
                if pending['price'] != trade.price:
                    continue
                
                # Check 2: Delta-t in valid range?
                delta_t = update_time_ms - pending['trade_time_ms']
                
                if delta_t < -20:  # Race condition
                    continue
                
                if delta_t > 100:  # Too old
                    self.book.pending_refill_checks.remove(pending)
                    continue
                
                # Check 3: Volume restored?
                current_vol = self._get_volume_at_price(trade.price, pending['is_ask'])
                
                if current_vol >= pending['visible_before']:
                    
                    # CALL ANALYZER WITH DELTA-T!
                    iceberg_event = IcebergAnalyzer.analyze_with_timing(
                        book=self.book,
                        trade=trade,
                        visible_before=pending['visible_before'],
                        delta_t_ms=delta_t,
                        update_time_ms=update_time_ms
                    )
                    
                    if iceberg_event:
                        lvl = self.book.active_icebergs.get(trade.price)
                        total_hidden = lvl.total_hidden_volume if lvl else iceberg_event.detected_hidden_volume
                        obi = self.book.get_weighted_obi(depth=20)
                        self._print_iceberg_update(iceberg_event, total_hidden, obi, lvl)
                        
                        if self.repository and lvl:
                            asyncio.create_task(self.repository.save_level(lvl, self.symbol))
                    
                    self.book.pending_refill_checks.remove(pending)
```

---

## ✅ Проверка после интеграции:
```bash
python validate_delta_t.py
```

## 🚨 Если что-то сломалось:
1. Проверь отступы (Python чувствителен к ним!)
2. Убедись что НЕ удалил ML LOGIC блок
3. Проверь что все импорты на месте
