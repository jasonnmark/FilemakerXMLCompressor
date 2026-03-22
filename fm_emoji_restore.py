"""
FileMaker DDR Emoji Restoration Module v4
==========================================
Restores garbled emoji in FileMaker DDR XML exports.

Corruption: Multi-byte emoji become literal "??" in the DDR XML.
  ??‍??  = ZWJ compound emoji (two codepoints joined by U+200D)
  ??     = Single emoji (one multi-byte codepoint)

Surviving emoji (BMP, not corrupted): ⚙️ ✅ ✖️ ➕ ⚠️ ⏱️ 🌐

Mapping sources:
  [S] Script names   [R] Relationship graph screenshots
  [U] User stated    [I] Inferred from context

Unmatched -> 🚫. Edit EMOJI_MAP to add new emoji.
"""

import re

# ============================================================================
# EMOJI MAP: (emoji_type, following_word) -> replacement
# ============================================================================

EMOJI_MAP = {

    # ===== ZWJ COMPOUND (??‍??) =====

    # 🧑‍🎓 Student [S]
    ("ZWJ", "(START)"):                          "🧑‍🎓",
    ("ZWJ", "Student"):                          "🧑‍🎓",
    ("ZWJ", "Nursing"):                          "🧑‍🎓",
    ("ZWJ", "⚙️Settings"):                       "🧑‍🎓",
    ("ZWJ", "✅SignInSignOut"):                    "🧑‍🎓",
    ("ZWJ", "✅StudentSignInStatus"):              "🧑‍🎓",
    ("ZWJ", "➕ImportAttendance"):                 "🧑‍🎓",
    ("ZWJ", "➕ImportMCAS"):                       "🧑‍🎓",
    ("ZWJ", "➕ImportMCASFullAssessmentHistory"):  "🧑‍🎓",
    ("ZWJ", "➕ImportNWEA"):                       "🧑‍🎓",
    ("ZWJ", "➕ImportPR600SIMS"):                  "🧑‍🎓",
    ("ZWJ", "➕ImportPartnersCareerPathway"):      "🧑‍🎓",
    ("ZWJ", "➕ImportStudentHolderToFindDupes"):   "🧑‍🎓",
    ("ZWJ", "➕ImportStudentProfileGravityForm"):  "🧑‍🎓",
    ("ZWJ", "➕ImportTrackerBanks"):               "🧑‍🎓",
    ("ZWJ", "➕ImportTrackerCredits"):             "🧑‍🎓",
    ("ZWJ", "➕ImportTrackerCurrentCourses"):      "🧑‍🎓",
    ("ZWJ", "➕ImportTrackerTranscriptDetails"):   "🧑‍🎓",
    ("ZWJ", "➕ImportTrackerTranscriptSummary"):   "🧑‍🎓",
    ("ZWJ", "➕StudentSchoolHistory"):             "🧑‍🎓",

    # 🧑‍🏫 Staff [U]
    ("ZWJ", "Staff"):                            "🧑‍🏫",

    # 👨‍👩‍👦 Contacts (family) [U]
    # In DDR: ??‍??‍🌐 -> first ??‍?? matched here, ‍🌐 stays
    ("ZWJ", "🌎"):                               "👨‍👩‍👦",
    ("ZWJ", "🌐"):                               "👨‍👩‍👦",
    ("ZWJ", "\u200d🌐"):                         "👨‍👩‍👦",
    ("ZWJ", "\u200d🌎"):                         "👨‍👩‍👦",

    # ===== SINGLE (??) — RELATIONSHIP / TO CONTEXT =====

    # 🌎 Globe Americas — Globals-anchored TO prefix [U]
    ("SINGLE", "(START)"):                       "🌎",

    # 📚 CourseCatalog [S]
    ("SINGLE", "CourseCatalog"):                 "📚",

    # 📅 Attendance [S]
    ("SINGLE", "Attendance"):                    "📅",
    ("SINGLE", "AttendanceWrapup"):              "📅",

    # 📗 Green book [U]
    ("SINGLE", "LookupCourseName"):              "📗",
    ("SINGLE", "LookupNCESCodeFromCourseName"):  "📗",
    ("SINGLE", "LookupNWEAGradeLevel"):          "📗",
    ("SINGLE", "Support"):                       "📗",
    ("SINGLE", "DirectCertResults"):             "📗",
    ("SINGLE", "Intake"):                        "📗",
    ("SINGLE", "AdultSupports"):                 "📗",
    ("SINGLE", "Assessment"):                    "📗",

    # 📊 Charts / analytics [U]
    ("SINGLE", "Charts"):                        "📊",
    ("SINGLE", "AccountabilityData"):            "📊",
    ("SINGLE", "PhasesDESE2022"):                "📊",
    ("SINGLE", "PhasesDESEEngagement"):          "📊",
    ("SINGLE", "Phase"):                         "📊",
    ("SINGLE", "Reports"):                       "📊",      # [U] 📊Reports

    # 💬 Touchpoint [I]
    ("SINGLE", "Touchpoint"):                    "💬",
    ("SINGLE", "PastEngagements"):               "💬",

    # 🔗 Join tables [I]
    ("SINGLE", "JoinTouchpoint"):                "🔗",
    ("SINGLE", "JoinPartnersCareerPathwayStudent"): "🔗",

    # 🥕 FoodPantry [U] — same in relationships AND scripts
    ("SINGLE", "SignInFoodPantry"):              "🥕",
    ("SINGLE", "FoodPantry"):                    "🥕",

    # 📋 Task [R]
    ("SINGLE", "Task"):                          "📋",

    # 👥 Contact [R]
    ("SINGLE", "Contact"):                       "👥",

    # 🌐 Globals [S]
    ("SINGLE", "Globals"):                       "🌐",

    # 📚 ImportNCESCodesEPIMs [U]
    ("SINGLE", "ImportNCESCodesEPIMs"):          "📚",

    # 🎓 GradPath / GradRundown [U]
    ("SINGLE", "GradPathProposedClasses"):       "🎓",
    ("SINGLE", "GradpathProposedClasses"):       "🎓",
    ("SINGLE", "GradpathPropsedClasses"):        "🎓",
    ("SINGLE", "GradPathReviewNotes"):           "🎓",
    ("SINGLE", "GradRundown"):                   "🎓",

    # 🏢 ContactOrganization [R]
    ("SINGLE", "ContactOrganization"):           "🏢",

    # Entity refs in TO chains
    ("SINGLE", "Student"):                       "🧑‍🎓",
    ("SINGLE", "Staff"):                         "🧑‍🏫",

    # Surviving emoji combos (base prefix before surviving emoji)
    ("SINGLE", "⚙️Settings"):                    "🌎",
    ("SINGLE", "✅SignInSignOut"):                 "🌎",
    ("SINGLE", "➕ImportAttendance"):              "📅",

    # ===== SINGLE (??) — SCRIPT / LAYOUT CONTEXT =====

    # 📤 Email/outbox [U]
    ("SINGLE", "Email"):                         "📤",

    # 🖨️ Print [U]
    ("SINGLE", "Print"):                         "🖨️",
    ("SINGLE", "Printout"):                      "🖨️",

    # 🌐 Server / automated scripts [U] — "anything - 🌐 is globe"
    ("SINGLE", "Server"):                        "🌐",
    ("SINGLE", "RunOnPaceReport"):               "🌐",
    ("SINGLE", "RunOnServer"):                   "🌐",      # suffix: Nightly🌐_RunOnServer
    ("SINGLE", "FTPImport"):                     "🌐",
    ("SINGLE", "CombinedAttendanceExportCache"): "🌐",
    ("SINGLE", "Checkin"):                       "🌐",
    ("SINGLE", "SignIn"):                        "🌐",      # ✅🌐SignIn_LogStatusOnServer
    ("SINGLE", "Cache"):                         "🌐",
    ("SINGLE", "Nightly"):                       "🌐",

    # 🪳 Debug / Dev [U]
    ("SINGLE", "Debug"):                         "🪳",
    ("SINGLE", "Dev"):                           "🪳",
    ("SINGLE", "Optimizations"):                 "🪳",
    ("SINGLE", "Errors"):                        "🪳",
    ("SINGLE", "ErrorCodeLookups"):              "🪳",

    # 📊 Touchpoints folder in scripts [U] — same chart icon as Reports
    ("SINGLE", "Touchpoints"):                   "📊",
}

FALLBACK_EMOJI = "🚫"


def _get_entity_word(following_text):
    """Extract the first entity word after an emoji pattern."""
    clean = following_text.lstrip('_')
    if not clean:
        return "(START)"
    word = re.split(r'(?<=[A-Za-z0-9])_|(?<=[A-Za-z0-9])#', clean)[0] if clean else "(START)"
    return word if word else "(START)"


def restore_emoji(text):
    """Restore garbled emoji using context-based mapping."""

    def replace_match(m):
        emoji_part = m.group(1)
        following = m.group(2) or ""

        is_zwj = '\u200d' in emoji_part
        etype = "ZWJ" if is_zwj else "SINGLE"
        entity = _get_entity_word(following)

        # Handle surviving emoji at start of entity (✅, ⚙️, etc.)
        replacement = EMOJI_MAP.get((etype, entity))
        if replacement is None and entity and entity[0] in '✅⚙➕⏱⚠✖🌐🌎':
            stripped = entity.lstrip('✅⚙️➕⏱️⚠️✖️🌐🌎\ufe0f')
            if stripped:
                replacement = EMOJI_MAP.get((etype, stripped))

        # Partial-match fallback
        if replacement is None:
            for (t, e), emoji in EMOJI_MAP.items():
                if t == etype and e != "(START)" and len(e) > 2 and entity.startswith(e):
                    replacement = emoji
                    break

        if replacement is None:
            replacement = FALLBACK_EMOJI

        return replacement + following

    result = re.sub(
        r'(\?\?(?:\u200d\?\?)?)'
        r'[\ufe0e\ufe0f]?'
        r'((?:_)?[\w⚙️✅✖️➕🌐🌎🔗🧑🎓💬📤📚📗📊📅📋👥🏢🥕🪳🖨️\u200d\ufe0f]*)?',
        replace_match,
        text
    )

    # Fix leftover 🚫 from previous restore runs (e.g. 🚫‍🌎 -> 👨‍👩‍👦‍🌎)
    result = result.replace('🚫\u200d🌎', '👨\u200d👩\u200d👦\u200d🌎')
    result = result.replace('🚫\u200d🌐', '👨\u200d👩\u200d👦\u200d🌐')

    return result


def restore_emoji_in_file(filepath, output_path=None):
    """Restore emoji in a file. Returns (replacements, remaining_unknowns)."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    restored = restore_emoji(content)
    out = output_path or filepath
    with open(out, 'w', encoding='utf-8') as f:
        f.write(restored)

    original_qq = len(re.findall(r'\?\?', content))
    restored_qq = len(re.findall(r'\?\?', restored))
    replacements = (original_qq - restored_qq) // 2
    remaining = restored.count('🚫')

    return replacements, remaining


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python fm_emoji_restore.py <file> [output]")
        print("Unresolvable patterns become 🚫")
        sys.exit(1)
    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    replacements, remaining = restore_emoji_in_file(input_file, output_file)
    out_name = output_file or input_file
    print(f"Restored {replacements} emoji in {out_name}")
    if remaining:
        print(f"  ⚠️  {remaining} unresolved (🚫)")
    else:
        print(f"  ✅ All resolved!")
