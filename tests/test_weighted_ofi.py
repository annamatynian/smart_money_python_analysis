"""
Test Weighted OFI (Order Flow Imbalance) - Fix for Depth Bias Vulnerability

WHY: Уязвимость Б (Gemini Validation) - L1 Bias / No Decay
- Проблема: Все уровни стакана имеют одинаковый вес
- Спуф на L10 ($200 от mid) = реальная ликвидность на L1 ($1 от mid)
- Решение: Экспоненциальные веса по глубине

Теория:
- OFI_weighted = Σ (OFI_i × e^(-λ × distance_pct))
- λ (lambda) из config (BTC=0.1, ETH=0.15)
- Дальние уровни затухают быстрее → фильтруем спуфинг
"""

import pytest
from decimal import Decimal
from domain import LocalOrderBook, OrderBookUpdate
from config import AssetConfig


class TestWeightedOFI:
    """
    Test Suite для Weighted OFI (экспоненциальное затухание по глубине)
    """
    
    def test_unweighted_ofi_treats_all_levels_equally(self):
        """
        СЦЕНАРИЙ: Спуф на дальних уровнях влияет на OFI так же сильно как реальная ликвидность.
        
        SETUP:
        - L1: +1 BTC на bid (реальная ликвидность у mid)
        - L10: +100 BTC на bid (спуф далеко от mid)
        
        БЕЗ ВЕСОВ: OFI = +101 BTC (спуф доминирует)
        С ВЕСАМИ: OFI ≈ +1 BTC (спуф отфильтрован)
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        
        # 1. Снапшот: Пустой стакан
        book.apply_snapshot(
            bids=[],
            asks=[],
            last_update_id=100
        )
        
        # 2. Update: Добавляем L1 (реальная ликвидность) + L10 (спуф)
        # WHY: Добавляем asks для расчёта mid_price (иначе weighted OFI отключится!)
        mid_price = Decimal('60000')
        
        # L1: +1 BTC на 59999 (0.00167% от mid)
        # L10: +100 BTC на 59400 (1.0% от mid - спуф)
        # Ask: +0.1 BTC на 60001 (для mid_price)
        update = OrderBookUpdate(
            bids=[
                (Decimal('59999'), Decimal('1.0')),   # L1 - реальная
                (Decimal('59400'), Decimal('100.0'))  # L10 - спуф
            ],
            asks=[
                (Decimal('60001'), Decimal('0.1'))  # ← Для mid_price!
            ],
            first_update_id=101,
            final_update_id=101,
            event_time=1000000
        )
        book.apply_update(update)
        
        # 3. UNWEIGHTED OFI (текущая реализация)
        ofi_unweighted = book.calculate_ofi(depth=20, use_weighted=False)
        
        # Ожидание: +101 BTC (оба уровня равны)
        assert abs(ofi_unweighted - 101.0) < 0.1
        
        # 4. WEIGHTED OFI (новая реализация)
        ofi_weighted = book.calculate_ofi(depth=20, use_weighted=True)
        
        # Ожидание: ~1.0 BTC (спуф отфильтрован exponential decay)
        # L1: 1.0 × e^(-10.0 × 0.00167) ≈ 1.0 × 0.983 ≈ 0.983
        # L10: 100 × e^(-10.0 × 1.0) ≈ 100 × 0.000045 ≈ 0.0045
        # Total: ~0.98 BTC (доминирует L1!)
        assert 0.8 < ofi_weighted < 1.2  # Спуф отфильтрован!
        
        print(f"✅ Unweighted OFI: {ofi_unweighted:.2f} BTC (спуф доминирует)")
        print(f"✅ Weighted OFI: {ofi_weighted:.2f} BTC (спуф отфильтрован)")
    
    def test_weighted_ofi_respects_config_lambda(self):
        """
        СЦЕНАРИЙ: Разные токены (BTC vs ETH) используют разные λ для decay.
        
        Теория:
        - BTC: λ=0.1 (медленное затухание, менее волатилен)
        - ETH: λ=0.15 (быстрое затухание, более волатилен)
        """
        # BTC Book (λ=0.1)
        book_btc = LocalOrderBook(symbol='BTCUSDT')
        book_btc.config.lambda_decay = 0.1
        
        book_btc.apply_snapshot(bids=[], asks=[], last_update_id=100)
        
        # Добавляем спуф на 0.5% от mid
        update = OrderBookUpdate(
            bids=[(Decimal('59700'), Decimal('50.0'))],  # 0.5% от 60000
            asks=[(Decimal('60300'), Decimal('0.1'))],   # ← Для mid_price
            first_update_id=101,
            final_update_id=101,
            event_time=1000000
        )
        book_btc.apply_update(update)
        
        ofi_btc = book_btc.calculate_ofi(depth=20, use_weighted=True)
        
        # ETH Book (λ=0.15 - более агрессивное затухание)
        book_eth = LocalOrderBook(symbol='ETHUSDT')
        book_eth.config.lambda_decay = 0.15
        
        book_eth.apply_snapshot(bids=[], asks=[], last_update_id=100)
        
        # Та же дистанция (0.5% от mid)
        eth_mid = Decimal('3000')
        update_eth = OrderBookUpdate(
            bids=[(Decimal('2985'), Decimal('50.0'))],  # 0.5% от 3000
            asks=[(Decimal('3015'), Decimal('0.1'))],   # ← Для mid_price
            first_update_id=101,
            final_update_id=101,
            event_time=1000000
        )
        book_eth.apply_update(update_eth)
        
        ofi_eth = book_eth.calculate_ofi(depth=20, use_weighted=True)
        
        # ETH должен сильнее отфильтровать (λ выше)
        # weight_btc = e^(-0.1 × 100 × 0.5) ≈ e^(-5.0) ≈ 0.0067
        # weight_eth = e^(-0.15 × 100 × 0.5) ≈ e^(-7.5) ≈ 0.00055
        assert ofi_eth < ofi_btc  # ETH фильтрует агрессивнее
        
        print(f"✅ BTC OFI (λ=0.1): {ofi_btc:.4f}")
        print(f"✅ ETH OFI (λ=0.15): {ofi_eth:.4f} (сильнее фильтрация)")
    
    def test_weighted_ofi_preserves_sign_direction(self):
        """
        СЦЕНАРИЙ: Weighted OFI сохраняет знак (направление давления).
        
        - Добавление bid ликвидности → OFI > 0 (покупатели)
        - Добавление ask ликвидности → OFI < 0 (продавцы)
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        book.apply_snapshot(bids=[], asks=[], last_update_id=100)
        
        # 1. Test BID pressure
        update_bid = OrderBookUpdate(
            bids=[(Decimal('59999'), Decimal('5.0'))],
            asks=[(Decimal('60001'), Decimal('0.1'))],  # ← Для mid_price
            first_update_id=101,
            final_update_id=101,
            event_time=1000000
        )
        book.apply_update(update_bid)
        
        ofi_bid = book.calculate_ofi(depth=20, use_weighted=True)
        assert ofi_bid > 0  # Положительный (покупатели)
        
        # 2. Reset и test ASK pressure
        book.apply_snapshot(bids=[], asks=[], last_update_id=102)
        
        update_ask = OrderBookUpdate(
            bids=[(Decimal('59999'), Decimal('0.1'))],  # ← Для mid_price
            asks=[(Decimal('60001'), Decimal('5.0'))],
            first_update_id=103,
            final_update_id=103,
            event_time=2000000
        )
        book.apply_update(update_ask)
        
        ofi_ask = book.calculate_ofi(depth=20, use_weighted=True)
        assert ofi_ask < 0  # Отрицательный (продавцы)
        
        print(f"✅ BID OFI: {ofi_bid:.2f} (положительный)")
        print(f"✅ ASK OFI: {ofi_ask:.2f} (отрицательный)")
    
    def test_weighted_ofi_backward_compatible(self):
        """
        СЦЕНАРИЙ: Параметр use_weighted=False возвращает старую логику.
        
        WHY: Обратная совместимость для существующих систем.
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        book.apply_snapshot(bids=[], asks=[], last_update_id=100)
        
        update = OrderBookUpdate(
            bids=[(Decimal('59999'), Decimal('10.0'))],
            asks=[(Decimal('60001'), Decimal('0.1'))],  # ← Для mid_price
            first_update_id=101,
            final_update_id=101,
            event_time=1000000
        )
        book.apply_update(update)
        
        # Старая логика (без весов)
        ofi_old = book.calculate_ofi(depth=20, use_weighted=False)
        
        # Новая логика (с весами) должна быть близка (для L1)
        ofi_new = book.calculate_ofi(depth=20, use_weighted=True)
        
        # Для L1 (близко к mid) разница минимальна
        assert abs(ofi_old - ofi_new) < 0.5  # <5% разница для L1
        
        print(f"✅ Legacy OFI: {ofi_old:.2f}")
        print(f"✅ Weighted OFI: {ofi_new:.2f}")
    
    def test_weighted_ofi_filters_deep_spoofing(self):
        """
        СЦЕНАРИЙ: Реальный case - маркет-мейкер ставит стену на $500 от mid (спуф).
        
        SETUP:
        - Mid: $60,000
        - L1-L5: Реальная ликвидность (~10 BTC total, <$50 от mid)
        - L15: Спуф 200 BTC на $59,500 (0.83% от mid)
        
        БЕЗ ВЕСОВ: OFI = +210 BTC (спуф доминирует)
        С ВЕСАМИ: OFI ≈ +10 BTC (спуф отфильтрован)
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        book.apply_snapshot(bids=[], asks=[], last_update_id=100)
        
        # Реальная ликвидность L1-L5 (близко к mid)
        real_liquidity_bids = [
            (Decimal('59999'), Decimal('2.0')),   # $1 от mid
            (Decimal('59995'), Decimal('2.0')),   # $5 от mid
            (Decimal('59990'), Decimal('2.0')),   # $10 от mid
            (Decimal('59980'), Decimal('2.0')),   # $20 от mid
            (Decimal('59950'), Decimal('2.0'))    # $50 от mid
        ]
        
        # Спуф на L15 (далеко от mid)
        spoof_bid = (Decimal('59500'), Decimal('200.0'))  # $500 от mid (~0.83%)
        
        update = OrderBookUpdate(
            bids=real_liquidity_bids + [spoof_bid],
            asks=[(Decimal('60001'), Decimal('0.1'))],  # ← Для mid_price
            first_update_id=101,
            final_update_id=101,
            event_time=1000000
        )
        book.apply_update(update)
        
        # Unweighted: спуф доминирует
        ofi_unweighted = book.calculate_ofi(depth=20, use_weighted=False)
        assert ofi_unweighted > 200  # ~210 BTC
        
        # Weighted: спуф отфильтрован
        ofi_weighted = book.calculate_ofi(depth=20, use_weighted=True)
        assert 7 < ofi_weighted < 13  # ~10 BTC (только реальная ликвидность, ±30% tolerance)
        
        print(f"✅ Unweighted OFI: {ofi_unweighted:.2f} BTC (СПУФ ВЛИЯЕТ)")
        print(f"✅ Weighted OFI: {ofi_weighted:.2f} BTC (СПУФ ОТФИЛЬТРОВАН)")
        print(f"✅ Фильтрация: {(1 - ofi_weighted/ofi_unweighted)*100:.1f}% спуфа убрано")


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
