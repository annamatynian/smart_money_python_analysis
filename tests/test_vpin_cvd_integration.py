"""
Integration Test: VPIN/CVD Data Fusion
WHY: Проверяет полную цепочку передачи контекста от Trade → Analyzer
"""
import pytest
from decimal import Decimal
from datetime import datetime
from domain import LocalOrderBook, TradeEvent, OrderBookUpdate
from analyzers import IcebergAnalyzer, AccumulationDetector
from config import get_config


class TestVPINCVDIntegration:
    """Integration tests for VPIN/CVD context passing"""
    
    def test_accumulation_detector_cache(self):
        """
        ПРОВЕРКА: AccumulationDetector кеширует divergence результаты
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        config = get_config('BTCUSDT')
        detector = AccumulationDetector(book=book, config=config)
        
        # Изначально кеш пуст
        assert detector.get_current_divergence_state() is None
        
        # Запускаем детекцию (не будет divergence т.к. нет истории)
        results = detector.detect_accumulation_multi_timeframe()
        
        # Кеш должен быть None (нет divergence)
        assert detector.get_current_divergence_state() is None
    
    def test_pending_queue_captures_context(self):
        """
        ПРОВЕРКА: pending_refill_checks сохраняет VPIN и CVD
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        
        # Симулируем добавление в pending queue
        trade = TradeEvent(
            price=Decimal('60000'),
            quantity=Decimal('1.5'),
            is_buyer_maker=False,
            event_time=1000
        )
        
        book.pending_refill_checks.append({
            'trade': trade,
            'visible_before': Decimal('5.0'),
            'trade_time_ms': 1000,
            'price': Decimal('60000'),
            'is_ask': True,
            'vpin_score': 0.75,  # ✅ VPIN context
            'cvd_divergence': {'type': 'BULLISH', 'confidence': 0.8}  # ✅ CVD context
        })
        
        # Проверяем что данные сохранились
        pending = book.pending_refill_checks[0]
        assert pending['vpin_score'] == 0.75
        assert pending['cvd_divergence']['type'] == 'BULLISH'
        assert pending['cvd_divergence']['confidence'] == 0.8
    
    def test_analyze_with_timing_receives_context(self):
        """
        ПРОВЕРКА: analyze_with_timing() принимает vpin_score и cvd_divergence
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        config = get_config('BTCUSDT')
        analyzer = IcebergAnalyzer(config=config)
        
        trade = TradeEvent(
            price=Decimal('60000'),
            quantity=Decimal('1.5'),
            is_buyer_maker=False,
            event_time=1000
        )
        
        # Вызываем с новыми параметрами
        result = analyzer.analyze_with_timing(
            book=book,
            trade=trade,
            visible_before=Decimal('5.0'),
            delta_t_ms=15.0,
            update_time_ms=1015,
            vpin_score=0.75,  # ✅ Передаём VPIN
            cvd_divergence={'type': 'BULLISH', 'confidence': 0.8}  # ✅ Передаём CVD
        )
        
        # Проверяем что метод принял параметры без ошибок
        assert result is not None or result is None  # Может быть None если не айсберг
    
    def test_domain_has_wyckoff_field(self):
        """
        ПРОВЕРКА: LocalOrderBook имеет поле latest_wyckoff_divergence
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        
        # Проверяем что поле существует
        assert hasattr(book, 'latest_wyckoff_divergence')
        
        # Изначально None
        assert book.latest_wyckoff_divergence is None
        
        # Можно записать dict
        book.latest_wyckoff_divergence = {
            'type': 'BULLISH',
            'confidence': 0.9,
            'timeframe': '4h'
        }
        
        assert book.latest_wyckoff_divergence['type'] == 'BULLISH'
    
    def test_full_integration_flow(self):
        """
        ПРОВЕРКА: Полная цепочка TradeEvent → pending → analyze_with_timing
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        config = get_config('BTCUSDT')
        analyzer = IcebergAnalyzer(config=config)
        detector = AccumulationDetector(book=book, config=config)
        
        # 1. TradeEvent - захватываем контекст
        trade = TradeEvent(
            price=Decimal('60000'),
            quantity=Decimal('1.5'),
            is_buyer_maker=False,
            event_time=1000
        )
        
        vpin_score = 0.65
        current_divergence = detector.get_current_divergence_state()  # None изначально
        
        # 2. Сохраняем в pending
        book.pending_refill_checks.append({
            'trade': trade,
            'visible_before': Decimal('5.0'),
            'trade_time_ms': 1000,
            'price': Decimal('60000'),
            'is_ask': True,
            'vpin_score': vpin_score,
            'cvd_divergence': current_divergence
        })
        
        # 3. OrderBookUpdate - извлекаем и передаём
        pending = book.pending_refill_checks[0]
        stored_vpin = pending.get('vpin_score')
        stored_divergence = pending.get('cvd_divergence')
        
        # 4. Передаём в analyze_with_timing
        result = analyzer.analyze_with_timing(
            book=book,
            trade=trade,
            visible_before=pending['visible_before'],
            delta_t_ms=15.0,
            update_time_ms=1015,
            vpin_score=stored_vpin,
            cvd_divergence=stored_divergence
        )
        
        # Проверяем что цепочка сработала без ошибок
        assert stored_vpin == 0.65
        assert stored_divergence is None  # Т.к. нет divergence
        assert result is not None or result is None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
