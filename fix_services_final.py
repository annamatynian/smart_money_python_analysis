#!/usr/bin/env python3
"""
Прямая перезапись services.py правильным содержимым
"""
import sys

print("Reading source from /tmp/services_fixed.py...")
try:
    with open('/tmp/services_fixed.py', 'r', encoding='utf-8') as f:
        content = f.read()
    
    first_line = content.split('\n')[0]
    print(f"Source first line: {first_line[:50]}...")
    print(f"Source size: {len(content)} bytes, {content.count(chr(10))} lines")
    
    if first_line.startswith('[READING FROM'):
        print("❌ ERROR: Source contains placeholder!")
        sys.exit(1)
    
    print("\nWriting to Windows services.py...")
    dest = r'C:\Users\annam\Documents\DeFi-RAG-Project\smart_money_python_analysis\services.py'
    
    with open(dest, 'w', encoding='utf-8', newline='\n') as f:
        f.write(content)
    
    print(f"✅ Written {len(content)} bytes")
    
    # Проверка
    print("\nVerifying written file...")
    with open(dest, 'r', encoding='utf-8') as f:
        written = f.read()
    
    written_first = written.split('\n')[0]
    print(f"Destination first line: {written_first[:50]}...")
    
    if written_first.startswith('[READING FROM'):
        print("❌ ERROR: Destination still has placeholder!")
        sys.exit(1)
    
    if written != content:
        print(f"⚠️ WARNING: Content mismatch! Written {len(written)} vs source {len(content)}")
    else:
        print("✅ Content matches perfectly")
    
    # Синтаксис проверка
    print("\nChecking syntax...")
    import py_compile
    py_compile.compile(dest, doraise=True)
    print("✅ SYNTAX OK!")
    
    print("\n🎯 SUCCESS! services.py is ready.")
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
