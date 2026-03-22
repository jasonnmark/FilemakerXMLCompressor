#!/usr/bin/env python3
"""
DDR XML Structure Diagnostic
Scans a FileMaker DDR XML to find where script step content lives.
Usage: python fm_ddr_diag.py /path/to/YourFile_fmp12.xml
"""
import sys
import xml.etree.ElementTree as ET
from collections import Counter

if len(sys.argv) < 2:
    print("Usage: python fm_ddr_diag.py /path/to/DDR.xml")
    sys.exit(1)

xml_path = sys.argv[1]
print(f"Scanning {xml_path}...")

# Use iterparse to avoid loading entire tree
tag_counter = Counter()
step_locations = []
steplist_locations = []
stepname_count = 0
script_with_children = 0
script_total = 0
max_script_children = 0
sample_script_with_steps = None

# Track nesting
path_stack = []

for event, elem in ET.iterparse(xml_path, events=("start", "end")):
    if event == "start":
        path_stack.append(elem.tag)
        current_path = "/".join(path_stack[-4:])  # last 4 levels

        if elem.tag == "Step":
            step_locations.append(current_path)
            if len(step_locations) <= 3:
                # Get parent info
                print(f"  Found <Step> at path: .../{current_path}")

        if elem.tag == "StepList":
            steplist_locations.append(current_path)
            if len(steplist_locations) <= 3:
                print(f"  Found <StepList> at path: .../{current_path}")

        if elem.tag == "StepName":
            stepname_count += 1

        if elem.tag == "Script":
            script_total += 1

        # Count top-level children of root
        if len(path_stack) == 2:
            tag_counter[elem.tag] += 1

    elif event == "end":
        if elem.tag == "Script" and len(path_stack) >= 2:
            children = list(elem)
            child_tags = [c.tag for c in children]
            if len(children) > 2:
                script_with_children += 1
                if len(children) > max_script_children:
                    max_script_children = len(children)
                    sample_script_with_steps = {
                        "name": elem.get("name", "?"),
                        "id": elem.get("id", "?"),
                        "child_tags": child_tags[:10],
                        "num_children": len(children),
                    }
            elem.clear()  # Free memory
        elif elem.tag not in ("Step", "StepList", "Script", "StepName"):
            if len(path_stack) > 3:
                elem.clear()

        if path_stack:
            path_stack.pop()

print()
print("=" * 60)
print("DDR XML STRUCTURE REPORT")
print("=" * 60)

print(f"\nTotal <Script> elements: {script_total}")
print(f"Scripts with >2 child elements: {script_with_children}")
print(f"Max children in a Script: {max_script_children}")
print(f"Total <Step> elements found: {len(step_locations)}")
print(f"Total <StepList> elements found: {len(steplist_locations)}")
print(f"Total <StepName> elements found: {stepname_count}")

print(f"\nTop-level children of root (File element):")
for tag, count in tag_counter.most_common(20):
    print(f"  <{tag}> x{count}")

if step_locations:
    print(f"\nStep element paths (first 5 unique):")
    for p in list(dict.fromkeys(step_locations))[:5]:
        print(f"  .../{p}")

if steplist_locations:
    print(f"\nStepList element paths (first 5 unique):")
    for p in list(dict.fromkeys(steplist_locations))[:5]:
        print(f"  .../{p}")

if sample_script_with_steps:
    print(f"\nSample Script with most children:")
    print(f"  Name: {sample_script_with_steps['name']}")
    print(f"  ID: {sample_script_with_steps['id']}")
    print(f"  Children: {sample_script_with_steps['num_children']}")
    print(f"  Child tags: {sample_script_with_steps['child_tags']}")

if not step_locations and not steplist_locations:
    print("\n⚠️  NO <Step> or <StepList> elements found anywhere!")
    print("This DDR may not include script step details.")
    print("Try re-exporting the DDR with 'Include script steps' checked.")
