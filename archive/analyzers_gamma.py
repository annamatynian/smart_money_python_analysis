# ===========================================================================
# GAMMA PROVIDER: Извлечение GEX метрик из LocalOrderBook
# ===========================================================================

"""
WHY: Простой провайдер для чтения gamma_profile из LocalOrderBook.

Используется FeatureCollector для получения GEX метрик без блокировки.
"""

from typing import Optional, Tuple
from decimal import Decimal

class GammaProvider:
    """
    WHY: Читает GEX данные из LocalOrderBook.gamma_profile.
    
    Интерфейс:
    - get_total_gex() → суммарная гамма-экспозиция
    - get_gamma_wall_distance(price) → расстояние до ближайшей стены
    """
    
    def __init__(self, order_book):
        """
        Args:
            order_book: LocalOrderBook с gamma_profile
        """
        self.book = order_book
    
    def get_total_gex(self) -> Optional[float]:
        """
        WHY: Возвращает суммарную gamma exposure.
        
        Returns:
            float: Суммарная GEX (может быть + или -)
            None: Если данных нет
        """
        if not self.book or not self.book.gamma_profile:
            return None
        
        try:
            return float(self.book.gamma_profile.total_gex)
        except:
            return None
    
    def get_gamma_wall_distance(self, current_price: float) -> Tuple[Optional[float], Optional[str]]:
        """
        WHY: Рассчитывает расстояние до ближайшей gamma wall.
        
        Теория (документ "Анализ данных смарт-мани"):
        - Gamma Wall = страйк с максимальной концентрацией гаммы
        - Call Wall = сопротивление (дилеры продают на росте)
        - Put Wall = поддержка (дилеры покупают на падении)
        
        Args:
            current_price: Текущая цена актива
        
        Returns:
            Tuple[distance_pct, wall_type]:
            - distance_pct: Процентное расстояние до ближайшей wall
            - wall_type: 'CALL' | 'PUT' | None
        """
        if not self.book or not self.book.gamma_profile:
            return None, None
        
        try:
            gamma_profile = self.book.gamma_profile
            
            # Расстояния до стен
            dist_to_call = abs(current_price - gamma_profile.call_wall)
            dist_to_put = abs(current_price - gamma_profile.put_wall)
            
            # Находим ближайшую
            if dist_to_call < dist_to_put:
                closest_wall = gamma_profile.call_wall
                wall_type = 'CALL'
                distance = dist_to_call
            else:
                closest_wall = gamma_profile.put_wall
                wall_type = 'PUT'
                distance = dist_to_put
            
            # Процентное расстояние
            distance_pct = (distance / current_price) * 100
            
            return float(distance_pct), wall_type
            
        except:
            return None, None
