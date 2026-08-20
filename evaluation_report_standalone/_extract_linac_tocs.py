# -*- coding: utf-8 -*-
import fitz
from pathlib import Path

base = Path(r"c:\Users\13785\Desktop\markdown2latex")
out_dir = Path(r"c:\Users\13785\Desktop\markdown2latex\evaluation_report_standalone")
files = [
    "250221-02 YP大余县人民医院（直线加速器、CT模拟定位机）C版 报批稿.pdf",
    "250454 KP 上饶市立医院（加速器）C版 报批稿 .pdf",
]
for name in files:
    pdf = base / name
    doc = fitz.open(pdf)
    lines = [f"FILE={name}", f"pages={len(doc)}", "===TOC==="]
    for lvl, title, page in doc.get_toc():
        lines.append(f"{'  '*(lvl-1)}{title}\tp{page}")
    lines.append("\n===PAGE1===")
    lines.append(doc[0].get_text("text")[:1500])
    # find appendix start in toc
    lines.append("\n===APPENDIX TOC===")
    for lvl, title, page in doc.get_toc():
        if "附件" in title or "附录" in title:
            lines.append(f"{'  '*(lvl-1)}{title}\tp{page}")
    safe = "yp_linac" if name.startswith("250221") else "kp_linac"
    out = out_dir / f"_{safe}_toc.txt"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("wrote", out.name, "pages", len(doc), "toc", len(doc.get_toc()))
