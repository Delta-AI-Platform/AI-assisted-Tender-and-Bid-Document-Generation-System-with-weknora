# -*- coding: utf-8 -*-
"""标书成品轻量预检：目次、附件、正偏离三处一致和体量基线。"""
import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def docx_metrics(path):
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        root = ET.fromstring(z.read("word/document.xml"))
        paragraphs = []
        styles = []
        for p in root.iter(W + "p"):
            text = "".join((t.text or "") for t in p.iter(W + "t"))
            paragraphs.append(text)
            ps = p.find(W + "pPr/" + W + "pStyle")
            styles.append(ps.get(W + "val") if ps is not None else "")
        tables = sum(1 for _ in root.iter(W + "tbl"))
        rows = sum(1 for _ in root.iter(W + "tr"))
        images = sum(1 for n in names if n.startswith("word/media/") and not n.endswith("/"))
        toc_field = any("TOC" in (x.get(W + "instr") or "") for x in root.iter(W + "fldSimple"))
        toc_field = toc_field or any("TOC" in (x.text or "") for x in root.iter(W + "instrText"))
        heading_levels = sorted({s for s in styles if re.search(r"(?:Heading|标题)\s*[1-9]", s or "", re.I)})
        return {
            "path": os.path.abspath(path), "sha256": sha256(path),
            "characters": sum(len(x) for x in paragraphs), "paragraphs": len(paragraphs),
            "tables": tables, "rows": rows, "images": images,
            "toc_field": toc_field, "toc_heading": any(x.strip() in {"目录", "目次"} for x in paragraphs),
            "heading_styles": heading_levels,
        }


def cmd_docx(args):
    m = docx_metrics(args.docx)
    m["multilevel_toc_pass"] = bool((m["toc_field"] or m["toc_heading"]) and len(m["heading_styles"]) >= 2)
    print(json.dumps(m, ensure_ascii=False, indent=2))
    return 0 if (args.allow_toc_exception or m["multilevel_toc_pass"]) else 1


def cmd_attachments(args):
    data = json.load(open(args.manifest, encoding="utf-8"))
    base = os.path.dirname(os.path.abspath(args.manifest))
    missing = []
    for item in data.get("attachments", []):
        p = item.get("path", "")
        full = p if os.path.isabs(p) else os.path.join(base, p)
        if not os.path.isfile(full):
            missing.append({"id": item.get("id"), "path": p})
    print(json.dumps({"total": len(data.get("attachments", [])), "missing": missing}, ensure_ascii=False, indent=2))
    return 1 if missing else 0


def cmd_positive(args):
    data = json.load(open(args.manifest, encoding="utf-8"))
    blank_form = data.get("formal_deviation_blank") is True
    if data.get("skill_version") == "v1.38":
        groups = ("technical_alignment", "positive_section", "evidence_index")
    else:
        groups = ("response", "positive_section", "internal_deviation") if blank_form else ("response", "positive_section", "deviation")
    sets = {g: {str(x) for x in data.get(g, [])} for g in groups}
    union = set().union(*sets.values())
    inconsistent = {x: [g for g in groups if x not in sets[g]] for x in sorted(union) if any(x not in sets[g] for g in groups)}
    if blank_form and data.get("deviation"):
        inconsistent["formal_deviation"] = ["Formal deviation list must be empty"]
    print(json.dumps({"counts": {g: len(sets[g]) for g in groups}, "inconsistent": inconsistent}, ensure_ascii=False, indent=2))
    return 1 if inconsistent else 0


def cmd_volume(args):
    current = docx_metrics(args.current)
    reference = docx_metrics(args.reference)
    ratios = {}
    failed = []
    for key in ("characters", "paragraphs", "tables", "rows", "images"):
        ratios[key] = None if reference[key] == 0 else round(current[key] / reference[key], 4)
        if ratios[key] is not None and ratios[key] < args.minimum_ratio:
            failed.append(key)
    print(json.dumps({"current": current, "reference": reference, "ratios": ratios, "below_baseline": failed}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("docx", help="检查DOCX完整性、目次及结构指标")
    p.add_argument("docx")
    p.add_argument("--allow-toc-exception", action="store_true", help="招标明确禁止或另定目录时使用")
    p.set_defaults(func=cmd_docx)
    p = sub.add_parser("attachments", help="核对引用附件清单中的实际文件")
    p.add_argument("manifest", help='JSON: {"attachments":[{"id":"A1","path":"..."}]}')
    p.set_defaults(func=cmd_attachments)
    p = sub.add_parser("positive", help="核对正偏离在响应、优势章节和适用证据之间一致")
    p.add_argument("manifest", help='v1.38 JSON: {"skill_version":"v1.38","technical_alignment":[],"positive_section":[],"evidence_index":[],"formal_deviation_blank":true}')
    p.set_defaults(func=cmd_positive)
    p = sub.add_parser("volume", help="与适用历史成熟卷比较结构体量")
    p.add_argument("current")
    p.add_argument("reference")
    p.add_argument("--minimum-ratio", type=float, default=0.35)
    p.set_defaults(func=cmd_volume)
    args = ap.parse_args()
    try:
        return args.func(args)
    except (OSError, KeyError, ValueError, zipfile.BadZipFile, ET.ParseError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
