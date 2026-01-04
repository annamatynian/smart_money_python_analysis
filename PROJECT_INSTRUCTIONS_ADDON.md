# CRITICAL FILE OPERATION RULES - Add to Project Instructions

## 🛑 INLINE TOOL WARNINGS (Read BEFORE using tools)

### bash_tool:
```
⚠️ WINDOWS CRITICAL WARNING ⚠️
NEVER use bash_tool for writing to C:\ paths!

FORBIDDEN (destroys files):
  cat >> "C:\path\file"
  echo >> "C:\path\file"  
  sed -i 's/...' "C:\path\file"
  
ALLOWED:
  cat "C:\path\file" (read only)
  ls "C:\path\" (read only)
  
For Windows file writes: Use Filesystem:write_file
```

### Filesystem:edit_file:
```
⚠️ LARGE EDIT WARNING ⚠️
If changing >50 lines → Use Filesystem:write_file instead

WORKFLOW:
1. Count lines to change
2. If >50: read_file → modify → write_file
3. If <50: edit_file (copy EXACT indentation)
4. ALWAYS after: python -m py_compile file.py
```

### Filesystem:write_file / edit_file (both):
```
⚠️ MANDATORY VERIFICATION ⚠️
After EVERY file edit:

STEP 1: Verify syntax
  bash_tool: python -m py_compile services.py
  
STEP 2: If SyntaxError → ROLLBACK
  bash_tool: cp services.py.backup_* services.py
  
STEP 3: Run tests
  bash_tool: pytest tests/test_X.py -v

NO BLIND FIXES. Rollback if broken.
```

---

## ENFORCEMENT MECHANISM

Before using bash_tool, Filesystem:edit_file, or Filesystem:write_file:

**Claude MUST print:**
```
═══════════════════════════════════════
🛑 FILE OPERATION CHECKLIST 🛑
═══════════════════════════════════════
File: [path]
Operation: [read/write/edit]

✓ Platform: [ ] Linux [ ] Windows
✓ If Windows: Using Filesystem (not bash)? ___
✓ If edit: Size [ ] <50 lines [ ] >50 lines
✓ If >50: Using write_file? ___
✓ Backup exists? ___
✓ Will verify with py_compile? ___
═══════════════════════════════════════
```

Then answer EVERY question before proceeding.

---

## COST OF VIOLATION

Proven incident (2025-01-02):
- Time lost: 50 minutes
- Tokens wasted: 80,000 (42% of budget)
- Cause: Used str_replace on 200-line block
- Prevention: Follow checklist EVERY TIME

---

## QUICK REFERENCE

| Operation | Windows Path? | Size | Tool |
|-----------|---------------|------|------|
| Read file | C:\ | Any | Filesystem:read_text_file |
| Edit file | C:\ | <50 lines | Filesystem:edit_file |
| Edit file | C:\ | >50 lines | read → modify → write_file |
| Write file | C:\ | Any | Filesystem:write_file |
| Write file | C:\ | Any | ❌ NEVER bash cat/echo/sed |
