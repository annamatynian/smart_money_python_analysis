import os
import asyncio
from datetime import datetime
from dotenv import load_dotenv

# Загрузка ключей
load_dotenv()

# Импорты LangChain
from langchain_groq import ChatGroq
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough

# Импортируем твой репозиторий для доступа к истории
from repository import PostgresRepository 

class HybridAdvisorService:
    # ### НОВОЕ: Теперь мы принимаем dsn (адрес базы) при инициализации ###
    def __init__(self, db_dsn: str = None):
        
        # Подключение к БД (для исторических запросов)
        self.db_dsn = db_dsn
        self.repo = PostgresRepository(dsn=db_dsn) if db_dsn else None
        
        # 1. АГЕНТ-ИНТЕРПРЕТАТОР (Groq / Llama-3-70b)
        self.interpreter_llm = ChatGroq(
            model="llama-3.3-70b-versatile", 
            temperature=0.7,
            max_tokens=1000  # Чуть увеличили для исторических ответов
        )

        # 2. АГЕНТ-ВАЛИДАТОР (Google / Gemini 1.5 Flash)
        self.validator_llm = ChatGoogleGenerativeAI(
            model="gemini-flash-latest",
            temperature=0.0,
        )

        self.chain = self._build_realtime_chain()
        # ### НОВОЕ: Цепочка для истории ###
        self.history_chain = self._build_history_chain()

    def _build_realtime_chain(self):
        """Цепочка для мгновенных алертов (как было раньше)"""
        interpreter_prompt = ChatPromptTemplate.from_template(
            """ТЫ — АГРЕССИВНЫЙ ХЕДЖ-ФОНД АНАЛИТИК.
            ВХОДНЫЕ ДАННЫЕ (JSON): {json_data}
            ЗАДАНИЕ: Напиши питч. Опирайся на: IcebergRatio > 0.3, OFI, Gamma Walls."""
        )

        validator_prompt = ChatPromptTemplate.from_template(
            """ТЫ — ГЛАВНЫЙ РИСК-МЕНЕДЖЕР.
            ПИТЧ: "{interpretation}"
            ФАКТЫ: {json_data}
            ВЕРДИКТ: [✅ APPROVED], [❌ REJECTED], или [⚠️ WARNING]. Объясни почему."""
        )

        return (
            {"json_data": RunnablePassthrough()} 
            | RunnablePassthrough.assign(interpretation=interpreter_prompt | self.interpreter_llm | StrOutputParser())
            | validator_prompt
            | self.validator_llm
            | StrOutputParser()
        )

    # ### НОВОЕ: Цепочка для анализа истории ###
    def _build_history_chain(self):
        """
        Специальная цепочка, которая умеет читать контекст SmartCandles.
        """
        # ПРОМПТ 1: Анализ контекста (Вайкофф)
        history_prompt = ChatPromptTemplate.from_template(
            """ТЫ — ЭКСПЕРТ ПО МЕТОДУ ВАЙКОФФА И VSA (Volume Spread Analysis).
            У тебя есть исторические данные за период в виде "Умных Свечей" (Smart Candles).
            
            КОНТЕКСТ РЫНКА (SMART CANDLES):
            {market_context_text}
            
            ВОПРОС ТРЕЙДЕРА:
            "{user_question}"
            
            ТВОЯ ЗАДАЧА:
            1. Проанализируй динамику Whale CVD vs Price (ищи дивергенции).
            2. Посмотри на Basis и Skew (настроения профи).
            3. Ответь на вопрос трейдера, используя факты из контекста.
            
            Если данных недостаточно или ситуация неопределенная — скажи честно.
            """
        )

        # Валидатор тут тоже нужен, чтобы проверить логику, но промпт проще
        validator_history_prompt = ChatPromptTemplate.from_template(
            """ТЫ — РЕДАКТОР ФИНАНСОВОГО ОТЧЕТА.
            Проверь этот анализ на логические ошибки.
            
            АНАЛИЗ: "{interpretation}"
            
            ИСХОДНЫЕ ДАННЫЕ:
            {market_context_text}
            
            ЕСЛИ анализ противоречит цифрам (например, пишет "Киты купили", а CVD отрицательный) — ИСПРАВЬ ЭТО.
            ЕСЛИ все верно — просто улучши стиль и выдай финальный ответ.
            """
        )

        return (
            {"market_context_text": RunnablePassthrough(), "user_question": RunnablePassthrough()} 
            | RunnablePassthrough.assign(interpretation=history_prompt | self.interpreter_llm | StrOutputParser())
            | validator_history_prompt
            | self.validator_llm
            | StrOutputParser()
        )

    # ### НОВОЕ: Вспомогательная функция для форматирования данных ###
    def _format_candles_to_text(self, candles: list) -> str:
        if not candles:
            return "ДАННЫХ НЕТ."
        
        text_report = "--- ОТЧЕТ ПО SMART CANDLES ---\n"
        for c in candles:
            # Получаем выводы Hard Code (Python логики)
            fuel = c.get_trend_fuel()
            is_fear = c.is_fear_divergence(price_rising=(c.close > c.open))
            
            text_report += (
                f"🕒 {c.timestamp.strftime('%Y-%m-%d %H:%M')}\n"
                f"   Price: {c.open:.0f} -> {c.close:.0f} | Vol: {c.volume}\n"
                f"   🐋 Whale CVD: {c.whale_cvd:+.2f} | 🐟 Minnow: {c.minnow_cvd:+.2f}\n"
                f"   🌊 OFI: {c.ofi:+.1f} | OBI: {c.weighted_obi:.2f}\n"
                f"   📊 Basis: {c.avg_basis_apr:.1f}% | Skew: {c.options_skew:.1f}%\n"
                f"   🧠 Python Signals: Fuel={fuel}, FearDivergence={is_fear}\n"
                "--------------------------------\n"
            )
        return text_report

    # ### НОВОЕ: Метод, который ты вызываешь руками ###
    async def ask_about_history(self, question: str, symbol: str, start: datetime, end: datetime, timeframe_m: int = 60):
        """
        Главный метод для вопросов типа: "Была ли реаккумуляция вчера?"
        """
        if not self.repo:
            return "❌ Ошибка: Нет подключения к БД (dsn не передан)."

        try:
            # 1. Подключаемся (если пула нет)
            if not self.repo.pool:
                await self.repo.connect()

            # 2. Достаем агрегированные "Умные свечи"
            print(f"🔍 Запрос в БД: {symbol} с {start} по {end}...")
            smart_candles = await self.repo.get_aggregated_smart_candles(
                symbol, start, end, timeframe_minutes=timeframe_m
            )
            
            if not smart_candles:
                return "⚠️ За этот период в базе нет данных."

            # 3. Превращаем объекты в читаемый текст
            context_text = self._format_candles_to_text(smart_candles)
            
            # 4. Скармливаем Агенту
            print("🤖 Агент анализирует контекст...")
            result = await self.history_chain.ainvoke({
                "market_context_text": context_text,
                "user_question": question
            })
            
            return result

        except Exception as e:
            return f"❌ Ошибка при анализе истории: {e}"

# --- ТЕСТОВЫЙ ЗАПУСК ---
if __name__ == "__main__":
    # Пример использования
    dsn = "postgresql://postgres:pass@localhost:5432/trading_db" # Твой DSN
    
    advisor = HybridAdvisorService(db_dsn=dsn)
    
    # Симуляция вопроса
    start_dt = datetime(2025, 12, 1, 10, 0) # Пример дат
    end_dt = datetime(2025, 12, 1, 14, 0)
    
    async def run_test():
        response = await advisor.ask_about_history(
            question="Видишь ли ты здесь признаки реаккумуляции по Вайкоффу?",
            symbol="BTCUSDT",
            start=start_dt,
            end=end_dt
        )
        print("\n=== ОТВЕТ АГЕНТА ===\n")
        print(response)

    # asyncio.run(run_test())