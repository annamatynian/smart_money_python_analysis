"""
TEST: Cold Start Warm-Up (Fix Vulnerability #4)

ПРОБЛЕМА (Gemini Validation):
- При старте бота переменные state (_last_whale_cvd) = 0.0
- Первый снапшот содержит мусор (whale_cvd=0.0, twap=None, volatility=None)
- ML модель обучается на паттерне "при старте бота whale_cvd всегда 0"

РЕШЕНИЕ:
- Флаг _is_warmed_up = False
- capture_snapshot() возвращает None пока не готов
- Критерии готовности:
  * _last_whale_cvd != 0.0 (state инициализирован)
  * len(price_history) >= 60 (1 минута истории для TWAP)
  * Критические метрики != None

WHY: Предотвращает загрязнение ML датасета мусорными снапшотами.
"""

import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from analyzers_features import FeatureCollector
from domain import LocalOrderBook
from config import BTC_CONFIG


class TestColdStartWarmup:
    """
    WHY: Валидирует что FeatureCollector не возвращает снапшоты на холодном старте.
    """
    
    def test_initial_state_not_warmed_up(self):
        """
        WHY: Проверяет что после создания collector НЕ warmed up.
        
        GIVEN: Новый FeatureCollector
        WHEN: Проверяем is_warmed_up
        THEN: Должен быть False
        """
        book = LocalOrderBook(symbol="BTCUSDT")
        collector = FeatureCollector(order_book=book)
        
        # ASSERT: Не готов к работе
        assert collector.is_warmed_up == False
    
    def test_capture_snapshot_returns_none_when_not_warmed(self):
        """
        WHY: Проверяет что capture_snapshot() возвращает None на холодном старте.
        
        GIVEN: FeatureCollector без warm-up
        WHEN: Вызываем capture_snapshot()
        THEN: Должен вернуть None (не мусорный снапшот)
        """
        book = LocalOrderBook(symbol="BTCUSDT")
        collector = FeatureCollector(order_book=book)
        
        # ACT: Пытаемся создать снапшот
        snapshot = collector.capture_snapshot()
        
        # ASSERT: Должен быть None
        assert snapshot is None, "Cold start должен возвращать None, не мусорный снапшот"
    
    def test_warmup_requires_state_initialization(self):
        """
        WHY: Проверяет что warm-up требует инициализации CVD state.
        
        GIVEN: FeatureCollector с пустым state (_last_whale_cvd = 0.0)
        WHEN: Проверяем готовность
        THEN: is_ready() должен быть False
        """
        book = LocalOrderBook(symbol="BTCUSDT")
        collector = FeatureCollector(order_book=book)
        
        # ASSERT: State не инициализирован
        assert collector._last_whale_cvd == 0.0
        assert collector._last_minnow_cvd == 0.0
        assert collector._last_dolphin_cvd == 0.0
        
        # ASSERT: Не готов
        assert collector.is_ready() == False
    
    def test_warmup_requires_price_history(self):
        """
        WHY: Проверяет что warm-up требует минимум 60 точек price_history.
        
        GIVEN: FeatureCollector с инициализированным state но БЕЗ истории цен
        WHEN: Проверяем готовность
        THEN: is_ready() должен быть False
        """
        book = LocalOrderBook(symbol="BTCUSDT")
        collector = FeatureCollector(order_book=book)
        
        # Эмулируем инициализацию state (первый _get_whale_cvd вызван)
        collector._last_whale_cvd = 1000.0  # Не ноль
        collector._last_minnow_cvd = 500.0
        collector._last_dolphin_cvd = 200.0
        
        # ASSERT: price_history пустая
        assert len(collector.price_history) == 0
        
        # ASSERT: Всё ещё не готов (нет истории для TWAP)
        assert collector.is_ready() == False
    
    def test_warmup_successful_with_sufficient_data(self):
        """
        WHY: Проверяет что warm-up завершается при достаточных данных.
        
        GIVEN: FeatureCollector с:
               - Инициализированным state (_last_whale_cvd != 0)
               - 60+ точек price_history
        WHEN: Проверяем готовность
        THEN: is_ready() должен быть True
        """
        book = LocalOrderBook(symbol="BTCUSDT")
        collector = FeatureCollector(order_book=book)
        
        # 1. Инициализируем state (эмуляция первого вызова CVD)
        collector._last_whale_cvd = 1000.0
        collector._last_minnow_cvd = 500.0
        collector._last_dolphin_cvd = 200.0
        
        # 2. Добавляем 60 точек в price_history (1 минута при 1 сек частоте)
        now = datetime.now(timezone.utc)
        for i in range(60):
            collector.update_price(95000.0 + i * 10)  # Симулируем изменение цены
        
        # ASSERT: Теперь готов
        assert collector.is_ready() == True
    
    def test_capture_snapshot_returns_data_after_warmup(self):
        """
        WHY: Проверяет что после warm-up снапшоты возвращаются нормально.
        
        GIVEN: FeatureCollector после успешного warm-up
        WHEN: Вызываем capture_snapshot()
        THEN: Должен вернуть валидный FeatureSnapshot
        """
        book = LocalOrderBook(symbol="BTCUSDT")
        collector = FeatureCollector(order_book=book)
        
        # Warm-up
        collector._last_whale_cvd = 1000.0
        collector._last_minnow_cvd = 500.0
        collector._last_dolphin_cvd = 200.0
        
        for i in range(60):
            collector.update_price(95000.0 + i * 10)
        
        # ASSERT: Готов
        assert collector.is_ready() == True
        
        # WHY: Устанавливаем флаг warm-up (имитация check_warmup())
        collector.check_warmup()
        
        # ACT: Создаём снапшот
        snapshot = collector.capture_snapshot()
        
        # ASSERT: Снапшот создан
        assert snapshot is not None
        assert snapshot.snapshot_time is not None
    
    def test_check_warmup_sets_flag_when_ready(self):
        """
        WHY: Проверяет автоматическую установку флага _is_warmed_up.
        
        GIVEN: FeatureCollector с достаточными данными но флаг ещё False
        WHEN: Вызываем check_warmup()
        THEN: Флаг _is_warmed_up должен стать True
        """
        book = LocalOrderBook(symbol="BTCUSDT")
        collector = FeatureCollector(order_book=book)
        
        # Подготовка к warm-up
        collector._last_whale_cvd = 1000.0
        collector._last_minnow_cvd = 500.0
        collector._last_dolphin_cvd = 200.0
        
        for i in range(60):
            collector.update_price(95000.0)
        
        # ASSERT: Изначально не warmed up
        assert collector.is_warmed_up == False
        
        # ACT: Проверяем warm-up
        collector.check_warmup()
        
        # ASSERT: Флаг установлен
        assert collector.is_warmed_up == True
    
    def test_realistic_cold_start_scenario(self):
        """
        WHY: Симулирует реальный сценарий холодного старта бота.
        
        GIVEN: Новый FeatureCollector
        WHEN: Симулируем первые 60 секунд работы (update_price каждую секунду)
        THEN: 
            - Первые 59 вызовов capture_snapshot() возвращают None
            - 60-й вызов возвращает валидный снапшот
        """
        book = LocalOrderBook(symbol="BTCUSDT")
        collector = FeatureCollector(order_book=book)
        
        # Симуляция первых 60 секунд
        snapshots = []
        
        for second in range(60):
            # 1. Обновляем цену (каждую секунду)
            collector.update_price(95000.0 + second * 5)
            
            # 2. Эмулируем вызов CVD методов (обновление state)
            # WHY: В реальности это происходит через _get_whale_cvd() внутри capture_snapshot()
            # Но для теста делаем явно, чтобы контролировать moment инициализации state
            if second == 0:
                # Первый вызов - инициализирует state (эмуляция первого trade)
                collector._last_whale_cvd = 1000.0
                collector._last_minnow_cvd = 500.0
                collector._last_dolphin_cvd = 200.0
            
            # 3. Проверяем warm-up статус (автоматически в реальной системе)
            collector.check_warmup()
            
            # 4. Пытаемся создать снапшот
            snapshot = collector.capture_snapshot()
            snapshots.append(snapshot)
        
        # ASSERT: Первые 59 снапшотов = None (cold start)
        none_count = sum(1 for s in snapshots if s is None)
        assert none_count == 59, f"Должно быть 59 None снапшотов, получено {none_count}"
        
        # ASSERT: 60-й снапшот валидный
        assert snapshots[-1] is not None, "60-й снапшот должен быть валидным"
        assert snapshots[-1].snapshot_time is not None
    
    def test_warmup_criteria_documented(self):
        """
        WHY: Документирует критерии warm-up для будущих разработчиков.
        
        Критерии готовности (is_ready):
        1. State инициализирован: _last_whale_cvd != 0.0
        2. Price history заполнена: len(price_history) >= 60
        3. (future) OrderBook синхронизирован: book.last_update_id is not None
        
        Это НЕ тест кода, а тест документации/контракта.
        """
        book = LocalOrderBook(symbol="BTCUSDT")
        collector = FeatureCollector(order_book=book)
        
        # Критерий 1: State инициализирован
        assert hasattr(collector, '_last_whale_cvd'), "Должно быть поле _last_whale_cvd"
        assert hasattr(collector, '_last_minnow_cvd'), "Должно быть поле _last_minnow_cvd"
        assert hasattr(collector, '_last_dolphin_cvd'), "Должно быть поле _last_dolphin_cvd"
        
        # Критерий 2: Price history существует
        assert hasattr(collector, 'price_history'), "Должно быть поле price_history"
        
        # Критерий 3: Метод is_ready() существует
        assert hasattr(collector, 'is_ready'), "Должен быть метод is_ready()"
        assert callable(collector.is_ready), "is_ready должен быть вызываемым"
        
        # Критерий 4: Флаг _is_warmed_up существует
        assert hasattr(collector, 'is_warmed_up'), "Должно быть поле is_warmed_up"
        assert isinstance(collector.is_warmed_up, bool), "is_warmed_up должен быть bool"
        
        # Критерий 5: Метод check_warmup() существует
        assert hasattr(collector, 'check_warmup'), "Должен быть метод check_warmup()"
        assert callable(collector.check_warmup), "check_warmup должен быть вызываемым"
