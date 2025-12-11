import asyncio
import json
import websockets
from datetime import datetime
import os

# Цвета для консоли (чтобы было красиво и наглядно)
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'

class WhaleWatcher:
    def __init__(self):
        self.url = "wss://stream.binance.com:9443/ws/btcusdt@aggTrade"
        # Накопительная дельта (CVD) для разных групп
        self.cvd = {
            'whale': 0.0,    # > $100k
            'dolphin': 0.0,  # $1k - $100k
            'minnow': 0.0    # < $1k
        }
        self.trade_count = 0

    async def start(self):
        print(f"{Colors.YELLOW}📡 Подключение к потоку Binance (BTC/USDT)...{Colors.RESET}")
        print("Фильтр: 🐋 КИТЫ > $100,000 | 🐟 РЫБЫ < $1,000")
        
        async with websockets.connect(self.url) as ws:
            while True:
                try:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    self.process_trade(data)
                except Exception as e:
                    print(f"Ошибка: {e}")
                    break

    def process_trade(self, data):
        # Распаковка данных от Binance
        # p = цена, q = количество, m = is_buyer_maker (True значит ПРОДАЖА по рынку)
        price = float(data['p'])
        qty = float(data['q'])
        is_sell = data['m'] # Если True, значит инициатор - продавец
        
        volume_usd = price * qty
        
        # Определяем направление сделки для CVD
        # Если is_sell=True, то мы вычитаем объем (давление вниз), иначе прибавляем
        signed_vol = -volume_usd if is_sell else volume_usd
        
        # Сегментация по размеру (Стр. 6 вашего PDF)
        category = 'dolphin'
        icon = '🐬'
        
        if volume_usd > 100000:
            category = 'whale'
            icon = '🐋'
            self.print_whale_alert(price, volume_usd, is_sell)
            
        elif volume_usd < 1000:
            category = 'minnow'
            icon = '🐟'

        # Обновляем CVD
        self.cvd[category] += signed_vol
        self.trade_count += 1

        # Раз в 50 сделок обновляем статистику на экране
        if self.trade_count % 50 == 0:
            self.print_status(price)

    def print_whale_alert(self, price, volume, is_sell):
        """Выводит красивое уведомление, когда проходит крупная сделка"""
        side = f"{Colors.RED}SELL 🔴{Colors.RESET}" if is_sell else f"{Colors.GREEN}BUY 🟢{Colors.RESET}"
        print(f"\n🚀 {Colors.BLUE}WHALE ALERT!{Colors.RESET} {side} ${volume:,.0f} @ {price:.2f}")

    def print_status(self, current_price):
        """Выводит текущий баланс сил"""
        # Очистка строки (чтобы не спамить, а обновлять блок)
        print(f"\n--- 📊 CVD STATUS (Баланс спроса) @ ${current_price:.2f} ---")
        
        # Форматирование цвета цифр
        def color_val(val):
            c = Colors.GREEN if val > 0 else Colors.RED
            return f"{c}${val/1000:,.0f}k{Colors.RESET}"

        print(f"🐋 КИТЫ (Smart Money): {color_val(self.cvd['whale'])}")
        print(f"🐬 Дельфины (Трейдеры): {color_val(self.cvd['dolphin'])}")
        print(f"🐟 Рыбы (Толпа):        {color_val(self.cvd['minnow'])}")
        print("-" * 40)

if __name__ == "__main__":
    # Запуск
    watcher = WhaleWatcher()
    try:
        asyncio.run(watcher.start())
    except KeyboardInterrupt:
        print("\nОстановка скрипта...")