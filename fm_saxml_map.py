#!/usr/bin/env python3
"""
FileMaker SaXML Structure Mapper
=================================
Scans a Save-as-XML file and reports the element structure,
catalog names, sample objects, and key differences from DDR XML.

Usage: python fm_saxml_map.py <path_to_saxml.xml>

Share the output so the compressor can be built to match.
"""
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

if len(sys.argv) < 2:
    print("Usage: python fm_saxml_map.py <path_to_saxml.xml>")
    sys.exit(1)

xml_path = sys.argv[1]
print(f"Scanning: {xml_path}")
print()

import re

print("Reading XML...")
# Detect encoding from raw bytes first
with open(xml_path, 'rb') as f:
    head = f.read(200)

# Check for BOM or encoding declaration
encoding = 'utf-8'
if head[:2] == b'\xff\xfe':
    encoding = 'utf-16-le'
    print(f"  Detected UTF-16 LE (BOM)")
elif head[:2] == b'\xfe\xff':
    encoding = 'utf-16-be'
    print(f"  Detected UTF-16 BE (BOM)")
elif head[:3] == b'\xef\xbb\xbf':
    encoding = 'utf-8-sig'
    print(f"  Detected UTF-8 with BOM")
elif b'\x00' in head[:20]:
    # Likely UTF-16 without BOM
    if head[0:1] == b'<' and head[1:2] == b'\x00':
        encoding = 'utf-16-le'
        print(f"  Detected UTF-16 LE (no BOM)")
    elif head[0:1] == b'\x00' and head[1:2] == b'<':
        encoding = 'utf-16-be'
        print(f"  Detected UTF-16 BE (no BOM)")
else:
    # Check XML declaration for encoding
    head_str = head.decode('ascii', errors='ignore')
    enc_match = re.search(r'encoding=["\']([^"\']+)["\']', head_str)
    if enc_match:
        encoding = enc_match.group(1)
        print(f"  Detected encoding from XML declaration: {encoding}")
    else:
        print(f"  Assuming UTF-8")

import os
file_size = os.path.getsize(xml_path)
print(f"  File size: {file_size / (1024*1024):.1f} MB")

with open(xml_path, 'r', encoding=encoding, errors='replace') as f:
    raw = f.read()

print(f"  Read {len(raw)} characters")

# Remove XML-illegal control chars (0x00-0x08, 0x0B, 0x0C, 0x0E-0x1F) but keep tab/newline/CR
cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', raw)
n_stripped = len(raw) - len(cleaned)
if n_stripped > 0:
    print(f"  Stripped {n_stripped} illegal control characters.")
else:
    print("  No illegal characters found.")

# Sanity check: should start with '<'
cleaned = cleaned.lstrip('\ufeff')  # strip BOM if present
first_char = cleaned.lstrip()[:1]
if first_char != '<':
    print(f"  ⚠️  File doesn't start with '<' (starts with {repr(first_char)})")
    print(f"  First 100 chars: {repr(cleaned[:100])}")
    sys.exit(1)

print("Parsing XML...")
root = ET.fromstring(cleaned)
del raw, cleaned  # free memory
print("Done parsing.")

# ============================================================
# 1. TOP-LEVEL STRUCTURE (2 levels deep)
# ============================================================
print("=" * 70)
print("TOP-LEVEL STRUCTURE (root and first 2 levels)")
print("=" * 70)

def show_tree(el, indent=0, max_depth=2, max_children=15):
    attrs = " ".join(f'{k}="{v[:30]}"' for k, v in list(el.attrib.items())[:3])
    n_children = len(list(el))
    label = f"<{el.tag}"
    if attrs:
        label += f" {attrs}"
    label += f"> ({n_children} children)"
    print(f"{'  ' * indent}{label}")
    if indent < max_depth:
        for i, child in enumerate(el):
            if i >= max_children:
                print(f"{'  ' * (indent+1)}... and {n_children - max_children} more")
                break
            show_tree(child, indent + 1, max_depth, max_children)

show_tree(root, 0, 2, 20)

# ============================================================
# 2. ALL CATALOG/TOP-LEVEL SECTION NAMES
# ============================================================
print()
print("=" * 70)
print("ALL TOP-LEVEL SECTIONS (children of root or root/Structure)")
print("=" * 70)

# SaXML wraps everything in <FMDynamicTemplate> or similar
# Find the main container
structure = root.find("Structure") or root
for child in structure:
    n = len(list(child))
    print(f"  <{child.tag}> — {n} children")
    # Also show first level inside each catalog
    for i, grandchild in enumerate(child):
        if i >= 5:
            print(f"    ... and {n - 5} more <{grandchild.tag}> elements")
            break
        gc_attrs = " ".join(f'{k}="{v[:40]}"' for k, v in list(grandchild.attrib.items())[:3])
        print(f"    <{grandchild.tag} {gc_attrs}>")

# ============================================================
# 3. SAMPLE SCRIPT (first one with steps)
# ============================================================
print()
print("=" * 70)
print("SAMPLE SCRIPT STRUCTURE")
print("=" * 70)

# Find ScriptCatalog or ScriptCatalogue
for tag_guess in ["ScriptCatalog", "ScriptCatalogue", "ScriptCataogue"]:
    sc = root.find(f".//{tag_guess}")
    if sc is not None:
        print(f"Found: <{tag_guess}>")
        # Show first Script element structure
        for script in sc.iter():
            if "Script" in script.tag and script.tag != tag_guess:
                print(f"\n  Sample <{script.tag}> element:")
                show_tree(script, 2, 4, 8)
                break
        break
else:
    # Search for anything with "Script" in tag name
    script_tags = set()
    for el in root.iter():
        if "cript" in el.tag:
            script_tags.add(el.tag)
    print(f"No ScriptCatalog found. Script-related tags: {script_tags}")

# Find StepsForScripts
for tag_guess in ["StepsForScripts", "ScriptSteps"]:
    ss = root.find(f".//{tag_guess}")
    if ss is not None:
        print(f"\nFound: <{tag_guess}> with {len(list(ss))} children")
        # Show first script's steps
        for child in ss:
            print(f"\n  Sample steps container <{child.tag}>:")
            show_tree(child, 2, 4, 6)
            break
        break

# ============================================================
# 4. SAMPLE LAYOUT WITH OBJECTS
# ============================================================
print()
print("=" * 70)
print("SAMPLE LAYOUT STRUCTURE")
print("=" * 70)

for tag_guess in ["LayoutCatalog", "LayoutCatalogue"]:
    lc = root.find(f".//{tag_guess}")
    if lc is not None:
        print(f"Found: <{tag_guess}> with {len(list(lc))} children")
        # Find a layout with objects
        for layout in lc.iter():
            if "Layout" in layout.tag and layout.tag != tag_guess:
                n_descendants = sum(1 for _ in layout.iter())
                if n_descendants > 10:
                    print(f"\n  Sample layout (with {n_descendants} descendants):")
                    show_tree(layout, 2, 5, 8)
                    break
        break

# ============================================================
# 5. SAMPLE FIELD DEFINITION
# ============================================================
print()
print("=" * 70)
print("SAMPLE FIELD/TABLE STRUCTURE")
print("=" * 70)

for tag_guess in ["BaseTableCatalog", "BaseTableCatalogue", "FieldsForTables",
                   "FieldCatalog", "FieldCatalogue", "FieldCataloogue"]:
    fc = root.find(f".//{tag_guess}")
    if fc is not None:
        print(f"Found: <{tag_guess}> with {len(list(fc))} children")
        for child in fc:
            print(f"\n  Sample <{child.tag}>:")
            show_tree(child, 2, 4, 6)
            break
        break

# ============================================================
# 6. SAMPLE RELATIONSHIP / TABLE OCCURRENCE
# ============================================================
print()
print("=" * 70)
print("SAMPLE RELATIONSHIP/TO STRUCTURE")
print("=" * 70)

for tag_guess in ["RelationshipGraph", "TableOccuerenceCatalogue",
                   "TableOccurrenceCatalog", "TableOccurrenceCatalogue"]:
    rg = root.find(f".//{tag_guess}")
    if rg is not None:
        print(f"Found: <{tag_guess}> with {len(list(rg))} children")
        for child in list(rg)[:2]:
            print(f"\n  Sample <{child.tag}>:")
            show_tree(child, 2, 4, 6)
        break

# ============================================================
# 7. EMOJI TEST
# ============================================================
print()
print("=" * 70)
print("EMOJI SURVIVAL TEST")
print("=" * 70)

emoji_chars = set()
for el in root.iter():
    for attr_val in el.attrib.values():
        for ch in attr_val:
            if ord(ch) > 0xFFFF:
                emoji_chars.add(ch)
    if el.text:
        for ch in el.text:
            if ord(ch) > 0xFFFF:
                emoji_chars.add(ch)

if emoji_chars:
    print(f"Found {len(emoji_chars)} unique supplementary-plane characters (emoji):")
    for ch in sorted(emoji_chars, key=ord):
        print(f"  U+{ord(ch):05X} = {ch}")
    print("✅ Emoji survive in SaXML!")
else:
    qq_count = sum(1 for el in root.iter()
                   for v in list(el.attrib.values()) + [el.text or ""]
                   if "??" in v)
    if qq_count:
        print(f"⚠️  No emoji found but {qq_count} '??' patterns detected — corruption present")
    else:
        print("No supplementary-plane characters found (may be normal for small files)")

# ============================================================
# 8. QUICK STATS
# ============================================================
print()
print("=" * 70)
print("QUICK STATS")
print("=" * 70)

tag_counts = Counter()
for el in root.iter():
    tag_counts[el.tag] += 1

print(f"Total elements: {sum(tag_counts.values())}")
print(f"Unique tags: {len(tag_counts)}")
print("\nTop 30 most common tags:")
for tag, count in tag_counts.most_common(30):
    print(f"  {tag}: {count}")

print("\n\nDone! Share this output to build the compressor.")