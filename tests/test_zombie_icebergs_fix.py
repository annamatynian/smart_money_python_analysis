# ===========================================================================
# TEST: Zombie Icebergs Fix (Gemini Validation)
# ===========================================================================

"""
WHY: Проверяет экспоненциальное затухание confidence (Fix: Zombie Icebergs).

Проблема (до fix):
- Айсберги детектированные часы назад сохраняли высокий confidence_score
- FeatureCollector читал статический confidence_score
- ML features загрязнялись "призрачными" уровнями поддержки
- Модель обучалась на false positives

После fix:
- IcebergLevel.get_decayed_confidence() вычисляет затухание
- Conf(t) = Conf_initial · e^(-λ·Δt)
- ML features получают реальную уверенность
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta
import math
from domain import IcebergLevel


class TestConfidenceDecay:
    """Тесты экспоненциального затухания confidence"""
    
    def test_no_decay_at_zero_time(self):
        """
        WHY: Проверяет что confidence не меняется при Δt=0.
        
        Сценарий: Айсберг только что обновился.
        Ожидается: decayed_confidence == confidence_score
        """
        now = datetime.now()
        
        iceberg = IcebergLevel(
            price=Decimal("60000"),
            is_ask=False,
            confidence_score=0.9,
            last_update_time=now  # Только что обновился
        )
        
        decayed = iceberg.get_decayed_confidence(now, half_life_seconds=300)
        
        # При Δt=0 затухание = 0 → confidence не изменился
        assert decayed == pytest.approx(0.9, abs=0.001), \
            f"При Δt=0 confidence должен остаться 0.9, получили {decayed}"
    
    def test_half_life_decay(self):
        """
        WHY: Проверяет корректность периода полураспада.
        
        Теория: При t = T_half → Conf = Conf_initial * 0.5
        Формула: Conf(t) = Conf_initial · e^(-λ·Δt), где λ = ln(2) / T_half
        """
        now = datetime.now()
        half_life_seconds = 300.0  # 5 минут
        
        iceberg = IcebergLevel(
            price=Decimal("60000"),
            is_ask=False,
            confidence_score=0.8,
            last_update_time=now - timedelta(seconds=half_life_seconds)  # Прошло T_half
        )
        
        decayed = iceberg.get_decayed_confidence(now, half_life_seconds=half_life_seconds)
        
        # Через 1 период полураспада confidence должен упасть вдвое
        expected = 0.8 * 0.5  # 0.4
        assert decayed == pytest.approx(expected, abs=0.001), \
            f"Через T_half confidence должен быть {expected}, получили {decayed}"
    
    def test_two_half_lives_decay(self):
        """
        WHY: Проверяет затухание через 2 периода полураспада.
        
        Теория: После 2 * T_half → Conf = Conf_initial * 0.25
        """
        now = datetime.now()
        half_life_seconds = 300.0
        
        iceberg = IcebergLevel(
            price=Decimal("60000"),
            is_ask=False,
            confidence_score=0.8,
            last_update_time=now - timedelta(seconds=2 * half_life_seconds)  # 10 минут
        )
        
        decayed = iceberg.get_decayed_confidence(now, half_life_seconds=half_life_seconds)
        
        # Через 2 периода: 0.8 * 0.5 * 0.5 = 0.2
        expected = 0.8 * 0.25
        assert decayed == pytest.approx(expected, abs=0.001), \
            f"Через 2*T_half confidence должен быть {expected}, получили {decayed}"
    
    def test_zombie_iceberg_scenario(self):
        """
        WHY: Проверяет реальный сценарий "зомби-айсберга".
        
        Сценарий:
        - Айсберг детектирован 40 минут назад (confidence=0.9)
        - Ни одного рефилла (last_update_time не обновлялся)
        - half_life = 5 минут
        
        Ожидается: confidence < 0.01 (практически 0)
        """
        now = datetime.now()
        
        zombie = IcebergLevel(
            price=Decimal("60000"),
            is_ask=False,
            confidence_score=0.9,
            last_update_time=now - timedelta(minutes=40)  # 40 минут без обновлений!
        )
        
        decayed = zombie.get_decayed_confidence(now, half_life_seconds=300)
        
        # Через 40 минут (8 периодов полураспада) confidence ≈ 0
        # 0.9 * (0.5)^8 = 0.9 * 0.00390625 ≈ 0.0035
        assert decayed < 0.01, \
            f"Зомби-айсберг (40 мин) должен иметь confidence < 0.01, получили {decayed}"
    
    def test_different_half_life_strategies(self):
        """
        WHY: Проверяет корректность для разных стратегий.
        
        Тестирует рекомендованные half_life:
        - Scalping: 60 сек
        - Swing: 300 сек
        - Position: 3600 сек
        """
        now = datetime.now()
        
        # Сценарий: Айсберг не обновлялся 5 минут (300 сек)
        iceberg = IcebergLevel(
            price=Decimal("60000"),
            is_ask=False,
            confidence_score=1.0,
            last_update_time=now - timedelta(minutes=5)
        )
        
        # Scalping (60 сек half-life): за 300 сек прошло 5 периодов
        scalping_decay = iceberg.get_decayed_confidence(now, half_life_seconds=60)
        expected_scalping = 1.0 * (0.5 ** 5)  # = 0.03125
        assert scalping_decay == pytest.approx(expected_scalping, abs=0.001), \
            f"Scalping (5*T_half) должно быть {expected_scalping}, получили {scalping_decay}"
        
        # Swing (300 сек half-life): за 300 сек прошел 1 период
        swing_decay = iceberg.get_decayed_confidence(now, half_life_seconds=300)
        expected_swing = 1.0 * 0.5  # = 0.5
        assert swing_decay == pytest.approx(expected_swing, abs=0.001), \
            f"Swing (1*T_half) должно быть {expected_swing}, получили {swing_decay}"
        
        # Position (3600 сек half-life): за 300 сек прошло 0.083 периода
        position_decay = iceberg.get_decayed_confidence(now, half_life_seconds=3600)
        # 300 сек / 3600 сек = 0.0833 периода
        # e^(-ln(2) * 0.0833) ≈ 0.944
        assert position_decay > 0.9, \
            f"Position (0.083*T_half) должно быть >0.9, получили {position_decay}"
    
    def test_negative_time_protection(self):
        """
        WHY: Проверяет защиту от отрицательного Δt (рассинхрон часов).
        
        Сценарий: last_update_time в будущем (часы сбились).
        Ожидается: Возврат исходного confidence без затухания.
        """
        now = datetime.now()
        
        iceberg = IcebergLevel(
            price=Decimal("60000"),
            is_ask=False,
            confidence_score=0.7,
            last_update_time=now + timedelta(minutes=10)  # В будущем!
        )
        
        decayed = iceberg.get_decayed_confidence(now, half_life_seconds=300)
        
        # Должен вернуть исходный confidence (защита от отрицательного времени)
        assert decayed == 0.7, \
            f"При отрицательном Δt должно вернуться 0.7, получили {decayed}"
    
    def test_boundary_values(self):
        """
        WHY: Проверяет граничные значения [0.0, 1.0].
        
        Проверяет что decayed_confidence всегда в диапазоне [0.0, 1.0].
        """
        now = datetime.now()
        
        # Граница 1: confidence_score = 1.0
        iceberg_max = IcebergLevel(
            price=Decimal("60000"),
            is_ask=False,
            confidence_score=1.0,
            last_update_time=now - timedelta(minutes=1)
        )
        
        decayed_max = iceberg_max.get_decayed_confidence(now, half_life_seconds=300)
        assert 0.0 <= decayed_max <= 1.0, \
            f"Decayed confidence должен быть в [0.0, 1.0], получили {decayed_max}"
        
        # Граница 2: confidence_score = 0.0
        iceberg_min = IcebergLevel(
            price=Decimal("60000"),
            is_ask=False,
            confidence_score=0.0,
            last_update_time=now - timedelta(minutes=1)
        )
        
        decayed_min = iceberg_min.get_decayed_confidence(now, half_life_seconds=300)
        assert decayed_min == 0.0, \
            f"При confidence=0.0 должно остаться 0.0, получили {decayed_min}"
    
    def test_mathematical_correctness(self):
        """
        WHY: Проверяет математическую корректность формулы.
        
        Формула: Conf(t) = Conf_initial · e^(-λ·Δt)
        где λ = ln(2) / T_half
        """
        now = datetime.now()
        half_life_seconds = 300.0
        delta_t_seconds = 600.0  # 10 минут = 2 * T_half
        
        iceberg = IcebergLevel(
            price=Decimal("60000"),
            is_ask=False,
            confidence_score=0.8,
            last_update_time=now - timedelta(seconds=delta_t_seconds)
        )
        
        # Ручной расчет
        lambda_decay = math.log(2) / half_life_seconds
        expected = 0.8 * math.exp(-lambda_decay * delta_t_seconds)
        
        # Метод
        decayed = iceberg.get_decayed_confidence(now, half_life_seconds=half_life_seconds)
        
        assert decayed == pytest.approx(expected, abs=0.0001), \
            f"Метод должен совпадать с ручным расчетом: expected={expected}, got={decayed}"


class TestConfidenceDecayIntegration:
    """Интеграционные тесты для реальных сценариев"""
    
    def test_ml_feature_cleanup_scenario(self):
        """
        WHY: Проверяет сценарий очистки ML features.
        
        Сценарий (FeatureCollector):
        - Собираем features каждые 10 секунд
        - Айсберг не обновлялся 20 минут
        - FeatureCollector должен получить низкий confidence
        
        Это предотвращает загрязнение ML данных зомби-айсбергами.
        """
        now = datetime.now()
        
        zombie_iceberg = IcebergLevel(
            price=Decimal("60000"),
            is_ask=False,
            confidence_score=0.95,  # Изначально высокая уверенность
            last_update_time=now - timedelta(minutes=20),  # 20 минут без обновлений
            refill_count=5  # Был активен, но больше не пополняется
        )
        
        # FeatureCollector вызывает get_decayed_confidence()
        decayed = zombie_iceberg.get_decayed_confidence(now, half_life_seconds=300)
        
        # Через 20 минут (4 периода полураспада): 0.95 * (0.5)^4 ≈ 0.059
        assert decayed < 0.1, \
            f"Зомби-айсберг (20 мин) должен иметь confidence < 0.1 для ML, получили {decayed}"
        
        # FeatureCollector может отфильтровать этот айсберг (confidence < 0.3)
        assert decayed < 0.3, "FeatureCollector должен отфильтровать по threshold 0.3"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestCleanupOldIcebergs:
    """Тесты автоматической очистки зомби-айсбергов"""
    
    def test_cleanup_removes_old_icebergs(self):
        """
        WHY: Проверяет что cleanup_old_icebergs() удаляет старые айсберги.
        
        Сценарий:
        - 3 айсберга с разным временем обновления
        - cleanup должен удалить те, у которых decayed_confidence < 0.1
        """
        from domain import LocalOrderBook, IcebergLevel
        from decimal import Decimal
        
        now = datetime.now()
        
        # Создаём order book
        book = LocalOrderBook(symbol="BTCUSDT")
        
        # Добавляем 3 айсберга
        
        # 1. Свежий айсберг (обновлён 1 минуту назад) - должен ОСТАТЬСЯ
        fresh_iceberg = IcebergLevel(
            price=Decimal("60000"),
            is_ask=False,
            confidence_score=0.9,
            last_update_time=now - timedelta(minutes=1)
        )
        book.active_icebergs[Decimal("60000")] = fresh_iceberg
        
        # 2. Старый айсберг (20 минут) - должен БЫТЬ УДАЛЁН
        old_iceberg = IcebergLevel(
            price=Decimal("60100"),
            is_ask=False,
            confidence_score=0.9,
            last_update_time=now - timedelta(minutes=20)  # 4 периода полураспада
        )
        book.active_icebergs[Decimal("60100")] = old_iceberg
        
        # 3. Очень старый айсберг (40 минут) - должен БЫТЬ УДАЛЁН
        very_old_iceberg = IcebergLevel(
            price=Decimal("60200"),
            is_ask=False,
            confidence_score=0.9,
            last_update_time=now - timedelta(minutes=40)  # 8 периодов
        )
        book.active_icebergs[Decimal("60200")] = very_old_iceberg
        
        # Выполняем cleanup
        removed_count = book.cleanup_old_icebergs(
            current_time=now,
            half_life_seconds=300,
            min_confidence=0.1
        )
        
        # Проверяем результаты
        assert removed_count == 2, f"Должно быть удалено 2 айсберга, получено {removed_count}"
        
        # Проверяем что остался только свежий
        assert len(book.active_icebergs) == 1, f"Должен остаться 1 айсберг, осталось {len(book.active_icebergs)}"
        assert Decimal("60000") in book.active_icebergs, "Свежий айсберг должен остаться"
        assert Decimal("60100") not in book.active_icebergs, "Старый айсберг должен быть удалён"
        assert Decimal("60200") not in book.active_icebergs, "Очень старый айсберг должен быть удалён"
    
    def test_cleanup_no_removal_if_all_fresh(self):
        """
        WHY: Проверяет что свежие айсберги не удаляются.
        """
        from domain import LocalOrderBook, IcebergLevel
        from decimal import Decimal
        
        now = datetime.now()
        book = LocalOrderBook(symbol="BTCUSDT")
        
        # Добавляем 2 свежих айсберга (обновлены недавно)
        book.active_icebergs[Decimal("60000")] = IcebergLevel(
            price=Decimal("60000"),
            is_ask=False,
            confidence_score=0.9,
            last_update_time=now - timedelta(seconds=30)
        )
        
        book.active_icebergs[Decimal("60100")] = IcebergLevel(
            price=Decimal("60100"),
            is_ask=False,
            confidence_score=0.8,
            last_update_time=now - timedelta(minutes=2)
        )
        
        removed_count = book.cleanup_old_icebergs(
            current_time=now,
            half_life_seconds=300,
            min_confidence=0.1
        )
        
        # Ни один айсберг не должен быть удалён
        assert removed_count == 0, f"Не должно быть удалений, получено {removed_count}"
        assert len(book.active_icebergs) == 2, "Все айсберги должны остаться"
    
    def test_cleanup_respects_min_confidence_threshold(self):
        """
        WHY: Проверяет что cleanup использует min_confidence threshold.
        
        Сценарий:
        - Айсберг с decayed_confidence=0.15
        - min_confidence=0.1 → ОСТАЁТСЯ
        - min_confidence=0.2 → УДАЛИТСЯ
        """
        from domain import LocalOrderBook, IcebergLevel
        from decimal import Decimal
        
        now = datetime.now()
        
        # Тест 1: min_confidence=0.1 (айсберг остаётся)
        book1 = LocalOrderBook(symbol="BTCUSDT")
        book1.active_icebergs[Decimal("60000")] = IcebergLevel(
            price=Decimal("60000"),
            is_ask=False,
            confidence_score=0.9,
            last_update_time=now - timedelta(minutes=11)  # decayed ≈ 0.15
        )
        
        removed1 = book1.cleanup_old_icebergs(now, half_life_seconds=300, min_confidence=0.1)
        assert removed1 == 0, "Айсберг с confidence>0.1 должен остаться"
        assert len(book1.active_icebergs) == 1
        
        # Тест 2: min_confidence=0.2 (айсберг удаляется)
        book2 = LocalOrderBook(symbol="BTCUSDT")
        book2.active_icebergs[Decimal("60000")] = IcebergLevel(
            price=Decimal("60000"),
            is_ask=False,
            confidence_score=0.9,
            last_update_time=now - timedelta(minutes=11)  # decayed ≈ 0.15
        )
        
        removed2 = book2.cleanup_old_icebergs(now, half_life_seconds=300, min_confidence=0.2)
        assert removed2 == 1, "Айсберг с confidence<0.2 должен быть удалён"
        assert len(book2.active_icebergs) == 0
    
    def test_cleanup_empty_registry(self):
        """
        WHY: Проверяет что cleanup безопасен для пустого реестра.
        """
        from domain import LocalOrderBook
        
        now = datetime.now()
        book = LocalOrderBook(symbol="BTCUSDT")
        
        # У нас нет айсбергов
        assert len(book.active_icebergs) == 0
        
        # cleanup не должен падать
        removed_count = book.cleanup_old_icebergs(now, half_life_seconds=300, min_confidence=0.1)
        
        assert removed_count == 0, "Не должно быть удалений в пустом реестре"
        assert len(book.active_icebergs) == 0
