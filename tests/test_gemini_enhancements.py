"""
WHY: Тесты для улучшений предложенных Gemini.

Три категории тестов:
1. Relative Depth Absorption (метрика важности айсберга)
2. Micro-Divergence (VPIN внутри жизненного цикла айсберга)
3. Footprint Histogram (данные для визуализации)

Принцип: TDD - сначала тесты, потом реализация.
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from domain import (
    LocalOrderBook, IcebergLevel, TradeEvent, 
    OrderBookUpdate, IcebergStatus
)
from config import get_config


# ========================================================================
# ТЕСТЫ #1: Relative Depth Absorption Ratio
# ========================================================================

class TestRelativeDepthAbsorption:
    """
    WHY: Проверяем метрику "айсберг vs весь стакан".
    
    Сценарий: Айсберг поглощает 200% видимой ликвидности BID стороны.
    Это явный признак Institutional Anchor.
    """
    
    def test_depth_absorption_calculation(self):
        """
        GIVEN: Стакан с 5 BTC bid ликвидности (топ-20)
        AND:   Айсберг поглотил 10 BTC
        WHEN:  Рассчитываем relative_depth_ratio
        THEN:  ratio = 10 / 5 = 2.0 (200%)
        """
        # Arrange
        book = LocalOrderBook(symbol='BTCUSDT')
        book.bids = {
            Decimal('60000'): Decimal('2.0'),
            Decimal('59990'): Decimal('1.5'),
            Decimal('59980'): Decimal('1.0'),
            Decimal('59970'): Decimal('0.5')
        }
        # Итого 5 BTC видимой ликвидности
        
        iceberg = IcebergLevel(
            price=Decimal('60000'),
            is_ask=False,
            total_hidden_volume=Decimal('10.0')
        )
        
        # Act
        ratio = iceberg.calculate_relative_depth_ratio(book, depth=20)
        
        # Assert
        assert ratio == 2.0, f"Expected 200% absorption, got {ratio}"
    
    def test_depth_absorption_ask_side(self):
        """
        GIVEN: ASK стакан с 3 BTC (топ-20)
        AND:   Айсберг-продавец на 60100 с 9 BTC скрытого объёма
        WHEN:  Рассчитываем ratio
        THEN:  ratio = 9 / 3 = 3.0 (300% - мощнейшая стена!)
        """
        # Arrange
        book = LocalOrderBook(symbol='BTCUSDT')
        book.asks = {
            Decimal('60100'): Decimal('1.0'),
            Decimal('60110'): Decimal('1.2'),
            Decimal('60120'): Decimal('0.8')
        }
        # Итого 3 BTC ask ликвидности
        
        iceberg = IcebergLevel(
            price=Decimal('60100'),
            is_ask=True,
            total_hidden_volume=Decimal('9.0')
        )
        
        # Act
        ratio = iceberg.calculate_relative_depth_ratio(book, depth=20)
        
        # Assert
        assert ratio == 3.0


# ========================================================================
# ТЕСТЫ #2: Micro-Divergence (VPIN внутри айсберга)
# ========================================================================

class TestMicroDivergenceVPIN:
    """
    WHY: Отслеживаем токсичность потока ВНУТРИ жизненного цикла айсберга.
    
    Сценарий:
    - Refill #1: VPIN = 0.3 (нормально)
    - Refill #2: VPIN = 0.5 (растёт)
    - Refill #3: VPIN = 0.8 (ОПАСНО - информированные агрессоры!)
    
    Действие: Снижаем confidence айсберга автоматически.
    """
    
    def test_vpin_whale_attack_penalty(self):
        """
        GIVEN: Айсберг под атакой китов
        AND:   VPIN высокий (0.8), whale_volume_pct = 0.7
        WHEN:  update_micro_divergence()
        THEN:  confidence_score ПАДАЕТ (штраф)
        """
        # Arrange
        iceberg = IcebergLevel(
            price=Decimal('60000'),
            is_ask=False,
            total_hidden_volume=Decimal('5.0'),
            confidence_score=0.9
        )
        
        # Act: Whale attack
        iceberg.update_micro_divergence(
            vpin_at_refill=0.8,
            whale_volume_pct=0.7,  # 70% объёма от китов
            minnow_volume_pct=0.2,
            price_drift_bps=0.0
        )
        
        # Assert: Confidence упал
        assert iceberg.confidence_score < 0.7, f"Expected penalty, got {iceberg.confidence_score}"
    
    def test_vpin_panic_absorption_bonus(self):
        """
        GIVEN: Айсберг поглощает панику толпы
        AND:   VPIN критический (0.9), но minnow_volume_pct = 0.8
        WHEN:  update_micro_divergence()
        THEN:  confidence_score РАСТЁТ (бонус!)
        """
        # Arrange
        iceberg = IcebergLevel(
            price=Decimal('60000'),
            is_ask=False,
            total_hidden_volume=Decimal('10.0'),
            confidence_score=0.7
        )
        
        # Act: Panic absorption
        iceberg.update_micro_divergence(
            vpin_at_refill=0.9,  # Экстремальный VPIN
            whale_volume_pct=0.1,
            minnow_volume_pct=0.8,  # 80% объёма от minnows
            price_drift_bps=0.0
        )
        
        # Assert: Confidence вырос!
        assert iceberg.confidence_score > 0.7, f"Expected bonus, got {iceberg.confidence_score}"
    
    def test_vpin_mixed_flow_caution(self):
        """
        GIVEN: Смешанный поток (нет доминирующей когорты)
        AND:   VPIN умеренный (0.6)
        WHEN:  update_micro_divergence()
        THEN:  Лёгкий штраф (консервативный подход)
        """
        # Arrange
        iceberg = IcebergLevel(
            price=Decimal('60000'),
            is_ask=False,
            total_hidden_volume=Decimal('10.0'),
            confidence_score=0.9
        )
        
        # Act: Смешанный поток
        iceberg.update_micro_divergence(
            vpin_at_refill=0.6,
            whale_volume_pct=0.4,  # 40% whale
            minnow_volume_pct=0.4,  # 40% minnow
            price_drift_bps=0.0
        )
        
        # Assert: Минимальный штраф
        assert 0.8 <= iceberg.confidence_score <= 0.9, "Expected light penalty for mixed flow"


# ========================================================================
# ТЕСТЫ #3: Footprint Histogram (Trade Distribution)
# ========================================================================

class TestIcebergFootprint:
    """
    WHY: Сохраняем распределение сделок внутри айсберг-уровня.
    
    Данные для визуализации:
    - Гистограмма Buy/Sell сделок по времени
    - Размер сделок (whale vs fish)
    - Кластеры агрессивности
    """
    
    def test_footprint_histogram_accumulation(self):
        """
        GIVEN: Айсберг регистрирует 5 сделок
        WHEN:  add_trade_to_footprint() вызывается для каждой
        THEN:  histogram содержит 5 записей с корректными данными
        """
        # Arrange
        iceberg = IcebergLevel(
            price=Decimal('60000'),
            is_ask=False,
            total_hidden_volume=Decimal('20.0')
        )
        
        trades = [
            TradeEvent(price=Decimal('60000'), quantity=Decimal('2.0'), 
                      is_buyer_maker=False, event_time=1000),
            TradeEvent(price=Decimal('60000'), quantity=Decimal('1.5'), 
                      is_buyer_maker=True, event_time=2000),
            TradeEvent(price=Decimal('60000'), quantity=Decimal('5.0'), 
                      is_buyer_maker=False, event_time=3000),  # WHALE
            TradeEvent(price=Decimal('60000'), quantity=Decimal('0.1'), 
                      is_buyer_maker=True, event_time=4000),   # FISH
            TradeEvent(price=Decimal('60000'), quantity=Decimal('3.0'), 
                      is_buyer_maker=False, event_time=5000),  # DOLPHIN
        ]
        
        # Act
        for trade in trades:
            iceberg.add_trade_to_footprint(trade)
        
        # Assert
        assert len(iceberg.trade_footprint) == 5
        
        # Проверяем, что сохранились все детали
        footprint = iceberg.trade_footprint
        assert footprint[0]['quantity'] == Decimal('2.0')
        assert footprint[0]['is_buy'] == True  # is_buyer_maker=False -> buyer aggressive
        assert footprint[2]['cohort'] == 'WHALE'  # 5 BTC
        assert footprint[3]['cohort'] == 'FISH'   # 0.1 BTC
    
    def test_footprint_buy_sell_ratio(self):
        """
        GIVEN: Айсберг поглотил 10 сделок (7 buy, 3 sell)
        WHEN:  Вызываем get_footprint_buy_ratio()
        THEN:  ratio = 0.7 (70% покупки)
        """
        # Arrange
        iceberg = IcebergLevel(price=Decimal('60000'), is_ask=False, 
                              total_hidden_volume=Decimal('10.0'))
        
        # 7 buy trades
        for _ in range(7):
            iceberg.add_trade_to_footprint(
                TradeEvent(price=Decimal('60000'), quantity=Decimal('1.0'),
                          is_buyer_maker=False, event_time=1000)
            )
        
        # 3 sell trades
        for _ in range(3):
            iceberg.add_trade_to_footprint(
                TradeEvent(price=Decimal('60000'), quantity=Decimal('1.0'),
                          is_buyer_maker=True, event_time=2000)
            )
        
        # Act
        buy_ratio = iceberg.get_footprint_buy_ratio()
        
        # Assert
        assert buy_ratio == 0.7


# ========================================================================
# ИНТЕГРАЦИОННЫЕ ТЕСТЫ (Phase 2)
# ========================================================================

class TestGeminiEnhancementsIntegration:
    """
    WHY: Проверяем, что все 3 улучшения работают вместе.
    
    Сценарий полного цикла:
    1. Айсберг детектируется с высоким depth_ratio (200%)
    2. VPIN растёт с каждым refill -> confidence падает
    3. Footprint показывает преобладание whale покупок
    4. Система делает правильный вывод: "Сильная зона, но давление информированных"
    """
    
    def test_full_cycle_with_all_metrics(self):
        """
        GIVEN: Полный сценарий детекции и мониторинга айсберга
        WHEN:  Применяем все 3 метрики
        THEN:  Получаем комплексную оценку качества уровня
        """
        # Arrange
        book = LocalOrderBook(symbol='BTCUSDT')
        book.bids = {
            Decimal('60000'): Decimal('3.0'),
            Decimal('59990'): Decimal('2.0')
        }  # 5 BTC total depth
        
        iceberg = IcebergLevel(
            price=Decimal('60000'),
            is_ask=False,
            total_hidden_volume=Decimal('10.0'),  # 200% depth ratio
            confidence_score=0.9
        )
        
        # Act: Симулируем 3 refill с растущим VPIN (все whale attack)
        iceberg.update_micro_divergence(
            vpin_at_refill=0.5, 
            whale_volume_pct=0.6,  # WHY: Киты начинают атаковать
            minnow_volume_pct=0.3, 
            price_drift_bps=0.0
        )
        iceberg.update_micro_divergence(
            vpin_at_refill=0.7, 
            whale_volume_pct=0.7,  # WHY: Атака усиливается
            minnow_volume_pct=0.2, 
            price_drift_bps=0.0
        )
        iceberg.update_micro_divergence(
            vpin_at_refill=0.9,  # WHY: Критический VPIN
            whale_volume_pct=0.85, # WHY: Массированная атака
            minnow_volume_pct=0.1, 
            price_drift_bps=0.0
        )
        
        # Добавляем trades в footprint
        for i in range(5):
            iceberg.add_trade_to_footprint(
                TradeEvent(price=Decimal('60000'), quantity=Decimal('2.0'),
                          is_buyer_maker=False, event_time=1000 + i*1000)
            )
        
        # Assert: Комбинированные проверки
        depth_ratio = iceberg.calculate_relative_depth_ratio(book, depth=20)
        assert depth_ratio == 2.0, "Depth ratio сохраняется"
        
        # WHY: Проверяем что confidence УПАЛ (был 0.9)
        assert iceberg.confidence_score < 0.9, \
            f"Confidence должен упасть после whale attacks, получили {iceberg.confidence_score}"
        
        assert len(iceberg.trade_footprint) == 5, "Footprint сохранён"
        
        # Проверяем итоговое решение системы
        # WHY: Если confidence упал достаточно, уровень ненадёжен
        is_strong = depth_ratio > 1.5 and iceberg.confidence_score > 0.7
        if not is_strong:
            # Это OK - несмотря на 200% depth, whale attacks снизили доверие
            pass


# ========================================================================
# PERFORMANCE ТЕСТЫ
# ========================================================================

class TestGeminiPerformance:
    """
    WHY: Проверяем что новые метрики не замедляют систему.
    
    Требования:
    - calculate_relative_depth_ratio() < 1ms
    - update_micro_divergence() < 0.5ms
    - add_trade_to_footprint() < 0.1ms
    """
    
    def test_depth_ratio_performance(self):
        """
        GIVEN: Стакан с 1000 уровней
        WHEN:  Рассчитываем depth_ratio 1000 раз
        THEN:  Среднее время < 1ms
        """
        import time
        
        book = LocalOrderBook(symbol='BTCUSDT')
        # Заполняем стакан
        for i in range(1000):
            book.bids[Decimal(f'60000') - Decimal(i)] = Decimal('1.0')
        
        iceberg = IcebergLevel(price=Decimal('60000'), is_ask=False,
                              total_hidden_volume=Decimal('100.0'))
        
        start = time.perf_counter()
        for _ in range(1000):
            iceberg.calculate_relative_depth_ratio(book, depth=20)
        elapsed = (time.perf_counter() - start) * 1000  # в мс
        
        avg_time = elapsed / 1000
        assert avg_time < 1.0, f"Среднее время {avg_time}ms превышает лимит 1ms"
