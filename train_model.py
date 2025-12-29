"""
Пример обучения ML модели с защитой от Data Leakage

WHY: Демонстрирует безопасную подготовку датасета и обучение модели
без риска "заглядывания в будущее".
"""
import asyncio
from repository import PostgresRepository
from datetime import datetime
import os

# Конфигурация
DB_DSN = os.getenv('DATABASE_URL', 'postgresql://postgres:password@localhost:5432/trading_db')


async def train_model_example():
    """
    Пример обучения XGBoost модели для предсказания цены.
    
    Модель предсказывает close следующей 1H свечи на основе:
    - Текущей свечи (OHLCV)
    - Микроструктуры (OBI, OFI)
    - CVD сегментов (whale/dolphin/minnow)
    - Деривативов (basis, skew)
    - Smart Money контекста (1w/1m/3m/6m trends)
    """
    # 1. Подключение к БД
    repo = PostgresRepository(dsn=DB_DSN)
    await repo.connect()
    
    print("=" * 70)
    print("🤖 ML MODEL TRAINING - Data Leakage Protected")
    print("=" * 70)
    
    try:
        # 2. Загрузка данных с защитой от утечек
        df = await repo.prepare_ml_dataset_safe(
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 12, 1),
            timeframe='1h',
            target_col='next_hour_close',
            symbol='BTCUSDT'
        )
        
        print(f"\n📊 Dataset Summary:")
        print(f"   Rows: {len(df)}")
        print(f"   Columns: {len(df.columns)}")
        print(f"   Date range: {df['candle_time'].min()} → {df['candle_time'].max()}")
        print(f"   Target: {df['next_hour_close'].describe()}\n")
        
        # 3. Подготовка признаков для модели
        # Убираем колонки времени и идентификаторы
        drop_cols = [
            'candle_time', 
            'next_hour_close',  # Таргет
            'snapshot_time',    # Время фичи (если есть)
            'lifecycle_event_id',  # ID (если есть)
            'symbol',
            'timeframe',
            'aggregation_version'
        ]
        
        # Удаляем только существующие колонки
        drop_cols_existing = [col for col in drop_cols if col in df.columns]
        
        X = df.drop(columns=drop_cols_existing)
        y = df['next_hour_close']
        
        print(f"🎯 Features: {len(X.columns)} columns")
        print(f"   {list(X.columns[:10])}... (showing first 10)\n")
        
        # 4. Train/Test split (по времени!)
        # IMPORTANT: НЕ используем random split для time-series!
        split_idx = int(len(df) * 0.8)
        
        X_train = X.iloc[:split_idx]
        X_test = X.iloc[split_idx:]
        y_train = y.iloc[:split_idx]
        y_test = y.iloc[split_idx:]
        
        print(f"📈 Train/Test Split:")
        print(f"   Train: {len(X_train)} rows ({len(X_train)/len(df)*100:.1f}%)")
        print(f"   Test: {len(X_test)} rows ({len(X_test)/len(df)*100:.1f}%)\n")
        
        # 5. Обучение модели
        print(f"🤖 Training XGBoost model...")
        
        from xgboost import XGBRegressor
        
        model = XGBRegressor(
            n_estimators=100,
            max_depth=6,
            learning_rate=0.1,
            random_state=42,
            n_jobs=-1
        )
        
        model.fit(X_train, y_train)
        
        # 6. Оценка качества
        from sklearn.metrics import mean_squared_error, r2_score
        import numpy as np
        
        y_pred_train = model.predict(X_train)
        y_pred_test = model.predict(X_test)
        
        rmse_train = np.sqrt(mean_squared_error(y_train, y_pred_train))
        rmse_test = np.sqrt(mean_squared_error(y_test, y_pred_test))
        r2_train = r2_score(y_train, y_pred_train)
        r2_test = r2_score(y_test, y_pred_test)
        
        print(f"\n✅ Model Training Complete!")
        print(f"\n📊 Model Performance:")
        print(f"   Train RMSE: {rmse_train:.2f}")
        print(f"   Test RMSE: {rmse_test:.2f}")
        print(f"   Train R²: {r2_train:.4f}")
        print(f"   Test R²: {r2_test:.4f}")
        
        # 7. Feature Importance (топ-10)
        import pandas as pd
        
        feature_importance = pd.DataFrame({
            'feature': X.columns,
            'importance': model.feature_importances_
        }).sort_values('importance', ascending=False)
        
        print(f"\n🎯 Top 10 Most Important Features:")
        for idx, row in feature_importance.head(10).iterrows():
            print(f"   {row['feature']}: {row['importance']:.4f}")
        
        print(f"\n" + "=" * 70)
        print(f"✅ SUCCESS - Model trained without data leakage!")
        print(f"=" * 70)
        
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        # 8. Закрываем соединение
        await repo.close()


if __name__ == '__main__':
    asyncio.run(train_model_example())
