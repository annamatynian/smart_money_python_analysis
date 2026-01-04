#!/usr/bin/env python3
"""Temporary script to find iceberg_analyzer.analyze calls"""

with open('services.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
    
for i, line in enumerate(lines, 1):
    if 'iceberg_analyzer.analyze' in line or 'repository.save_iceberg' in line:
        # Print surrounding context
        start = max(0, i-5)
        end = min(len(lines), i+5)
        print(f"\n{'='*60}")
        print(f"Found at line {i}:")
        print(f"{'='*60}")
        for j in range(start, end):
            marker = ">>> " if j == i-1 else "    "
            print(f"{marker}{j+1:4d}: {lines[j]}", end='')
