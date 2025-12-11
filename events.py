from pydantic import BaseModel, Field
from decimal import Decimal
from datetime import datetime

# Базовый класс события
class SystemEvent(BaseModel):
    event_time: datetime = Field(default_factory=datetime.now)

# Перенесенная модель из domain.py (с улучшениями)
class IcebergDetectedEvent(SystemEvent):
    symbol: str
    price: Decimal
    detected_hidden_volume: Decimal
    visible_volume_before: Decimal
    confidence: float
