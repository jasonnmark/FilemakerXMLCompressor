#!/usr/bin/env python3
"""
FileMaker DDR XML Compressor
=============================
Converts a FileMaker Database Design Report (DDR) XML file into compact,
Claude-friendly text files organized by category.

Output Format: A custom shorthand notation designed to be:
  - Maximally token-efficient for LLM consumption
  - Human-readable with a key/legend at the top of each file
  - Free of redundant data (no DisplayCalculation, no duplicate UUIDs, no styling)

Usage:
    python fm_ddr_compress.py <path_to_ddr_xml> [--output-dir <dir>]

Output Files:
    01_SCHEMA.txt       - Tables, fields, auto-enter, validation, storage
    02_RELATIONSHIPS.txt - Relationship graph with predicates
    03_SCRIPTS.txt      - All scripts with full step details
    04_LAYOUTS.txt      - Layout names, TOs, objects, triggers
    05_VALUELISTS.txt   - Value list definitions
    06_CUSTOM_FUNCS.txt - Custom function definitions
    07_ACCOUNTS.txt     - Accounts, privilege sets, extended privileges
    08_MENUS.txt        - Custom menus and menu sets
    09_FILE_REFS.txt    - External data source references
    10_SUMMARY.txt      - Overview stats and cross-reference index

Author: Generated for J's FileMaker development workflow
"""

import xml.etree.ElementTree as ET
import argparse
import os
import sys
import re
from collections import defaultdict
from datetime import datetime

# Try to import emoji restoration module (optional but recommended)
try:
    from fm_emoji_restore import restore_emoji, EMOJI_MAP
    HAS_EMOJI_RESTORE = True
except ImportError:
    HAS_EMOJI_RESTORE = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def attr(el, name, default=""):
    """Safely get an attribute from an element."""
    if el is None:
        return default
    return el.get(name, default)


def text(el, default=""):
    """Safely get text content from an element."""
    if el is None:
        return default
    return (el.text or default).strip()


def cdata_text(el):
    """Extract CDATA or text from a Calculation element."""
    if el is None:
        return ""
    return (el.text or "").strip()


def find_calc(parent, tag="Calculation"):
    """Find a Calculation element and return its text."""
    if parent is None:
        return ""
    calc = parent.find(tag)
    if calc is not None:
        return cdata_text(calc)
    return ""


def indent_text(txt, indent=4):
    """Indent multi-line text."""
    prefix = " " * indent
    return "\n".join(prefix + line for line in txt.split("\n"))


def safe_name(name):
    """Return a name safe for display, escaping pipes."""
    return name.replace("|", "\\|") if name else ""


# ---------------------------------------------------------------------------
# Parsers for each section
# ---------------------------------------------------------------------------

class DDRParser:
    def __init__(self, xml_path):
        self.xml_path = xml_path
        self.tree = None
        self.root = None
        self.file_node = None
        self.stats = {}

    def parse(self):
        """Parse the XML file. Uses iterparse for memory efficiency on large files."""
        print(f"Parsing {self.xml_path}...")
        print("(This may take a minute for large files...)")

        # First, try a quick check of file size
        file_size = os.path.getsize(self.xml_path)
        print(f"File size: {file_size / (1024*1024):.1f} MB")

        # For very large files, we use iterparse with targeted extraction
        # For smaller files, we can load the whole tree
        if file_size > 500 * 1024 * 1024:  # > 500MB
            print("WARNING: Very large file. This may use significant memory.")

        self.tree = ET.parse(self.xml_path)
        self.root = self.tree.getroot()

        # The main file node - DDR XML structure is:
        # <FMPReport><File>...</File></FMPReport>
        # or for the detail report:
        # <FMPReport type="Report"><File>...</File></FMPReport>
        self.file_node = self.root.find("File")
        if self.file_node is None:
            # Might be at root level in some versions
            self.file_node = self.root

        report_type = self.root.get("type", "")
        if report_type == "Summary":
            print("ERROR: This appears to be the Summary XML file.")
            print("Please provide the detail Report XML file instead.")
            print("(It's usually named like 'YourFile_fmp12.xml')")
            sys.exit(1)

        print("XML parsed successfully.")

        # --- DIAGNOSTIC: Find script step locations ---
        # Build index of script steps by ID for scripts whose steps
        # are not nested inside ScriptCatalog
        self.script_steps_by_id = {}
        catalog = self.file_node.find("ScriptCatalog")

        if catalog is not None:
            # Check if ANY script in the catalog has a StepList
            has_embedded_steps = False
            sample_script = None
            for script_el in catalog.iter("Script"):
                if sample_script is None:
                    sample_script = script_el
                    # Report what child elements a Script has
                    child_tags = [c.tag for c in script_el]
                    sid = script_el.get("id", "?")
                    sname = script_el.get("name", "?")
                    print(f"  Script sample '{sname}' [id:{sid}] children: {child_tags}")
                sl = script_el.find("StepList")
                if sl is not None and len(list(sl)) > 0:
                    has_embedded_steps = True
                    break

            if has_embedded_steps:
                print("  ✅ Script steps found embedded in ScriptCatalog.")
            else:
                print("  ⚠️  No embedded StepList found in ScriptCatalog scripts.")
                print("  Scanning full XML tree for Step/StepList elements...")

                # Search the ENTIRE XML tree for StepList or Step elements
                step_count = 0
                steplist_parents = set()
                for sl in self.root.iter("StepList"):
                    parent = None
                    # Find the parent Script element
                    # ElementTree doesn't have parent tracking, so search
                    step_count += len(list(sl))
                    # Track where we found it
                    steplist_parents.add("found")

                if step_count > 0:
                    print(f"  Found {step_count} steps in StepList elements elsewhere in XML.")
                    # Build parent map to find Script parents of StepLists
                    parent_map = {}
                    for parent in self.root.iter():
                        for child in parent:
                            parent_map[child] = parent

                    for sl in self.root.iter("StepList"):
                        if sl in parent_map:
                            p = parent_map[sl]
                            if p.tag == "Script":
                                sid = p.get("id", "")
                                if sid:
                                    self.script_steps_by_id[sid] = sl
                    print(f"  Indexed {len(self.script_steps_by_id)} scripts with steps.")
                else:
                    # Maybe steps are direct children of Script elements
                    # outside the catalog
                    for script_el in self.root.iter("Script"):
                        sid = script_el.get("id", "")
                        if not sid:
                            continue
                        # Check for Step children directly
                        steps = script_el.findall("Step")
                        if steps:
                            step_count += len(steps)
                            self.script_steps_by_id[sid] = script_el

                    if step_count > 0:
                        print(f"  Found {step_count} steps as direct children of Script elements.")
                        print(f"  Indexed {len(self.script_steps_by_id)} scripts.")
                    else:
                        print("  ❌ No script steps found anywhere in the XML.")
                        print("  The DDR export may not include script steps.")
                        print("  Try re-exporting: File > Manage > Database Design Report")
                        print("  and ensure all options are checked.")

    # --- SCHEMA (Tables & Fields) ---
    def extract_schema(self):
        """Extract base tables and their field definitions."""
        output = []
        output.append("=" * 70)
        output.append("FILEMAKER SCHEMA - Tables & Fields")
        output.append("=" * 70)
        output.append("")
        output.append("KEY:")
        output.append("  T: <table_name> (<record_count> records) [id:<id>]")
        output.append("  F: <name> | <field_type> | <data_type> [id:<id>]")
        output.append("     field_type: N=Normal, C=Calculated, S=Summary, X=Invalid")
        output.append("     data_type: T=Text, N=Number, D=Date, Ti=Time, TS=Timestamp, B=Binary")
        output.append("  CALC: <calculation formula>")
        output.append("  AE: auto-enter info (serial, creation/mod stamps, lookup, calc)")
        output.append("  VAL: validation rules")
        output.append("  STOR: storage (global, index, unstored, repetitions)")
        output.append("  CMT: field comment")
        output.append("  SUM: summary operation details")
        output.append("")

        ft_map = {"Normal": "N", "Calculated": "C", "Summary": "S", "Invalid": "X"}
        dt_map = {"Text": "T", "Number": "N", "Date": "D", "Time": "Ti",
                  "TimeStamp": "TS", "Binary": "B"}

        catalog = self.file_node.find("BaseTableCatalog")
        if catalog is None:
            output.append("(No tables found)")
            return "\n".join(output)

        tables = catalog.findall("BaseTable")
        self.stats["tables"] = len(tables)
        total_fields = 0

        for table in tables:
            tname = attr(table, "name")
            tid = attr(table, "id")
            records = attr(table, "records", "0")
            shadow = attr(table, "shadow", "False")

            header = f"T: {tname} ({records} records) [id:{tid}]"
            if shadow == "True":
                header += " [SHADOW]"
                ds = table.find("DataSourceRef")
                if ds is not None:
                    header += f" src:{attr(ds, 'name')}/{attr(ds, 'tableName')}"
            output.append(header)

            field_catalog = table.find("FieldCatalog")
            if field_catalog is None:
                output.append("  (no fields)")
                output.append("")
                continue

            fields = field_catalog.findall("Field")
            total_fields += len(fields)

            for field in fields:
                fname = attr(field, "name")
                fid = attr(field, "id")
                ftype = ft_map.get(attr(field, "fieldType"), "?")
                dtype = dt_map.get(attr(field, "dataType"), "?")

                line = f"  F: {fname} | {ftype} | {dtype} [id:{fid}]"
                output.append(line)

                # Calculation
                calc_el = field.find("Calculation")
                if calc_el is not None:
                    calc_text = cdata_text(calc_el)
                    if calc_text:
                        output.append(f"    CALC: {calc_text}")

                # Summary info
                summary = field.find("SummaryInfo")
                if summary is not None:
                    op = attr(summary, "operation")
                    sf = summary.find("SummaryField/Field")
                    sf_name = attr(sf, "name") if sf is not None else "?"
                    sum_line = f"    SUM: {op} of {sf_name}"
                    restart = attr(summary, "restartForEachSortedGroup")
                    if restart == "True":
                        sum_line += " (restart each group)"
                    output.append(sum_line)

                # Auto-enter
                ae = field.find("AutoEnter")
                if ae is not None:
                    ae_parts = []
                    ae_val = attr(ae, "value")
                    if ae_val:
                        ae_parts.append(ae_val)

                    serial = ae.find("Serial")
                    if serial is not None:
                        gen = attr(serial, "generate")
                        nxt = attr(serial, "nextValue")
                        inc = attr(serial, "increment")
                        ae_parts.append(f"Serial({gen},next:{nxt},inc:{inc})")

                    if attr(ae, "calculation") == "True":
                        ae_calc = find_calc(ae)
                        if ae_calc:
                            ae_parts.append(f"Calc={ae_calc}")

                    if attr(ae, "lookup") == "True":
                        lk = ae.find("Lookup")
                        if lk is not None:
                            lk_field = lk.find("Field")
                            lk_table = lk.find("Table")
                            lk_name = attr(lk_field, "name") if lk_field is not None else "?"
                            lk_tbl = attr(lk_field, "table") if lk_field is not None else ""
                            ae_parts.append(f"Lookup={lk_tbl}::{lk_name}")

                    const = ae.find("ConstantData")
                    if const is not None and const.text:
                        ae_parts.append(f"Const=\"{const.text.strip()}\"")

                    allow_edit = attr(ae, "allowEditing")
                    if allow_edit == "False":
                        ae_parts.append("noEdit")

                    if ae_parts:
                        output.append(f"    AE: {', '.join(ae_parts)}")

                # Validation
                val = field.find("Validation")
                if val is not None:
                    val_parts = []
                    vtype = attr(val, "type")
                    if vtype:
                        val_parts.append(f"when:{vtype}")

                    strict = val.find("StrictDataType")
                    if strict is not None:
                        val_parts.append(f"strict:{attr(strict, 'value')}")

                    ne = val.find("NotEmpty")
                    if ne is not None and attr(ne, "value") == "True":
                        val_parts.append("notEmpty")

                    uniq = val.find("Unique")
                    if uniq is not None and attr(uniq, "value") == "True":
                        val_parts.append("unique")

                    exist = val.find("Existing")
                    if exist is not None and attr(exist, "value") == "True":
                        val_parts.append("existing")

                    rng = val.find("Range")
                    if rng is not None:
                        val_parts.append(f"range:{attr(rng,'from')}-{attr(rng,'to')}")

                    if attr(val, "calculation") == "True":
                        vc = find_calc(val)
                        if vc:
                            val_parts.append(f"calc={vc}")

                    if attr(val, "valuelist") == "True":
                        vl = val.find("ValueList")
                        if vl is not None:
                            val_parts.append(f"valueList:{attr(vl,'name')}")

                    maxlen = val.find("MaxDataLength")
                    if maxlen is not None:
                        val_parts.append(f"maxLen:{attr(maxlen,'value')}")

                    if attr(val, "message") == "True":
                        msg = val.find("ErrorMessage")
                        if msg is not None and msg.text:
                            val_parts.append(f"msg=\"{msg.text.strip()[:80]}\"")

                    if val_parts:
                        output.append(f"    VAL: {', '.join(val_parts)}")

                # Storage
                stor = field.find("Storage")
                if stor is not None:
                    stor_parts = []
                    if attr(stor, "global") == "True":
                        stor_parts.append("GLOBAL")
                    idx = attr(stor, "index")
                    if idx and idx != "None":
                        stor_parts.append(f"idx:{idx}")
                    if attr(stor, "storeCalculationResults") == "False":
                        stor_parts.append("UNSTORED")
                    maxrep = attr(stor, "maxRepetition")
                    if maxrep and maxrep != "1":
                        stor_parts.append(f"reps:{maxrep}")
                    if stor_parts:
                        output.append(f"    STOR: {', '.join(stor_parts)}")

                # Comment
                comment = field.find("Comment")
                if comment is not None and comment.text and comment.text.strip():
                    cmt = comment.text.strip().replace("\n", " | ")[:200]
                    output.append(f"    CMT: {cmt}")

            output.append("")

        self.stats["fields"] = total_fields
        return "\n".join(output)

    # --- RELATIONSHIPS ---
    def extract_relationships(self):
        """Extract the relationship graph."""
        output = []
        output.append("=" * 70)
        output.append("FILEMAKER RELATIONSHIPS")
        output.append("=" * 70)
        output.append("")
        output.append("KEY:")
        output.append("  TABLE OCCURRENCES (TOs):")
        output.append("    TO: <name> -> base:<base_table> [id:<id>]")
        output.append("  RELATIONSHIPS:")
        output.append("    REL[id]: <LeftTO> <=> <RightTO>")
        output.append("    PRED: <leftField> <op> <rightField>")
        output.append("    ops: == != < <= > >= x(cartesian)")
        output.append("    FLAGS: delCascade, createCascade, sortLeft, sortRight")
        output.append("")

        rg = self.file_node.find("RelationshipGraph")
        if rg is None:
            output.append("(No relationships found)")
            return "\n".join(output)

        # Table Occurrences
        to_list = rg.find("TableList")
        if to_list is not None:
            output.append("--- TABLE OCCURRENCES ---")
            tos = to_list.findall("Table")
            self.stats["table_occurrences"] = len(tos)
            for to in tos:
                name = attr(to, "name")
                tid = attr(to, "id")
                bt = to.find("BaseTable")
                bt_name = attr(bt, "name") if bt is not None else "?"
                line = f"  TO: {name} -> base:{bt_name} [id:{tid}]"
                output.append(line)
            output.append("")

        # Relationships
        rel_list = rg.find("RelationshipList")
        if rel_list is not None:
            output.append("--- RELATIONSHIPS ---")
            rels = rel_list.findall("Relationship")
            self.stats["relationships"] = len(rels)
            for rel in rels:
                rid = attr(rel, "id")

                lt = rel.find("LeftTable")
                rt = rel.find("RightTable")
                lt_name = attr(lt, "name") if lt is not None else "?"
                rt_name = attr(rt, "name") if rt is not None else "?"

                flags = []
                if lt is not None:
                    if attr(lt, "cascadeDelete") == "True":
                        flags.append("delL")
                    if attr(lt, "cascadeCreate") == "True":
                        flags.append("createL")
                if rt is not None:
                    if attr(rt, "cascadeDelete") == "True":
                        flags.append("delR")
                    if attr(rt, "cascadeCreate") == "True":
                        flags.append("createR")

                flag_str = f" [{','.join(flags)}]" if flags else ""
                output.append(f"  REL[{rid}]: {lt_name} <=> {rt_name}{flag_str}")

                # Sort lists
                for side, node in [("L", lt), ("R", rt)]:
                    if node is not None:
                        sl = node.find("SortList")
                        if sl is not None and attr(sl, "value") == "True":
                            sorts = sl.findall("Sort")
                            sort_strs = []
                            for s in sorts:
                                sf = s.find("Field")
                                sname = attr(sf, "name") if sf is not None else "?"
                                sord = attr(s, "type", "Ascending")
                                sort_strs.append(f"{sname} {'↑' if sord == 'Ascending' else '↓'}")
                            if sort_strs:
                                output.append(f"    SORT-{side}: {', '.join(sort_strs)}")

                # Predicates (join conditions)
                jps = rel.find("JoinPredicateList")
                if jps is not None:
                    for jp in jps.findall("JoinPredicate"):
                        op_type = attr(jp, "type", "Equal")
                        op_map = {"Equal": "==", "NotEqual": "!=",
                                  "LessThan": "<", "LessThanOrEqual": "<=",
                                  "GreaterThan": ">", "GreaterThanOrEqual": ">=",
                                  "Cartesian": "x"}
                        op = op_map.get(op_type, op_type)

                        lf = jp.find("LeftField/Field")
                        rf = jp.find("RightField/Field")
                        lf_name = attr(lf, "name") if lf is not None else "?"
                        rf_name = attr(rf, "name") if rf is not None else "?"
                        output.append(f"    PRED: {lf_name} {op} {rf_name}")

                output.append("")

        return "\n".join(output)

    # --- SCRIPTS ---
    def extract_scripts(self):
        """Extract all scripts with their step details."""
        output = []
        output.append("=" * 70)
        output.append("FILEMAKER SCRIPTS")
        output.append("=" * 70)
        output.append("")
        output.append("KEY:")
        output.append("  FOLDER: <name> [id:<id>]")
        output.append("  SCRIPT: <name> [id:<id>]")
        output.append("    Steps are numbered and indented by nesting level.")
        output.append("    Step format: <step#>. <StepName> [enabled/disabled] <parameters>")
        output.append("    Calculations shown inline after =>")
        output.append("    Comments shown with //")
        output.append("")

        catalog = self.file_node.find("ScriptCatalog")
        if catalog is None:
            output.append("(No scripts found)")
            return "\n".join(output)

        script_count = 0
        step_total = 0

        def process_script_item(item, depth=0):
            nonlocal script_count, step_total
            prefix = "  " * depth
            tag = item.tag

            if tag == "Group":
                name = attr(item, "name")
                gid = attr(item, "id")
                output.append(f"{prefix}FOLDER: {name} [id:{gid}]")
                for child in item:
                    process_script_item(child, depth + 1)
                output.append("")
            elif tag == "Script":
                script_count += 1
                name = attr(item, "name")
                sid = attr(item, "id")
                include = attr(item, "includeInMenu")
                run_full = attr(item, "runFullAccess")

                flags = []
                if include == "True":
                    flags.append("inMenu")
                if run_full == "True":
                    flags.append("fullAccess")
                flag_str = f" ({','.join(flags)})" if flags else ""

                output.append(f"{prefix}SCRIPT: {name} [id:{sid}]{flag_str}")

                # Script triggers at script level
                triggers = item.find("ScriptTriggers")
                if triggers is not None:
                    for trig in triggers:
                        trig_script = trig.find("Script") or trig.find(".//Script")
                        if trig_script is not None:
                            output.append(f"{prefix}  TRIGGER: {trig.tag} -> {attr(trig_script, 'name')}")

                step_list = item.find("StepList")

                # If no embedded StepList, check the step index
                if (step_list is None or len(list(step_list)) == 0) and hasattr(self, 'script_steps_by_id'):
                    indexed = self.script_steps_by_id.get(sid)
                    if indexed is not None:
                        if indexed.tag == "StepList":
                            step_list = indexed
                        else:
                            # Steps are direct children of a Script element
                            step_list = indexed

                if step_list is not None:
                    # Steps could be in a StepList or direct children
                    steps = step_list.findall("Step")
                    line_num = 0
                    for step in steps:
                        line_num += 1
                        step_text = self._format_step(step, depth + 1, line_num)
                        if step_text:
                            output.append(step_text)
                            step_total += 1

                output.append("")
            elif tag == "Separator":
                output.append(f"{prefix}---")

        for child in catalog:
            process_script_item(child, 0)

        self.stats["scripts"] = script_count
        print(f"  Extracted {script_count} scripts, {step_total} total steps.")
        return "\n".join(output)

    def _format_step(self, step, depth, line_num):
        """Format a single script step into compact notation."""
        prefix = "  " * depth
        step_id = attr(step, "id")
        enable = attr(step, "enable", "True")

        # Step name can be in different locations depending on DDR version
        name_el = step.find("StepName")
        if name_el is None:
            name_el = step.find("Name")
        if name_el is None:
            # Could be an attribute
            step_name = step.get("name", "")
        else:
            step_name = text(name_el)

        if not step_name:
            # Last resort: use the step id or tag
            step_name = f"Step#{step_id}" if step_id else "UnknownStep"

        disabled = " [DISABLED]" if enable == "False" else ""

        # Build parameter string based on step type
        params = self._extract_step_params(step, step_name)

        if params:
            return f"{prefix}{line_num}. {step_name}{disabled} {params}"
        else:
            return f"{prefix}{line_num}. {step_name}{disabled}"

    def _extract_step_params(self, step, step_name):
        """Extract the meaningful parameters from a script step."""
        parts = []

        # Comments get special treatment
        if step_name == "Comment":
            comment_el = step.find("Text")
            if comment_el is not None and comment_el.text:
                return f"// {comment_el.text.strip()}"
            return "// (empty)"

        # Set Variable
        if step_name == "Set Variable":
            name_el = step.find("Name")
            val_el = step.find("Value")
            rep_el = step.find("Repetition")
            if name_el is not None:
                var_name = text(name_el)
                val_calc = find_calc(val_el) if val_el is not None else ""
                rep_calc = find_calc(rep_el) if rep_el is not None else ""
                result = f"{var_name} = {val_calc}" if val_calc else var_name
                if rep_calc and rep_calc != "1":
                    result += f" [rep:{rep_calc}]"
                return result

        # Set Field / Set Field By Name
        if step_name in ("Set Field", "Set Field By Name"):
            field_el = step.find(".//Field")
            calc_el = step.find("Calculation")
            if field_el is not None:
                tbl = attr(field_el, "table")
                fname = attr(field_el, "name")
                calc = cdata_text(calc_el) if calc_el is not None else ""
                return f"{tbl}::{fname} = {calc}" if calc else f"{tbl}::{fname}"
            elif calc_el is not None:
                return f"=> {cdata_text(calc_el)}"

        # If / Else If
        if step_name in ("If", "Else If"):
            calc_el = step.find("Calculation")
            if calc_el is not None:
                return f"({cdata_text(calc_el)})"

        # Loop / Exit Loop If
        if step_name == "Exit Loop If":
            calc_el = step.find("Calculation")
            if calc_el is not None:
                return f"({cdata_text(calc_el)})"

        # Go to Layout
        if step_name == "Go to Layout":
            layout_ref = step.find("Layout")
            if layout_ref is not None:
                return f'"{attr(layout_ref, "name")}"'
            # Could be by calculation
            calc_el = step.find("Calculation")
            if calc_el is not None:
                return f"=> {cdata_text(calc_el)}"

        # Perform Script
        if step_name in ("Perform Script", "Perform Script on Server"):
            script_ref = step.find("Script") or step.find(".//Script")
            param_el = step.find("Calculation")
            if script_ref is not None:
                sname = attr(script_ref, "name")
                param = cdata_text(param_el) if param_el is not None else ""
                file_ref = step.find("FileReference")
                file_str = f" @{attr(file_ref, 'name')}" if file_ref is not None else ""
                if param:
                    return f'"{sname}"{file_str} param=({param})'
                return f'"{sname}"{file_str}'

        # Show Custom Dialog
        if step_name == "Show Custom Dialog":
            title_el = step.find("Title")
            msg_el = step.find("Message")
            title = find_calc(title_el) if title_el is not None else ""
            msg = find_calc(msg_el) if msg_el is not None else ""
            buttons = step.findall(".//Button")
            btn_strs = []
            for b in buttons:
                btn_calc = find_calc(b)
                if btn_calc:
                    btn_strs.append(btn_calc)
            result = ""
            if title:
                result += f"title=({title})"
            if msg:
                result += f" msg=({msg})"
            if btn_strs:
                result += f" btns=[{','.join(btn_strs)}]"
            return result.strip()

        # Insert from URL / various URL steps
        if step_name == "Insert from URL":
            target = step.find(".//Field")
            url_el = step.find("URL") or step.find("Calculation")
            target_str = ""
            if target is not None:
                target_str = f"{attr(target, 'table')}::{attr(target, 'name')}"
            url_str = ""
            if url_el is not None:
                url_str = cdata_text(url_el) or find_calc(url_el)
            return f"target={target_str} url=({url_str})" if target_str else f"url=({url_str})"

        # New Record / Delete Record / Duplicate Record - simple, no params usually
        if step_name in ("New Record/Request", "Delete Record/Request",
                         "Duplicate Record/Request", "Delete All Records",
                         "Open Record/Request", "Commit Records/Requests",
                         "Revert Record/Request"):
            no_dialog = step.find("NoInteract")
            if no_dialog is not None and attr(no_dialog, "state") == "True":
                return "[no dialog]"
            return ""

        # Sort Records
        if step_name == "Sort Records":
            restore = step.find("Restore")
            if restore is not None and attr(restore, "state") == "True":
                sort_list = step.find("SortList")
                if sort_list is not None:
                    sorts = []
                    for s in sort_list.findall("Sort"):
                        sf = s.find("Field")
                        if sf is not None:
                            sord = attr(s, "type", "Ascending")
                            arrow = "↑" if sord == "Ascending" else "↓"
                            sorts.append(f"{attr(sf, 'table')}::{attr(sf, 'name')}{arrow}")
                    if sorts:
                        return f"[{', '.join(sorts)}]"
            return ""

        # Perform Find
        if step_name == "Perform Find":
            restore = step.find("Restore")
            if restore is not None and attr(restore, "state") == "True":
                requests = step.findall(".//FindRequest")
                req_strs = []
                for req in requests:
                    omit = attr(req, "type") == "Exclude"
                    criteria = []
                    for fc in req.findall("FindCriterion"):
                        field = fc.find("Field")
                        val = fc.find("Text")
                        if field is not None:
                            fname = f"{attr(field, 'table')}::{attr(field, 'name')}"
                            fval = text(val) if val is not None else ""
                            criteria.append(f"{fname}={fval}")
                    prefix = "OMIT " if omit else ""
                    req_strs.append(f"{prefix}{{{', '.join(criteria)}}}")
                if req_strs:
                    return " ".join(req_strs)
            return ""

        # Go to Record
        if step_name in ("Go to Record/Request/Page"):
            calc_el = step.find("Calculation")
            opt_el = step.find("Option")
            if opt_el is not None:
                return attr(opt_el, "value", "")
            if calc_el is not None:
                return f"=> {cdata_text(calc_el)}"

        # Generic: Try to extract any Calculation, Field, or Option
        # This catches many other steps
        calc_el = step.find("Calculation")
        field_el = step.find(".//Field")
        option_el = step.find("Option")

        if calc_el is not None and cdata_text(calc_el):
            return f"=> {cdata_text(calc_el)}"
        if field_el is not None:
            tbl = attr(field_el, "table")
            fn = attr(field_el, "name")
            if tbl:
                return f"{tbl}::{fn}"
            return fn

        # StepText as fallback (but only first line to save space)
        step_text_el = step.find("StepText")
        if step_text_el is not None and step_text_el.text:
            st = step_text_el.text.strip()
            if st and len(st) < 200:
                # Only use if it adds info beyond the step name
                if st.lower() != step_name.lower():
                    return st

        return ""

    # --- LAYOUTS ---
    def extract_layouts(self):
        """Extract layout definitions."""
        output = []
        output.append("=" * 70)
        output.append("FILEMAKER LAYOUTS")
        output.append("=" * 70)
        output.append("")
        output.append("KEY:")
        output.append("  FOLDER: <name>")
        output.append("  LAYOUT: <name> [id:<id>] TO:<table_occurrence>")
        output.append("    TRIGGER: <trigger_type> -> <script_name>")
        output.append("    FIELD <TO::FieldName> | name:<obj_name> | TRIGGER:...")
        output.append("    BUTTON \"label\" | -> <script_name> | param=(...) | icon:<id>")
        output.append("    PORTAL showFrom:<TO> | filter=(...) | sort=[...]")
        output.append("      (portal child objects indented below)")
        output.append("    TABCTRL tabs:[Tab1,Tab2,...] | TAB: <n> + children")
        output.append("    POPOVER | WEBVIEWER | CHART | SLIDECTRL | BUTTONBAR")
        output.append("    TEXT (only if has trigger or merge field)")
        output.append("")

        catalog = self.file_node.find("LayoutCatalog")
        if catalog is None:
            output.append("(No layouts found)")
            return "\n".join(output)

        layout_count = 0

        def process_layout_item(item, depth=0):
            nonlocal layout_count
            prefix = "  " * depth
            tag = item.tag

            if tag == "Group":
                name = attr(item, "name")
                output.append(f"{prefix}FOLDER: {name}")
                for child in item:
                    process_layout_item(child, depth + 1)
            elif tag == "Layout":
                layout_count += 1
                name = attr(item, "name")
                lid = attr(item, "id")
                table_el = item.find("Table")
                to_name = attr(table_el, "name") if table_el is not None else "?"

                output.append(f"{prefix}LAYOUT: {name} [id:{lid}] TO:{to_name}")

                # Layout triggers
                triggers = item.find("ScriptTriggers")
                if triggers is not None:
                    for trig in triggers:
                        ts = trig.find("Script") or trig.find(".//Script")
                        if ts is not None:
                            output.append(f"{prefix}  TRIGGER: {trig.tag} -> {attr(ts, 'name')}")

                # Layout parts and objects
                self._extract_layout_objects(item, output, depth + 1)
                output.append("")
            elif tag == "Separator":
                pass  # Skip separators

        for child in catalog:
            process_layout_item(child, 0)

        self.stats["layouts"] = layout_count
        # Count objects in output
        obj_count = sum(1 for line in output if any(
            line.strip().startswith(t) for t in
            ("FIELD", "BUTTON", "PORTAL", "TABCTRL", "POPOVER",
             "WEBVIEWER", "CHART", "SLIDECTRL", "BUTTONBAR", "TEXT", "OBJ:")))
        print(f"  Extracted {layout_count} layouts, {obj_count} objects.")
        return "\n".join(output)

    def _extract_layout_objects(self, layout_el, output, depth):
        """Extract objects from a layout, focusing on functional elements."""
        prefix = "  " * depth

        # Parts — objects are often nested INSIDE parts, not siblings
        part_list = layout_el.find("PartList")
        if part_list is not None:
            for part in part_list.findall("Part"):
                ptype = attr(part, "type")
                # Look for objects inside each part
                obj_list = part.find("ObjectList")
                if obj_list is not None:
                    self._process_objects(obj_list, output, depth)

        # Also check for objects directly under layout (some DDR versions)
        obj_list = layout_el.find("ObjectList") or layout_el.find("Object")
        if obj_list is not None:
            self._process_objects(obj_list, output, depth)

        # Fallback: search anywhere under the layout for objects
        # (handles unexpected nesting)
        if not any("OBJ:" in line for line in output[-20:] if isinstance(line, str)):
            for obj in layout_el.iter("Object"):
                otype = attr(obj, "type")
                if otype and otype not in ("Line", "Rectangle", "RoundedRect", "Oval"):
                    self._process_single_object(obj, output, depth)

    def _process_objects(self, parent, output, depth, max_depth=8):
        """Recursively process layout objects, extracting functional info."""
        if depth > max_depth + 4:
            return

        for obj in parent:
            if obj.tag == "Object":
                self._process_single_object(obj, output, depth, max_depth)
            elif obj.tag == "ObjectList":
                self._process_objects(obj, output, depth, max_depth)

    def _process_single_object(self, obj, output, depth, max_depth=8):
        """Process a single layout object and extract functional info."""
        prefix = "  " * depth
        otype = attr(obj, "type")
        oname = attr(obj, "name")

        # Skip purely decorative objects
        if otype in ("Line", "Rectangle", "RoundedRect", "Oval"):
            return

        parts = []

        # --- Field reference ---
        field_el = (obj.find(".//FieldObj/Field") or obj.find(".//Field")
                    or obj.find("FieldObj/Field"))
        field_ref = ""
        if field_el is not None:
            ftbl = attr(field_el, "table")
            fname = attr(field_el, "name")
            if ftbl and fname:
                field_ref = f"{ftbl}::{fname}"

        # --- Portal ---
        portal = obj.find("PortalObj") or obj.find("Portal")
        if portal is not None:
            ptable = portal.find("Table")
            portal_to = attr(ptable, "name") if ptable is not None else "?"
            portal_parts = [f"PORTAL showFrom:{portal_to}"]
            if oname:
                portal_parts.append(f"name:{oname}")
            # Portal filter
            pfilter = portal.find("Filter")
            if pfilter is not None:
                fc = find_calc(pfilter)
                if fc:
                    portal_parts.append(f"filter=({fc[:120]})")
            # Portal sort
            psort = portal.find("SortList")
            if psort is not None:
                sorts = []
                for s in psort.findall("Sort"):
                    sf = s.find("Field")
                    if sf is not None:
                        sorts.append(f"{attr(sf, 'table')}::{attr(sf, 'name')}")
                if sorts:
                    portal_parts.append(f"sort=[{','.join(sorts)}]")
            # Portal row count
            initial_rows = portal.get("initialRows", "")
            if initial_rows:
                portal_parts.append(f"rows:{initial_rows}")
            output.append(f"{prefix}{' | '.join(portal_parts)}")
            # Recurse into portal child objects (fields inside portal)
            child_obj_list = obj.find("ObjectList")
            if child_obj_list is not None:
                self._process_objects(child_obj_list, output, depth + 1, max_depth)
            return

        # --- Button / ButtonBar / PopoverButton ---
        btn = obj.find("ButtonObj") or obj.find("Button")
        btn_bar = obj.find("ButtonBarObj")
        popover = obj.find("PopoverButtonObj")

        if btn is not None or otype == "Button":
            btn_el = btn if btn is not None else obj
            btn_parts = ["BUTTON"]
            if oname:
                btn_parts.append(f'"{oname}"')
            # Button label/text
            btn_text_el = obj.find(".//Text/ParagraphList//Data")
            if btn_text_el is None:
                btn_text_el = obj.find(".//Text")
            if btn_text_el is not None and btn_text_el.text:
                label = btn_text_el.text.strip()[:60]
                if label:
                    btn_parts.append(f'label:"{label}"')
            # Script reference
            script_ref = btn_el.find(".//Script") or btn_el.find("Script")
            if script_ref is not None:
                btn_parts.append(f"-> {attr(script_ref, 'name')}")
                # Script parameter
                param_el = btn_el.find(".//Calculation")
                if param_el is not None:
                    pc = cdata_text(param_el) or find_calc(param_el)
                    if pc:
                        btn_parts.append(f"param=({pc[:100]})")
            # Single-step action
            step = btn_el.find(".//Step") or btn_el.find("Step")
            if step is not None and script_ref is None:
                sn = step.find("StepName")
                if sn is not None:
                    btn_parts.append(f"action:{text(sn)}")
            # Icon
            icon_el = obj.find(".//IconID") or obj.find("IconID")
            if icon_el is not None and icon_el.text:
                btn_parts.append(f"icon:{icon_el.text.strip()}")
            output.append(f"{prefix}{' | '.join(btn_parts)}")
            # Recurse into child objects
            child_obj_list = obj.find("ObjectList")
            if child_obj_list is not None:
                self._process_objects(child_obj_list, output, depth + 1, max_depth)
            return

        if btn_bar is not None or otype == "ButtonBar":
            output.append(f"{prefix}BUTTONBAR{' | name:' + oname if oname else ''}")
            # Each segment of the button bar
            for seg in obj.iter("Object"):
                if seg is not obj:
                    self._process_single_object(seg, output, depth + 1, max_depth)
            return

        if popover is not None or otype == "PopoverButton":
            pop_parts = ["POPOVER"]
            if oname:
                pop_parts.append(f'"{oname}"')
            # Popover button script
            script_ref = obj.find(".//Script")
            if script_ref is not None:
                pop_parts.append(f"-> {attr(script_ref, 'name')}")
            output.append(f"{prefix}{' | '.join(pop_parts)}")
            child_obj_list = obj.find("ObjectList")
            if child_obj_list is not None:
                self._process_objects(child_obj_list, output, depth + 1, max_depth)
            return

        # --- Tab Control ---
        tab_ctrl = obj.find("TabControlObj")
        if tab_ctrl is not None or otype == "TabControl":
            tabs = obj.findall(".//TabPanel") or obj.findall(".//Tab")
            tab_names = [attr(t, "name") or f"Tab{i+1}" for i, t in enumerate(tabs)]
            output.append(f"{prefix}TABCTRL tabs:[{','.join(tab_names)}]"
                          f"{' | name:' + oname if oname else ''}")
            # Recurse into tab panels
            for tab_panel in tabs:
                tname = attr(tab_panel, "name") or "?"
                child_obj_list = tab_panel.find("ObjectList")
                if child_obj_list is not None:
                    output.append(f"{prefix}  TAB: {tname}")
                    self._process_objects(child_obj_list, output, depth + 2, max_depth)
            return

        # --- Slide Control ---
        slide_ctrl = obj.find("SlideControlObj")
        if slide_ctrl is not None or otype == "SlideControl":
            output.append(f"{prefix}SLIDECTRL{' | name:' + oname if oname else ''}")
            child_obj_list = obj.find("ObjectList")
            if child_obj_list is not None:
                self._process_objects(child_obj_list, output, depth + 1, max_depth)
            return

        # --- Web Viewer ---
        wv = obj.find("WebViewerObj")
        if wv is not None or otype == "WebViewer":
            url_calc = find_calc(wv) if wv is not None else ""
            output.append(f"{prefix}WEBVIEWER"
                          f"{' | name:' + oname if oname else ''}"
                          f"{' | url=(' + url_calc[:100] + ')' if url_calc else ''}")
            return

        # --- Chart ---
        if otype == "Chart":
            output.append(f"{prefix}CHART{' | name:' + oname if oname else ''}")
            return

        # --- Field (standalone, not in portal/button) ---
        if field_ref:
            fparts = [f"FIELD {field_ref}"]
            if oname:
                fparts.append(f"name:{oname}")
            # Object trigger
            triggers = obj.find("ScriptTriggers")
            if triggers is not None:
                for trig in triggers:
                    ts = trig.find("Script") or trig.find(".//Script")
                    if ts is not None:
                        fparts.append(f"TRIGGER:{trig.tag}->{attr(ts, 'name')}")
            # Conditional visibility
            cond_vis = obj.find("ConditionalVisibility")
            if cond_vis is not None:
                cv_calc = find_calc(cond_vis)
                if cv_calc:
                    fparts.append(f"hideWhen=({cv_calc[:80]})")
            output.append(f"{prefix}{' | '.join(fparts)}")
            # Recurse
            child_obj_list = obj.find("ObjectList")
            if child_obj_list is not None:
                self._process_objects(child_obj_list, output, depth + 1, max_depth)
            return

        # --- Text object (only if it has a trigger or merge field) ---
        if otype == "Text":
            has_trigger = obj.find("ScriptTriggers") is not None
            merge = obj.find(".//MergeField") or obj.find(".//Field")
            if has_trigger or merge:
                tparts = ["TEXT"]
                if oname:
                    tparts.append(f"name:{oname}")
                if merge is not None:
                    tparts.append(f"merge:{attr(merge, 'table')}::{attr(merge, 'name')}")
                triggers = obj.find("ScriptTriggers")
                if triggers is not None:
                    for trig in triggers:
                        ts = trig.find("Script") or trig.find(".//Script")
                        if ts is not None:
                            tparts.append(f"TRIGGER:{trig.tag}->{attr(ts, 'name')}")
                output.append(f"{prefix}{' | '.join(tparts)}")
            return

        # --- Generic: anything else with a trigger or conditional visibility ---
        if otype and otype not in ("Text", "Group"):
            has_trigger = obj.find("ScriptTriggers") is not None
            cond_vis = obj.find("ConditionalVisibility")
            if has_trigger or cond_vis is not None or oname:
                gparts = [f"OBJ:{otype}"]
                if oname:
                    gparts.append(f"name:{oname}")
                if has_trigger:
                    triggers = obj.find("ScriptTriggers")
                    for trig in triggers:
                        ts = trig.find("Script") or trig.find(".//Script")
                        if ts is not None:
                            gparts.append(f"TRIGGER:{trig.tag}->{attr(ts, 'name')}")
                if cond_vis is not None:
                    cv_calc = find_calc(cond_vis)
                    if cv_calc:
                        gparts.append(f"hideWhen=({cv_calc[:80]})")
                output.append(f"{prefix}{' | '.join(gparts)}")

        # Recurse
        child_obj_list = obj.find("ObjectList")
        if child_obj_list is not None:
            self._process_objects(child_obj_list, output, depth + 1, max_depth)

    # --- VALUE LISTS ---
    def extract_valuelists(self):
        """Extract value list definitions."""
        output = []
        output.append("=" * 70)
        output.append("FILEMAKER VALUE LISTS")
        output.append("=" * 70)
        output.append("")
        output.append("KEY:")
        output.append("  VL: <name> [id:<id>] type:<Custom|Field|External>")
        output.append("    For Custom: values listed")
        output.append("    For Field: source field and optional 2nd field")
        output.append("")

        catalog = self.file_node.find("ValueListCatalog")
        if catalog is None:
            output.append("(No value lists found)")
            return "\n".join(output)

        vls = catalog.findall("ValueList")
        self.stats["valuelists"] = len(vls)

        for vl in vls:
            name = attr(vl, "name")
            vid = attr(vl, "id")

            # Custom values
            custom = vl.find("CustomValues")
            if custom is not None:
                vals = (custom.text or "").strip()
                output.append(f"VL: {name} [id:{vid}] type:Custom")
                if vals:
                    # Truncate very long lists
                    lines = vals.split("\n")
                    if len(lines) > 20:
                        shown = "\n".join(lines[:20])
                        output.append(f"  {shown}")
                        output.append(f"  ... ({len(lines) - 20} more values)")
                    else:
                        output.append(f"  {vals}")
            else:
                # Field-based value list
                source = vl.find("Source")
                if source is not None:
                    src_type = attr(source, "type", "Field")
                    output.append(f"VL: {name} [id:{vid}] type:{src_type}")

                    field1 = source.find("Field")
                    if field1 is not None:
                        output.append(f"  field1: {attr(field1, 'table')}::{attr(field1, 'name')}")

                    field2 = source.find("SecondField") or source.find("Field2")
                    if field2 is not None:
                        f2 = field2.find("Field")
                        if f2 is not None:
                            output.append(f"  field2: {attr(f2, 'table')}::{attr(f2, 'name')}")
                            show = attr(field2, "showValuesFrom", "")
                            if show:
                                output.append(f"  show: {show}")

                    # Related table filter
                    rel_table = source.find("Table")
                    if rel_table is not None:
                        output.append(f"  from TO: {attr(rel_table, 'name')}")
                else:
                    output.append(f"VL: {name} [id:{vid}]")

            output.append("")

        return "\n".join(output)

    # --- CUSTOM FUNCTIONS ---
    def extract_custom_functions(self):
        """Extract custom function definitions."""
        output = []
        output.append("=" * 70)
        output.append("FILEMAKER CUSTOM FUNCTIONS")
        output.append("=" * 70)
        output.append("")
        output.append("KEY:")
        output.append("  CF: <name>(<params>) [id:<id>]")
        output.append("    <calculation body>")
        output.append("")

        catalog = self.file_node.find("CustomFunctionCatalog")
        if catalog is None:
            output.append("(No custom functions found)")
            return "\n".join(output)

        cfs = catalog.findall("CustomFunction")
        self.stats["custom_functions"] = len(cfs)

        for cf in cfs:
            name = attr(cf, "name")
            cid = attr(cf, "id")
            visibility = attr(cf, "visible", "True")

            # Parameters
            params = []
            param_list = cf.findall("Parameter") or cf.findall(".//Parameter")
            for p in param_list:
                params.append(attr(p, "name", text(p)))

            param_str = ", ".join(params) if params else ""
            vis_str = " [hidden]" if visibility == "False" else ""

            output.append(f"CF: {name}({param_str}) [id:{cid}]{vis_str}")

            # Function body
            calc = find_calc(cf)
            if calc:
                output.append(indent_text(calc))
            output.append("")

        return "\n".join(output)

    # --- ACCOUNTS & PRIVILEGES ---
    def extract_accounts(self):
        """Extract accounts and privilege information."""
        output = []
        output.append("=" * 70)
        output.append("FILEMAKER ACCOUNTS & PRIVILEGES")
        output.append("=" * 70)
        output.append("")

        # Accounts
        acct_catalog = self.file_node.find("AccountCatalog")
        if acct_catalog is not None:
            output.append("--- ACCOUNTS ---")
            accts = acct_catalog.findall("Account")
            self.stats["accounts"] = len(accts)
            for acct in accts:
                name = attr(acct, "name")
                status = attr(acct, "status", "Active")
                priv = acct.find("PrivilegeSet")
                priv_name = attr(priv, "name") if priv is not None else "?"
                auth = attr(acct, "type", "FileMaker")
                output.append(f"  ACCT: {name} | {status} | privSet:{priv_name} | auth:{auth}")
            output.append("")

        # Privilege Sets
        priv_catalog = self.file_node.find("PrivilegeSetCatalog")
        if priv_catalog is not None:
            output.append("--- PRIVILEGE SETS ---")
            privs = priv_catalog.findall("PrivilegeSet")
            self.stats["privilege_sets"] = len(privs)
            for ps in privs:
                name = attr(ps, "name")
                pid = attr(ps, "id")
                desc = ps.find("Comment") or ps.find("Description")
                desc_text = text(desc)[:100] if desc is not None else ""
                output.append(f"  PRIV: {name} [id:{pid}]")
                if desc_text:
                    output.append(f"    desc: {desc_text}")

                # Record access
                records = ps.find("Records")
                if records is not None:
                    for rec in records:
                        tname = attr(rec, "name", rec.tag)
                        access = attr(rec, "access", "")
                        if access:
                            output.append(f"    records: {tname} = {access}")

                # Layout access
                layouts = ps.find("Layouts")
                if layouts is not None:
                    la = attr(layouts, "access", "")
                    if la:
                        output.append(f"    layouts: {la}")

                # Script access
                scripts = ps.find("Scripts")
                if scripts is not None:
                    sa = attr(scripts, "access", "")
                    if sa:
                        output.append(f"    scripts: {sa}")

            output.append("")

        # Extended Privileges
        ext_catalog = self.file_node.find("ExtendedPrivilegeCatalog")
        if ext_catalog is not None:
            output.append("--- EXTENDED PRIVILEGES ---")
            exts = ext_catalog.findall("ExtendedPrivilege")
            self.stats["extended_privileges"] = len(exts)
            for ep in exts:
                name = attr(ep, "name")
                eid = attr(ep, "id")
                comment = ep.find("Comment")
                comment_text = text(comment)[:100] if comment is not None else ""
                output.append(f"  EXT: {name} [id:{eid}]")
                if comment_text:
                    output.append(f"    {comment_text}")
                # Which priv sets have this
                ps_list = ep.findall(".//PrivilegeSet")
                if ps_list:
                    ps_names = [attr(p, "name") for p in ps_list]
                    output.append(f"    usedBy: {', '.join(ps_names)}")
            output.append("")

        return "\n".join(output)

    # --- CUSTOM MENUS ---
    def extract_menus(self):
        """Extract custom menus and menu sets."""
        output = []
        output.append("=" * 70)
        output.append("FILEMAKER CUSTOM MENUS")
        output.append("=" * 70)
        output.append("")

        # Menu Sets
        ms_catalog = self.file_node.find("CustomMenuSetCatalog")
        if ms_catalog is not None:
            output.append("--- MENU SETS ---")
            msets = ms_catalog.findall("CustomMenuSet")
            self.stats["menu_sets"] = len(msets)
            for ms in msets:
                name = attr(ms, "name")
                msid = attr(ms, "id")
                output.append(f"  MENUSET: {name} [id:{msid}]")
                menus = ms.findall(".//CustomMenu") or ms.findall("CustomMenu")
                for m in menus:
                    output.append(f"    -> {attr(m, 'name')}")
            output.append("")

        # Custom Menus
        m_catalog = self.file_node.find("CustomMenuCatalog")
        if m_catalog is not None:
            output.append("--- CUSTOM MENUS ---")
            menus = m_catalog.findall("CustomMenu")
            self.stats["custom_menus"] = len(menus)
            for menu in menus:
                name = attr(menu, "name")
                mid = attr(menu, "id")
                base = attr(menu, "baseMenu", "")
                output.append(f"  MENU: {name} [id:{mid}] base:{base}")

                items = menu.findall(".//MenuItem") or menu.findall("MenuItem")
                for item in items:
                    iname = attr(item, "name", "")
                    script_ref = item.find(".//Script") or item.find("Script")
                    if script_ref is not None:
                        output.append(f"    ITEM: {iname} -> script:{attr(script_ref, 'name')}")
                    elif iname:
                        output.append(f"    ITEM: {iname}")
            output.append("")

        if "menu_sets" not in self.stats and "custom_menus" not in self.stats:
            output.append("(No custom menus found)")

        return "\n".join(output)

    # --- FILE REFERENCES ---
    def extract_file_refs(self):
        """Extract external data source references."""
        output = []
        output.append("=" * 70)
        output.append("FILEMAKER EXTERNAL DATA SOURCES")
        output.append("=" * 70)
        output.append("")

        catalog = self.file_node.find("ExternalDataSourcesCatalog")
        if catalog is None:
            output.append("(No external data sources found)")
            return "\n".join(output)

        # File References
        file_refs = catalog.findall("FileReference")
        self.stats["file_references"] = len(file_refs)
        if file_refs:
            output.append("--- FILE REFERENCES ---")
            for fr in file_refs:
                name = attr(fr, "name")
                frid = attr(fr, "id")
                paths = attr(fr, "pathList", "").strip()
                output.append(f"  REF: {name} [id:{frid}]")
                if paths:
                    for p in paths.split("\r"):
                        p = p.strip()
                        if p:
                            output.append(f"    path: {p}")
            output.append("")

        # ODBC Sources
        odbc_refs = catalog.findall("OdbcDataSource")
        if odbc_refs:
            output.append("--- ODBC DATA SOURCES ---")
            for od in odbc_refs:
                name = attr(od, "name")
                dsn = attr(od, "DSN")
                output.append(f"  ODBC: {name} DSN:{dsn}")
            output.append("")

        return "\n".join(output)

    # --- SUMMARY ---
    def generate_summary(self):
        """Generate a summary overview file."""
        output = []
        output.append("=" * 70)
        output.append("FILEMAKER DDR SUMMARY")
        output.append("=" * 70)
        output.append("")
        output.append(f"Source: {self.xml_path}")
        output.append(f"Parsed: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        output.append(f"Original size: {os.path.getsize(self.xml_path) / (1024*1024):.1f} MB")

        file_name = attr(self.file_node, "name", "Unknown")
        output.append(f"File: {file_name}")
        output.append("")

        output.append("--- COUNTS ---")
        for key, val in sorted(self.stats.items()):
            output.append(f"  {key}: {val}")
        output.append("")

        output.append("--- OUTPUT FILES ---")
        output.append("  01_SCHEMA.txt       - Tables, fields, calculations, auto-enter, validation")
        output.append("  02_RELATIONSHIPS.txt - Table occurrences and relationship predicates")
        output.append("  03_SCRIPTS.txt      - All scripts with step details")
        output.append("  04_LAYOUTS.txt      - Layouts, parts, objects, triggers")
        output.append("  05_VALUELISTS.txt   - Value list definitions")
        output.append("  06_CUSTOM_FUNCS.txt - Custom function definitions")
        output.append("  07_ACCOUNTS.txt     - Security: accounts, privileges")
        output.append("  08_MENUS.txt        - Custom menus and menu sets")
        output.append("  09_FILE_REFS.txt    - External data source references")
        output.append("  10_SUMMARY.txt      - This file")
        output.append("")
        output.append("TIPS FOR USING WITH CLAUDE:")
        output.append("  - Upload 01_SCHEMA + 02_RELATIONSHIPS for data model questions")
        output.append("  - Upload 03_SCRIPTS when working on script logic")
        output.append("  - Upload 04_LAYOUTS when working on UI/layout issues")
        output.append("  - Upload 06_CUSTOM_FUNCS when debugging calculations")
        output.append("  - Upload multiple files together for cross-cutting work")
        output.append("  - If a file is still too large, you can split it manually")
        output.append("    by table or by script folder")

        return "\n".join(output)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Compress FileMaker DDR XML into compact text files for LLM consumption."
    )
    parser.add_argument("xml_file", help="Path to the DDR XML file (the detail report, not Summary.xml)")
    parser.add_argument("--output-dir", "-o", default=None,
                        help="Output directory (default: same directory as input, subfolder 'DDR_Compressed')")
    parser.add_argument("--no-emoji", action="store_true",
                        help="Skip emoji restoration (leave ?? patterns as-is)")

    args = parser.parse_args()

    if not os.path.exists(args.xml_file):
        print(f"ERROR: File not found: {args.xml_file}")
        sys.exit(1)

    # Set up output directory
    if args.output_dir:
        out_dir = args.output_dir
    else:
        base_dir = os.path.dirname(os.path.abspath(args.xml_file))
        out_dir = os.path.join(base_dir, "DDR_Compressed")

    os.makedirs(out_dir, exist_ok=True)

    # Parse
    ddr = DDRParser(args.xml_file)
    ddr.parse()

    # Extract each section and write to file
    sections = [
        ("01_SCHEMA.txt",       "Schema (Tables & Fields)", ddr.extract_schema),
        ("02_RELATIONSHIPS.txt", "Relationships",           ddr.extract_relationships),
        ("03_SCRIPTS.txt",      "Scripts",                  ddr.extract_scripts),
        ("04_LAYOUTS.txt",      "Layouts",                  ddr.extract_layouts),
        ("05_VALUELISTS.txt",   "Value Lists",              ddr.extract_valuelists),
        ("06_CUSTOM_FUNCS.txt", "Custom Functions",         ddr.extract_custom_functions),
        ("07_ACCOUNTS.txt",     "Accounts & Privileges",    ddr.extract_accounts),
        ("08_MENUS.txt",        "Custom Menus",             ddr.extract_menus),
        ("09_FILE_REFS.txt",    "File References",          ddr.extract_file_refs),
    ]

    total_size = 0
    emoji_total = 0
    do_emoji = HAS_EMOJI_RESTORE and not args.no_emoji

    if do_emoji:
        print(f"Emoji restoration: ENABLED ({len(EMOJI_MAP)} mappings loaded)")
    elif not HAS_EMOJI_RESTORE:
        print("Emoji restoration: DISABLED (fm_emoji_restore.py not found in same directory)")
        print("  Place fm_emoji_restore.py next to this script to enable.")
    else:
        print("Emoji restoration: DISABLED (--no-emoji flag)")

    for filename, label, extractor in sections:
        print(f"Extracting {label}...")
        try:
            content = extractor()

            # Restore garbled emoji if module is available
            if do_emoji:
                original_qq = content.count('??')
                content = restore_emoji(content)
                restored_qq = content.count('??')
                emoji_fixed = (original_qq - restored_qq) // 2
                emoji_total += emoji_fixed
                if emoji_fixed > 0:
                    print(f"  -> Restored {emoji_fixed} emoji")

            filepath = os.path.join(out_dir, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            size = os.path.getsize(filepath)
            total_size += size
            print(f"  -> {filepath} ({size / 1024:.1f} KB)")
        except Exception as e:
            print(f"  ERROR extracting {label}: {e}")
            import traceback
            traceback.print_exc()

    # Summary (needs stats from other extractions)
    print("Generating summary...")
    summary = ddr.generate_summary()
    if do_emoji:
        summary = restore_emoji(summary)
    summary_path = os.path.join(out_dir, "10_SUMMARY.txt")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary)
    total_size += os.path.getsize(summary_path)

    original_size = os.path.getsize(args.xml_file)
    ratio = (total_size / original_size) * 100

    print("")
    print("=" * 50)
    print(f"DONE!")
    print(f"Original:   {original_size / (1024*1024):.1f} MB")
    print(f"Compressed: {total_size / (1024*1024):.1f} MB ({ratio:.1f}% of original)")
    if do_emoji:
        print(f"Emoji restored: {emoji_total} replacements")
    print(f"Output:     {out_dir}")
    print("=" * 50)


if __name__ == "__main__":
    main()