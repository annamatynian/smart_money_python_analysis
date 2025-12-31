"""
Тесты для GEMINI FIX: Frozen VPIN Problem
==========================================

Проверяет решение проблемы "застывшего VPIN" при низкой ликвидности.

Что тестируется:
1. VolumeBucket.age_seconds() - возраст корзины
2. VolumeBucket.last_update_at - обновление timestamp
3. FlowToxicityAnalyzer.get_current_vpin() - учет partial bucket
4. FlowToxicityAnalyzer.get_vpin_status() - определение stale VPIN
5. Volume-Weighted VPIN формула - корректность расчета

Ссылка: Документ "МЕТРИКА №2: VPIN (Токсичность потока)"
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from domain import LocalOrderBook, VolumeBucket, TradeEvent
from analyzers import FlowToxicityAnalyzer
from config import BTC_CONFIG


# ===========================================================================
# TEST SUITE 1: VolumeBucket Metadata (Временные метаданные)
# ===========================================================================

class TestVolumeBucketMetadata:
    """
    Проверяет, что VolumeBucket корректно отслеживает время создания
    и последнего обновления.
    """
    
    def test_bucket_has_created_at(self):
        """Проверка наличия поля created_at"""
        bucket = VolumeBucket(
            bucket_size=Decimal("10"),
            symbol="BTCUSDT"
        )
        
        assert hasattr(bucket, 'created_at')
        assert isinstance(bucket.created_at, datetime)
    
    def test_bucket_has_last_update_at(self):
        """Проверка наличия поля last_update_at"""
        bucket = VolumeBucket(
            bucket_size=Decimal("10"),
            symbol="BTCUSDT"
        )
        
        assert hasattr(bucket, 'last_update_at')
        assert isinstance(bucket.last_update_at, datetime)
    
    def test_add_trade_updates_last_update_at(self):
        """Проверка, что add_trade() обновляет last_update_at"""
        bucket = VolumeBucket(
            bucket_size=Decimal("10"),
            symbol="BTCUSDT"
        )
        
        # Запоминаем начальное время
        initial_time = bucket.last_update_at
        
        # Делаем небольшую паузу (имитация)
        import time
        time.sleep(0.01)  # 10ms
        
        # Добавляем сделку
        trade = TradeEvent(
            price=Decimal("100000"),
            quantity=Decimal("1.0"),
            is_buyer_maker=False,
            event_time=1234567890000,
            trade_id=1
        )
        
        bucket.add_trade(trade)
        
        # Проверяем что время обновилось
        assert bucket.last_update_at > initial_time
    
    def test_age_seconds_calculation(self):
        """Проверка расчета возраста корзины"""
        # Создаем корзину с фиксированным временем
        fixed_time = datetime(2025, 1, 1, 12, 0, 0)
        
        bucket = VolumeBucket(
            bucket_size=Decimal("10"),
            symbol="BTCUSDT"
        )
        bucket.last_update_at = fixed_time
        
        # Проверяем возраст через 5 минут
        current_time = fixed_time + timedelta(minutes=5)
        age = bucket.age_seconds(current_time)
        
        assert age == 300.0  # 5 минут = 300 секунд
    
    def test_age_seconds_with_recent_trade(self):
        """Проверка возраста для свежей корзины"""
        bucket = VolumeBucket(
            bucket_size=Decimal("10"),
            symbol="BTCUSDT"
        )
        
        # Добавляем сделку
        trade = TradeEvent(
            price=Decimal("100000"),
            quantity=Decimal("1.0"),
            is_buyer_maker=False,
            event_time=1234567890000,
            trade_id=1
        )
        
        bucket.add_trade(trade)
        
        # Сразу проверяем возраст
        age = bucket.age_seconds()
        
        # Должен быть очень маленький (< 1 секунды)
        assert age < 1.0


# ===========================================================================
# TEST SUITE 2: FlowToxicityAnalyzer - Partial Bucket Inclusion
# ===========================================================================

class TestPartialBucketInclusion:
    """
    Проверяет, что get_current_vpin() учитывает current_vpin_bucket
    если она заполнена больше чем на 20%.
    """
    
    def setup_method(self):
        """Создаем book и analyzer перед каждым тестом"""
        self.book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
        self.analyzer = FlowToxicityAnalyzer(
            book=self.book,
            bucket_size=Decimal("10")  # 10 BTC
        )
    
    def test_current_bucket_ignored_when_empty(self):
        """
        Если current_bucket пустая (0% заполнения) - не включается в расчет.
        """
        # Создаем 10 полных исторических корзин
        for i in range(10):
            bucket = VolumeBucket(
                bucket_size=Decimal("10"),
                symbol="BTCUSDT"
            )
            bucket.buy_volume = Decimal("6")
            bucket.sell_volume = Decimal("4")
            bucket.is_complete = True
            self.book.vpin_buckets.append(bucket)
        
        # current_bucket пустая
        assert self.book.current_vpin_bucket.total_volume() == 0
        
        # VPIN должен рассчитываться только по 10 историческим
        vpin = self.analyzer.get_current_vpin()
        assert vpin is not None
        
        # Формула: sum(|6-4|) / sum(10) = 20 / 100 = 0.2
        assert abs(vpin - 0.2) < 0.001
    
    def test_current_bucket_included_when_above_threshold(self):
        """
        Если current_bucket заполнена >20% - включается в расчет.
        """
        # Создаем 10 полных корзин (нейтральные)
        for i in range(10):
            bucket = VolumeBucket(
                bucket_size=Decimal("10"),
                symbol="BTCUSDT"
            )
            bucket.buy_volume = Decimal("5")
            bucket.sell_volume = Decimal("5")
            bucket.is_complete = True
            self.book.vpin_buckets.append(bucket)
        
        # Заполняем current_bucket на 30% (3 BTC из 10)
        # С СИЛЬНЫМ дисбалансом
        for i in range(3):
            trade = TradeEvent(
                price=Decimal("100000"),
                quantity=Decimal("1.0"),
                is_buyer_maker=False,  # Buy
                event_time=1234567890000 + i,
                trade_id=i
            )
            self.book.current_vpin_bucket.add_trade(trade)
        
        # Проверяем заполнение
        fill = float(self.book.current_vpin_bucket.total_volume() / Decimal("10"))
        assert fill == 0.3  # 30%
        
        # VPIN должен учитывать partial bucket
        vpin = self.analyzer.get_current_vpin()
        assert vpin is not None
        
        # Partial bucket: |3-0| = 3, volume = 3
        # Historical buckets: |5-5| = 0, volume = 100
        # VPIN = (0 + 3) / (100 + 3) = 3/103 ≈ 0.029
        assert abs(vpin - 0.029) < 0.01
    
    def test_current_bucket_ignored_when_below_threshold(self):
        """
        Если current_bucket заполнена <20% - НЕ включается в расчет.
        """
        # Создаем 10 полных корзин
        for i in range(10):
            bucket = VolumeBucket(
                bucket_size=Decimal("10"),
                symbol="BTCUSDT"
            )
            bucket.buy_volume = Decimal("5")
            bucket.sell_volume = Decimal("5")
            bucket.is_complete = True
            self.book.vpin_buckets.append(bucket)
        
        # Заполняем current_bucket на 15% (1.5 BTC из 10)
        trade = TradeEvent(
            price=Decimal("100000"),
            quantity=Decimal("1.5"),
            is_buyer_maker=False,
            event_time=1234567890000,
            trade_id=1
        )
        self.book.current_vpin_bucket.add_trade(trade)
        
        # Проверяем заполнение
        fill = float(self.book.current_vpin_bucket.total_volume() / Decimal("10"))
        assert fill == 0.15  # 15%
        
        # VPIN должен игнорировать partial bucket
        vpin = self.analyzer.get_current_vpin()
        assert vpin is not None
        
        # Только historical: |5-5| = 0, volume = 100
        # VPIN = 0 / 100 = 0.0
        assert vpin == 0.0


# ===========================================================================
# TEST SUITE 3: VPIN Freshness Detection
# ===========================================================================

class TestVPINFreshness:
    """
    Проверяет, что get_vpin_status() корректно определяет stale VPIN.
    """
    
    def setup_method(self):
        """Создаем book и analyzer перед каждым тестом"""
        self.book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
        self.analyzer = FlowToxicityAnalyzer(
            book=self.book,
            bucket_size=Decimal("10")
        )
    
    def test_vpin_status_returns_dict(self):
        """Проверка структуры возвращаемого dict"""
        # Добавляем минимум корзин
        for i in range(10):
            bucket = VolumeBucket(
                bucket_size=Decimal("10"),
                symbol="BTCUSDT"
            )
            bucket.is_complete = True
            self.book.vpin_buckets.append(bucket)
        
        status = self.analyzer.get_vpin_status()
        
        assert 'vpin' in status
        assert 'is_stale' in status
        assert 'freshness' in status
        assert 'buckets_used' in status
    
    def test_fresh_vpin_not_stale(self):
        """
        Если последняя сделка <5 минут назад - VPIN свежий (is_stale=False).
        """
        # Создаем корзины
        for i in range(10):
            bucket = VolumeBucket(
                bucket_size=Decimal("10"),
                symbol="BTCUSDT"
            )
            bucket.is_complete = True
            self.book.vpin_buckets.append(bucket)
        
        # Добавляем current_bucket с недавней сделкой
        trade = TradeEvent(
            price=Decimal("100000"),
            quantity=Decimal("1.0"),
            is_buyer_maker=False,
            event_time=1234567890000,
            trade_id=1
        )
        self.book.current_vpin_bucket.add_trade(trade)
        
        # Проверяем сразу (freshness < 1 секунды)
        status = self.analyzer.get_vpin_status()
        
        assert status['is_stale'] == False
        assert status['freshness'] < 1.0
    
    def test_stale_vpin_after_5_minutes(self):
        """
        Если последняя сделка >5 минут назад - VPIN stale (is_stale=True).
        """
        # Создаем корзины с старым timestamp
        old_time = datetime.now() - timedelta(minutes=10)
        
        for i in range(10):
            bucket = VolumeBucket(
                bucket_size=Decimal("10"),
                symbol="BTCUSDT"
            )
            bucket.last_update_at = old_time
            bucket.is_complete = True
            self.book.vpin_buckets.append(bucket)
        
        # Устанавливаем старое время для current_bucket
        self.book.current_vpin_bucket.last_update_at = old_time
        
        # Проверяем через 10 минут
        current_time = datetime.now()
        status = self.analyzer.get_vpin_status(current_time)
        
        assert status['is_stale'] == True
        assert status['freshness'] > 300.0  # >5 минут
    
    def test_buckets_used_count(self):
        """
        Проверка корректности подсчета buckets_used.
        """
        # 10 исторических корзин
        for i in range(10):
            bucket = VolumeBucket(
                bucket_size=Decimal("10"),
                symbol="BTCUSDT"
            )
            bucket.is_complete = True
            self.book.vpin_buckets.append(bucket)
        
        # current_bucket заполнена на 30% (включится в расчет)
        for i in range(3):
            trade = TradeEvent(
                price=Decimal("100000"),
                quantity=Decimal("1.0"),
                is_buyer_maker=False,
                event_time=1234567890000 + i,
                trade_id=i
            )
            self.book.current_vpin_bucket.add_trade(trade)
        
        status = self.analyzer.get_vpin_status()
        
        # Должно быть 10 (historical) + 1 (current) = 11
        assert status['buckets_used'] == 11


# ===========================================================================
# TEST SUITE 4: Volume-Weighted VPIN Formula
# ===========================================================================

class TestVolumeWeightedVPIN:
    """
    Проверяет, что новая формула VPIN = Σ|OI_i| / ΣV_i работает корректно
    при смешивании полных и частичных корзин.
    """
    
    def setup_method(self):
        """Создаем book и analyzer перед каждым тестом"""
        self.book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
        self.analyzer = FlowToxicityAnalyzer(
            book=self.book,
            bucket_size=Decimal("10")
        )
    
    def test_old_formula_vs_new_formula(self):
        """
        Сравнение старой и новой формулы на полных корзинах.
        Результаты должны совпадать.
        """
        # Создаем 10 полных корзин с разными дисбалансами
        for i in range(10):
            bucket = VolumeBucket(
                bucket_size=Decimal("10"),
                symbol="BTCUSDT"
            )
            bucket.buy_volume = Decimal("7")
            bucket.sell_volume = Decimal("3")
            bucket.is_complete = True
            self.book.vpin_buckets.append(bucket)
        
        # Новая формула
        vpin_new = self.analyzer.get_current_vpin()
        
        # Старая формула (вручную)
        # VPIN_old = Σ|7-3| / (10 * 10) = 40 / 100 = 0.4
        vpin_old = 0.4
        
        assert abs(vpin_new - vpin_old) < 0.001
    
    def test_partial_bucket_weighted_correctly(self):
        """
        Проверка, что частичная корзина взвешивается корректно.
        """
        # 9 полных корзин (нейтральные)
        for i in range(9):
            bucket = VolumeBucket(
                bucket_size=Decimal("10"),
                symbol="BTCUSDT"
            )
            bucket.buy_volume = Decimal("5")
            bucket.sell_volume = Decimal("5")
            bucket.is_complete = True
            self.book.vpin_buckets.append(bucket)
        
        # 1 полная корзина с дисбалансом
        bucket_full = VolumeBucket(
            bucket_size=Decimal("10"),
            symbol="BTCUSDT"
        )
        bucket_full.buy_volume = Decimal("8")
        bucket_full.sell_volume = Decimal("2")
        bucket_full.is_complete = True
        self.book.vpin_buckets.append(bucket_full)
        
        # Частичная корзина (50% заполнения) с СИЛЬНЫМ дисбалансом
        for i in range(5):
            trade = TradeEvent(
                price=Decimal("100000"),
                quantity=Decimal("1.0"),
                is_buyer_maker=False,  # Все Buy
                event_time=1234567890000 + i,
                trade_id=i
            )
            self.book.current_vpin_bucket.add_trade(trade)
        
        # Расчет вручную:
        # 9 нейтральных: |5-5| = 0, volume = 90
        # 1 полная: |8-2| = 6, volume = 10
        # 1 частичная: |5-0| = 5, volume = 5
        # VPIN = (0 + 6 + 5) / (90 + 10 + 5) = 11 / 105 ≈ 0.1048
        
        vpin = self.analyzer.get_current_vpin()
        assert abs(vpin - 0.1048) < 0.01
    
    def test_mixed_bucket_sizes_handled(self):
        """
        Проверка обработки корзин разного размера (edge case).
        """
        # Создаем 10 корзин с разными размерами
        # (теоретически возможно если bucket_size менялся)
        for i in range(10):
            bucket = VolumeBucket(
                bucket_size=Decimal("10"),
                symbol="BTCUSDT"
            )
            # Заполняем на разные объемы
            bucket.buy_volume = Decimal(str(5 + i % 3))
            bucket.sell_volume = Decimal(str(5 - i % 3))
            bucket.is_complete = True
            self.book.vpin_buckets.append(bucket)
        
        # Проверяем что VPIN рассчитывается без ошибок
        vpin = self.analyzer.get_current_vpin()
        assert vpin is not None
        assert 0.0 <= vpin <= 1.0


# ===========================================================================
# TEST SUITE 5: Integration Test (End-to-End)
# ===========================================================================

class TestFrozenVPINIntegration:
    """
    Интеграционный тест: симуляция сценария "низкой ликвидности ночью".
    """
    
    def test_frozen_vpin_scenario(self):
        """
        Сценарий:
        1. Дневная торговля (10 корзин заполняются быстро)
        2. Ночь - торговля замирает (current_bucket не закрывается)
        3. Проверяем что VPIN помечается как stale
        """
        book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
        analyzer = FlowToxicityAnalyzer(book=book, bucket_size=Decimal("10"))
        
        # === ФАЗА 1: ДНЕВНАЯ ТОРГОВЛЯ ===
        # Создаем 10 полных корзин (историческая активность)
        day_time = datetime(2025, 1, 1, 14, 0, 0)  # 14:00
        
        for i in range(10):
            bucket = VolumeBucket(
                bucket_size=Decimal("10"),
                symbol="BTCUSDT"
            )
            bucket.buy_volume = Decimal("6")
            bucket.sell_volume = Decimal("4")
            bucket.is_complete = True
            bucket.created_at = day_time + timedelta(minutes=i)
            bucket.last_update_at = day_time + timedelta(minutes=i)
            book.vpin_buckets.append(bucket)
        
        # VPIN днем должен быть свежим
        status_day = analyzer.get_vpin_status(current_time=day_time + timedelta(minutes=10))
        assert status_day['is_stale'] == False
        assert status_day['vpin'] is not None
        
        # === ФАЗА 2: НОЧЬ - ТОРГОВЛЯ ЗАМИРАЕТ ===
        # Последняя корзина закрылась в 14:10
        # Текущая корзина заполнена только на 10% и не обновлялась
        night_time = datetime(2025, 1, 1, 14, 50, 0)  # 14:50 (40 минут спустя)
        
        # Добавляем 1 сделку в current_bucket (10% заполнения)
        trade = TradeEvent(
            price=Decimal("100000"),
            quantity=Decimal("1.0"),
            is_buyer_maker=False,
            event_time=int(day_time.timestamp() * 1000),
            trade_id=1
        )
        book.current_vpin_bucket.add_trade(trade)
        book.current_vpin_bucket.last_update_at = day_time + timedelta(minutes=10)
        
        # === ПРОВЕРКА: VPIN должен быть STALE ===
        status_night = analyzer.get_vpin_status(current_time=night_time)
        
        assert status_night['is_stale'] == True  # >5 минут простоя
        assert status_night['freshness'] > 300.0  # >300 секунд
        assert status_night['vpin'] is not None  # Значение все еще есть
        
        # === ВЫВОД: ML модель должна игнорировать этот VPIN ===
        print(f"\n[FROZEN VPIN SCENARIO]")
        print(f"Day VPIN: {status_day['vpin']:.3f} (fresh)")
        print(f"Night VPIN: {status_night['vpin']:.3f} (stale, freshness={status_night['freshness']:.0f}s)")
        print(f"✅ ML model will ignore stale VPIN")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
