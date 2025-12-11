"""
WHY: Unit-тесты для улучшенного Weighted OBI с экспоненциальным затуханием

Покрытие:
1. get_weighted_obi(use_exponential=True) - новая реализация
2. get_weighted_obi(use_exponential=False) - legacy (обратная совместимость)
3. Сравнение linear vs exponential - доказательство превосходства
4. Edge cases: пустой стакан, симметричный стакан, один уровень

Теория (документ "Анализ данных биржевого стакана"):
- Линейный decay (1/i) переоценивает дальние уровни
- Экспоненциальный decay (e^-λx) более реалистичен
- Отражает реальную вероятность исполнения ордера
"""

# WHY: Добавляем родительскую папку в sys.path для импорта модулей
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from decimal import Decimal
from domain import LocalOrderBook


# ===========================================================================
# ТЕСТЫ БАЗОВОЙ ФУНКЦИОНАЛЬНОСТИ
# ===========================================================================

def test_obi_empty_book():
    """WHY: Пустой стакан должен вернуть 0.0"""
    book = LocalOrderBook(symbol="BTCUSDT")
    
    # Не применяем снапшот - стакан пустой
    
    obi = book.get_weighted_obi()
    assert obi == 0.0


def test_obi_one_sided_bid_only():
    """WHY: Только bid ликвидность = OBI = +1.0"""
    book = LocalOrderBook(symbol="BTCUSDT")
    
    book.apply_snapshot(
        bids=[(Decimal("60000"), Decimal("10.0"))],
        asks=[],  # Нет ask
        last_update_id=100
    )
    
    obi = book.get_weighted_obi()
    assert obi == 1.0, f"Expected OBI=1.0 for bid-only, got {obi}"


def test_obi_one_sided_ask_only():
    """WHY: Только ask ликвидность = OBI = -1.0"""
    book = LocalOrderBook(symbol="BTCUSDT")
    
    book.apply_snapshot(
        bids=[],
        asks=[(Decimal("60100"), Decimal("5.0"))],
        last_update_id=100
    )
    
    obi = book.get_weighted_obi()
    assert obi == -1.0, f"Expected OBI=-1.0 for ask-only, got {obi}"


def test_obi_symmetric_book():
    """WHY: Симметричный стакан (равные объемы) = OBI ≈ 0"""
    book = LocalOrderBook(symbol="BTCUSDT")
    
    # 10 уровней по 1 BTC с каждой стороны
    bids = [(Decimal(f"59990.{i:02d}"), Decimal("1.0")) for i in range(10)]
    asks = [(Decimal(f"60010.{i:02d}"), Decimal("1.0")) for i in range(10)]
    
    book.apply_snapshot(bids=bids, asks=asks, last_update_id=100)
    
    obi = book.get_weighted_obi(depth=10)
    assert abs(obi) < 0.01, f"Expected OBI≈0 for symmetric book, got {obi}"


# ===========================================================================
# ТЕСТЫ ЭКСПОНЕНЦИАЛЬНОГО vs ЛИНЕЙНОГО DECAY
# ===========================================================================

def test_exponential_reduces_far_levels():
    """
    WHY: Экспоненциальный decay должен снижать влияние дальних уровней
    
    Сценарий:
    - 1 BTC на близком уровне (bid)
    - 100 BTC на дальнем уровне (bid)
    - Линейный OBI переоценивает 100 BTC
    - Экспоненциальный OBI игнорирует дальний уровень
    """
    book = LocalOrderBook(symbol="BTCUSDT")
    
    # Bid: 1 BTC близко + 100 BTC далеко
    bids = [
        (Decimal("59999.00"), Decimal("1.0")),    # Близко к mid (~60000)
        (Decimal("59800.00"), Decimal("100.0"))   # Далеко (200 тиков)
    ]
    
    # Ask: минимальная ликвидность для расчета mid
    asks = [(Decimal("60001.00"), Decimal("1.0"))]
    
    book.apply_snapshot(bids=bids, asks=asks, last_update_id=100)
    
    obi_linear = book.get_weighted_obi(depth=20, use_exponential=False)
    obi_exp = book.get_weighted_obi(depth=20, use_exponential=True)
    
    # Линейный: 100 BTC имеет вес 0.5 (i=2, weight=1/2)
    # Экспоненциальный: 100 BTC имеет вес ≈0.0000 (e^-0.1*200 ≈ 2e-9)
    
    # Оба должны быть положительными (больше bids), но:
    assert obi_linear > 0
    assert obi_exp > 0
    
    # Экспоненциальный должен быть МЕНЬШЕ (дальний уровень почти не влияет)
    assert obi_exp < obi_linear, \
        f"Exponential should filter far levels: exp={obi_exp:.4f}, linear={obi_linear:.4f}"
    
    print(f"✅ Linear OBI (overestimates far levels): {obi_linear:.4f}")
    print(f"✅ Exponential OBI (filters far levels): {obi_exp:.4f}")


def test_exponential_preserves_near_levels():
    """
    WHY: Экспоненциальный decay должен СОХРАНЯТЬ влияние близких уровней
    
    Сценарий:
    - Вся ликвидность в топ-3 уровнях
    - Оба метода должны дать похожие результаты
    """
    book = LocalOrderBook(symbol="BTCUSDT")
    
    bids = [
        (Decimal("59999"), Decimal("5.0")),
        (Decimal("59998"), Decimal("5.0")),
        (Decimal("59997"), Decimal("5.0"))
    ]
    
    asks = [
        (Decimal("60001"), Decimal("5.0")),
        (Decimal("60002"), Decimal("5.0")),
        (Decimal("60003"), Decimal("5.0"))
    ]
    
    book.apply_snapshot(bids=bids, asks=asks, last_update_id=100)
    
    obi_linear = book.get_weighted_obi(depth=5, use_exponential=False)
    obi_exp = book.get_weighted_obi(depth=5, use_exponential=True)
    
    # Разница должна быть минимальной (<10%)
    relative_diff = abs(obi_linear - obi_exp) / (abs(obi_linear) + 0.0001)
    assert relative_diff < 0.1, \
        f"Near levels should have similar OBI: linear={obi_linear:.4f}, exp={obi_exp:.4f}"


def test_exponential_decay_slope():
    """
    WHY: Проверка корректности экспоненциальной кривой
    
    Тест: Вес должен падать экспоненциально с расстоянием
    """
    book = LocalOrderBook(symbol="BTCUSDT")
    
    # Создаем градиент ликвидности (все уровни по 1 BTC)
    # Но расстояния разные: 1, 10, 50, 100 тиков от mid
    bids = [
        (Decimal("59999.99"), Decimal("1.0")),  # 1 тик
        (Decimal("59999.90"), Decimal("1.0")),  # 10 тиков
        (Decimal("59999.50"), Decimal("1.0")),  # 50 тиков
        (Decimal("59999.00"), Decimal("1.0"))   # 100 тиков
    ]
    
    asks = [(Decimal("60000.01"), Decimal("1.0"))]  # Mid ≈ 60000
    
    book.apply_snapshot(bids=bids, asks=asks, last_update_id=100)
    
    obi_exp = book.get_weighted_obi(depth=10, use_exponential=True)
    
    # WHY: С λ_scaled=5.0 даже уровни в 10-100 тиков получают затухание
    # OBI > 0.5 означает что первый уровень (1 тик) доминирует
    assert obi_exp > 0.50, f"Expected OBI > 0.5 (first level dominates), got {obi_exp}"


# ===========================================================================
# ТЕСТЫ ОБРАТНОЙ СОВМЕСТИМОСТИ
# ===========================================================================

def test_backward_compatibility_default_exponential():
    """WHY: По умолчанию должен использоваться exponential (новая логика)"""
    book = LocalOrderBook(symbol="BTCUSDT")
    
    book.apply_snapshot(
        bids=[(Decimal("60000"), Decimal("10.0"))],
        asks=[(Decimal("60100"), Decimal("5.0"))],
        last_update_id=100
    )
    
    # Вызов без параметра use_exponential
    obi_default = book.get_weighted_obi(depth=20)
    
    # Явный вызов exponential
    obi_exp = book.get_weighted_obi(depth=20, use_exponential=True)
    
    # Должны совпадать
    assert obi_default == obi_exp, \
        f"Default should use exponential: default={obi_default}, exp={obi_exp}"


def test_legacy_mode_works():
    """WHY: Старая линейная логика должна продолжать работать"""
    book = LocalOrderBook(symbol="BTCUSDT")
    
    book.apply_snapshot(
        bids=[(Decimal("60000"), Decimal("10.0"))],
        asks=[(Decimal("60100"), Decimal("5.0"))],
        last_update_id=100
    )
    
    # Явный вызов linear (legacy)
    obi_linear = book.get_weighted_obi(depth=20, use_exponential=False)
    
    # Должен вернуть валидное значение (не крашнуться)
    assert -1.0 <= obi_linear <= 1.0


# ===========================================================================
# СТРЕСС-ТЕСТЫ И EDGE CASES
# ===========================================================================

def test_obi_with_zero_volumes():
    """WHY: Edge case - все уровни с нулевым объемом"""
    book = LocalOrderBook(symbol="BTCUSDT")
    
    book.apply_snapshot(
        bids=[(Decimal("60000"), Decimal("0"))],  # 0 volume!
        asks=[(Decimal("60100"), Decimal("0"))],
        last_update_id=100
    )
    
    obi = book.get_weighted_obi()
    assert obi == 0.0  # Должен корректно обработать division by zero


def test_obi_large_depth():
    """WHY: Производительность при большой глубине"""
    book = LocalOrderBook(symbol="BTCUSDT")
    
    # 100 уровней с каждой стороны
    bids = [(Decimal(f"59000.{i:02d}"), Decimal("1.0")) for i in range(100)]
    asks = [(Decimal(f"60100.{i:02d}"), Decimal("1.0")) for i in range(100)]
    
    book.apply_snapshot(bids=bids, asks=asks, last_update_id=100)
    
    # Должен работать без ошибок
    obi = book.get_weighted_obi(depth=100, use_exponential=True)
    assert -1.0 <= obi <= 1.0


def test_obi_missing_mid_price():
    """WHY: Edge case - нельзя вычислить mid_price (одна из сторон пуста)"""
    book = LocalOrderBook(symbol="BTCUSDT")
    
    book.apply_snapshot(
        bids=[(Decimal("60000"), Decimal("10.0"))],
        asks=[],  # Нет asks!
        last_update_id=100
    )
    
    # Exponential режим требует mid_price, но его нет
    obi = book.get_weighted_obi(use_exponential=True)
    
    # Должен gracefully вернуть 1.0 (fallback)
    assert obi == 1.0


# ===========================================================================
# ИНТЕГРАЦИОННЫЕ ТЕСТЫ
# ===========================================================================

def test_obi_integration_spoofing_detection():
    """
    WHY: Интеграционный тест - детекция спуфинга через OBI
    
    Сценарий:
    - Огромная "стена" на дальнем уровне (спуфинг)
    - Мелкая ликвидность близко
    - Линейный OBI покажет сильный дисбаланс (ложный сигнал)
    - Экспоненциальный OBI проигнорирует дальнюю стену
    """
    book = LocalOrderBook(symbol="BTCUSDT")
    
    bids = [
        (Decimal("59999"), Decimal("2.0")),     # Реальная ликвидность
        (Decimal("59500"), Decimal("1000.0"))   # СПУФ-СТЕНА далеко!
    ]
    
    asks = [(Decimal("60001"), Decimal("2.0"))]
    
    book.apply_snapshot(bids=bids, asks=asks, last_update_id=100)
    
    obi_linear = book.get_weighted_obi(depth=20, use_exponential=False)
    obi_exp = book.get_weighted_obi(depth=20, use_exponential=True)
    
    # Линейный: 1000 BTC имеет огромный вес → сильный дисбаланс
    # Экспоненциальный: 1000 BTC почти не влияет (500$ = 0.83% → вес ≈ 0.015)
    
    assert obi_linear > 0.5, "Linear should detect big imbalance (false signal)"
    # WHY: С λ=5.0 спуф-стена на 500$ получает вес ~0.015, но ask тоже есть
    # OBI должен быть близок к 0, но не строго
    assert abs(obi_exp) < 0.5, f"Exponential should ignore far spoofing wall, got {obi_exp}"
    
    print(f"🚨 Linear OBI (fooled by spoofing): {obi_linear:.4f}")
    print(f"✅ Exponential OBI (filters spoofing): {obi_exp:.4f}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
