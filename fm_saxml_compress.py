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
    """Extract calculation text from SaXML.

    Step parameters use a doubly-nested form:
      <Calculation datatype=".."><Calculation><Text>...</Text></Calculation></Calculation>
    Field auto-enter / portal filters use a single level:
      <Calculation><Text>...</Text></Calculation>
    Auto-enter wraps in <Calculated>.
    """
    if parent is None:
        return ""
    calc = parent.find("Calculation")
    if calc is None:
        calcd = parent.find("Calculated")
        if calcd is not None:
            calc = calcd.find("Calculation")
    if calc is not None:
        inner = calc.find("Calculation")
        if inner is not None:
            t = inner.find("Text")
            if t is not None and t.text:
                return t.text.strip()
        t = calc.find("Text")
        if t is not None and t.text:
            return t.text.strip()
        if calc.text and calc.text.strip():
            return calc.text.strip()
    t = parent.find("Text")
    if t is not None and t.text:
        return t.text.strip()
    return ""


def param_field_ref(param_el):
    """Read TO::Field from a <Parameter type="FieldReference"> element."""
    if param_el is None:
        return ""
    fr = param_el.find("FieldReference")
    if fr is None:
        return ""
    fname = attr(fr, "name")
    tor = fr.find("TableOccurrenceReference")
    to_name = attr(tor, "name") if tor is not None else ""
    if to_name and fname:
        return f"{to_name}::{fname}"
    return fname or ""


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

    def _catalog_items(self, catalog):
        """Yield item elements from a catalog.

        Some catalogs (CustomFunctions, Accounts, PrivilegeSets,
        ExtendedPrivileges, CustomMenuSets) wrap their items inside an
        <ObjectList> child alongside metadata like <UUID>, <TagList>,
        <Options>. Others list items as direct children. Handle both.
        """
        if catalog is None:
            return
        ol = catalog.find("ObjectList")
        source = ol if ol is not None else catalog
        for child in source:
            if child.tag in ("UUID", "TagList", "Options",
                             "PasteIndexList", "ObjectList"):
                continue
            yield child

    # ============================================================
    # 01 SCHEMA — Tables & Fields
    # ============================================================
    def extract_schema(self):
        output = []
        output.append("# FileMaker Schema")
        output.append("")
        output.append("Tables, fields, calculations, validation, and storage.")
        output.append("")

        bt_catalog = self._find_catalog("BaseTableCatalog")
        if bt_catalog is None:
            output.append("_(No BaseTableCatalog found)_")
            return "\n".join(output)

        table_count = 0
        field_count = 0

        fields_by_table = defaultdict(list)

        for action in [self.add_action, self.modify_action]:
            if action is None:
                continue
            for fft in action.findall("FieldsForTables"):
                self._collect_fields(fft, fields_by_table)
            for fc in action.findall("FieldCatalog"):
                self._collect_fields(fc, fields_by_table)

        for (tid, tname) in sorted(fields_by_table.keys(), key=lambda x: x[1]):
            table_count += 1
            fields = fields_by_table[(tid, tname)]
            output.append(f"## Table: **`{tname}`** — {len(fields)} fields  <sub>`[id:{tid}]`</sub>")
            output.append("")

            for field in fields:
                fid = attr(field, "id")
                fname = attr(field, "name")
                ftype = attr(field, "fieldtype") or attr(field, "fieldType", "Normal")
                dtype = attr(field, "datatype") or attr(field, "dataType", "")
                comment = attr(field, "comment", "")

                if not fname:
                    continue
                field_count += 1

                type_bits = [b for b in (ftype, dtype) if b]
                type_str = f" *({', '.join(type_bits)})*" if type_bits else ""
                output.append(f"- **`{fname}`**{type_str}  <sub>`[id:{fid}]`</sub>")

                calc = field.find("Calculation")
                if calc is not None:
                    calc_text = find_calc(field)
                    if calc_text:
                        output.append(f"  - **Calc:** `{calc_text[:200]}`")

                ae = field.find("AutoEnter")
                if ae is not None:
                    ae_parts = []
                    ae_type = attr(ae, "type")
                    if ae_type:
                        ae_parts.append(ae_type)
                    ae_calc = find_calc(ae)
                    if ae_calc:
                        ae_parts.append(f"calc=`{ae_calc[:120]}`")
                    if ae_parts:
                        output.append(f"  - **Auto-enter:** {', '.join(ae_parts)}")

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
                        output.append(f"  - **Validation:** {', '.join(val_parts)}")

                if comment:
                    output.append(f"  - **Comment:** _{comment[:100]}_")

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
                        output.append(f"  - **Storage:** {', '.join(stor_parts)}")

            output.append("")

        found_ids = set(tid for tid, _ in fields_by_table.keys())
        for bt in bt_catalog:
            tid = attr(bt, "id")
            tname = attr(bt, "name")
            if tid and tid not in found_ids and tname:
                table_count += 1
                output.append(f"## Table: **`{tname}`** — 0 fields in export  <sub>`[id:{tid}]`</sub>")
                output.append("")

        self.stats["tables"] = table_count
        self.stats["fields"] = field_count
        print(f"  Schema: {table_count} tables, {field_count} fields")
        return "\n".join(output)

    def _collect_fields(self, container, fields_by_table):
        """Collect fields from a FieldCatalog or FieldsForTables container.

        FieldsForTables wraps one FieldCatalog per table — iterate them all.
        A single FieldCatalog has BaseTableReference + ObjectList > Field.

        Note: never chain Element returns with `or` — childless Elements
        are falsy in Python, so `find('A') or find('B')` returns None
        when A exists but has no children. Use explicit `is None` checks.
        """
        table_ref = container.find("BaseTableReference")
        if table_ref is None:
            table_ref = container.find("TableReference")

        # Wrapper case: no table ref at this level → recurse into every child FieldCatalog
        if table_ref is None:
            child_fcs = (container.findall("FieldCatalog")
                         + container.findall("FieldCataloogue"))
            if child_fcs:
                for fc in child_fcs:
                    self._collect_fields(fc, fields_by_table)
                return
            # No nested catalogs — fall through and try to read fields directly

        tname = attr(table_ref, "name") if table_ref is not None else "?"
        tid = attr(table_ref, "id") if table_ref is not None else ""

        # Fields in ObjectList > Field
        obj_list = container.find("ObjectList")
        if obj_list is not None:
            for field in obj_list:
                if field.tag == "Field":
                    fields_by_table[(tid, tname)].append(field)
        else:
            for field in container:
                if field.tag == "Field" and field.get("name"):
                    fields_by_table[(tid, tname)].append(field)

    # ============================================================
    # 02 TABLE OCCURRENCES & RELATIONSHIPS (merged)
    # ============================================================
    def extract_relationships(self):
        output = []
        output.append("# Table Occurrences & Relationships")
        output.append("")
        output.append(
            "Each table occurrence (TO) is shown with its relationships listed beneath. "
            "Flags in `(…)` after a relationship: `createL/createR` = allow creation of related records, "
            "`deleteL/deleteR` = cascade delete."
        )
        output.append("")

        to_catalog = self._find_catalog("TableOccurrenceCatalog")
        if to_catalog is None:
            to_catalog = self._find_catalog("TableOccuerenceCatalogue")
        if to_catalog is None:
            output.append("_(No TableOccurrenceCatalog found)_")
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

            pred_str = " — " + ", ".join(f"`{p}`" for p in preds) if preds else ""
            flag_str = f" _({', '.join(flags)})_" if flags else ""
            rel_count += 1

            rels_by_to[left_name].append((right_name, pred_str, flag_str))
            rels_by_to[right_name].append((left_name, pred_str, flag_str))

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

            output.append(f"## TO: **`{to_name}`** → base: `{bt_name}`  <sub>`[id:{to_id}]`</sub>")
            output.append("")

            rels = rels_by_to.get(to_name, [])
            if not rels:
                output.append("_(no relationships)_")
            else:
                for other, pred_str, flag_str in rels:
                    output.append(f"- ⟷ **`{other}`**{pred_str}{flag_str}")
            output.append("")

        self.stats["table_occurrences"] = to_count
        self.stats["relationships"] = rel_count
        print(f"  Relationships: {to_count} TOs, {rel_count} joins")
        return "\n".join(output)

    # ============================================================
    # 03 SCRIPTS — Names, folders, and steps
    # ============================================================
    def extract_scripts(self):
        output = []
        output.append("# FileMaker Scripts")
        output.append("")
        # Parsing guide for downstream tools / LLMs that need to slice this
        # file by script. Lives at the top so it survives any head-only read.
        output.append("## Format")
        output.append("- Script = `## Script: **`Name`** <sub>`[id:N]`</sub>` header → next `## Script:` header. Slice between headers; don't read line-by-line.")
        output.append("- Steps numbered `N.` in ```javascript fences. Multi-line steps: only line 1 has `N.`; rest are unprefixed continuations (~14k lines).")
        output.append("- Disabled steps: `//` on every line. `// (empty)` = blank spacer. `--`/`-` script with no fence = folder separator.")
        output.append("- grep lands inside continuations (no step#/name attached). To locate a match, walk back to nearest `## Script:` header.")
        output.append("")

        sc = self._find_catalog("ScriptCatalog")
        sfs = self._find_catalog("StepsForScripts")

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
                        steps = script_el.findall("Step")
                        if steps:
                            step_index[sid] = steps
            print(f"  Step index: {len(step_index)} scripts with steps")

        script_count = 0
        step_total = 0

        if sc is None:
            output.append("_(No ScriptCatalog found)_")
            return "\n".join(output)

        def process_script_item(item, depth=0):
            nonlocal script_count, step_total

            is_folder = attr(item, "isFolder") == "True"
            name = attr(item, "name")
            sid = attr(item, "id")

            if is_folder:
                # Folders use h2 (or h3 if nested) so the outline panel groups by folder.
                folder_heading = "##" if depth == 0 else "###"
                output.append(f"{folder_heading} 📁 {name}")
                output.append("")
                for child in item:
                    if child.tag == "Script":
                        process_script_item(child, depth + 1)
            elif item.tag == "Script" and name:
                script_count += 1
                # Scripts always h2 so every script is the same prominence in the outline.
                output.append("---")
                output.append("")
                output.append(f"## Script: **`{name}`**  <sub>`[id:{sid}]`</sub>")
                output.append("")

                steps = step_index.get(sid, [])
                steps_sorted = sorted(steps, key=lambda s: int(attr(s, "index", "0")))

                if not steps_sorted:
                    output.append("_(no steps)_")
                    output.append("")
                    return

                output.append("```javascript")
                for line_num, step in enumerate(steps_sorted, 1):
                    step_lines = self._format_step_md(step, line_num)
                    output.extend(step_lines)
                    step_total += 1
                output.append("```")
                output.append("")

        for child in sc:
            if child.tag == "Script":
                process_script_item(child, 0)

        self.stats["scripts"] = script_count
        print(f"  Scripts: {script_count} scripts, {step_total} steps")
        return "\n".join(output)

    def _format_step_md(self, step, line_num):
        """Render a script step as one-or-more code-block lines.

        Multi-line calculations keep the FileMaker-source formatting they came
        in with — we just split on `\n` so the disabled-prefix can be applied
        per line. Disabled steps get `//` on every line so the JS syntax
        highlighter colors them as comments (effectively greying them out).
        """
        step_type_id = attr(step, "id")
        # SaXML puts the human-readable step name directly on the <Step> element.
        # Trust that first; the hardcoded STEP_TYPES map is only a fallback for
        # steps where the XML omits the attribute.
        step_name = attr(step, "name") or STEP_TYPES.get(step_type_id, f"Step#{step_type_id}")
        enabled = attr(step, "enable") or attr(step, "enabled", "True")
        is_disabled = enabled == "False"

        params = self._extract_saxml_step_params(step, step_type_id, step_name)

        if step_type_id == "89":
            # Comment step — params already begins with "// "; drop the step name.
            body = f"{line_num}. {params}"
        elif params:
            body = f"{line_num}. {step_name} {params}"
        else:
            body = f"{line_num}. {step_name}"

        lines = body.split("\n")

        if is_disabled:
            lines = [f"// {l}" for l in lines]

        return lines

    def _extract_saxml_step_params(self, step, type_id, step_name):
        """Extract parameters from a SaXML step element."""

        # Comment — the body is stored as the `value` attribute on a
        # <Comment> element (NOT a <Text> child): the XML looks like
        # <Parameter type="Comment"><Comment value="…"/></Parameter>.
        if type_id == "89":
            c = step.find("ParameterValues/Parameter/Comment")
            if c is None:
                c = step.find(".//Comment")
            if c is not None:
                ct = attr(c, "value") or text(c)
                if ct:
                    return f"// {ct}"
            return "// (empty)"

        params = step.findall("ParameterValues/Parameter")

        # Set Variable (141)
        if type_id == "141":
            var_name = ""
            var_value = ""
            for p in params:
                if attr(p, "type") == "Variable":
                    name_el = p.find("Name")
                    if name_el is not None:
                        var_name = attr(name_el, "value")
                    val_el = p.find("value")
                    if val_el is not None:
                        var_value = find_calc(val_el)
            if var_name:
                return f"{var_name} = {var_value}" if var_value else var_name

        # Set Field (76)
        if type_id == "76":
            fr = ""
            calc = ""
            for p in params:
                ptype = attr(p, "type")
                if ptype == "FieldReference":
                    fr = param_field_ref(p)
                elif ptype == "Calculation":
                    calc = find_calc(p)
            if fr:
                return f"{fr} = {calc}" if calc else fr

        # Set Field By Name (178): two Calculation params (target name, value)
        if type_id == "178":
            calcs = [find_calc(p) for p in params if attr(p, "type") == "Calculation"]
            calcs = [c for c in calcs if c]
            if len(calcs) >= 2:
                return f"{calcs[0]} = {calcs[1]}"
            if calcs:
                return f"name=({calcs[0]})"

        # If (68) / Exit Loop If (72)
        if type_id in ("68", "72"):
            for p in params:
                if attr(p, "type") == "Calculation":
                    c = find_calc(p)
                    if c:
                        return f"({c})"

        # Perform Script (1) / Perform Script on Server (140)
        if type_id in ("1", "140"):
            sref = step.find(".//ScriptReference")
            sname = attr(sref, "name") if sref is not None else "?"
            param_calc = ""
            for p in params:
                if attr(p, "type") == "Parameter":
                    inner = p.find("Parameter")
                    if inner is not None:
                        param_calc = find_calc(inner)
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
            for p in params:
                if attr(p, "type") == "Calculation":
                    c = find_calc(p)
                    if c:
                        return f"=> {c}"

        # Show Custom Dialog (87, 55)
        if type_id in ("87", "55"):
            title = ""
            msg = ""
            for p in params:
                ptype = attr(p, "type")
                if ptype == "Title":
                    title = find_calc(p)
                elif ptype == "Message":
                    msg = find_calc(p)
            result = ""
            if title:
                result += f"title=({title})"
            if msg:
                result += f" msg=({msg})"
            return result.strip()

        # Exit Script (103, 177)
        if type_id in ("103", "177"):
            for p in params:
                if attr(p, "type") == "Calculation":
                    c = find_calc(p)
                    if c:
                        return f"result=({c})"

        # Insert from URL (171)
        if type_id == "171":
            fr = ""
            url = ""
            for p in params:
                ptype = attr(p, "type")
                if ptype == "FieldReference":
                    fr = param_field_ref(p) or fr
                elif ptype == "Calculation":
                    c = find_calc(p)
                    if c and not url:
                        url = c
            if not fr:
                fr = field_ref_str(step)
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
        output.append("# FileMaker Layouts")
        output.append("")
        output.append(
            "Each layout shows its table occurrence, layout-level script triggers, and a "
            "nested list of objects (fields, buttons, portals, tab controls, web viewers). "
            "Per-object annotations include `name`, object `trigger`s, `condFormat` "
            "(conditional-formatting rules), `placeholder` text, and `hideWhen`. Decorative "
            "shapes (lines, rectangles) are stripped. Folders use 📁; layouts are h2."
        )
        output.append("")

        lc = self._find_catalog("LayoutCatalog", search_modify=True)
        if lc is None:
            output.append("_(No LayoutCatalog found)_")
            return "\n".join(output)

        layout_count = 0
        obj_count = 0

        def process_layout_item(item, depth=0):
            nonlocal layout_count, obj_count

            if item.tag == "Group" or attr(item, "isFolder") == "True":
                name = attr(item, "name")
                folder_heading = "##" if depth == 0 else "###"
                output.append(f"{folder_heading} 📁 {name}")
                output.append("")
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

            output.append("---")
            output.append("")
            output.append(f"## Layout: **`{name}`** → TO: `{to_name}`  <sub>`[id:{lid}]`</sub>")
            output.append("")

            triggers_el = item.find("ScriptTriggers")
            triggers_out = []
            if triggers_el is not None:
                for trigger in triggers_el.findall("ScriptTrigger"):
                    sref = trigger.find("ScriptReference")
                    if sref is not None:
                        taction = attr(trigger, "action", "Trigger")
                        triggers_out.append(f"- **{taction}** → `{attr(sref, 'name')}`")
            if triggers_out:
                output.append("**Triggers**")
                output.append("")
                output.extend(triggers_out)
                output.append("")

            objs_before = len(output)
            output.append("**Objects**")
            output.append("")
            obj_section_start = len(output)

            parts_list = item.find("PartsList")
            if parts_list is not None:
                for part in parts_list.findall("Part"):
                    obj_list = part.find("ObjectList")
                    if obj_list is not None:
                        for lo in obj_list:
                            if lo.tag == "LayoutObject":
                                self._process_layout_object(lo, output, 0)
                                obj_count += 1

            for obj_list in item.findall("ObjectList"):
                for lo in obj_list:
                    if lo.tag == "LayoutObject":
                        self._process_layout_object(lo, output, 0)
                        obj_count += 1

            if len(output) == obj_section_start:
                # Nothing emitted — strip the "Objects" header.
                del output[objs_before:]

            output.append("")

        for child in lc:
            process_layout_item(child, 0)

        self.stats["layouts"] = layout_count
        print(f"  Layouts: {layout_count} layouts, {obj_count} objects")
        return "\n".join(output)

    def _process_layout_object(self, lo, output, depth, max_depth=8):
        """Process a single LayoutObject from SaXML, emitting nested markdown bullets."""
        if depth > max_depth:
            return
        prefix = "  " * depth

        otype = attr(lo, "type")
        oname = attr(lo, "name")

        if otype in ("Line", "Rectangle", "RoundedRect", "Oval", "Graphic"):
            return

        hide_calc = ""
        hide_el = lo.find("Hide")
        if hide_el is not None:
            hc = hide_el.find("Calculation")
            if hc is not None:
                t = hc.find("Text")
                hide_calc = (t.text or "").strip() if t is not None else ""

        # Conditional formatting and placeholder text apply to (almost) any
        # object type, so compute them once here and let emit() append them
        # automatically — no per-branch wiring needed.
        cond_formats = self._conditional_formats(lo)
        placeholder = self._placeholder_text(lo)

        def emit(label, extras=None):
            """Emit `- **LABEL** — extra · extra` at the current depth.

            Conditional formatting and placeholder text are appended here so
            every object type picks them up without each branch repeating the
            logic.
            """
            extras = list(extras) if extras else []
            for cf in cond_formats:
                extras.append(f"condFormat `{cf[:80]}`")
            if placeholder:
                extras.append(f"placeholder `{placeholder[:80]}`")
            tail = (" — " + " · ".join(extras)) if extras else ""
            output.append(f"{prefix}- {label}{tail}")

        portal_el = lo.find("Portal")
        if otype == "Portal" or portal_el is not None:
            if portal_el is None:
                portal_el = lo
            tor = portal_el.find("TableOccurrenceReference")
            portal_to = attr(tor, "name") if tor is not None else "?"
            extras = []
            if oname:
                extras.append(f"name `{oname}`")
            pc = portal_el.find("Calculation")
            if pc is not None:
                t = pc.find("Text")
                fc = (t.text or "").strip() if t is not None else ""
                if fc:
                    extras.append(f"filter `{fc[:120]}`")
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
                    extras.append(f"sort `{', '.join(sorts)}`")
            if hide_calc:
                extras.append(f"hideWhen `{hide_calc[:80]}`")
            emit(f"**PORTAL** showFrom `{portal_to}`", extras)
            for obj_list in portal_el.findall("ObjectList"):
                for child_lo in obj_list:
                    if child_lo.tag == "LayoutObject":
                        self._process_layout_object(child_lo, output, depth + 1, max_depth)
            return

        gb_el = lo.find("GroupedButton")
        if otype == "Grouped Button" or gb_el is not None:
            if gb_el is None:
                gb_el = lo
            label = "**BUTTON**"
            if oname:
                label += f" `{oname}`"
            extras = []
            action = gb_el.find("action")
            if action is not None:
                sref = action.find("ScriptReference")
                if sref is not None:
                    extras.append(f"→ `{attr(sref, 'name')}`")
                    pcalc = action.find("Calculation/Text")
                    if pcalc is not None and pcalc.text and pcalc.text.strip():
                        extras.append(f"param `{pcalc.text.strip()[:100]}`")
                if sref is None:
                    step = action.find("Step")
                    if step is not None:
                        step_type = attr(step, "id")
                        sname = attr(step, "name") or STEP_TYPES.get(step_type, f"Step#{step_type}")
                        extras.append(f"action `{sname}`")
            for child_lo in gb_el.iter("LayoutObject"):
                if child_lo is not lo:
                    child_type = attr(child_lo, "type")
                    if child_type == "Edit Box":
                        cfr = self._get_field_ref(child_lo)
                        if cfr:
                            extras.append(f"field `{cfr}`")
                            break
                    elif child_type == "Text":
                        data = child_lo.find(".//Data")
                        if data is not None and data.text:
                            lbl = data.text.strip()[:50]
                            if lbl:
                                extras.append(f'label "{lbl}"')
                                break
            if hide_calc:
                extras.append(f"hideWhen `{hide_calc[:80]}`")
            emit(label, extras)
            return

        button_el = lo.find("Button")
        if otype == "Button" or button_el is not None:
            label = "**BUTTON**"
            if oname:
                label += f" `{oname}`"
            extras = []
            btn = button_el if button_el is not None else lo
            label_data = btn.find("Label/Text/StyledText/Data")
            if label_data is not None and label_data.text:
                lbl = label_data.text.strip()[:50]
                if lbl:
                    extras.append(f'label "{lbl}"')
            action = btn.find("action")
            if action is not None:
                sref = action.find("ScriptReference")
                if sref is not None:
                    extras.append(f"→ `{attr(sref, 'name')}`")
                    pcalc = action.find("Calculation/Text")
                    if pcalc is not None and pcalc.text and pcalc.text.strip():
                        extras.append(f"param `{pcalc.text.strip()[:100]}`")
                else:
                    step = action.find("Step")
                    if step is not None:
                        step_type = attr(step, "id")
                        sname = attr(step, "name") or STEP_TYPES.get(step_type, f"Step#{step_type}")
                        extras.append(f"action `{sname}`")
            for trigger in lo.findall("ScriptTriggers/ScriptTrigger"):
                tref = trigger.find("ScriptReference")
                if tref is not None:
                    extras.append(f"trigger {attr(trigger, 'action')} → `{attr(tref, 'name')}`")
            if hide_calc:
                extras.append(f"hideWhen `{hide_calc[:80]}`")
            emit(label, extras)
            for obj_list in lo.findall("ObjectList"):
                for child_lo in obj_list:
                    if child_lo.tag == "LayoutObject":
                        self._process_layout_object(child_lo, output, depth + 1, max_depth)
            return

        if otype == "Button Bar":
            extras = [f"name `{oname}`"] if oname else None
            emit("**BUTTON BAR**", extras)
            for obj_list in lo.findall("ObjectList"):
                for child_lo in obj_list:
                    if child_lo.tag == "LayoutObject":
                        self._process_layout_object(child_lo, output, depth + 1, max_depth)
            return

        if otype == "Popover Button":
            label = "**POPOVER**"
            if oname:
                label += f" `{oname}`"
            extras = []
            sref = lo.find(".//ScriptReference")
            if sref is not None:
                extras.append(f"→ `{attr(sref, 'name')}`")
            if hide_calc:
                extras.append(f"hideWhen `{hide_calc[:80]}`")
            emit(label, extras)
            for obj_list in lo.findall(".//ObjectList"):
                for child_lo in obj_list:
                    if child_lo.tag == "LayoutObject":
                        self._process_layout_object(child_lo, output, depth + 1, max_depth)
            return

        tab_ctrl = lo.find("TabControl")
        if otype == "Tab Control" or tab_ctrl is not None:
            panels = lo.findall(".//TabPanel") + lo.findall(".//Panel")
            tab_names = [attr(tp, "name") or "?" for tp in panels]
            extras = [f"tabs `{', '.join(tab_names)}`"]
            if oname:
                extras.append(f"name `{oname}`")
            emit("**TAB CONTROL**", extras)
            tab_prefix = "  " * (depth + 1)
            for tp in panels:
                tn = attr(tp, "name") or "?"
                output.append(f"{tab_prefix}- **TAB** `{tn}`")
                for obj_list in tp.findall("ObjectList"):
                    for child_lo in obj_list:
                        if child_lo.tag == "LayoutObject":
                            self._process_layout_object(child_lo, output, depth + 2, max_depth)
            return

        if otype == "Slide Control":
            extras = [f"name `{oname}`"] if oname else None
            emit("**SLIDE CONTROL**", extras)
            for obj_list in lo.findall(".//ObjectList"):
                for child_lo in obj_list:
                    if child_lo.tag == "LayoutObject":
                        self._process_layout_object(child_lo, output, depth + 1, max_depth)
            return

        if otype == "Web Viewer":
            url = ""
            wv_calc = lo.find(".//Calculation/Text")
            if wv_calc is not None:
                url = (wv_calc.text or "").strip()
            extras = []
            if oname:
                extras.append(f"name `{oname}`")
            if url:
                extras.append(f"url `{url[:100]}`")
            emit("**WEB VIEWER**", extras or None)
            return

        field_el = lo.find("Field")
        if otype == "Edit Box" or field_el is not None:
            fr = self._get_field_ref(lo)
            if not fr:
                return
            label = f"**FIELD** `{fr}`"
            extras = []
            if oname:
                extras.append(f"name `{oname}`")
            for trigger in lo.findall("ScriptTriggers/ScriptTrigger"):
                tref = trigger.find("ScriptReference")
                if tref is not None:
                    taction = attr(trigger, "action") or attr(trigger, "type", "Trigger")
                    extras.append(f"trigger {taction} → `{attr(tref, 'name')}`")
            if hide_calc:
                extras.append(f"hideWhen `{hide_calc[:80]}`")
            emit(label, extras)
            return

        if otype in ("Drop Down List", "Pop-up Menu", "Checkbox Set", "Radio Button Set"):
            fr = self._get_field_ref(lo)
            if fr:
                label = f"**FIELD** `{fr}` *({otype})*"
                extras = []
                if oname:
                    extras.append(f"name `{oname}`")
                for trigger in lo.findall("ScriptTriggers/ScriptTrigger"):
                    tref = trigger.find("ScriptReference")
                    if tref is not None:
                        extras.append(f"trigger {attr(trigger, 'action')} → `{attr(tref, 'name')}`")
                if hide_calc:
                    extras.append(f"hideWhen `{hide_calc[:80]}`")
                emit(label, extras)
            return

        if otype == "Text":
            has_trigger = lo.find("ScriptTriggers/ScriptTrigger") is not None
            merge_fr = self._get_field_ref(lo)
            if has_trigger or merge_fr or hide_calc or cond_formats or placeholder:
                label = "**TEXT**"
                extras = []
                if oname:
                    extras.append(f"name `{oname}`")
                if merge_fr:
                    extras.append(f"merge `{merge_fr}`")
                for trigger in lo.findall("ScriptTriggers/ScriptTrigger"):
                    tref = trigger.find("ScriptReference")
                    if tref is not None:
                        extras.append(f"trigger {attr(trigger, 'action')} → `{attr(tref, 'name')}`")
                if hide_calc:
                    extras.append(f"hideWhen `{hide_calc[:80]}`")
                emit(label, extras)
            return

        sref = lo.find(".//ScriptReference")
        has_trigger = lo.find("ScriptTriggers/ScriptTrigger") is not None
        if (sref is not None or has_trigger or hide_calc or cond_formats
                or placeholder or (oname and otype)):
            label = f"**{otype.upper()}**" if otype else "**OBJECT**"
            extras = []
            if oname:
                extras.append(f"name `{oname}`")
            if sref is not None:
                extras.append(f"→ `{attr(sref, 'name')}`")
            for trigger in lo.findall("ScriptTriggers/ScriptTrigger"):
                tref = trigger.find("ScriptReference")
                if tref is not None:
                    taction = attr(trigger, "action") or attr(trigger, "type", "Trigger")
                    extras.append(f"trigger {taction} → `{attr(tref, 'name')}`")
            if hide_calc:
                extras.append(f"hideWhen `{hide_calc[:80]}`")
            emit(label, extras)

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

    def _conditional_formats(self, lo):
        """Return the calc text of each conditional-formatting rule on an object.

        SaXML nests these as a direct child:
          <LayoutObject>…<Conditions><Formatting membercount="N">
            <Condition><Calculation><Text>…</Text></Calculation>
            <LocalCSS>…styling…</LocalCSS></Condition>…
        We keep the rule's calc (what triggers the formatting); the actual CSS
        styling is dropped as noise. `Conditions/Formatting` is matched only as
        a direct child so we don't pull in child objects' rules."""
        out = []
        fmt = lo.find("Conditions/Formatting")
        if fmt is None:
            return out
        for cond in fmt.findall("Condition"):
            calc = cond.find("Calculation")
            if calc is not None:
                t = calc.find("Text")
                if t is not None and t.text and t.text.strip():
                    out.append(t.text.strip())
        return out

    def _placeholder_text(self, lo):
        """Return a field's placeholder-text calc, scoped to the object's own
        Field so portals/containers don't borrow a nested field's placeholder.

        Structure: <Field>…<Display>…<Placeholder><Calculation><Text>…"""
        field_el = lo.find("Field")
        if field_el is None:
            return ""
        ph = field_el.find(".//Placeholder")
        if ph is None:
            return ""
        calc = ph.find("Calculation")
        if calc is not None:
            t = calc.find("Text")
            if t is not None and t.text and t.text.strip():
                return t.text.strip()
        return ""

    # ============================================================
    # 05 VALUE LISTS
    # ============================================================
    def extract_valuelists(self):
        output = []
        output.append("# FileMaker Value Lists")
        output.append("")

        vlc = self._find_catalog("ValueListCatalog")
        if vlc is None:
            output.append("_(No ValueListCatalog found)_")
            return "\n".join(output)

        vl_count = 0
        for vl in vlc:
            name = attr(vl, "name")
            vid = attr(vl, "id")
            if not name:
                continue
            vl_count += 1
            vl_type = attr(vl, "type", "Custom")
            output.append(f"- **`{name}`** *({vl_type})*  <sub>`[id:{vid}]`</sub>")
            fr = field_ref_str(vl)
            if fr:
                output.append(f"  - **Source:** `{fr}`")

        output.append("")
        self.stats["valuelists"] = vl_count
        return "\n".join(output)

    # ============================================================
    # 06 CUSTOM FUNCTIONS
    # ============================================================
    def extract_custom_functions(self):
        output = []
        output.append("# FileMaker Custom Functions")
        output.append("")

        cfc = self._find_catalog("CustomFunctionsCatalog")
        if cfc is None:
            output.append("_(No CustomFunctionsCatalog found)_")
            return "\n".join(output)

        # Function bodies live in a separate CalcsForCustomFunctions catalog —
        # the CustomFunctionsCatalog itself only carries names and parameters
        # (same split as ScriptCatalog vs StepsForScripts). Index the calcs by
        # the function id so we can join them back on.
        calc_index = {}
        cfcalcs = self._find_catalog("CalcsForCustomFunctions")
        if cfcalcs is not None:
            for cfc_el in cfcalcs.iter("CustomFunctionCalc"):
                ref = cfc_el.find("CustomFunctionReference")
                if ref is None:
                    continue
                ctext = find_calc(cfc_el)
                if ctext:
                    rid = attr(ref, "id")
                    if rid:
                        calc_index[rid] = ctext

        cf_count = 0
        for cf in self._catalog_items(cfc):
            name = attr(cf, "name")
            cfid = attr(cf, "id")
            if not name:
                continue
            cf_count += 1

            params = []
            for p in cf.findall(".//Parameter"):
                pname = attr(p, "name")
                if pname:
                    params.append(pname)
            param_str = f"({', '.join(params)})" if params else "()"

            output.append("---")
            output.append("")
            output.append(f"## **`{name}{param_str}`**  <sub>`[id:{cfid}]`</sub>")
            output.append("")
            # Prefer the body from CalcsForCustomFunctions; fall back to any
            # inline calc in the catalog entry for older/edge exports.
            calc = calc_index.get(cfid) or find_calc(cf)
            if calc:
                output.append("```")
                output.append(calc)
                output.append("```")
                output.append("")

        self.stats["custom_functions"] = cf_count
        return "\n".join(output)

    # ============================================================
    # 07 ACCOUNTS & PRIVILEGES
    # ============================================================
    def extract_accounts(self):
        output = []
        output.append("# FileMaker Accounts & Privileges")
        output.append("")

        search_locations = [self.add_action, self.modify_action, self.meta_add_action]

        acct_count = 0
        output.append("## Accounts")
        output.append("")
        accts_emitted = False
        for loc in search_locations:
            if loc is None:
                continue
            for cat in [loc.find("AccountsCatalog"), loc.find("AccountCatalog")]:
                for acct in self._catalog_items(cat):
                    name = find_text(acct.find("Authentication"), "AccountName") if acct.find("Authentication") is not None else ""
                    if not name:
                        name = attr(acct, "name")
                    if not name:
                        continue
                    acct_count += 1
                    accts_emitted = True
                    enabled = attr(acct, "enable") or attr(acct, "active")
                    state = "Active" if enabled == "True" else "Inactive"
                    atype = attr(acct, "type")
                    psref = acct.find("PrivilegeSetReference")
                    ps = attr(psref, "name") if psref is not None else ""
                    extras = [f"_{state}_"]
                    if atype:
                        extras.append(atype)
                    if ps:
                        extras.append(f"priv `{ps}`")
                    output.append(f"- **`{name}`** — {' · '.join(extras)}")
        if not accts_emitted:
            output.append("_(none)_")
        output.append("")

        output.append("## Privilege Sets")
        output.append("")
        ps_emitted = False
        for loc in search_locations:
            if loc is None:
                continue
            for cat in [loc.find("PrivilegeSetsCatalog"), loc.find("PrivilegeSetCatalog")]:
                for ps in self._catalog_items(cat):
                    name = attr(ps, "name")
                    if name:
                        ps_emitted = True
                        output.append(f"- **`{name}`**")
        if not ps_emitted:
            output.append("_(none)_")
        output.append("")

        output.append("## Extended Privileges")
        output.append("")
        ext_emitted = False
        for loc in search_locations:
            if loc is None:
                continue
            for cat in [loc.find("ExtendedPrivilegesCatalog"),
                        loc.find("ExtendedPrivilegeCatalog")]:
                for ep in self._catalog_items(cat):
                    name = attr(ep, "name")
                    if name:
                        ext_emitted = True
                        output.append(f"- **`{name}`**")
        if not ext_emitted:
            output.append("_(none)_")
        output.append("")

        self.stats["accounts"] = acct_count
        return "\n".join(output)

    # ============================================================
    # 10 SUMMARY
    # ============================================================
    def write_summary(self):
        output = []
        output.append("# FileMaker SaXML Summary")
        output.append("")
        output.append(f"- **Source:** `{self.xml_path}`")
        output.append(f"- **File:** {attr(self.root, 'File')}")
        output.append(f"- **FileMaker version:** {attr(self.root, 'Source')}")
        output.append(f"- **SaXML format:** {attr(self.root, 'version')}")
        output.append("")
        output.append("## Counts")
        output.append("")
        for k, v in sorted(self.stats.items()):
            output.append(f"- **{k}:** {v}")
        output.append("")
        output.append("## Output files")
        output.append("")
        output.append("| File | Contents |")
        output.append("|------|----------|")
        output.append("| `01_SCHEMA.md` | Tables, fields, calculations |")
        output.append("| `02_RELATIONSHIPS.md` | Table occurrences and relationships |")
        output.append("| `03_SCRIPTS.md` | Scripts with step details (full markdown) |")
        output.append("| `04_LAYOUTS.md` | Layouts with objects |")
        output.append("| `05_VALUELISTS.md` | Value list definitions |")
        output.append("| `06_CUSTOM_FUNCS.md` | Custom function definitions |")
        output.append("| `07_ACCOUNTS.md` | Security: accounts, privileges |")
        output.append("| `10_SUMMARY.md` | This file |")
        output.append("")
        return "\n".join(output)

    # ============================================================
    # RUN ALL
    # ============================================================
    def run(self):
        self.parse()

        extractors = [
            ("01_SCHEMA.md", self.extract_schema),
            ("02_RELATIONSHIPS.md", self.extract_relationships),
            ("03_SCRIPTS.md", self.extract_scripts),
            ("04_LAYOUTS.md", self.extract_layouts),
            ("05_VALUELISTS.md", self.extract_valuelists),
            ("06_CUSTOM_FUNCS.md", self.extract_custom_functions),
            ("07_ACCOUNTS.md", self.extract_accounts),
        ]

        # Prepended to every output file so the source DB is identifiable when
        # comparing schemas across exports.
        folder_name = os.path.basename(os.path.normpath(self.output_dir))
        xml_name = os.path.basename(self.xml_path)
        source_header = (
            f"> 📁 **{folder_name}**  \n"
            f"> 📄 Source: `{xml_name}`\n\n"
        )

        for filename, extractor in extractors:
            print(f"\nExtracting {filename}...")
            content = extractor()
            if not content.endswith("\n"):
                content += "\n"
            filepath = os.path.join(self.output_dir, filename)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(source_header + content)
            print(f"  Written: {filepath}")

        # Summary last (needs stats)
        summary = self.write_summary()
        filepath = os.path.join(self.output_dir, "10_SUMMARY.md")
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(source_header + summary)
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