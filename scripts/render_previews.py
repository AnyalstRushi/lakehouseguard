import json
import re
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'assets'
OUT.mkdir(exist_ok=True)
m = json.loads((ROOT / 'results.json').read_text())
assert m['failure_batches'] > 0 and 0 <= m['row_level']['outlier_amount']['recall'] <= 1
BG, INK, BLUE, MUTED = '#F3F0E7', '#172132', '#2447F9', '#526077'
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
def text(d, xy, value, size=20, color=INK):
    d.text(xy, str(value), fill=color, font=ImageFont.truetype(FONT, size))
for kind in ['hero', 'architecture', 'evidence']:
    frames = []
    for i in range(48):
        im = Image.new('RGB', (1000, 500), BG)
        d = ImageDraw.Draw(im)
        d.rectangle((0, 0, 10, 500), fill=BLUE)
        text(d, (35, 22), 'LAKEHOUSEGUARD / RELIABILITY OBSERVATORY', 15, BLUE)
        if kind == 'hero':
            text(d, (35, 82), 'Trust is a', 64)
            text(d, (35, 159), 'pipeline property.', 64, BLUE)
            text(d, (38, 254), 'Contracts. Quarantine. Evidence-led review.', 22, MUTED)
            for j, label in enumerate(['INGEST', 'VALIDATE', 'EXPLAIN', 'REVIEW']):
                x = 38 + j * 240
                d.rounded_rectangle((x, 326, x + 210, 409), radius=8, fill=BLUE if i // 12 == j else '#E2E3E9')
                text(d, (x + 18, 355), label, 20, '#FFFFFF' if i // 12 == j else INK)
        elif kind == 'architecture':
            text(d, (35, 82), 'The quality boundary.', 44)
            nodes = [(40, 190, 'BRONZE'), (350, 190, 'CONTRACT GATE'), (720, 190, 'SILVER'), (350, 340, 'QUARANTINE')]
            paths = [(260, 230, 350, 230), (620, 230, 720, 230), (485, 270, 485, 340)]
            for x1, y1, x2, y2 in paths:
                d.line((x1, y1, x2, y2), fill='#BAC2D5', width=3)
                t = (i % 24) / 23
                x, y = x1 + (x2-x1)*t, y1 + (y2-y1)*t
                d.ellipse((x-6, y-6, x+6, y+6), fill=BLUE)
            for x, y, label in nodes:
                d.rounded_rectangle((x, y, x+270 if label == 'CONTRACT GATE' else x+220, y+80), radius=8, fill='#F5D4C2' if label == 'QUARANTINE' else '#E2E3E9', outline=BLUE, width=2)
                text(d, (x+14, y+28), label, 18)
            text(d, (35, 145), 'Illustrative flow, not a recorded pipeline execution.', 18, MUTED)
        else:
            text(d, (35, 82), 'Evidence, including the limits.', 40)
            values = [('Injected failure batches', str(m['failure_batches'])), ('Healthy batches', str(m['healthy_batches'])), ('Reported detection on injected cases', f"{m['overall_detection_rate']:.0%}"), ('Alerts before / after triage', f"{m['raw_incidents']} / {m['after_triage']}"), ('Outlier row recall: known limitation', f"{m['row_level']['outlier_amount']['recall']:.1%}")]
            for j, (label, value) in enumerate(values):
                y = 157 + j * 48
                if i // 10 % len(values) == j:
                    d.rectangle((30, y-4, 970, y+36), fill='#E1E6FF')
                text(d, (42, y), label, 19)
                text(d, (760, y), value, 22, BLUE)
            text(d, (35, 415), 'Existing results.json snapshot. Not rerun; not production accuracy.', 17, MUTED)
        text(d, (35, 466), 'SYNTHETIC EDUCATIONAL DATA | ANIMATED PREVIEW | NOT LIVE TELEMETRY', 13, MUTED)
        frames.append(im)
    p = OUT / f'observatory-{kind}.gif'
    frames[0].save(p, save_all=True, append_images=frames[1:], duration=125, loop=0, optimize=True, disposal=2)
    with Image.open(p) as check:
        assert check.n_frames > 1 and check.size == (1000, 500)
    assert p.stat().st_size < 8_000_000
p = ROOT / 'README.md'
s = p.read_text()
s = re.sub(r'<!-- OBSERVATORY_PREVIEWS -->.*?<!-- /OBSERVATORY_PREVIEWS -->\s*', '', s, flags=re.S)
s = s.replace('![LakehouseGuard reliability observatory](assets/observatory-hero.svg)', '<!-- OBSERVATORY_STATIC_HERO -->', 1)
block = '<!-- OBSERVATORY_PREVIEWS -->\n![Animated Observatory hero](assets/observatory-hero.gif)\n\n## Architecture in motion\n![Illustrative contract routing](assets/observatory-architecture.gif)\n\n## Benchmark evidence preview\n![Existing benchmark snapshot including limitations](assets/observatory-evidence.gif)\n\nAnimation illustrates the design and an existing synthetic benchmark snapshot; it is not live execution or a new benchmark run.\n\n<!-- /OBSERVATORY_PREVIEWS -->\n\n'
p.write_text(block + s.lstrip())
for rel in re.findall(r'!\[[^\]]*\]\(([^)]+)\)', p.read_text()):
    if not rel.startswith(('http://', 'https://')):
        assert (ROOT / rel).is_file(), f'Missing README image: {rel}'
