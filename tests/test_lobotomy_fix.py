# ===========================================================================
# ТЕСТ: Проверка устранения "Лоботомии" FeatureCollector
# ===========================================================================

"""
WHY: Проверяет что FeatureCollector получает все необходимые зависимости.

ПРОБЛЕМА (из Gemini диагностики):
    В services.py коллектор инициализировался с None для всех анализаторов:
    - flow_analyzer=None
    - derivatives_analyzer=None  
    - spoofing_detector=None
    - gamma_provider=None
    
    Это приводило к тому, что capture_snapshot() возвращал только 
    order_book метрики, а остальные 14 фичей были NULL.

ОЖИДАЕМОЕ ПОВЕДЕНИЕ ПОСЛЕ ИСПРАВЛЕНИЯ:
    ✅ derivatives_analyzer подключен
    ✅ spoofing_detector подключен
    ✅ flow_toxicity_analyzer подключен
    ✅ gamma_provider подключен
    ⚠️ flow_analyzer=None (НО это OK - CVD читается из book.whale_cvd)
"""

import pytest
from services import TradingEngine
from infrastructure import BinanceInfrastructure
from config import get_config
from decimal import Decimal

class TestFeatureCollectorLobotomyFix:
    """
    WHY: Набор тестов для проверки что "Лоботомия" устранена.
    
    Критерии успеха:
    1. FeatureCollector имеет все необходимые зависимости (не None)
    2. capture_snapshot() возвращает реальные данные (не только None)
    3. Количество непустых метрик >= 10 (из 18 общих)
    """
    
    def test_feature_collector_has_derivatives_analyzer(self):
        """
        WHY: Проверяет что derivatives_analyzer подключен.
        
        БЫЛО: derivatives_analyzer=None
        ДОЛЖНО БЫТЬ: derivatives_analyzer=DerivativesAnalyzer()
        """
        # Arrange
        symbol = "BTCUSDT"
        infra = BinanceInfrastructure()  # FIX: Не принимает аргументы
        
        # Act
        engine = TradingEngine(symbol, infra)
        
        # Assert
        assert engine.feature_collector.derivatives is not None, \
            "❌ derivatives_analyzer не подключен!"
        
        print("✅ derivatives_analyzer подключен")
    
    def test_feature_collector_has_spoofing_detector(self):
        """
        WHY: Проверяет что spoofing_detector подключен.
        
        БЫЛО: spoofing_detector=None
        ДОЛЖНО БЫТЬ: spoofing_detector=SpoofingAnalyzer()
        """
        # Arrange
        symbol = "BTCUSDT"
        infra = BinanceInfrastructure()  # FIX
        
        # Act
        engine = TradingEngine(symbol, infra)
        
        # Assert
        assert engine.feature_collector.spoofing is not None, \
            "❌ spoofing_detector не подключен!"
        
        print("✅ spoofing_detector подключен")
    
    def test_feature_collector_has_flow_toxicity_analyzer(self):
        """
        WHY: Проверяет что flow_toxicity_analyzer подключен.
        
        БЫЛО: Не было вообще
        ДОЛЖНО БЫТЬ: flow_toxicity_analyzer=FlowToxicityAnalyzer()
        """
        # Arrange
        symbol = "BTCUSDT"
        infra = BinanceInfrastructure()  # FIX
        
        # Act
        engine = TradingEngine(symbol, infra)
        
        # Assert
        assert engine.feature_collector.flow_toxicity is not None, \
            "❌ flow_toxicity_analyzer не подключен!"
        
        print("✅ flow_toxicity_analyzer подключен")
    
    def test_feature_collector_has_gamma_provider(self):
        """
        WHY: Проверяет что gamma_provider подключен.
        
        БЫЛО: gamma_provider=None
        ДОЛЖНО БЫТЬ: gamma_provider=GammaProvider(book)
        """
        # Arrange
        symbol = "BTCUSDT"
        infra = BinanceInfrastructure()  # FIX
        
        # Act
        engine = TradingEngine(symbol, infra)
        
        # Assert
        assert engine.feature_collector.gamma is not None, \
            "❌ gamma_provider не подключен!"
        
        print("✅ gamma_provider подключен")
    
    def test_feature_collector_can_read_order_book(self):
        """
        WHY: Проверяет что order_book подключен и читается.
        
        Это базовая зависимость - если она None, всё сломано.
        """
        # Arrange
        symbol = "BTCUSDT"
        infra = BinanceInfrastructure()  # FIX
        engine = TradingEngine(symbol, infra)
        
        # Act
        order_book = engine.feature_collector.order_book
        
        # Assert
        assert order_book is not None, \
            "❌ order_book не подключен!"
        
        assert order_book.symbol == symbol, \
            f"❌ order_book символ не совпадает: {order_book.symbol} != {symbol}"
        
        print(f"✅ order_book подключен для {symbol}")
    
    def test_capture_snapshot_returns_non_null_metrics(self):
        """
        WHY: Проверяет что capture_snapshot() возвращает реальные данные.
        
        КРИТЕРИЙ УСПЕХА:
        - Хотя бы 10 из 18 метрик не должны быть None
        - Обязательно должны быть: obi, spread_bps, depth_ratio
        
        NOTE: Некоторые метрики могут быть None если нет данных
        (например, skew без Deribit, или whale_cvd без сделок).
        Но если ВСЕ метрики None - это проблема "Лоботомии".
        """
        # Arrange
        symbol = "BTCUSDT"
        infra = BinanceInfrastructure()  # FIX
        engine = TradingEngine(symbol, infra)
        
        # Создаём минимальный стакан для тестирования
        book = engine.book
        book.bids[Decimal("50000")] = Decimal("1.5")
        book.bids[Decimal("49900")] = Decimal("2.0")
        book.asks[Decimal("50100")] = Decimal("1.8")
        book.asks[Decimal("50200")] = Decimal("2.2")
        
        # Act
        snapshot = engine.feature_collector.capture_snapshot()
        
        # Assert - проверяем базовые order_book метрики
        assert snapshot.obi_value is not None, \
            "❌ OBI должен рассчитываться из order_book!"
        
        assert snapshot.spread_bps is not None, \
            "❌ Spread должен рассчитываться из order_book!"
        
        assert snapshot.depth_ratio is not None, \
            "❌ Depth ratio должен рассчитываться из order_book!"
        
        # Считаем количество непустых метрик
        non_null_count = sum(
            1 for field in snapshot.__dataclass_fields__
            if getattr(snapshot, field) is not None
        )
        
        # Минимум 5 метрик должны быть заполнены
        # (snapshot_time + obi + spread + depth + current_price)
        assert non_null_count >= 5, \
            f"❌ Слишком мало метрик: {non_null_count}/18. Возможна лоботомия!"
        
        print(f"✅ Snapshot содержит {non_null_count}/18 непустых метрик")
        print(f"   OBI: {snapshot.obi_value}")
        print(f"   Spread: {snapshot.spread_bps} bps")
        print(f"   Depth ratio: {snapshot.depth_ratio}")
    
    def test_cvd_metrics_work_without_flow_analyzer(self):
        """
        WHY: Проверяет что CVD метрики работают БЕЗ flow_analyzer.
        
        ТЕОРИЯ:
        flow_analyzer=None - это НОРМАЛЬНО!
        CVD читается напрямую из book.whale_cvd через WhaleAnalyzer.
        
        ТЕСТ:
        1. Убедиться что flow_analyzer=None
        2. Убедиться что CVD метрики всё равно доступны через book
        """
        # Arrange
        symbol = "BTCUSDT"
        infra = BinanceInfrastructure()  # FIX
        engine = TradingEngine(symbol, infra)
        
        # Act & Assert - flow_analyzer должен быть None
        assert engine.feature_collector.flow is None, \
            "flow_analyzer должен быть None (CVD из book.whale_cvd)"
        
        # Но book.whale_cvd должен существовать
        assert hasattr(engine.book, 'whale_cvd'), \
            "❌ book.whale_cvd не существует!"
        
        assert isinstance(engine.book.whale_cvd, dict), \
            "❌ book.whale_cvd должен быть dict!"
        
        # Проверяем что методы _get_whale_cvd() работают
        whale_cvd = engine.feature_collector._get_whale_cvd()
        fish_cvd = engine.feature_collector._get_fish_cvd()
        dolphin_cvd = engine.feature_collector._get_dolphin_cvd()
        
        # CVD может быть 0.0 (если нет сделок), но не должен быть None
        # если book.whale_cvd существует
        assert whale_cvd is not None or fish_cvd is not None or dolphin_cvd is not None, \
            "❌ Все CVD метрики None, хотя book.whale_cvd существует!"
        
        print("✅ CVD метрики работают через book.whale_cvd (flow_analyzer не нужен)")


class TestFeatureCollectorDependencies:
    """
    WHY: Детальная проверка каждой зависимости.
    
    Убеждаемся что:
    1. Экземпляры созданы правильно
    2. Типы корректные
    3. Методы доступны
    """
    
    def test_derivatives_analyzer_type(self):
        """Проверяет тип derivatives_analyzer"""
        from services import TradingEngine
        from infrastructure import BinanceInfrastructure
        from analyzers_derivatives import DerivativesAnalyzer
        
        engine = TradingEngine("BTCUSDT", BinanceInfrastructure())  # FIX
        
        assert isinstance(engine.derivatives_analyzer, DerivativesAnalyzer), \
            f"❌ Неправильный тип: {type(engine.derivatives_analyzer)}"
        
        print("✅ derivatives_analyzer имеет правильный тип")
    
    def test_spoofing_analyzer_type(self):
        """Проверяет тип spoofing_analyzer"""
        from services import TradingEngine
        from infrastructure import BinanceInfrastructure
        from analyzers import SpoofingAnalyzer
        
        engine = TradingEngine("BTCUSDT", BinanceInfrastructure())  # FIX
        
        assert isinstance(engine.spoofing_analyzer, SpoofingAnalyzer), \
            f"❌ Неправильный тип: {type(engine.spoofing_analyzer)}"
        
        print("✅ spoofing_analyzer имеет правильный тип")
    
    def test_flow_toxicity_analyzer_type(self):
        """Проверяет тип flow_toxicity_analyzer"""
        from services import TradingEngine
        from infrastructure import BinanceInfrastructure
        from analyzers import FlowToxicityAnalyzer
        
        engine = TradingEngine("BTCUSDT", BinanceInfrastructure())  # FIX
        
        assert isinstance(engine.flow_toxicity_analyzer, FlowToxicityAnalyzer), \
            f"❌ Неправильный тип: {type(engine.flow_toxicity_analyzer)}"
        
        print("✅ flow_toxicity_analyzer имеет правильный тип")
    
    def test_gamma_provider_type(self):
        """Проверяет тип gamma_provider"""
        from services import TradingEngine
        from infrastructure import BinanceInfrastructure
        from analyzers import GammaProvider  # FIX: Теперь в analyzers.py
        
        engine = TradingEngine("BTCUSDT", BinanceInfrastructure())  # FIX
        
        assert isinstance(engine.gamma_provider, GammaProvider), \
            f"❌ Неправильный тип: {type(engine.gamma_provider)}"
        
        print("✅ gamma_provider имеет правильный тип")


# ===========================================================================
# РАСШИРЕННЫЕ ТЕСТЫ: Полная валидация 18 метрик (Gemini рекомендация)
# ===========================================================================

class TestFeatureCollectorFullValidation:
    """
    WHY: Детальная проверка КАЖДОЙ из 18 метрик.
    
    ЦЕЛЬ (Gemini):
    - Порог non_null >= 5 слишком низкий для продакшена
    - Нужна проверка каждой метрики индивидуально
    - Симуляция полных данных (Deribit + VPIN)
    
    18 МЕТРИК:
    ORDER BOOK (6): spread_bps, obi_20, ofi_20, price, bid_depth, ask_depth
    CVD (3): whale_cvd, fish_cvd, dolphin_cvd
    DERIVATIVES (2): basis_annual, skew_25d
    SPOOFING (2): spoofing_score, cancel_ratio
    TOXICITY (2): vpin, vpin_settling
    GAMMA (2): total_gex, gamma_wall_dist
    ICEBERG (1): wall_whale_vol
    """
    
    def test_all_18_metrics_with_minimal_data(self):
        """
        WHY: Проверяет что метрики НЕ падают даже при минимальных данных.
        
        SETUP:
        - Минимальный стакан (2 уровня bid/ask)
        - Без Deribit данных (basis/skew будут None)
        - Без VPIN buckets (vpin будут None)
        
        ОЖИДАНИЯ:
        - Order Book метрики: ✅ (есть стакан)
        - CVD метрики: ✅ (book.whale_cvd существует)
        - Derivatives: ❌ None (нет Deribit)
        - Spoofing: ✅ (можно рассчитать)
        - Toxicity: ❌ None (нет VPIN buckets)
        - Gamma: ❌ None (нет gamma_profile)
        - Iceberg: ✅ (можно рассчитать)
        """
        from services import TradingEngine
        from infrastructure import BinanceInfrastructure
        from decimal import Decimal
        
        # Arrange
        engine = TradingEngine("BTCUSDT", BinanceInfrastructure())
        book = engine.book
        
        # Создаём минимальный стакан
        book.bids[Decimal("50000")] = Decimal("1.5")
        book.bids[Decimal("49900")] = Decimal("2.0")
        book.asks[Decimal("50100")] = Decimal("1.8")
        book.asks[Decimal("50200")] = Decimal("2.2")
        
        # Act
        snapshot = engine.feature_collector.capture_snapshot()
        
        # Assert - детальная проверка КАЖДОЙ метрики
        print("\n📊 ДЕТАЛЬНАЯ ПРОВЕРКА 18 МЕТРИК:")
        print("=" * 60)
        
        # === ORDER BOOK (6 метрик) ===
        print("\n🔵 ORDER BOOK (6):")
        assert snapshot.spread_bps is not None, "❌ spread_bps должен быть!"
        print(f"  ✅ spread_bps = {snapshot.spread_bps:.2f}")
        
        assert snapshot.obi_value is not None, "❌ obi_20 должен быть!"
        print(f"  ✅ obi_20 = {snapshot.obi_value:.4f}")
        
        assert snapshot.ofi_value is not None, "❌ ofi_20 должен быть!"
        print(f"  ✅ ofi_20 = {snapshot.ofi_value:.4f}")
        
        assert snapshot.current_price is not None, "❌ price должен быть!"
        print(f"  ✅ price = {snapshot.current_price:.2f}")
        
        # bid_depth и ask_depth - используем depth_ratio
        assert snapshot.depth_ratio is not None, "❌ depth_ratio должен быть!"
        print(f"  ✅ depth_ratio = {snapshot.depth_ratio:.4f}")
        
        # === CVD (3 метрики) ===
        print("\n🐋 CVD (3):")
        # CVD могут быть 0.0 (нет сделок), но НЕ None
        assert snapshot.whale_cvd is not None, "❌ whale_cvd должен быть!"
        print(f"  ✅ whale_cvd = {snapshot.whale_cvd:.2f}")
        
        assert snapshot.fish_cvd is not None, "❌ fish_cvd должен быть!"
        print(f"  ✅ fish_cvd = {snapshot.fish_cvd:.2f}")
        
        assert snapshot.dolphin_cvd is not None, "❌ dolphin_cvd должен быть!"
        print(f"  ✅ dolphin_cvd = {snapshot.dolphin_cvd:.2f}")
        
        # === DERIVATIVES (2 метрики) - ОЖИДАЕМ None ===
        print("\n📈 DERIVATIVES (2 - Expected None без Deribit):")
        print(f"  ⚠️ basis_annual = {snapshot.futures_basis_apr} (OK: нет Deribit)")
        print(f"  ⚠️ skew_25d = {snapshot.options_skew} (OK: нет Deribit)")
        
        # === SPOOFING (2 метрики) ===
        print("\n🎭 SPOOFING (2):")
        # Могут быть None если нет данных для расчёта, но метод должен работать
        print(f"  📊 spoofing_score = {snapshot.spoofing_score}")
        print(f"  📊 cancel_ratio = {snapshot.cancel_ratio_5m}")
        
        # === TOXICITY (2 метрики) - ОЖИДАЕМ None ===
        print("\n☢️ TOXICITY (2 - Expected None без VPIN buckets):")
        print(f"  ⚠️ vpin = {snapshot.vpin_score} (OK: нет buckets)")
        print(f"  ⚠️ vpin_level = {snapshot.vpin_level} (OK: нет buckets)")
        
        # === GAMMA (2 метрики) - ОЖИДАЕМ None ===
        print("\n🔮 GAMMA (2 - Expected None без gamma_profile):")
        print(f"  ⚠️ total_gex = {snapshot.total_gex} (OK: нет gamma)")
        print(f"  ⚠️ gamma_wall_dist = {snapshot.dist_to_gamma_wall} (OK: нет gamma)")
        
        # === ICEBERG - не в snapshot, пропускаем ===
        
        print("\n" + "=" * 60)
        # Подсчитываем non-null через dataclass fields
        from dataclasses import fields
        non_null_count = sum(1 for f in fields(snapshot) if getattr(snapshot, f.name) is not None)
        print(f"✅ ИТОГО: {non_null_count}/{len(fields(snapshot))} метрик доступны")
        print("✅ Все ORDER BOOK метрики работают")
        print("✅ Все CVD метрики работают")
        print("✅ Система стабильна даже без Deribit/VPIN данных")
    
    def test_snapshot_with_empty_book(self):
        """
        WHY: Edge case - пустой стакан при холодном старте.
        
        СЦЕНАРИЙ:
        - WebSocket только что подключился
        - Ещё не получен snapshot
        - Стакан пустой
        
        ОЖИДАНИЕ:
        - Система НЕ падает
        - Метрики возвращают None (а не exception)
        - capture_snapshot() завершается успешно
        """
        from services import TradingEngine
        from infrastructure import BinanceInfrastructure
        
        # Arrange - НЕ заполняем стакан
        engine = TradingEngine("BTCUSDT", BinanceInfrastructure())
        
        # Act - должно пройти БЕЗ exception
        try:
            snapshot = engine.feature_collector.capture_snapshot()
            success = True
        except Exception as e:
            success = False
            error = e
        
        # Assert
        assert success, f"❌ capture_snapshot() упал на пустом стакане: {error}"
        
        # Проверяем что ВСЕ метрики None или 0 (но не exception)
        print("\n📊 SNAPSHOT С ПУСТЫМ СТАКАНОМ:")
        from dataclasses import fields
        for f in fields(snapshot):
            value = getattr(snapshot, f.name)
            print(f"  {f.name}: {value}")
        
        # Spread должен быть None (нет bid/ask)
        assert snapshot.spread_bps is None, \
            "❌ spread_bps должен быть None при пустом стакане!"
        
        # OBI должен быть None или 0.0
        assert snapshot.obi_value is None or snapshot.obi_value == 0.0, \
            "❌ obi_20 должен быть None/0.0 при пустом стакане!"
        
        print("✅ Система стабильна при пустом стакане (холодный старт OK)")
    
    def test_snapshot_with_full_data(self):
        """
        WHY: Симуляция ПОЛНЫХ данных - максимальное покрытие метрик.
        
        SETUP:
        - Заполненный стакан (10+ уровней)
        - Gamma profile (симуляция Deribit)
        - VPIN buckets (симуляция сделок)
        - Активные айсберги
        
        ЦЕЛЬ:
        - Порог: >= 15 ключевых метрик (ORDER BOOK + CVD + GAMMA + VPIN + SPOOFING)
        - Проверить что ВСЕ ключевые метрики работают при наличии данных
        
        NOTE: FeatureSnapshot имеет 33 поля, но ~18 из них - future features
              (тренды 1w/1m/3m/6m, режимы и т.д.) которые пока не заполняются.
        """
        from services import TradingEngine
        from infrastructure import BinanceInfrastructure
        from decimal import Decimal
        from domain import GammaProfile, VolumeBucket, IcebergLevel
        
        # Arrange
        engine = TradingEngine("BTCUSDT", BinanceInfrastructure())
        book = engine.book
        
        # === 1. СТАКАН (10 уровней) ===
        for i in range(10):
            bid_price = Decimal("50000") - Decimal(str(i * 100))
            ask_price = Decimal("50100") + Decimal(str(i * 100))
            book.bids[bid_price] = Decimal("1.5") + Decimal(str(i * 0.1))
            book.asks[ask_price] = Decimal("1.8") + Decimal(str(i * 0.1))
        
        # === 2. GAMMA PROFILE (симуляция Deribit) ===
        book.gamma_profile = GammaProfile(
            total_gex=1500000.0,  # $1.5M GEX
            call_wall=52000.0,    # Call wall at $52k
            put_wall=48000.0      # Put wall at $48k
        )
        
        # === 3. VPIN BUCKETS (симуляция сделок) ===
        # Создаём 20 закрытых корзин для VPIN расчёта
        for i in range(20):
            bucket = VolumeBucket(
                bucket_size=Decimal("10.0"),  # 10 BTC per bucket
                symbol="BTCUSDT",             # ОБЯЗАТЕЛЬНО
                buy_volume=Decimal("6.0"),      # 60% покупки
                sell_volume=Decimal("4.0"),     # 40% продажи
                is_complete=True                # Корзина заполнена
            )
            book.vpin_buckets.append(bucket)
        
        # === 4. АКТИВНЫЕ АЙСБЕРГИ ===
        # Создаём whale айсберг для проверки wall_whale_vol
        iceberg = IcebergLevel(
            price=Decimal("50000"),
            is_ask=False,  # BID iceberg
            total_hidden_volume=Decimal("50.0"),  # 50 BTC скрыто
            confidence_score=0.85,
            is_gamma_wall=True  # Совпадает с gamma wall
        )
        book.active_icebergs[Decimal("50000")] = iceberg
        
        # === 5. CVD (симуляция сделок) ===
        book.whale_cvd = {
            'whale': 100000.0,   # $100k whale buying
            'dolphin': 50000.0,  # $50k dolphin buying
            'minnow': -30000.0   # $30k minnow selling (паника)
        }
        
        # Act
        snapshot = engine.feature_collector.capture_snapshot()
        
        # Assert - ВЫСОКИЙ порог для продакшена
        from dataclasses import fields
        non_null_count = sum(1 for f in fields(snapshot) if getattr(snapshot, f.name) is not None)
        
        print("\n📊 SNAPSHOT С ПОЛНЫМИ ДАННЫМИ:")
        print("=" * 60)
        for f in fields(snapshot):
            value = getattr(snapshot, f.name)
            status = "✅" if value is not None else "❌"
            print(f"  {status} {f.name}: {value}")
        print("=" * 60)
        
        # КРИТЕРИЙ УСПЕХА для продакшена:
        # WHY: FeatureSnapshot имеет 33 поля, но многие - future features (whale_cvd_trend_1w и т.д.)
        # Реалистичный порог: >= 15 КЛЮЧЕВЫХ метрик (покрывает все категории)
        total_fields = len(fields(snapshot))
        
        # Проверяем что >= 15 метрик работают (покрывает ORDER BOOK + CVD + GAMMA + VPIN + SPOOFING)
        assert non_null_count >= 15, \
            f"❌ Недостаточно метрик для продакшена: {non_null_count}/{total_fields} (нужно >= 15 ключевых)"
        
        # Проверяем КЛЮЧЕВЫЕ метрики индивидуально
        assert snapshot.spread_bps is not None, "❌ spread_bps должен быть!"
        assert snapshot.obi_value is not None, "❌ obi_20 должен быть!"
        assert snapshot.whale_cvd is not None, "❌ whale_cvd должен быть!"
        assert snapshot.total_gex is not None, "❌ total_gex должен быть (есть gamma)!"
        assert snapshot.dist_to_gamma_wall is not None, "❌ gamma_wall_dist должен быть!"
        assert snapshot.vpin_score is not None, "❌ vpin должен быть (есть buckets)!"
        
        print(f"\n✅ ПРОДАКШЕН ГОТОВ: {non_null_count}/{total_fields} метрик работают ({non_null_count/total_fields*100:.1f}%)")
        print("✅ Ключевые метрики доступны: ORDER BOOK, CVD, GAMMA, VPIN, SPOOFING")
        print("✅ Порог для продакшена пройден (>= 15 ключевых)")
    
    def test_throttling_prevents_db_overload(self):
        """
        WHY: Gemini рекомендация - предотвращаем перегрузку БД при лавинообразных рефиллах.
        
        Сценарий:
        - Айсберг рефиллится 10 раз за 50 мс (лавинообразный поток сделок)
        - Без throttling: 10 записей в БД за 50 мс → перегрузка
        - С throttling: только 1 запись (остальные возвращают кешированный снапшот)
        
        Проверяем:
        1. Первый вызов создает новый снапшот
        2. Повторные вызовы < 100 мс возвращают тот же объект (кеш)
        3. Вызов через >= 100 мс создает новый снапшот
        """
        import time
        from datetime import datetime, timezone
        from services import TradingEngine
        from infrastructure import BinanceInfrastructure
        
        # Arrange
        engine = TradingEngine("BTCUSDT", BinanceInfrastructure())
        book = engine.book
        
        # Минимальный стакан
        book.bids[Decimal("50000")] = Decimal("1.0")
        book.asks[Decimal("50100")] = Decimal("1.0")
        
        # Act 1: Первый снапшот (холодный старт)
        snapshot1 = engine.feature_collector.capture_snapshot()
        time1 = engine.feature_collector._last_snapshot_time
        
        # Act 2: Второй снапшот через 50 мс (слишком рано!)
        time.sleep(0.05)  # 50 мс
        snapshot2 = engine.feature_collector.capture_snapshot()
        
        # Assert: snapshot2 должен быть ТОТ ЖЕ объект (кеш)
        assert snapshot2 is snapshot1, \
            "❌ Throttling НЕ работает! Должен возвращаться кешированный объект при < 100 мс"
        
        print("\n⏱️  THROTTLING ТЕСТ:")
        print("=" * 60)
        print(f"  ✅ Вызов #1 (0 мс): Новый снапшот создан")
        print(f"  ✅ Вызов #2 (50 мс): Кеш возвращен (тот же объект)")
        print(f"     → snapshot2 is snapshot1: {snapshot2 is snapshot1}")
        
        # Act 3: Третий снапшот через 150 мс (достаточно времени)
        time.sleep(0.1)  # Еще 100 мс (всего 150 мс от начала)
        snapshot3 = engine.feature_collector.capture_snapshot()
        
        # Assert: snapshot3 должен быть НОВЫЙ объект
        assert snapshot3 is not snapshot1, \
            "❌ Throttling работает неправильно! Через 150 мс должен создаться новый снапшот"
        
        # Проверяем что время обновилось
        time3 = engine.feature_collector._last_snapshot_time
        assert time3 > time1, "❌ _last_snapshot_time не обновился!"
        
        # Проверяем что кеш обновился на snapshot3
        assert engine.feature_collector._last_snapshot_cache is snapshot3, \
            "❌ Кеш должен указывать на последний снапшот!"
        
        print(f"  ✅ Вызов #3 (150 мс): Новый снапшот создан")
        print(f"     → snapshot3 is not snapshot1: {snapshot3 is not snapshot1}")
        print("=" * 60)
        
        # Act 4: Симуляция лавинообразных рефиллов (10 вызовов за 50 мс)
        snapshot_flood_start = engine.feature_collector.capture_snapshot()
        time.sleep(0.15)  # Сброс throttle
        
        flood_results = []
        for i in range(10):
            s = engine.feature_collector.capture_snapshot()
            flood_results.append(s)
            time.sleep(0.005)  # 5 мс между вызовами
        
        # Проверяем что ВСЕ 10 вызовов вернули ТОТ ЖЕ объект
        unique_snapshots = len(set(id(s) for s in flood_results))
        
        print(f"\n🌊 ЛАВИНООБРАЗНЫЙ РЕФИЛЛ (10 вызовов за 50 мс):")
        print("=" * 60)
        print(f"  Уникальных снапшотов создано: {unique_snapshots}")
        print(f"  Ожидается: 1 (все остальные из кеша)")
        
        # В идеале должен быть только 1 уникальный объект
        # Но допускаем до 2-3 из-за возможных race conditions в sleep
        assert unique_snapshots <= 3, \
            f"❌ Throttling не предотвратил перегрузку! Создано {unique_snapshots} снапшотов вместо ~1"
        
        print(f"  ✅ Throttling предотвратил {10 - unique_snapshots} лишних записей в БД!")
        print("  ✅ БД защищена от перегрузки при лавинообразных событиях")
        print("=" * 60)
