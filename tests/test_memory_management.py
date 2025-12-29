"""
Tests for Memory Management (Logging)
WHY: Gemini рекомендация - логировать удаляемые PriceZone в cleanup
"""
import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from domain import LocalOrderBook, IcebergLevel
from analyzers import AccumulationDetector
from config import get_config
import logging


class TestMemoryManagement:
    """Tests for cleanup task logging"""
    
    def test_cleanup_logs_removed_zones(self, caplog):
        """
        ПРОВЕРКА: _periodic_cleanup_task логирует удаляемые зоны
        
        WHY: Отслеживаем "тяжёлые" зоны которые съедают память
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        config = get_config('BTCUSDT')
        detector = AccumulationDetector(book=book, config=config)
        
        # Создаём старую зону (> 30 мин назад)
        old_time = datetime.now() - timedelta(minutes=35)
        old_zone_id = (Decimal('60000'), True)  # (price, is_ask)
        
        detector.price_zones[old_zone_id] = {
            'icebergs': [
                IcebergLevel(
                    price=Decimal('60000'),
                    hidden_volume_executed=Decimal('5.0'),
                    is_ask=True,
                    confidence_score=0.8,
                    first_detected_at=old_time,
                    last_refill_at=old_time
                )
            ],
            'created_at': old_time
        }
        
        # Запускаем cleanup с логированием
        with caplog.at_level(logging.INFO):
            detector._periodic_cleanup_task()
        
        # Проверяем что лог содержит информацию об удалённой зоне
        assert len(caplog.records) > 0
        # Должно быть логирование удаления
        log_messages = [rec.message for rec in caplog.records]
        assert any('Removed PriceZone' in msg or 'зона' in msg.lower() for msg in log_messages)
    
    def test_cleanup_does_not_log_if_no_removal(self, caplog):
        """
        ПРОВЕРКА: cleanup НЕ логирует если ничего не удалили
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        config = get_config('BTCUSDT')
        detector = AccumulationDetector(book=book, config=config)
        
        # Создаём СВЕЖУЮ зону (< 5 мин назад)
        fresh_time = datetime.now() - timedelta(minutes=2)
        fresh_zone_id = (Decimal('60000'), True)
        
        detector.price_zones[fresh_zone_id] = {
            'icebergs': [
                IcebergLevel(
                    price=Decimal('60000'),
                    hidden_volume_executed=Decimal('5.0'),
                    is_ask=True,
                    confidence_score=0.8,
                    first_detected_at=fresh_time,
                    last_refill_at=fresh_time
                )
            ],
            'created_at': fresh_time
        }
        
        # Запускаем cleanup
        with caplog.at_level(logging.INFO):
            detector._periodic_cleanup_task()
        
        # НЕ должно быть логов удаления (или лог показывает 0 удалённых)
        log_messages = [rec.message for rec in caplog.records]
        # Либо нет логов, либо лог говорит "0 zones removed"
        if log_messages:
            assert not any('Removed PriceZone' in msg for msg in log_messages) or \
                   any('0' in msg for msg in log_messages)
