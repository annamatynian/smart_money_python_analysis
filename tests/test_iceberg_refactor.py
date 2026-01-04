"""
WHY: TDD для рефакторинга Native vs Synthetic айсберг-детекции.

Теория (документ "Идентификация айсберг-ордеров", раздел 1.2):
- Native айсберги: delta_t <= 5-10ms (биржевое ядро, детерминированный)
- Synthetic айсберги: 10ms < delta_t <= 50ms (API боты, стохастический)

Задача:
- Test A: delta_t=3ms → Native path, confidence=1.0
- Test B: delta_t=30ms → Synthetic path, confidence~0.5 (sigmoid)
"""

import pytest
from decimal import Decimal
from domain import LocalOrderBook, TradeEvent
from analyzers import IcebergAnalyzer
from config import BTC_CONFIG


class TestNativeSyntheticSplit:
    """
    WHY: Валидация разделения логики Native/Synthetic айсбергов.
    
    Gemini Audit: "Unified Path" создает false negatives для Native
    и false positives для Synthetic. Нужно 2 независимых пути.
    """
    
    def setup_method(self):
        """Подготовка для каждого теста"""
        self.book = LocalOrderBook(symbol="BTCUSDT")
        self.analyzer = IcebergAnalyzer(config=BTC_CONFIG)
        
        # WHY: Заполняем стакан через apply_snapshot (не прямое присваивание)
        # Создаем реалистичный стакан: bid=95000, ask=95001
        self.book.apply_snapshot(
            bids=[(Decimal("95000.00"), Decimal("1.0")), (Decimal("94999.00"), Decimal("2.0"))],
            asks=[(Decimal("95001.00"), Decimal("1.0")), (Decimal("95002.00"), Decimal("2.0"))],
            last_update_id=1000
        )
    
    def test_native_iceberg_fast_refill(self):
        """
        TEST A: Native айсберг (delta_t=3ms)
        
        WHY: Биржевое ядро (Matching Engine) рефиллит за 100μs-10ms.
        Это детерминированный процесс → confidence должен быть ~1.0.
        
        Сценарий:
        1. Trade на 1.5 BTC по цене 95000
        2. visible_before = 1.0 BTC
        3. hidden_volume = 1.5 - 1.0 = 0.5 BTC
        4. delta_t = 3ms (NATIVE threshold)
        
        Ожидание:
        - IcebergDetectedEvent возвращен
        - confidence >= 0.95 (Native не требует сигмоиды)
        """
        # Arrange: Создаём сделку
        trade = TradeEvent(
            price=Decimal("95000.00"),
            quantity=Decimal("1.5"),  # Больше visible
            is_buyer_maker=True,
            event_time=1000000,  # ms
            trade_id=123
        )
        
        visible_before = Decimal("1.0")  # Видимый объем ДО сделки
        delta_t_ms = 3  # NATIVE: очень быстрый refill
        update_time_ms = 1000003  # 3ms после trade
        
        # Act: Вызываем анализатор
        result = self.analyzer.analyze_with_timing(
            book=self.book,
            trade=trade,
            visible_before=visible_before,
            delta_t_ms=delta_t_ms,
            update_time_ms=update_time_ms,
            vpin_score=None,
            cvd_divergence=None
        )
        
        # Assert: Должен вернуть айсберг с высокой уверенностью
        assert result is not None, "Native айсберг должен быть обнаружен"
        assert result.confidence >= 0.95, (
            f"Native айсберг (delta_t={delta_t_ms}ms) должен иметь "
            f"confidence >= 0.95, получили {result.confidence}"
        )
        assert result.detected_hidden_volume == Decimal("0.5")
        assert result.price == Decimal("95000.00")
    
    def test_synthetic_iceberg_medium_refill(self):
        """
        TEST B: Synthetic айсберг (delta_t=30ms)
        
        WHY: API боты рефиллят за >20ms из-за network roundtrip.
        Это стохастический процесс → confidence рассчитывается через sigmoid.
        
        Сценарий:
        1. Trade на 2.0 BTC по цене 95000
        2. visible_before = 1.0 BTC
        3. hidden_volume = 2.0 - 1.0 = 1.0 BTC
        4. delta_t = 30ms (SYNTHETIC, точка перехода sigmoid)
        
        Ожидание:
        - IcebergDetectedEvent возвращен
        - confidence ≈ 0.4-0.6 (зависит от sigmoid параметров)
        - НЕ должно быть 1.0 (это не Native!)
        """
        # Arrange: Создаём сделку
        trade = TradeEvent(
            price=Decimal("95000.00"),
            quantity=Decimal("2.0"),  # Больше visible
            is_buyer_maker=False,  # ASK iceberg
            event_time=2000000,
            trade_id=456
        )
        
        visible_before = Decimal("1.0")
        delta_t_ms = 30  # SYNTHETIC: средняя задержка (точка cutoff)
        update_time_ms = 2000030
        
        # Act
        result = self.analyzer.analyze_with_timing(
            book=self.book,
            trade=trade,
            visible_before=visible_before,
            delta_t_ms=delta_t_ms,
            update_time_ms=update_time_ms,
            vpin_score=None,
            cvd_divergence=None
        )
        
        # Assert: Должен вернуть айсберг с умеренной уверенностью
        assert result is not None, "Synthetic айсберг должен быть обнаружен"
        
        # WHY: При delta_t=30ms (cutoff), sigmoid даёт ~0.5
        # После умножения на volume_confidence (~0.5), ожидаем 0.25-0.35
        # (без GEX/VPIN adjustments, которые могут повысить)
        assert 0.20 <= result.confidence <= 0.70, (
            f"Synthetic айсберг (delta_t={delta_t_ms}ms) должен иметь "
            f"умеренный confidence (0.2-0.7), получили {result.confidence}"
        )
        
        # КРИТИЧНО: НЕ должно быть Native confidence (1.0)
        assert result.confidence < 0.95, (
            f"Synthetic айсберг НЕ должен иметь Native confidence, "
            f"получили {result.confidence}"
        )
        
        assert result.detected_hidden_volume == Decimal("1.0")
        assert result.price == Decimal("95000.00")
    
    def test_too_slow_rejected(self):
        """
        TEST C: Слишком медленный refill отклоняется
        
        WHY: delta_t > synthetic_refill_max_ms (50ms) = НЕ айсберг,
        а новый ордер от третьей стороны.
        
        Ожидание:
        - Возвращает None (событие отклонено)
        """
        trade = TradeEvent(
            price=Decimal("95000.00"),
            quantity=Decimal("1.5"),
            is_buyer_maker=True,
            event_time=3000000,
            trade_id=789
        )
        
        visible_before = Decimal("1.0")
        delta_t_ms = 60  # СЛИШКОМ МЕДЛЕННО (> 50ms)
        update_time_ms = 3000060
        
        # Act
        result = self.analyzer.analyze_with_timing(
            book=self.book,
            trade=trade,
            visible_before=visible_before,
            delta_t_ms=delta_t_ms,
            update_time_ms=update_time_ms,
            vpin_score=None,
            cvd_divergence=None
        )
        
        # Assert: Должен вернуть None (отклонено)
        assert result is None, (
            f"Refill с delta_t={delta_t_ms}ms (> 50ms) должен быть отклонён, "
            f"но вернул {result}"
        )
    
    def test_config_override_multi_asset(self):
        """
        TEST D (Gemini Recommendation): Config Override для мульти-ассет
        
        WHY: Защита от hardcoded значений при работе с BTC/ETH/SOL.
        Проверяем что analyzer использует ПРАВИЛЬНЫЙ config.
        
        Сценарий:
        - delta_t = 8ms (фиксированное значение)
        - BTC config: native_refill_max_ms=5 → 8ms = SYNTHETIC
        - SOL config: native_refill_max_ms=10 → 8ms = NATIVE
        
        Ожидание:
        - BTC: confidence < 0.95 (Synthetic sigmoid)
        - SOL: confidence >= 0.95 (Native детерминистика)
        
        Если тест падает: analyzer использует hardcoded значения!
        """
        from config import SOL_CONFIG
        
        # Arrange: Одинаковая сделка для обоих конфигов
        trade = TradeEvent(
            price=Decimal("180.50"),  # SOL цена (реалистичнее)
            quantity=Decimal("100.0"),
            is_buyer_maker=True,
            event_time=4000000,
            trade_id=999
        )
        
        visible_before = Decimal("50.0")
        delta_t_ms = 8  # КРИТИЧНАЯ ТОЧКА: Native для SOL, Synthetic для BTC
        update_time_ms = 4000008
        
        # === BTC ANALYZER (native_refill_max_ms=5) ===
        btc_book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
        btc_book.apply_snapshot(
            bids=[(Decimal("95000.00"), Decimal("1.0"))],
            asks=[(Decimal("95001.00"), Decimal("1.0"))],
            last_update_id=1000
        )
        btc_analyzer = IcebergAnalyzer(config=BTC_CONFIG)
        
        btc_result = btc_analyzer.analyze_with_timing(
            book=btc_book,
            trade=trade,
            visible_before=visible_before,
            delta_t_ms=delta_t_ms,
            update_time_ms=update_time_ms,
            vpin_score=None,
            cvd_divergence=None
        )
        
        # === SOL ANALYZER (native_refill_max_ms=10) ===
        sol_book = LocalOrderBook(symbol="SOLUSDT", config=SOL_CONFIG)
        sol_book.apply_snapshot(
            bids=[(Decimal("180.00"), Decimal("1.0"))],
            asks=[(Decimal("181.00"), Decimal("1.0"))],
            last_update_id=2000
        )
        sol_analyzer = IcebergAnalyzer(config=SOL_CONFIG)
        
        sol_result = sol_analyzer.analyze_with_timing(
            book=sol_book,
            trade=trade,
            visible_before=visible_before,
            delta_t_ms=delta_t_ms,
            update_time_ms=update_time_ms,
            vpin_score=None,
            cvd_divergence=None
        )
        
        # === ASSERTIONS ===
        
        # 1. Оба должны вернуть результат (айсберг обнаружен)
        assert btc_result is not None, "BTC должен обнаружить айсберг"
        assert sol_result is not None, "SOL должен обнаружить айсберг"
        
        # 2. BTC: delta_t=8ms > native_max=5ms → SYNTHETIC (умеренный confidence)
        assert btc_result.confidence < 0.95, (
            f"BTC с delta_t={delta_t_ms}ms (> native_max=5ms) должен быть Synthetic, "
            f"но confidence={btc_result.confidence:.2f} (слишком высокий, похож на Native)"
        )
        
        # 3. SOL: delta_t=8ms <= native_max=10ms → NATIVE (высокий confidence)
        assert sol_result.confidence >= 0.95, (
            f"SOL с delta_t={delta_t_ms}ms (<= native_max=10ms) должен быть Native, "
            f"но confidence={sol_result.confidence:.2f} (слишком низкий, похож на Synthetic)"
        )
        
        # 4. Проверяем РАЗНИЦУ в confidence (критично для мульти-ассет)
        confidence_diff = sol_result.confidence - btc_result.confidence
        assert confidence_diff > 0.3, (
            f"Разница в confidence между SOL (Native) и BTC (Synthetic) должна быть >0.3, "
            f"получили {confidence_diff:.2f}. "
            f"Возможно analyzer использует hardcoded значения вместо config!"
        )
