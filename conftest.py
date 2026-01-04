"""
Pytest configuration and markers
=================================

WHY: Categorization for refactoring triage
"""
import pytest


def pytest_configure(config):
    """Register custom markers"""
    # ГРУППА 2: Логика айсбергов - будет переписана при Native/Synthetic split
    config.addinivalue_line(
        "markers",
        "refactoring_pending: Tests for iceberg logic that will be refactored (skip during rework)"
    )
    
    # ГРУППА 3: ML/Фичи - отложено до окончания рефакторинга
    config.addinivalue_line(
        "markers", 
        "ml_features: Tests for ML feature collection (deferred until after refactoring)"
    )
    
    # Критичные инфраструктурные тесты
    config.addinivalue_line(
        "markers",
        "infrastructure: Critical infrastructure tests (fix immediately)"
    )
