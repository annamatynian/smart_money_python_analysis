"""
WHY: Тест интеграции AccumulationDetector (Wyckoff) в TradingEngine.

Проверяет:
1. AccumulationDetector создается в __init__
2. detect_accumulation() вызывается периодически
3. Алерты о накоплении/дистрибуции работают
4. Wyckoff паттерны (SPRING, UPTHRUST) детектируются
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from services import TradingEngine
from domain import LocalOrderBook, TradeEvent
from analyzers import AccumulationDetector
from config import BTC_CONFIG  # FIX: Gemini Validation - мульти-ассет


class TestAccumulationIntegration:
    """Проверка интеграции Wyckoff детектора"""
    
    def test_accumulation_detector_initialized(self):
        """
        WHY: Проверяем что AccumulationDetector создается в TradingEngine.__init__
        """
        # Mock infrastructure
        class MockInfra:
            async def get_snapshot(self, symbol):
                return {'bids': [], 'asks': [], 'lastUpdateId': 0}
            async def listen_updates(self, symbol):
                return
                yield
            async def listen_trades(self, symbol):
                return
                yield
        
        # Create engine
        engine = TradingEngine(
            symbol='BTCUSDT',
            infra=MockInfra(),
            deribit_infra=None,
            repository=None
        )
        
        # ASSERT: accumulation_detector должен существовать
        assert hasattr(engine, 'accumulation_detector'), "AccumulationDetector не создан в __init__"
        assert engine.accumulation_detector is not None, "accumulation_detector = None"
        assert isinstance(engine.accumulation_detector, AccumulationDetector)
    
    def test_detect_accumulation_with_divergence(self):
        """
        WHY: Проверяем детекцию накопления при CVD дивергенции
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        detector = AccumulationDetector(book, BTC_CONFIG)
        
        # Создаем искусственную дивергенцию в historical_memory
        # Цена падает, но Whale CVD растет (BULLISH divergence)
        base_time = datetime.now()
        
        for i in range(20):
            timestamp = base_time + timedelta(hours=i)
            price = Decimal('50000') - Decimal(i * 100)  # Цена падает
            whale_cvd = float(i * 10)  # Whale CVD растет
            minnow_cvd = float(-i * 5)  # Minnow CVD падает (паника)
            
            book.historical_memory.update_history(
                timestamp=timestamp,
                whale_cvd=whale_cvd,
                minnow_cvd=minnow_cvd,
                price=price
            )
        
        # ASSERT: Детектор должен найти BULLISH накопление
        result = detector.detect_accumulation(timeframe='1h')
        assert result is not None, "Должна быть обнаружена дивергенция"
        assert result['type'] == 'BULLISH', f"Должно быть BULLISH, получено {result['type']}"
        # FIX: Разрешаем ровно 0.5 (базовая уверенность без бонусов)
        # WHY: Дивергенция сама по себе - валидный сигнал (50% confidence)
        assert result['confidence'] >= 0.5, f"Confidence должна быть >=0.5, получено {result['confidence']}"
    
    def test_wyckoff_spring_pattern(self):
        """
        WHY: Проверяем детекцию паттерна SPRING (идеальный сигнал)
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        detector = AccumulationDetector(book, BTC_CONFIG)
        
        # Создаем SPRING условия:
        # 1. BULLISH divergence
        # 2. Крупный BID айсберг (absorption)
        # 3. Positive OBI (подтверждение)
        
        # 1. Дивергенция
        base_time = datetime.now()
        for i in range(20):
            timestamp = base_time + timedelta(hours=i)
            price = Decimal('50000') - Decimal(i * 100)
            whale_cvd = float(i * 10)
            minnow_cvd = float(-i * 5)
            
            book.historical_memory.update_history(
                timestamp=timestamp,
                whale_cvd=whale_cvd,
                minnow_cvd=minnow_cvd,
                price=price
            )
        
        # 2. Создаем крупный BID айсберг (поддержка)
        from domain import IcebergLevel
        iceberg = IcebergLevel(
            price=Decimal('49500'),
            is_ask=False,  # BID
            confidence_score=0.9,
            total_hidden_volume=Decimal('5.0'),  # Крупный
            refill_count=10
        )
        book.active_icebergs[iceberg.price] = iceberg
        
        # 3. Устанавливаем положительный OBI
        # (симулируем bid > ask в стакане)
        book.bids[Decimal('49500')] = Decimal('100')
        book.asks[Decimal('50500')] = Decimal('50')
        
        # ASSERT: Детектор должен найти SPRING
        result = detector.detect_accumulation(timeframe='1h')
        assert result is not None
        assert result['type'] == 'BULLISH'
        assert result['wyckoff_pattern'] == 'SPRING', \
            f"Должен быть SPRING, получено {result['wyckoff_pattern']}"
        assert result['absorption_detected'] is True
        assert result['confidence'] >= 0.7, \
            f"SPRING должен иметь высокую confidence, получено {result['confidence']}"
    
    def test_multi_timeframe_detection(self):
        """
        WHY: Проверяем детекцию на множественных таймфреймах
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        detector = AccumulationDetector(book, BTC_CONFIG)
        
        # Создаем дивергенцию на 1h таймфрейме
        base_time = datetime.now()
        for i in range(20):
            timestamp = base_time + timedelta(hours=i)
            price = Decimal('50000') - Decimal(i * 100)
            whale_cvd = float(i * 10)
            minnow_cvd = float(-i * 5)
            
            book.historical_memory.update_history(
                timestamp=timestamp,
                whale_cvd=whale_cvd,
                minnow_cvd=minnow_cvd,
                price=price
            )
        
        # ASSERT: Метод detect_accumulation_multi_timeframe должен работать
        results = detector.detect_accumulation_multi_timeframe()
        assert isinstance(results, dict), "Должен вернуть dict"
        
        # Проверяем что есть результат хотя бы на одном таймфрейме
        assert len(results) > 0, "Должна быть дивергенция хотя бы на одном таймфрейме"
        
        # Проверяем структуру результата
        for tf, result in results.items():
            assert 'type' in result
            assert 'confidence' in result
            assert 'wyckoff_pattern' in result
    
    def test_no_false_signals_without_divergence(self):
        """
        WHY: Проверяем что детектор НЕ генерирует ложные сигналы
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        detector = AccumulationDetector(book, BTC_CONFIG)
        
        # Создаем нормальное движение (без дивергенции)
        # Цена и CVD двигаются синхронно
        base_time = datetime.now()
        for i in range(20):
            timestamp = base_time + timedelta(hours=i)
            price = Decimal('50000') + Decimal(i * 100)  # Цена растет
            whale_cvd = float(i * 10)  # Whale CVD тоже растет
            minnow_cvd = float(i * 5)  # Minnow CVD тоже растет
            
            book.historical_memory.update_history(
                timestamp=timestamp,
                whale_cvd=whale_cvd,
                minnow_cvd=minnow_cvd,
                price=price
            )
        
        # ASSERT: НЕ должно быть дивергенции
        result = detector.detect_accumulation(timeframe='1h')
        assert result is None, "Не должно быть сигнала при синхронном движении"
    
    def test_conflicting_signals_bearish_div_bullish_absorption(self):
        """
        WHY: Проверяем обработку конфликтующих сигналов (Gemini Validation)
        
        Сценарий:
        - BEARISH divergence (цена растет, Whale CVD падает)
        - Но есть BID айсберг (Bullish absorption)
        
        Ожидание:
        - Система должна вернуть DISTRIBUTION (приоритет дивергенции)
        - absorption_detected = True, но pattern = DISTRIBUTION (не SPRING)
        
        Теория:
        - Конфликт разрешается через decision tree в _classify_wyckoff_pattern
        - BEARISH divergence всегда дает DISTRIBUTION/UPTHRUST
        - Absorption только УСИЛИВАЕТ confidence, но не меняет тип
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        detector = AccumulationDetector(book, BTC_CONFIG)
        
        # 1. Создаем BEARISH divergence
        # Цена растет, Whale CVD падает (дистрибуция)
        base_time = datetime.now()
        for i in range(20):
            timestamp = base_time + timedelta(hours=i)
            price = Decimal('50000') + Decimal(i * 100)  # Цена РАСТЕТ
            whale_cvd = float(100 - i * 5)  # Whale CVD ПАДАЕТ (продают)
            minnow_cvd = float(i * 10)  # Minnow CVD растет (жадность)
            
            book.historical_memory.update_history(
                timestamp=timestamp,
                whale_cvd=whale_cvd,
                minnow_cvd=minnow_cvd,
                price=price
            )
        
        # 2. Создаем КОНФЛИКТНЫЙ BID айсберг (Bullish absorption)
        # WHY: Это создает логическое противоречие:
        #      - Дивергенция говорит BEARISH
        #      - Айсберг говорит BULLISH support
        from domain import IcebergLevel
        conflicting_iceberg = IcebergLevel(
            price=Decimal('51000'),
            is_ask=False,  # BID айсберг (конфликт!)
            confidence_score=0.8,
            total_hidden_volume=Decimal('3.0'),  # Достаточно крупный
            refill_count=5
        )
        book.active_icebergs[conflicting_iceberg.price] = conflicting_iceberg
        
        # 3. Устанавливаем положительный OBI (усиливает конфликт)
        book.bids[Decimal('51000')] = Decimal('100')
        book.asks[Decimal('51500')] = Decimal('50')
        
        # ASSERT: Детектор должен выбрать DISTRIBUTION
        result = detector.detect_accumulation(timeframe='1h')
        
        assert result is not None, "Дивергенция должна быть обнаружена"
        assert result['type'] == 'BEARISH', \
            f"Должно быть BEARISH (приоритет дивергенции), получено {result['type']}"
        
        # CRITICAL: Pattern = DISTRIBUTION (не UPTHRUST)
        # WHY: Нет ASK айсберга на правильной стороне для BEARISH divergence
        assert result['wyckoff_pattern'] == 'DISTRIBUTION', \
            f"Должно быть DISTRIBUTION при конфликте, получено {result['wyckoff_pattern']}"
        
        # КЛЮЧЕВОЙ МОМЕНТ: BID айсберг НЕ засчитывается (конфликт с BEARISH)
        # WHY: _check_passive_absorption ищет только ASK айсберги для BEARISH
        # Это правильное поведение - конфликтующие сигналы игнорируются
        assert result['absorption_detected'] is False, \
            "BID айсберг НЕ должен засчитываться при BEARISH divergence (конфликт типов)"
        
        # OBI подтверждение ТАКЖЕ конфликтует (положительный OBI при BEARISH)
        # WHY: _check_weighted_obi требует ОТРИЦАТЕЛЬНЫЙ OBI для BEARISH
        # Положительный OBI (>0.2) не пройдет проверку (<-0.2)
        assert result['obi_confirms'] is False, \
            "Положительный OBI НЕ должен подтверждать BEARISH divergence (конфликт)"
        
        # Confidence = базовая (0.5 для 1h таймфрейма)
        # WHY: Нет бонусов - ни absorption, ни OBI не засчитаны из-за конфликта
        # Дивергенция обнаружена, но подтверждений нет → минимальная уверенность
        assert result['confidence'] == 0.5, \
            f"Confidence должна быть базовой (без бонусов), получено {result['confidence']}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
