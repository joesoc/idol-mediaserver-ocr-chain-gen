import os
import requests
import xml.etree.ElementTree as ET
from urllib.parse import quote
import time
from datetime import datetime

# ====================== SETTINGS ======================
# Host directory (for listing files)
HOST_IMAGE_DIR = '/home/ubuntu/projects/mediaserver/data/images/Kapish'

# Path INSIDE the container (this is what MediaServer expects)
CONTAINER_IMAGE_DIR = '/data/images/Kapish'
TRUTH_XML = 'test_truth.xml'
CERT = "/home/ubuntu/projects/mediaserver/mediaserver/ssl/bundle.crt"

truth_xml_path = os.path.join(os.path.dirname(__file__), TRUTH_XML)
output_dir = os.path.join(os.path.dirname(__file__), 'reports')
os.makedirs(output_dir, exist_ok=True)

CONFIGS = ['Kapish_debug']
LIVE_REPORT_HTML  = 'report_{config}.html'   # {config} is substituted at runtime
XML_REPORT_HTML   = 'report_xml_output.html'

# MediaServer XSLT-transformed XML output directory
MS_OUTPUT_DIR = '/home/ubuntu/projects/mediaserver/mediaserver/MediaServer_26.2.0_LINUX_X86_64/output'

API_URL_TEMPLATE = (
    'https://mediaserver-gpu.idoldemos.net:14000/action=process'
    '&Source={source_path}'
    '&ConfigName={config}'
    '&Synchronous=true'
)


def load_expected_dl_numbers(xml_path):
    expected = {}
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        for file_elem in root.findall('file'):
            name = file_elem.find('name').text
            act = file_elem.find('act_dl').text if file_elem.find('act_dl') is not None else None
            nsw = file_elem.find('nsw_dl').text if file_elem.find('nsw_dl') is not None else None
            expected[name] = {'act': act, 'nsw': nsw}
        print(f"✅ Loaded expected numbers for {len(expected)} files from {xml_path}")
    except FileNotFoundError:
        print(f"❌ Truth XML not found: {xml_path}")
        print("   Please create test_truth_dl.xml with expected DL numbers.")
    except Exception as e:
        print(f"⚠️ Error loading truth XML: {e}")
    return expected


def load_expected_numbers(xml_path):
    """Load expected DL numbers from truth XML using the <passport_number> field."""
    expected = {}
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        for file_elem in root.findall('file'):
            name = file_elem.find('name').text
            number_elem = file_elem.find('passport_number')
            expected[name] = number_elem.text.strip() if number_elem is not None and number_elem.text else None
        print(f"✅ Loaded {len(expected)} expected DL numbers from {xml_path}")
    except FileNotFoundError:
        print(f"❌ Truth XML not found: {xml_path}")
    except Exception as e:
        print(f"⚠️ Error loading truth XML: {e}")
    return expected


def extract_dl_numbers(xml_content):
    """Extract ACT and NSW numbers from response"""
    try:
        root = ET.fromstring(xml_content)
        ns = {'autn': 'http://schemas.autonomy.com/aci/'}

        texts = []
        for path in [".//OCRResult/text", ".//text", ".//record/OCRResult/text"]:
            texts.extend(root.findall(path, ns))

        extracted_texts = [t.text.strip().replace(' ', '') 
                          for t in texts if t.text and t.text.strip()]

        act = extracted_texts[0] if len(extracted_texts) >= 1 else None
        nsw = extracted_texts[1] if len(extracted_texts) >= 2 else None

        return {'act': act, 'nsw': nsw}

    except Exception as e:
        print(f"   XML Parse Error: {e}")
        return {'act': None, 'nsw': None}


def run_config(config_name, files, expected_numbers):
    results = []
    print(f"\n{'='*85}")
    print(f"  Running Driver License Config: {config_name}")
    print(f"{'='*85}")

    for file in files:
        expected = expected_numbers.get(file, {'act': None, 'nsw': None})
        
        # Use CONTAINER path, not host path
        container_path = f"{CONTAINER_IMAGE_DIR}/{file}"
        source_path = quote(container_path)

        api_url = API_URL_TEMPLATE.format(source_path=source_path, config=config_name)

        print(f"  Processing: {file:<40}", end=' ')
        t_start = time.perf_counter()

        try:
            response = requests.get(api_url, timeout=60, verify=CERT)
            elapsed = time.perf_counter() - t_start

            if response.status_code != 200:
                extracted = {'act': None, 'nsw': None}
                status = f"HTTP {response.status_code}"
                print(f"[{elapsed:.3f}s] ❌ {status}")
            else:
                extracted = extract_dl_numbers(response.text)
                
                act_match = extracted['act'] == expected['act'] if expected['act'] else False
                nsw_match = extracted['nsw'] == expected['nsw'] if expected['nsw'] else False

                if expected['act'] and expected['nsw']:
                    status = 'PASS' if (act_match and nsw_match) else 'FAIL'
                elif expected['act']:
                    status = 'PASS' if act_match else 'FAIL'
                elif expected['nsw']:
                    status = 'PASS' if nsw_match else 'FAIL'
                else:
                    status = 'N/A'

                print(f"[{elapsed:.3f}s] {status} | ACT: {extracted['act'] or '—':<12} | NSW: {extracted['nsw'] or '—'}")

            results.append({
                'filename': file,
                'expected_act': expected['act'],
                'expected_nsw': expected['nsw'],
                'extracted_act': extracted['act'],
                'extracted_nsw': extracted['nsw'],
                'status': status,
                'time': elapsed,
            })

        except Exception as e:
            elapsed = time.perf_counter() - t_start
            print(f"[{elapsed:.3f}s] 💥 Exception: {e}")
            results.append({
                'filename': file,
                'expected_act': expected.get('act'),
                'expected_nsw': expected.get('nsw'),
                'extracted_act': None,
                'extracted_nsw': None,
                'status': 'EXCEPTION',
                'time': elapsed,
            })

    return results


# ====================== HTML REPORT ======================
def generate_html_report(config_name, results, output_path, run_timestamp):
    times = [r['time'] for r in results]
    total = len(results)
    passes = sum(1 for r in results if r['status'] == 'PASS')
    fails = sum(1 for r in results if r['status'] == 'FAIL')
    avg_time = sum(times) / total if total else 0
    total_time = sum(times)
    pass_rate = f'{(passes/total*100):.1f}%' if total else '0%'

    rows_html = ''
    for i, r in enumerate(results, 1):
        row_class = 'row-pass' if r['status'] == 'PASS' else 'row-fail' if r['status'] == 'FAIL' else ''
        rows_html += f"""
        <tr class="{row_class}">
            <td>{i}</td>
            <td class="mono">{r['filename']}</td>
            <td class="mono">{r['expected_act'] or '—'}</td>
            <td class="mono">{r['expected_nsw'] or '—'}</td>
            <td class="mono">{r['extracted_act'] or '—'}</td>
            <td class="mono">{r['extracted_nsw'] or '—'}</td>
            <td>{'<span class="badge pass">PASS</span>' if r['status']=='PASS' else '<span class="badge fail">FAIL</span>'}</td>
            <td class="mono time-cell">{r['time']:.4f}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>MediaServer DL Report – {config_name}</title>
  <style>
    /* Paste your full original CSS style block here */
    /* ... (same as your previous beautiful report) ... */
  </style>
</head>
<body>
  <header>
    <h1>🚗 MediaServer Driver Licence Report</h1>
    <h2>Config: <strong>{config_name}</strong></h2>
    <p class="meta">Generated: {run_timestamp}</p>
  </header>

  <div class="stats-grid">
    <div class="stat-card"><div class="stat-value">{total}</div><div class="stat-label">Total Files</div></div>
    <div class="stat-card green"><div class="stat-value">{passes}</div><div class="stat-label">Pass</div></div>
    <div class="stat-card red"><div class="stat-value">{fails}</div><div class="stat-label">Fail</div></div>
    <div class="stat-card orange"><div class="stat-value">{pass_rate}</div><div class="stat-label">Pass Rate</div></div>
    <div class="stat-card teal"><div class="stat-value">{avg_time:.3f}s</div><div class="stat-label">Avg Time / File</div></div>
  </div>

  <div class="card">
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>Filename</th>
          <th>Expected ACT</th>
          <th>Expected NSW</th>
          <th>Extracted ACT</th>
          <th>Extracted NSW</th>
          <th>Status</th>
          <th>Time (s)</th>
        </tr>
      </thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>
  </div>
  <footer>IDOL MediaServer Driver Licence Benchmark — {run_timestamp}</footer>
</body>
</html>"""

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"✅ HTML report saved: {output_path}")


# ====================== DOC TYPE XML FIELDS ======================
# Maps (number_xml_element, confidence_xml_element) per document type.
# gen_chain.py appends new entries here automatically when adding a chain.
_DOC_FIELDS = [
    ('ACT_DL_NUMBER',        'ACT_CONFIDENCE'),
    ('NSW_DL_NUMBER',        'NSW_CONFIDENCE'),
    ('QLD_DL_NUMBER',        'QLD_CONFIDENCE'),
    ('TAS_DL_NUMBER',        'TAS_CONFIDENCE'),
    ('VIC_DL_NUMBER',        'VIC_DL_CONFIDENCE'),
    ('WA_DL_LICENCE_NUMBER', 'WA_DL_CONFIDENCE'),
    # [GEN_CHAIN_INSERT]
]


# ====================== XML OUTPUT COMPARISON ======================

def read_xml_output_results(output_xml_dir, expected):
    """
    Read all *_combined.xml files (excluding *_combined_pre.xml) from the
    MediaServer output directory, extract DL numbers, and compare against
    expected values from truth XML.
    """
    all_images = sorted(
        f for f in os.listdir(HOST_IMAGE_DIR)
        if os.path.isfile(os.path.join(HOST_IMAGE_DIR, f))
        and f.lower().endswith(('.png', '.jpg', '.jpeg', '.tiff'))
    )

    # Map stem -> full path for every *_combined.xml (not pre)
    xml_map = {}
    if os.path.isdir(output_xml_dir):
        for fname in os.listdir(output_xml_dir):
            if fname.endswith('_combined.xml') and not fname.endswith('_combined_pre.xml'):
                stem = fname[: -len('_combined.xml')]
                xml_map[stem] = os.path.join(output_xml_dir, fname)

    print(f"\n{'='*85}")
    print(f"  Comparing XML Output Files  ({len(xml_map)} found in output dir)")
    print(f"{'='*85}")

    results = []
    for image_file in all_images:
        stem = os.path.splitext(image_file)[0]
        expected_number = expected.get(image_file)
        xml_path = xml_map.get(stem)

        if xml_path is None:
            status = 'NO_OUTPUT'
            extracted_number = license_type = confidence = ocr_confidence = None
            print(f"  {image_file:<52} NO OUTPUT")
        else:
            try:
                doc = ET.parse(xml_path).getroot().find('.//document')
                license_type     = doc.findtext('LICENSE_TYPE') or None
                confidence       = doc.findtext('LICENSE_CONFIDENCE') or None
                extracted_number = None
                ocr_confidence   = None
                for num_tag, conf_tag in _DOC_FIELDS:
                    val = doc.findtext(num_tag)
                    if val and val.strip():
                        extracted_number = val.strip().replace(' ', '') or None
                        ocr_confidence   = doc.findtext(conf_tag)
                        break

                if expected_number and extracted_number:
                    status = 'PASS' if extracted_number == expected_number else 'FAIL'
                elif extracted_number:
                    status = 'N/A'
                else:
                    status = 'NO_NUMBER'

                icon = '✅' if status == 'PASS' else ('❌' if status == 'FAIL' else '⚠️')
                print(f"  {image_file:<52} {icon} {status:<10}  "
                      f"{license_type or '—':<10}  expected={expected_number or '—'}  "
                      f"extracted={extracted_number or '—'}")

            except Exception as e:
                status = 'ERROR'
                extracted_number = license_type = confidence = ocr_confidence = None
                print(f"  {image_file:<52} 💥 {e}")

        results.append({
            'filename':         image_file,
            'license_type':     license_type,
            'license_conf':     confidence,
            'ocr_confidence':   ocr_confidence,
            'expected':         expected_number,
            'extracted':        extracted_number,
            'status':           status,
        })

    return results


def generate_xml_output_report(results, output_path, run_timestamp):
    """Generate a styled HTML report from XML output file comparison."""
    total      = len(results)
    passes     = sum(1 for r in results if r['status'] == 'PASS')
    fails      = sum(1 for r in results if r['status'] == 'FAIL')
    no_output  = sum(1 for r in results if r['status'] in ('NO_OUTPUT', 'NO_NUMBER', 'ERROR'))
    pass_rate  = f'{passes/total*100:.1f}%' if total else '0%'

    rows_html = ''
    for i, r in enumerate(results, 1):
        s = r['status']
        row_class = 'row-pass' if s == 'PASS' else ('row-fail' if s == 'FAIL' else ('row-no-output' if s == 'NO_OUTPUT' else ''))
        if s == 'PASS':
            badge = '<span class="badge pass">PASS</span>'
        elif s == 'FAIL':
            badge = '<span class="badge fail">FAIL</span>'
        elif s == 'N/A':
            badge = '<span class="badge na">N/A</span>'
        elif s == 'NO_OUTPUT':
            badge = '<span class="badge no-output">NO OUTPUT</span>'
        elif s == 'NO_NUMBER':
            badge = '<span class="badge no-output">NO NUMBER</span>'
        else:
            badge = f'<span class="badge error">{s}</span>'

        rows_html += (
            f'<tr class="{row_class}">'
            f'<td>{i}</td>'
            f'<td class="mono">{r["filename"]}</td>'
            f'<td class="mono">{r["license_type"] or "—"}</td>'
            f'<td class="mono">{r["license_conf"] or "—"}</td>'
            f'<td class="mono">{r["ocr_confidence"] or "—"}</td>'
            f'<td class="mono">{r["expected"] or "—"}</td>'
            f'<td class="mono"><strong>{r["extracted"] or "—"}</strong></td>'
            f'<td>{badge}</td>'
            f'</tr>\n'
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>MediaServer XML Output Report</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Segoe UI', Arial, sans-serif;
      background: #f0f4f8;
      color: #333;
      padding: 30px 40px;
    }}
    header {{ margin-bottom: 28px; }}
    header h1 {{ font-size: 1.8rem; color: #1a3a5c; }}
    header h2 {{ font-size: 1.1rem; color: #4a90d9; margin-top: 4px; font-weight: normal; }}
    header p.meta {{ font-size: 0.85rem; color: #888; margin-top: 6px; }}
    .stats-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 16px;
      margin-bottom: 30px;
    }}
    .stat-card {{
      background: #fff;
      border-radius: 10px;
      padding: 18px 16px;
      text-align: center;
      box-shadow: 0 2px 8px rgba(0,0,0,0.08);
      border-top: 4px solid #4a90d9;
    }}
    .stat-card.green  {{ border-top-color: #27ae60; }}
    .stat-card.red    {{ border-top-color: #e74c3c; }}
    .stat-card.orange {{ border-top-color: #f39c12; }}
    .stat-card.grey   {{ border-top-color: #95a5a6; }}
    .stat-value {{ font-size: 2rem; font-weight: 700; color: #1a3a5c; line-height: 1.1; }}
    .stat-label {{ font-size: 0.78rem; color: #888; margin-top: 5px; text-transform: uppercase; letter-spacing: 0.05em; }}
    .card {{
      background: #fff;
      border-radius: 10px;
      box-shadow: 0 2px 8px rgba(0,0,0,0.08);
      overflow: hidden;
    }}
    table {{ width: 100%; border-collapse: collapse; }}
    thead tr {{ background: #1a3a5c; color: #fff; }}
    thead th {{ padding: 12px 14px; text-align: left; font-size: 0.85rem; font-weight: 600; letter-spacing: 0.04em; }}
    tbody tr {{ border-bottom: 1px solid #eef0f3; transition: background 0.15s; }}
    tbody tr:last-child {{ border-bottom: none; }}
    tbody tr:hover {{ background: #f5f9ff; }}
    tbody tr.row-pass {{ background: #f0fff4; }}
    tbody tr.row-fail {{ background: #fff5f5; }}
    tbody tr.row-no-output {{ background: #fafafa; color: #aaa; }}
    tbody tr.row-pass:hover {{ background: #e6ffee; }}
    tbody tr.row-fail:hover {{ background: #ffe8e8; }}
    td {{ padding: 10px 14px; font-size: 0.88rem; }}
    .mono {{ font-family: 'Cascadia Code', 'Consolas', monospace; }}
    .badge {{
      display: inline-block;
      padding: 3px 10px;
      border-radius: 12px;
      font-size: 0.78rem;
      font-weight: 700;
      letter-spacing: 0.05em;
    }}
    .badge.pass      {{ background: #d4edda; color: #155724; }}
    .badge.fail      {{ background: #f8d7da; color: #721c24; }}
    .badge.na        {{ background: #e2e3e5; color: #383d41; }}
    .badge.no-output {{ background: #fff3cd; color: #856404; }}
    .badge.error     {{ background: #fce8e8; color: #a00; }}
    footer {{ margin-top: 30px; font-size: 0.8rem; color: #aaa; text-align: center; }}
  </style>
</head>
<body>
  <header>
    <h1>&#128203; MediaServer XML Output Report</h1>
    <h2>Source: <strong>Transformed Combined XML Files vs test_truth.xml</strong></h2>
    <p class="meta">Generated: {run_timestamp}</p>
  </header>

  <div class="stats-grid">
    <div class="stat-card">
      <div class="stat-value">{total}</div>
      <div class="stat-label">Total Images</div>
    </div>
    <div class="stat-card green">
      <div class="stat-value">{passes}</div>
      <div class="stat-label">Pass</div>
    </div>
    <div class="stat-card red">
      <div class="stat-value">{fails}</div>
      <div class="stat-label">Fail</div>
    </div>
    <div class="stat-card orange">
      <div class="stat-value">{pass_rate}</div>
      <div class="stat-label">Pass Rate</div>
    </div>
    <div class="stat-card grey">
      <div class="stat-value">{no_output}</div>
      <div class="stat-label">No Output</div>
    </div>
  </div>

  <div class="card">
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>Filename</th>
          <th>License Type</th>
          <th>Detection Conf</th>
          <th>OCR Conf</th>
          <th>Expected Number</th>
          <th>Extracted Number</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>
  </div>

  <footer>IDOL MediaServer XML Output Benchmark &mdash; {run_timestamp}</footer>
</body>
</html>"""

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"✅ XML output report saved: {output_path}")


# ====================== MAIN ======================
if __name__ == '__main__':
    # Clear MediaServer output directory before running tests so results are fresh
    if os.path.exists(MS_OUTPUT_DIR):
        import shutil
        shutil.rmtree(MS_OUTPUT_DIR)
        print(f"🗑️  Cleared output directory: {MS_OUTPUT_DIR}")
    os.makedirs(MS_OUTPUT_DIR, exist_ok=True)

    expected_numbers = load_expected_dl_numbers(truth_xml_path)
    
    files = sorted([
        f for f in os.listdir(HOST_IMAGE_DIR)
        if os.path.isfile(os.path.join(HOST_IMAGE_DIR, f))
        and f.lower().endswith(('.png', '.jpg', '.jpeg', '.tiff'))
    ])

    print(f"Found {len(files)} image(s) in {HOST_IMAGE_DIR}")
    run_timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # ── Part 1: Live API test ───────────────────────────────────────────────
    config_name = CONFIGS[0]
    results = run_config(config_name, files, expected_numbers)
    report_path = os.path.join(output_dir, LIVE_REPORT_HTML.format(config=config_name))
    generate_html_report(config_name, results, report_path, run_timestamp)

    # ── Part 2: XML output file comparison ─────────────────────────────────
    expected_simple = load_expected_numbers(truth_xml_path)
    xml_results = read_xml_output_results(MS_OUTPUT_DIR, expected_simple)
    xml_report_path = os.path.join(output_dir, XML_REPORT_HTML)
    generate_xml_output_report(xml_results, xml_report_path, run_timestamp)