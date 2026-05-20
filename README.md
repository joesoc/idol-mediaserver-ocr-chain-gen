# OCR Chain Generator — `gen_chain.py`

Generates all the boilerplate needed to add a new document type (driver's
licence, passport, etc.) to the Knowledge Discovery (IDOL) MediaServer OCR pipeline.  A single YAML
definition file is all you need to write.

---

## What gets generated

For each YAML definition the tool produces:

| File | Purpose |
|---|---|
| `lua/filters/filter_<id>.lua` | Filters ObjectRecognition output to this document type only |
| `lua/compute_dynamic_region_<id>_<field>.lua` | Computes the pixel-level OCR region for one field |
| `lua/draw/draw_<id>.lua` | Debug overlay: draws the region box + detection boundary on the image |
| Patch `Kapish_debug.cfg` | Adds Filter → Combine → DynRegion → Draw → Save → OCR engine chain |
| Patch `Kapish.cfg` | Adds Filter → Combine → DynRegion → OCR engine chain (no debug draw) |

Both cfg files are updated atomically: Session engine numbers are renumbered
and a new `InputN` line is added to `CombineOCRResults`.

---

## Prerequisites

```bash
pip3 install pyyaml
```

Python 3.8+ required.

---

## Quick start

```bash
cd /home/ubuntu/projects/mediaserver

# 1. See what is already configured
python3 tools/gen_chain.py --list

# 2. Preview what would be generated (no files written)
python3 tools/gen_chain.py tools/definitions/wa_dl.yaml --dry-run

# 3. Generate for real
python3 tools/gen_chain.py tools/definitions/wa_dl.yaml

# 4. Restart MediaServer, process a sample image, inspect the debug PNG
#    output/debug/*_wa_dl_licence_number_pre_ocr.png
#
# 5. If the box is wrong, edit the lua/compute_dynamic_region_wa_dl_licence_number.lua
#    roi fractions and re-test.  When tuned, update the YAML to match.
```

---

## YAML definition reference

Copy `tools/definitions/template.yaml` as a starting point.

### `identifier`  *(required)*

The exact string returned by the ObjectRecognition Documents database for this
document type.  Case-sensitive.  Convention: `STATE_DOCTYPE`.

```yaml
identifier: WA_DL
```

---

### `description`  *(optional)*

Free-text name used only in Lua comments.

---

### `card_estimation`  *(required)*

Describes how the ObjectRecognition bounding box maps to the full card.

#### Full-card detection (e.g. QLD_DL)

When the model is trained on the whole card image, the bounding box **is** the
card.  Use identity values:

```yaml
card_estimation:
  left_offset_factor: 0.0
  top_offset_factor:  0.0
  width_factor:       1.0
  height_factor:      1.0
```

#### Banner detection (e.g. TAS_DL, VIC_DL, WA_DL)

When the model is trained on only the top banner strip, the engine log shows:

```
[VisionCore]: Documents/WA_DL/WA-DL.png The image has a large aspect ratio
```

The bounding box covers the banner; you must extrapolate to the full card.

**How to measure:**

1. Open the source card image in any image editor.
2. Measure the banner height in pixels and the total card height in pixels.
3. `height_factor = card_height_px / banner_height_px`
4. If the banner does **not** span the full card width, also adjust `width_factor`
   and `left_offset_factor`.

```yaml
# WA DL: banner ≈ 16% of card height, spans full width
card_estimation:
  left_offset_factor: 0.0
  top_offset_factor:  0.0
  width_factor:       1.0
  height_factor:      6.25     # = 1 / 0.16
```

```yaml
# TAS DL: banner ≈ 10% of card height, spans ~45% of card width
card_estimation:
  left_offset_factor: -0.185   # expand left edge outward
  top_offset_factor:   0.0
  width_factor:        2.2     # expand to full card width
  height_factor:       10.35
```

---

### `fields`  *(required, at least one)*

List of OCR target regions.  Each field produces its own engine chain.

```yaml
fields:
  - name: Licence_Number          # used verbatim in engine names
    description: "7-digit number"
    roi:
      left:   0.67   # fraction of card_width  from card_left
      top:    0.22   # fraction of card_height from card_top
      width:  0.31   # fraction of card_width
      height: 0.10   # fraction of card_height
    ocr:
      languages: en
      character_types: digit      # omit to allow all characters
      word_reject_threshold: 60
      ocr_mode: document
```

#### `name`

Used as-is in engine names, Lua labels, and output file names.  Use
`CamelCase_With_Underscores`.

#### `roi` fractions

All values are fractions (0.0–1.0) of the full card dimensions as computed by
`card_estimation`.

```
0,0 ─────────────────── 1,0
 │                         │
 │   roi starts here ─┐    │
 │   (left, top)      │    │
 │                    │    │
 │   ┌──────────────┐ │    │
 │   │ OCR region   │height│
 │   │   width ─────┘ │    │
 │   └──────────────┘      │
 │                         │
0,1 ─────────────────── 1,1
```

**Measuring fractions from an image:**

```
left   = (pixel_x_start - card_x_start) / card_width_px
top    = (pixel_y_start - card_y_start) / card_height_px
width  = region_width_px  / card_width_px
height = region_height_px / card_height_px
```

#### `ocr` settings

| Key | Values | Notes |
|---|---|---|
| `languages` | `en`, `en,fr`, … | ISO 639-1 codes |
| `character_types` | `digit`, `uppercase`, `lowercase`, `uppercase,digit` | Omit for all types |
| `word_reject_threshold` | 0–100 | 60 = good default; lower = keep less-confident words |
| `ocr_mode` | `document`, `auto` | `document` for printed text; `auto` for mixed |
| `process_text_elements` | `true` / `false` | Set `true` for handwritten content |

---

### `draw`  *(optional)*

Colours for the debug PNG overlay.

```yaml
draw:
  roi_color:      [220, 120,   0]   # RGB — box around the OCR region
  boundary_color: [  0, 100, 200]   # RGB — polygon around the detection boundary
  label_fallback: WOR               # shown if rect.label is absent
```

**Suggested colour palette** (avoid duplicating existing states):

| State | Colour | RGB |
|---|---|---|
| ACT | orange | `[255, 140, 0]` |
| NSW | yellow | `[220, 200, 0]` |
| QLD | green  | `[0, 200, 80]` |
| TAS | cyan   | `[0, 200, 220]` |
| VIC | purple | `[160, 0, 220]` |
| WA  | amber  | `[220, 120, 0]` |
| SA  | red    | `[220, 40, 40]` |
| NT  | teal   | `[0, 180, 160]` |

---

## CLI reference

```
usage: gen_chain.py [-h] [--list] [--dry-run] [--force] [--config-dir PATH]
                    [definition]

positional arguments:
  definition          Path to a YAML chain definition file

options:
  --list              List all engines currently in Kapish_debug.cfg
  --dry-run           Print planned changes without writing any files
  --force             Overwrite Lua files that already exist
  --config-dir PATH   Override the configurations/ directory
```

---

## ROI tuning workflow

After running the tool for the first time:

1. Process a sample image using `Kapish_debug.cfg`.
2. Open `output/debug/<image>_<id>_<field>_pre_ocr.png`.
3. The coloured box shows where OCR will look.  If it is wrong:
   - Open `lua/compute_dynamic_region_<id>_<field>.lua`.
   - Adjust the `left`, `top`, `width`, `height` fractions.
   - Re-process the image and check the PNG again.
4. Once the box is correct and OCR reads the right value, update the matching
   fractions in your YAML definition (`wa_dl.yaml`) to keep it as the source
   of truth.  Re-run the tool with `--force` to regenerate the Lua from the
   updated YAML.

---

## Adding a field to an existing chain

1. Add a new entry to the `fields` list in the YAML file.
2. Run the tool with `--force`.

The tool will **skip** the cfg files if `Filter_<identifier>` is already
present.  To add a new field to an existing chain, you must either:

- Add the new engine stanzas manually following the existing pattern, **or**
- Remove the old chain from the cfg files first, then re-run the tool.

> **Tip:** The safest approach is to keep the YAML as the authoritative record
> of all fields and regenerate the entire chain whenever you make changes.

---

## File naming convention

Generated files follow a deterministic naming scheme so you can always find
them without looking at the cfg:

| Type | Path |
|---|---|
| Filter | `lua/filters/filter_<id_lower>.lua` |
| Compute region | `lua/compute_dynamic_region_<id_lower>_<field_lower>.lua` |
| Draw overlay | `lua/draw/draw_<id_lower>.lua` |

Engine names in the cfg use the original-case `identifier` + `field name`:
- `Filter_WA_DL`
- `DynamicRegion_WA_DL_Licence_Number`
- `OCR_WA_DL_Licence_Number`

---

## Example: adding WA_DL Licence Number

```bash
# Preview
python3 tools/gen_chain.py tools/definitions/wa_dl.yaml --dry-run

# Generate
python3 tools/gen_chain.py tools/definitions/wa_dl.yaml

# Verify
python3 tools/gen_chain.py --list
```

Expected output files:
```
lua/filters/filter_wa_dl.lua
lua/compute_dynamic_region_wa_dl_licence_number.lua
lua/draw/draw_wa_dl.lua
configurations/Kapish_debug.cfg  (patched)
configurations/Kapish.cfg        (patched)
```

---

## Adding further document types

| Document | Likely identifier | Notes |
|---|---|---|
| SA Driver's Licence | `SA_DL` | Check engine.log for aspect ratio warning |
| NT Driver's Licence | `NT_DL` | |
| Australian Passport | `AU_PASSPORT` | Full-card detection likely |
| Medicare Card | `MEDICARE` | Multiple fields: card number, reference, name |

Follow the same workflow: copy `template.yaml`, fill in values, run the tool,
tune the ROI fractions from the debug PNG.
