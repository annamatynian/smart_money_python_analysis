# Temporary script to find method location
with open(r'C:\Users\annam\Documents\DeFi-RAG-Project\smart_money_python_analysis\repository.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
    
for i, line in enumerate(lines, 1):
    if 'def get_aggregated_smart_candles' in line:
        print(f"Line {i}: {line.strip()}")
        print(f"Context (lines {i} to {i+5}):")
        for j in range(i-1, min(i+5, len(lines))):
            print(f"{j+1}: {lines[j]}", end='')
        print("\n" + "="*80 + "\n")
