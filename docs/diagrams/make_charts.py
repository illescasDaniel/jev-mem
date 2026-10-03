"""Render docs/img/results.svg from the numbers in docs/evaluation.md (stdlib only).

    python docs/diagrams/make_charts.py
"""
from pathlib import Path

DATA = [  # label, (vector top-5, hybrid top-5, jevmem full), items injected (baseline, jevmem)
    ("Held-out notes\n(41 questions)", (0.86, 0.93, 0.99), (5.0, 1.4)),
    ("LoCoMo, 9 conversations\n(272 questions)", (0.70, 0.65, 0.89), (5.0, 2.3)),
    ("Real commit history\n(31 questions)", (0.90, 0.90, 1.00), (5.0, 2.1)),
]
SERIES = [("vector top-5", "#94A3B8"), ("hybrid top-5", "#64748B"), ("jevmem", "#0EA5E9")]
W, H = 760, 360
out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
       'font-family="Helvetica, Arial, sans-serif">',
       f'<rect width="{W}" height="{H}" fill="#FFFFFF"/>',
       '<text x="380" y="26" text-anchor="middle" font-size="16" font-weight="bold" fill="#0F172A">'
       'Recall: did the right note reach the agent? (higher is better)</text>']
x0, y0, ph, gw = 60, 50, 190, 200
for i in range(0, 11, 2):
    v = i / 10
    y = y0 + ph - v * ph
    out.append(f'<line x1="{x0}" x2="{W-20}" y1="{y:.1f}" y2="{y:.1f}" stroke="#E2E8F0"/>')
    out.append(f'<text x="{x0-8}" y="{y+4:.1f}" text-anchor="end" font-size="11" fill="#475569">{v:.1f}</text>')
bw = 44
for gi, (label, vals, items) in enumerate(DATA):
    gx = x0 + 25 + gi * (gw + 15)
    for si, v in enumerate(vals):
        x = gx + si * (bw + 4)
        h = v * ph
        out.append(f'<rect x="{x}" y="{y0+ph-h:.1f}" width="{bw}" height="{h:.1f}" rx="3" fill="{SERIES[si][1]}"/>')
        out.append(f'<text x="{x+bw/2}" y="{y0+ph-h-5:.1f}" text-anchor="middle" font-size="12" '
                   f'font-weight="{"bold" if si==2 else "normal"}" fill="#0F172A">{v:.2f}</text>')
    for li, line in enumerate(label.split("\n")):
        out.append(f'<text x="{gx+(3*bw+8)/2}" y="{y0+ph+18+li*14}" text-anchor="middle" font-size="12" '
                   f'fill="#334155">{line}</text>')
    out.append(f'<text x="{gx+(3*bw+8)/2}" y="{y0+ph+60}" text-anchor="middle" font-size="11" fill="#0369A1">'
               f'notes injected: {items[0]:.0f} vs {items[1]:.1f}</text>')
lx = x0 + 10
for name, col in SERIES:
    out.append(f'<rect x="{lx}" y="{H-22}" width="12" height="12" rx="2" fill="{col}"/>')
    out.append(f'<text x="{lx+18}" y="{H-12}" font-size="12" fill="#334155">{name}</text>')
    lx += 130
out.append(f'<text x="{W-20}" y="{H-34}" text-anchor="end" font-size="10" fill="#64748B">'
           'bge-small embeddings; small datasets, see docs/evaluation.md</text>')
out.append('</svg>')
Path(__file__).resolve().parent.parent.joinpath("img", "results.svg").write_text("\n".join(out))
