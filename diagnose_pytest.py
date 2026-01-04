#!/usr/bin/env python3
"""
Диагностика pytest SyntaxError
"""
import sys
import os

os.chdir(r'C:\Users\annam\Documents\DeFi-RAG-Project\smart_money_python_analysis')
sys.path.insert(0, '.')

print("=== Step 1: Import domain ===")
try:
    import domain
    print("✅ domain imported OK")
except SyntaxError as e:
    print(f"❌ SYNTAX ERROR in domain.py:")
    print(f"   Line {e.lineno}: {e.msg}")
    if e.text:
        print(f"   Code: {e.text.rstrip()}")
    sys.exit(1)
except Exception as e:
    print(f"⚠️ domain import error: {type(e).__name__}: {e}")

print("\n=== Step 2: Import services ===")
try:
    import services
    print("✅ services imported OK")
except SyntaxError as e:
    print(f"❌ SYNTAX ERROR in services.py:")
    print(f"   Line {e.lineno}: {e.msg}")
    if e.text:
        print(f"   Code: {e.text.rstrip()}")
    sys.exit(1)
except Exception as e:
    print(f"⚠️ services import error: {type(e).__name__}: {e}")

print("\n=== Step 3: Import test file ===")
try:
    import tests.test_error_boundary
    print("✅ test_error_boundary imported OK")
except SyntaxError as e:
    print(f"❌ SYNTAX ERROR in test:")
    print(f"   File: {e.filename}")
    print(f"   Line {e.lineno}: {e.msg}")
    if e.text:
        print(f"   Code: {e.text.rstrip()}")
        if e.offset:
            print(f"   {' ' * (e.offset - 1)}^")
    sys.exit(1)
except Exception as e:
    import traceback
    print(f"⚠️ test import error: {type(e).__name__}: {e}")
    traceback.print_exc()

print("\n✅ ALL IMPORTS SUCCESSFUL! Running pytest...")
import pytest
sys.exit(pytest.main(['tests/test_error_boundary.py', '-v']))
