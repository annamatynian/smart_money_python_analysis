# CRITICAL GUARDRAILS - MANDATORY CHECKS

## ⚠️ BEFORE ANY FILE OPERATION - CHECK THIS LIST ⚠️

### 🛑 STOP! Are you about to edit a file on Windows?

**ASK YOURSELF:**
1. [ ] Am I using bash_tool to WRITE/MODIFY a Windows file (C:\...)? 
   - ❌ **FORBIDDEN!** Use MCP Filesystem instead
   
2. [ ] Am I editing >50 lines with str_replace?
   - ❌ **FORBIDDEN!** Read full file → modify → write_file
   
3. [ ] Did I create a backup BEFORE editing?
   - ❌ **FORBIDDEN!** Create backup first: `cp file.py file.py.backup`
   
4. [ ] Will I verify syntax IMMEDIATELY after editing?
   - ❌ **FORBIDDEN!** Always run: `python -m py_compile file.py`

---

## 🚨 AUTOMATIC TRIGGERS - Read These BEFORE Tool Calls

### BEFORE `bash_tool`:
```
🛑 STOP! Is this a Windows path (C:\...)? 
   → YES: Use Filesystem:read_text_file / write_file / edit_file
   → NO: bash_tool OK for read-only operations
```

### BEFORE `Filesystem:edit_file`:
```
🛑 STOP! How many lines am I changing?
   → >50 lines: Use Filesystem:write_file (full rewrite)
   → <50 lines: OK, but COPY EXACT INDENTATION from original
```

### BEFORE `str_replace`:
```
🛑 STOP! Did I copy indentation EXACTLY?
   → Count spaces in original: "    def method():" = 4 spaces
   → new_str must have IDENTICAL spacing
```

---

## 💀 FORBIDDEN OPERATIONS - NEVER DO THESE

### ❌ bash_tool + Windows paths + WRITE operations:
```bash
# THESE DESTROY FILES ON WINDOWS:
cat >> "C:\path\file.py"           # ❌ OVERWRITES FILE
echo "content" >> "C:\path\file"   # ❌ OVERWRITES FILE  
sed -i 's/old/new/' "C:\path\file" # ❌ CORRUPTS FILE
python script.py > "C:\path\file"  # ❌ UNPREDICTABLE
```

### ❌ str_replace on large blocks:
```python
# THIS BREAKS INDENTATION:
Filesystem:edit_file(
    old_str="def method():\n    line1\n...\n    line200",  # 200 lines
    new_str="def method():\n    CHANGED\n...\n    line200"
)
# One wrong space = SyntaxError
```

### ❌ Editing without backup:
```python
# THIS IS GAMBLING:
Filesystem:edit_file(path="services.py", ...)  # No backup!
# If it breaks → 50 minutes lost
```

---

## ✅ MANDATORY WORKFLOW - ALWAYS FOLLOW

### For ANY file edit on Windows:

```
STEP 1: CREATE BACKUP
  bash_tool: cp services.py services.py.backup_$(date +%Y%m%d_%H%M%S)
  
STEP 2: CHOOSE METHOD
  If changes >50 lines:
    → Filesystem:read_text_file
    → Modify in memory
    → Filesystem:write_file (full content)
  If changes <50 lines:
    → Filesystem:edit_file
    → Copy EXACT indentation from original
    
STEP 3: VERIFY IMMEDIATELY
  bash_tool: python -m py_compile services.py
  
STEP 4: RUN TESTS
  bash_tool: pytest tests/test_X.py -v
  
STEP 5: If SyntaxError → ROLLBACK
  bash_tool: cp services.py.backup_* services.py
```

---

## 🎯 ENFORCEMENT MECHANISM

### Add this to PROJECT INSTRUCTIONS (top of system prompt):

```xml
<critical_file_operations_checklist>
BEFORE using bash_tool, Filesystem:edit_file, or str_replace:

1. READ THIS FILE: EDITING_GUARDRAILS.md
2. ANSWER CHECKLIST QUESTIONS (out loud in response)
3. If ANY answer is "NO" → STOP and use alternative method
4. After edit → ALWAYS verify: python -m py_compile file.py
5. If SyntaxError → ROLLBACK immediately, don't try to fix blindly

VIOLATIONS COST:
- 2025-01-02 indentation incident: 50 minutes, 80K tokens wasted
- Root cause: Ignored "no bash for Windows files" rule
- Prevention: ENFORCE this checklist EVERY TIME
</critical_file_operations_checklist>
```

---

## 📋 PRE-FLIGHT CHECKLIST (Print Before EVERY Edit)

```
═══════════════════════════════════════════════
🛑 FILE EDIT PRE-FLIGHT CHECKLIST 🛑
═══════════════════════════════════════════════

File: ________________
Operation: [ ] Read [ ] Edit [ ] Write

✓ Backup created? _____
✓ Platform: [ ] Linux [ ] Windows
✓ If Windows: Using MCP Filesystem? _____
✓ Edit size: [ ] <50 lines [ ] >50 lines
✓ If >50: Using write_file (not edit_file)? _____
✓ Indentation verified (spaces match)? _____
✓ Will run py_compile after? _____

═══════════════════════════════════════════════
PROCEED ONLY IF ALL CHECKED ✓
═══════════════════════════════════════════════
```

---

## 🔧 RECOMMENDED PROMPT STRUCTURE

Instead of long instructions at top, use **INLINE CONTEXT** near tool definitions:

```xml
<function name="bash_tool">
  <description>
    ⚠️ WINDOWS WARNING: Do NOT use for writing to C:\ paths
    Use Filesystem:write_file instead
    
    Run a bash command in the container
  </description>
  ...
</function>

<function name="Filesystem:edit_file">
  <description>
    ⚠️ LARGE EDITS WARNING: If changing >50 lines, use write_file instead
    ⚠️ INDENTATION WARNING: Copy exact spacing from original
    
    Make line-based edits to a text file
  </description>
  ...
</function>
```

This keeps warnings **VISIBLE** when I'm about to use the tool.

---

## 🎓 WHY LONG INSTRUCTIONS DON'T WORK

**Problem:**
- Instructions at top of prompt = 10,000 words away
- When context grows (100K+ tokens), early instructions "fade"
- LLM focuses on recent conversation, not distant rules

**Solution:**
- **INLINE warnings** near tool definitions ✅
- **CHECKLIST** printed before EVERY edit ✅  
- **IMMEDIATE validation** after edit ✅
- **Auto-rollback** on SyntaxError ✅

---

## 💡 IMPLEMENTATION RECOMMENDATION

Add to **TOP** of system prompt (replace current file rules):

```
<file_edit_enforcement>
CRITICAL: Before ANY file operation, Claude MUST:
1. Print the PRE-FLIGHT CHECKLIST (from EDITING_GUARDRAILS.md)
2. Answer EVERY question
3. If any "NO" → use alternative method
4. After edit → run: python -m py_compile file.py
5. If error → IMMEDIATE rollback, no blind fixes

COST OF VIOLATION: 50 min + 80K tokens (proven 2025-01-02)
</file_edit_enforcement>
```

---

## 🏆 SUCCESS METRICS

You'll know this works when:
- ✅ I print checklist BEFORE every edit
- ✅ I never use bash for Windows file writes
- ✅ I verify syntax immediately after edits
- ✅ I rollback on error instead of guessing fixes
- ✅ Zero indentation incidents for 30 days

---

**Bottom line:** 
Long instructions don't work. Need **SHORT, LOUD, INLINE** warnings that I can't miss.
