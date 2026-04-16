# FileMaker XML Compressor

Converts a FileMaker **Save a Copy as XML** file (FileMaker 2024 / v21–22) into compact, LLM-friendly text files that Claude can actually work with.

## What It Does

Takes your large `<FMSaveAsXML>` export and produces ~10 small text files organized by category, typically **95-97% smaller** than the original. It:

- **Strips** all styling/positioning data (bounds, CSS, coordinates, colors, fonts)
- **Strips** theme definitions, UUIDs, and internal tracking data
- **Preserves** every table, field, calculation, relationship, script step, layout object, value list, custom function, and security setting
- **Deduplicates** data that FileMaker repeats across sections
- **Handles UTF-16 LE/BE with BOM** natively (no encoding gymnastics on your end)
- **Preserves emoji intact** — no restoration shim needed
- Uses a **custom shorthand format** (not XML or JSON) designed for maximum token efficiency

## Requirements

- **macOS** (the AppleScript droplet) — Python 3.6+ for the standalone script
- **Python 3.6+** (no external packages — uses only the standard library)

## Two Ways to Use It

### A. The Mac App (recommended)

`build/FileMaker XML Compressor.app` — a drag-and-drop droplet built from `build/XMLCompressor.applescript`.

1. Drop a `*_fmp12.xml` file (your Save-a-Copy-as-XML export) onto the app icon
   - or double-click the app to get a file picker
2. Pick where the compressed output folder should go
3. The app runs the compressor and offers to reveal the result in Finder

**First-launch note:** unsigned, so Gatekeeper blocks the first launch. Right-click → Open once and approve.

### B. The Python script

```bash
# Basic usage — outputs to a "<filename>_Compressed" subfolder next to your XML
python3 fm_saxml_compress.py /path/to/YourFile_fmp12.xml

# Custom output directory
python3 fm_saxml_compress.py /path/to/YourFile_fmp12.xml --output-dir /path/to/output
```

## How to Generate the Source XML

In FileMaker Pro (2024 / v21–22):

1. Open the file you want to export
2. **File → Save a Copy as…** (or the "Save a Copy as XML" item on newer versions)
3. Choose **XML** as the format
4. Save

This produces a single `*.xml` file. Feed that to the app or script.

## Output Files

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

## Rebuilding the Mac App

The compiled `.app` is gitignored (it's a build artifact). To rebuild from source:

```bash
cd build
osacompile -o "FileMaker XML Compressor.app" XMLCompressor.applescript
cp ../fm_saxml_compress.py "FileMaker XML Compressor.app/Contents/Resources/"
```

(Recompiling wipes the Resources folder, so the `cp` always has to follow.)

## If Files Are Still Too Large

The `03_SCRIPTS.txt` and `04_LAYOUTS.txt` files tend to be the largest. If they're still too big:

1. Open in a text editor
2. Search for `SCRIPT:` (or `LAYOUT:`) to find item boundaries
3. Copy just the items you're working on into a separate file
4. Upload that instead

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

**MemoryError on very large files**
→ The script loads the entire XML into memory. For files over 500MB, you may need a machine with 8GB+ RAM available. Close other applications first.

**Missing sections in output**
→ Some sections only exist if your file uses them (e.g., custom menus, external data sources).

**App says "Compression failed"**
→ The dialog shows the underlying Python traceback. Most often this means the XML isn't a Save-a-Copy-as-XML file (e.g., it's the old DDR format, or a record export). The root element should be `<FMSaveAsXML>`.

## Legacy DDR Support

Earlier versions of this project compressed the old **Database Design Report** XML (`<FMPReport>` root). That code path has been retired in favor of the newer Save-a-Copy-as-XML format, which is faster, handles emoji natively, and contains everything the DDR did. The retired DDR scripts are preserved in git history if ever needed.
