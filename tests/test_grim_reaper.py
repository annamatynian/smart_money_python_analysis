# ===========================================================================
# TESTS: Grim Reaper - Retrospective Labeling (Step 3)
# ===========================================================================

"""
WHY: Тестируем ретроспективную разметку данных для ML.

Проверяем:
1. _calculate_outcome() корректно определяет исход (Win/Loss/Neutral)
2. run_grim_reaper_labeling() обрабатывает batch айсбергов
3. Логика барьеров (stop/take profit) работает правильно
"""

import pytest
from datetime import datetime, timedelta
from decimal import Decimal
import asyncpg

from repository import PostgresRepository


class TestCalculateOutcome:
    """Тесты для логики расчёта исхода"""
    
    @pytest.fixture
    def mock_conn(self):
        """
        WHY: Мок connection для тестирования без реальной БД.
        
        CRITICAL: Эмулирует SQL фильтрацию WHERE time > start_time!
        Без этого тесты будут получать "шумные" свечи и давать ложные результаты.
        
        Возвращает объект с методом fetch() который симулирует SQL запросы.
        """
        class MockConn:
            def __init__(self):
                self.candles = []
            
            async def fetch(self, query, *args):
                """
                Эмулирует SQL: WHERE time > $2
                
                args[0] = symbol
                args[1] = start_time (фильтр времени!)
                args[2] = end_time (опционально)
                """
                # WHY: Проверяем есть ли start_time в аргументах
                if len(args) >= 2 and isinstance(args[1], datetime):
                    start_time = args[1]
                    
                    # WHY: Фильтруем свечи, эмулируя SQL WHERE time > $2
                    # Это критически важно для тестов Smart Settling!
                    filtered = [
                        c for c in self.candles 
                        if c.get('time', datetime.max) > start_time
                    ]
                    return filtered
                
                # WHY: Fallback если start_time не передан
                return self.candles
            
            async def execute(self, query, *args):
                """Мок execute для UPDATE"""
                pass
        
        return MockConn()
    
    @pytest.mark.asyncio
    async def test_calculate_outcome_win_buy_iceberg(self, mock_conn):
        """
        WHY: Тест WIN сценария для BUY айсберга.
        
        Scenario: 
        - BUY iceberg at 60000 (is_ask=False)
        - ATR = 500 → take_profit = 60000 + 3000 = 63000
        - Цена достигает 63000 на 3й свече
        
        Expected: Win (1)
        """
        # Arrange
        repo = PostgresRepository(dsn="mock")
        
        iceberg_data = {
            'id': 'test-uuid',
            'symbol': 'BTCUSDT',
            'price': Decimal('60000'),
            'is_ask': False,  # BUY iceberg (поддержка)
            'event_time': datetime(2025, 1, 1),
            'volatility_at_entry': 500.0  # ATR
        }
        
        # Симулируем свечи: цена растёт и достигает take profit
        mock_conn.candles = [
            {'close': 60200.0},  # +200
            {'close': 61500.0},  # +1500
            {'close': 63100.0},  # +3100 (TAKE PROFIT HIT!)
            {'close': 62000.0}   # Откат (не важно уже)
        ]
        
        # Act
        outcome = await repo._calculate_outcome(mock_conn, iceberg_data)
        
        # Assert
        assert outcome == 1, "Expected Win for take profit hit"
    
    @pytest.mark.asyncio
    async def test_calculate_outcome_loss_buy_iceberg(self, mock_conn):
        """
        WHY: Тест LOSS сценария для BUY айсберга.
        
        Scenario:
        - BUY iceberg at 60000
        - ATR = 500 → stop_loss = 60000 - 1500 = 58500
        - Цена падает до 58000 на 2й свече
        
        Expected: Loss (-1)
        """
        # Arrange
        repo = PostgresRepository(dsn="mock")
        
        iceberg_data = {
            'id': 'test-uuid',
            'symbol': 'BTCUSDT',
            'price': Decimal('60000'),
            'is_ask': False,
            'event_time': datetime(2025, 1, 1),
            'volatility_at_entry': 500.0
        }
        
        # Цена падает и пробивает stop loss
        mock_conn.candles = [
            {'close': 59500.0},  # -500
            {'close': 58000.0},  # -2000 (STOP LOSS HIT!)
            {'close': 59000.0}   # Откат
        ]
        
        # Act
        outcome = await repo._calculate_outcome(mock_conn, iceberg_data)
        
        # Assert
        assert outcome == -1, "Expected Loss for stop loss hit"
    
    @pytest.mark.asyncio
    async def test_calculate_outcome_win_sell_iceberg(self, mock_conn):
        """
        WHY: Тест WIN для SELL айсберга (обратная логика).
        
        Scenario:
        - SELL iceberg at 60000 (is_ask=True, сопротивление)
        - ATR = 500 → take_profit = 60000 - 3000 = 57000
        - Цена падает до 56800
        
        Expected: Win (1)
        """
        # Arrange
        repo = PostgresRepository(dsn="mock")
        
        iceberg_data = {
            'id': 'test-uuid',
            'symbol': 'BTCUSDT',
            'price': Decimal('60000'),
            'is_ask': True,  # SELL iceberg
            'event_time': datetime(2025, 1, 1),
            'volatility_at_entry': 500.0
        }
        
        # Цена падает (выгодно для шорта)
        mock_conn.candles = [
            {'close': 59000.0},
            {'close': 57500.0},
            {'close': 56800.0}  # TAKE PROFIT HIT
        ]
        
        # Act
        outcome = await repo._calculate_outcome(mock_conn, iceberg_data)
        
        # Assert
        assert outcome == 1
    
    @pytest.mark.asyncio
    async def test_calculate_outcome_neutral_no_barrier_hit(self, mock_conn):
        """
        WHY: Тест NEUTRAL - ни один барьер не пробит за 7 дней.
        
        Expected: Neutral (0)
        """
        # Arrange
        repo = PostgresRepository(dsn="mock")
        
        iceberg_data = {
            'id': 'test-uuid',
            'symbol': 'BTCUSDT',
            'price': Decimal('60000'),
            'is_ask': False,
            'event_time': datetime(2025, 1, 1),
            'volatility_at_entry': 500.0
        }
        
        # Цена колеблется в диапазоне (не пробивает барьеры)
        mock_conn.candles = [
            {'close': 60100.0},
            {'close': 60200.0},
            {'close': 59900.0},
            {'close': 60300.0}
        ]
        
        # Act
        outcome = await repo._calculate_outcome(mock_conn, iceberg_data)
        
        # Assert
        assert outcome == 0, "Expected Neutral when no barrier hit"
    
    @pytest.mark.asyncio
    async def test_calculate_outcome_no_atr_returns_neutral(self, mock_conn):
        """
        WHY: Граничный случай - нет ATR данных.
        
        Expected: Neutral (0) - не можем рассчитать барьеры
        """
        # Arrange
        repo = PostgresRepository(dsn="mock")
        
        iceberg_data = {
            'id': 'test-uuid',
            'symbol': 'BTCUSDT',
            'price': Decimal('60000'),
            'is_ask': False,
            'event_time': datetime(2025, 1, 1),
            'volatility_at_entry': None  # НЕТ ATR!
        }
        
        mock_conn.candles = []
        
        # Act
        outcome = await repo._calculate_outcome(mock_conn, iceberg_data)
        
        # Assert
        assert outcome == 0
    
    @pytest.mark.asyncio
    async def test_calculate_outcome_zero_atr_returns_neutral(self, mock_conn):
        """
        WHY: ATR = 0 (нет волатильности).
        
        Expected: Neutral
        """
        # Arrange
        repo = PostgresRepository(dsn="mock")
        
        iceberg_data = {
            'id': 'test-uuid',
            'symbol': 'BTCUSDT',
            'price': Decimal('60000'),
            'is_ask': False,
            'event_time': datetime(2025, 1, 1),
            'volatility_at_entry': 0.0
        }
        
        # Act
        outcome = await repo._calculate_outcome(mock_conn, iceberg_data)
        
        # Assert
        assert outcome == 0


    @pytest.mark.asyncio
    async def test_panic_absorption_no_settling_delay(self, mock_conn):
        """
        WHY: Тест PANIC ABSORPTION - high VPIN НО minnows panic = NO delay.
        
        Scenario:
        - VPIN 0.85 (high)
        - minnow_cvd_delta < whale_cvd_delta (рыбы паникуют)
        - Анализ ДОЛЖЕН начаться СРАЗУ (без settling)
        
        Expected: 
        - start_time = event_time (нет задержки)
        - V-shape recovery должен быть засчитан как Win
        """
        # Arrange
        repo = PostgresRepository(dsn="mock")
        
        # WHY: Panic Absorption scenario
        iceberg_data = {
            'id': 'test-uuid',
            'symbol': 'BTCUSDT',
            'price': Decimal('60000'),
            'is_ask': False,  # BUY iceberg
            'event_time': datetime(2025, 1, 1, 10, 0),
            'volatility_at_entry': 500.0,
            'vpin_at_entry': 0.85,  # HIGH VPIN!
            
            # KEY: Panic markers
            'minnow_cvd_delta': -5000.0,  # Рыбы паникуют (продают)
            'whale_cvd_delta': 3000.0,    # Киты покупают
            
            't_settled': None  # Не задан
        }
        
        # WHY: V-shape recovery - цена быстро восстанавливается
        mock_conn.candles = [
            {'close': 60200.0, 'time': datetime(2025, 1, 1, 10, 2)},  # +200 (в первые минуты!)
            {'close': 61500.0, 'time': datetime(2025, 1, 1, 10, 5)},  # +1500
            {'close': 63100.0, 'time': datetime(2025, 1, 1, 10, 10)} # TAKE PROFIT
        ]
        
        # Act
        outcome = await repo._calculate_outcome(mock_conn, iceberg_data)
        
        # Assert
        assert outcome == 1, "Expected Win - panic absorption should enter immediately and catch V-shape"
    
    @pytest.mark.asyncio
    async def test_whale_attack_with_settling_delay(self, mock_conn):
        """
        WHY: Тест WHALE ATTACK - high VPIN НО whales attacking = WAIT.
        
        Scenario:
        - VPIN 0.85 (high)
        - whale_cvd_delta < minnow_cvd_delta (киты атакуют)
        - Анализ ДОЛЖЕН подождать 30 мин
        
        Expected:
        - start_time = event_time + 30 min
        - Шум ДО settling игнорируется
        """
        # Arrange
        repo = PostgresRepository(dsn="mock")
        
        # WHY: Whale Attack scenario  
        iceberg_data = {
            'id': 'test-uuid',
            'symbol': 'BTCUSDT',
            'price': Decimal('60000'),
            'is_ask': False,
            'event_time': datetime(2025, 1, 1, 10, 0),
            'volatility_at_entry': 500.0,
            'vpin_at_entry': 0.85,
            
            # KEY: Whale attack markers
            'minnow_cvd_delta': 2000.0,   # Рыбы покупают (наивно)
            'whale_cvd_delta': -8000.0,   # Киты продают (dump)
            
            't_settled': None
        }
        
        # WHY: Шум ДО 10:30 + реальное движение ПОСЛЕ
        mock_conn.candles = [
            # NOISE period (should be ignored)
            {'close': 58000.0, 'time': datetime(2025, 1, 1, 10, 5)},
            {'close': 62000.0, 'time': datetime(2025, 1, 1, 10, 15)},
            
            # REAL period (after 10:30 settling)
            {'close': 60200.0, 'time': datetime(2025, 1, 1, 10, 35)},
            {'close': 61500.0, 'time': datetime(2025, 1, 1, 11, 0)},
            {'close': 63100.0, 'time': datetime(2025, 1, 1, 12, 0)}  # TAKE PROFIT
        ]
        
        # Act
        outcome = await repo._calculate_outcome(mock_conn, iceberg_data)
        
        # Assert  
        assert outcome == 1, "Expected Win - noise ignored, only post-settling matters"


class TestGrimReaperIntegration:
    """Интеграционные тесты (требуют реальную БД)"""
    
    @pytest.mark.skip(reason="Requires PostgreSQL database")
    @pytest.mark.asyncio
    async def test_run_grim_reaper_labels_old_icebergs(self):
        """
        WHY: Полный тест Grim Reaper workflow.
        
        Scenario:
        1. Вставляем тестовый айсберг (старше 7 дней)
        2. Вставляем свечи в market_metrics_full
        3. Запускаем run_grim_reaper_labeling()
        4. Проверяем что y_strategic_result заполнился
        
        NOTE: Этот тест требует настроенную PostgreSQL
        """
        # TODO: Реализовать после интеграционного окружения
        pass


# === RUN TESTS ===
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
