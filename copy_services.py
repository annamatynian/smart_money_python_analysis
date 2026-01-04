#!/usr/bin/env python3
"""
ФИНАЛЬНОЕ РЕШЕНИЕ: Копирование через Python скрипт на Windows
"""
import sys

source = '/tmp/services_fixed.py'
dest = r'C:\Users\annam\Documents\DeFi-RAG-Project\smart_money_python_analysis\services.py'

print(f"Reading {source}...")
with open(source, 'r', encoding='utf-8') as f:
    content = f.read()

first = content.split('\n')[0]
if first.startswith('[READING'):
    print("❌ Source has placeholder!")
    sys.exit(1)

print(f"✅ Source OK: {len(content)} bytes")
print(f"   First line: {first[:60]}...")

print(f"\nWriting to {dest}...")
with open(dest, 'w', encoding='utf-8', newline='\n') as f:
    f.write(content)

print("✅ WRITTEN!")

# Verify
with open(dest, 'r', encoding='utf-8') as f:
    verified = f.read()

verified_first = verified.split('\n')[0]
if verified_first.startswith('[READING'):
    print("❌ DESTINATION HAS PLACEHOLDER!")
    sys.exit(1)

print(f"✅ Verified: {len(verified)} bytes")
print(f"   First line: {verified_first[:60]}...")

print("\n🎯 SUCCESS!")
