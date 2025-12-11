import requests
import pandas as pd
import numpy as np
import time
from datetime import datetime, timezone
from scipy.stats import norm

# Настройки отображения pandas
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 1000)

class DeribitLoader:
    def __init__(self, currency='BTC'):
        self.currency = currency
        self.base_url = "https://www.deribit.com/api/v2/public"
    
    def fetch_option_summary(self):
        url = f"{self.base_url}/get_book_summary_by_currency"
        params = {"currency": self.currency, "kind": "option"}
        
        try:
            print(f"⏳ [1/3] Запрос данных Deribit для {self.currency}...")
            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code == 429:
                print("⚠️ Лимит запросов! Ждем 60 сек...")
                time.sleep(60)
                return None
                
            data = response.json()
            if 'result' not in data:
                print("❌ Пустой ответ API")
                return None
                
            print(f"✅ [2/3] Получено {len(data['result'])} контрактов. Обработка...")
            
            # Превращаем в таблицу
            df = self._process_data(data['result'])
            
            # Считаем Гамму (математика Smart Money)
            print(f"🧮 [3/3] Расчет Гамма-экспозиции (GEX)...")
            df = self._calculate_gex(df)
            
            return df
            
        except Exception as e:
            print(f"❌ Критическая ошибка в fetch_option_summary: {e}")
            import traceback
            traceback.print_exc() 
            return None

    def _process_data(self, raw_data):
        df = pd.DataFrame(raw_data)
        
        # 1. Гарантируем наличие колонок (даже если биржа их не прислала)
        needed_cols = ['instrument_name', 'mark_price', 'underlying_price', 
                       'open_interest', 'bid_iv', 'ask_iv', 'mark_iv']
        
        for col in needed_cols:
            if col not in df.columns:
                df[col] = np.nan # Заполняем пустотой, если нет данных

        df = df[needed_cols].copy()
        
        # 2. Парсим название: BTC-29MAR24-60000-C
        def parse_instr(name):
            try:
                parts = name.split('-')
                # Возвращаем: Дата, Страйк, Тип
                return parts[1], float(parts[2]), parts[3] 
            except:
                return None, None, None

        df[['expiry_date', 'strike', 'type']] = df['instrument_name'].apply(
            lambda x: pd.Series(parse_instr(x))
        )
        
        df = df.dropna(subset=['strike']) # Удаляем ошибки парсинга
        
        # 3. Время до экспирации (исправлено предупреждение datetime)
        now = datetime.now(timezone.utc)
        
        # Используем format='mixed' чтобы pandas сам разобрался с форматами дат (28MAR25 и т.д.)
        df['expiry_dt'] = pd.to_datetime(df['expiry_date'], utc=True, format='mixed', errors='coerce')
        
        df['days_to_expiry'] = (df['expiry_dt'] - now).dt.total_seconds() / (24 * 3600)
        df['time_years'] = df['days_to_expiry'] / 365.0
        
        # Фильтруем просроченные или те, что истекают сегодня (time_years ~ 0)
        # Чтобы не делить на ноль в формуле Блэка-Шоулза
        df = df[df['time_years'] > 0.001]
        
        return df

    def _calculate_gex(self, df):
        """
        Расчет GEX. Используем mark_iv как приоритет, так как он есть всегда.
        """
        # Логика выбора волатильности: Mark IV > (Bid+Ask)/2
        df['iv'] = df['mark_iv'] / 100.0
        
        # Если mark_iv нет (редкость), пробуем среднюю
        mask_nan = df['iv'].isna()
        df.loc[mask_nan, 'iv'] = df.loc[mask_nan, ['bid_iv', 'ask_iv']].mean(axis=1) / 100.0
        
        # Удаляем строки, где вообще нет волатильности (нельзя посчитать)
        df = df.dropna(subset=['iv']) 
        
        # Параметры для формулы
        S = df['underlying_price']
        K = df['strike']
        T = df['time_years']
        v = df['iv']
        
        # Формула d1 из Блэка-Шоулза
        d1 = (np.log(S / K) + (0.5 * v**2) * T) / (v * np.sqrt(T))
        
        # Гамма
        pdf_d1 = norm.pdf(d1)
        df['gamma'] = pdf_d1 / (S * v * np.sqrt(T))
        
        # GEX ($ value) = Gamma * OpenInterest * Spot^2 * 0.01
        df['gex'] = df['gamma'] * df['open_interest'] * (S**2) * 0.01
        
        # Для Путов инвертируем знак (Dealer Short Gamma exposure)
        df.loc[df['type'] == 'P', 'gex'] *= -1
        
        return df

# --- Блок запуска ---
if __name__ == "__main__":
    loader = DeribitLoader('BTC')
    df = loader.fetch_option_summary()
    
    if df is not None:
        spot_price = df['underlying_price'].iloc[0]
        total_gex = df['gex'].sum()
        
        print("\n" + "="*50)
        print(f"💰 BTC ЦЕНА: ${spot_price:,.0f}")
        print(f"🌊 TOTAL GEX (Барометр рынка): ${total_gex/1_000_000:,.2f}M")
        print("="*50)
        
        print("\n🚧 CALL WALLS (Сопротивление / Дилеры продают):")
        calls = df[df['type'] == 'C'].groupby('strike')['gex'].sum().sort_values(ascending=False).head(5)
        for strike, gex in calls.items():
            print(f"   Strike ${strike:,.0f} | GEX: +${gex/1_000_000:,.2f}M")
            
        print("\n🕳️ PUT WALLS (Поддержка / Дилеры покупают):")
        puts = df[df['type'] == 'P'].groupby('strike')['gex'].sum().sort_values().head(5)
        for strike, gex in puts.items():
            print(f"   Strike ${strike:,.0f} | GEX: ${gex/1_000_000:,.2f}M")
            
        print("\n------------------------------------------------")
        if total_gex > 0:
            print("✅ ПОЗИТИВНАЯ ГАММА: Рынок подавляет волатильность.")
            print("   Дилеры покупают на падении и продают на росте (Mean Reversion).")
        else:
            print("⚠️ НЕГАТИВНАЯ ГАММА: Рынок усиливает движения.")
            print("   Дилеры продают на падении (ускоряя крах) и покупают на росте.")