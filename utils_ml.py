"""
🛡️ ML Data Quality Guards - Защита от Data Leakage

WHY: Предотвращает "заглядывание в будущее" при обучении ML моделей.
Критично для time-series данных (SmartCandles + IcebergFeatures).

Используется перед model.fit() для валидации датасета.
"""
import pandas as pd
import numpy as np
from datetime import timedelta
from colorama import Fore, Style, init
from typing import Optional

init(autoreset=True)


class DataLeakageGuard:
    """
    🛡️ GUARDIAN OF TIME
    
    Проверяет датасет на 5 типов утечек перед ML-обучением:
    1. Timestamp Alignment - фичи не из будущего
    2. Correlation Spike - таргет не подмешан как фича
    3. Shift Integrity - lag-фичи действительно сдвинуты
    4. Timeframe Mixing - 1H свечи не содержат 4H контекст
    5. Aggregation Version - все данные версии '1.0'
    
    Usage:
        guard = DataLeakageGuard(df, time_col='candle_time', target_col='next_hour_close')
        guard.check_all()  # Запускает все проверки, падает с ValueError при утечке
    """

    def __init__(self, df: pd.DataFrame, time_col: str, target_col: str):
        """
        Args:
            df: Датасет для обучения (SmartCandles + Features merged)
            time_col: Колонка времени свечи (candle_time)
            target_col: Целевая переменная (например, next_hour_close)
        """
        self.df = df.sort_values(by=time_col).reset_index(drop=True)
        self.time_col = time_col
        self.target_col = target_col
        self.issues_found = []

    # =========================================================================
    # ПРОВЕРКА 1: TIMESTAMP ALIGNMENT (Критическая)
    # =========================================================================
    def check_timestamp_alignment(self, feature_time_col: str):
        """
        ПРОВЕРКА 1: Физика времени.
        Время расчета фичи (snapshot_time) НЕ МОЖЕТ быть позже времени открытия свечи (candle_time).
        
        Пример утечки:
        - Свеча Open: 14:00
        - Context Calculated: 14:59
        - Модель в 14:00 НЕ МОЖЕТ знать данные из 14:59!
        
        Args:
            feature_time_col: Название колонки времени фичи (например, 'snapshot_time')
        
        Raises:
            ValueError: Если найдены строки, где feature_time > candle_time
        """
        print(f"{Fore.CYAN}🔍 [CHECK 1/5] Timestamp Alignment: {feature_time_col}...{Style.RESET_ALL}")
        
        if feature_time_col not in self.df.columns:
            print(f"{Fore.YELLOW}⚠️  Column '{feature_time_col}' not found. Skipping.{Style.RESET_ALL}")
            return
        
        # Ищем строки, где время фичи > времени свечи (УТЕЧКА!)
        leaks = self.df[self.df[feature_time_col] > self.df[self.time_col]]
        
        if not leaks.empty:
            example = leaks.iloc[0]
            msg = (
                f"{Fore.RED}🚨 CRITICAL LEAK DETECTED!{Style.RESET_ALL}\n"
                f"   Future data found in {len(leaks)}/{len(self.df)} rows ({len(leaks)/len(self.df)*100:.1f}%)\n"
                f"   Example:\n"
                f"     Candle Time: {example[self.time_col]}\n"
                f"     Feature Time: {example[feature_time_col]}\n"
                f"   → Feature is {(example[feature_time_col] - example[self.time_col]).total_seconds()} seconds ahead!"
            )
            print(msg)
            self.issues_found.append(f"Timestamp leakage in {feature_time_col}: {len(leaks)} rows")
            raise ValueError(f"Data Leakage: {feature_time_col} contains future timestamps")
        else:
            print(f"{Fore.GREEN}   ✅ OK - No future timestamps detected{Style.RESET_ALL}")

    # =========================================================================
    # ПРОВЕРКА 2: CORRELATION SPIKE (Подозрительная)
    # =========================================================================
    def check_target_correlation_spike(self, threshold: float = 0.95):
        """
        ПРОВЕРКА 2: "Слишком хорошо, чтобы быть правдой".
        
        Если какая-то фича коррелирует с таргетом > 95%, это почти всегда утечка.
        Пример: вы случайно подали close_price в фичи, пытаясь предсказать close_price.
        
        Args:
            threshold: Порог корреляции (по умолчанию 0.95 = 95%)
        """
        print(f"{Fore.CYAN}🔍 [CHECK 2/5] Correlation Spike (threshold={threshold})...{Style.RESET_ALL}")
        
        numeric_cols = self.df.select_dtypes(include=[np.number]).columns
        
        # Исключаем саму целевую переменную и временные колонки
        exclude = [self.target_col, self.time_col]
        numeric_cols = [col for col in numeric_cols if col not in exclude]
        
        if self.target_col not in self.df.columns:
            print(f"{Fore.YELLOW}⚠️  Target column '{self.target_col}' not found. Skipping.{Style.RESET_ALL}")
            return
        
        correlations = self.df[numeric_cols].corrwith(self.df[self.target_col]).abs()
        suspicious = correlations[correlations > threshold]
        
        if not suspicious.empty:
            msg = f"{Fore.YELLOW}⚠️  WARNING: Suspiciously high correlations found:{Style.RESET_ALL}"
            print(msg)
            for col, val in suspicious.items():
                print(f"     • {col}: {val:.4f}")
                self.issues_found.append(f"High correlation: {col} ({val:.4f})")
            print(f"{Fore.YELLOW}   → Check if these are derived directly from the target!{Style.RESET_ALL}")
        else:
            print(f"{Fore.GREEN}   ✅ OK - No suspicious correlations{Style.RESET_ALL}")

    # =========================================================================
    # ПРОВЕРКА 3: SHIFT INTEGRITY (Улучшенная)
    # =========================================================================
    def check_shift_integrity(self, lag_columns: Optional[list] = None):
        """
        ПРОВЕРКА 3: Lag-фичи действительно сдвинуты.
        
        Проверяет, что колонки с суффиксами '_1h_ago', '_prev', '_lag1' и т.д.
        содержат корректно сдвинутые значения из оригинальных колонок.
        
        Args:
            lag_columns: Список lag-колонок для проверки (автообнаружение если None)
        """
        print(f"{Fore.CYAN}🔍 [CHECK 3/5] Shift Integrity (lag features)...{Style.RESET_ALL}")
        
        # Автообнаружение lag-колонок
        if lag_columns is None:
            suffixes = ['_1h_ago', '_4h_ago', '_1d_ago', '_prev', '_lag1', '_lag']
            lag_columns = [col for col in self.df.columns if any(suf in col for suf in suffixes)]
        
        if not lag_columns:
            print(f"{Fore.YELLOW}   ℹ️  No lag columns detected. Skipping.{Style.RESET_ALL}")
            return
        
        print(f"   Found {len(lag_columns)} lag columns to check...")
        
        for lag_col in lag_columns:
            # Пытаемся найти оригинальную колонку
            original_col = None
            for suffix in ['_1h_ago', '_4h_ago', '_1d_ago', '_prev', '_lag1', '_lag']:
                if suffix in lag_col:
                    original_col = lag_col.replace(suffix, '')
                    break
            
            if original_col and original_col in self.df.columns:
                # Проверяем NaN в начале (при shift(1) должны появляться)
                first_value = self.df[lag_col].iloc[0]
                if pd.notna(first_value):
                    warning = f"   ⚠️  {lag_col}: No NaN at start (missing shift?)"
                    print(f"{Fore.YELLOW}{warning}{Style.RESET_ALL}")
                    self.issues_found.append(f"Missing shift: {lag_col}")
                
                # Проверяем, что lag_col[i] ≈ original_col[i-1]
                expected_shift = self.df[original_col].shift(1)
                actual_lag = self.df[lag_col]
                
                # Допускаем небольшую погрешность для float
                mismatch = (expected_shift - actual_lag).abs() > 0.0001
                mismatch_count = mismatch.sum()
                
                # Вычитаем первую строку (там всегда NaN)
                total_comparable = len(self.df) - 1
                mismatch_rate = mismatch_count / total_comparable if total_comparable > 0 else 0
                
                if mismatch_rate > 0.01:  # Больше 1% несовпадений
                    warning = f"   ❌ {lag_col}: {mismatch_count}/{total_comparable} mismatches ({mismatch_rate*100:.1f}%)"
                    print(f"{Fore.RED}{warning}{Style.RESET_ALL}")
                    self.issues_found.append(f"Shift mismatch: {lag_col}")
                else:
                    print(f"{Fore.GREEN}   ✅ {lag_col}: Correct shift{Style.RESET_ALL}")
        
        if not self.issues_found:
            print(f"{Fore.GREEN}   ✅ OK - All lag columns properly shifted{Style.RESET_ALL}")

    # =========================================================================
    # ПРОВЕРКА 4: TIMEFRAME CONSISTENCY (Специфичная для SmartCandles)
    # =========================================================================
    def check_timeframe_consistency(self, timeframe_col: str = 'timeframe'):
        """
        ПРОВЕРКА 4: Смешивание таймфреймов.
        
        1H свеча НЕ МОЖЕТ содержать контекст из 4H/1D свечи.
        Проверяет, что в одном timestamp не смешиваются разные таймфреймы.
        
        Args:
            timeframe_col: Название колонки таймфрейма (по умолчанию 'timeframe')
        """
        print(f"{Fore.CYAN}🔍 [CHECK 4/5] Timeframe Consistency...{Style.RESET_ALL}")
        
        if timeframe_col not in self.df.columns:
            print(f"{Fore.YELLOW}   ℹ️  Column '{timeframe_col}' not found. Skipping.{Style.RESET_ALL}")
            return
        
        # Группируем по времени свечи и смотрим, сколько уникальных таймфреймов
        grouped = self.df.groupby(self.time_col)[timeframe_col].apply(lambda x: list(set(x)))
        mixed = grouped[grouped.apply(len) > 1]
        
        if not mixed.empty:
            msg = (
                f"{Fore.RED}❌ ERROR: Timeframe mixing detected!{Style.RESET_ALL}\n"
                f"   Found {len(mixed)} timestamps with multiple timeframes:\n"
            )
            print(msg)
            for timestamp, frames in mixed.head(3).items():
                print(f"     {timestamp}: {frames}")
            
            self.issues_found.append(f"Timeframe mixing: {len(mixed)} timestamps")
            raise ValueError(f"Timeframe mixing detected in {len(mixed)} timestamps")
        else:
            print(f"{Fore.GREEN}   ✅ OK - No timeframe mixing{Style.RESET_ALL}")

    # =========================================================================
    # ПРОВЕРКА 5: AGGREGATION VERSION (Специфичная для SmartCandles)
    # =========================================================================
    def check_aggregation_version(self, version_col: str = 'aggregation_version', expected_version: str = '1.0'):
        """
        ПРОВЕРКА 5: Версия агрегации SmartCandles.
        
        Все свечи должны быть одной версии ('1.0').
        Смешивание версий '0.9' и '1.0' приведет к несовместимым фичам.
        
        Args:
            version_col: Название колонки версии
            expected_version: Ожидаемая версия (по умолчанию '1.0')
        """
        print(f"{Fore.CYAN}🔍 [CHECK 5/5] Aggregation Version (expected: {expected_version})...{Style.RESET_ALL}")
        
        if version_col not in self.df.columns:
            print(f"{Fore.YELLOW}   ℹ️  Column '{version_col}' not found. Skipping.{Style.RESET_ALL}")
            return
        
        versions = self.df[version_col].unique()
        
        # Проверка 1: Смешивание версий
        if len(versions) > 1:
            msg = (
                f"{Fore.RED}❌ ERROR: Mixed aggregation versions!{Style.RESET_ALL}\n"
                f"   Found versions: {versions}\n"
                f"   → Do not mix old and new SmartCandles!"
            )
            print(msg)
            self.issues_found.append(f"Mixed versions: {versions}")
            raise ValueError(f"Mixed aggregation versions: {versions}")
        
        # Проверка 2: Неправильная версия
        if versions[0] != expected_version:
            msg = (
                f"{Fore.YELLOW}⚠️  WARNING: Using old aggregation version!{Style.RESET_ALL}\n"
                f"   Current: {versions[0]}, Expected: {expected_version}\n"
                f"   → Consider regenerating SmartCandles"
            )
            print(msg)
            self.issues_found.append(f"Old version: {versions[0]}")
        else:
            print(f"{Fore.GREEN}   ✅ OK - All data is version {expected_version}{Style.RESET_ALL}")

    # =========================================================================
    # ЗАПУСК ВСЕХ ПРОВЕРОК
    # =========================================================================
    def check_all(self, feature_time_col: str = 'snapshot_time', 
                  timeframe_col: str = 'timeframe',
                  version_col: str = 'aggregation_version'):
        """
        Запускает все 5 проверок подряд.
        
        Args:
            feature_time_col: Колонка времени фичи (для check 1)
            timeframe_col: Колонка таймфрейма (для check 4)
            version_col: Колонка версии агрегации (для check 5)
        
        Raises:
            ValueError: Если найдена критическая утечка
        """
        print(f"\n{Fore.MAGENTA}{'='*70}")
        print(f"🛡️  DATA LEAKAGE GUARD - FULL AUDIT")
        print(f"{'='*70}{Style.RESET_ALL}")
        print(f"Dataset: {len(self.df)} rows, {len(self.df.columns)} columns")
        print(f"Time range: {self.df[self.time_col].min()} → {self.df[self.time_col].max()}\n")
        
        self.issues_found = []
        
        # 1. Timestamp alignment (критичная)
        self.check_timestamp_alignment(feature_time_col)
        
        # 2. Correlation spike (подозрительная)
        self.check_target_correlation_spike()
        
        # 3. Shift integrity (lag-фичи)
        self.check_shift_integrity()
        
        # 4. Timeframe consistency (SmartCandles)
        self.check_timeframe_consistency(timeframe_col)
        
        # 5. Aggregation version (SmartCandles)
        self.check_aggregation_version(version_col)
        
        # Финальный отчет
        print(f"\n{Fore.MAGENTA}{'='*70}")
        if not self.issues_found:
            print(f"{Fore.GREEN}✅ AUDIT PASSED - Dataset is clean!{Style.RESET_ALL}")
            print(f"{Fore.GREEN}   Safe to proceed with model.fit(){Style.RESET_ALL}")
        else:
            print(f"{Fore.YELLOW}⚠️  AUDIT COMPLETED WITH WARNINGS:{Style.RESET_ALL}")
            for issue in self.issues_found:
                print(f"   • {issue}")
            print(f"\n{Fore.YELLOW}   Review warnings before training.{Style.RESET_ALL}")
        print(f"{Fore.MAGENTA}{'='*70}{Style.RESET_ALL}\n")


# =============================================================================
# HELPER: SAFE MERGE для SmartCandles + Features
# =============================================================================
def safe_merge_candles_features(candles_df: pd.DataFrame, 
                                features_df: pd.DataFrame,
                                candle_time_col: str = 'candle_time',
                                feature_time_col: str = 'snapshot_time') -> pd.DataFrame:
    """
    Безопасное объединение SmartCandles + IcebergFeatures с защитой от утечек.
    
    Использует pd.merge_asof с direction='backward', что гарантирует:
    - Для каждой свечи берется БЛИЖАЙШИЙ контекст из ПРОШЛОГО
    - Контекст из будущего НЕВОЗМОЖЕН
    
    Args:
        candles_df: SmartCandles (таргет)
        features_df: IcebergFeatures (предикторы)
        candle_time_col: Колонка времени в свечах
        feature_time_col: Колонка времени в фичах
    
    Returns:
        Объединенный датасет (свечи + фичи)
    
    Example:
        candles = await repo.fetch_smart_candles(start, end)
        features = await repo.fetch_feature_snapshots(start, end)
        
        df = safe_merge_candles_features(candles, features)
        # Теперь можно безопасно обучать model.fit(df)
    """
    # Сортировка обязательна для merge_asof
    candles_sorted = candles_df.sort_values(candle_time_col).reset_index(drop=True)
    features_sorted = features_df.sort_values(feature_time_col).reset_index(drop=True)
    
    # Безопасный merge (берет только backward context)
    merged = pd.merge_asof(
        candles_sorted,
        features_sorted,
        left_on=candle_time_col,
        right_on=feature_time_col,
        direction='backward',  # ← КЛЮЧЕВАЯ ЗАЩИТА!
        suffixes=('_candle', '_feature')
    )
    
    print(f"{Fore.CYAN}🔗 Safe merge completed:{Style.RESET_ALL}")
    print(f"   Candles: {len(candles_df)} rows")
    print(f"   Features: {len(features_df)} rows")
    print(f"   Merged: {len(merged)} rows")
    
    # Проверяем, сколько свечей не получили фичи (NaN в feature_time_col)
    missing_features = merged[feature_time_col].isna().sum()
    if missing_features > 0:
        print(f"{Fore.YELLOW}   ⚠️  {missing_features} candles have no features (too early){Style.RESET_ALL}")
    
    return merged
