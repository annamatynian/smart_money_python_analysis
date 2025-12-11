import asyncio
import json
import websockets
from datetime import datetime

class IcebergDetector:
    def __init__(self):
        # Комбинированный стрим: сделки + состояние стакана
        self.url = "wss://stream.binance.com:9443/stream?streams=btcusdt@aggTrade/btcusdt@bookTicker"
        
        self.best_bid_qty = 0.0
        self.best_ask_qty = 0.0
        self.best_bid_price = 0.0
        self.best_ask_price = 0.0

    async def start(self):
        print(f"📡 Подключение к Binance (Расширенный режим)...")
        print("Формула из PDF: Скрытое = Сделка (Trade) - Видимое в стакане (Visible)")
        print("-" * 60)
        
        async with websockets.connect(self.url) as ws:
            while True:
                try:
                    message = await ws.recv()
                    data = json.loads(message)
                    
                    stream_type = data['stream']
                    payload = data['data']

                    if 'bookTicker' in stream_type:
                        self.update_order_book(payload)
                    elif 'aggTrade' in stream_type:
                        self.process_trade(payload)

                except Exception as e:
                    print(f"Ошибка: {e}")
                    break

    def update_order_book(self, data):
        self.best_bid_price = float(data['b'])
        self.best_bid_qty = float(data['B'])   # Видимый Bid
        self.best_ask_price = float(data['a'])
        self.best_ask_qty = float(data['A'])   # Видимый Ask

    def process_trade(self, data):
        price = float(data['p'])
        qty = float(data['q'])    # V_trade (Объем сделки)
        is_sell_maker = data['m'] # True = Продажа по рынку

        # Ждем пока стакан наполнится данными
        if self.best_bid_qty == 0: 
            return

        detected = False
        hidden_size = 0.0
        visible_size = 0.0 # Новая переменная для отчета
        side_text = ""
        
        # Логика сравнения V_trade vs V_visible [cite: 37-40]
        if is_sell_maker: 
            # Удар продавца в Bid (покупку)
            visible_size = self.best_bid_qty
            
            # Если продали больше, чем было видно, но цена не ушла ниже бида
            if qty > visible_size and price >= self.best_bid_price:
                hidden_size = qty - visible_size
                # Фильтр: показываем только если скрыто > 0.01 BTC
                if hidden_size > 0.01:
                    detected = True
                    side_text = "🟢 BUY ICEBERG (Скрытая покупка)"
        
        else:
            # Удар покупателя в Ask (продажу)
            visible_size = self.best_ask_qty
            
            if qty > visible_size and price <= self.best_ask_price:
                hidden_size = qty - visible_size
                if hidden_size > 0.01:
                    detected = True
                    side_text = "🔴 SELL ICEBERG (Скрытая продажа)"

        if detected:
            self.print_detailed_alert(side_text, price, qty, visible_size, hidden_size)

    def print_detailed_alert(self, side, price, trade_vol, visible_vol, hidden_vol):
        # Детальный вывод математики
        ratio = (hidden_vol / trade_vol) * 100
        
        print(f"\n🧊 {side}")
        print(f"   Цена исполнения: ${price:,.2f}")
        print(f"   ---------------------------------------------")
        print(f"   ⚡ Объем сделки (Trade):   {trade_vol:.4f} BTC")
        print(f"   👀 Видимо в стакане:       {visible_vol:.4f} BTC")
        print(f"   ---------------------------------------------")
        print(f"   🕵️  СКРЫТАЯ ЧАСТЬ:        {hidden_vol:.4f} BTC")
        print(f"   📊  Процент скрытия:      {ratio:.1f}%")
        print("-" * 60)

if __name__ == "__main__":
    detector = IcebergDetector()
    try:
        asyncio.run(detector.start())
    except KeyboardInterrupt:
        print("\nОстановка.")