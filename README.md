# FileMaker DDR XML Compressor

Converts a FileMaker Database Design Report (DDR) XML file into compact, LLM-friendly text files that Claude can actually work with.

## What It Does

Takes your 146MB+ DDR XML and produces ~10 small text files organized by category, typically **95-97% smaller** than the original. It:

- **Strips** all `<DisplayCalculation>` blocks (redundant - just formatted versions of the raw calc)
- **Strips** all styling/positioning data (bounds, CSS, coordinates, colors, fonts)
- **Strips** theme definitions, UUIDs, and internal tracking data
- **Preserves** every table, field, calculation, relationship, script step, layout object, value list, custom function, and security setting
- **Deduplicates** data that FileMaker repeats across sections
- Uses a **custom shorthand format** (not XML or JSON) designed for maximum token efficiency

## Requirements

- **Python 3.6+** (no external packages needed - uses only the standard library)

## Setup

1. Copy `fm_ddr_compress.py` to any convenient folder
2. That's it. No pip install, no dependencies.

## Usage

### Step 1: Generate Your DDR

In FileMaker Pro:
1. **File → Save/Send Records As → Database Design Report** (or **Tools → Database Design Report** in older versions)
2. Select **XML** format
3. Check all the sections you want (recommended: check everything)
4. Choose an output folder and click **Create**

This produces a folder with `Summary.xml` and one or more detail files like `YourFile_fmp12.xml`. **You want the detail file** (the big one), not Summary.xml.

### Step 2: Run the Compressor

```bash
# Basic usage - outputs to a DDR_Compressed subfolder next to your XML
python fm_ddr_compress.py /path/to/YourFile_fmp12.xml

# Custom output directory
python fm_ddr_compress.py /path/to/YourFile_fmp12.xml --output-dir /path/to/output

# On Windows
python fm_ddr_compress.py "C:\Users\You\Documents\DDR\YourFile_fmp12.xml"

# On Mac
python3 fm_ddr_compress.py ~/Documents/DDR/YourFile_fmp12.xml
```

### Step 3: Upload to Claude

The output folder contains 10 files. Upload what you need based on your task:

| File | Contents | Upload when... |
|------|----------|---------------|
| `01_SCHEMA.txt` | Tables, fields, calcs, auto-enter, validation, storage | Working on data model, field calcs, or schema questions |
| `02_RELATIONSHIPS.txt` | Table occurrences, relationship predicates, sort orders | Debugging relationships or building new ones |
| `03_SCRIPTS.txt` | All scripts with every step and parameter | Working on script logic, debugging, or writing new scripts |
| `04_LAYOUTS.txt` | Layout objects, portals, triggers, conditional visibility | Working on UI, layout triggers, or object scripting |
| `05_VALUELISTS.txt` | Value list definitions (custom and field-based) | Working with dropdowns, pop-up menus |
| `06_CUSTOM_FUNCS.txt` | Custom function names, params, and full bodies | Debugging or writing calculations using custom functions |
| `07_ACCOUNTS.txt` | Accounts, privilege sets, extended privileges | Security questions |
| `08_MENUS.txt` | Custom menus and menu sets | Menu customization |
| `09_FILE_REFS.txt` | External data source paths | Multi-file solutions |
| `10_SUMMARY.txt` | Stats overview and file guide | Quick reference |

**Common combos:**
- **Schema work:** 01 + 02 (+ 06 if you use custom functions in calcs)
- **Script development:** 03 (+ 01 if scripts reference field structures)
- **Layout/UI work:** 04 + 03 (scripts are often triggered from layouts)
- **Full picture:** 01 + 02 + 03 + 06 (usually fits in context)

## If Files Are Still Too Large

The `03_SCRIPTS.txt` file tends to be the largest. If it's still too big:

1. Open it in a text editor
2. Search for `SCRIPT:` to find script boundaries
3. Copy just the scripts you're working on into a separate file
4. Upload that instead

Same approach works for `01_SCHEMA.txt` - search for `T:` to find table boundaries.

## Output Format Explained

The format uses a compact shorthand with a key at the top of each file. Example from SCHEMA:

```
T: Contacts (1523 records) [id:129]
  F: __pk_ContactID | N | T [id:1]
    AE: Serial(OnCreation,next:1524,inc:1)
    VAL: when:Always, notEmpty, unique
    STOR: idx:All
  F: NameFirst | N | T [id:2]
    STOR: idx:Minimal
  F: NameFull | C | T [id:5]
    CALC: NameFirst & " " & NameLast
    STOR: UNSTORED
  F: _cAge | C | N [id:12]
    CALC: Year(Get(CurrentDate)) - Year(DOB)
    STOR: UNSTORED
    CMT: Calculated age in years
```

This is roughly **10-20x more token-efficient** than the equivalent XML while preserving all the information you'd need for development work.

## Troubleshooting

**"This appears to be the Summary XML file"**
→ You pointed it at `Summary.xml`. Use the detail file instead (usually named like `YourFile_fmp12.xml`).

**MemoryError on very large files**
→ The script loads the entire XML into memory. For files over 500MB, you may need a machine with 8GB+ RAM available. Close other applications first.

**Missing sections in output**
→ The DDR only includes sections you checked when generating it. Re-generate with all sections checked.

**Encoding errors**
→ Try: `python fm_ddr_compress.py yourfile.xml 2>&1 | head -50` to see where it fails. The DDR should be UTF-8 but some FileMaker versions produce inconsistent encoding.
