"""
WHY: Тесты для Phase 2 - CVD Enhancement.

Проверяет интеграцию CVD дивергенций с IcebergAnalyzer.
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from domain import LocalOrderBook, TradeEvent, GammaProfile
from analyzers import IcebergAnalyzer
from config import BTC_CONFIG


@pytest.mark.skip(reason="Group 2: Refactoring pending - iceberg logic будет переписан")
class TestCVDEnhancement:
    """
    WHY: Тестируем Phase 2 - интеграцию CVD дивергенций с confidence adjustment.
    """
    
    def test_helper_methods_get_latest_cvd(self):
        """
        WHY: Проверяем helper методы для получения CVD.
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        
        # Добавляем данные в historical_memory
        now = datetime.now()
        book.historical_memory.update_history(
            timestamp=now,
            whale_cvd=10000.0,
            minnow_cvd=-5000.0,
            price=Decimal("100000")
        )
        
        # Проверяем get_latest_cvd
        whale_cvd = book.get_latest_cvd(timeframe='1h', cohort='whale')
        minnow_cvd = book.get_latest_cvd(timeframe='1h', cohort='minnow')
        
        assert whale_cvd == 10000.0
        assert minnow_cvd == -5000.0
    
    def test_helper_methods_get_cvd_change(self):
        """
        WHY: Проверяем расчёт изменения CVD за N периодов.
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        
        # Добавляем 3 точки данных
        now = datetime.now()
        for i in range(3):
            book.historical_memory.update_history(
                timestamp=now + timedelta(hours=i),
                whale_cvd=10000.0 + i * 1000.0,  # CVD растёт
                minnow_cvd=-5000.0,
                price=Decimal("100000")
            )
        
        # Проверяем изменение за 3 периода
        cvd_change = book.get_cvd_change(timeframe='1h', cohort='whale', periods=3)
        
        # Должен быть рост на 2000 (от 10000 до 12000)
        assert cvd_change == 2000.0
    
    def test_bullish_divergence_boosts_bid_iceberg(self):
        """
        WHY: BULLISH divergence (цена ↓, whale CVD ↑) + BID айсберг → confidence УСИЛИВАЕТСЯ.
        
        Scenario:
        - base_confidence = 0.5
        - BULLISH divergence (confidence 0.8)
        - Айсберг на BID (поддержка)
        Expected: confidence *= 1.0 + (0.8 * 0.25) = 1.0 + 0.2 = 1.2 → capped at 1.0
        """
        analyzer = IcebergAnalyzer(config=BTC_CONFIG)
        
        # CVD divergence: BULLISH с высокой confidence
        cvd_div = (True, 'BULLISH', 0.8)
        
        adjusted, is_major = analyzer.adjust_confidence_by_gamma(
            base_confidence=0.5,
            gamma_profile=None,  # Без GEX
            price=Decimal("100000"),
            is_ask=False,  # BID айсберг (поддержка)
            vpin_score=None,  # Без VPIN
            cvd_divergence=cvd_div
        )
        
        # Проверяем что confidence увеличился
        assert adjusted > 0.5, "BULLISH divergence должен усилить BID айсберг"
        assert is_major == True, "CVD divergence = major event"
    
    def test_bearish_divergence_boosts_ask_iceberg(self):
        """
        WHY: BEARISH divergence (цена ↑, whale CVD ↓) + ASK айсберг → confidence УСИЛИВАЕТСЯ.
        """
        analyzer = IcebergAnalyzer(config=BTC_CONFIG)
        
        cvd_div = (True, 'BEARISH', 0.8)
        
        adjusted, is_major = analyzer.adjust_confidence_by_gamma(
            base_confidence=0.5,
            gamma_profile=None,
            price=Decimal("100000"),
            is_ask=True,  # ASK айсберг (сопротивление)
            vpin_score=None,
            cvd_divergence=cvd_div
        )
        
        assert adjusted > 0.5, "BEARISH divergence должен усилить ASK айсберг"
        assert is_major == True
    
    def test_bullish_divergence_weakens_ask_iceberg(self):
        """
        WHY: BULLISH divergence + ASK айсберг (противоречие) → confidence СНИЖАЕТСЯ.
        
        Scenario:
        - Киты покупают (whale CVD ↑), но айсберг на ASK (продажа)
        - Это противоречие → снижаем confidence
        """
        analyzer = IcebergAnalyzer(config=BTC_CONFIG)
        
        cvd_div = (True, 'BULLISH', 0.8)
        
        adjusted, is_major = analyzer.adjust_confidence_by_gamma(
            base_confidence=0.5,
            gamma_profile=None,
            price=Decimal("100000"),
            is_ask=True,  # ASK айсберг (противоречие!)
            vpin_score=None,
            cvd_divergence=cvd_div
        )
        
        assert adjusted < 0.5, "BULLISH + ASK = противоречие → confidence снижается"
        assert is_major == False, "Противоречие не является major event"
    
    def test_bearish_divergence_weakens_bid_iceberg(self):
        """
        WHY: BEARISH divergence + BID айсберг (противоречие) → confidence СНИЖАЕТСЯ.
        """
        analyzer = IcebergAnalyzer(config=BTC_CONFIG)
        
        cvd_div = (True, 'BEARISH', 0.8)
        
        adjusted, is_major = analyzer.adjust_confidence_by_gamma(
            base_confidence=0.5,
            gamma_profile=None,
            price=Decimal("100000"),
            is_ask=False,  # BID айсберг (противоречие!)
            vpin_score=None,
            cvd_divergence=cvd_div
        )
        
        assert adjusted < 0.5, "BEARISH + BID = противоречие → confidence снижается"
        assert is_major == False
    
    def test_low_confidence_divergence_ignored(self):
        """
        WHY: Дивергенция с низкой confidence (<0.5) игнорируется.
        
        Scenario:
        - CVD divergence confidence = 0.3 (слабая)
        - Не должна влиять на айсберг
        """
        analyzer = IcebergAnalyzer(config=BTC_CONFIG)
        
        cvd_div = (True, 'BULLISH', 0.3)  # Слабая confidence
        
        adjusted, is_major = analyzer.adjust_confidence_by_gamma(
            base_confidence=0.5,
            gamma_profile=None,
            price=Decimal("100000"),
            is_ask=False,
            vpin_score=None,
            cvd_divergence=cvd_div
        )
        
        # Должно остаться без изменений
        assert adjusted == 0.5, "Слабая divergence должна игнорироваться"
        assert is_major == False
    
    def test_three_phase_adjustment_gex_vpin_cvd(self):
        """
        WHY: Проверяем работу всех трёх фаз adjustment одновременно.
        
        Scenario:
        - GEX: +GEX, on gamma wall → x1.8
        - VPIN: 0.2 (шумный поток) → x1.13
        - CVD: BULLISH + BID → x1.2
        - Total: 0.5 * 1.8 * 1.13 * 1.2 = 1.22 → capped at 1.0
        """
        analyzer = IcebergAnalyzer(config=BTC_CONFIG)
        
        # +GEX профиль с gamma wall на 100000
        gamma = GammaProfile(
            total_gex=5000.0,
            call_wall=105000.0,
            put_wall=100000.0  # Айсберг будет здесь
        )
        
        cvd_div = (True, 'BULLISH', 0.8)
        
        adjusted, is_major = analyzer.adjust_confidence_by_gamma(
            base_confidence=0.5,
            gamma_profile=gamma,
            price=Decimal("100000"),  # На put wall
            is_ask=False,  # BID
            vpin_score=0.2,  # Шумный поток
            cvd_divergence=cvd_div
        )
        
        # Проверяем что все фазы отработали
        assert adjusted > 0.9, "Все 3 фазы должны усилить confidence"
        assert is_major == True, "Gamma wall + CVD = major event"
    
    def test_cvd_divergence_none_is_safe(self):
        """
        WHY: cvd_divergence=None не ломает функцию.
        """
        analyzer = IcebergAnalyzer(config=BTC_CONFIG)
        
        adjusted, is_major = analyzer.adjust_confidence_by_gamma(
            base_confidence=0.5,
            gamma_profile=None,
            price=Decimal("100000"),
            is_ask=False,
            vpin_score=None,
            cvd_divergence=None  # Нет CVD данных
        )
        
        # Должно работать без ошибок
        assert adjusted == 0.5
        assert is_major == False
