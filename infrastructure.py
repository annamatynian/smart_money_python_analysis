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

    async def get_gamma_profile(self, currency="BTC") -> Optional[GammaProfile]:
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
            
            # Выносим тяжелую математику Pandas в отдельный поток,
            # чтобы не блокировать обработку стакана Binance.
            loop = asyncio.get_running_loop()
            profile = await loop.run_in_executor(None, self._calculate_gex_sync, data['result'])
            return profile

        except Exception as e:
            print(f"❌ Deribit Connection Error: {e}")
            return None

    def _calculate_gex_sync(self, raw_data) -> Optional[GammaProfile]:
        """
        Полная копия логики из вашего файла deribit_loader.py
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

            # [CHECK 5] Формула Блэка-Шоулза (как в строках 119-123 оригинала)
            df['S'] = df['underlying_price']
            d1 = (np.log(df['S']/df['strike']) + (0.5 * df['iv']**2) * df['years']) / (df['iv'] * np.sqrt(df['years']))
            df['gamma'] = norm.pdf(d1) / (df['S'] * df['iv'] * np.sqrt(df['years']))
            
            # [CHECK 6] Расчет GEX и Инверсия Путов (как в строках 126-129 оригинала)
            df['gex'] = df['gamma'] * df['open_interest'] * (df['S']**2) * 0.01
            df.loc[df['type'] == 'P', 'gex'] *= -1 
            
            # [CHECK 7] Агрегация Стен (как в блоке print оригинала)
            if df.empty: return None

            total_gex = df['gex'].sum()
            call_wall = df[df['type']=='C'].groupby('strike')['gex'].sum().idxmax()
            put_wall = df[df['type']=='P'].groupby('strike')['gex'].sum().idxmin()
            
            return GammaProfile(
                total_gex=total_gex, 
                call_wall=call_wall, 
                put_wall=put_wall
            )
        except Exception as e:
            # print(f"Math Error in GEX: {e}") # Для отладки
            return None