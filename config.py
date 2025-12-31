from dataclasses import dataclass
from decimal import Decimal
from typing import Dict

@dataclass
class AssetConfig:
    """Конфигурация для конкретного актива"""
    symbol: str
    
    # --- 1. ТЕХНИЧЕСКИЕ ПАРАМЕТРЫ (ОБЯЗАТЕЛЬНО) ---
    dust_threshold: Decimal         # Меньше этого - игнорируем (фильтр шума)
    price_display_format: str       # Формат вывода цены (напр. "{:,.2f}")
    
    # --- 2. Iceberg Detection ---
    min_hidden_volume: Decimal      # Минимальный скрытый объем (в монетах!)
    min_iceberg_ratio: Decimal      # hidden/total ratio
    
    # --- 3. Gamma Walls ---
    # Лучше использовать % от цены, а не фикс USD, но если USD, то настраивать точно
    gamma_wall_tolerance_pct: Decimal  # 0.001 = 0.1% от цены
    
    # --- 4. Whale Classification ---
    static_whale_threshold_usd: float
    static_minnow_threshold_usd: float
    min_whale_floor_usd: float
    min_minnow_floor_usd: float
    
    # --- 4.1 Accumulation Detector (Wyckoff) ---
    # WHY: Порог для passive_absorption (в монетах токена!)
    # Теория: R_abs = total/visible. Если total=threshold, visible=threshold/10 → R_abs=10 (кит)
    # Gemini Fix: Мульти-ассет поддержка (вместо хардкода 2.0 BTC)
    accumulation_whale_threshold: Decimal
    
    # --- 5. Spoofing & Breach ---
    spoofing_volume_threshold: Decimal  # Порог объема для подозрений (в монетах!)
    breach_tolerance_pct: Decimal       # % толеранс для пробоя
    
    # --- 6. OBI Exponential Decay ---
    # ⚠️ PRODUCTION CALIBRATION WARNING (Gemini Validation):
    # lambda_decay используется в weighted OFI с множителем ×100
    # При λ=0.1: ордер на 0.1% от mid получает weight=0.36 (штраф 64%)
    # Если OFI "тихий" - уменьши до 0.01-0.05 (см. CALIBRATION_NOTES.md)
    lambda_decay: float = 0.1           # Коэффициент экспоненциального затухания
    
    # --- 7. OFI Depth ---
    ofi_depth: int = 20                 # Глубина расчёта OFI (WHY: Gemini Phase 2.2 - Dynamic Depth)
    
    # --- 8. VPIN (Flow Toxicity) ---
    vpin_bucket_size: Decimal = Decimal("10")  # Размер корзины для VPIN (в монетах токена)
    vpin_window_size: int = 50                  # Количество корзин в скользящем окне
    vpin_toxicity_threshold: float = 0.7        # Порог токсичности (>0.7 = toxic flow)
    vpin_noise_threshold: float = 0.3           # Порог шума (<0.3 = noisy flow)
    
    # === GEMINI FIX: Real-Time VPIN Parameters ===
    vpin_inclusion_threshold: float = 0.2       # WHY: Включать current_bucket если заполнен > 20%
    vpin_stale_threshold_seconds: int = 300     # WHY: Считать VPIN stale через 5 мин простоя (swing trading)

# --- КОНФИГУРАЦИИ ---

BTC_CONFIG = AssetConfig(
    symbol="BTCUSDT",
    # Tech
    dust_threshold=Decimal("0.0001"), # ~$10
    price_display_format="{:,.2f}",   # 95,000.50
    # Iceberg
    min_hidden_volume=Decimal("0.05"),
    min_iceberg_ratio=Decimal("0.3"),
    # Gamma (0.1% от 100k = $100)
    gamma_wall_tolerance_pct=Decimal("0.001"), 
    # Whale
    static_whale_threshold_usd=100000.0,
    static_minnow_threshold_usd=1000.0,
    min_whale_floor_usd=10000.0,
    min_minnow_floor_usd=100.0,
    # Accumulation
    accumulation_whale_threshold=Decimal("2.0"),  # WHY: 2.0 BTC ≈ $200k (кит, R_abs>10)
    # Risk
    spoofing_volume_threshold=Decimal("0.1"),
    breach_tolerance_pct=Decimal("0.0005"),
    # OBI
    lambda_decay=0.1,  # WHY: BTC узкие спреды → агрессивная фильтрация
    # OFI
    ofi_depth=20,  # WHY: BTC узкие спреды → 20 уровней достаточно
    # VPIN
    vpin_bucket_size=Decimal("10"),       # WHY: 10 BTC ≈ $1M (минимальный институциональный объем)
    vpin_window_size=50,                   # WHY: 50 корзин = 500 BTC история (рекомендация Easley-O'Hara)
    vpin_toxicity_threshold=0.7,           # WHY: Стандарт из академической литературы
    vpin_noise_threshold=0.3               # WHY: Стандарт из академической литературы
)

ETH_CONFIG = AssetConfig(
    symbol="ETHUSDT",
    # Tech
    dust_threshold=Decimal("0.01"),   # ~$30
    price_display_format="{:,.2f}",
    # Iceberg
    min_hidden_volume=Decimal("1.0"), # 1 ETH
    min_iceberg_ratio=Decimal("0.3"),
    # Gamma
    gamma_wall_tolerance_pct=Decimal("0.0015"), # Чуть шире для ETH
    # Whale
    static_whale_threshold_usd=50000.0,
    static_minnow_threshold_usd=500.0,
    min_whale_floor_usd=5000.0,
    min_minnow_floor_usd=50.0,
    # Accumulation
    accumulation_whale_threshold=Decimal("30.0"),  # WHY: 30 ETH ≈ $100k (кит, R_abs>10)
    # Risk
    spoofing_volume_threshold=Decimal("2.0"),
    breach_tolerance_pct=Decimal("0.001"),
    # OBI
    lambda_decay=0.05,  # WHY: ETH шире спреды → мягче фильтр
    # OFI
    ofi_depth=30,  # WHY: ETH более волатильный → больше глубина
    # VPIN
    vpin_bucket_size=Decimal("100"),      # WHY: 100 ETH ≈ $300k (меньше ликвидность чем BTC)
    vpin_window_size=50,                   # WHY: Стандарт 50 корзин
    vpin_toxicity_threshold=0.7,           # WHY: Стандарт
    vpin_noise_threshold=0.3               # WHY: Стандарт
)

# Для примера: SOL (чтобы понять зачем нужны форматы)
SOL_CONFIG = AssetConfig(
    symbol="SOLUSDT",
    dust_threshold=Decimal("0.1"),
    price_display_format="{:,.3f}",   # 145.235 (больше точности)
    min_hidden_volume=Decimal("10.0"),
    min_iceberg_ratio=Decimal("0.3"),
    gamma_wall_tolerance_pct=Decimal("0.005"),  # WHY: Gemini - SOL lower liquidity → wider tolerance (0.5%)
    static_whale_threshold_usd=25000.0,
    static_minnow_threshold_usd=200.0,
    min_whale_floor_usd=2000.0,
    min_minnow_floor_usd=20.0,
    # Accumulation
    accumulation_whale_threshold=Decimal("500.0"),  # WHY: 500 SOL ≈ $100k (кит, R_abs>10)
    spoofing_volume_threshold=Decimal("20.0"),
    breach_tolerance_pct=Decimal("0.001"),
    lambda_decay=0.03,  # WHY: SOL очень волатильный → минимальная фильтрация
    ofi_depth=50,  # WHY: SOL очень волатильный → максимальная глубина
    # VPIN
    vpin_bucket_size=Decimal("500"),      # WHY: 500 SOL ≈ $75k (малая ликвидность)
    vpin_window_size=50,                   # WHY: Стандарт 50 корзин
    vpin_toxicity_threshold=0.75,          # WHY: Выше порог для волатильного актива
    vpin_noise_threshold=0.25              # WHY: Ниже порог для волатильного актива
)

CONFIG_REGISTRY = {
    "BTCUSDT": BTC_CONFIG,
    "ETHUSDT": ETH_CONFIG,
    "SOLUSDT": SOL_CONFIG
}

def get_config(symbol: str) -> AssetConfig:
    return CONFIG_REGISTRY.get(symbol, BTC_CONFIG)