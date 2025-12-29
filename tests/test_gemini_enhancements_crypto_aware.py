# ========================================================================
# GEMINI CRYPTO-AWARE INTEGRATION TESTS
# ========================================================================

"""
WHY: Проверяем что crypto-aware логика работает корректно.

Отличия от TradFi:
- Высокий VPIN + Minnow агрессоры = БОНУС (поглощение паники)
- Высокий VPIN + Whale агрессоры = ШТРАФ (атака китов)
"""

from decimal import Decimal
import pytest
from domain import IcebergLevel, LocalOrderBook, TradeEvent


class TestCryptoAwareMicroDivergence:
    """
    Тесты новой crypto-aware логики update_micro_divergence()
    """
    
    def test_whale_attack_scenario(self):
        """
        СЦЕНАРИЙ А: Whale Attack (VPIN 0.8, 70% whale volume)
        
        GIVEN: Айсберг на BID 60000
        AND:   VPIN критический (0.8)
        AND:   70% объёма от китов
        WHEN:  update_micro_divergence()
        THEN:  Confidence ПАДАЕТ (штраф ~25%)
        """
        # Arrange
        iceberg = IcebergLevel(
            price=Decimal('60000'),
            is_ask=False,
            total_hidden_volume=Decimal('10.0'),
            confidence_score=0.9
        )
        
        # Act: Whale attack
        iceberg.update_micro_divergence(
            vpin_at_refill=0.8,
            whale_volume_pct=0.7,  # Киты штурмуют
            minnow_volume_pct=0.2,
            price_drift_bps=6.0  # Цена "прогибается"
        )
        
        # Assert: Штраф ~35% (0.25 base + 0.1 price drift)
        assert iceberg.confidence_score < 0.65, f"Expected penalty, got {iceberg.confidence_score}"
        assert len(iceberg.vpin_history) == 1, "VPIN записан в историю"
    
    def test_panic_absorption_scenario(self):
        """
        СЦЕНАРИЙ Б: Panic Absorption (VPIN 0.9, 80% minnow volume)
        
        GIVEN: Айсберг на BID 60000
        AND:   VPIN экстремальный (0.9)
        AND:   80% объёма от minnows (толпа в панике)
        WHEN:  update_micro_divergence()
        THEN:  Confidence РАСТЁТ (бонус +10%)
        """
        # Arrange
        iceberg = IcebergLevel(
            price=Decimal('60000'),
            is_ask=False,
            total_hidden_volume=Decimal('15.0'),
            confidence_score=0.7
        )
        
        # Act: Panic absorption
        iceberg.update_micro_divergence(
            vpin_at_refill=0.9,  # Экстремальный VPIN
            whale_volume_pct=0.1,
            minnow_volume_pct=0.8,  # Паника толпы!
            price_drift_bps=3.0  # Цена стабильна
        )
        
        # Assert: Бонус +10%
        assert iceberg.confidence_score >= 0.75, f"Expected bonus, got {iceberg.confidence_score}"
    
    def test_mixed_flow_caution(self):
        """
        СЦЕНАРИЙ В: Mixed Flow (VPIN 0.6, 40% whale + 40% minnow)
        
        GIVEN: Неопределённый поток
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
        
        # Act
        iceberg.update_micro_divergence(
            vpin_at_refill=0.6,
            whale_volume_pct=0.4,
            minnow_volume_pct=0.4,
            price_drift_bps=0.0
        )
        
        # Assert: Минимальный штраф (~5%)
        assert 0.8 <= iceberg.confidence_score <= 0.9, "Expected light penalty"


class TestFullCycleCryptoScenarios:
    """
    Интеграционные тесты: все 3 метрики + crypto-aware логика
    """
    
    def test_institutional_panic_absorption(self):
        """
        РЕАЛЬНЫЙ КЕЙС: Кит выкупает каскадные ликвидации
        
        GIVEN: Крупный айсберг (10 BTC) на BID 60000
        AND:   Стакан имеет 5 BTC видимой ликвидности
        AND:   Толпа в панике (80% minnow volume, VPIN 0.85)
        WHEN:  Применяем все метрики
        THEN:  Айсберг классифицируется как INSTITUTIONAL ANCHOR
        """
        # Arrange: Книга ордеров
        book = LocalOrderBook(symbol='BTCUSDT')
        book.bids = {
            Decimal('60000'): Decimal('2.0'),
            Decimal('59990'): Decimal('1.5'),
            Decimal('59980'): Decimal('1.5')
        }  # Total: 5 BTC
        
        iceberg = IcebergLevel(
            price=Decimal('60000'),
            is_ask=False,
            total_hidden_volume=Decimal('10.0'),
            confidence_score=0.85
        )
        
        # Act 1: Depth Absorption
        depth_ratio = iceberg.calculate_relative_depth_ratio(book, depth=20)
        
        # Act 2: Panic Absorption (CRYPTO-SPECIFIC!)
        iceberg.update_micro_divergence(
            vpin_at_refill=0.85,
            whale_volume_pct=0.15,
            minnow_volume_pct=0.80,  # ПАНИКА!
            price_drift_bps=2.0
        )
        
        # Act 3: Trade Footprint
        for i in range(10):
            trade = TradeEvent(
                price=Decimal('60000'),
                quantity=Decimal('0.5'),  # Minnow
                is_buyer_maker=True,  # SELL (паника)
                event_time=1000 + i
            )
            iceberg.add_trade_to_footprint(trade)
        
        # Assert
        assert depth_ratio == 2.0, "Айсберг поглотил 200% видимой ликвидности"
        assert iceberg.confidence_score >= 0.85, "Confidence НЕ упал (паника = хорошо)"
        assert iceberg.get_footprint_buy_ratio() == 0.0, "Все сделки = продажи"
        
        # Вывод: Это СИЛЬНЫЙ институциональный уровень
        is_strong_anchor = (depth_ratio > 1.5 and 
                           iceberg.confidence_score > 0.8 and 
                           iceberg.get_footprint_buy_ratio() < 0.2)
        assert is_strong_anchor, "Кит выкупает ликвидации = лучший лонг-сигнал"
    
    def test_weak_iceberg_whale_attack(self):
        """
        РЕАЛЬНЫЙ КЕЙС: Крупные игроки ломают слабый уровень
        
        GIVEN: Средний айсберг (3 BTC) на ASK 61000
        AND:   Стакан имеет 10 BTC видимой ликвидности
        AND:   Киты атакуют (70% whale volume, VPIN 0.75)
        WHEN:  Применяем все метрики
        THEN:  Айсберг классифицируется как WEAK
        """
        # Arrange
        book = LocalOrderBook(symbol='BTCUSDT')
        book.asks = {
            Decimal('61000'): Decimal('5.0'),
            Decimal('61010'): Decimal('3.0'),
            Decimal('61020'): Decimal('2.0')
        }  # Total: 10 BTC
        
        iceberg = IcebergLevel(
            price=Decimal('61000'),
            is_ask=True,
            total_hidden_volume=Decimal('3.0'),
            confidence_score=0.8
        )
        
        # Act 1: Depth Absorption (слабый)
        depth_ratio = iceberg.calculate_relative_depth_ratio(book, depth=20)
        
        # Act 2: Whale Attack!
        iceberg.update_micro_divergence(
            vpin_at_refill=0.75,
            whale_volume_pct=0.70,  # Киты атакуют
            minnow_volume_pct=0.20,
            price_drift_bps=8.0  # Цена проседает
        )
        
        # Act 3: Trade Footprint
        for i in range(5):
            trade = TradeEvent(
                price=Decimal('61000'),
                quantity=Decimal('5.0'),  # Whale size
                is_buyer_maker=False,  # BUY (атака на ASK)
                event_time=1000 + i
            )
            iceberg.add_trade_to_footprint(trade)
        
        # Assert
        assert depth_ratio < 0.5, "Айсберг мал относительно стакана"
        assert iceberg.confidence_score < 0.6, "Confidence упал (whale attack)"
        assert iceberg.get_footprint_buy_ratio() == 1.0, "Все сделки = покупки"
        
        # Вывод: Этот уровень НЕ устоит
        is_weak = (depth_ratio < 0.5 and 
                   iceberg.confidence_score < 0.6 and 
                   iceberg.get_footprint_buy_ratio() > 0.8)
        assert is_weak, "Whale attack + слабый depth = пробой неизбежен"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
