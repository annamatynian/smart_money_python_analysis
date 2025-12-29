# ===========================================================================
# DERIVATIVES DATA FETCHER: Binance Futures + Deribit Options
# ===========================================================================

"""
WHY: Периодический сбор данных деривативов для SmartCandle метрик.

Источники:
1. Binance Futures API: Spot price, Futures price, Open Interest
2. Deribit API: Options IV (25-delta Put/Call), Options volume

Используется для расчета:
- Annualized Futures Basis (перегрев/дно рынка)
- Options Skew (страх институционалов)
- OI Delta (топливо тренда)
"""

import asyncio
import aiohttp
from typing import Optional, Dict, Tuple
from decimal import Decimal
from datetime import datetime, timedelta
import logging

# WHY: Используем централизованную конфигурацию вместо hardcoded symbols
from config import AssetConfig, get_config

logger = logging.getLogger(__name__)


class DerivativesDataFetcher:
    """
    WHY: Асинхронный fetcher для данных деривативов (Futures + Options).
    
    Теория (документ "Анализ данных смарт-мани"):
    - Futures Basis показывает перегрев рынка
    - Options Skew показывает страх институционалов
    - OI Delta показывает силу тренда
    
    API endpoints:
    - Binance Spot: https://api.binance.com/api/v3/ticker/price
    - Binance Futures: https://fapi.binance.com/fapi/v1/ticker/price
    - Binance OI: https://fapi.binance.com/fapi/v1/openInterest
    - Deribit: https://www.deribit.com/api/v2/public/get_book_summary_by_currency
    """
    
    def __init__(self, symbol: str = 'BTCUSDT'):
        """
        WHY: Принимает полный symbol (BTCUSDT) и загружает конфигурацию.
        
        Args:
            symbol: Trading pair ('BTCUSDT', 'ETHUSDT', 'SOLUSDT')
        """
        # WHY: Используем AssetConfig для централизованного управления
        self.config = get_config(symbol)
        
        # WHY: Извлекаем base symbol для API запросов
        # BTCUSDT → BTC, ETHUSDT → ETH
        self.symbol = self.config.symbol
        self.base_symbol = self.symbol.replace('USDT', '')
        
        # API endpoints
        self.binance_spot_url = "https://api.binance.com/api/v3/ticker/price"
        self.binance_futures_url = "https://fapi.binance.com/fapi/v1/ticker/price"
        self.binance_oi_url = "https://fapi.binance.com/fapi/v1/openInterest"
        self.deribit_url = "https://www.deribit.com/api/v2/public"
        
        # Cached OI для расчета delta
        self.last_oi: Optional[float] = None
        self.last_oi_timestamp: Optional[datetime] = None
    
    async def fetch_spot_price(self) -> Optional[Decimal]:
        """
        WHY: Получает текущую цену спота с Binance.
        
        Returns:
            Spot price или None при ошибке
        """
        try:
            async with aiohttp.ClientSession() as session:
                params = {'symbol': self.symbol}  # Already includes USDT
                async with session.get(self.binance_spot_url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        return Decimal(data['price'])
                    else:
                        logger.error(f"Binance Spot API error: {response.status}")
                        return None
        except Exception as e:
            logger.error(f"Error fetching spot price: {e}")
            return None
    
    async def fetch_futures_price(self) -> Optional[Decimal]:
        """
        WHY: Получает цену бессрочного фьючерса (Perpetual) с Binance.
        
        NOTE: Для расчета basis используем perpetual, а не quarterly futures.
        Perpetual имеет funding rate, который коррелирует с basis.
        
        Returns:
            Futures price или None при ошибке
        """
        try:
            async with aiohttp.ClientSession() as session:
                params = {'symbol': self.symbol}
                async with session.get(self.binance_futures_url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        return Decimal(data['price'])
                    else:
                        logger.error(f"Binance Futures API error: {response.status}")
                        return None
        except Exception as e:
            logger.error(f"Error fetching futures price: {e}")
            return None
    
    async def fetch_open_interest(self) -> Optional[float]:
        """
        WHY: Получает текущий Open Interest (открытые позиции) с Binance.
        
        Returns:
            Open Interest в базовой валюте (BTC, ETH) или None при ошибке
        """
        try:
            async with aiohttp.ClientSession() as session:
                params = {'symbol': self.symbol}
                async with session.get(self.binance_oi_url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        oi = float(data['openInterest'])
                        
                        # Сохраняем для расчета delta
                        self.last_oi = oi
                        self.last_oi_timestamp = datetime.now()
                        
                        return oi
                    else:
                        logger.error(f"Binance OI API error: {response.status}")
                        return None
        except Exception as e:
            logger.error(f"Error fetching open interest: {e}")
            return None
    
    async def fetch_options_skew(self) -> Optional[float]:
        """
        WHY: Получает 25-delta Options Skew с Deribit.
        
        Теория:
        - Deribit предоставляет mark_iv для всех опционов
        - Нужно найти 25-delta Put и 25-delta Call
        - Skew = IV_put_25d - IV_call_25d
        
        IMPLEMENTATION NOTE:
        Deribit API не предоставляет прямой доступ к "25-delta IV".
        Вместо этого мы используем упрощенный подход:
        - Берем ATM (at-the-money) Put и Call IV как proxy
        - Для production нужна интеграция с /get_instruments для точного расчета delta
        
        Returns:
            Options Skew (%) или None при ошибке
        """
        try:
            async with aiohttp.ClientSession() as session:
                # Получаем книгу опционов
                url = f"{self.deribit_url}/get_book_summary_by_currency"
                params = {
                    'currency': self.base_symbol,  # Deribit uses BTC, not BTCUSDT
                    'kind': 'option'
                }
                
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        instruments = data.get('result', [])
                        
                        if not instruments:
                            logger.warning(f"No options data for {self.base_symbol}")
                            return None
                        
                        # Фильтруем ATM опционы (ближайшие к текущей цене)
                        # Берем первые Put и Call из списка (упрощенная версия)
                        put_iv = None
                        call_iv = None
                        
                        for inst in instruments:
                            instrument_name = inst.get('instrument_name', '')
                            mark_iv = inst.get('mark_iv')
                            
                            if mark_iv is None:
                                continue
                            
                            # Определяем Put или Call
                            if '-P' in instrument_name and put_iv is None:
                                put_iv = mark_iv
                            elif '-C' in instrument_name and call_iv is None:
                                call_iv = mark_iv
                            
                            # Если нашли оба - выходим
                            if put_iv is not None and call_iv is not None:
                                break
                        
                        if put_iv is not None and call_iv is not None:
                            # Skew = Put IV - Call IV
                            skew = put_iv - call_iv
                            logger.info(f"Options Skew: Put IV={put_iv:.2f}%, Call IV={call_iv:.2f}%, Skew={skew:.2f}%")
                            return skew
                        else:
                            logger.warning(f"Incomplete options data: put_iv={put_iv}, call_iv={call_iv}")
                            return None
                    
                    else:
                        logger.error(f"Deribit API error: {response.status}")
                        return None
        
        except Exception as e:
            logger.error(f"Error fetching options skew: {e}")
            return None
    
    async def calculate_oi_delta(self, current_oi: float, previous_oi: Optional[float]) -> Optional[float]:
        """
        WHY: Рассчитывает изменение OI за период.
        
        Args:
            current_oi: Текущий OI
            previous_oi: Предыдущий OI (из кеша или None)
        
        Returns:
            OI Delta или None если нет предыдущего значения
        """
        if previous_oi is None:
            return None
        
        delta = current_oi - previous_oi
        return delta
    
    async def fetch_all_metrics(self) -> Dict[str, Optional[float]]:
        """
        WHY: Собирает все метрики деривативов одновременно (параллельно).
        
        ВАЖНО: Возвращает ТОЛЬКО сырые данные (IO layer).
        Математика (basis, oi_delta) вынесена в DerivativesAnalyzer.
        
        Returns:
            dict: {
                'spot_price': Decimal,
                'futures_price': Decimal,
                'open_interest': float,
                'options_skew': Optional[float],  # Already calculated by Deribit
                'previous_oi': Optional[float],   # For OI delta calculation
                'timestamp': datetime
            }
        """
        # Параллельный запрос всех данных
        spot_task = self.fetch_spot_price()
        futures_task = self.fetch_futures_price()
        oi_task = self.fetch_open_interest()
        skew_task = self.fetch_options_skew()
        
        # Ждем завершения всех запросов
        spot_price, futures_price, current_oi, options_skew = await asyncio.gather(
            spot_task,
            futures_task,
            oi_task,
            skew_task
        )
        
        # WHY: Сохраняем предыдущий OI для расчета delta в analyzer
        previous_oi = self.last_oi
        
        # WHY: Возвращаем ТОЛЬКО сырые данные - математика в DerivativesAnalyzer!
        result = {
            'spot_price': spot_price,
            'futures_price': futures_price,
            'open_interest': current_oi,
            'previous_oi': previous_oi,
            'options_skew': options_skew,  # NOTE: Skew уже рассчитан Deribit API
            'timestamp': datetime.now()
        }
        
        logger.info(f"{self.symbol} Derivatives: Spot=${spot_price} Futures=${futures_price} OI={current_oi}")
        
        return result


# ===========================================================================
# USAGE EXAMPLE
# ===========================================================================

async def example_usage():
    """
    Example: Периодический сбор метрик каждые 5 минут.
    
    NOTE: Для расчета basis и oi_delta используйте DerivativesAnalyzer.
    """
    from analyzers_derivatives import DerivativesAnalyzer
    
    fetcher = DerivativesDataFetcher(symbol='BTCUSDT')
    analyzer = DerivativesAnalyzer()
    
    while True:
        try:
            # Запрашиваем сырые данные
            metrics = await fetcher.fetch_all_metrics()
            
            # Рассчитываем метрики через analyzer
            basis_apr = None
            if metrics['spot_price'] and metrics['futures_price']:
                basis_apr = analyzer.calculate_annualized_basis(
                    spot_price=metrics['spot_price'],
                    futures_price=metrics['futures_price'],
                    days_to_expiry=1  # Perpetual futures
                )
            
            oi_delta = None
            if metrics['open_interest'] is not None and metrics['previous_oi'] is not None:
                oi_delta, _ = analyzer.calculate_oi_delta(
                    oi_start=metrics['previous_oi'],
                    oi_end=metrics['open_interest']
                )
            
            # Вывод результатов
            print(f"📊 Derivatives Metrics ({metrics['timestamp']}):")
            print(f"  Spot: ${metrics['spot_price']}")
            print(f"  Futures: ${metrics['futures_price']}")
            print(f"  Basis APR: {basis_apr:.2f}%" if basis_apr else "  Basis APR: N/A")
            print(f"  OI: {metrics['open_interest']:.0f} {fetcher.base_symbol}" if metrics['open_interest'] else "  OI: N/A")
            print(f"  OI Delta: {oi_delta:.0f}" if oi_delta else "  OI Delta: N/A")
            print(f"  Options Skew: {metrics['options_skew']:.2f}%" if metrics['options_skew'] else "  Options Skew: N/A")
            print()
            
            # Ждем 5 минут
            await asyncio.sleep(300)
        
        except Exception as e:
            logger.error(f"Error in metrics loop: {e}")
            await asyncio.sleep(60)  # Retry after 1 minute


if __name__ == "__main__":
    # Настройка логирования
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Запуск
    asyncio.run(example_usage())
