#!/usr/bin/env python3
"""
Прямая диагностика: что Python видит в services.py
"""
import sys
import os

# Проверяем sys.path
print("=== sys.path ===")
for i, p in enumerate(sys.path):
    print(f"{i}: {p}")

# Проверяем какой файл будет импортироваться
print("\n=== Finding services.py ===")
import importlib.util
spec = importlib.util.find_spec("services")
if spec:
    print(f"Found: {spec.origin}")
else:
    print("NOT FOUND in sys.path")

# Читаем файл НАПРЯМУЮ
print("\n=== Reading file directly ===")
services_path = r'C:\Users\annam\Documents\DeFi-RAG-Project\smart_money_python_analysis\services.py'

if os.path.exists(services_path):
    print(f"File exists: {services_path}")
    print(f"Size: {os.path.getsize(services_path)} bytes")
    
    with open(services_path, 'r', encoding='utf-8') as f:
        first_line = f.readline().strip()
        second_line = f.readline().strip()
        third_line = f.readline().strip()
    
    print(f"\nFirst 3 lines:")
    print(f"1: {first_line}")
    print(f"2: {second_line}")
    print(f"3: {third_line}")
    
    if first_line.startswith('[READING'):
        print("\n❌❌❌ FILE ON DISK HAS PLACEHOLDER!")
    else:
        print("\n✅ File on disk is correct")
else:
    print(f"❌ File does not exist: {services_path}")

# Проверяем __pycache__
print("\n=== Checking __pycache__ ===")
pycache_dir = r'C:\Users\annam\Documents\DeFi-RAG-Project\smart_money_python_analysis\__pycache__'
if os.path.exists(pycache_dir):
    print(f"Found __pycache__: {pycache_dir}")
    files = os.listdir(pycache_dir)
    services_pyc = [f for f in files if f.startswith('services')]
    if services_pyc:
        print(f"Found cached: {services_pyc}")
        print("⚠️ Deleting cached files...")
        for f in services_pyc:
            full_path = os.path.join(pycache_dir, f)
            os.remove(full_path)
            print(f"   Deleted: {f}")
    else:
        print("No services.*.pyc found")
else:
    print("No __pycache__ directory")

print("\n=== Now trying import ===")
os.chdir(r'C:\Users\annam\Documents\DeFi-RAG-Project\smart_money_python_analysis')
sys.path.insert(0, '.')

try:
    import services
    print("✅ Import succeeded!")
    print(f"services.__file__ = {services.__file__}")
except SyntaxError as e:
    print(f"❌ SYNTAX ERROR:")
    print(f"   File: {e.filename}")
    print(f"   Line {e.lineno}: {e.msg}")
    if e.text:
        print(f"   Text: {e.text.rstrip()}")
except Exception as e:
    print(f"⚠️ Other error: {type(e).__name__}: {e}")
