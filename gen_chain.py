#!/usr/bin/env python3
"""
gen_chain.py — MediaServer OCR chain generator
===============================================
Reads a YAML document-type definition and:
  1. Generates Lua scripts (filter, per-field compute_dynamic_region, draw overlay)
  2. Patches Kapish_debug.cfg  (Filter → Combine → DynRegion → Draw → Save → OCR)
  3. Patches Kapish.cfg        (Filter → Combine → DynRegion → OCR; no Draw/Save)
  4. Patches transformed_combined.xsl  (adds <xsl:if> block for the new identifier)
  5. Patches unit test                 (adds field entries to _DOC_FIELDS)

Usage:
  python3 tools/gen_chain.py definitions/wa_dl.yaml
  python3 tools/gen_chain.py definitions/wa_dl.yaml --dry-run
  python3 tools/gen_chain.py definitions/wa_dl.yaml --force
  python3 tools/gen_chain.py --list
  python3 tools/gen_chain.py --audit
"""

import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required.  Install with:  pip3 install pyyaml")


# ---------------------------------------------------------------------------
# Default path resolution  (relative to this script's location)
# ---------------------------------------------------------------------------

TOOLS_DIR  = Path(__file__).resolve().parent
PROJ_DIR   = TOOLS_DIR.parent
CONFIG_DIR    = PROJ_DIR / "mediaserver" / "MediaServer_26.2.0_LINUX_X86_64" / "configurations"
LUA_DIR       = CONFIG_DIR / "lua"
DEBUG_CFG     = CONFIG_DIR / "Kapish_debug.cfg"
PLAIN_CFG     = CONFIG_DIR / "Kapish.cfg"
XSL_PATH      = CONFIG_DIR / "xsl" / "transformed_combined.xsl"
UNITTEST_PATH = PROJ_DIR / "code" / "unittestforOCRProdV2.py"


# ===========================================================================
#  XSL / unit-test helpers
# ===========================================================================

def _next_combine_input(cfg_path: Path) -> int:
    """Return what the NEXT InputN number will be in [CombineOCRResults]."""
    if not cfg_path.exists():
        return 5  # safe fallback
    text = cfg_path.read_text(encoding="utf-8")
    combine_pos = text.find("\n[CombineOCRResults]")
    if combine_pos == -1:
        return 5
    luascript_pos = text.find("LuaScript", combine_pos)
    if luascript_pos == -1:
        return 5
    block = text[combine_pos:luascript_pos]
    existing = [int(x) for x in re.findall(r"Input(\d+)", block)]
    return (max(existing) + 1) if existing else 0


def _find_ocr_input_pin(cfg_path: Path, identifier: str, fields: list):
    """Return the inputPin of the first OCR engine for this chain, or None."""
    if not cfg_path.exists():
        return None
    text = cfg_path.read_text(encoding="utf-8")
    combine_pos = text.find("\n[CombineOCRResults]")
    if combine_pos == -1:
        return None
    luascript_pos = text.find("LuaScript", combine_pos)
    if luascript_pos == -1:
        return None
    block = text[combine_pos:luascript_pos]
    first_field = fields[0]["name"]
    engine = f"OCR_{identifier}_{first_field}.Result"
    m = re.search(r"Input(\d+)\s*=\s*" + re.escape(engine), block)
    return int(m.group(1)) if m else None


def _xsl_if_block(identifier: str, fields: list, starting_pin: int) -> str:
    """Build the <xsl:if> block for the XSL template.

    Uses (//ObjectRecognitionResult)[1] to restrict the condition to the
    primary (highest-confidence) detection only, avoiding false matches when
    ObjectRecognition returns multiple results for the same image.

    Uses trackname-based record selection instead of inputPin, which shifts
    unpredictably when ObjectRecognition returns more than one result.
    """
    lines = [
        f"                    <!-- {identifier} -->",
        f"                    <xsl:if test=\"(//ObjectRecognitionResult)[1]/identity/identifier = '{identifier}'\">",
    ]
    for i, f in enumerate(fields):
        engine_trackname = f"OCR_{identifier}_{f['name']}.Result"
        xml_num  = f"{identifier}_{f['name'].upper()}"
        xml_conf = f"{identifier}_CONFIDENCE"
        lines += [
            f"                        <{xml_num}>",
            f"                            <xsl:value-of select=\"//record[trackname='{engine_trackname}']/OCRResult/text\"/>",
            f"                        </{xml_num}>",
        ]
        if i == 0:  # emit confidence once (first field)
            lines += [
                f"                        <{xml_conf}>",
                f"                            <xsl:value-of select=\"//record[trackname='{engine_trackname}']/OCRResult/confidence\"/>",
                f"                        </{xml_conf}>",
            ]
    lines.append("                    </xsl:if>")
    lines.append("")
    return "\n".join(lines)


# ===========================================================================
#  Lua generation
# ===========================================================================

def lua_filter(identifier: str) -> str:
    """Generate filter_<id_lower>.lua — pred() that matches one identifier."""
    id_low = identifier.lower()
    return (
        f"-- filter_{id_low}.lua\n"
        f"-- Auto-generated by gen_chain.py.  Edit the YAML and regenerate.\n"
        f"function pred(rec)\n"
        f"    local obj = rec.ObjectRecognitionResult or rec.ObjectRecognitionResultAndImage\n"
        f"    if not obj then\n"
        f'        log("filter_{id_low}: no ObjectRecognitionResult, skipping")\n'
        f"        return false\n"
        f"    end\n"
        f'    local ident = obj.identity and obj.identity.identifier or obj.name or "unknown"\n'
        f'    log("filter_{id_low}: identifier=\'" .. tostring(ident) .. "\'")\n'
        f'    local matched = ident == "{identifier}"\n'
        f'    if matched then\n'
        f'        log("filter_{id_low}: PASSED ({identifier})")\n'
        f'    else\n'
        f'        log("filter_{id_low}: REJECTED")\n'
        f'    end\n'
        f"    return matched\n"
        f"end\n"
    )


def lua_compute(identifier: str, field: dict, card: dict) -> str:
    """Generate compute_dynamic_region_<id>_<field_lower>.lua — rectangle() function."""
    id_low  = identifier.lower()
    fname   = field["name"]
    f_low   = fname.lower()
    desc    = field.get("description", fname)
    roi     = field["roi"]

    lw_f  = float(card.get("width_factor",       1.0))
    lh_f  = float(card.get("height_factor",      1.0))
    l_off = float(card.get("left_offset_factor", 0.0))
    t_off = float(card.get("top_offset_factor",  0.0))

    # Format floats without unnecessary trailing zeros
    def fmt(v):
        return f"{v:g}"

    if lw_f == 1.0 and lh_f == 1.0 and l_off == 0.0 and t_off == 0.0:
        card_note = "    -- Model detects the full card; bounding box IS the card."
    else:
        card_note = (
            "    -- Model detects a partial region (e.g. a header banner).\n"
            "    -- card_estimation factors extrapolate to the full card dimensions.\n"
            "    -- Verify by checking the debug pre_ocr PNG after first test run."
        )

    lines = [
        f"-- compute_dynamic_region_{id_low}_{f_low}.lua",
        f"-- Auto-generated by gen_chain.py",
        f"-- OCR region for {identifier} / {fname}: {desc}",
        "",
        "function rectangle(r)",
        f'    log("=== {identifier} {fname} Rectangle ===")',
        "",
        "    local result = r.ObjectRecognitionResult",
        "                or r.ObjectRecognitionResultAndImage",
        "                or r.ObjectRecognitionResultAndSource",
        "    if not result or not result.boundary or not result.boundary.point then",
        '        log("ERROR: no boundary found")',
        "        return nil",
        "    end",
        "",
        '    local ident = result.identity and result.identity.identifier or "unknown"',
        f'    if ident ~= "{identifier}" then',
        f'        log("Not {identifier}, skipping: " .. tostring(ident))',
        "        return nil",
        "    end",
        "",
        "    local pts = result.boundary.point",
        "    local min_x, min_y =  1e9,  1e9",
        "    local max_x, max_y = -1e9, -1e9",
        "    for _, p in ipairs(pts) do",
        "        min_x = math.min(min_x, p.x);  max_x = math.max(max_x, p.x)",
        "        min_y = math.min(min_y, p.y);  max_y = math.max(max_y, p.y)",
        "    end",
        "    local lw = max_x - min_x",
        "    local lh = max_y - min_y",
        "",
        card_note,
        f"    local card_left   = min_x + lw * {fmt(l_off)}",
        f"    local card_top    = min_y + lh * {fmt(t_off)}",
        f"    local card_width  = lw   * {fmt(lw_f)}",
        f"    local card_height = lh   * {fmt(lh_f)}",
        '    log(string.format("[Card] L:%.0f T:%.0f W:%.0f H:%.0f",',
        "        card_left, card_top, card_width, card_height))",
        "",
        f"    -- === {fname} ROI ===",
        "    -- Fractions are relative to the full card bounding box.",
        "    -- TODO: tune after first test run — check the debug pre_ocr PNG.",
        "    local roi = {",
        f"        left   = math.floor(card_left + card_width  * {fmt(roi['left'])}),",
        f"        top    = math.floor(card_top  + card_height * {fmt(roi['top'])}),",
        f"        width  = math.floor(card_width  * {fmt(roi['width'])}),",
        f"        height = math.floor(card_height * {fmt(roi['height'])}),",
        f'        label  = "{identifier}_{fname}"',
        "    }",
        "    if roi.left < 0 then roi.left = 0 end",
        "    if roi.top  < 0 then roi.top  = 0 end",
        f'    log(string.format("[ROI] {fname} -> L:%d T:%d W:%d H:%d",',
        "        roi.left, roi.top, roi.width, roi.height))",
        "    return { roi }",
        "end",
        "",
    ]
    return "\n".join(lines)


def lua_draw(identifier: str, draw_cfg: dict) -> str:
    """Generate draw/draw_<id_lower>.lua — draw() overlay for debug images."""
    id_low = identifier.lower()
    r1, g1, b1 = draw_cfg.get("roi_color",      [255, 165,   0])
    r2, g2, b2 = draw_cfg.get("boundary_color", [  0,   0, 160])
    # Darker variants for text backgrounds
    r1d, g1d, b1d = max(0, r1 - 50), max(0, g1 - 50), max(0, b1 - 50)
    r2d, g2d, b2d = max(0, r2 - 40), max(0, g2 - 40), max(0, b2 - 40)
    fallback = draw_cfg.get("label_fallback", (id_low.replace("_", "")[:3].upper() + "R"))

    lines = [
        f"-- draw_{id_low}.lua",
        f"-- Auto-generated by gen_chain.py",
        f"-- Debug draw overlay for {identifier}",
        "",
        "function draw(record)",
        "    -- 1. ROI rectangle",
        "    local rect = record.RectangleData or record.RegionData",
        "    if rect then",
        "        drawRectangle(",
        "            {left = math.floor(rect.left), top = math.floor(rect.top),",
        "             width = math.floor(rect.width), height = math.floor(rect.height)},",
        f"            4, rgb({r1}, {g1}, {b1}))",
        '        local lbl = (type(rect.label) == "string" and rect.label) or ' + f'"{fallback}"',
        "        drawText(lbl,",
        "            math.max(5, math.floor(rect.left)),",
        "            math.max(25, math.floor(rect.top) - 30),",
        f'            "DejaVuSans", 20, rgb(255, 255, 255), rgb({r1}, {g1}, {b1}))',
        "    end",
        "",
        "    -- 2. ObjectRecognition boundary polygon",
        "    local obj = record.ObjectRecognitionResultAndImage or record.ObjectRecognitionResult",
        "    if obj and obj.boundary and obj.boundary.point then",
        "        local pts = obj.boundary.point",
        "        for i = 1, #pts do",
        "            local p1 = pts[i]",
        "            local p2 = pts[(i % #pts) + 1]",
        "            drawLine(math.floor(p1.x), math.floor(p1.y),",
        "                     math.floor(p2.x), math.floor(p2.y),",
        f"                     2, rgb({r2}, {g2}, {b2}))",
        "        end",
        '        local ident = (obj.identity and obj.identity.identifier) or obj.name or '
        f'"{identifier}"',
        "        local conf  = (obj.identity and obj.identity.confidence) or 0",
        '        drawText(string.format("%s %.0f%%", ident, conf),',
        "            math.max(5, math.floor(pts[1].x)),",
        "            math.max(25, math.floor(pts[1].y) - 30),",
        f'            "DejaVuSans", 20, rgb(255, 255, 255), rgb({r2d}, {g2d}, {b2d}))',
        "    end",
        "",
        "    -- 3. OCR result overlay",
        "    if record.OCRResult and rect then",
        '        drawText("OCR: " .. (record.OCRResult.text or ""),',
        "            math.max(5, math.floor(rect.left)),",
        "            math.max(25, math.floor(rect.top + rect.height + 5)),",
        f'            "DejaVuSans", 20, rgb(255, 255, 255), rgb({r1d}, {g1d}, {b1d}))',
        "    end",
        "end",
        "",
    ]
    return "\n".join(lines)


# ===========================================================================
#  CFG helpers
# ===========================================================================

def get_combine_inputs(text: str) -> set:
    """Return the set of result-track values wired into [CombineOCRResults]."""
    combine_pos = text.find("\n[CombineOCRResults]")
    if combine_pos == -1:
        return set()
    luascript_pos = text.find("LuaScript", combine_pos)
    end = luascript_pos if luascript_pos != -1 else len(text)
    block = text[combine_pos:end]
    return set(re.findall(r"Input\d+\s*=\s*(\S+)", block))


def get_session_engines(text: str) -> list:
    """Return the ordered list of engine names from the [Session] block."""
    m = re.search(r'\[Session\][^\[]*', text, re.DOTALL)
    if not m:
        return []
    return re.findall(r'Engine\d+\s*=\s*(\S+)', m.group())


def patch_session(text: str, new_names: list, before: str = "CombineOCRResults") -> str:
    """
    Insert new_names before `before` in the [Session] engine list and renumber all.
    The [Session] block must contain only Engine<N> = <Name> lines (no interspersed comments).
    """
    def _replace(m):
        existing = re.findall(r'Engine\d+\s*=\s*(\S+)', m.group())
        if before not in existing:
            return m.group()  # nothing to do
        idx = existing.index(before)
        merged = existing[:idx] + new_names + existing[idx:]
        lines = "\n".join(f"Engine{i:<2} = {n}" for i, n in enumerate(merged))
        return "[Session]\n" + lines + "\n"

    result = re.sub(
        r'\[Session\]\s*\n(?:[ \t]*Engine\d+[ \t]*=[ \t]*\S+[ \t]*\n)+',
        _replace,
        text,
    )
    if result == text:
        raise ValueError(
            f"Session block not found or '{before}' not in engine list.\n"
            "Ensure the [Session] section contains only Engine<N> = <Name> lines."
        )
    return result


def insert_stanzas_before(text: str, stanzas: str, anchor: str = "CombineOCRResults") -> str:
    """Insert stanza block immediately before the [<anchor>] section."""
    marker = f"\n[{anchor}]"
    idx = text.find(marker)
    if idx == -1:
        raise ValueError(f"[{anchor}] not found in cfg file")
    return text[:idx] + "\n" + stanzas.rstrip("\n") + text[idx:]


def add_combine_inputs(text: str, new_results: list) -> str:
    """
    Append InputN lines to [CombineOCRResults], inserting before its LuaScript= line.
    """
    combine_pos = text.find("\n[CombineOCRResults]")
    if combine_pos == -1:
        raise ValueError("[CombineOCRResults] not found in cfg file")
    luascript_pos = text.find("LuaScript", combine_pos)
    if luascript_pos == -1:
        raise ValueError("LuaScript= not found inside [CombineOCRResults]")
    block = text[combine_pos:luascript_pos]
    existing_nums = [int(x) for x in re.findall(r'Input(\d+)', block)]
    next_n = (max(existing_nums) + 1) if existing_nums else 0
    new_lines = "".join(f"Input{next_n + i} = {r}\n" for i, r in enumerate(new_results))
    return text[:luascript_pos] + new_lines + text[luascript_pos:]


# ===========================================================================
#  Stanza builders
# ===========================================================================

def _ocr_block(ocr: dict) -> str:
    """Build the per-OCR-engine settings lines from an ocr dict."""
    parts = [f"Languages = {ocr.get('languages', 'en')}"]
    if "character_types" in ocr:
        parts.append(f"CharacterTypes = {ocr['character_types']}")
    if "word_reject_threshold" in ocr:
        parts.append(f"WordRejectThreshold = {ocr['word_reject_threshold']}")
    if "ocr_mode" in ocr:
        parts.append(f"OCRMode = {ocr['ocr_mode']}")
    if ocr.get("process_text_elements"):
        parts.append("ProcessTextElements = True")
    return "\n".join(parts)


def build_stanzas(identifier: str, fields: list, id_low: str, debug: bool) -> str:
    """
    Return the cfg stanza block for all engines in the chain.
    debug=True adds Draw + Save engines; debug=False omits them.
    """
    parts = []

    parts.append(
        f"[Filter_{identifier}]\n"
        f"Type = filter\n"
        f"Input = ObjectRecognition.Result\n"
        f"LuaScript = filters/filter_{id_low}.lua\n"
    )
    parts.append(
        f"[Combine_{identifier}]\n"
        f"Type = combine\n"
        f"Input0 = RotateTask.Output\n"
        f"Input1 = Filter_{identifier}.Output\n"
    )

    for f in fields:
        fname = f["name"]
        f_low = fname.lower()
        ocr   = f.get("ocr", {})
        stem  = f"{id_low}_{f_low}"
        # Allow YAML to override the compute Lua script name (e.g. for hand-written scripts
        # that predate gen_chain.py or use custom logic not expressible by the template).
        lua_script = f.get("lua_script", f"compute_dynamic_region_{id_low}_{f_low}.lua")

        parts.append(
            f"[DynamicRegion_{identifier}_{fname}]\n"
            f"Type = setrectangle\n"
            f"Input = Combine_{identifier}.Output\n"
            f"LuaScript = {lua_script}\n"
        )

        if debug:
            parts.append(
                f"[Draw_PreOCR_{identifier}_{fname}]\n"
                f"Type = draw\n"
                f"Input = DynamicRegion_{identifier}_{fname}.Output\n"
                f"LuaScript = draw/draw_{id_low}.lua\n"
            )
            parts.append(
                f"[SavePreOCR_{identifier}_{fname}]\n"
                f"Type = imageencoder\n"
                f"ImageInput = Draw_PreOCR_{identifier}_{fname}.Output\n"
                f"OutputPath = output/debug/%source.filename.stem%_{stem}_pre_ocr.png\n"
            )
            ocr_input = f"Draw_PreOCR_{identifier}_{fname}.Output"
        else:
            ocr_input = f"DynamicRegion_{identifier}_{fname}.Output"

        parts.append(
            f"[OCR_{identifier}_{fname}]\n"
            f"Type = OCR\n"
            f"Input = {ocr_input}\n"
            f"Region = Input\n"
            f"{_ocr_block(ocr)}\n"
        )

    return "\n".join(parts)


def session_engine_names(identifier: str, fields: list, debug: bool) -> list:
    """Return the ordered engine names that go into [Session] for this chain."""
    names = [f"Filter_{identifier}", f"Combine_{identifier}"]
    for f in fields:
        fname = f["name"]
        names.append(f"DynamicRegion_{identifier}_{fname}")
        if debug:
            names.append(f"Draw_PreOCR_{identifier}_{fname}")
            names.append(f"SavePreOCR_{identifier}_{fname}")
        names.append(f"OCR_{identifier}_{fname}")
    return names


# ===========================================================================
#  Chain builder
# ===========================================================================

class ChainBuilder:
    def __init__(self, defn: dict, config_dir: Path, dry_run: bool, force: bool):
        self.defn       = defn
        self.config_dir = config_dir
        self.lua_dir    = config_dir / "lua"
        self.dry_run    = dry_run
        self.force      = force

        self.identifier = defn["identifier"]
        self.id_low     = self.identifier.lower()
        self.fields     = defn.get("fields", [])
        self.card       = defn.get("card_estimation", {})
        self.draw_cfg   = defn.get("draw", {})

    # -------------------------------------------------------------------------

    def _rel(self, path: Path) -> str:
        """Return a display-friendly relative path."""
        try:
            return str(path.relative_to(PROJ_DIR))
        except ValueError:
            return str(path)

    def _write_lua(self, path: Path, content: str):
        tag = "[DRY-RUN] " if self.dry_run else ""
        if path.exists() and not self.force:
            print(f"  SKIP (exists)   {self._rel(path)}")
            return
        if self.dry_run:
            print(f"  {tag}WRITE       {self._rel(path)}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            print(f"  WROTE           {self._rel(path)}")

    def _patch_cfg(self, cfg_path: Path, debug: bool):
        label = cfg_path.name
        if not cfg_path.exists():
            print(f"  SKIP (not found)  {label}")
            return

        text = cfg_path.read_text(encoding="utf-8")

        # Guard against duplicates
        if re.search(rf'Engine\d+\s*=\s*Filter_{re.escape(self.identifier)}\b', text):
            print(f"  SKIP (already present)  {label}  →  Filter_{self.identifier} exists")
            return

        identifier = self.identifier
        id_low     = self.id_low
        fields     = self.fields

        try:
            text = patch_session(text, session_engine_names(identifier, fields, debug))
            text = insert_stanzas_before(text, build_stanzas(identifier, fields, id_low, debug))
            ocr_results = [f"OCR_{identifier}_{f['name']}.Result" for f in fields]
            text = add_combine_inputs(text, ocr_results)
        except ValueError as exc:
            print(f"  ERROR  {label}: {exc}")
            return

        if self.dry_run:
            print(f"  [DRY-RUN] PATCH   {label}")
        else:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = cfg_path.with_suffix(f".{ts}.bak")
            shutil.copy2(cfg_path, backup)
            print(f"  BACKUP            {self._rel(backup)}")
            cfg_path.write_text(text, encoding="utf-8")
            print(f"  PATCHED           {label}")

    def _patch_xsl(self, xsl_path: Path, starting_pin: int):
        label = xsl_path.name
        if not xsl_path.exists():
            print(f"  SKIP (not found)  {label}")
            return

        text = xsl_path.read_text(encoding="utf-8")

        if f"'{self.identifier}'" in text:
            print(f"  SKIP (already present)  {label}  →  {self.identifier} exists")
            return

        anchor = "<!-- Metadata -->"
        idx = text.find(anchor)
        if idx == -1:
            print(f"  ERROR  {label}: anchor '<!-- Metadata -->' not found")
            return

        block = _xsl_if_block(self.identifier, self.fields, starting_pin)
        new_text = text[:idx] + block + "\n" + text[idx:]

        if self.dry_run:
            print(f"  [DRY-RUN] PATCH   {label}")
            for f in self.fields:
                pin = starting_pin + self.fields.index(f)
                print(f"    {self.identifier}_{f['name'].upper()} @ inputPin={pin}")
        else:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = xsl_path.with_suffix(f".{ts}.bak")
            shutil.copy2(xsl_path, backup)
            print(f"  BACKUP            {self._rel(backup)}")
            xsl_path.write_text(new_text, encoding="utf-8")
            print(f"  PATCHED           {label}")

    def _patch_unittest(self, test_path: Path):
        label = test_path.name
        if not test_path.exists():
            print(f"  SKIP (not found)  {label}")
            return

        text = test_path.read_text(encoding="utf-8")

        # Guard: check if any XML element for this identifier is already present
        if f"'{self.identifier}_" in text:
            print(f"  SKIP (already present)  {label}  →  {self.identifier} exists")
            return

        sentinel = "    # [GEN_CHAIN_INSERT]"
        if sentinel not in text:
            print(f"  ERROR  {label}: sentinel comment '# [GEN_CHAIN_INSERT]' not found in _DOC_FIELDS")
            return

        new_lines = []
        for f in self.fields:
            xml_num  = f"{self.identifier}_{f['name'].upper()}"
            xml_conf = f"{self.identifier}_CONFIDENCE"
            new_lines.append(f"    ('{xml_num}', '{xml_conf}'),")
        new_entry = "\n".join(new_lines) + "\n"

        if self.dry_run:
            print(f"  [DRY-RUN] PATCH   {label}")
            for line in new_lines:
                print(f"    {line.strip()}")
        else:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = test_path.with_suffix(f".{ts}.bak")
            shutil.copy2(test_path, backup)
            print(f"  BACKUP            {self._rel(backup)}")
            test_path.write_text(text.replace(sentinel, new_entry + sentinel), encoding="utf-8")
            print(f"  PATCHED           {label}")

    # -------------------------------------------------------------------------

    def run(self):
        identifier = self.identifier
        id_low     = self.id_low
        fields     = self.fields

        print(f"\n{'='*62}")
        print(f"  Chain: {identifier}  |  Fields: {[f['name'] for f in fields]}")
        print(f"  Dry-run: {self.dry_run}  |  Force overwrite: {self.force}")
        print(f"{'='*62}\n")

        # -- Lua files ---------------------------------------------------------
        print("Lua files:")

        self._write_lua(
            self.lua_dir / "filters" / f"filter_{id_low}.lua",
            lua_filter(identifier),
        )
        for f in fields:
            if "lua_script" in f:
                # Hand-written / custom script — skip generation, just note the override.
                print(f"  SKIP (lua_script override)  {f['lua_script']}")
            else:
                self._write_lua(
                    self.lua_dir / f"compute_dynamic_region_{id_low}_{f['name'].lower()}.lua",
                    lua_compute(identifier, f, self.card),
                )
        self._write_lua(
            self.lua_dir / "draw" / f"draw_{id_low}.lua",
            lua_draw(identifier, self.draw_cfg),
        )

        # -- Determine inputPin before patching (pin changes once CFG is patched) -
        plain_cfg = self.config_dir / "Kapish.cfg"
        starting_pin = _find_ocr_input_pin(plain_cfg, identifier, fields)
        if starting_pin is None:
            starting_pin = _next_combine_input(plain_cfg)

        # -- CFG files ---------------------------------------------------------
        print("\nCFG files:")
        self._patch_cfg(self.config_dir / "Kapish_debug.cfg", debug=True)
        self._patch_cfg(self.config_dir / "Kapish.cfg",       debug=False)

        # -- XSL file ----------------------------------------------------------
        print("\nXSL file:")
        xsl_path = self.config_dir / "xsl" / "transformed_combined.xsl"
        self._patch_xsl(xsl_path, starting_pin)

        # -- Unit test ---------------------------------------------------------
        print("\nUnit test:")
        self._patch_unittest(UNITTEST_PATH)

        # -- Summary -----------------------------------------------------------
        print()
        if not self.dry_run:
            print("Next steps:")
            print(f"  1. Restart MediaServer and process a {identifier} sample image.")
            for f in fields:
                stem = f"{id_low}_{f['name'].lower()}"
                print(f"  2. Check  output/debug/*_{stem}_pre_ocr.png")
                print(f"     Tune   lua/compute_dynamic_region_{id_low}_{f['name'].lower()}.lua")
            print("     (adjust left/top/width/height fractions until the box is correct)")


# ===========================================================================
#  Validation
# ===========================================================================

REQUIRED_DEFINITION_KEYS = ("identifier", "card_estimation", "fields")
REQUIRED_FIELD_KEYS      = ("name", "roi")
REQUIRED_ROI_KEYS        = ("left", "top", "width", "height")


def validate(defn: dict, path: Path):
    for k in REQUIRED_DEFINITION_KEYS:
        if k not in defn:
            sys.exit(f"ERROR: Missing required key '{k}' in {path}")
    if not defn["fields"]:
        sys.exit(f"ERROR: 'fields' list is empty in {path}")
    for f in defn["fields"]:
        for k in REQUIRED_FIELD_KEYS:
            if k not in f:
                sys.exit(f"ERROR: Field entry missing '{k}': {f}  ({path})")
        for k in REQUIRED_ROI_KEYS:
            if k not in f["roi"]:
                sys.exit(f"ERROR: Field '{f['name']}' roi missing '{k}'  ({path})")
        for k in REQUIRED_ROI_KEYS:
            v = f["roi"][k]
            if not (0.0 <= float(v) <= 2.0):
                sys.exit(f"ERROR: Field '{f['name']}' roi.{k}={v} looks wrong (expect 0–1)  ({path})")


# ===========================================================================
#  CLI commands
# ===========================================================================

def cmd_list(config_dir: Path):
    cfg_path = config_dir / "Kapish_debug.cfg"
    if not cfg_path.exists():
        sys.exit(f"Not found: {cfg_path}")
    engines = get_session_engines(cfg_path.read_text(encoding="utf-8"))
    print(f"\nEngines currently in {cfg_path.name}:\n")
    for i, name in enumerate(engines):
        print(f"  Engine{i:<2} = {name}")
    print()


def cmd_audit(config_dir: Path, defs_dir: Path):
    """
    Cross-check every YAML definition against the deployed configuration.

    For each definition the following artifacts are verified:
      Lua    filter · compute (per field) · draw
      CFG    Kapish.cfg       — session engines + [CombineOCRResults] wiring
      CFG    Kapish_debug.cfg — session engines + [CombineOCRResults] wiring
      XSL    transformed_combined.xsl — <xsl:if> block present
      Test   unittestforOCRProdV2.py  — _DOC_FIELDS entry present
    """
    TICK  = "\u2713"
    CROSS = "\u2717"
    SEP   = "=" * 72

    def_files = sorted(p for p in defs_dir.glob("*.yaml") if p.name != "template.yaml")
    if not def_files:
        sys.exit(f"No definition files found in {defs_dir}")

    lua_dir        = config_dir / "lua"
    plain_cfg_path = config_dir / "Kapish.cfg"
    debug_cfg_path = config_dir / "Kapish_debug.cfg"
    xsl_path       = config_dir / "xsl" / "transformed_combined.xsl"

    plain_cfg  = plain_cfg_path.read_text(encoding="utf-8")  if plain_cfg_path.exists()  else ""
    debug_cfg  = debug_cfg_path.read_text(encoding="utf-8")  if debug_cfg_path.exists()  else ""
    xsl_text   = xsl_path.read_text(encoding="utf-8")        if xsl_path.exists()        else ""
    test_text  = UNITTEST_PATH.read_text(encoding="utf-8")   if UNITTEST_PATH.exists()   else ""

    plain_session  = set(get_session_engines(plain_cfg))
    debug_session  = set(get_session_engines(debug_cfg))
    plain_combine  = get_combine_inputs(plain_cfg)
    debug_combine  = get_combine_inputs(debug_cfg)

    total_checks = 0
    total_fails  = 0

    print(f"\nAudit  —  {len(def_files)} definitions in {defs_dir.name}/")
    print(SEP)

    for def_file in def_files:
        defn       = yaml.safe_load(def_file.read_text(encoding="utf-8"))
        identifier = defn.get("identifier", "?")
        id_low     = identifier.lower()
        fields     = defn.get("fields", [])

        issues = []

        def chk(ok: bool, msg: str) -> bool:
            nonlocal total_checks, total_fails
            total_checks += 1
            if not ok:
                total_fails += 1
                issues.append(msg)
            return ok

        # --- Lua scripts ---
        chk(
            (lua_dir / "filters" / f"filter_{id_low}.lua").exists(),
            f"Lua: filter_{id_low}.lua missing",
        )
        chk(
            (lua_dir / "draw" / f"draw_{id_low}.lua").exists(),
            f"Lua: draw_{id_low}.lua missing",
        )
        for f in fields:
            lua_name = f.get("lua_script",
                             f"compute_dynamic_region_{id_low}_{f['name'].lower()}.lua")
            override_note = "  [override]" if "lua_script" in f else ""
            chk(
                (lua_dir / lua_name).exists(),
                f"Lua: {lua_name} missing{override_note}",
            )

        # --- Kapish.cfg ---
        chk(
            f"Filter_{identifier}" in plain_session,
            f"Kapish.cfg [Session]: Filter_{identifier} missing",
        )
        for f in fields:
            fname  = f["name"]
            result = f"OCR_{identifier}_{fname}.Result"
            chk(
                f"DynamicRegion_{identifier}_{fname}" in plain_session,
                f"Kapish.cfg [Session]: DynamicRegion_{identifier}_{fname} missing",
            )
            chk(
                f"OCR_{identifier}_{fname}" in plain_session,
                f"Kapish.cfg [Session]: OCR_{identifier}_{fname} missing",
            )
            chk(
                result in plain_combine,
                f"Kapish.cfg [CombineOCRResults]: {result} missing",
            )

        # --- Kapish_debug.cfg ---
        chk(
            f"Filter_{identifier}" in debug_session,
            f"Kapish_debug.cfg [Session]: Filter_{identifier} missing",
        )
        for f in fields:
            fname  = f["name"]
            result = f"OCR_{identifier}_{fname}.Result"
            chk(
                f"DynamicRegion_{identifier}_{fname}" in debug_session,
                f"Kapish_debug.cfg [Session]: DynamicRegion_{identifier}_{fname} missing",
            )
            chk(
                f"OCR_{identifier}_{fname}" in debug_session,
                f"Kapish_debug.cfg [Session]: OCR_{identifier}_{fname} missing",
            )
            chk(
                result in debug_combine,
                f"Kapish_debug.cfg [CombineOCRResults]: {result} missing",
            )

        # --- XSL ---
        chk(
            f"'{identifier}'" in xsl_text,
            f"XSL: identifier '{identifier}' not found in transformed_combined.xsl",
        )

        # --- Unit test ---
        for f in fields:
            xml_num = f"{identifier}_{f['name'].upper()}"
            chk(
                f"'{xml_num}'" in test_text,
                f"Unit test: '{xml_num}' not found in _DOC_FIELDS",
            )

        # --- Print result ---
        n_fail  = len(issues)
        n_total = total_checks  # running total includes this definition's checks
        # compute this definition's count
        n_def   = 2 + len(fields) + 3 * len(fields) + 3 * len(fields) + 1 + len(fields)
        n_pass  = n_def - n_fail
        status  = f"{TICK} {n_pass}/{n_def}" if n_fail == 0 else f"{CROSS} {n_pass}/{n_def}"
        print(f"  {identifier:<22}  {status}")
        for msg in issues:
            print(f"      {CROSS}  {msg}")

    print(SEP)
    n_pass = total_checks - total_fails
    if total_fails == 0:
        print(f"  Summary: {n_pass}/{total_checks} checks passed  —  all good!")
    else:
        print(f"  Summary: {n_pass}/{total_checks} passed,  {total_fails} FAILED")
    print()


def cmd_generate(args):
    def_path = Path(args.definition)
    if not def_path.exists():
        sys.exit(f"Definition file not found: {def_path}")

    with open(def_path, encoding="utf-8") as fh:
        defn = yaml.safe_load(fh)

    validate(defn, def_path)

    config_dir = Path(args.config_dir) if args.config_dir else CONFIG_DIR

    ChainBuilder(
        defn=defn,
        config_dir=config_dir,
        dry_run=args.dry_run,
        force=args.force,
    ).run()


# ===========================================================================
#  Entry point
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        prog="gen_chain.py",
        description="Generate a MediaServer OCR extraction chain from a YAML definition.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ./gen_chain.py definitions/wa_dl.yaml           # generate new chain\n"
            "  ./gen_chain.py definitions/wa_dl.yaml --dry-run # preview changes\n"
            "  ./gen_chain.py definitions/wa_dl.yaml --force   # overwrite existing Lua\n"
            "  ./gen_chain.py --list                           # show deployed engines\n"
            "  ./gen_chain.py --audit                          # verify all definitions\n"
        ),
    )
    parser.add_argument(
        "definition", nargs="?",
        help="Path to a YAML chain definition file",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List all engines currently configured in Kapish_debug.cfg",
    )
    parser.add_argument(
        "--audit", action="store_true",
        help="Cross-check all YAML definitions against deployed Lua/CFG/XSL/test artifacts",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be generated/changed without writing any files",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite existing Lua files (default: skip if they already exist)",
    )
    parser.add_argument(
        "--config-dir", metavar="PATH",
        help=f"Override the configurations/ directory (default: {CONFIG_DIR})",
    )
    parser.add_argument(
        "--defs-dir", metavar="PATH",
        help=f"Override the definitions/ directory used by --audit (default: {TOOLS_DIR / 'definitions'})",
    )

    args = parser.parse_args()

    config_dir = Path(args.config_dir) if args.config_dir else CONFIG_DIR
    defs_dir   = Path(args.defs_dir)   if args.defs_dir   else TOOLS_DIR / "definitions"

    if args.audit:
        cmd_audit(config_dir, defs_dir)
    elif args.list:
        cmd_list(config_dir)
    elif args.definition:
        cmd_generate(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
