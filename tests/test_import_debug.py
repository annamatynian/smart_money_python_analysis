#!/usr/bin/env python3
"""Minimal test to find syntax error"""
import sys
sys.path.insert(0, '.')

try:
    print("Attempting to import services...")
    import services
    print("✅ Import successful!")
except SyntaxError as e:
    print(f"❌ SYNTAX ERROR:")
    print(f"   File: {e.filename}")
    print(f"   Line {e.lineno}: {e.msg}")
    if e.text:
        print(f"   Text: {e.text.rstrip()}")
        if e.offset:
            print(f"   {' ' * (e.offset - 1)}^")
    sys.exit(1)
except Exception as e:
    import traceback
    print(f"⚠️ Other error: {type(e).__name__}: {e}")
    traceback.print_exc()
    sys.exit(1)
