import asyncio
import aiohttp
import json
import websockets
import pandas as pd
import numpy as np
from scipy.stats import norm
from domain import GammaProfile
from decimal import Decimal
from typing import AsyncGenerator, Dict, Any, Optional
from domain import OrderBookUpdate, TradeEvent, GammaProfile
from abc import ABC, abstractmethod
import time
from heapq import heappush, heappop
from typing import List, Tuple, Any
from collections import deque
import statistics


class LatencyMonitor:
    """
    WHY: Мониторинг задержек сети для адаптивной синхронизации потоков.
    
    Отслеживает:
    - RTT (Round-Trip Time): Разница между event_time и arrival_time
    - Джиттер (σ_jit): Стандартное отклонение задержек
    
    Формула адаптивной задержки (из документации):
    T_GU(t) = μ_RTT(t) + μ_proc(t) + k · σ_jit(t)
    
    Где:
    - μ_RTT: среднее RTT
    - μ_proc: среднее время обработки биржи (~5-10ms для Binance)
    - k: коэффициент уверенности (3 для 99.7% покрытия)
    - σ_jit: стандартное отклонение (джиттер)
    """
    
    def __init__(self, window_size: int = 100, k: float = 3.0, base_processing_ms: float = 10.0):
        """
        Args:
            window_size: Размер скользящего окна для расчета статистики
            k: Коэффициент для σ (правило трёх сигм = 3.0)
            base_processing_ms: Базовое время обработки биржи (Binance ~10ms)
        """
        self.window_size = window_size
        self.k = k
        self.base_processing_ms = base_processing_ms
        
        # Скользящее окно задержек (в миллисекундах)
        self.latencies = deque(maxlen=window_size)
        
        # Минимальная задержка (защита от нуля)
        self.min_delay_ms = 10.0
        self.max_delay_ms = 500.0  # Защита от аномально высоких значений
    
    def record_latency(self, event_time_ms: int, arrival_time_ms: float):
        """
        Записывает задержку между временем события и временем прибытия.
        
        Args:
            event_time_ms: Время события от биржи (в миллисекундах)
            arrival_time_ms: Локальное время прибытия (time.time() * 1000)
        """
        # RTT = arrival_time - event_time (может быть отрицательным если часы рассинхронены)
        latency_ms = abs(arrival_time_ms - event_time_ms)
        
        # Фильтруем аномальные значения (>5 секунд = явная рассинхронизация часов)
        if latency_ms < 5000:
            self.latencies.append(latency_ms)
    
    def get_adaptive_delay(self) -> float:
        """
        Вычисляет адаптивную задержку по формуле:
        T_GU = μ_RTT + μ_proc + k · σ_jit
        
        Returns:
            Рекомендуемая задержка в миллисекундах
        """
        if len(self.latencies) < 10:  # Недостаточно данных
            return 50.0  # Возвращаем дефолтное значение
        
        # Расчет статистики
        mean_rtt = statistics.mean(self.latencies)
        stdev_jitter = statistics.stdev(self.latencies) if len(self.latencies) > 1 else 0.0
        
        # Формула адаптивной задержки
        adaptive_delay = mean_rtt + self.base_processing_ms + (self.k * stdev_jitter)
        
        # Ограничиваем диапазон
        adaptive_delay = max(self.min_delay_ms, min(adaptive_delay, self.max_delay_ms))
        
        return adaptive_delay
    
    def get_stats(self) -> dict:
        """
        Возвращает текущую статистику для отладки.
        
        Returns:
            Dict с ключами: mean_rtt, stdev_jitter, adaptive_delay, sample_size
        """
        if len(self.latencies) < 2:
            return {
                'mean_rtt': 0.0,
                'stdev_jitter': 0.0,
                'adaptive_delay': 50.0,
                'sample_size': len(self.latencies)
            }
        
        mean_rtt = statistics.mean(self.latencies)
        stdev_jitter = statistics.stdev(self.latencies)
        
        return {
            'mean_rtt': round(mean_rtt, 2),
            'stdev_jitter': round(stdev_jitter, 2),
            'adaptive_delay': round(self.get_adaptive_delay(), 2),
            'sample_size': len(self.latencies)
        }


class ReorderingBuffer:
    """
    Буфер переупорядочивания (Re-ordering Buffer).
    Решает проблему Race Condition, когда depthUpdate приходит раньше aggTrade.
    Источник: Часть 2.2 вашего документа [cite: 98-99].
    """
    def __init__(self, delay_ms: int = 50):
        self.delay_sec = delay_ms / 1000.0
        self.buffer: List[Tuple[float, int, Any]] = [] # (event_time, priority, item)
        self.counter = 0
        
    def add(self, item, event_time: int, priority: int):
        """
        Добавляет элемент в буфер.
        priority: 0 для Trade (высший), 1 для Depth (низший).
        Это гарантирует, что при равном времени Trade обработается первым[cite: 108].
        """
        # Binance event_time в мс, приводим к секундам для удобства
        et = event_time / 1000.0
        self.counter += 1

        # Используем кучу (heap) для автоматической сортировки при вставке
        heappush(self.buffer, (et, priority, self.counter, item))

     

    def pop_ready(self) -> List[Any]:
        """
        Возвращает список событий, которые "созрели" (старше чем delay).
        """
        now = time.time() # Текущее локальное время
        # В реальной HFT системе здесь используют arrival_time, 
        # но для Python и Binance event_time более надежен для сортировки.
        
        ready_items = []
        
        # Смотрим на самый старый элемент в куче (без удаления)
        while self.buffer:
            event_time, priority, item = self.buffer[0]
            
            # Эвристика: Мы ждем, пока "виртуальное время" события не отстанет от реального на delay.
            # Но так как event_time - это время биржи, а now - наше, они рассинхронены.
            # Упрощенный подход для MVP:
            # Мы просто накапливаем буфер. В реальном asyncio loop мы будем вызывать pop 
            # с задержкой. Здесь мы вернем всё, что есть, полагаясь на то, 
            # что потребитель вызывает нас с паузой.
            
            # ПРАВИЛЬНЫЙ ПОДХОД ДЛЯ ASYNCIO:
            # Мы просто сортируем всё что есть. Логика ожидания будет в services.py
            break 
            
        return []

    def get_all_sorted(self):
        """
        Выгружает ВЕСЬ буфер в отсортированном виде и очищает его.
        Сортировка: Сначала по времени, если время совпадает (в пределах мс) -> по приоритету.
        """
        # heappop всегда возвращает наименьший элемент (самый старый и приоритетный)
        result = []
        while self.buffer:
            _, _, _, item = heappop(self.buffer)
            result.append(item)
        return result


class IMarketDataSource(ABC):
    @abstractmethod
    async def get_snapshot(self, symbol: str) -> Dict[str, Any]:
        """Возвращает dict с ключами: bids, asks, lastUpdateId"""
        pass
    
    @abstractmethod
    async def listen_updates(self, symbol: str) -> AsyncGenerator[OrderBookUpdate, None]:
        pass
    
    @abstractmethod
    async def listen_trades(self, symbol: str) -> AsyncGenerator[TradeEvent, None]:
        pass


class BinanceInfrastructure(IMarketDataSource):
    """Production-ready реализация для Binance"""
    WS_URL = "wss://stream.binance.com:9443/ws"
    REST_URL = "https://api.binance.com/api/v3/depth"
    
    async def get_snapshot(self, symbol: str, limit: int = 1000) -> Dict[str, Any]:
        """
        Скачивает полный снапшот через REST API.
        
        Returns:
            {
                'bids': [(price, qty), ...],
                'asks': [(price, qty), ...],
                'lastUpdateId': int
            }
        """
        url = f"{self.REST_URL}?symbol={symbol}&limit={limit}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    raise Exception(f"Failed to get snapshot: {response.status}")
                
                data = await response.json()
                
                return {
                    'bids': [(Decimal(price), Decimal(qty)) for price, qty in data['bids']],
                    'asks': [(Decimal(price), Decimal(qty)) for price, qty in data['asks']],
                    'lastUpdateId': data['lastUpdateId']
                }

    async def listen_updates(self, symbol: str) -> AsyncGenerator[OrderBookUpdate, None]:
        """Поток обновлений стакана (Depth Stream)"""
        url = f"{self.WS_URL}/{symbol.lower()}@depth@100ms"  # 100ms для минимальной задержки
        
        async for msg in self._ws_connect_with_retry(url):
            data = json.loads(msg)
            
            # Binance отправляет:
            # {
            #   "e": "depthUpdate",
            #   "E": event_time,
            #   "s": "BTCUSDT",
            #   "U": first_update_id,
            #   "u": final_update_id,
            #   "b": [["price", "qty"], ...],
            #   "a": [["price", "qty"], ...]
            # }
            
            yield OrderBookUpdate(
                first_update_id=data['U'],
                final_update_id=data['u'],
                event_time=data['E'],  # WHY: Биржевое Event Time (Fix: Timestamp Skew)
                bids=[(Decimal(p), Decimal(q)) for p, q in data.get('b', [])],
                asks=[(Decimal(p), Decimal(q)) for p, q in data.get('a', [])]
            )

    async def listen_trades(self, symbol: str) -> AsyncGenerator[TradeEvent, None]:
        """Поток сделок (Trade Stream)"""
        url = f"{self.WS_URL}/{symbol.lower()}@aggTrade"
        
        async for msg in self._ws_connect_with_retry(url):
            data = json.loads(msg)
            
            # Binance aggTrade:
            # {
            #   "e": "aggTrade",
            #   "E": event_time,
            #   "s": "BTCUSDT",
            #   "a": agg_trade_id,
            #   "p": "60000.00",  # price
            #   "q": "0.5",        # quantity
            #   "T": 1638747660000, # trade_time
            #   "m": true/false    # is_buyer_maker
            # }
            
            yield TradeEvent(
                price=Decimal(data['p']),
                quantity=Decimal(data['q']),
                is_buyer_maker=data['m'],
                event_time=data['T'],
                trade_id=data.get('a')
            )

    async def _ws_connect_with_retry(self, url: str, max_retries: int = 999) -> AsyncGenerator[str, None]:
        """
        WebSocket подключение с автоматическим реконнектом.
        КРИТИЧНО для production: Нельзя терять данные при временных сбоях сети.
        """
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                async with websockets.connect(url) as ws:
                    print(f"✅ Connected to {url}")
                    retry_count = 0  # Сброс счетчика после успешного подключения
                    
                    while True:
                        msg = await ws.recv()
                        yield msg
                        
            except websockets.ConnectionClosed as e:
                retry_count += 1
                backoff = min(2 ** retry_count, 60)  # Exponential backoff, макс 60 сек
                print(f"⚠️ WebSocket closed: {e}. Retry {retry_count}/{max_retries} in {backoff}s...")
                await asyncio.sleep(backoff)
                
            except Exception as e:
                retry_count += 1
                print(f"❌ WebSocket error: {e}. Retry {retry_count}/{max_retries}...")
                await asyncio.sleep(2)
        
        raise Exception(f"Failed to connect after {max_retries} retries")


# Эмуляция для тестирования (без реального API)
class BinanceMockInfrastructure(IMarketDataSource):
    """Мок для тестирования без подключения к бирже"""
    
    async def get_snapshot(self, symbol: str) -> Dict[str, Any]:
        print(f"🎭 [MOCK] Скачиваем снапшот для {symbol}")
        await asyncio.sleep(0.5)
        return {
            'bids': [(Decimal("60000.00"), Decimal("1.5")), (Decimal("59900.00"), Decimal("2.0"))],
            'asks': [(Decimal("60100.00"), Decimal("0.5")), (Decimal("60200.00"), Decimal("1.2"))],
            'lastUpdateId': 1000
        }
    
    async def listen_updates(self, symbol: str) -> AsyncGenerator[OrderBookUpdate, None]:
        """Генерирует фейковые обновления"""
        update_id = 1001
        while True:
            await asyncio.sleep(0.1)
            yield OrderBookUpdate(
                first_update_id=update_id,
                final_update_id=update_id,
                event_time=int(time.time() * 1000),  # WHY: Реалистичное время для тестов
                bids=[(Decimal("60000.00"), Decimal("1.6"))],  # Увеличили объем на bid
                asks=[]
            )
            update_id += 1
    
    async def listen_trades(self, symbol: str) -> AsyncGenerator[TradeEvent, None]:
        """Генерирует фейковые сделки"""
        while True:
            await asyncio.sleep(0.5)
            yield TradeEvent(
                price=Decimal("60050.00"),
                quantity=Decimal("0.5"),
                is_buyer_maker=False,
                event_time=1638747660000
            )

class DeribitInfrastructure:
    """
    Асинхронная реализация логики из deribit_loader.py
    """
    BASE_URL = "https://www.deribit.com/api/v2/public"

    async def get_gamma_data(self, currency="BTC") -> Optional[Dict[str, Any]]:
        """
        WHY: Загружает RAW данные опционов (IO только, математика в Analyzer).
        
        Clean Architecture Pattern:
        - Infrastructure: Только HTTP запросы + подготовка данных
        - Analyzer: Математика (Black-Scholes, агрегация GEX)
        - Services: Оркестрация (fetch → analyze → cache)
        
        Returns:
            {
                'strikes': List[float],
                'types': List[str],       # 'C' or 'P'
                'expiry_years': List[float],
                'ivs': List[float],       # Implied Volatility
                'open_interest': List[float],
                'underlying_price': float
            }
            None если нет данных
        """
        url = f"{self.BASE_URL}/get_book_summary_by_currency"
        params = {"currency": currency, "kind": "option"}
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as resp:
                    # ВАЖНО: Обработка Rate Limit из вашего файла
                    if resp.status == 429:
                        print(f"⚠️ Deribit Rate Limit! Пропускаем обновление.")
                        return None
                    
                    if resp.status != 200:
                        print(f"⚠️ Deribit API Error: {resp.status}")
                        return None
                        
                    data = await resp.json()
            
            if 'result' not in data: return None
            
            # Выносим подготовку данных Pandas в отдельный поток,
            # чтобы не блокировать обработку стакана Binance.
            loop = asyncio.get_running_loop()
            prepared_data = await loop.run_in_executor(None, self._prepare_gamma_data_sync, data['result'])
            return prepared_data

        except Exception as e:
            print(f"❌ Deribit Connection Error: {e}")
            return None

    def _prepare_gamma_data_sync(self, raw_data) -> Optional[Dict[str, Any]]:
        """
        WHY: Подготовка данных (IO только, математика в Analyzer).
        
        Процесс:
        1. Парсинг инструментов (strike, type, expiry)
        2. Фильтрация по времени (исключаем истекшие)
        3. Расчет IV (mark_iv или (bid_iv + ask_iv)/2)
        4. Возврат RAW данных
        
        Математика (Black-Scholes, GEX агрегация) в DerivativesAnalyzer
        """
        try:
            df = pd.DataFrame(raw_data)
            
            # [CHECK 1] Гарантия колонок (как в строках 64-69 оригинала)
            # Если биржа прислала пустой стакан по опциону, этих полей может не быть
            needed_cols = ['instrument_name', 'mark_price', 'underlying_price', 
                           'open_interest', 'bid_iv', 'ask_iv', 'mark_iv']
            for col in needed_cols:
                if col not in df.columns:
                    df[col] = np.nan

            # [CHECK 2] Парсинг названия (как в строках 73-83 оригинала)
            def parse(name):
                try:
                    parts = name.split('-')
                    # Возвращаем: Страйк, Тип, Дата
                    return float(parts[2]), parts[3], pd.to_datetime(parts[1], utc=True, format='mixed')
                except: return None, None, None

            df[['strike', 'type', 'expiry']] = df['instrument_name'].apply(lambda x: pd.Series(parse(x)))
            df = df.dropna(subset=['strike'])
            
            # [CHECK 3] Фильтр времени (как в строках 90-99 оригинала)
            now = pd.Timestamp.now(tz='utc')
            df['years'] = (df['expiry'] - now).dt.total_seconds() / (365 * 24 * 3600)
            # Убираем экспирировавшиеся или те, что истекают прямо сейчас (деление на 0)
            df = df[df['years'] > 0.002] 
            
            # [CHECK 4] Умный расчет IV (как в строках 106-110 оригинала)
            # Приоритет Mark IV -> Если нет, то (Bid+Ask)/2
            df['iv'] = df['mark_iv'] / 100.0
            mask_nan = df['iv'].isna()
            df.loc[mask_nan, 'iv'] = df.loc[mask_nan, ['bid_iv', 'ask_iv']].mean(axis=1) / 100.0
            
            # Удаляем те, где IV так и не нашли
            df = df.dropna(subset=['iv'])

            # Возвращаем RAW данные для DerivativesAnalyzer
            if df.empty: return None
            
            return {
                'strikes': df['strike'].tolist(),
                'types': df['type'].tolist(),
                'expiry_years': df['years'].tolist(),
                'ivs': df['iv'].tolist(),
                'open_interest': df['open_interest'].tolist(),
                'underlying_price': df['underlying_price'].iloc[0]  # Одинаково для всех
            }
        except Exception as e:
            # print(f"Math Error in GEX: {e}") # Для отладки
            return None
    
    # === РЕФАКТОРИНГ: Clean Architecture - IO Only (ШАГ 6.1) ===
    
    async def get_futures_data(self, currency="BTC") -> Optional[Dict[str, Any]]:
        """
        WHY: Загружает RAW данные фьючерсов (IO только, математика в Analyzer).
        
        Clean Architecture Pattern:
        - Infrastructure: Только HTTP запросы
        - Analyzer: Математика (calculate_annualized_basis)
        - Services: Оркестрация (fetch → analyze → cache)
        
        Returns:
            {
                'spot_price': float,
                'futures_price': float,
                'days_to_expiry': float
            }
            None если нет данных
        """
        url = f"{self.BASE_URL}/get_instruments"
        params = {"currency": currency, "kind": "future", "expired": "false"}
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as resp:
                    if resp.status == 429:
                        return None
                    if resp.status != 200:
                        return None
                    
                    data = await resp.json()
            
            if 'result' not in data or not data['result']:
                return None
            
            # Ищем ближайший квартальный контракт (например, BTC-28JUN25)
            futures = [f for f in data['result'] if f['settlement_period'] == 'month']
            
            if not futures:
                return None
            
            # Берем первый активный контракт (наибольшая ликвидность)
            future = sorted(futures, key=lambda x: x.get('expiration_timestamp', 0))[0]
            
            # Получаем ticker для mark_price
            ticker_url = f"{self.BASE_URL}/ticker"
            ticker_params = {"instrument_name": future['instrument_name']}
            
            async with aiohttp.ClientSession() as session:
                async with session.get(ticker_url, params=ticker_params) as resp:
                    if resp.status != 200:
                        return None
                    ticker_data = await resp.json()
            
            if 'result' not in ticker_data:
                return None
            
            result = ticker_data['result']
            futures_price = result.get('mark_price')  # F
            spot_price = result.get('underlying_index')  # S
            expiration_ts = future.get('expiration_timestamp')
            
            if not all([futures_price, spot_price, expiration_ts]):
                return None
            
            # Расчет DTE (Days To Expiration)
            now_ts = pd.Timestamp.now(tz='utc').timestamp() * 1000  # в миллисекундах
            days_to_expiry = (expiration_ts - now_ts) / (1000 * 60 * 60 * 24)
            
            if days_to_expiry <= 0:
                return None  # Контракт уже истек
            
            # Возвращаем RAW данные (математика в DerivativesAnalyzer)
            return {
                'spot_price': spot_price,
                'futures_price': futures_price,
                'days_to_expiry': days_to_expiry
            }
            
        except Exception as e:
            # print(f"Basis calculation error: {e}")
            return None
    
    # === РЕФАКТОРИНГ: Clean Architecture - IO Only (ШАГ 6.2) ===
    
    async def get_options_data(self, currency="BTC") -> Optional[Dict[str, Any]]:
        """
        WHY: Загружает RAW данные опционов (IO только, математика в Analyzer).
        
        Clean Architecture Pattern:
        - Infrastructure: Только HTTP запросы
        - Analyzer: Математика (calculate_options_skew)
        - Services: Оркестрация (fetch → analyze → cache)
        
        Returns:
            {
                'put_iv_25d': float,  # 25-delta OTM Put IV
                'call_iv_25d': float  # 25-delta OTM Call IV
            }
            None если нет данных
        """
        url = f"{self.BASE_URL}/get_book_summary_by_currency"
        params = {"currency": currency, "kind": "option"}
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as resp:
                    if resp.status == 429:
                        return None
                    if resp.status != 200:
                        return None
                    
                    data = await resp.json()
            
            if 'result' not in data or not data['result']:
                return None
            
            # Фильтруем опционы с expiry ~30 дней
            df = pd.DataFrame(data['result'])
            
            # Парсинг инструмента (BTC-31JAN25-100000-C)
            def parse_instrument(name):
                try:
                    parts = name.split('-')
                    return {
                        'strike': float(parts[2]),
                        'type': parts[3],  # 'C' or 'P'
                        'expiry': pd.to_datetime(parts[1], utc=True, format='mixed')
                    }
                except:
                    return None
            
            df['parsed'] = df['instrument_name'].apply(parse_instrument)
            df = df.dropna(subset=['parsed'])
            
            df['strike'] = df['parsed'].apply(lambda x: x['strike'])
            df['type'] = df['parsed'].apply(lambda x: x['type'])
            df['expiry'] = df['parsed'].apply(lambda x: x['expiry'])
            
            # Фильтр по времени (25-35 дней до expiry)
            now = pd.Timestamp.now(tz='utc')
            df['days_to_expiry'] = (df['expiry'] - now).dt.total_seconds() / (60 * 60 * 24)
            df = df[(df['days_to_expiry'] >= 25) & (df['days_to_expiry'] <= 35)]
            
            if df.empty:
                return None
            
            # Ищем 25-delta options (OTM)
            # Упрощение: берем опционы с strike ~5% OTM
            spot_price = df['underlying_price'].iloc[0]
            
            # Puts: strike < spot (OTM puts)
            puts = df[(df['type'] == 'P') & (df['strike'] < spot_price * 0.95)]
            # Calls: strike > spot (OTM calls)
            calls = df[(df['type'] == 'C') & (df['strike'] > spot_price * 1.05)]
            
            if puts.empty or calls.empty:
                return None
            
            # Берем среднюю IV
            put_iv_avg = puts['mark_iv'].mean()
            call_iv_avg = calls['mark_iv'].mean()
            
            if pd.isna(put_iv_avg) or pd.isna(call_iv_avg):
                return None
            
            # Возвращаем RAW данные (математика в DerivativesAnalyzer)
            return {
                'put_iv_25d': put_iv_avg,
                'call_iv_25d': call_iv_avg
            }
            
        except Exception as e:
            # print(f"Skew calculation error: {e}")
            return None


# ===========================================================================
# HELPER FUNCTIONS: Volume & Market Data
# ===========================================================================

async def get_average_daily_volume(
    symbol: str,  # ОБЯЗАТЕЛЬНЫЙ параметр (multi-asset support)
    days: int = 20,
    exchange: str = "binance"
) -> Optional[float]:
    """
    WHY: Получить средний дневной объём за последние N дней для нормализации GEX.
    
    === GEMINI FIX: GEX Normalization ===
    Используется для расчёта total_gex_normalized = total_gex / ADV_20d.
    
    === MULTI-ASSET SUPPORT ===
    Symbol ОБЯЗАТЕЛЕН - нет дефолтов для BTC/ETH/SOL.
    
    Логика:
    - Запрос к Binance Klines API для получения дневных свечей
    - Извлечение volume из каждой свечи
    - Расчёт среднего за N дней
    
    Args:
        symbol: Торговая пара ("BTCUSDT", "ETHUSDT", "SOLUSDT" и т.д.) - ОБЯЗАТЕЛЬНО!
        days: Количество дней для усреднения (default: 20)
        exchange: Биржа (default: "binance")
    
    Returns:
        Средний дневной объём в USD или None при ошибке
    
    Example:
        >>> adv_20d = await get_average_daily_volume("BTCUSDT", days=20)
        >>> # adv_20d ≈ 2_000_000_000.0 (2B USD)
        >>> 
        >>> # Для ETH
        >>> adv_eth = await get_average_daily_volume("ETHUSDT", days=20)
    """
    if exchange == "binance":
        url = "https://api.binance.com/api/v3/klines"
        params = {
            "symbol": symbol,
            "interval": "1d",  # Дневные свечи
            "limit": days      # Последние N дней
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        return None
                    
                    data = await resp.json()
            
            if not data or len(data) < days:
                return None
            
            # Klines format: [timestamp, open, high, low, close, volume, ...]
            # Volume находится на индексе 5
            volumes = [float(candle[5]) * float(candle[4]) for candle in data]  # volume * close_price = USD volume
            
            # Средний дневной объём
            avg_volume = sum(volumes) / len(volumes)
            
            return avg_volume
            
        except Exception as e:
            # Логирование ошибки (опционально)
            # print(f"ADV calculation error for {symbol}: {e}")
            return None
    
    else:
        # TODO: Поддержка других бирж (Deribit, OKX, etc.)
        return None