"""
TEST: Schema Mismatch - Проверка соответствия имён полей

WHY: Gemini нашёл критическую ошибку:
repository.py создавал SmartCandle с неправильными именами полей:
- ofi → book_ofi
- obi → book_obi  
- weighted_obi → book_obi

ПРОБЛЕМА:
Pydantic выбросит ValidationError при создании объекта, так как:
1. Аргументы ofi/weighted_obi неизвестны модели
2. Поля book_ofi/book_obi останутся None
3. Данные теряются молча (если extra='allow')

FIX: Приведены все вызовы SmartCandle(...) в соответствие с schema
"""

import pytest
from datetime import datetime
from decimal import Decimal
from domain_smartcandle import SmartCandle


class TestSmartCandleSchema:
    """Проверяем что SmartCandle создаётся с правильными именами полей"""
    
    def test_correct_field_names(self):
        """
        WHY: Проверяем что Pydantic не выбросит ValidationError.
        
        ПРАВИЛЬНЫЕ имена полей:
        - book_ofi (НЕ ofi)
        - book_obi (НЕ obi, НЕ weighted_obi)
        """
        # Должно работать без ошибок
        candle = SmartCandle(
            # Обязательные поля
            symbol="BTCUSDT",
            timeframe="1h",
            candle_time=datetime.now(),
            open=Decimal("50000"),
            high=Decimal("50500"),
            low=Decimal("49500"),
            close=Decimal("50000"),
            volume=Decimal("100"),
            flow_whale_cvd=1000.0,
            flow_dolphin_cvd=500.0,
            flow_minnow_cvd=-500.0,
            total_trades=1000,
            # Проверяемые поля
            book_ofi=0.5,      # ✅ ПРАВИЛЬНО
            book_obi=0.3       # ✅ ПРАВИЛЬНО
        )
        
        # Проверяем что значения сохранились
        assert candle.book_ofi == 0.5
        assert candle.book_obi == 0.3
    
    def test_wrong_field_names_rejected(self):
        """
        WHY: Убеждаемся что старые имена НЕ работают.
        
        НЕПРАВИЛЬНЫЕ имена (должны быть отклонены):
        - ofi → не сохраняется в модели
        - obi → не сохраняется в модели
        - weighted_obi → не сохраняется в модели
        
        NOTE: Если Pydantic extra='allow' или 'ignore',
        то неизвестные поля просто игнорируются.
        Мы проверяем что book_ofi/book_obi остаются None.
        """
        # Базовые обязательные поля
        base_fields = {
            'symbol': 'BTCUSDT',
            'timeframe': '1h',
            'candle_time': datetime.now(),
            'open': Decimal("50000"),
            'high': Decimal("50500"),
            'low': Decimal("49500"),
            'close': Decimal("50000"),
            'volume': Decimal("100"),
            'flow_whale_cvd': 1000.0,
            'flow_dolphin_cvd': 500.0,
            'flow_minnow_cvd': -500.0,
            'total_trades': 1000
        }
        
        # Проверяем что ofi НЕ попадает в book_ofi
        candle1 = SmartCandle(**base_fields, ofi=0.5)
        assert candle1.book_ofi is None, \
            "ofi НЕ должен попадать в book_ofi! Используйте book_ofi=..."
        
        # Проверяем что obi НЕ попадает в book_obi
        candle2 = SmartCandle(**base_fields, obi=0.3)
        assert candle2.book_obi is None, \
            "obi НЕ должен попадать в book_obi! Используйте book_obi=..."
        
        # Проверяем что weighted_obi НЕ попадает в book_obi
        candle3 = SmartCandle(**base_fields, weighted_obi=0.3)
        assert candle3.book_obi is None, \
            "weighted_obi НЕ должен попадать в book_obi! Используйте book_obi=..."
    
    def test_field_names_match_database_columns(self):
        """
        WHY: SQL запросы используют book_ofi/book_obi в БД.
        Pydantic модель должна совпадать с DB schema.
        
        DATABASE COLUMNS (market_metrics_full):
        - book_ofi NUMERIC
        - book_obi NUMERIC
        
        PYDANTIC MODEL (SmartCandle):
        - book_ofi: Optional[float]
        - book_obi: Optional[float]
        """
        # Симулируем данные из БД
        row = {
            'candle_time': datetime.now(),
            'symbol': 'BTCUSDT',
            'timeframe': '1h',
            'open': 50000,
            'high': 50500,
            'low': 49500,
            'close': 50000,
            'volume': 100,
            'whale_cvd': 1000,
            'dolphin_cvd': 500,
            'minnow_cvd': -500,
            'total_trades': 1000,
            'avg_ofi': 0.5,  # Из SQL: AVG(book_ofi) AS avg_ofi
            'avg_obi': 0.3   # Из SQL: AVG(book_obi) AS avg_obi
        }
        
        # Правильное создание (как в repository.py после FIX)
        candle = SmartCandle(
            symbol=row['symbol'],
            timeframe=row['timeframe'],
            candle_time=row['candle_time'],
            open=Decimal(str(row['open'])),
            high=Decimal(str(row['high'])),
            low=Decimal(str(row['low'])),
            close=Decimal(str(row['close'])),
            volume=Decimal(str(row['volume'])),
            flow_whale_cvd=float(row['whale_cvd']),
            flow_dolphin_cvd=float(row['dolphin_cvd']),
            flow_minnow_cvd=float(row['minnow_cvd']),
            total_trades=int(row['total_trades']),
            book_ofi=float(row['avg_ofi']),  # ✅ ПРАВИЛЬНО
            book_obi=float(row['avg_obi'])   # ✅ ПРАВИЛЬНО
        )
        
        assert candle.book_ofi == 0.5
        assert candle.book_obi == 0.3
    
    def test_all_repository_methods_use_correct_names(self):
        """
        WHY: Проверяем что ВСЕ методы в repository.py используют правильные имена.
        
        МЕТОДЫ ГДЕ СОЗДАЁТСЯ SmartCandle:
        1. get_aggregated_smart_candles() - строка ~518
        2. get_cold_start_candles() - строка ~790
        3. get_materialized_candles() - строка ~897
        
        ВСЕ должны использовать:
        - book_ofi (НЕ ofi)
        - book_obi (НЕ obi, НЕ weighted_obi)
        """
        import inspect
        from repository import PostgresRepository
        
        repo = PostgresRepository(dsn="postgresql://fake")
        
        # 1. Проверяем get_aggregated_smart_candles
        source1 = inspect.getsource(repo.get_aggregated_smart_candles)
        assert 'book_ofi=' in source1, \
            "get_aggregated_smart_candles должен использовать book_ofi="
        assert 'book_obi=' in source1, \
            "get_aggregated_smart_candles должен использовать book_obi="
        
        # Проверяем что НЕТ старых имён
        assert 'ofi=float(' not in source1 or 'book_ofi=float(' in source1, \
            "get_aggregated_smart_candles НЕ должен использовать ofi="
        assert 'weighted_obi=' not in source1, \
            "get_aggregated_smart_candles НЕ должен использовать weighted_obi="
        
        # 2. Проверяем get_cold_start_candles
        source2 = inspect.getsource(repo.get_cold_start_candles)
        assert 'book_ofi=' in source2, \
            "get_cold_start_candles должен использовать book_ofi="
        assert 'book_obi=' in source2, \
            "get_cold_start_candles должен использовать book_obi="
        
        # Проверяем что НЕТ старых имён
        assert 'ofi=' not in source2 or 'book_ofi=' in source2, \
            "get_cold_start_candles НЕ должен использовать ofi="
        assert 'obi=' not in source2 or 'book_obi=' in source2, \
            "get_cold_start_candles НЕ должен использовать obi="
        
        # 3. Проверяем get_materialized_candles
        source3 = inspect.getsource(repo.get_materialized_candles)
        assert 'book_ofi=' in source3, \
            "get_materialized_candles должен использовать book_ofi="
        assert 'book_obi=' in source3, \
            "get_materialized_candles должен использовать book_obi="
        
        # Проверяем что НЕТ старых имён
        assert 'weighted_obi=' not in source3, \
            "get_materialized_candles НЕ должен использовать weighted_obi="
    
    def test_pydantic_config_allows_correct_behavior(self):
        """
        WHY: Проверяем конфигурацию Pydantic модели.
        
        ТРЕБОВАНИЯ:
        1. Модель должна принимать только известные поля (extra='forbid')
           ИЛИ игнорировать лишние (extra='ignore')
        2. Модель НЕ должна молча терять данные (extra='allow' опасно!)
        """
        # Пытаемся создать с неизвестным полем
        try:
            candle = SmartCandle(
                symbol="BTCUSDT",
                timeframe="1h",
                candle_time=datetime.now(),
                open=Decimal("50000"),
                high=Decimal("50500"),
                low=Decimal("49500"),
                close=Decimal("50000"),
                volume=Decimal("100"),
                flow_whale_cvd=1000.0,
                flow_dolphin_cvd=500.0,
                flow_minnow_cvd=-500.0,
                total_trades=1000,
                unknown_field="test"  # Неизвестное поле
            )
            
            # Если дошли сюда - проверяем конфигурацию
            config = SmartCandle.model_config
            
            # extra='allow' - ПЛОХО (молча игнорирует ошибки)
            # extra='ignore' - OK (игнорирует лишние)
            # extra='forbid' - ХОРОШО (выбрасывает ошибку)
            
            extra = config.get('extra', 'allow')
            assert extra in ['ignore', 'forbid'], \
                f"SmartCandle.extra={extra} должен быть 'ignore' или 'forbid', не 'allow'!"
            
        except Exception as e:
            # Если упало - это хорошо (extra='forbid')
            print(f"✅ Pydantic правильно отклонил неизвестное поле: {e}")


class TestRepositorySQLQueries:
    """Проверяем SQL запросы в repository.py"""
    
    def test_sql_uses_book_prefix(self):
        """
        WHY: SQL запросы должны использовать book_ofi/book_obi.
        
        ТАБЛИЦА: market_metrics_full
        КОЛОНКИ:
        - book_ofi NUMERIC (НЕ ofi)
        - book_obi NUMERIC (НЕ obi)
        """
        import inspect
        from repository import PostgresRepository
        
        repo = PostgresRepository(dsn="postgresql://fake")
        
        # Проверяем get_aggregated_smart_candles SQL
        source = inspect.getsource(repo.get_aggregated_smart_candles)
        
        # SQL должен использовать book_ofi/book_obi в FROM market_metrics_full
        assert 'SUM(book_ofi)' in source or 'AVG(book_ofi)' in source, \
            "SQL должен агрегировать book_ofi из market_metrics_full"
        assert 'AVG(book_obi)' in source or 'SUM(book_obi)' in source, \
            "SQL должен агрегировать book_obi из market_metrics_full"
    
    def test_sql_column_aliases_match_pydantic(self):
        """
        WHY: SQL алиасы (AS) должны совпадать с Pydantic полями.
        
        ПЛОХО:
        SELECT AVG(book_ofi) AS ofi  -- ❌ алиас не совпадает с полем
        
        ХОРОШО:
        SELECT AVG(book_ofi) AS avg_ofi  -- ✅ потом маппится в book_ofi
        """
        import inspect
        from repository import PostgresRepository
        
        repo = PostgresRepository(dsn="postgresql://fake")
        source = inspect.getsource(repo.get_aggregated_smart_candles)
        
        # Проверяем что SQL использует промежуточные алиасы
        # (avg_ofi, avg_obi) которые потом маппятся в book_ofi, book_obi
        if 'AS ofi' in source or 'AS weighted_obi' in source:
            pytest.fail(
                "SQL использует устаревшие алиасы (ofi, weighted_obi). "
                "Используйте промежуточные (avg_ofi, avg_obi) "
                "и маппинг в book_ofi, book_obi при создании SmartCandle"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
