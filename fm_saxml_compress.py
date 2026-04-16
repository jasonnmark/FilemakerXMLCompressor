#!/usr/bin/env python3
"""
FileMaker SaXML Compressor
===========================
Converts a FileMaker "Save as XML" (SaXML) file into compact,
Claude-friendly text files organized by category.

Works with FileMaker 21/22 SaXML exports (UTF-16 LE with BOM).
Emoji survive intact — no restoration needed.

Usage:
    python fm_saxml_compress.py <path_to_saxml.xml> [--output-dir <dir>]
"""

import xml.etree.ElementTree as ET
import os
import sys
import re
import argparse
from collections import defaultdict


# ============================================================
# HELPERS
# ============================================================

def attr(el, name, default=""):
    """Get attribute from element, empty string if missing."""
    if el is None:
        return default
    return el.get(name, default)


def text(el):
    """Get text content of element."""
    if el is None:
        return ""
    return (el.text or "").strip()


def find_text(parent, tag):
    """Find a child element and return its text."""
    if parent is None:
        return ""
    el = parent.find(tag)
    return text(el)


def find_calc(parent):
    """Extract calculation text. SaXML pattern: Calculation > Text > CDATA."""
    if parent is None:
        return ""
    # Direct Calculation child
    calc = parent.find("Calculation")
    if calc is None:
        # Auto-enter: Calculated > Calculation
        calcd = parent.find("Calculated")
        if calcd is not None:
            calc = calcd.find("Calculation")
    if calc is not None:
        # SaXML: Calculation > Text with CDATA
        t = calc.find("Text")
        if t is not None and t.text:
            return t.text.strip()
        # Direct text on Calculation element
        if calc.text and calc.text.strip():
            return calc.text.strip()
    # Fallback: Text child of parent
    t = parent.find("Text")
    if t is not None and t.text:
        return t.text.strip()
    return ""


def ref_str(ref_el):
    """Format a reference element (FieldReference, ScriptReference, etc.)."""
    if ref_el is None:
        return ""
    name = attr(ref_el, "name")
    rid = attr(ref_el, "id")
    return f"{name}" if name else f"id:{rid}"


def field_ref_str(parent):
    """Extract TO::Field from FieldReference + TableOccurrenceReference."""
    if parent is None:
        return ""
    fr = parent.find("FieldReference")
    if fr is None:
        fr = parent.find(".//FieldReference")
    if fr is None:
        return ""
    fname = attr(fr, "name")
    # TO reference might be sibling or nested
    tor = parent.find("TableOccurrenceReference")
    if tor is None:
        tor = fr.find("TableOccurrenceReference")
    if tor is None:
        tor = parent.find(".//TableOccurrenceReference")
    to_name = attr(tor, "name") if tor is not None else ""
    if to_name and fname:
        return f"{to_name}::{fname}"
    return fname or ""


# ============================================================
# STEP TYPE MAP (SaXML step id -> human name)
# ============================================================
# In SaXML, Step id= is the step TYPE, not a unique ID.
# index= is the position in the script.

STEP_TYPES = {
    "1": "Perform Script",
    "2": "Go to Layout",
    "3": "Go to Record/Request/Page",
    "6": "Go to Layout",
    "7": "New Record/Request",
    "8": "Duplicate Record/Request",
    "9": "Delete Record/Request",
    "12": "Omit Record",
    "14": "Go to Portal Row",
    "16": "Delete All Records",
    "17": "Sort Records",
    "19": "Open Record/Request",
    "20": "Revert Record/Request",
    "25": "Enter Find Mode",
    "28": "Perform Find",
    "29": "Show All Records",
    "31": "Modify Last Find",
    "32": "Omit Multiple Records",
    "38": "Enter Browse Mode",
    "39": "Enter Preview Mode",
    "55": "Show Custom Dialog",
    "61": "Refresh Window",
    "63": "Freeze Window",
    "67": "Set Window Title",
    "68": "If",
    "69": "Else",
    "70": "End If",
    "71": "Loop",
    "72": "Exit Loop If",
    "73": "End Loop",
    "74": "Go to Related Record",
    "75": "Commit Records/Requests",
    "76": "Set Field",
    "79": "Freeze Window",
    "82": "Export Records",
    "85": "Import Records",
    "87": "Show Custom Dialog",
    "89": "Comment",
    "100": "Scroll Window",
    "103": "Exit Script",
    "104": "Halt Script",
    "125": "Set Error Capture",
    "129": "Allow User Abort",
    "134": "Close File",
    "135": "Open File",
    "136": "Send Mail",
    "140": "Perform Script on Server",
    "141": "Set Variable",
    "164": "Install OnTimer Script",
    "166": "Show/Hide Menubar",
    "167": "Show/Hide Toolbars",
    "169": "Go to Object",
    "170": "Open URL",
    "171": "Insert from URL",
    "172": "Set Web Viewer",
    "173": "Save Records as PDF",
    "174": "Save Records as Excel",
    "176": "Go to Layout (animation)",
    "177": "Exit Script (result)",
    "178": "Set Field By Name",
    "184": "Perform Script on Server (wait)",
    "186": "Set Layout Object Animation",
    "188": "Refresh Object",
    "189": "Truncate Table",
    "190": "Open Manage Database",
    "192": "Configure Region Monitor Script",
    "197": "Perform JavaScript in Web Viewer",
    "198": "Configure Local Notification",
}


# ============================================================
# MAIN COMPRESSOR CLASS
# ============================================================

class SaXMLCompressor:
    def __init__(self, xml_path, output_dir=None):
        self.xml_path = xml_path
        self.output_dir = output_dir or os.path.dirname(xml_path)
        self.root = None
        self.structure = None
        self.add_action = None
        self.modify_action = None
        self.stats = {}

    def parse(self):
        """Parse the SaXML file, handling UTF-16 and BOM."""
        print(f"Parsing {self.xml_path}...")
        file_size = os.path.getsize(self.xml_path)
        print(f"  File size: {file_size / (1024*1024):.1f} MB")

        # Detect encoding
        with open(self.xml_path, 'rb') as f:
            head = f.read(200)

        encoding = 'utf-8'
        if head[:2] == b'\xff\xfe':
            encoding = 'utf-16-le'
        elif head[:2] == b'\xfe\xff':
            encoding = 'utf-16-be'
        elif head[:3] == b'\xef\xbb\xbf':
            encoding = 'utf-8-sig'
        elif b'\x00' in head[:20]:
            if head[0:1] == b'<' and head[1:2] == b'\x00':
                encoding = 'utf-16-le'
            elif head[0:1] == b'\x00' and head[1:2] == b'<':
                encoding = 'utf-16-be'
        print(f"  Encoding: {encoding}")

        with open(self.xml_path, 'r', encoding=encoding, errors='replace') as f:
            raw = f.read()

        # Strip BOM and illegal XML control chars
        raw = raw.lstrip('\ufeff')
        raw = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', raw)

        print("  Parsing XML tree...")
        self.root = ET.fromstring(raw)
        del raw

        # Navigate to Structure > AddAction / ModifyAction
        self.structure = self.root.find("Structure")
        if self.structure is None:
            self.structure = self.root

        for child in self.structure:
            if child.tag == "AddAction":
                self.add_action = child
            elif child.tag == "ModifyAction":
                self.modify_action = child

        if self.add_action is None:
            print("  WARNING: No <AddAction> found. Trying root as fallback.")
            self.add_action = self.structure

        # Also check Metadata for additional AddAction
        metadata = self.root.find("Metadata")
        if metadata is not None:
            meta_add = metadata.find("AddAction")
            # We'll use this for accounts/privileges if needed
            self.meta_add_action = meta_add
        else:
            self.meta_add_action = None

        source = attr(self.root, "Source")
        fname = attr(self.root, "File")
        print(f"  Source: FileMaker {source}")
        print(f"  File: {fname}")
        print("  Parsed successfully.")

    def _find_catalog(self, tag, search_modify=False):
        """Find a catalog element in AddAction or ModifyAction."""
        if self.add_action is not None:
            el = self.add_action.find(tag)
            if el is not None:
                return el
        if search_modify and self.modify_action is not None:
            el = self.modify_action.find(tag)
            if el is not None:
                return el
        # Fallback: search everywhere
        return self.root.find(f".//{tag}")

    # ============================================================
    # 01 SCHEMA — Tables & Fields
    # ============================================================
    def extract_schema(self):
        output = []
        output.append("=" * 70)
        output.append("FILEMAKER SCHEMA - Tables & Fields")
        output.append("=" * 70)
        output.append("")
        output.append("KEY:")
        output.append("  T: <table_name> [id:<id>]")
        output.append("  F: <name> | <type> | <data_type> [id:<id>]")
        output.append("     type: Normal, Calculated, Summary")
        output.append("     data_type: Text, Number, Date, Time, Timestamp, Container")
        output.append("  CALC: <formula>")
        output.append("  AE: auto-enter info")
        output.append("  VAL: validation rules")
        output.append("  CMT: field comment")
        output.append("")

        # Base tables
        bt_catalog = self._find_catalog("BaseTableCatalog")
        if bt_catalog is None:
            output.append("(No BaseTableCatalog found)")
            return "\n".join(output)

        table_count = 0
        field_count = 0

        # Build table name map from BaseTableCatalog
        table_map = {}  # id -> name
        for bt in bt_catalog:
            if bt.tag in ("BaseTable", "Table") or "Table" in bt.tag:
                tid = attr(bt, "id")
                tname = attr(bt, "name")
                if tid and tname:
                    table_map[tid] = tname

        # Fields: can be in FieldsForTables or standalone FieldCatalog elements
        # Structure: FieldCatalog > BaseTableReference + ObjectList > Field
        # OR: FieldsForTables > FieldCatalog > ...
        fields_by_table = defaultdict(list)

        for action in [self.add_action, self.modify_action]:
            if action is None:
                continue

            # Path 1: FieldsForTables wrapper
            for fft in action.findall("FieldsForTables"):
                self._collect_fields(fft, fields_by_table)

            # Path 2: Standalone FieldCatalog (could be direct child or deeper)
            for fc in action.findall("FieldCatalog"):
                self._collect_fields(fc, fields_by_table)

        # Output tables with their fields
        for (tid, tname) in sorted(fields_by_table.keys(), key=lambda x: x[1]):
            table_count += 1
            fields = fields_by_table[(tid, tname)]
            output.append(f"T: {tname} [id:{tid}] ({len(fields)} fields)")

            for field in fields:
                fid = attr(field, "id")
                fname = attr(field, "name")
                ftype = attr(field, "fieldtype") or attr(field, "fieldType", "Normal")
                dtype = attr(field, "datatype") or attr(field, "dataType", "")
                comment = attr(field, "comment", "")

                if not fname:
                    continue
                field_count += 1

                output.append(f"  F: {fname} | {ftype} | {dtype} [id:{fid}]")

                # Calculation
                calc = field.find("Calculation")
                if calc is not None:
                    calc_text = find_calc(field)
                    if calc_text:
                        output.append(f"    CALC: {calc_text[:200]}")

                # Auto-enter
                ae = field.find("AutoEnter")
                if ae is not None:
                    ae_parts = []
                    ae_type = attr(ae, "type")
                    if ae_type:
                        ae_parts.append(ae_type)
                    ae_calc = find_calc(ae)
                    if ae_calc:
                        ae_parts.append(f"Calc={ae_calc[:120]}")
                    if ae_parts:
                        output.append(f"    AE: {', '.join(ae_parts)}")

                # Validation
                val = field.find("Validation")
                if val is not None:
                    val_parts = []
                    vtype = attr(val, "type")
                    if vtype:
                        val_parts.append(vtype)
                    if attr(val, "notEmpty") == "True":
                        val_parts.append("notEmpty")
                    if attr(val, "unique") == "True":
                        val_parts.append("unique")
                    if attr(val, "allowOverride") == "False":
                        val_parts.append("strict")
                    if val_parts:
                        output.append(f"    VAL: {', '.join(val_parts)}")

                # Comment (attribute on Field element)
                if comment:
                    output.append(f"    CMT: {comment[:100]}")

                # Storage (global, unstored, index)
                stor = field.find("Storage")
                if stor is not None:
                    stor_parts = []
                    if attr(stor, "global") == "True":
                        stor_parts.append("global")
                    if attr(stor, "storeCalculationResults") == "False":
                        stor_parts.append("unstored")
                    idx = attr(stor, "index")
                    if idx and idx != "None":
                        stor_parts.append(f"index:{idx}")
                    reps = attr(stor, "maxRepetitions")
                    if reps and reps != "1":
                        stor_parts.append(f"reps:{reps}")
                    if stor_parts:
                        output.append(f"    STOR: {', '.join(stor_parts)}")

            output.append("")

        # Tables without fields (from BaseTableCatalog)
        found_ids = set(tid for tid, _ in fields_by_table.keys())
        for bt in bt_catalog:
            tid = attr(bt, "id")
            tname = attr(bt, "name")
            if tid and tid not in found_ids and tname:
                table_count += 1
                output.append(f"T: {tname} [id:{tid}] (0 fields in export)")
                output.append("")

        self.stats["tables"] = table_count
        self.stats["fields"] = field_count
        print(f"  Schema: {table_count} tables, {field_count} fields")
        return "\n".join(output)

    def _collect_fields(self, container, fields_by_table):
        """Collect fields from a FieldCatalog or FieldsForTables container."""
        table_ref = (container.find("BaseTableReference")
                     or container.find("TableReference"))
        if table_ref is None:
            for child in container:
                if "Reference" in child.tag and "Field" not in child.tag:
                    table_ref = child
                    break

        tname = attr(table_ref, "name") if table_ref is not None else "?"
        tid = attr(table_ref, "id") if table_ref is not None else ""

        # Fields in ObjectList > Field
        obj_list = container.find("ObjectList")
        if obj_list is not None:
            for field in obj_list:
                if field.tag == "Field":
                    fields_by_table[(tid, tname)].append(field)
        else:
            # Nested FieldCatalog
            fc = container.find("FieldCatalog") or container.find("FieldCataloogue")
            if fc is not None:
                self._collect_fields(fc, fields_by_table)
            else:
                for field in container:
                    if field.tag == "Field" and field.get("name"):
                        fields_by_table[(tid, tname)].append(field)

    # ============================================================
    # 02 TABLE OCCURRENCES & RELATIONSHIPS (merged)
    # ============================================================
    def extract_relationships(self):
        output = []
        output.append("=" * 70)
        output.append("FILEMAKER TABLE OCCURRENCES & RELATIONSHIPS")
        output.append("=" * 70)
        output.append("")
        output.append("KEY:")
        output.append("  TO: <n> -> base:<table> [id:<id>]")
        output.append("    REL: <=> <other_TO> [predicates] (flags)")
        output.append("    Each TO shows its relationships indented below.")
        output.append("")

        to_catalog = self._find_catalog("TableOccurrenceCatalog")
        if to_catalog is None:
            to_catalog = self._find_catalog("TableOccuerenceCatalogue")
        if to_catalog is None:
            output.append("(No TableOccurrenceCatalog found)")
            return "\n".join(output)

        to_count = 0
        rel_count = 0

        # Collect all relationships, indexed by TO name
        rels_by_to = defaultdict(list)

        all_rels = []
        for action in [self.add_action, self.modify_action]:
            if action is None:
                continue
            all_rels.extend(action.findall("Relationship"))
            for child in action:
                if "Relationship" in child.tag or "Catalog" in child.tag:
                    all_rels.extend(child.findall("Relationship"))
        all_rels.extend(to_catalog.findall("Relationship"))
        if not all_rels:
            all_rels = list(self.root.iter("Relationship"))

        for rel in all_rels:
            left_tor = rel.find("LeftTable/TableOccurrenceReference")
            left_name = attr(left_tor, "name") if left_tor is not None else "?"
            left_tbl = rel.find("LeftTable")
            l_create = attr(left_tbl, "cascadeCreate") if left_tbl is not None else ""
            l_delete = attr(left_tbl, "cascadeDelete") if left_tbl is not None else ""

            right_tor = rel.find("RightTable/TableOccurrenceReference")
            right_name = attr(right_tor, "name") if right_tor is not None else "?"
            right_tbl = rel.find("RightTable")
            r_create = attr(right_tbl, "cascadeCreate") if right_tbl is not None else ""
            r_delete = attr(right_tbl, "cascadeDelete") if right_tbl is not None else ""

            preds = []
            jpl = rel.find("JoinPredicateList")
            if jpl is not None:
                for jp in jpl.findall("JoinPredicate"):
                    jp_type = attr(jp, "type", "Equal")
                    lf_ref = jp.find("LeftField/FieldReference")
                    lf_name = attr(lf_ref, "name") if lf_ref is not None else "?"
                    lf_tor = lf_ref.find("TableOccurrenceReference") if lf_ref is not None else None
                    lf_to = attr(lf_tor, "name") if lf_tor is not None else ""
                    rf_ref = jp.find("RightField/FieldReference")
                    rf_name = attr(rf_ref, "name") if rf_ref is not None else "?"
                    rf_tor = rf_ref.find("TableOccurrenceReference") if rf_ref is not None else None
                    rf_to = attr(rf_tor, "name") if rf_tor is not None else ""
                    lf_str = f"{lf_to}::{lf_name}" if lf_to else lf_name
                    rf_str = f"{rf_to}::{rf_name}" if rf_to else rf_name
                    op_map = {"Equal": "=", "NotEqual": "\u2260", "GreaterThan": ">",
                              "GreaterThanOrEqual": "\u2265", "LessThan": "<",
                              "LessThanOrEqual": "\u2264", "CartesianProduct": "\u2716\ufe0f"}
                    op = op_map.get(jp_type, jp_type)
                    preds.append(f"{lf_str} {op} {rf_str}")

            flags = []
            if l_create == "True": flags.append("createL")
            if r_create == "True": flags.append("createR")
            if l_delete == "True": flags.append("deleteL")
            if r_delete == "True": flags.append("deleteR")

            pred_str = " [" + ", ".join(preds) + "]" if preds else ""
            flag_str = " (" + ",".join(flags) + ")" if flags else ""
            rel_count += 1

            rels_by_to[left_name].append(f"<=> {right_name}{pred_str}{flag_str}")
            rels_by_to[right_name].append(f"<=> {left_name}{pred_str}{flag_str}")

        # Output each TO with its relationships inline
        for to_el in to_catalog:
            if to_el.tag != "TableOccurrence":
                continue
            to_name = attr(to_el, "name")
            to_id = attr(to_el, "id")
            if not to_name:
                continue
            to_count += 1

            bt_ref = to_el.find("BaseTableSourceReference/BaseTableReference")
            if bt_ref is None:
                bt_ref = to_el.find(".//BaseTableReference")
            bt_name = attr(bt_ref, "name") if bt_ref is not None else "?"

            output.append(f"TO: {to_name} -> base:{bt_name} [id:{to_id}]")

            for rel_line in rels_by_to.get(to_name, []):
                output.append(f"  REL: {rel_line}")

        self.stats["table_occurrences"] = to_count
        self.stats["relationships"] = rel_count
        print(f"  Relationships: {to_count} TOs, {rel_count} joins")
        return "\n".join(output)

    # ============================================================
    # 03 SCRIPTS — Names, folders, and steps
    # ============================================================
    def extract_scripts(self):
        output = []
        output.append("=" * 70)
        output.append("FILEMAKER SCRIPTS")
        output.append("=" * 70)
        output.append("")
        output.append("KEY:")
        output.append("  FOLDER: <name> [id:<id>]")
        output.append("  SCRIPT: <name> [id:<id>]")
        output.append("    Steps numbered sequentially per script.")
        output.append("    <line#>. <StepType> <parameters>")
        output.append("")

        # Script catalog has names/folders
        sc = self._find_catalog("ScriptCatalog")
        # Steps are in StepsForScripts
        sfs = self._find_catalog("StepsForScripts")

        # Build step index: script_id -> list of Step elements
        step_index = {}
        if sfs is not None:
            for script_el in sfs:
                if script_el.tag != "Script":
                    continue
                sref = script_el.find("ScriptReference")
                if sref is not None:
                    sid = attr(sref, "id")
                    obj_list = script_el.find("ObjectList")
                    if obj_list is not None:
                        step_index[sid] = list(obj_list)
                    else:
                        # Steps might be direct children
                        steps = script_el.findall("Step")
                        if steps:
                            step_index[sid] = steps
            print(f"  Step index: {len(step_index)} scripts with steps")

        script_count = 0
        step_total = 0

        if sc is None:
            output.append("(No ScriptCatalog found)")
            return "\n".join(output)

        def process_script_item(item, depth=0):
            nonlocal script_count, step_total
            prefix = "  " * depth

            is_folder = attr(item, "isFolder") == "True"
            name = attr(item, "name")
            sid = attr(item, "id")

            if is_folder:
                output.append(f"{prefix}FOLDER: {name} [id:{sid}]")
                for child in item:
                    if child.tag == "Script":
                        process_script_item(child, depth + 1)
                output.append("")
            elif item.tag == "Script" and name:
                script_count += 1
                output.append(f"{prefix}SCRIPT: {name} [id:{sid}]")

                # Get steps from index
                steps = step_index.get(sid, [])
                # Sort by index attribute
                steps_sorted = sorted(steps, key=lambda s: int(attr(s, "index", "0")))

                for line_num, step in enumerate(steps_sorted, 1):
                    step_text = self._format_step(step, depth + 1, line_num)
                    if step_text:
                        output.append(step_text)
                        step_total += 1

                output.append("")

        for child in sc:
            if child.tag == "Script":
                process_script_item(child, 0)

        self.stats["scripts"] = script_count
        print(f"  Scripts: {script_count} scripts, {step_total} steps")
        return "\n".join(output)

    def _format_step(self, step, depth, line_num):
        """Format a SaXML script step."""
        prefix = "  " * depth
        step_type_id = attr(step, "id")
        step_name = STEP_TYPES.get(step_type_id, f"Step#{step_type_id}")
        enabled = attr(step, "enabled", "True")
        disabled = " [DISABLED]" if enabled == "False" else ""

        # Extract parameters based on step type
        params = self._extract_saxml_step_params(step, step_type_id, step_name)

        if params:
            return f"{prefix}{line_num}. {step_name}{disabled} {params}"
        else:
            return f"{prefix}{line_num}. {step_name}{disabled}"

    def _extract_saxml_step_params(self, step, type_id, step_name):
        """Extract parameters from a SaXML step element."""

        # Comment
        if type_id == "89":
            t = step.find(".//Text")
            if t is not None:
                ct = text(t)
                if ct:
                    return f"// {ct}"
            return "// (empty)"

        # Set Variable (141)
        if type_id == "141":
            params = step.findall(".//ParameterValues/Parameter")
            var_name = ""
            var_value = ""
            for p in params:
                pid = attr(p, "id")
                val = p.find("Value")
                if val is not None:
                    calc = find_calc(val)
                    if pid == "0":  # Variable name
                        var_name = calc or find_text(val, "Text")
                    elif pid == "1":  # Value
                        var_value = calc or find_text(val, "Text")
            if var_name:
                return f"{var_name} = {var_value}" if var_value else var_name

        # Set Field (76)
        if type_id == "76":
            fr = field_ref_str(step)
            calc = ""
            params = step.findall(".//ParameterValues/Parameter")
            for p in params:
                val = p.find("Value")
                if val is not None:
                    c = find_calc(val)
                    if c:
                        calc = c
                        break
            if fr:
                return f"{fr} = {calc}" if calc else fr

        # If / Else If (68)
        if type_id == "68":
            params = step.findall(".//ParameterValues/Parameter")
            for p in params:
                val = p.find("Value")
                if val is not None:
                    c = find_calc(val)
                    if c:
                        return f"({c})"

        # Exit Loop If (72)
        if type_id == "72":
            params = step.findall(".//ParameterValues/Parameter")
            for p in params:
                val = p.find("Value")
                if val is not None:
                    c = find_calc(val)
                    if c:
                        return f"({c})"

        # Perform Script (1) / Perform Script on Server (140)
        if type_id in ("1", "140"):
            sref = step.find(".//ScriptReference")
            sname = attr(sref, "name") if sref is not None else "?"
            # Script parameter
            params = step.findall(".//ParameterValues/Parameter")
            param_calc = ""
            for p in params:
                val = p.find("Value")
                if val is not None:
                    c = find_calc(val)
                    if c:
                        param_calc = c
                        break
            result = f'"{sname}"'
            if param_calc:
                result += f" param=({param_calc})"
            return result

        # Go to Layout (6, 176)
        if type_id in ("6", "176"):
            layout_ref = step.find(".//LayoutReference")
            if layout_ref is not None:
                return f'"{attr(layout_ref, "name")}"'
            # By calculation
            params = step.findall(".//ParameterValues/Parameter")
            for p in params:
                val = p.find("Value")
                if val is not None:
                    c = find_calc(val)
                    if c:
                        return f"=> {c}"

        # Show Custom Dialog (87, 55)
        if type_id in ("87", "55"):
            params = step.findall(".//ParameterValues/Parameter")
            title = ""
            msg = ""
            for p in params:
                pid = attr(p, "id")
                val = p.find("Value")
                if val is not None:
                    c = find_calc(val)
                    if pid == "0":
                        title = c
                    elif pid == "1":
                        msg = c
            result = ""
            if title:
                result += f"title=({title})"
            if msg:
                result += f" msg=({msg})"
            return result.strip()

        # Exit Script (103, 177)
        if type_id in ("103", "177"):
            params = step.findall(".//ParameterValues/Parameter")
            for p in params:
                val = p.find("Value")
                if val is not None:
                    c = find_calc(val)
                    if c:
                        return f"result=({c})"

        # Insert from URL (171)
        if type_id == "171":
            fr = field_ref_str(step)
            params = step.findall(".//ParameterValues/Parameter")
            url = ""
            for p in params:
                val = p.find("Value")
                if val is not None:
                    c = find_calc(val)
                    if c and ("http" in c.lower() or "fmnet" in c.lower() or len(c) > 20):
                        url = c
                        break
            if fr and url:
                return f"target={fr} url=({url[:120]})"
            elif url:
                return f"url=({url[:120]})"
            elif fr:
                return f"target={fr}"

        # Commit Records (75)
        if type_id == "75":
            opts = step.find(".//Options")
            if opts is not None and attr(opts, "NoInteract") == "True":
                return "[no dialog]"

        # Sort Records (17)
        if type_id == "17":
            # Sort info in parameters
            pass

        # Go to Related Record (74)
        if type_id == "74":
            tor = step.find(".//TableOccurrenceReference")
            layout_ref = step.find(".//LayoutReference")
            parts = []
            if tor is not None:
                parts.append(f"TO:{attr(tor, 'name')}")
            if layout_ref is not None:
                parts.append(f'layout:"{attr(layout_ref, "name")}"')
            return " | ".join(parts) if parts else ""

        # DDR_INFO display text (FM 21+ with analysis info)
        ddr_info = step.find("DDR_INFO")
        if ddr_info is not None:
            display = find_text(ddr_info, "Display")
            if display:
                return display[:150]

        # Generic: try to extract any calculation or reference
        calc = find_calc(step)
        if calc:
            return f"=> {calc[:120]}"
        fr = field_ref_str(step)
        if fr:
            return fr

        return ""

    # ============================================================
    # 04 LAYOUTS — with full object detail
    # ============================================================
    def extract_layouts(self):
        output = []
        output.append("=" * 70)
        output.append("FILEMAKER LAYOUTS")
        output.append("=" * 70)
        output.append("")
        output.append("KEY:")
        output.append("  LAYOUT: <name> [id:<id>] TO:<table_occurrence>")
        output.append("    TRIGGER: <type> -> <script>")
        output.append("    FIELD <TO::FieldName> | name:<obj_name>")
        output.append("    BUTTON \"label\" | -> <script> | param=(...)")
        output.append("    PORTAL showFrom:<TO> | filter=(...)")
        output.append("    TABCTRL tabs:[...] | POPOVER | WEBVIEWER")
        output.append("")

        lc = self._find_catalog("LayoutCatalog", search_modify=True)
        if lc is None:
            output.append("(No LayoutCatalog found)")
            return "\n".join(output)

        layout_count = 0
        obj_count = 0

        # Layouts can be in Groups (folders) or directly
        def process_layout_item(item, depth=0):
            nonlocal layout_count, obj_count
            prefix = "  " * depth

            if item.tag == "Group" or attr(item, "isFolder") == "True":
                name = attr(item, "name")
                output.append(f"{prefix}FOLDER: {name}")
                for child in item:
                    process_layout_item(child, depth + 1)
                return

            if item.tag != "Layout":
                return

            layout_count += 1
            name = attr(item, "name")
            lid = attr(item, "id")
            tor = item.find("TableOccurrenceReference")
            to_name = attr(tor, "name") if tor is not None else "?"

            output.append(f"{prefix}LAYOUT: {name} [id:{lid}] TO:{to_name}")

            # Layout triggers (direct children only, not deep into objects)
            triggers_el = item.find("ScriptTriggers")
            if triggers_el is not None:
                for trigger in triggers_el.findall("ScriptTrigger"):
                    sref = trigger.find("ScriptReference")
                    if sref is not None:
                        taction = attr(trigger, "action", "Trigger")
                        output.append(f"{prefix}  TRIGGER: {taction} -> {attr(sref, 'name')}")

            # Parts contain objects
            parts_list = item.find("PartsList")
            if parts_list is not None:
                for part in parts_list.findall("Part"):
                    obj_list = part.find("ObjectList")
                    if obj_list is not None:
                        for lo in obj_list:
                            if lo.tag == "LayoutObject":
                                self._process_layout_object(lo, output, depth + 1)
                                obj_count += 1

            # Also check for ObjectList directly under Layout
            for obj_list in item.findall("ObjectList"):
                for lo in obj_list:
                    if lo.tag == "LayoutObject":
                        self._process_layout_object(lo, output, depth + 1)
                        obj_count += 1

            output.append("")

        for child in lc:
            process_layout_item(child, 0)

        self.stats["layouts"] = layout_count
        print(f"  Layouts: {layout_count} layouts, {obj_count} objects")
        return "\n".join(output)

    def _process_layout_object(self, lo, output, depth, max_depth=8):
        """Process a single LayoutObject from SaXML."""
        if depth > max_depth:
            return
        prefix = "  " * depth

        otype = attr(lo, "type")
        oname = attr(lo, "name")

        # Skip decorative / noise
        if otype in ("Line", "Rectangle", "RoundedRect", "Oval", "Graphic"):
            return

        # --- HIDE CONDITION (applies to any object) ---
        hide_calc = ""
        hide_el = lo.find("Hide")
        if hide_el is not None:
            hc = hide_el.find("Calculation")
            if hc is not None:
                t = hc.find("Text")
                hide_calc = (t.text or "").strip() if t is not None else ""

        # --- PORTAL ---
        portal_el = lo.find("Portal")
        if otype == "Portal" or portal_el is not None:
            if portal_el is None:
                portal_el = lo
            tor = portal_el.find("TableOccurrenceReference")
            portal_to = attr(tor, "name") if tor is not None else "?"
            parts = [f"PORTAL showFrom:{portal_to}"]
            if oname:
                parts.append(f"name:{oname}")
            # Filter (Calculation directly inside Portal)
            pc = portal_el.find("Calculation")
            if pc is not None:
                t = pc.find("Text")
                fc = (t.text or "").strip() if t is not None else ""
                if fc:
                    parts.append(f"filter=({fc[:120]})")
            # Sort
            sort_spec = portal_el.find("SortSpecification")
            if sort_spec is not None:
                sorts = []
                for sort in sort_spec.findall(".//Sort"):
                    fr = sort.find(".//FieldReference")
                    if fr is not None:
                        fname = attr(fr, "name")
                        tor2 = fr.find("TableOccurrenceReference")
                        to2 = attr(tor2, "name") if tor2 is not None else ""
                        sdir = "↑" if attr(sort, "type") == "Ascending" else "↓"
                        sorts.append(f"{to2}::{fname}{sdir}" if to2 else f"{fname}{sdir}")
                if sorts:
                    parts.append(f"sort=[{','.join(sorts)}]")
            if hide_calc:
                parts.append(f"hideWhen=({hide_calc[:80]})")
            output.append(f"{prefix}{' | '.join(parts)}")
            # Recurse into portal's ObjectList
            for obj_list in portal_el.findall("ObjectList"):
                for child_lo in obj_list:
                    if child_lo.tag == "LayoutObject":
                        self._process_layout_object(child_lo, output, depth + 1, max_depth)
            return

        # --- GROUPED BUTTON (most common button type in SaXML) ---
        gb_el = lo.find("GroupedButton")
        if otype == "Grouped Button" or gb_el is not None:
            if gb_el is None:
                gb_el = lo
            parts = ["BUTTON"]
            if oname:
                parts.append(f'"{oname}"')
            # Script in <action> > <ScriptReference>
            action = gb_el.find("action")
            if action is not None:
                sref = action.find("ScriptReference")
                if sref is not None:
                    parts.append(f"-> {attr(sref, 'name')}")
                    # Script param: action > Calculation > Text
                    pcalc = action.find("Calculation/Text")
                    if pcalc is not None and pcalc.text and pcalc.text.strip():
                        parts.append(f"param=({pcalc.text.strip()[:100]})")
                # Single-step action (no script reference)
                if sref is None:
                    step = action.find("Step")
                    if step is not None:
                        step_type = attr(step, "id")
                        sname = STEP_TYPES.get(step_type, f"Step#{step_type}")
                        parts.append(f"action:{sname}")
            # Find label from child text/field objects
            for child_lo in gb_el.iter("LayoutObject"):
                if child_lo is not lo:
                    child_type = attr(child_lo, "type")
                    if child_type == "Edit Box":
                        cfr = self._get_field_ref(child_lo)
                        if cfr:
                            parts.append(cfr)
                            break
                    elif child_type == "Text":
                        data = child_lo.find(".//Data")
                        if data is not None and data.text:
                            label = data.text.strip()[:50]
                            if label:
                                parts.append(f'label:"{label}"')
                                break
            if hide_calc:
                parts.append(f"hideWhen=({hide_calc[:80]})")
            output.append(f"{prefix}{' | '.join(parts)}")
            return

        # --- BUTTON (plain, non-grouped) ---
        button_el = lo.find("Button")
        if otype == "Button" or button_el is not None:
            parts = ["BUTTON"]
            if oname:
                parts.append(f'"{oname}"')
            btn = button_el if button_el is not None else lo
            # Label: Button > Label > Text > StyledText > Data
            label_data = btn.find("Label/Text/StyledText/Data")
            if label_data is not None and label_data.text:
                label = label_data.text.strip()[:50]
                if label:
                    parts.append(f'label:"{label}"')
            # Script: action > ScriptReference
            action = btn.find("action")
            if action is not None:
                sref = action.find("ScriptReference")
                if sref is not None:
                    parts.append(f"-> {attr(sref, 'name')}")
                    # Script param: action > Calculation > Text
                    pcalc = action.find("Calculation/Text")
                    if pcalc is not None and pcalc.text and pcalc.text.strip():
                        parts.append(f"param=({pcalc.text.strip()[:100]})")
                else:
                    # Single-step action
                    step = action.find("Step")
                    if step is not None:
                        step_type = attr(step, "id")
                        sname = STEP_TYPES.get(step_type, f"Step#{step_type}")
                        parts.append(f"action:{sname}")
            # Script triggers on the button itself
            for trigger in lo.findall("ScriptTriggers/ScriptTrigger"):
                tref = trigger.find("ScriptReference")
                if tref is not None:
                    parts.append(f"TRIGGER:{attr(trigger, 'action')}->{attr(tref, 'name')}")
            if hide_calc:
                parts.append(f"hideWhen=({hide_calc[:80]})")
            output.append(f"{prefix}{' | '.join(parts)}")
            # Recurse for child objects
            for obj_list in lo.findall("ObjectList"):
                for child_lo in obj_list:
                    if child_lo.tag == "LayoutObject":
                        self._process_layout_object(child_lo, output, depth + 1, max_depth)
            return

        # --- BUTTON BAR ---
        if otype == "Button Bar":
            output.append(f"{prefix}BUTTONBAR{' | name:' + oname if oname else ''}")
            for obj_list in lo.findall("ObjectList"):
                for child_lo in obj_list:
                    if child_lo.tag == "LayoutObject":
                        self._process_layout_object(child_lo, output, depth + 1, max_depth)
            return

        # --- POPOVER BUTTON ---
        if otype == "Popover Button":
            parts = ["POPOVER"]
            if oname:
                parts.append(f'"{oname}"')
            sref = lo.find(".//ScriptReference")
            if sref is not None:
                parts.append(f"-> {attr(sref, 'name')}")
            if hide_calc:
                parts.append(f"hideWhen=({hide_calc[:80]})")
            output.append(f"{prefix}{' | '.join(parts)}")
            for obj_list in lo.findall(".//ObjectList"):
                for child_lo in obj_list:
                    if child_lo.tag == "LayoutObject":
                        self._process_layout_object(child_lo, output, depth + 1, max_depth)
            return

        # --- TAB CONTROL ---
        tab_ctrl = lo.find("TabControl")
        if otype == "Tab Control" or tab_ctrl is not None:
            tab_names = []
            panels = lo.findall(".//TabPanel") + lo.findall(".//Panel")
            for tp in panels:
                tn = attr(tp, "name") or "?"
                tab_names.append(tn)
            output.append(f"{prefix}TABCTRL tabs:[{','.join(tab_names)}]"
                          f"{' | name:' + oname if oname else ''}")
            for tp in panels:
                tn = attr(tp, "name") or "?"
                output.append(f"{prefix}  TAB: {tn}")
                for obj_list in tp.findall("ObjectList"):
                    for child_lo in obj_list:
                        if child_lo.tag == "LayoutObject":
                            self._process_layout_object(child_lo, output, depth + 2, max_depth)
            return

        # --- SLIDE CONTROL ---
        if otype == "Slide Control":
            output.append(f"{prefix}SLIDECTRL{' | name:' + oname if oname else ''}")
            for obj_list in lo.findall(".//ObjectList"):
                for child_lo in obj_list:
                    if child_lo.tag == "LayoutObject":
                        self._process_layout_object(child_lo, output, depth + 1, max_depth)
            return

        # --- WEB VIEWER ---
        if otype == "Web Viewer":
            url = ""
            wv_calc = lo.find(".//Calculation/Text")
            if wv_calc is not None:
                url = (wv_calc.text or "").strip()
            output.append(f"{prefix}WEBVIEWER{' | name:' + oname if oname else ''}"
                          f"{' | url=(' + url[:100] + ')' if url else ''}")
            return

        # --- EDIT BOX / FIELD ---
        field_el = lo.find("Field")
        if otype == "Edit Box" or field_el is not None:
            fr = self._get_field_ref(lo)
            if not fr:
                return
            fparts = [f"FIELD {fr}"]
            if oname:
                fparts.append(f"name:{oname}")
            # Script triggers
            for trigger in lo.findall("ScriptTriggers/ScriptTrigger"):
                tref = trigger.find("ScriptReference")
                if tref is not None:
                    ttype = attr(trigger, "type", "Trigger")
                    fparts.append(f"TRIGGER:{ttype}->{attr(tref, 'name')}")
            if hide_calc:
                fparts.append(f"hideWhen=({hide_calc[:80]})")
            output.append(f"{prefix}{' | '.join(fparts)}")
            return

        # --- DROP DOWN / POP-UP MENU / CHECKBOX / RADIO ---
        if otype in ("Drop Down List", "Pop-up Menu", "Checkbox Set", "Radio Button Set"):
            fr = self._get_field_ref(lo)
            if fr:
                fparts = [f"FIELD {fr} ({otype})"]
                if oname:
                    fparts.append(f"name:{oname}")
                for trigger in lo.findall("ScriptTriggers/ScriptTrigger"):
                    tref = trigger.find("ScriptReference")
                    if tref is not None:
                        fparts.append(f"TRIGGER:{attr(trigger, 'action')}->{attr(tref, 'name')}")
                if hide_calc:
                    fparts.append(f"hideWhen=({hide_calc[:80]})")
                output.append(f"{prefix}{' | '.join(fparts)}")
            return

        # --- TEXT (only if has trigger, merge field, or hide condition) ---
        if otype == "Text":
            has_trigger = lo.find("ScriptTriggers/ScriptTrigger") is not None
            merge_fr = self._get_field_ref(lo)
            if has_trigger or merge_fr or hide_calc:
                tparts = ["TEXT"]
                if oname:
                    tparts.append(f"name:{oname}")
                if merge_fr:
                    tparts.append(f"merge:{merge_fr}")
                for trigger in lo.findall("ScriptTriggers/ScriptTrigger"):
                    tref = trigger.find("ScriptReference")
                    if tref is not None:
                        tparts.append(f"TRIGGER:{attr(trigger, 'action')}->{attr(tref, 'name')}")
                if hide_calc:
                    tparts.append(f"hideWhen=({hide_calc[:80]})")
                output.append(f"{prefix}{' | '.join(tparts)}")
            return

        # --- GENERIC: anything else with a script, trigger, hide, or name ---
        sref = lo.find(".//ScriptReference")
        has_trigger = lo.find("ScriptTriggers/ScriptTrigger") is not None
        if sref is not None or has_trigger or hide_calc or (oname and otype):
            gparts = [f"OBJ:{otype}"]
            if oname:
                gparts.append(f"name:{oname}")
            if sref is not None:
                gparts.append(f"-> {attr(sref, 'name')}")
            for trigger in lo.findall("ScriptTriggers/ScriptTrigger"):
                tref = trigger.find("ScriptReference")
                if tref is not None:
                    gparts.append(f"TRIGGER:{attr(trigger, 'action')}->{attr(tref, 'name')}")
            if hide_calc:
                gparts.append(f"hideWhen=({hide_calc[:80]})")
            output.append(f"{prefix}{' | '.join(gparts)}")

        # Recurse into child ObjectLists
        for obj_list in lo.findall("ObjectList"):
            for child_lo in obj_list:
                if child_lo.tag == "LayoutObject":
                    self._process_layout_object(child_lo, output, depth + 1, max_depth)

    def _get_field_ref(self, lo):
        """Extract TO::FieldName from a LayoutObject's Field > FieldReference."""
        field_el = lo.find("Field")
        if field_el is None:
            field_el = lo
        fr = field_el.find("FieldReference")
        if fr is None:
            fr = field_el.find(".//FieldReference")
        if fr is None:
            return ""
        fname = attr(fr, "name")
        tor = fr.find("TableOccurrenceReference")
        to_name = attr(tor, "name") if tor is not None else ""
        if to_name and fname:
            return f"{to_name}::{fname}"
        return fname or ""

    # ============================================================
    # 05 VALUE LISTS
    # ============================================================
    def extract_valuelists(self):
        output = []
        output.append("=" * 70)
        output.append("FILEMAKER VALUE LISTS")
        output.append("=" * 70)
        output.append("")

        vlc = self._find_catalog("ValueListCatalog")
        if vlc is None:
            output.append("(No ValueListCatalog found)")
            return "\n".join(output)

        vl_count = 0
        for vl in vlc:
            name = attr(vl, "name")
            vid = attr(vl, "id")
            if not name:
                continue
            vl_count += 1
            vl_type = attr(vl, "type", "Custom")
            output.append(f"VL: {name} [id:{vid}] type:{vl_type}")

            # Field-based value lists
            fr = field_ref_str(vl)
            if fr:
                output.append(f"  source: {fr}")
        output.append("")

        self.stats["valuelists"] = vl_count
        return "\n".join(output)

    # ============================================================
    # 06 CUSTOM FUNCTIONS
    # ============================================================
    def extract_custom_functions(self):
        output = []
        output.append("=" * 70)
        output.append("FILEMAKER CUSTOM FUNCTIONS")
        output.append("=" * 70)
        output.append("")

        cfc = self._find_catalog("CustomFunctionsCatalog")
        if cfc is None:
            output.append("(No CustomFunctionsCatalog found)")
            return "\n".join(output)

        cf_count = 0
        for cf in cfc:
            name = attr(cf, "name")
            cfid = attr(cf, "id")
            if not name:
                continue
            cf_count += 1

            # Parameters
            params = []
            for p in cf.findall(".//Parameter"):
                pname = attr(p, "name")
                if pname:
                    params.append(pname)
            param_str = f"({', '.join(params)})" if params else "()"

            output.append(f"CF: {name}{param_str} [id:{cfid}]")

            # Calculation body
            calc = find_calc(cf)
            if calc:
                output.append(f"  = {calc[:300]}")
            output.append("")

        self.stats["custom_functions"] = cf_count
        return "\n".join(output)

    # ============================================================
    # 07 ACCOUNTS & PRIVILEGES
    # ============================================================
    def extract_accounts(self):
        output = []
        output.append("=" * 70)
        output.append("FILEMAKER ACCOUNTS & PRIVILEGES")
        output.append("=" * 70)
        output.append("")

        # Accounts might be in Metadata > AddAction
        search_locations = [self.add_action, self.modify_action, self.meta_add_action]

        acct_count = 0
        output.append("--- ACCOUNTS ---")
        for loc in search_locations:
            if loc is None:
                continue
            for cat in [loc.find("AccountsCatalog"), loc.find("AccountCatalog")]:
                if cat is None:
                    continue
                for acct in cat:
                    name = attr(acct, "name")
                    if not name:
                        continue
                    acct_count += 1
                    active = attr(acct, "active", "?")
                    output.append(f"  ACCT: {name} | {'Active' if active == 'True' else 'Inactive'}")

        output.append("")
        output.append("--- PRIVILEGE SETS ---")
        for loc in search_locations:
            if loc is None:
                continue
            for cat in [loc.find("PrivilegeSetsCatalog"), loc.find("PrivilegeSetCatalog")]:
                if cat is None:
                    continue
                for ps in cat:
                    name = attr(ps, "name")
                    if name:
                        output.append(f"  PRIVSET: {name}")

        output.append("")
        output.append("--- EXTENDED PRIVILEGES ---")
        for loc in search_locations:
            if loc is None:
                continue
            for cat in [loc.find("ExtendedPrivilegesCatalog"),
                        loc.find("ExtendedPrivilegeCatalog")]:
                if cat is None:
                    continue
                for ep in cat:
                    name = attr(ep, "name")
                    if name:
                        output.append(f"  EXT: {name}")

        self.stats["accounts"] = acct_count
        return "\n".join(output)

    # ============================================================
    # 10 SUMMARY
    # ============================================================
    def write_summary(self):
        output = []
        output.append("=" * 70)
        output.append("FILEMAKER SaXML SUMMARY")
        output.append("=" * 70)
        output.append("")
        output.append(f"Source: {self.xml_path}")
        output.append(f"File: {attr(self.root, 'File')}")
        output.append(f"FileMaker version: {attr(self.root, 'Source')}")
        output.append(f"SaXML format: {attr(self.root, 'version')}")
        output.append("")
        output.append("--- COUNTS ---")
        for k, v in sorted(self.stats.items()):
            output.append(f"  {k}: {v}")
        output.append("")
        output.append("--- OUTPUT FILES ---")
        output.append("  01_SCHEMA.txt       - Tables, fields, calculations")
        output.append("  02_RELATIONSHIPS.txt - Table occurrences and relationships")
        output.append("  03_SCRIPTS.txt      - Scripts with step details")
        output.append("  04_LAYOUTS.txt      - Layouts with objects")
        output.append("  05_VALUELISTS.txt   - Value list definitions")
        output.append("  06_CUSTOM_FUNCS.txt - Custom function definitions")
        output.append("  07_ACCOUNTS.txt     - Security: accounts, privileges")
        output.append("  10_SUMMARY.txt      - This file")
        return "\n".join(output)

    # ============================================================
    # RUN ALL
    # ============================================================
    def run(self):
        self.parse()

        extractors = [
            ("01_SCHEMA.txt", self.extract_schema),
            ("02_RELATIONSHIPS.txt", self.extract_relationships),
            ("03_SCRIPTS.txt", self.extract_scripts),
            ("04_LAYOUTS.txt", self.extract_layouts),
            ("05_VALUELISTS.txt", self.extract_valuelists),
            ("06_CUSTOM_FUNCS.txt", self.extract_custom_functions),
            ("07_ACCOUNTS.txt", self.extract_accounts),
        ]

        for filename, extractor in extractors:
            print(f"\nExtracting {filename}...")
            content = extractor()
            filepath = os.path.join(self.output_dir, filename)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"  Written: {filepath}")

        # Summary last (needs stats)
        summary = self.write_summary()
        filepath = os.path.join(self.output_dir, "10_SUMMARY.txt")
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(summary)
        print(f"\n  Written: {filepath}")

        print("\n" + "=" * 70)
        print("DONE!")
        print("=" * 70)
        for k, v in sorted(self.stats.items()):
            print(f"  {k}: {v}")


# ============================================================
# CLI
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compress FileMaker SaXML into Claude-friendly text files")
    parser.add_argument("xml_path", help="Path to SaXML file")
    parser.add_argument("--output-dir", "-o", help="Output directory (default: same as input)")
    args = parser.parse_args()

    output_dir = args.output_dir or os.path.dirname(os.path.abspath(args.xml_path))
    os.makedirs(output_dir, exist_ok=True)

    compressor = SaXMLCompressor(args.xml_path, output_dir)
    compressor.run()