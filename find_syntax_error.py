#!/usr/bin/env python3
"""Find exact syntax error location"""
import sys
sys.path.insert(0, '.')

try:
    print("Step 1: Importing services...")
    import services
    print("✅ services imported OK")
    
    print("\nStep 2: Importing test...")
    import tests.test_error_boundary
    print("✅ test imported OK")
    
except SyntaxError as e:
    print(f"\n❌ SYNTAX ERROR FOUND:")
    print(f"   File: {e.filename}")
    print(f"   Line: {e.lineno}")
    print(f"   Message: {e.msg}")
    if e.text:
        text = e.text.rstrip()
        print(f"   Code: {text}")
        if e.offset:
            print(f"         {' ' * (e.offset - 1)}^")
    sys.exit(1)
except Exception as e:
    import traceback
    print(f"\n⚠️ Other error: {type(e).__name__}: {e}")
    traceback.print_exc()
    sys.exit(1)

print("\n✅ ALL IMPORTS SUCCESSFUL!")
